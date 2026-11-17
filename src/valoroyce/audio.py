"""Audio sources. Each yields mono float32 blocks at 16 kHz.

Nothing here touches the game: loopback capture asks Windows for a copy of what
an output device is playing (the same mechanism OBS uses).
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import wave
from collections.abc import Iterator
from fractions import Fraction

import numpy as np

from .config import SAMPLE_RATE, AudioConfig

BLOCK_SECONDS = 0.1


class Resampler:
    """Streaming resampler to 16 kHz: windowed-sinc low-pass, then linear pick.

    Speech-recognition quality, not hi-fi. Keeps filter and phase state across
    blocks so block boundaries do not click.
    """

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE, taps: int = 63):
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self.passthrough = src_rate == dst_rate
        self.step = Fraction(src_rate, dst_rate)
        cutoff = 0.45 * min(src_rate, dst_rate) / src_rate  # cycles per sample
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        self.h = (h / h.sum()).astype(np.float32)
        self.tail = np.zeros(taps - 1, dtype=np.float32)  # filter history
        self.last = np.float32(0.0)  # last filtered sample of the previous block
        self.pos = Fraction(0)  # next output position, in filtered-input samples

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.passthrough or len(block) == 0:
            return block.astype(np.float32, copy=False)
        x = np.concatenate([self.tail, block.astype(np.float32, copy=False)])
        y = np.convolve(x, self.h, mode="valid")  # len == len(block)
        self.tail = x[-(len(self.h) - 1):]
        # Position p (in this block's coordinates) interpolates y[p], where
        # y[-1] is the previous block's last sample.
        ext = np.concatenate([[self.last], y])
        count = int((Fraction(len(y) - 1) - self.pos) // self.step) + 1
        if count <= 0:
            self.pos -= len(y)
            self.last = y[-1]
            return np.zeros(0, dtype=np.float32)
        idx = float(self.pos) + float(self.step) * np.arange(count)
        out = np.interp(idx + 1.0, np.arange(len(ext)), ext).astype(np.float32)
        self.pos = self.pos + self.step * count - len(y)
        self.last = y[-1]
        return out


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a PCM WAV as mono float32 in [-1, 1]."""
    with wave.open(path, "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"{path}: unsupported sample width {width * 8} bit")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


class FileSource:
    """Play a WAV through the pipeline, at real-time pace by default."""

    def __init__(self, path: str, realtime: bool = True):
        samples, rate = read_wav(path)
        self.samples = Resampler(rate).process(samples)
        self.realtime = realtime
        self._late = 0.0

    def lag(self) -> float:
        """Seconds the reader is behind the file's real-time schedule."""
        return self._late

    def __iter__(self) -> Iterator[np.ndarray]:
        block = int(SAMPLE_RATE * BLOCK_SECONDS)
        start = time.monotonic()
        for i in range(0, len(self.samples), block):
            if self.realtime:
                due = start + (i + block) / SAMPLE_RATE
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self._late = max(0.0, -delay)
            yield self.samples[i:i + block]
        self._late = 0.0
        # Trailing silence so the VAD can close the last utterance.
        for _ in range(10):
            if self.realtime:
                time.sleep(BLOCK_SECONDS)
            yield np.zeros(block, dtype=np.float32)


def _soundcard():
    try:
        import soundcard
    except ImportError as e:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "live capture needs the 'soundcard' package: pip install 'valoroyce[capture]'"
        ) from e
    return soundcard


def list_devices() -> list[tuple[str, str, bool]]:
    """(kind, name, is_default) for every capture candidate."""
    sc = _soundcard()
    out = []
    default_speaker = sc.default_speaker().name
    for s in sc.all_speakers():
        out.append(("loopback", s.name, s.name == default_speaker))
    default_mic = sc.default_microphone().name
    for m in sc.all_microphones(include_loopback=False):
        out.append(("mic", m.name, m.name == default_mic))
    return out


