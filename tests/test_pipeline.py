"""End to end on real models. Skipped until `valoroyce setup` has been run."""
import threading
from pathlib import Path

import pytest

from valoroyce.audio import FileSource
from valoroyce.config import Config
from valoroyce.glossary import Glossary
from valoroyce.pipeline import Pipeline
from valoroyce.translate import GlossaryTranslator

MODELS = Path(__file__).parent.parent / "models"
WAVS = MODELS / "sense-voice" / "test_wavs"
pytestmark = pytest.mark.skipif(
    not (MODELS / "sense-voice" / "model.int8.onnx").exists() or not WAVS.exists(),
    reason="models or sample audio not downloaded",
)


def config(backend="sensevoice", **asr):
    # The recogniser is named, not left to "auto": the same tests must mean the
    # same thing on a machine with a graphics card and on one without.
    cfg = Config(models_dir=str(MODELS))
    cfg.asr.backend = backend
    for k, v in asr.items():
        setattr(cfg.asr, k, v)
    return cfg


def _no_whisper():
    from valoroyce.speech import whisper_unavailable

    return whisper_unavailable(MODELS, Config().asr)


BACKENDS = ["sensevoice",
            pytest.param("whisper", marks=pytest.mark.skipif(bool(_no_whisper()),
                                                             reason=f"Whisper: {_no_whisper()}"))]


def run(wav, **asr):
    cfg = config(**asr)
    glossary = Glossary.load()
    captions, lock = [], threading.Lock()

    def on_caption(c):
        with lock:
            captions.append(c)

    p = Pipeline(cfg, FileSource(str(WAVS / wav), realtime=False), GlossaryTranslator(glossary),
                 glossary, on_caption)
    p.start()
    assert p.done.wait(60)
    assert p.error is None
    return captions


@pytest.mark.parametrize("backend", BACKENDS)
def test_japanese_file_yields_final_caption(backend):
    finals = [c for c in run("ja.wav", backend=backend) if c.final]
    assert finals and all(c.lang == "ja" for c in finals)
    assert "弁当" in "".join(c.source for c in finals)


@pytest.mark.parametrize("backend", BACKENDS)
def test_english_file_is_detected_as_english(backend):
    finals = [c for c in run("en.wav", backend=backend) if c.final]
    assert finals and all(c.lang == "en" for c in finals)
    assert "tribal" in " ".join(c.source for c in finals).lower()


@pytest.mark.parametrize("backend", BACKENDS)
def test_unexpected_language_is_decoded_as_the_default(backend):
    # Korean audio with ja/en expected: must come back labelled ja, not ko.
    finals = [c for c in run("ko.wav", backend=backend) if c.final]
    assert all(c.lang == "ja" for c in finals)


def test_recording_holds_the_audio_heard_and_the_captions_shown(tmp_path):
    from valoroyce.audio import read_wav
    from valoroyce.record import Recording, describe

    cfg = config()
    glossary = Glossary.load()
    shown = []
    p = Pipeline(cfg, FileSource(str(WAVS / "ja.wav"), realtime=False), GlossaryTranslator(glossary),
                 glossary, shown.append)
    rec = p.recording = Recording(tmp_path, describe(cfg, "ja.wav"))
    p.start()
    assert p.done.wait(60) and p.error is None
    rec.close()

    original, _ = read_wav(str(WAVS / "ja.wav"))
    saved, rate = read_wav(str(rec.wav_path))
    assert rate == 16000 and len(saved) >= len(original)   # plus the trailing silence
    assert abs(saved[:len(original)] - original).max() < 1e-3
    lines = [l.split("\t") for l in rec.log_path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    assert len(lines) == len(shown) and lines[-1][1] == "final" and "弁当" in lines[-1][5]
    assert any("min_silence" in l for l in rec.log_path.read_text(encoding="utf-8").splitlines()[:6])


def test_recording_stops_by_itself(tmp_path, monkeypatch):
    import numpy as np

    from valoroyce import record

    monkeypatch.setattr(record, "MAX_SECONDS", 0.5)
    rec = record.Recording(tmp_path)
    for _ in range(10):
        rec.audio(np.zeros(1600, dtype=np.float32))
    assert rec.closed and 0.5 <= rec.seconds < 0.7
    rec.audio(np.zeros(1600, dtype=np.float32))  # ignored, no error
    rec.close()


def test_partials_precede_the_final_and_share_its_number():
    cfg_caps = run("ja.wav", partial_interval=0.3)
    by_utt = {}
    for c in cfg_caps:
        by_utt.setdefault(c.utt, []).append(c.final)
    for flags in by_utt.values():
        assert flags[-1] is True and flags.count(True) == 1


def test_slow_recognition_does_not_fall_behind_the_audio():
    import time

    cfg = config()
    glossary = Glossary.load()
    captions = []
    source = FileSource(str(WAVS / "ja.wav"), realtime=True)
    p = Pipeline(cfg, source, GlossaryTranslator(glossary), glossary, captions.append)
    passes, real = [], p.recognizer.transcribe

    def slow(samples):
        time.sleep(1.0)  # twice the partial interval, on top of the real work
        passes.append(len(samples))
        return real(samples)

    p.recognizer.transcribe = slow
    audio_seconds = len(source.samples) / 16000
    t0 = time.monotonic()
    p.start()
    assert p.done.wait(60) and p.error is None
    spent = time.monotonic() - t0
    # A pass per partial interval would take twice as long as the audio lasts.
    # Passes are only made when the reading is live, so the delay stays at
    # about one pass.
    assert len(passes) <= audio_seconds / 1.0 + 2
    assert spent < audio_seconds + 7.0
    finals = [c for c in captions if c.final]
    assert finals and "弁当" in finals[-1].source


def test_empty_translation_falls_back_to_what_was_heard():
    class Silent:
        def translate(self, text, lang, hits):
            return ""

    cfg = config()
    glossary = Glossary.load()
    captions = []
    p = Pipeline(cfg, FileSource(str(WAVS / "ja.wav"), realtime=False), Silent(), glossary,
                 captions.append)
    p.start()
    assert p.done.wait(60) and p.error is None
    finals = [c for c in captions if c.final]
    assert finals and all(c.text == glossary.substitute(c.source) for c in finals)
