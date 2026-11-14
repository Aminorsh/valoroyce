"""Opt-in recording for problem reports.

Saves the audio exactly as the recogniser received it (16 kHz mono WAV) and
the captions that were shown, as two files with the same name in one folder.
Nothing is uploaded. The files contain other people's voices: whoever made
the recording decides whether to share them.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import __version__
from .config import SAMPLE_RATE, Config

FOLDER = "recordings"
MAX_SECONDS = 600.0  # a forgotten recording stops by itself (about 19 MB)


def describe(cfg: Config, source: str) -> list[str]:
    """The settings that decide how captions come out, for the log header."""
    v, a, t = cfg.vad, cfg.asr, cfg.translate
    return [
        f"valoroyce {__version__}",
        f"source: {cfg.audio.source} / {source}",
        f"vad: threshold={v.threshold} min_silence={v.min_silence} "
        f"min_speech={v.min_speech} max_speech={v.max_speech}",
        f"asr: languages={a.languages} partial_interval={a.partial_interval}",
        f"translate: backend={t.backend} prompt={t.prompt} gpu={t.gpu} partials={t.partials}",
    ]


class Recording:
    def __init__(self, folder: str | Path = FOLDER, header: list[str] = ()):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stem = folder / time.strftime("valoroyce-%Y%m%d-%H%M%S")
        self.wav_path, self.log_path = stem.with_suffix(".wav"), stem.with_suffix(".txt")
        self._wav = wave.open(str(self.wav_path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self._log = open(self.log_path, "w", encoding="utf-8")
        for line in (*header, "seconds\tkind\tutterance\tlanguage\tlatency\theard\tshown"):
            self._log.write(f"# {line}\n")
        self._lock = threading.Lock()
        self._samples = 0
        self.closed = False

    @property
    def seconds(self) -> float:
        return self._samples / SAMPLE_RATE

    def audio(self, block: np.ndarray) -> None:
        with self._lock:
            if self.closed:
                return
            pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2")
            self._wav.writeframes(pcm.tobytes())
            self._samples += len(block)
            if self.seconds >= MAX_SECONDS:
                self._close()

    def caption(self, c) -> None:
        """One line per caption shown, stamped with the position in the WAV."""
        with self._lock:
            if self.closed:
                return
            kind = "final" if c.final else "partial"
            fields = (c.source, c.text)
            heard, shown = (f.replace("\t", " ").replace("\n", " ") for f in fields)
            self._log.write(f"{self.seconds:.2f}\t{kind}\t{c.utt}\t{c.lang}\t"
                            f"{c.latency:.2f}\t{heard}\t{shown}\n")
            self._log.flush()

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if not self.closed:
            self.closed = True
            self._wav.close()
            self._log.close()
