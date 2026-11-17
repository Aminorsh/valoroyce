"""Choices made in the settings window and the tray menu, remembered between runs.

Kept apart from config.toml so the program never rewrites a file the user
edits by hand. A value saved here wins over config.toml; delete
valoroyce.state.json to go back to what the config says.

Settings are stored under "settings" by the name config.toml gives them
("overlay.font_size"). Everything else in the file (the caption window's
position) is the program's own.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from .config import Config

STATE_FILE = Path("valoroyce.state.json")

# Names used by files written before settings had their config names.
_OLD_NAMES = {
    "font_size": "overlay.font_size",
    "show_source": "overlay.show_source",
    "gpu": "translate.gpu",
    "device": "audio.device",
    "compose_target": "compose.target",
}


def load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    settings = data.get("settings")
    data["settings"] = settings = settings if isinstance(settings, dict) else {}
    for old, name in _OLD_NAMES.items():
        if old in data:
            settings.setdefault(name, data.pop(old))
    return data


def _write(data: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # a read-only folder must not break captions


def save(**changes: Any) -> None:
    """Remember values of the program's own, or settings by their short old names."""
    data = load()
    for key, value in changes.items():
        if key in _OLD_NAMES:
            data["settings"][_OLD_NAMES[key]] = value
        else:
            data[key] = value
    _write(data)


def put(name: str, value: Any) -> None:
    """Remember a setting by its config name, e.g. put("asr.order", ["sensevoice"])."""
    data = load()
    data["settings"][name] = value
    _write(data)


def forget(*names: str) -> None:
    """Drop remembered values; with no names, every setting (not the window position)."""
    data = load()
    if not names:
        data["settings"] = {}
    for name in names:
        data.pop(name, None)
        data["settings"].pop(name, None)
    _write(data)


def assign(cfg: Config, name: str, value: Any) -> bool:
    """Set the config value called `name`; False if there is none or the type is wrong."""
    obj: Any = cfg
    *path, last = name.split(".")
    for part in path:
        obj = getattr(obj, part, None)
        if not dataclasses.is_dataclass(obj):
            return False
    if not any(f.name == last for f in dataclasses.fields(obj)):
        return False
    current = getattr(obj, last)
    if isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if type(value) is not type(current):
        return False
    setattr(obj, last, value)
    return True


def apply(cfg: Config) -> None:
    """Overlay remembered choices onto the loaded config. A value that no
    longer fits (a hand-edited or outdated file) is ignored."""
    for name, value in load().get("settings", {}).items():
        if name == "compose.target" and value not in ("ja", "en"):
            continue
        assign(cfg, name, value)
