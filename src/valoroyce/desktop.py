"""What a program started without a console needs: a log file instead of the
console, a message box for errors, child processes that end with it, and one
running copy at a time.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

LOG_FILE = Path("valoroyce.log")
_LOG_LIMIT = 2_000_000  # bytes; the previous log is kept as valoroyce.log.1
INSTANCE_NAME = "valoroyce-captions"

_job = None  # Windows job object that our child processes are put in


def log_to_file(path: Path = LOG_FILE) -> Path:
    """Send everything printed to a log file. A program started without a
    console has no stdout or stderr, so a print would fail or go nowhere."""
    try:
        if path.stat().st_size > _LOG_LIMIT:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass
    stream = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = stream
    return path


def message_box(title: str, text: str, error: bool = True) -> None:
    """Show a message where nobody is watching a console: a Windows message
    box, or stderr elsewhere."""
    if sys.platform == "win32":
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x10000)  # MB_SETFOREGROUND
            return
        except (AttributeError, OSError):
            pass
    stream = sys.__stderr__ or (sys.stderr if not isinstance(sys.stderr, io.StringIO) else None)
    if stream:
        print(f"{title}: {text}", file=stream)


def end_with_us(handle: int) -> bool:
    """Make Windows end a child process when this one ends, also when this one
    is killed or its console is closed. Without it llama-server outlives us,
    keeping its port and its memory on the graphics card. False where that is
    not possible."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    global _job

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Counters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _job is None:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return False
            info = Extended()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info),  # Extended info
                                                    ctypes.sizeof(info)):
                return False
            _job = job  # never closed: the handle closes when we end, and that ends the children
        return bool(kernel32.AssignProcessToJobObject(_job, handle))
    except (AttributeError, OSError):
        return False


class SingleInstance:
    """One running copy. The first one listens on a local socket; a second
    start finds it, asks it to show its settings window and stops."""

    def __init__(self, on_message, name: str = INSTANCE_NAME) -> None:
        from PySide6.QtNetwork import QLocalServer

        self._on_message, self._name = on_message, name
        self._server = QLocalServer()
        self._server.newConnection.connect(self._accept)

    @staticmethod
    def ask_running(message: str = "settings", timeout_ms: int = 1000,
                    name: str = INSTANCE_NAME) -> bool:
        """True when another copy is running; it has been sent `message`."""
        from PySide6.QtNetwork import QLocalSocket

        sock = QLocalSocket()
        sock.connectToServer(name)
        if not sock.waitForConnected(timeout_ms):
            return False
        if sys.platform == "win32":
            import ctypes

            try:  # the user just started us: let the running copy bring its window to the front
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            except (AttributeError, OSError):
                pass
        sock.write(message.encode())
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def listen(self) -> bool:
        from PySide6.QtNetwork import QLocalServer

        if self._server.listen(self._name):
            return True
        # A name left behind by a copy that crashed (only possible on Unix).
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def _accept(self) -> None:
        from PySide6.QtNetwork import QLocalSocket

        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        # The sender writes one message and hangs up, possibly before we get
        # here: collect what arrives and act once it is gone.
        data = bytearray()
        done = []

        def finish() -> None:
            data.extend(bytes(sock.readAll()))
            if done:
                return
            done.append(True)
            sock.deleteLater()
            message = data.decode(errors="replace").strip()
            if message:
                self._on_message(message)

        sock.readyRead.connect(lambda: data.extend(bytes(sock.readAll())))
        sock.disconnected.connect(finish)
        if sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            finish()
