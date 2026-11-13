"""Choices made in the tray menu, remembered between runs.

Kept apart from config.toml so the program never rewrites a file the user
edits by hand. A value saved here wins over config.toml; delete
valoroyce.state.json to go back to what the config says.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Config

STATE_FILE = Path("valoroyce.state.json")


def load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(**changes: Any) -> None:
    data = load()
    data.update(changes)
    try:
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # a read-only folder must not break captions


def apply(cfg: Config) -> None:
    """Overlay remembered menu choices onto the loaded config."""
    s = load()
    if isinstance(s.get("font_size"), int):
        cfg.overlay.font_size = s["font_size"]
    if isinstance(s.get("show_source"), bool):
        cfg.overlay.show_source = s["show_source"]
    if isinstance(s.get("gpu"), bool):
        cfg.translate.gpu = s["gpu"]
    if isinstance(s.get("device"), str):
        cfg.audio.device = s["device"]
