"""Voice activity detection and speech recognition (both via sherpa-onnx).

`Segmenter` turns the audio stream into utterances and exposes the one still
being spoken, so the pipeline can caption it before the speaker finishes.
`Recognizer` runs SenseVoice, which is not a streaming model but decodes a few
seconds of audio in tens of milliseconds, so re-decoding the growing utterance
is cheap enough to fake streaming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE, AsrConfig, VadConfig

_LANG_TAG = re.compile(r"<\|(\w+)\|>")
# Anything a person could read: kana, kanji, latin letters, digits.
_HAS_CONTENT = re.compile(r"[0-9A-Za-z぀-ヿ一-鿿]")


def _sherpa():
    import sherpa_onnx

    return sherpa_onnx


def require(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run `valoroyce setup` to download models.")
    return str(path)


@dataclass
class Segment:
    samples: np.ndarray
    start: int  # sample index in the stream

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


class Segmenter:
    def __init__(self, models: Path, cfg: VadConfig):
        so = _sherpa()
        vad_cfg = so.VadModelConfig()
        vad_cfg.silero_vad.model = require(models / "silero_vad.onnx")
        vad_cfg.silero_vad.threshold = cfg.threshold
        vad_cfg.silero_vad.min_silence_duration = cfg.min_silence
        vad_cfg.silero_vad.min_speech_duration = cfg.min_speech
        vad_cfg.silero_vad.max_speech_duration = cfg.max_speech
        vad_cfg.sample_rate = SAMPLE_RATE
        self._vad = so.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=30)

    def feed(self, samples: np.ndarray) -> list[Segment]:
        """Add audio; return the utterances that just ended."""
        self._vad.accept_waveform(samples)
        return self._drain()

    def flush(self) -> list[Segment]:
        self._vad.flush()
        return self._drain()

    def _drain(self) -> list[Segment]:
        done = []
        while not self._vad.empty():
            seg = self._vad.front
            done.append(Segment(np.array(seg.samples, dtype=np.float32), seg.start))
            self._vad.pop()
        return done

    def current(self) -> Segment | None:
        """The utterance in progress, if someone is talking right now."""
        if not self._vad.is_speech_detected():
            return None
        seg = self._vad.current_segment
        samples = np.array(seg.samples, dtype=np.float32)
        return Segment(samples, seg.start) if len(samples) else None


@dataclass
class Transcript:
    text: str
    lang: str  # "ja", "en", ...

    @property
    def has_content(self) -> bool:
        return bool(_HAS_CONTENT.search(self.text))


class Recognizer:
    def __init__(self, models: Path, cfg: AsrConfig):
        self.cfg = cfg
        self.languages = cfg.languages or ["ja"]
        model_dir = models / "sense-voice"
        self._model = require(model_dir / "model.int8.onnx")
        self._tokens = require(model_dir / "tokens.txt")
        self._forced = None
        # One expected language: pin it. Several: let the model pick, and keep a
        # pinned recogniser for when it picks something we do not expect.
        if len(self.languages) == 1:
            self._main = self._build(self.languages[0])
        else:
            self._main = self._build("auto")

    def _build(self, language: str):
        return _sherpa().OfflineRecognizer.from_sense_voice(
            model=self._model,
            tokens=self._tokens,
            num_threads=self.cfg.num_threads,
            provider=self.cfg.provider,
            language=language,
            use_itn=True,  # punctuation and digits
        )

    @staticmethod
    def _decode(recognizer, samples: np.ndarray) -> Transcript:
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        result = stream.result
        m = _LANG_TAG.search(result.lang or "")
        return Transcript(result.text.strip(), m.group(1) if m else "")

    def transcribe(self, samples: np.ndarray) -> Transcript:
        out = self._decode(self._main, samples)
        if len(self.languages) == 1:
            out.lang = self.languages[0]
        elif out.lang not in self.languages:
            # Short or noisy clips get mislabelled (Korean, Cantonese...).
            if self._forced is None:
                self._forced = self._build(self.languages[0])
            out = self._decode(self._forced, samples)
            out.lang = self.languages[0]
        return out
