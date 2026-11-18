"""What can be downloaded, switched off and removed.

The models and runtimes the program uses, described once for the settings
window and for the first start: what each is for, what hardware it wants, how
big it is, whether it is here, and how to fetch or delete it. Nothing in this
file needs a window.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import os
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import setup
from .config import Config
from .setup import Asset, Cancelled

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# -- hardware -------------------------------------------------------------------


@dataclass
class Gpu:
    name: str
    memory_mb: int


@functools.lru_cache(maxsize=1)
def nvidia_gpu() -> Gpu | None:
    """The NVIDIA graphics card as its own driver tool reports it; None without one."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
        ).stdout
        name, memory = out.strip().splitlines()[0].rsplit(",", 1)
        return Gpu(name.strip(), int(memory))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


@dataclass
class Card:
    name: str
    vendor: str      # "nvidia", "amd", "intel" or "other"
    memory_mb: int   # the card's own memory; integrated graphics have little or none


VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x1022: "amd", 0x8086: "intel"}


@functools.lru_cache(maxsize=1)
def graphics_cards() -> tuple[Card, ...]:
    """Every graphics adapter Windows knows, as DirectX lists them (no driver
    tool needed, any maker). Empty elsewhere or when the list cannot be read."""
    if sys.platform != "win32":
        return ()
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, byref, c_uint, c_void_p

    class Desc(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                    ("SubSysId", c_uint), ("Revision", c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("LuidLow", c_uint), ("LuidHigh", ctypes.c_long), ("Flags", c_uint)]

    def call(obj, index, *args, argtypes=()):
        fn = WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)(ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0][index])
        return fn(obj, *args)

    import uuid

    iid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    factory, cards = c_void_p(), []
    try:
        dxgi = ctypes.WinDLL("dxgi")
        if dxgi.CreateDXGIFactory1(byref(iid), byref(factory)) < 0:
            return ()
        i = 0
        while True:
            adapter = c_void_p()
            if call(factory, 12, c_uint(i), byref(adapter), argtypes=(c_uint, POINTER(c_void_p))) < 0:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            desc = Desc()
            if call(adapter, 10, byref(desc), argtypes=(POINTER(Desc),)) >= 0 and not desc.Flags & 2:
                cards.append(Card(desc.Description, VENDORS.get(desc.VendorId, "other"),
                                  desc.DedicatedVideoMemory // (1 << 20)))
            call(adapter, 2)  # Release
            i += 1
    except OSError:
        return ()
    finally:
        if factory:
            call(factory, 2)
    return tuple(cards)


# Below this much memory of its own, a card is integrated graphics or too small
# for Whisper beside the game.
VULKAN_MIN_MB = 3000


def vulkan_card() -> Card | None:
    """The card whisper.cpp would use for Whisper on a PC without NVIDIA: the
    one with the most memory of its own, if it has enough."""
    cards = [c for c in graphics_cards() if c.memory_mb >= VULKAN_MIN_MB]
    return max(cards, key=lambda c: c.memory_mb) if cards else None


# -- files ----------------------------------------------------------------------


def _is_link(path: Path) -> bool:
    """A symbolic link or a Windows junction: a name for a folder kept elsewhere."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return getattr(st, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def delete(path: Path) -> None:
    """Remove a file or folder. Of a link only the link goes, never what it points at."""
    if _is_link(path):
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# -- the Whisper libraries ------------------------------------------------------

WHISPER_PACKAGES = ["faster-whisper", "ctranslate2", "nvidia-cublas-cu12", "nvidia-cudnn-cu12",
                    "nvidia-cuda-nvrtc-cu12"]
WHISPER_MODULES = ["faster_whisper", "ctranslate2"]
LIBRARY_BYTES = 1_300_000_000  # roughly what installing them downloads
_PYPI = ("https://pypi.org/simple", "https://files.pythonhosted.org")
_TUNA = ("https://pypi.tuna.tsinghua.edu.cn/simple", "https://pypi.tuna.tsinghua.edu.cn")


def libraries_installed() -> bool:
    try:
        return all(importlib.util.find_spec(m) is not None for m in WHISPER_MODULES)
    except (ImportError, ValueError):
        return False


def libraries_loaded() -> bool:
    return any(m in sys.modules for m in WHISPER_MODULES)


def _library_folders() -> list[Path]:
    spec = importlib.util.find_spec("ctranslate2") if libraries_installed() else None
    if not spec or not spec.origin:
        return []
    site = Path(spec.origin).parents[1]
    return [site / "ctranslate2", site / "faster_whisper", site / "nvidia" / "cublas",
            site / "nvidia" / "cudnn", site / "nvidia" / "cuda_nvrtc"]


def project_root() -> Path | None:
    """Where pyproject.toml and uv.lock are, when running from a source folder."""
    for root in (Path(__file__).resolve().parents[2], Path.cwd()):
        if (root / "uv.lock").exists() and (root / "pyproject.toml").exists():
            return root
    return None


def find_uv() -> str | None:
    exe = "uv.exe" if os.name == "nt" else "uv"
    root = project_root()
    candidates = [os.environ.get("VALOROYCE_UV"), os.environ.get("UV"), str(Path.cwd() / exe),
                  str(root / exe) if root else None, shutil.which("uv")]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def _run(cmd: list[str], say, cancel, cwd=None, env=None) -> None:
    """Run a tool, passing on what it prints; stop it when `cancel` is set."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line.rstrip()) for line in proc.stdout],
                              daemon=True)
    reader.start()
    tail: list[str] = []
    while True:
        try:
            line = lines.get(timeout=0.2)
            if line:
                tail = [*tail[-5:], line]
                say(line)
        except queue.Empty:
            if proc.poll() is not None and not reader.is_alive() and lines.empty():
                break
        if cancel is not None and cancel.is_set():
            proc.terminate()
            proc.wait()
            raise Cancelled()
    if proc.returncode:
        raise RuntimeError("\n".join(tail) or f"{Path(cmd[0]).name} failed ({proc.returncode})")


def _sample_wheel(root: Path | None, package: str = "nvidia-cudnn-cu12") -> str:
    """Path of a large locked wheel of `package` on the package servers, to
    time them with."""
    if root is None:
        return ""
    tag = {"win32": "win_amd64", "linux": "manylinux"}.get(sys.platform, "none")
    try:
        with open(root / "uv.lock", "rb") as f:
            packages = tomllib.load(f).get("package", [])
    except (OSError, ValueError):
        return ""
    wanted = package
    for package in packages:
        if package.get("name") == wanted:
            for wheel in package.get("wheels", []):
                url = wheel.get("url", "")
                if tag in url and "aarch64" not in url and url.startswith(_PYPI[1]):
                    return url[len(_PYPI[1]):]
    return ""


def _index_routes(mirror: str, root: Path | None, say,
                  sample_package: str = "nvidia-cudnn-cu12") -> list[tuple[str, dict[str, str]]]:
    """(package index, environment for the installer) routes, fastest first."""
    return [(index, env) for index, _files, _label, env, _opener
            in package_routes(mirror, _sample_wheel(root, sample_package), say)]


def package_routes(mirror: str, sample: str, say) -> list[tuple]:
    """(package index, file server, label, environment for the installer,
    opener) for each server and way to reach it, fastest first when `sample`
    (a file's path on the servers) can time them."""
    proxies = urllib.request.getproxies()
    through = dict(os.environ)
    for scheme in ("http", "https"):  # a proxy set in Windows only: tell the installer too
        if proxies.get(scheme):
            through.setdefault(f"{scheme.upper()}_PROXY", proxies[scheme])
    servers = {"global": [_PYPI], "cn": [_TUNA]}.get(mirror, [_PYPI, _TUNA])
    routes = []
    for index, files in servers:
        routes.append((index, files, "", through, urllib.request.build_opener()))
        if proxies:
            # Only this server bypasses the proxy: uv reaches other hosts too, and
            # without the proxy those can crawl (a dry run took 13 s instead of 2).
            hosts = ",".join(dict.fromkeys(u.split("/")[2] for u in (index, files)))
            no_proxy = ",".join(x for x in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy"),
                                            hosts) if x)
            routes.append((index, files, " (direct)", {**through, "NO_PROXY": no_proxy,
                                                       "no_proxy": no_proxy},
                           urllib.request.build_opener(urllib.request.ProxyHandler({}))))
    if len(routes) > 1 and sample:
        say("testing download speed...")
        # All at once: one after another took half a minute, most of it on dead routes.
        speeds = [0.0] * len(routes)

        def probe(i):
            speeds[i] = setup._probe(routes[i][4], routes[i][1] + sample, thorough=True)

        probes = [threading.Thread(target=probe, args=(i,), daemon=True) for i in range(len(routes))]
        for thread in probes:
            thread.start()
        for thread in probes:
            thread.join(setup.THOROUGH_SECONDS + 15)
        for route, speed in zip(routes, speeds):
            say(f"  {route[0].split('/')[2]}{route[2]}: {speed / 1e6:.2f} MB/s")
        fastest = sorted(zip(speeds, range(len(routes))), key=lambda t: -t[0])
        routes = [routes[i] for speed, i in fastest if speed > 0] or routes
    return routes


