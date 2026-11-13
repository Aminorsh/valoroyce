"""A system-wide keyboard shortcut (Windows only).

Uses RegisterHotKey, the ordinary Windows API for this: the system delivers a
message when the combination is pressed. It does not install a keyboard hook
and sees no other keystrokes.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter

_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_ID = 0x5641  # arbitrary, unique within this process


def parse(spec: str) -> tuple[int, int]:
    """'ctrl+alt+t' -> (modifier flags, virtual-key code)."""
    mods, key = 0, None
    for part in (p.strip().lower() for p in spec.split("+")):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and (part.isascii() and part.isalnum()):
            key = ord(part.upper())
        elif part[:1] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 12:
            key = 0x70 + int(part[1:]) - 1
        elif part == "space":
            key = 0x20
        else:
            raise ValueError(f"unknown key {part!r} in hotkey {spec!r}")
    if key is None or not mods:
        raise ValueError(f"hotkey {spec!r} needs at least one of ctrl/alt/shift/win and one key")
    return mods, key


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(self, app, spec: str, callback):
        super().__init__()
        self.callback = callback
        self.registered = False
        mods, key = parse(spec)
        if sys.platform != "win32":
            return
        import ctypes

        self._user32 = ctypes.windll.user32
        # Must run on the thread that owns Qt's message loop.
        self.registered = bool(self._user32.RegisterHotKey(None, _ID, mods | _MOD_NOREPEAT, key))
        if self.registered:
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and msg.wParam == _ID:
                self.callback()
                return True, 0
        return False, 0

    def release(self) -> None:
        if self.registered:
            self._user32.UnregisterHotKey(None, _ID)
            self.registered = False
