"""Ask Windows to run this program at full speed while it is in the background.

Recent Windows runs a program that does not own the foreground window in an
efficiency mode: a lower clock speed and, on processors that have them, the
slower cores. This program is always in the background, since the game is in
front. Measured on a laptop: one recognition pass took 0.5 s that way and
0.07 s after the request below.

The request is the documented way for a process to opt out for itself
(SetProcessInformation with ProcessPowerThrottling). It changes nothing about
any other program.
"""

from __future__ import annotations

import sys

_PROCESS_POWER_THROTTLING = 4            # PROCESS_INFORMATION_CLASS
_EXECUTION_SPEED = 0x1                   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


def full_speed(handle: int | None = None) -> bool:
    """Switch execution-speed throttling off for this process, or for a child
    process of ours given by its handle. False where that is not possible
    (not Windows, or a Windows that does not know the request)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   wintypes.DWORD]
        kernel32.SetProcessInformation.restype = wintypes.BOOL
        # "We control execution speed, and the throttled state is off."
        state = State(1, _EXECUTION_SPEED, 0)
        target = kernel32.GetCurrentProcess() if handle is None else handle
        return bool(kernel32.SetProcessInformation(target, _PROCESS_POWER_THROTTLING,
                                                   ctypes.byref(state), ctypes.sizeof(state)))
    except (AttributeError, OSError):
        return False
