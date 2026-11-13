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


def run(wav, **asr):
    cfg = Config(models_dir=str(MODELS))
    for k, v in asr.items():
        setattr(cfg.asr, k, v)
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


def test_japanese_file_yields_final_caption():
    finals = [c for c in run("ja.wav") if c.final]
    assert finals and all(c.lang == "ja" for c in finals)
    assert "弁当" in "".join(c.source for c in finals)


def test_english_file_is_detected_as_english():
    finals = [c for c in run("en.wav") if c.final]
    assert finals and all(c.lang == "en" for c in finals)
    assert "tribal" in " ".join(c.source for c in finals).lower()


def test_unexpected_language_is_decoded_as_the_default():
    # Korean audio with ja/en expected: must come back labelled ja, not ko.
    finals = [c for c in run("ko.wav") if c.final]
    assert all(c.lang == "ja" for c in finals)


def test_partials_precede_the_final_and_share_its_number():
    cfg_caps = run("ja.wav", partial_interval=0.3)
    by_utt = {}
    for c in cfg_caps:
        by_utt.setdefault(c.utt, []).append(c.final)
    for flags in by_utt.values():
        assert flags[-1] is True and flags.count(True) == 1
