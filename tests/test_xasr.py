"""X-ASR, the Chinese recogniser for spoken messages."""

import wave
from pathlib import Path

import numpy as np
import pytest

from valoroyce import catalog, setup, xasr

MODELS = Path(__file__).resolve().parents[1] / "models"


def test_nothing_there_means_not_available(tmp_path):
    assert xasr.files(tmp_path) is None and not xasr.available(tmp_path)
    assert not catalog.XASR.installed(tmp_path)
    assert catalog.XASR.download_bytes(tmp_path) == setup.XASR_ARCHIVE.size


def test_an_unfinished_unpacking_does_not_count(tmp_path):
    folder = tmp_path / setup.XASR_DIR / setup.XASR_NAME
    folder.mkdir(parents=True)
    for name in ("encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
                 "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt"):
        (folder / name).write_bytes(b"x")
    assert not xasr.available(tmp_path)  # no marker: unpacking did not finish
    (tmp_path / setup.XASR_DIR / f".{setup.XASR_NAME}").touch()
    assert xasr.available(tmp_path) and catalog.XASR.installed(tmp_path)
    catalog.XASR.remove(tmp_path)
    assert not (tmp_path / setup.XASR_DIR).exists()


def test_the_lazy_one_loads_once(monkeypatch):
    made = []

    class Fake:
        def __init__(self, models):
            made.append(models)

        def transcribe(self, samples, language="", prompt=""):
            return "heard"

    monkeypatch.setattr(xasr, "XAsrRecognizer", Fake)
    lazy = xasr.Lazy(Path("m"))
    assert not lazy.loaded
    lazy.preload()
    assert lazy.transcribe(np.zeros(10)) == "heard" and lazy.transcribe(np.zeros(10)) == "heard"
    assert made == [Path("m")] and lazy.loaded


@pytest.mark.skipif(not xasr.available(MODELS), reason="X-ASR is not downloaded")
def test_the_real_model_hears_chinese():
    wav = sorted((MODELS / setup.XASR_DIR).rglob("test_wavs/0.wav"))[0]
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16000
        samples = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    heard = xasr.XAsrRecognizer(MODELS).transcribe(samples, language="zh")
    assert heard.lang == "zh" and any("一" <= c <= "鿿" for c in heard.text)
