"""Chinese speech for spoken messages: X-ASR on the CPU.

X-ASR (a zipformer transducer for Chinese and English, Apache-2.0) heard a
player's own recorded callouts far better than Whisper, SenseVoice or
Paraformer, in about 0.1 s a sentence on the CPU. It is used for spoken
messages only: captions are Japanese and English, where Whisper does better.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE
from .setup import XASR_DIR, xasr_present


def files(models: Path) -> dict[str, str] | None:
    """The model's files, or None when it is not (completely) there."""
    if not xasr_present(models):
        return None
    found = {}
    for key, pattern in (("encoder", "encoder*.onnx"), ("decoder", "decoder*.onnx"),
                         ("joiner", "joiner*.onnx"), ("tokens", "tokens.txt")):
        hits = sorted((models / XASR_DIR).rglob(pattern))
        if not hits:
            return None
        found[key] = str(hits[0])
    return found


def available(models: Path) -> bool:
    return files(models) is not None


class XAsrRecognizer:
    id, name, label = "x-asr", "X-ASR (Chinese, CPU)", "X-ASR（中文，CPU）"

    def __init__(self, models: Path, threads: int = 2):
        import sherpa_onnx

        found = files(models)
        if found is None:
            raise RuntimeError(f"X-ASR is not in {models / XASR_DIR}")
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(num_threads=threads, **found)
        self._lock = threading.Lock()

    def transcribe(self, samples: np.ndarray, language: str = "", prompt: str = ""):
        """What was said, in Chinese (English words stay English). `language`
        and `prompt` are accepted for the caption recognisers' sake and unused."""
        from .speech import Piece, Transcript

        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
            self._rec.decode_stream(stream)
            text = stream.result.text.strip()
        seconds = len(samples) / SAMPLE_RATE
        return Transcript(text, "zh", [Piece(text, 0.0, seconds)] if text else [])


class Lazy:
    """Loads the recogniser the first time it is needed, on the thread that
    needs it (loading takes about a second)."""

    def __init__(self, models: Path):
        self.models = models
        self._real: XAsrRecognizer | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._real is not None

    def load(self) -> XAsrRecognizer:
        with self._lock:
            if self._real is None:
                self._real = XAsrRecognizer(self.models)
            return self._real

    def preload(self) -> None:
        """Load ahead of time; a failure shows when the message is recognised."""
        try:
            self.load()
        except Exception:
            pass

    def transcribe(self, samples: np.ndarray, language: str = "", prompt: str = ""):
        return self.load().transcribe(samples, language, prompt)