def install_libraries(mirror: str = "auto", say=print, cancel=None) -> None:
    """Put the Whisper packages into the environment the program runs in, at
    the versions the lock file names, from whichever package server is fastest."""
    uv = find_uv()
    if uv is None:
        raise RuntimeError("找不到 uv，无法安装运行库。请用 start.bat 启动程序后再试。")
    root = project_root()
    with tempfile.TemporaryDirectory() as tmp:
        if root:
            wanted = Path(tmp) / "whisper.txt"
            _run([uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--extra", "whisper",
                  "-o", str(wanted), "--quiet"], lambda _line: None, cancel, cwd=root)
            what = ["-r", str(wanted)]
        else:
            what = ["faster-whisper>=1.1", "nvidia-cublas-cu12>=12.4", "nvidia-cudnn-cu12>=9.1,<10"]
        errors = []
        for index, env in _index_routes(mirror, root, say):
            try:
                say(f"installing from {index.split('/')[2]}")
                _run([uv, "pip", "install", "--python", sys.executable, "--default-index", index,
                      *what], lambda line: say(f"installing: {line}"), cancel, env=env)
                importlib.invalidate_caches()
                return
            except Cancelled:
                raise
            except (OSError, RuntimeError) as e:
                errors.append(f"{index}: {e}")
        raise RuntimeError("could not install the Whisper libraries\n" + "\n".join(errors))


