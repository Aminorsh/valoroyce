"""The shortcut of an installed copy runs this, on the Python that ships with
the installer. The first time, and after an update, it puts the Python
packages in place with a window that shows what it is doing; then, and every
other time at once, it starts the program without a console.

Only the standard library may be used here (tkinter included): nothing else
is installed yet when this runs.
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import subprocess
import sys
import sysconfig
import tempfile
import threading
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import catalog, setup

MARKER = Path(".venv") / "valoroyce-installed.txt"  # what the packages were installed for
GUI = Path(".venv") / "Scripts" / "valoroyce-gui.exe"
CONSOLE = Path(".venv") / "Scripts" / "valoroyce.exe"
CACHE = Path("cache") / "uv"  # uv's cache, on the drive of .venv: files are linked there, not copied
WHEELS = Path("cache") / "wheels"  # packages fetched by us, with progress; gone once installed
PROGRAM = Path("wheel")  # the program itself, built with the installer: nothing to build here
SAMPLE_PACKAGE = "pyside6-essentials"  # the largest download, to time the servers with
DOWNLOAD_MB = 150  # roughly what a first start fetches


def fingerprint(root: Path) -> str:
    """Changes whenever an update brings other packages or another program."""
    digest = hashlib.sha256()
    for name in ("uv.lock", "pyproject.toml"):
        digest.update((root / name).read_bytes())
    for source in sorted((root / "src").rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            digest.update(str(source.relative_to(root)).encode())
            digest.update(source.read_bytes())
    return digest.hexdigest()


def ready(root: Path) -> bool:
    try:
        return (root / GUI).is_file() and (root / MARKER).read_text().strip() == fingerprint(root)
    except OSError:
        return False


@dataclass(frozen=True)
class Wheel:
    filename: str
    path: str  # on the package servers; the same on PyPI's and on the mirror
    sha256: str
    size: int


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _tags(plat: str = "", version: tuple[int, int] | None = None) -> list[tuple[str, str, str]]:
    """Wheel tags this Python can install, best first: the Windows part of
    what pip computes. Elsewhere none, and uv fetches the packages itself."""
    if not plat:
        if sys.platform != "win32":
            return []
        plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")  # win_amd64
    major, minor = version or sys.version_info[:2]
    cp = f"cp{major}{minor}"
    tags = [(cp, cp, plat), (cp, "abi3", plat), (cp, "none", plat)]
    tags += [(f"cp{major}{m}", "abi3", plat) for m in range(minor - 1, 1, -1)]
    pys = [f"py{major}{minor}", f"py{major}"] + [f"py{major}{m}" for m in range(minor - 1, -1, -1)]
    tags += [(py, "none", plat) for py in pys]
    tags += [(cp, "none", "any")] + [(py, "none", "any") for py in pys]
    return tags


def _wheel_tags(filename: str) -> set[tuple[str, str, str]]:
    py, abi, plat = filename[:-len(".whl")].split("-")[-3:]
    return {(p, a, q) for p in py.split(".") for a in abi.split(".") for q in plat.split(".")}


def locked_wheels(lock: Path, packages: list[tuple[str, str]],
                  tags: list[tuple[str, str, str]] | None = None) -> list[Wheel] | None:
    """The wheel of each (name, version) in the lock file that fits this
    Python best. None when one of them has no fitting wheel on PyPI."""
    tags = _tags() if tags is None else tags
    rank = {tag: i for i, tag in enumerate(tags)}
    with open(lock, "rb") as f:
        entries = {(_norm(e.get("name", "")), e.get("version")): e
                   for e in tomllib.load(f).get("package", [])}
    files = catalog._PYPI[1]
    out = []
    for name, version in packages:
        best = None
        for wheel in entries.get((_norm(name), version), {}).get("wheels", []):
            url, digest = wheel.get("url", ""), wheel.get("hash", "")
            if not url.startswith(files) or not digest.startswith("sha256:"):
                continue
            filename = url.rsplit("/", 1)[-1]
            fits = [rank[tag] for tag in _wheel_tags(filename) if tag in rank]
            if fits and (best is None or min(fits) < best[0]):
                best = (min(fits), Wheel(filename, url[len(files):], digest[len("sha256:"):],
                                         wheel.get("size", 0)))
        if best is None:
            return None
        out.append(best[1])
    return out


def fetch_wheels(wheels: list[Wheel], folder: Path, routes: list[tuple], progress,
                 cancel: threading.Event | None = None) -> None:
    """Download into `folder`, resuming what is there, trying the routes in
    turn. `progress` gets (bytes so far, of total, bytes per second)."""
    folder.mkdir(parents=True, exist_ok=True)
    total, done = sum(w.size for w in wheels), 0
    for wheel in wheels:
        dest = folder / wheel.filename
        if not (dest.is_file() and dest.stat().st_size == wheel.size):
            part = dest.with_name(dest.name + ".part")
            errors = []
            for _index, files, label, _env, opener in routes:
                try:
                    setup._fetch(opener, files + wheel.path, part, wheel.size,
                                 lambda got, _total, speed: progress(done + got, total, speed),
                                 cancel)
                    if setup._sha256(part) != wheel.sha256:
                        part.unlink()
                        raise OSError("checksum mismatch")
                    part.replace(dest)
                    break
                except setup.Cancelled:
                    raise
                except Exception as e:  # the next route, keeping what was fetched
                    errors.append(f"{files.split('/')[2]}{label}: {e}")
            else:
                raise RuntimeError(f"{wheel.filename}:\n" + "\n".join(errors or ["no server"]))
        done += wheel.size
        progress(done, total, 0.0)


def unpacked_bytes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        with zipfile.ZipFile(path) as z:
            total += sum(info.file_size for info in z.infolist())
    return total


def _watched(cmd: list[str], root: Path, env: dict, say, cancel, progress, total: int,
             watch: list[Path]) -> None:
    """Run uv, telling `progress` how much it has written into `watch` so far."""
    before = [catalog.tree_bytes(p) if p.exists() else 0 for p in watch]
    failure: list[BaseException] = []

    def work():
        try:
            catalog._run(cmd, say, cancel, cwd=root, env=env)
        except BaseException as e:  # handed over to the waiting thread
            failure.append(e)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(1.0)
        written = max((catalog.tree_bytes(p) if p.exists() else 0) - b for p, b in zip(watch, before))
        progress(max(0, min(written, total)), total, 0.0)
    if failure:
        raise failure[0]
    progress(total, total, 0.0)


def _plan(uv: str, venv_python: str, wanted: Path, index: str, root: Path,
          env: dict) -> list[tuple[str, str]]:
    """The (name, version) pairs uv would install into .venv. Without an
    index uv looks only at .venv and its cache (and fails if that is not
    enough)."""
    where = ["--default-index", index] if index else ["--offline"]
    proc = subprocess.run([uv, "pip", "install", "--dry-run", "--python", venv_python,
                           *where, "-r", str(wanted)],
                          cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, creationflags=catalog._NO_WINDOW)
    if proc.returncode:
        raise RuntimeError("\n".join((proc.stderr or proc.stdout).strip().splitlines()[-5:]))
    return re.findall(r"^ \+ (\S+)==(\S+)", proc.stdout + "\n" + proc.stderr, re.M)


def _install_program(uv: str, venv_python: str, root: Path, env: dict, say, cancel,
                     index: str = "") -> None:
    say("正在安装 Valoroyce…")
    shipped = sorted((root / PROGRAM).glob("valoroyce-*.whl"))
    where = ["--no-index", str(shipped[-1])] if shipped else ["--default-index", index, "."]
    catalog._run([uv, "pip", "install", "--python", venv_python, "--no-deps",
                  "--reinstall-package", "valoroyce", *where], say, cancel, cwd=root, env=env)


def _complete_offline(uv: str, venv_python: str, wanted: Path, root: Path, env: dict) -> bool:
    """True when .venv already has every package (an update of the program
    alone) and the program comes as a built wheel: no network needed."""
    if not any((root / PROGRAM).glob("valoroyce-*.whl")):
        return False
    try:
        return not _plan(uv, venv_python, wanted, "", root, env)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def uv_env(root: Path, base: dict | None = None) -> dict[str, str]:
    return {**(os.environ if base is None else base), "UV_PYTHON_DOWNLOADS": "never",
            "UV_CACHE_DIR": str(root / CACHE)}


def prepare(root: Path, say, cancel: threading.Event | None = None, progress=None) -> None:
    """Make .venv hold the locked packages and this version of the program.
    Packages the settings window added (the Whisper libraries) are kept.
    `progress` gets (step, bytes so far, of total, bytes per second), the
    step being "download" or "install"."""
    progress = progress or (lambda *_: None)
    uv, python = str(root / "uv.exe"), root / "python" / "python.exe"
    env = uv_env(root)
    quiet = lambda _line: None  # noqa: E731
    say("正在准备 Python 环境…")
    catalog._run([uv, "venv", "--quiet", "--allow-existing", "--python", str(python), ".venv"],
                 quiet, cancel, cwd=root, env=env)
    venv_python = str(root / ".venv" / "Scripts" / "python.exe")
    with tempfile.TemporaryDirectory() as tmp:
        wanted = Path(tmp) / "app.txt"
        catalog._run([uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--extra", "app",
                      "--quiet", "-o", str(wanted)], quiet, cancel, cwd=root, env=env)
        if _complete_offline(uv, venv_python, wanted, root, env):
            _install_program(uv, venv_python, root, env, say, cancel)
            routes = []
        else:
            routes = catalog.package_routes("auto", catalog._sample_wheel(root, SAMPLE_PACKAGE), say)
        errors = []
        for route in routes:
            index, files, label = route[0], route[1], route[2]
            route_env = uv_env(root, route[3])
            host = files.split("/")[2] + label
            try:
                packages = _plan(uv, venv_python, wanted, index, root, route_env)
                wheels = locked_wheels(root / "uv.lock", packages) if packages else []
                if wheels is None:  # not all known here: uv fetches them, without a measure
                    say(f"正在从 {host} 下载运行组件（约 {DOWNLOAD_MB} MB，只在第一次需要）…")
                    catalog._run([uv, "pip", "install", "--python", venv_python, "--default-index",
                                  index, "-r", str(wanted)], say, cancel, cwd=root, env=route_env)
                elif wheels:
                    mb = sum(w.size for w in wheels) / 1e6
                    say(f"正在从 {host} 下载运行组件（{len(wheels)} 个文件，共 {mb:.0f} MB，"
                        f"只在第一次需要）…")
                    # This route for every file, the others behind it.
                    fetch_wheels(wheels, root / WHEELS, [route, *(r for r in routes if r is not route)],
                                 lambda got, total, speed: progress("download", got, total, speed),
                                 cancel)
                    say("正在安装运行组件…")
                    paths = [root / WHEELS / w.filename for w in wheels]
                    _watched([uv, "pip", "install", "--python", venv_python, "--no-index",
                              "--find-links", str(root / WHEELS), "-r", str(wanted)],
                             root, route_env, say, cancel,
                             lambda got, total, speed: progress("install", got, total, speed),
                             unpacked_bytes(paths), [root / CACHE, root / ".venv"])
                _install_program(uv, venv_python, root, route_env, say, cancel, index)
                break
            except catalog.Cancelled:
                raise
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                errors.append(f"{host}: {e}")
        else:
            if routes:
                raise RuntimeError("\n".join(errors) or "no package server could be reached")
    catalog.delete(root / WHEELS)
    (root / MARKER).write_text(fingerprint(root) + "\n")


def launch(root: Path, args: list[str] = ()) -> None:
    env = {**uv_env(root), "VALOROYCE_UV": str(root / "uv.exe")}  # also for the Whisper libraries
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS, NEW_PROCESS_GROUP
    subprocess.Popen([str(root / GUI), *args], cwd=root, env=env, creationflags=flags,
                     close_fds=True)


STEPS = {"download": "第 1 步（共 2 步）：下载运行组件", "install": "第 2 步（共 2 步）：安装运行组件"}


def _duration(seconds: float) -> str:
    return f"约 {max(1, round(seconds))} 秒" if seconds < 90 else f"约 {round(seconds / 60)} 分钟"


def describe(step: str, got: int, total: int, speed: float) -> str:
    line = f"{got / 1e6:.0f} / {total / 1e6:.0f} MB"
    if step == "download" and speed > 0:
        line += f" · {speed / 1e6:.1f} MB/s · 还剩{_duration((total - got) / speed)}"
    return line


class Rate:
    """Speed over a whole step. The downloader's own figure starts again with
    each file and reads low while a connection opens, which made the time
    left jump between seconds and minutes."""

    SETTLE = 2.0  # seconds before a speed is given at all

    def __init__(self, clock=None):
        import time

        self.clock = clock or time.monotonic
        self.step, self.start, self.base = None, 0.0, 0

    def __call__(self, step: str, got: int) -> float:
        now = self.clock()
        if step != self.step:
            self.step, self.start, self.base = step, now, got
        elapsed = now - self.start
        return (got - self.base) / elapsed if elapsed >= self.SETTLE else 0.0


class Window:
    """Progress of `prepare`, with retry and what to do when it fails."""

    def __init__(self, root: Path):
        import tkinter as tk
        from tkinter import ttk

        self.root_dir, self.lines, self.result = root, queue.Queue(), None
        self.rate = Rate()
        self.cancel = threading.Event()
        self.tk = tk.Tk()
        self.tk.title("Valoroyce · 首次启动")
        self.tk.geometry("620x360")
        self.tk.minsize(480, 300)
        icon = root / "src" / "valoroyce" / "data" / "icon.png"
        if icon.exists():
            try:
                self._icon = tk.PhotoImage(file=str(icon))
                self.tk.iconphoto(True, self._icon)
            except tk.TclError:
                pass
        frame = ttk.Frame(self.tk, padding=16)
        frame.pack(fill="both", expand=True)
        self.title = ttk.Label(frame, font=("Microsoft YaHei UI", 12, "bold"),
                               text="正在准备 Valoroyce，请稍候")
        self.title.pack(anchor="w")
        self.hint = ttk.Label(frame, wraplength=580, justify="left", text=(
            "第一次启动和每次更新后需要下载运行组件。完成后程序会自动打开。\n"
            "语音模型稍后在程序的「设置与模型」窗口里下载。"))
        self.hint.pack(anchor="w", pady=(6, 10))
        self.step = ttk.Label(frame, text="")
        self.step.pack(anchor="w")
        self.bar = ttk.Progressbar(frame, mode="indeterminate", maximum=1000)
        self.bar.pack(fill="x", pady=(2, 0))
        self.bar.start(12)
        self.detail = ttk.Label(frame, text="")
        self.detail.pack(anchor="e")
        self.log = tk.Text(frame, height=8, wrap="word", relief="flat",
                           background=self.tk.cget("background"), font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=10)
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e")
        self.retry = ttk.Button(buttons, text="重试", command=self.start)
        self.close = ttk.Button(buttons, text="取消", command=self.quit)
        self.close.pack(side="right")
        self.tk.protocol("WM_DELETE_WINDOW", self.quit)

    def say(self, line: str) -> None:
        self.lines.put(("line", line))

    def progress(self, step: str, got: int, total: int, speed: float) -> None:
        self.lines.put(("progress", step, got, total, speed))

    def _show(self, step: str, got: int, total: int, speed: float) -> None:
        if str(self.bar.cget("mode")) != "determinate":
            self.bar.stop()
            self.bar.config(mode="determinate")
        self.step.config(text=STEPS.get(step, ""))
        self.bar.config(value=1000 * got / total if total else 0)
        self.detail.config(text=describe(step, got, total, self.rate(step, got)))

    def start(self) -> None:
        self.retry.pack_forget()
        self.close.config(text="取消")
        self.title.config(text="正在准备 Valoroyce，请稍候")
        self.bar.config(mode="indeterminate", value=0)
        self.bar.start(12)
        self.step.config(text="")
        self.detail.config(text="")
        self.rate = Rate()  # a retry measures afresh
        self.cancel.clear()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            prepare(self.root_dir, self.say, self.cancel, self.progress)
            self.lines.put(("done", ""))
        except catalog.Cancelled:
            self.lines.put(("cancelled", ""))
        except Exception as e:
            self.lines.put(("failed", str(e) or type(e).__name__))

    def _poll(self) -> None:
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                break
            if item[0] == "line":
                self.log.insert("end", item[1] + "\n")
                self.log.see("end")
            elif item[0] == "progress":
                self._show(*item[1:])
            else:
                self._finish(*item)
                return
        self.tk.after(100, self._poll)

    def _finish(self, how: str, why: str) -> None:
        self.bar.stop()
        if how == "done":
            self.result = True
            self._destroy()
            return
        if how == "cancelled":
            self._destroy()
            return
        self.title.config(text="下载运行组件失败")
        self.log.insert("end", "\n" + why + "\n")
        self.log.see("end")
        self.hint.config(text=(
            "请检查网络连接（如果用了加速器或代理，确认它在运行），然后点「重试」。\n"
            "已经下载的部分会保留。如果一直失败，把这个窗口里的文字截图发给开发者。"))
        self.retry.pack(side="right", padx=(0, 8))
        self.close.config(text="关闭")
        self.tk.after(100, self._poll)

    def quit(self) -> None:
        self.cancel.set()  # stops a running download within a moment
        self.tk.after(300, self._destroy)

    def _destroy(self) -> None:
        try:
            self.tk.destroy()
        except Exception:  # already gone
            pass

    def run(self) -> bool:
        self.start()
        self.tk.after(100, self._poll)
        self.tk.mainloop()
        return bool(self.result)


def main(root: Path, args: list[str] | None = None) -> int:
    args = sys.argv[1:] if args is None else args
    os.chdir(root)  # models, settings and the log live next to the program
    if args[:1] == ["--quit"]:  # the installer, before it replaces files
        if (root / CONSOLE).is_file():
            subprocess.run([str(root / CONSOLE), "quit"], cwd=root, timeout=30,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        return 0
    if not ready(root) and not Window(root).run():
        return 1
    launch(root, args)
    return 0
