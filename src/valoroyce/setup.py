"""`valoroyce setup`: fetch the models and the llama.cpp runtime.

Every file has several sources. Before a large download we time a short
sample from each (through the system proxy and, if one is set, also directly)
and use the fastest: mirrors inside China are often far quicker without the
proxy, and the opposite holds for GitHub and Hugging Face.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

LLAMA_BUILD = "b12336"
_GH_LLAMA = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"
_GH_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
_HF = "https://huggingface.co"
_MS = "https://modelscope.cn/models"

PROBE_BYTES = 2_000_000
PROBE_SECONDS = 4.0
SMALL = 5_000_000  # below this, do not bother probing
BIG = 500_000_000  # above this, probe thoroughly
THOROUGH_BYTES = 40_000_000
THOROUGH_SECONDS = 6.0


@dataclass
class Asset:
    dest: str  # path under the models directory
    urls: dict[str, list[str]]  # "global" / "cn" -> candidate URLs
    sha256: str = ""
    size: int = 0
    extract_to: str = ""  # archives: unpack into this directory
    note: str = field(default="", compare=False)

    def candidates(self, mirror: str) -> list[str]:
        if mirror == "auto":
            return [*self.urls.get("global", []), *self.urls.get("cn", [])]
        return self.urls.get(mirror) or self.urls.get("global", [])


SPEECH_ASSETS = [
    Asset(
        "silero_vad.onnx",
        {"global": [f"{_GH_SHERPA}/silero_vad.onnx"]},
        sha256="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        size=643_854,
    ),
    Asset(
        "sense-voice/tokens.txt",
        {
            "global": [f"{_HF}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt"],
            "cn": [f"{_MS}/pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/resolve/master/tokens.txt"],
        },
        sha256="f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        size=315_894,
    ),
    Asset(
        "sense-voice/model.int8.onnx",
        {
            "global": [f"{_HF}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx"],
            "cn": [f"{_MS}/pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/resolve/master/model.int8.onnx"],
        },
        sha256="c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        size=239_233_841,
        note="speech recognition (SenseVoice)",
    ),
]

# Whisper large-v3-turbo converted for CTranslate2; both sources hold the same files.
_WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
WHISPER_DIR = "whisper-large-v3-turbo"
WHISPER_ASSETS = [
    Asset(
        f"{WHISPER_DIR}/{name}",
        {"global": [f"{_HF}/{_WHISPER}/resolve/main/{name}"],
         "cn": [f"{_MS}/{_WHISPER}/resolve/master/{name}"]},
        sha256=sha256, size=size, note=note,
    )
    for name, sha256, size, note in [
        ("config.json", "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e", 2_263, ""),
        ("preprocessor_config.json",
         "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711", 340, ""),
        ("tokenizer.json", "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd", 2_710_337, ""),
        ("vocabulary.json", "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1", 1_068_114, ""),
        ("model.bin", "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da", 1_617_884_929,
         "speech recognition on the graphics card (Whisper large-v3-turbo)"),
    ]
]

# Whisper on graphics cards of any maker: whisper.cpp built with Vulkan by this
# repository's CI (its own releases have no such Windows build), and the same
# model in whisper.cpp's format, 8-bit. Both model sources hold the same file.
WHISPERCPP_VERSION = "v1.9.5"
WHISPERCPP_DIR = "whisper-cpp"
_WHISPERCPP_ZIP = f"whisper-{WHISPERCPP_VERSION}-bin-win-vulkan-x64.zip"
WHISPERCPP_RUNTIME = Asset(
    f"downloads/{_WHISPERCPP_ZIP}",
    {"global": [f"https://github.com/Aminorsh/valoroyce/releases/download/"
                f"whisper.cpp-{WHISPERCPP_VERSION}-vulkan/{_WHISPERCPP_ZIP}"]},
    sha256="1694dca606a8f0dbee21440021ed73f1931a541ec35073e9c42f7cb26af3da7e",
    size=14_841_320, extract_to=WHISPERCPP_DIR, note="whisper.cpp runtime (Vulkan)",
)
WHISPERCPP_MODEL = Asset(
    "whisper-ggml/ggml-large-v3-turbo-q8_0.bin",
    {"global": [f"{_HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin"],
     "cn": [f"{_MS}/GamePP/whisper-ggml/resolve/master/ggml-large-v3-turbo-q8_0.bin"]},
    sha256="317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1",
    size=874_188_075, note="speech recognition on any graphics card (Whisper large-v3-turbo, whisper.cpp)",
)

# Chinese for spoken messages: X-ASR (a zipformer transducer, Chinese and
# English, Apache-2.0), as sherpa-onnx publishes it, 8-bit. Runs on the CPU.
XASR_NAME = "sherpa-onnx-x-asr-zipformer-transducer-zh-en-int8-2026-06-03"
XASR_DIR = "x-asr-zh-en"
XASR_ARCHIVE = Asset(
    f"downloads/{XASR_NAME}.tar.bz2",
    {"global": [f"{_GH_SHERPA}/{XASR_NAME}.tar.bz2"]},
    sha256="7592d1a1e9bc1e034c39fbaab9b0db8af46429d95d64c6e2a69593934af10e9c",
    size=136_902_463,
    extract_to=XASR_DIR, note="Chinese speech recognition for spoken messages (X-ASR)",
)

LLM_ASSET = Asset(
    "Hy-MT2-1.8B-Q4_K_M.gguf",
    {
        "global": [f"{_HF}/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q4_K_M.gguf"],
        "cn": [f"{_MS}/Tencent-Hunyuan/Hy-MT2-1.8B-GGUF/resolve/master/Hy-MT2-1.8B-Q4_K_M.gguf"],
    },
    sha256="dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
    size=1_133_080_448,
    note="translation (Hy-MT2 1.8B)",
)


def llama_assets(gpu: str) -> list[Asset]:
    system, machine = platform.system(), platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if system == "Windows":
        names = {
            "vulkan": [f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip"],
            "cpu": [f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip"],
            # The CUDA build needs NVIDIA's runtime DLLs next to it.
            "cuda": [f"llama-{LLAMA_BUILD}-bin-win-cuda-12.4-x64.zip",
                     "cudart-llama-bin-win-cuda-12.4-x64.zip"],
        }[gpu]
    elif system == "Darwin":
        names = [f"llama-{LLAMA_BUILD}-bin-macos-{'arm64' if arm else 'x64'}.tar.gz"]
    else:
        variant = "vulkan-" if gpu == "vulkan" else ""
        if gpu == "cuda":
            print("note: no prebuilt Linux CUDA bundle is wired up here; using the CPU build.")
        names = [f"llama-{LLAMA_BUILD}-bin-ubuntu-{variant}{'arm64' if arm else 'x64'}.tar.gz"]
    return [
        Asset(f"downloads/{n}", {"global": [f"{_GH_LLAMA}/{n}"]}, extract_to="llama",
              note="llama.cpp runtime")
        for n in names
    ]


# -- downloading ---------------------------------------------------------------

def _openers() -> dict[str, urllib.request.OpenerDirector]:
    out = {"": urllib.request.build_opener()}
    if urllib.request.getproxies():
        out[" (direct)"] = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return out


def _request(url: str, start: int = 0) -> urllib.request.Request:
    headers = {"User-Agent": "valoroyce-setup"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.Request(url, headers=headers)


def _probe(opener, url: str, thorough: bool = False) -> float:
    """Bytes per second over a short sample; 0 if unusable. `thorough` samples
    longer: some routes start fast and slow down after a few megabytes, which
    matters when a gigabyte follows."""
    limit, seconds = (THOROUGH_BYTES, THOROUGH_SECONDS) if thorough else (PROBE_BYTES, PROBE_SECONDS)
    try:
        t0 = time.monotonic()
        got = 0
        with opener.open(_request(url), timeout=8) as resp:
            while got < limit and time.monotonic() - t0 < seconds:
                chunk = resp.read(65536)
                if not chunk:
                    break
                got += len(chunk)
        return got / max(time.monotonic() - t0, 1e-3)
    except Exception:
        return 0.0


class Cancelled(Exception):
    """The user stopped a download. What was fetched stays, to be resumed."""


def _cli_progress(got: int, total: int, speed: float) -> None:
    pct = f"{got * 100 / total:5.1f}%" if total else ""
    print(f"\r  {got / 1e6:8.1f} MB {pct} {speed / 1e6:6.2f} MB/s ", end="", flush=True)


def _rank(urls: list[str], size: int, say=print, cancel=None):
    """(opener, url, label) routes, best first."""
    openers = _openers()
    routes = [(op, url, label) for url in urls for label, op in openers.items()]
    if len(routes) == 1 or size < SMALL:
        return routes
    say("  testing download speed...")
    timed = []
    for op, url, label in routes:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        speed = _probe(op, url, thorough=size > BIG)
        host = url.split("/")[2]
        say(f"    {host}{label}: {speed / 1e6:6.2f} MB/s")
        if speed > 0:
            timed.append((speed, op, url, label))
    timed.sort(key=lambda r: -r[0])
    return [(op, url, label) for _, op, url, label in timed]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _fetch(opener, url: str, part: Path, size: int, progress, cancel) -> None:
    """Download to `part`, resuming whatever is already there."""
    have = part.stat().st_size if part.exists() else 0
    with opener.open(_request(url, have), timeout=30) as resp:
        if have and resp.status != 206:
            have = 0  # server ignored the Range header: start over
        total = size or (have + int(resp.headers.get("Content-Length") or 0))
        t0, last, got = time.monotonic(), 0.0, have
        with open(part, "ab" if have else "wb") as f:
            while chunk := resp.read(1 << 18):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                f.write(chunk)
                got += len(chunk)
                now = time.monotonic()
                if now - last > 0.5:
                    last = now
                    progress(got, total, (got - have) / max(now - t0, 1e-3))


def present(asset: Asset, models: Path) -> bool:
    dest = models / asset.dest
    return dest.exists() and (not asset.size or dest.stat().st_size == asset.size)


def download(asset: Asset, models: Path, mirror: str, say=print, progress=None, cancel=None) -> Path:
    """Fetch one file unless it is there already. `say` gets lines of status,
    `progress` gets (bytes so far, total or 0, bytes per second), and setting
    the `cancel` event stops the download with `Cancelled`."""
    cli = progress is None
    dest = models / asset.dest
    if present(asset, models):
        say(f"ok       {asset.dest}")
        return dest
    say(f"download {asset.dest}" + (f"  - {asset.note}" if asset.note else ""))
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    errors = []
    for opener, url, label in _rank(asset.candidates(mirror), asset.size, say, cancel):
        try:
            say(f"  from {url.split('/')[2]}{label}")
            try:
                _fetch(opener, url, part, asset.size, _cli_progress if cli else progress, cancel)
            finally:
                if cli:
                    print()  # end the line the progress was written on
            if asset.sha256 and _sha256(part) != asset.sha256:
                part.unlink()
                raise OSError("checksum mismatch")
            part.replace(dest)
            return dest
        except Cancelled:
            raise
        except Exception as e:  # try the next route, keeping the partial file
            errors.append(f"{url}: {e}")
            say(f"  failed: {e}")
    raise RuntimeError(
        f"could not download {asset.dest}. You can fetch it by hand and place it at "
        f"{dest}\n  " + "\n  ".join(errors or ["no reachable source"])
    )


def _extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            t.extractall(target, filter="data")


def install_llama(models: Path, mirror: str = "auto", gpu: str = "vulkan", say=print,
                  progress=None, cancel=None) -> None:
    """The llama.cpp runtime, unpacked into models/llama."""
    marker = models / "llama" / f".{LLAMA_BUILD}-{gpu}"
    if marker.exists():
        say("ok       llama.cpp runtime")
        return
    shutil.rmtree(models / "llama", ignore_errors=True)
    for asset in llama_assets(gpu):
        _extract(download(asset, models, mirror, say, progress, cancel), models / asset.extract_to)
    marker.touch()


def whispercpp_runtime_present(models: Path) -> bool:
    return (models / WHISPERCPP_DIR / f".{WHISPERCPP_VERSION}").exists()


def install_whispercpp(models: Path, mirror: str = "auto", say=print, progress=None, cancel=None) -> None:
    """The whisper.cpp runtime (Windows, Vulkan), unpacked into models/whisper-cpp."""
    if whispercpp_runtime_present(models):
        say("ok       whisper.cpp runtime")
        return
    shutil.rmtree(models / WHISPERCPP_DIR, ignore_errors=True)
    _extract(download(WHISPERCPP_RUNTIME, models, mirror, say, progress, cancel), models / WHISPERCPP_DIR)
    (models / WHISPERCPP_DIR / f".{WHISPERCPP_VERSION}").touch()


def xasr_present(models: Path) -> bool:
    return (models / XASR_DIR / f".{XASR_NAME}").exists()


def install_xasr(models: Path, mirror: str = "auto", say=print, progress=None, cancel=None) -> None:
    """X-ASR, unpacked into models/x-asr-zh-en; the archive is removed after."""
    if xasr_present(models):
        say("ok       X-ASR")
        return
    shutil.rmtree(models / XASR_DIR, ignore_errors=True)
    archive = download(XASR_ARCHIVE, models, mirror, say, progress, cancel)
    _extract(archive, models / XASR_DIR)
    (models / XASR_DIR / f".{XASR_NAME}").touch()
    archive.unlink(missing_ok=True)


def run_setup(models: Path, mirror: str = "auto", gpu: str = "vulkan", skip_llm: bool = False,
              whisper: bool = False, whispercpp: bool = False, xasr: bool = False) -> int:
    models.mkdir(parents=True, exist_ok=True)
    print(f"models directory: {models.resolve()}")
    for asset in SPEECH_ASSETS + (WHISPER_ASSETS if whisper else []):
        download(asset, models, mirror)
    if whispercpp:
        if platform.system() != "Windows":
            print("note: the whisper.cpp runtime is built for Windows only; skipped.")
        else:
            download(WHISPERCPP_MODEL, models, mirror)
            install_whispercpp(models, mirror)
    if xasr:
        install_xasr(models, mirror)
    if not skip_llm:
        download(LLM_ASSET, models, mirror)
        install_llama(models, mirror, gpu)
    print("\nsetup complete. Start with: valoroyce")
    return 0


if __name__ == "__main__":
    sys.exit(run_setup(Path("models")))