def remove_libraries(say=print) -> None:
    uv = find_uv()
    if uv is None:
        raise RuntimeError("找不到 uv，无法删除运行库。请用 start.bat 启动程序后再试。")
    _run([uv, "pip", "uninstall", "--python", sys.executable, *WHISPER_PACKAGES], say, None)
    importlib.invalidate_caches()


# -- the catalogue ----------------------------------------------------------------


class Item:
    """One thing the user can download, switch off or remove."""

    id = ""
    role = ""         # "asr", "translate" or "base"
    title = ""
    summary = ""      # what it is for
    hardware = ""     # what it wants from the PC
    required = False  # captions cannot run without it: no switch, no delete button
    paths: tuple[str, ...] = ()  # what removing it deletes, inside the models folder

    def assets(self) -> list[Asset]:
        return []

    def installed(self, models: Path) -> bool:
        return all(setup.present(a, models) for a in self.assets())

    def on_disk(self, models: Path) -> bool:
        """Whether anything of it is here, complete or not."""
        return any((models / p).exists() or _is_link(models / p) for p in self.paths)

    def blocked(self) -> str:
        """Why this PC cannot use it at all, or ""."""
        return ""

    def download_bytes(self, models: Path) -> int:
        return sum(a.size for a in self.assets() if not setup.present(a, models))

    def disk_bytes(self, models: Path) -> int:
        return sum(tree_bytes(models / p) for p in self.paths if (models / p).exists())

    def install(self, models: Path, mirror: str = "auto", say=print, progress=None, cancel=None):
        self._fetch(self.assets(), models, mirror, say, progress, cancel)

    def _fetch(self, assets, models, mirror, say, progress, cancel) -> None:
        todo = [a for a in assets if not setup.present(a, models)]
        total, before = sum(a.size for a in todo), 0
        for asset in todo:
            def part(got, _total, speed, before=before):
                if progress:
                    progress(before + got, total, speed)
            setup.download(asset, models, mirror, say, part if progress else None, cancel)
            before += asset.size

    def remove(self, models: Path) -> str:
        """Delete it. Returns a note for the user when something had to stay."""
        for p in self.paths:
            delete(models / p)
            delete(models / (p + ".part"))  # a download that was not finished
        return ""


