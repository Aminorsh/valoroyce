"""Voice activity detection and speech recognition.

`Segmenter` turns the audio stream into utterances and exposes the one still
being spoken, so it can be captioned before the speaker finishes.

Two recognisers, neither of them a streaming model; both are fast enough that
the growing utterance is simply recognised again:

- `WhisperRecognizer`: Whisper large-v3-turbo on an NVIDIA graphics card. Far
  more accurate on game voice chat, and it says where each phrase ends.
- `Recognizer`: SenseVoice on the CPU, for machines without such a card.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE, AsrConfig, VadConfig

_LANG_TAG = re.compile(r"<\|(\w+)\|>")
# Anything a person could read: kana, kanji, latin letters, digits.
_HAS_CONTENT = re.compile(r"[0-9A-Za-z぀-ヿ一-鿿]")


def _sherpa():
    import sherpa_onnx

    return sherpa_onnx


def require(path: Path, setup: str = "valoroyce setup") -> str:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run `{setup}` to download models.")
    return str(path)


# The voice detector starts an utterance at the first sound it is sure of,
# which is after the first syllable began ("スパイク" was then recognised as
# "マイク"). So an utterance is handed on with this much of the audio before
# it. Measured on a recorded match: it recovered most of what recognising
# single utterances lost against recognising the whole file; audio after the
# utterance recovered nothing.
LEAD_SECONDS = 0.4


@dataclass
class Segment:
    samples: np.ndarray
    start: int     # sample index in the stream
    lead: int = 0  # samples at the start that come before the detected speech

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


class Lead:
    """The stream as received, kept long enough to hand out an utterance with
    the audio just before it. No audio is handed out in two utterances."""

    def __init__(self, keep_seconds: float, lead: float = LEAD_SECONDS):
        self._keep = int(keep_seconds * SAMPLE_RATE)
        self._lead = int(lead * SAMPLE_RATE)
        self._audio = np.zeros(0, dtype=np.float32)
        self._from = 0       # stream position of _audio[0]
        self._given_to = 0   # where the last finished utterance ended

    def add(self, samples: np.ndarray) -> None:
        self._audio = np.concatenate([self._audio, np.asarray(samples, dtype=np.float32)])
        if len(self._audio) > 2 * self._keep:
            self._from += len(self._audio) - self._keep
            self._audio = self._audio[-self._keep:]

    def segment(self, start: int, length: int, finished: bool) -> Segment:
        """The speech at [start, start + length) and the audio before it, as
        far back as the utterance before allows."""
        end = start + length
        lo = min(start, max(start - self._lead, self._given_to, self._from))
        if finished:
            self._given_to = end
        return Segment(self._audio[lo - self._from:end - self._from].copy(), lo, start - lo)


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
        buffer = 30
        self._vad = so.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=buffer)
        self._stream = Lead(keep_seconds=max(buffer, cfg.max_speech) + 5)

    def feed(self, samples: np.ndarray) -> list[Segment]:
        """Add audio; return the utterances that just ended."""
        self._stream.add(samples)
        self._vad.accept_waveform(samples)
        return self._drain()

    def flush(self) -> list[Segment]:
        self._vad.flush()
        return self._drain()

    def _drain(self) -> list[Segment]:
        done = []
        while not self._vad.empty():
            seg = self._vad.front
            done.append(self._stream.segment(seg.start, len(seg.samples), True))
            self._vad.pop()
        return done

    def current(self) -> Segment | None:
        """The utterance in progress, if someone is talking right now."""
        if not self._vad.is_speech_detected():
            return None
        seg = self._vad.current_segment
        length = len(seg.samples)
        return self._stream.segment(seg.start, length, False) if length else None


@dataclass
class Piece:
    """A phrase of a transcript and its place in the audio, in seconds."""

    text: str
    start: float
    end: float


@dataclass
class Transcript:
    text: str
    lang: str  # "ja", "en", ...
    # The phrases the text consists of, when the model says where they end.
    pieces: list[Piece] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(_HAS_CONTENT.search(self.text))


def join(texts: list[str], lang: str) -> str:
    """Phrases back into one line. Japanese is written without spaces."""
    return ("" if lang in ("ja", "zh") else " ").join(t for t in texts if t)


class Recognizer:
    """SenseVoice on the CPU: a few seconds of audio in tens of milliseconds."""

    id, name, label = "sensevoice", "SenseVoice (CPU)", "SenseVoice（CPU）"

    def __init__(self, models: Path, cfg: AsrConfig):
        self.cfg = cfg
        self.languages = cfg.languages or ["ja"]
        model_dir = models / "sense-voice"
        self._model = require(model_dir / "model.int8.onnx")
        self._tokens = require(model_dir / "tokens.txt")
        self._forced = None
        self._pinned: dict[str, object] = {}  # recognisers asked for one language
        self._lock = threading.Lock()  # captions and spoken messages share it
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

    def transcribe(self, samples: np.ndarray, language: str = "", prompt: str = "") -> Transcript:
        """What was said. `language` given: recognised as that language only.
        SenseVoice takes no prompt."""
        with self._lock:
            if language:
                if language not in self._pinned:
                    self._pinned[language] = self._build(language)
                out = self._decode(self._pinned[language], samples)
                out.lang = language
                return out
            return self._expected(samples)

    def _expected(self, samples: np.ndarray) -> Transcript:
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


# -- Whisper on the graphics card ---------------------------------------------

WHISPER_SETUP = "valoroyce setup --whisper"

# Whisper was trained on subtitles and, hearing no words, writes what subtitles
# end with. A pass is dropped when the model itself rates its text this
# unlikely (mean log-probability per token) or when the text repeats itself
# this much (compression ratio); a phrase is dropped when it is one of the
# credits nobody says in a match.
MADE_UP_LOGPROB = -1.0
MADE_UP_REPETITION = 2.4
_CREDITS = re.compile(
    r"ご視聴|視聴ありがとう|チャンネル登録|次回予告|thanks? (you )?for watching|subscribe|amara\.org",
    re.IGNORECASE)
# Game sound and one-word shouts are often taken for another language, and
# recognising them as that language invents sentences. The first expected
# language is used unless another one is this probable. Measured on a Japanese
# match: noise reached 0.84 for English, clean English speech gives 1.00.
OTHER_LANGUAGE = 0.9
# Whisper writes Chinese in traditional characters about as often as in
# simplified ones; a sentence in simplified characters before the audio
# steers it to simplified.
LANGUAGE_PROMPT = {"zh": "以下是普通话的句子。"}


def _nvidia_libraries() -> None:
    """Let Windows find the NVIDIA libraries that were installed as packages."""
    if sys.platform != "win32":
        return
    for base in sys.path:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            d = Path(base) / sub
            if d.is_dir() and str(d) not in os.environ.get("PATH", ""):
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def whisper_unavailable(models: Path, cfg: AsrConfig) -> str:
    """Why Whisper cannot be used here, or "" when it can."""
    if not (models / cfg.whisper_model / "model.bin").exists():
        return f"its model is not in {models / cfg.whisper_model} (get it with `{WHISPER_SETUP}`)"
    try:
        import ctranslate2
        import faster_whisper  # noqa: F401
    except ImportError:
        return "its libraries are not installed (the 'whisper' extra)"
    if ctranslate2.get_cuda_device_count() < 1:
        return "no NVIDIA graphics card was found"
    return ""


class WhisperRecognizer:
    """Whisper through CTranslate2: about a third of a second per pass on a
    laptop RTX 4060 whatever the length, since the model always looks at a
    30-second window."""

    id, name, label = "whisper", "Whisper (graphics card)", "Whisper（显卡）"

    def __init__(self, models: Path, cfg: AsrConfig):
        self.languages = cfg.languages or ["ja"]
        problem = whisper_unavailable(models, cfg)
        if problem:
            raise RuntimeError(f"Whisper cannot run: {problem}. Or set [asr] backend = "
                               '"sensevoice" in config.toml to recognise on the CPU.')
        _nvidia_libraries()
        from faster_whisper import WhisperModel

        class Model(WhisperModel):
            # With the language left open the library encodes the same audio
            # twice, once to detect the language and once to transcribe. The
            # encoder is most of the work, so its last result is kept.
            _kept = (None, None)

            def encode(self, features):
                key = hash(features.tobytes())
                if self._kept[0] != key:
                    self._kept = (key, super().encode(features))
                return self._kept[1]

        self._model = Model(str(models / cfg.whisper_model), device="cuda",
                            compute_type=cfg.whisper_compute)
        self._lock = threading.Lock()  # captions and spoken messages share it

    def _language(self, samples: np.ndarray) -> str:
        from faster_whisper.audio import pad_or_trim

        model = self._model
        features = model.feature_extractor(samples)
        # Cut exactly as the library will for its first window, so that the
        # encoder result is found again when it transcribes.
        frames = min(model.feature_extractor.nb_max_frames, features.shape[-1] - 1)
        encoded = model.encode(pad_or_trim(features[:, :frames]))
        likely = {token[2:-2]: p for token, p in model.model.detect_language(encoded)[0]}
        other = max(self.languages[1:], key=lambda code: likely.get(code, 0.0))
        return other if likely.get(other, 0.0) >= OTHER_LANGUAGE else self.languages[0]

    def transcribe(self, samples: np.ndarray, language: str = "", prompt: str = "") -> Transcript:
        """What was said. `language` given: recognised as that language only.
        `prompt`: text that comes before the audio, which steers the words."""
        with self._lock:
            return self._transcribe(samples, language, prompt)

    def _transcribe(self, samples: np.ndarray, language: str, prompt: str = "") -> Transcript:
        lang = language or (self._language(samples) if len(self.languages) > 1 else self.languages[0])
        segments, _ = self._model.transcribe(
            samples, language=lang, beam_size=1,
            temperature=0.0,  # no second tries: a caption cannot wait for them
            condition_on_previous_text=False, vad_filter=False,
            initial_prompt=prompt or LANGUAGE_PROMPT.get(language),
        )
        pieces = []
        for s in segments:  # decoding happens here, phrase by phrase
            text = s.text.strip()
            if s.avg_logprob < MADE_UP_LOGPROB or s.compression_ratio > MADE_UP_REPETITION:
                continue
            if text and not _CREDITS.search(text):
                pieces.append(Piece(text, s.start, s.end))
        return Transcript(join([p.text for p in pieces], lang), lang, pieces)


def sensevoice_unavailable(models: Path, cfg: AsrConfig) -> str:
    if not (models / "sense-voice" / "model.int8.onnx").exists():
        return f"its model is not in {models / 'sense-voice'} (get it with `valoroyce setup`)"
    return ""


from .whispercpp import WhisperCppRecognizer, unavailable as whispercpp_unavailable  # noqa: E402

RECOGNIZERS = {
    "whisper": ("Whisper", WhisperRecognizer, whisper_unavailable),
    "whispercpp": ("Whisper (Vulkan)", WhisperCppRecognizer, whispercpp_unavailable),
    "sensevoice": ("SenseVoice", Recognizer, sensevoice_unavailable),
}


def make_recognizer(models: Path, cfg: AsrConfig):
    backend = cfg.backend
    if backend == "auto":
        unknown = [name for name in cfg.order if name not in RECOGNIZERS]
        if unknown or not cfg.order:
            raise ValueError(f"asr.order must list recognisers ({', '.join(RECOGNIZERS)}), "
                             f"got {cfg.order!r}")
        # The first that can run here. The last one is tried whatever its state,
        # so that its own message says what is missing.
        backend = cfg.order[-1]
        for name in cfg.order[:-1]:
            problem = RECOGNIZERS[name][2](models, cfg)
            if not problem:
                backend = name
                break
            print(f"note: {RECOGNIZERS[name][0]} is not used because {problem}",
                  file=sys.stderr, flush=True)
    if backend not in RECOGNIZERS:
        raise ValueError(f"unknown asr.backend: {cfg.backend!r} (auto, whisper, whispercpp or sensevoice)")
    return RECOGNIZERS[backend][1](models, cfg)
