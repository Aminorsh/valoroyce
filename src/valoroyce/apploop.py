"""Capture what one program plays, or everything but one program (Windows).

Windows 10 (2004) and later can hand out a copy of the sound of one process
tree: the "application loopback" of the audio service, the same thing OBS's
Application Audio Capture uses. The program is found by its name in the list
of running processes; nothing here opens, reads or signals any other process.
The audio service does the work.

Plain ctypes, so no extra dependency. Only `audio.AppSource` uses this.
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from ctypes import POINTER, Structure, byref, c_long, c_ubyte, c_uint16, c_uint32, c_uint64, c_ulong, c_void_p
from dataclasses import dataclass

import numpy as np

if sys.platform == "win32":
    from ctypes import HRESULT, WINFUNCTYPE
else:  # importable elsewhere, for the tests of the parts that are plain Python
    from ctypes import CFUNCTYPE as WINFUNCTYPE
    HRESULT = c_long

SAMPLE_RATE = 16000

# Programs a player is likely to pick, with a name a player knows them by.
KNOWN = {
    "riotclientservices.exe": "Riot 客户端（VALORANT 语音）",
    "valorant-win64-shipping.exe": "VALORANT 游戏",
    "discord.exe": "Discord",
    "qq.exe": "QQ",
    "kook.exe": "KOOK",
    "weixin.exe": "微信",
    "wechat.exe": "微信",
    "yy.exe": "YY",
    "chrome.exe": "Chrome 浏览器",
    "msedge.exe": "Edge 浏览器",
    "firefox.exe": "Firefox 浏览器",
}


def label(name: str) -> str:
    known = KNOWN.get(name.lower())
    return f"{known}（{name}）" if known else name


# ---------------------------------------------------------------- processes

@dataclass(frozen=True)
class Proc:
    pid: int
    parent: int
    name: str


def pick_root(procs: list[Proc], name: str) -> int:
    """The process whose tree holds a program, by the program's file name.

    A program often runs as several processes of the same name (a browser
    has dozens). Take the topmost ones, and of those the one with most
    same-named processes below it. 0 when none is running.
    """
    name = name.lower()
    mine = {p.pid: p for p in procs if p.name.lower() == name}
    if not mine:
        return 0
    roots = [p.pid for p in mine.values() if p.parent not in mine or p.parent == p.pid]
    children: dict[int, list[int]] = {}
    for p in mine.values():
        children.setdefault(p.parent, []).append(p.pid)

    def size(pid: int, seen: set[int]) -> int:
        seen.add(pid)
        return 1 + sum(size(c, seen) for c in children.get(pid, ()) if c not in seen)

    return max(roots, key=lambda pid: size(pid, set())) if roots else 0


class _ProcessEntry(Structure):
    _fields_ = [
        ("dwSize", c_ulong), ("cntUsage", c_ulong), ("th32ProcessID", c_ulong),
        ("th32DefaultHeapID", c_void_p), ("th32ModuleID", c_ulong),
        ("cntThreads", c_ulong), ("th32ParentProcessID", c_ulong),
        ("pcPriClassBase", c_long), ("dwFlags", c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def processes() -> list[Proc]:
    """Every running process: id, parent id and file name. A snapshot of the
    system's process list; no process is opened."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [c_ulong, c_ulong]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [c_void_p, POINTER(_ProcessEntry)]
    k32.CloseHandle.argtypes = [c_void_p]
    snap = k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "cannot list processes")
    out = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, byref(entry))
        while ok:
            out.append(Proc(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


# ---------------------------------------------------------------------- COM

class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_IUnknown = _guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = _guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_CompletionHandler = _guid("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")
CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = _guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = _guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")

E_NOINTERFACE = -2147467262  # 0x80004002


def _call(obj: c_void_p, index: int, *args, argtypes=(), restype=HRESULT):
    """Call method `index` of the COM interface `obj`. A failing HRESULT
    raises OSError."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])
    return fn(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        _call(obj, 2, restype=c_ulong)


def _same(a: GUID, b: GUID) -> bool:
    return bytes(a) == bytes(b)


_QI = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
_REF = WINFUNCTYPE(c_ulong, c_void_p)
_DONE = WINFUNCTYPE(c_long, c_void_p, c_void_p)


class _HandlerVtbl(Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("ActivateCompleted", _DONE)]


class _Handler(Structure):
    _fields_ = [("lpVtbl", POINTER(_HandlerVtbl))]


class _Completion:
    """IActivateAudioInterfaceCompletionHandler, also agile (Windows refuses
    a handler that is not). Lives as long as this Python object."""

    def __init__(self):
        import threading
        self.done = threading.Event()
        self._fns = (_QI(self._qi), _REF(lambda _this: 1), _REF(lambda _this: 1), _DONE(self._completed))
        self._vtbl = _HandlerVtbl(*self._fns)
        self.obj = _Handler(ctypes.pointer(self._vtbl))

    def _qi(self, this, riid, out):
        iid = riid.contents
        if any(_same(iid, k) for k in (IID_IUnknown, IID_IAgileObject, IID_CompletionHandler)):
            out[0] = this
            return 0
        out[0] = None
        return E_NOINTERFACE

    def _completed(self, _this, _operation):
        self.done.set()
        return 0


class _Blob(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]


class _PropVariant(Structure):
    _fields_ = [("vt", c_uint16), ("r1", c_uint16), ("r2", c_uint16), ("r3", c_uint16), ("blob", _Blob)]


class _LoopbackParams(Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", c_ulong), ("ProcessLoopbackMode", ctypes.c_int)]


class _WaveFormat(Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
        ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
        ("cbSize", c_uint16),
    ]


_ole32 = None


def com_init() -> None:
    """Join the multi-threaded apartment on this thread (harmless when the
    thread is in one already)."""
    global _ole32
    if _ole32 is None:
        _ole32 = ctypes.WinDLL("ole32")
        _ole32.CoInitializeEx.argtypes = [c_void_p, c_ulong]
        _ole32.CoInitializeEx.restype = c_long
        _ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, c_ulong, POINTER(GUID), POINTER(c_void_p)]
        _ole32.CoCreateInstance.restype = HRESULT
        _ole32.CoTaskMemFree.argtypes = [c_void_p]
    _ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; S_FALSE / RPC_E_CHANGED_MODE are fine


_handlers: list[_Completion] = []


def _activate(pid: int, exclude: bool, timeout: float = 5.0) -> c_void_p:
    """An IAudioClient for the loopback of process tree `pid`."""
    mmdev = ctypes.WinDLL("Mmdevapi")
    fn = mmdev.ActivateAudioInterfaceAsync
    fn.argtypes = [ctypes.c_wchar_p, POINTER(GUID), POINTER(_PropVariant), c_void_p, POINTER(c_void_p)]
    fn.restype = HRESULT
    params = _LoopbackParams(1, pid, 1 if exclude else 0)  # PROCESS_LOOPBACK, include / exclude tree
    var = _PropVariant(vt=65)  # VT_BLOB
    var.blob.cbSize = ctypes.sizeof(params)
    var.blob.pBlobData = ctypes.cast(byref(params), c_void_p)
    handler = _Completion()
    _handlers.append(handler)  # Windows may let go of it after we return
    operation = c_void_p()
    fn("VAD\\Process_Loopback", byref(IID_IAudioClient), byref(var), ctypes.addressof(handler.obj), byref(operation))
    try:
        if not handler.done.wait(timeout):
            raise TimeoutError("Windows did not answer the request for program audio")
        result, client = HRESULT(), c_void_p()
        # IActivateAudioInterfaceAsyncOperation::GetActivateResult
        _call(operation, 3, byref(result), byref(client), argtypes=(POINTER(HRESULT), POINTER(c_void_p)))
        if result.value < 0:
            raise ctypes.WinError(result.value)
        return client
    finally:
        _release(operation)


# ------------------------------------------------------------------ capture

SILENT = 0x2  # AUDCLNT_BUFFERFLAGS_SILENT


class Loopback:
    """A running capture of one process tree (or of everything but it).

    Delivers mono float32 at 16 kHz: the audio service converts. `read()`
    returns what is waiting, as (samples, stream position of the first
    sample in frames); the position lets the caller see gaps, because Windows
    delivers nothing while the program is silent.
    """

    BUFFER_SECONDS = 2.0

    def __init__(self, pid: int, exclude: bool = False):
        com_init()
        self.pid, self.exclude = pid, exclude
        self.client = self.capture = c_void_p()
        self.event = None
        self.client = _activate(pid, exclude)
        try:
            fmt = _WaveFormat(3, 1, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 32, 0)  # IEEE float, mono
            flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000
            # LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY
            _call(self.client, 3, 0, flags, c_uint64(int(self.BUFFER_SECONDS * 1e7)), c_uint64(0), byref(fmt), None,
                  argtypes=(ctypes.c_int, c_ulong, c_uint64, c_uint64, POINTER(_WaveFormat), c_void_p))
            k32 = ctypes.WinDLL("kernel32")
            k32.CreateEventW.restype = c_void_p
            k32.CreateEventW.argtypes = [c_void_p, ctypes.c_int, ctypes.c_int, c_void_p]
            k32.WaitForSingleObject.argtypes = [c_void_p, c_ulong]
            k32.CloseHandle.argtypes = [c_void_p]
            self._k32 = k32
            self.event = k32.CreateEventW(None, 0, 0, None)
            _call(self.client, 13, self.event, argtypes=(c_void_p,))  # SetEventHandle
            capture = c_void_p()
            _call(self.client, 14, byref(IID_IAudioCaptureClient), byref(capture),
                  argtypes=(POINTER(GUID), POINTER(c_void_p)))  # GetService
            self.capture = capture
            _call(self.client, 10)  # Start
        except BaseException:
            self.close()
            raise

    def wait(self, seconds: float) -> None:
        """Until audio may be waiting, or `seconds` pass."""
        self._k32.WaitForSingleObject(self.event, int(seconds * 1000))

    def read(self) -> list[tuple[np.ndarray, int]]:
        out = []
        size = c_uint32()
        while True:
            _call(self.capture, 5, byref(size), argtypes=(POINTER(c_uint32),))  # GetNextPacketSize
            if size.value == 0:
                return out
            data, frames, flags, position = c_void_p(), c_uint32(), c_ulong(), c_uint64()
            _call(self.capture, 3, byref(data), byref(frames), byref(flags), byref(position), None,
                  argtypes=(POINTER(c_void_p), POINTER(c_uint32), POINTER(c_ulong), POINTER(c_uint64), c_void_p))
            n = frames.value
            if flags.value & SILENT or not data.value:
                samples = np.zeros(n, dtype=np.float32)
            else:
                samples = np.ctypeslib.as_array(ctypes.cast(data, POINTER(ctypes.c_float)), (n,)).copy()
            _call(self.capture, 4, c_uint32(n), argtypes=(c_uint32,))  # ReleaseBuffer
            out.append((samples, position.value))

    def close(self) -> None:
        if self.client:
            try:
                _call(self.client, 11)  # Stop
            except OSError:
                pass
        _release(self.capture)
        _release(self.client)
        self.capture = self.client = c_void_p()
        if self.event:
            self._k32.CloseHandle(self.event)
            self.event = None


# ----------------------------------------------------------- what is playing

def playing() -> list[tuple[str, bool]]:
    """Programs that have sound open on any output device, as (file name,
    sounding now). Our own process and Windows' system sounds are left out."""
    com_init()
    names = {p.pid: p.name for p in processes()}
    found: dict[str, bool] = {}
    enum = c_void_p()
    _ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 0x17, byref(IID_IMMDeviceEnumerator), byref(enum))
    try:
        devices = c_void_p()
        _call(enum, 3, 0, 1, byref(devices), argtypes=(ctypes.c_int, c_ulong, POINTER(c_void_p)))  # render, active
        try:
            count = c_uint32()
            _call(devices, 3, byref(count), argtypes=(POINTER(c_uint32),))
            for i in range(count.value):
                device = c_void_p()
                _call(devices, 4, c_uint32(i), byref(device), argtypes=(c_uint32, POINTER(c_void_p)))
                try:
                    _sessions(device, names, found)
                finally:
                    _release(device)
        finally:
            _release(devices)
    finally:
        _release(enum)
    return sorted(found.items(), key=lambda item: (not item[1], item[0].lower()))


def _sessions(device: c_void_p, names: dict[int, str], found: dict[str, bool]) -> None:
    manager = c_void_p()
    _call(device, 3, byref(IID_IAudioSessionManager2), 0x17, None, byref(manager),
          argtypes=(POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)))
    try:
        sessions = c_void_p()
        _call(manager, 5, byref(sessions), argtypes=(POINTER(c_void_p),))
        try:
            count = ctypes.c_int()
            _call(sessions, 3, byref(count), argtypes=(POINTER(ctypes.c_int),))
            for i in range(count.value):
                control = c_void_p()
                _call(sessions, 4, i, byref(control), argtypes=(ctypes.c_int, POINTER(c_void_p)))
                try:
                    control2 = c_void_p()
                    _call(control, 0, byref(IID_IAudioSessionControl2), byref(control2),
                          argtypes=(POINTER(GUID), POINTER(c_void_p)))
                    try:
                        pid, state = c_ulong(), ctypes.c_int()
                        _call(control2, 14, byref(pid), argtypes=(POINTER(c_ulong),), restype=c_long)
                        _call(control2, 3, byref(state), argtypes=(POINTER(ctypes.c_int),))
                        name = names.get(pid.value)
                        if pid.value and pid.value != os.getpid() and name and state.value != 2:
                            found[name] = found.get(name, False) or state.value == 1
                    finally:
                        _release(control2)
                finally:
                    _release(control)
        finally:
            _release(sessions)
    finally:
        _release(manager)


def supported() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 19041