class _Vad(Item):
    id, role, required = "vad", "base", True
    title = "语音检测（Silero VAD）"
    summary = "判断什么时候有人在说话、一句话在哪里结束。所有识别模型都需要它。"
    hardware = "任何电脑都能运行"
    paths = ("silero_vad.onnx",)

    def assets(self):
        return setup.SPEECH_ASSETS[:1]


class _SenseVoice(Item):
    id, role = "sensevoice", "asr"
    title = "SenseVoice Small"
    summary = "在 CPU 上识别，不占显卡，哪台电脑都能跑。对真实的队内语音准确度差不少，适合没有独立显卡时使用，或作为后备。"
    hardware = "任何电脑 · 不需要显卡"
    paths = ("sense-voice",)

    def assets(self):
        return setup.SPEECH_ASSETS[1:]


class _Whisper(Item):
    id, role = "whisper", "asr"
    title = "Whisper large-v3-turbo"
    summary = "在显卡上识别，准确度明显更高，英雄名、报点和嘈杂的对局里都更稳。除了模型，还要安装一套约 1.3 GB 的显卡运行库。"
    hardware = "需要 NVIDIA 显卡 · 占用约 1.3 GB 显存"
    paths = (setup.WHISPER_DIR,)

    def assets(self):
        return setup.WHISPER_ASSETS

    def installed(self, models):
        return super().installed(models) and libraries_installed()

    def on_disk(self, models):
        return super().on_disk(models) or libraries_installed()

    def blocked(self):
        return "" if nvidia_gpu() else "这台电脑没有检测到 NVIDIA 显卡"

    def download_bytes(self, models):
        return super().download_bytes(models) + (0 if libraries_installed() else LIBRARY_BYTES)

    def disk_bytes(self, models):
        return super().disk_bytes(models) + sum(tree_bytes(p) for p in _library_folders() if p.exists())

    def install(self, models, mirror="auto", say=print, progress=None, cancel=None):
        if not libraries_installed():
            if progress:
                progress(0, 0, 0.0)  # the installer reports no byte counts
            install_libraries(mirror, say, cancel)
        self._fetch(self.assets(), models, mirror, say, progress, cancel)

    def remove(self, models):
        super().remove(models)
        if not libraries_installed():
            return ""
        if libraries_loaded():
            return "模型已删除。显卡运行库（约 1.3 GB）这次运行中用过，无法马上删除：重启程序后再点一次「删除」即可。"
        remove_libraries(lambda _line: None)
        return ""


class _WhisperVulkan(Item):
    id, role = "whispercpp", "asr"
    title = "Whisper large-v3-turbo（Vulkan）"
    summary = ("和上面的 Whisper 是同一个模型，换成 whisper.cpp 在显卡上运行：AMD、Intel 显卡也能用，"
               "也不需要另装 1.3 GB 的运行库。")
    hardware = "AMD / Intel / NVIDIA 独立显卡 · 占用约 1.2 GB 显存"
    paths = ("whisper-ggml", setup.WHISPERCPP_DIR)

    def assets(self):
        return [setup.WHISPERCPP_MODEL]

    def installed(self, models):
        return super().installed(models) and setup.whispercpp_runtime_present(models)

    def blocked(self):
        if sys.platform != "win32":
            return "目前只有 Windows 版"
        return "" if vulkan_card() else f"这台电脑没有检测到显存 {VULKAN_MIN_MB // 1000} GB 以上的独立显卡"

    def download_bytes(self, models):
        runtime = 0 if setup.whispercpp_runtime_present(models) else setup.WHISPERCPP_RUNTIME.size
        return super().download_bytes(models) + runtime

    def install(self, models, mirror="auto", say=print, progress=None, cancel=None):
        self._fetch(self.assets(), models, mirror, say, progress, cancel)
        setup.install_whispercpp(models, mirror, say, progress, cancel)


