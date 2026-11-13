"""Configuration: one TOML file, every key optional.

Defaults live here and nowhere else. `config.example.toml` documents the same
keys for users; keep the two in sync when adding one.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000  # what the VAD and ASR models expect


@dataclass
class AudioConfig:
    # "loopback": what the PC is playing. "mic": an input device. "file": a WAV.
    source: str = "loopback"
    # Case-insensitive substring of the device name. Empty = system default.
    device: str = ""
    # WAV path for source = "file".
    file: str = ""
    # Feed a file at real-time speed (True) or as fast as possible (False).
    realtime: bool = True


@dataclass
class VadConfig:
    threshold: float = 0.5
    # Silence that ends an utterance. The main latency knob for final captions.
    min_silence: float = 0.35
    min_speech: float = 0.2
    # Longer utterances are cut here so captions never stall on a monologue.
    max_speech: float = 8.0


@dataclass
class AsrConfig:
    # Languages we expect, first = default. Anything SenseVoice detects outside
    # this list is re-decoded as the default language.
    languages: list[str] = field(default_factory=lambda: ["ja", "en"])
    # While someone is still talking, re-decode this often to show a live caption.
    # 0 disables partial captions.
    partial_interval: float = 0.5
    num_threads: int = 2
    # onnxruntime provider: "cpu", "cuda" or "directml" (needs a matching build).
    provider: str = "cpu"


@dataclass
class TranslateConfig:
    # "llama": start a local llama-server ourselves. "openai": use a server that
    # is already running at base_url. "none": glossary substitution only.
    backend: str = "llama"
    target: str = "中文"
    base_url: str = "http://127.0.0.1:8177"
    api_key: str = ""
    # Model name sent to the server (matters for Ollama / LM Studio).
    model: str = "hy-mt2"
    # "hy-mt": Tencent Hy-MT prompt format. "generic": plain instructions for
    # any chat model.
    prompt: str = "hy-mt"
    # Also translate partial captions (uses the translator while people talk).
    partials: bool = True
    max_tokens: int = 96
    timeout: float = 8.0
    # backend = "llama" only:
    # Run the translation model on the graphics card. Faster, but it shares the
    # card with the game; turn off if the game stutters.
    gpu: bool = True
    gpu_layers: int = 99
    threads: int = 4
    server_args: list[str] = field(default_factory=list)


@dataclass
class GlossaryConfig:
    # Extra term files, merged over the built-in VALORANT list.
    extra: list[str] = field(default_factory=list)
    # Snap near-miss spellings from the recogniser onto known terms.
    fuzzy: bool = True


@dataclass
class OverlayConfig:
    enabled: bool = True
    font_family: str = "Microsoft YaHei"
    font_size: int = 22
    # How many captions stay on screen, and for how long after they finish.
    max_lines: int = 3
    linger: float = 6.0
    # Show the recognised Japanese/English above the Chinese.
    show_source: bool = False
    # Window geometry as fractions of the screen: centre x, bottom y, width.
    x: float = 0.5
    y: float = 0.82
    width: float = 0.5
    opacity: float = 0.72  # of the caption background


@dataclass
class Config:
    # Where models and the llama.cpp binary live.
    models_dir: str = "models"
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    glossary: GlossaryConfig = field(default_factory=GlossaryConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)

    @property
    def models_path(self) -> Path:
        return Path(os.environ.get("VALOROYCE_MODELS", self.models_dir)).expanduser()


def _fill(obj: Any, data: dict[str, Any], where: str) -> None:
    names = {f.name: f for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in names:
            raise ValueError(f"unknown config key: {where}{key}")
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"config key {where}{key} must be a table")
            _fill(current, value, f"{where}{key}.")
        else:
            if isinstance(current, float) and isinstance(value, int):
                value = float(value)
            if type(value) is not type(current):
                raise ValueError(
                    f"config key {where}{key} must be {type(current).__name__}, "
                    f"got {type(value).__name__}"
                )
            setattr(obj, key, value)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load `path`, or ./config.toml if it exists, or pure defaults."""
    cfg = Config()
    if path is None:
        default = Path("config.toml")
        if not default.exists():
            return cfg
        path = default
    with open(path, "rb") as f:
        _fill(cfg, tomllib.load(f), "")
    return cfg
