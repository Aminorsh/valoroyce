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


def _sample_wheel(root: Path | None) -> str:
    """Path of a large locked wheel on the package servers, to time them with."""
    if root is None:
        return ""
    tag = {"win32": "win_amd64", "linux": "manylinux"}.get(sys.platform, "none")
    try:
        with open(root / "uv.lock", "rb") as f:
            packages = tomllib.load(f).get("package", [])
    except (OSError, ValueError):
        return ""
    for package in packages:
        if package.get("name") == "nvidia-cudnn-cu12":
            for wheel in package.get("wheels", []):
                url = wheel.get("url", "")
                if tag in url and "aarch64" not in url and url.startswith(_PYPI[1]):
                    return url[len(_PYPI[1]):]
    return ""


def _index_routes(mirror: str, root: Path | None, say) -> list[tuple[str, dict[str, str]]]:
    """(package index, environment for the installer) routes, fastest first."""
    proxies = urllib.request.getproxies()
    through = dict(os.environ)
    for scheme in ("http", "https"):  # a proxy set in Windows only: tell the installer too
        if proxies.get(scheme):
            through.setdefault(f"{scheme.upper()}_PROXY", proxies[scheme])
    direct = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    ways = [("", through, urllib.request.build_opener())]
    if proxies:
        ways.append((" (direct)", direct, urllib.request.build_opener(urllib.request.ProxyHandler({}))))
    servers = {"global": [_PYPI], "cn": [_TUNA]}.get(mirror, [_PYPI, _TUNA])
    routes = [(index, files, label, env, opener)
              for index, files in servers for label, env, opener in ways]
    sample = _sample_wheel(root)
    if len(routes) > 1 and sample:
        say("testing download speed...")
        timed = []
        for route in routes:
            speed = setup._probe(route[4], route[1] + sample, thorough=True)
            say(f"  {route[0].split('/')[2]}{route[2]}: {speed / 1e6:.2f} MB/s")
            timed.append((speed, route))
        fastest = sorted(timed, key=lambda t: -t[0])
        routes = [r for speed, r in fastest if speed > 0] or routes
    return [(index, env) for index, _files, _label, env, _opener in routes]


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
    summary = "在 CPU 上识别，不占显卡，哪台电脑都能跑。对真实的队内语音准确度差不少，适合没有 NVIDIA 显卡时使用，或作为后备。"
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
ITEMS: dict[str, Item] = {i.id: i for i in (WHISPER, SENSEVOICE, TRANSLATION, VAD)}
RECOGNIZERS = [WHISPER, SENSEVOICE]

# Below this much memory on the card, Whisper beside the game and the
# translation model is a tight fit.
COMFORTABLE_MB = 6000


def recommended() -> list[Item]:
    """What a new user should download for this PC."""
    return [VAD, WHISPER if nvidia_gpu() else SENSEVOICE, TRANSLATION]


def advice() -> str:
    """One line about this PC for the top of the models page."""
    gpu = nvidia_gpu()
    if gpu is None:
        return "未检测到 NVIDIA 显卡：语音识别用 SenseVoice（CPU），翻译可以用显卡或 CPU。"
    line = f"显卡：{gpu.name} · {gpu.memory_mb / 1024:.0f} GB 显存。推荐用 Whisper 识别。"
    if gpu.memory_mb < COMFORTABLE_MB:
        line += "显存不到 6 GB，和游戏一起用可能吃紧：游戏卡顿时把「翻译」改到 CPU。"
    return line


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