class _Translation(Item):
    id, role = "hy-mt2", "translate"
    title = "Hy-MT2 1.8B"
    summary = "把识别出的日语 / 英语翻译成中文，完全在本机运行（附带 llama.cpp 运行库）。停用后字幕只替换英雄、地图等术语，其余显示原文。"
    hardware = "显卡上约占 1.5 GB 显存 · 也可以在「翻译」页改用 CPU"
    paths = (setup.LLM_ASSET.dest, "llama", "downloads")
    RUNTIME_BYTES = 40_000_000  # roughly; the archive's size differs per system

    def assets(self):
        return [setup.LLM_ASSET]

    @staticmethod
    def _runtime(models: Path) -> bool:
        return (models / "llama").is_dir() and any((models / "llama").rglob("llama-server*"))

    def installed(self, models):
        return super().installed(models) and self._runtime(models)

    def download_bytes(self, models):
        return super().download_bytes(models) + (0 if self._runtime(models) else self.RUNTIME_BYTES)

    def install(self, models, mirror="auto", say=print, progress=None, cancel=None):
        self._fetch(self.assets(), models, mirror, say, progress, cancel)
        if not self._runtime(models):
            setup.install_llama(models, mirror, "vulkan", say, progress, cancel)


VAD, SENSEVOICE, WHISPER, TRANSLATION = _Vad(), _SenseVoice(), _Whisper(), _Translation()
WHISPER_VULKAN = _WhisperVulkan()
ITEMS: dict[str, Item] = {i.id: i for i in (WHISPER, WHISPER_VULKAN, SENSEVOICE, TRANSLATION, VAD)}
RECOGNIZERS = [WHISPER, WHISPER_VULKAN, SENSEVOICE]

# Below this much memory on the card, Whisper beside the game and the
# translation model is a tight fit.
COMFORTABLE_MB = 6000


def recommended() -> list[Item]:
    """What a new user should download for this PC."""
    if nvidia_gpu():
        asr = WHISPER
    elif not WHISPER_VULKAN.blocked():
        asr = WHISPER_VULKAN
    else:
        asr = SENSEVOICE
    return [VAD, asr, TRANSLATION]


def advice() -> str:
    """One line about this PC for the top of the models page."""
    gpu = nvidia_gpu()
    if gpu is None:
        card = vulkan_card() if sys.platform == "win32" else None
        if card:
            return (f"显卡：{card.name} · {card.memory_mb / 1024:.0f} GB 显存。"
                    "推荐用 Whisper（Vulkan）识别，翻译可以用显卡或 CPU。")
        return "未检测到 NVIDIA 显卡：语音识别用 SenseVoice（CPU），翻译可以用显卡或 CPU。"
    line = f"显卡：{gpu.name} · {gpu.memory_mb / 1024:.0f} GB 显存。推荐用 Whisper 识别。"
    if gpu.memory_mb < COMFORTABLE_MB:
        line += "显存不到 6 GB，和游戏一起用可能吃紧：游戏卡顿时把「翻译」改到 CPU。"
    return line


# Written by the installer: the models the player ticked, one id per line
# (empty: none, for example because captions will use an online service).
CHOICE_FILE = Path("models-to-download.txt")


def take_choice(path: Path = CHOICE_FILE) -> list[Item] | None:
    """The installer's choice of models, once: the file is removed. None when
    there is no choice to act on."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    ids = [line.strip() for line in text.splitlines() if line.strip()]
    return [ITEMS[i] for i in dict.fromkeys(ids) if i in ITEMS]


def usable(item: Item, models: Path) -> bool:
    return item.installed(models) and not item.blocked()


def missing(cfg: Config) -> list[Item]:
    """What the current settings need and this PC does not have yet."""
    models, out = cfg.models_path, []
    if not VAD.installed(models):
        out.append(VAD)
    if cfg.asr.backend == "auto":
        order = [ITEMS[name] for name in cfg.asr.order if name in ITEMS]
        if not any(usable(item, models) for item in order):
            wanted = [i for i in order if not i.blocked()] or [SENSEVOICE]
            out.append(wanted[0])
    elif cfg.asr.backend in ITEMS and not ITEMS[cfg.asr.backend].installed(models):
        out.append(ITEMS[cfg.asr.backend])
    if (cfg.translate.backend == "llama" and not cfg.translate.model_file
            and not TRANSLATION.installed(models)):
        out.append(TRANSLATION)
    return out
