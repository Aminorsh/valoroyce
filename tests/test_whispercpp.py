"""Whisper through whisper.cpp (Vulkan): graphics cards of any maker."""

import numpy as np
import pytest

from valoroyce import catalog, whispercpp
from valoroyce.audio import read_wav
from valoroyce.config import AsrConfig, Config

SR = 16000


class FakeServer:
    """Answers like whisper-server's verbose_json."""

    def __init__(self, replies):
        self.replies, self.asked = replies, []

    def infer(self, samples, **fields):
        self.asked.append(fields)
        return self.replies[fields["language"]]

    def close(self):
        pass


def recognizer(replies, languages=("ja", "en")):
    r = whispercpp.WhisperCppRecognizer.__new__(whispercpp.WhisperCppRecognizer)
    r.languages, r.server = list(languages), FakeServer(replies)
    return r


def segment(text, start=0.0, end=1.0, logprob=-0.3):
    return {"text": text, "start": start, "end": end, "avg_logprob": logprob}


def test_the_first_language_wins_unless_another_is_near_certain():
    ja = {"segments": [segment(" ナイスカバー")], "language_probabilities": {"ja": 0.1, "en": 0.84}}
    r = recognizer({"ja": ja})
    out = r.transcribe(np.zeros(SR, dtype=np.float32))
    assert (out.lang, out.text) == ("ja", "ナイスカバー")
    assert len(r.server.asked) == 1 and r.server.asked[0]["no_language_probabilities"] is False

    sure = {"segments": [segment("nice")], "language_probabilities": {"ja": 0.01, "en": 0.97}}
    en = {"segments": [segment(" Nice cover!")]}
    r = recognizer({"ja": sure, "en": en})
    out = r.transcribe(np.zeros(SR, dtype=np.float32))
    assert (out.lang, out.text) == ("en", "Nice cover!")
    assert [a["language"] for a in r.server.asked] == ["ja", "en"]


def test_one_language_asks_for_no_probabilities():
    r = recognizer({"ja": {"segments": [segment("はい")]}}, languages=["ja"])
    assert r.transcribe(np.zeros(SR, dtype=np.float32)).text == "はい"
    assert r.server.asked[0]["no_language_probabilities"] is True


def test_made_up_phrases_are_dropped():
    reply = {"segments": [
        segment("前のTPのところで", 0.0, 1.5),
        segment("ご視聴ありがとうございました", 1.5, 3.0),           # credits nobody says in a match
        segment("はいはいはいはいはいはいはいはいはいはいはい", 3.0, 4.0),  # repetition
        segment("うーん", 4.0, 5.0, logprob=-1.4),                   # unsure
        segment("全員死んだ", 5.0, 6.0),
    ]}
    out = recognizer({"ja": reply}, languages=["ja"]).transcribe(np.zeros(SR, dtype=np.float32))
    assert [p.text for p in out.pieces] == ["前のTPのところで", "全員死んだ"]
    assert (out.pieces[1].start, out.pieces[1].end) == (5.0, 6.0)


def test_amd_and_intel_cards_get_whisper_through_vulkan(monkeypatch):
    monkeypatch.setattr(catalog, "nvidia_gpu", lambda: None)
    monkeypatch.setattr(catalog, "_windows", lambda: True)
    monkeypatch.setattr(catalog, "graphics_cards",
                        lambda: (catalog.Card("Intel UHD", "intel", 128),
                                 catalog.Card("AMD Radeon RX 6750 XT", "amd", 12272)))
    assert catalog.WHISPER_VULKAN in catalog.recommended()
    assert "6750" in catalog.advice()
    monkeypatch.setattr(catalog, "graphics_cards", lambda: (catalog.Card("Intel UHD", "intel", 128),))
    assert catalog.WHISPER_VULKAN.blocked()
    assert catalog.SENSEVOICE in catalog.recommended()


def test_nvidia_cards_get_it_first_too(monkeypatch):
    assert AsrConfig().order == ["whispercpp", "whisper", "sensevoice"]
    monkeypatch.setattr(catalog, "nvidia_gpu", lambda: catalog.Gpu("GeForce RTX 3050", 4096))
    monkeypatch.setattr(catalog, "_windows", lambda: True)
    monkeypatch.setattr(catalog, "graphics_cards", lambda: (catalog.Card("GeForce RTX 3050", "nvidia", 4096),))
    assert catalog.recommended()[1] is catalog.WHISPER_VULKAN
    assert "6 GB" in catalog.advice()  # tight beside the game: says what to do
    # not Windows, or too small a card: faster-whisper is still offered to NVIDIA
    monkeypatch.setattr(catalog, "graphics_cards", lambda: ())
    assert catalog.recommended()[1] is catalog.WHISPER


_cfg = Config()
_missing = whispercpp.unavailable(_cfg.models_path, _cfg.asr)


@pytest.mark.skipif(bool(_missing), reason=f"whisper.cpp cannot run here: {_missing}")
def test_real_server_recognises_japanese_and_english():
    r = whispercpp.WhisperCppRecognizer(_cfg.models_path, _cfg.asr)
    try:
        ja, _ = read_wav(str(_cfg.models_path / "sense-voice" / "test_wavs" / "ja.wav"))
        en, _ = read_wav(str(_cfg.models_path / "sense-voice" / "test_wavs" / "en.wav"))
        out = r.transcribe(ja)
        assert out.lang == "ja" and "弁当" in out.text
        out = r.transcribe(en)
        assert out.lang == "en" and "chieftain" in out.text.lower()
    finally:
        r.close()
    assert r.server is None


def test_every_local_server_has_a_port_of_its_own():
    from urllib.parse import urlparse

    from valoroyce.settings import CUSTOM_PORT

    cfg = Config()
    ports = [urlparse(cfg.translate.base_url).port, urlparse(cfg.compose.base_url).port,
             urlparse(CUSTOM_PORT).port, whispercpp.PORT]
    assert len(set(ports)) == len(ports), ports


def test_a_character_cut_in_half_is_dropped():
    reply = {"segments": [segment("别买枪�")]}
    assert recognizer({"zh": reply}).transcribe(np.zeros(SR, dtype=np.float32), language="zh").text == "别买枪"
