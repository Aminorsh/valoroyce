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
    # With source = "loopback": listen to one program only, by its file name
    # ("RiotClientServices.exe"), or to everything except it. Empty = every
    # program on `device`. When set, `device` is not used.
    app: str = ""
    # "only": just `app`. "except": everything but `app` (a voice-call program).
    app_mode: str = "only"
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
    # Which recogniser listens.
    #   "whisper"     Whisper on an NVIDIA graphics card: the accurate one. Needs
    #                 its model (`valoroyce setup --whisper`) and about 1.3 GB
    #                 of the card's memory.
    #   "whispercpp"  the same Whisper through whisper.cpp (Vulkan): graphics
    #                 cards of any maker, AMD and Intel included. Windows.
    #   "sensevoice"  SenseVoice on the CPU: works everywhere, much less accurate
    #                 on real voice chat.
    #   "auto"        the first recogniser of `order` that can run here.
    backend: str = "auto"
    # backend = "auto": the recognisers to try, in this order. One that is left
    # out is never used, even if its model is downloaded.
    order: list[str] = field(default_factory=lambda: ["whisper", "whispercpp", "sensevoice"])
    # Languages we expect, first = default. Speech detected as anything else is
    # recognised again as the likeliest of these.
    languages: list[str] = field(default_factory=lambda: ["ja", "en"])
    # While someone is still talking, recognise again this often (seconds of
    # audio): it shows a live caption and finishes phrases of a long sentence
    # early. 0 = captions only when the speaker pauses.
    partial_interval: float = 1.0
    # Whisper: the model's folder inside models_dir, and how its numbers are
    # held on the card ("int8_float16" needs half the memory of "float16").
    whisper_model: str = "whisper-large-v3-turbo"
    whisper_compute: str = "int8_float16"
    # Whisper on other graphics cards (whisper.cpp, Vulkan): the model file
    # inside models_dir.
    whispercpp_model: str = "whisper-ggml/ggml-large-v3-turbo-q8_0.bin"
    # SenseVoice: CPU threads, and the onnxruntime provider ("cpu", "cuda" or
    # "directml"; the last two need a matching build).
    num_threads: int = 2
    provider: str = "cpu"


@dataclass
class CustomModelConfig:
    """A caption model of the player's own choice, tried before the model of
    [translate]. A sentence it fails on, declines, or does not answer in
    `timeout` seconds is translated by that model instead."""

    #   ""        none: captions use the model of [translate] alone
    #   "openai"  a server that speaks OpenAI's API at base_url: Ollama, LM Studio,
    #             or an online service. An online service receives every sentence
    #             that is heard, and some services alter sentences on some topics.
    #   "llama"   a .gguf file of your own (`model_file` inside models_dir), started
    #             by us and served at base_url (a port of its own on 127.0.0.1)
    backend: str = ""
    base_url: str = "http://127.0.0.1:11434"
    # The key may also come from the environment variable VALOROYCE_CAPTION_API_KEY.
    api_key: str = ""
    model: str = ""
    # "generic": plain instructions for any chat model. "hy-mt": Hy-MT's format.
    prompt: str = "generic"
    timeout: float = 2.5
    # Also translate half-finished captions with this model. Off: those stay on
    # the model of [translate], and only finished sentences are sent here.
    partials: bool = False
    extra_body: dict = field(default_factory=dict)
    model_file: str = ""


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
    # Extra fields added to every request, for servers that need them, e.g.
    # {thinking = {type = "disabled"}} to switch off a model's thinking phase.
    extra_body: dict = field(default_factory=dict)
    # backend = "llama" only:
    # The caption model's file name inside models_dir. "" = the bundled one.
    model_file: str = ""
    # Run the translation model on the graphics card. Faster, but it shares the
    # card with the game; turn off if the game stutters.
    gpu: bool = True
    gpu_layers: int = 99
    threads: int = 4
    server_args: list[str] = field(default_factory=list)
    custom: CustomModelConfig = field(default_factory=CustomModelConfig)


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
    # How many rows of captions are on screen at most. The newest caption is
    # always there (its end, if it is longer than that); older ones stay while
    # they fit. More rows reach further up the screen, towards the crosshair.
    max_lines: int = 3
    # Seconds a caption stays after it is finished.
    linger: float = 6.0
    # Show the recognised Japanese/English above the Chinese.
    show_source: bool = False
    # Window geometry as fractions of the screen: centre x, bottom y, width.
    x: float = 0.5
    y: float = 0.82
    width: float = 0.5
    opacity: float = 0.72  # of the caption background


@dataclass
class ComposeConfig:
    """Typing Chinese and getting a message for teammates."""

    # Language your Chinese is translated into: "ja" or "en".
    target: str = "ja"
    # System-wide shortcut that opens the message box (Windows). "" = none.
    hotkey: str = "ctrl+alt+t"
    # Which model writes your messages. Captions are not affected.
    #   ""        the caption model (nothing extra to set up)
    #   "llama"   a second local model, `model_file` inside models_dir, started
    #             the first time the message box is opened and served at base_url
    #   "openai"  a server or online service that speaks OpenAI's API at base_url.
    #             An online service receives the text you type, nothing else.
    backend: str = ""
    model_file: str = ""
    base_url: str = "http://127.0.0.1:8178"
    # backend = "openai": the key and the model name the service expects. The
    # key may also come from the environment variable VALOROYCE_COMPOSE_API_KEY.
    api_key: str = ""
    model: str = ""
    # "generic": plain instructions for any chat model. "hy-mt": Hy-MT's format.
    prompt: str = "generic"
    timeout: float = 30.0
    # Extra request fields, as in [translate]. A model that "thinks" before it
    # answers must have that switched off here, or replies come back empty.
    extra_body: dict = field(default_factory=dict)
    # A message containing any of these words is translated by the caption
    # model and never sent to the model above. Hosted services decline or
    # alter sentences on some topics; list the words that give you trouble.
    local_only: list[str] = field(default_factory=lambda: ["台湾"])


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
    compose: ComposeConfig = field(default_factory=ComposeConfig)

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