def _no_false_silence(rec) -> None:
    """Stop soundcard from splicing silence into live audio.

    A silent device delivers nothing on Windows, so soundcard hands out zeros
    once 40 ms pass without data. It goes by the clock alone: when the reading
    thread was only kept waiting, the audio has arrived meanwhile and the zeros
    end up in front of it (measured: about 50 ms every few seconds, in the
    middle of words). Zeros are dropped when audio turns out to be waiting.
    """
    read = getattr(rec, "_record_chunk", None)
    available = getattr(rec, "_capture_available_frames", None)
    if read is None or available is None:
        return  # another backend or version of soundcard: leave it alone

    def record_chunk():
        nothing_waiting = available() == 0
        chunk = read()
        if nothing_waiting and len(chunk) and not chunk.any() and available() > 0:
            chunk = read()
        return chunk

    rec._record_chunk = record_chunk


class DeviceSource:
    """Capture from an output device (loopback) or an input device (mic).

    The device is read by a thread that does nothing else. Windows keeps only a
    short buffer of captured audio and throws away what is not collected in
    time, so reading must never wait for recognition. Blocks wait in a queue
    instead, and `lag()` says how much audio is waiting.
    """

    CAPTURE_RATE = 48000
    # Buffer asked of Windows, for when the capture thread itself is held up.
    # The default is one device period (10 ms).
    BUFFER_SECONDS = 2.0

    def __init__(self, device: str = "", loopback: bool = True):
        sc = _soundcard()
        if loopback:
            if device:
                matches = [s for s in sc.all_speakers() if device.lower() in s.name.lower()]
                if not matches:
                    raise RuntimeError(f"no output device matching {device!r}")
                speaker = matches[0]
            else:
                speaker = sc.default_speaker()
            # A loopback "microphone" with the speaker's id records what it plays.
            self.mic = sc.get_microphone(speaker.id, include_loopback=True)
        elif device:
            matches = [m for m in sc.all_microphones() if device.lower() in m.name.lower()]
            if not matches:
                raise RuntimeError(f"no input device matching {device!r}")
            self.mic = matches[0]
        else:
            self.mic = sc.default_microphone()
        self.name = self.mic.name
        self._stop = threading.Event()
        self._blocks: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the capture to let go of the device. Ending the process
        while it is inside a Windows audio call crashes it."""
        if self._thread is not None:
            self._thread.join(timeout)

    def lag(self) -> float:
        """Seconds of audio captured but not handed out yet."""
        return self._blocks.qsize() * BLOCK_SECONDS

    def _capture(self) -> None:
        frames = int(self.CAPTURE_RATE * BLOCK_SECONDS)
        options = {}
        if sys.platform == "win32":  # elsewhere a large block size means late audio
            options["blocksize"] = int(self.CAPTURE_RATE * self.BUFFER_SECONDS)
        try:
            with self.mic.recorder(samplerate=self.CAPTURE_RATE, **options) as rec:
                _no_false_silence(rec)
                while not self._stop.is_set():
                    self._blocks.put(rec.record(numframes=frames))  # (frames, channels) float32
        except BaseException as e:  # hand the failure to whoever is reading
            self._blocks.put(e)
        else:
            self._blocks.put(None)

    def __iter__(self) -> Iterator[np.ndarray]:
        resampler = Resampler(self.CAPTURE_RATE)
        self._thread = threading.Thread(target=self._capture, name="capture", daemon=True)
        self._thread.start()
        while True:
            data = self._blocks.get()
            if data is None:
                return
            if isinstance(data, BaseException):
                raise data
            mono = data.mean(axis=1) if data.ndim > 1 else data
            yield resampler.process(mono)


def open_source(cfg: AudioConfig):
    if cfg.source == "file":
        if not cfg.file:
            raise ValueError("audio.source = 'file' needs audio.file")
        return FileSource(cfg.file, cfg.realtime)
    if cfg.source in ("loopback", "mic"):
        return DeviceSource(cfg.device, loopback=cfg.source == "loopback")
    raise ValueError(f"unknown audio.source: {cfg.source!r}")
