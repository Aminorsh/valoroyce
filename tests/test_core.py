import numpy as np
import pytest

from valoroyce.audio import Resampler
from valoroyce.config import Config, TranslateConfig, load_config
from valoroyce.glossary import Glossary
from valoroyce.pipeline import _Job, _JobBox
from valoroyce.translate import build_messages, clean_output


def tone(freq, rate, seconds=1.0):
    return np.sin(2 * np.pi * freq * np.arange(int(rate * seconds)) / rate).astype(np.float32)


@pytest.mark.parametrize("rate", [48000, 44100, 32000, 16000, 8000])
def test_resampler_length_and_pitch(rate):
    r = Resampler(rate)
    blocks = np.array_split(tone(440, rate, 2.0), 37)  # uneven block sizes
    out = np.concatenate([r.process(b) for b in blocks])
    assert abs(len(out) - 32000) <= 2
    body = out[4000:28000]
    crossings = np.sum((body[:-1] < 0) & (body[1:] >= 0))
    assert abs(crossings - 440 * 1.5) <= 2
    assert 0.9 < np.abs(body).max() < 1.1


def test_resampler_removes_what_16k_cannot_carry():
    r = Resampler(48000)
    out = r.process(tone(12000, 48000))
    assert np.abs(out[200:]).max() < 0.05


class FakeDevice:
    """Stands in for a sound device: block k is filled with the value k."""

    name = "fake"

    def __init__(self, blocks, fail=None):
        self.blocks, self.fail, self.given, self.source = blocks, fail, 0, None

    def recorder(self, samplerate, **options):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        if self.given == self.blocks:
            if self.fail:
                raise self.fail
            self.source.stop()
        self.given += 1
        return np.full((numframes, 2), float(self.given - 1), dtype=np.float32)


def fake_source(monkeypatch, device):
    import types

    from valoroyce import audio

    sc = types.SimpleNamespace(default_speaker=lambda: types.SimpleNamespace(id="x"),
                               get_microphone=lambda id, include_loopback: device)
    monkeypatch.setattr(audio, "_soundcard", lambda: sc)
    device.source = audio.DeviceSource()
    return device.source


def test_capture_keeps_every_block_while_the_reader_is_busy(monkeypatch):
    import time

    source = fake_source(monkeypatch, FakeDevice(blocks=30))
    heard, lag = [], 0.0
    for block in source:
        if not heard:
            time.sleep(0.2)  # a slow recognition pass: the device keeps delivering
            lag = source.lag()
        heard.append(float(np.median(block)))
    assert lag >= 1.0  # ten or more 0.1 s blocks were waiting
    assert heard == pytest.approx([float(k) for k in range(31)], abs=0.01)
    assert source.lag() == 0.0


def test_silence_made_up_while_audio_was_waiting_is_dropped():
    import types

    from valoroyce.audio import _no_false_silence

    def recorder(waiting, chunks):
        waiting, chunks = iter(waiting), iter(chunks)
        return types.SimpleNamespace(_capture_available_frames=lambda: next(waiting),
                                     _record_chunk=lambda: next(chunks))

    zeros, sound = np.zeros(1920, dtype=np.float32), np.ones(480, dtype=np.float32)

    late = recorder([0, 480], [zeros, sound])  # nothing, then audio after all
    _no_false_silence(late)
    assert late._record_chunk() is sound

    silent = recorder([0, 0], [zeros])  # the device really is silent
    _no_false_silence(silent)
    assert silent._record_chunk() is zeros

    busy = recorder([480, 480], [zeros])  # a silent packet from the device itself
    _no_false_silence(busy)
    assert busy._record_chunk() is zeros

    other = types.SimpleNamespace()  # a backend without these internals
    _no_false_silence(other)
    assert not hasattr(other, "_record_chunk")


def test_capture_failure_reaches_the_reader(monkeypatch):
    source = fake_source(monkeypatch, FakeDevice(blocks=3, fail=RuntimeError("device unplugged")))
    with pytest.raises(RuntimeError, match="device unplugged"):
        list(source)


def test_config_defaults_and_overrides(tmp_path):
    assert load_config(tmp_path / "nope.toml" if False else None).asr.languages == ["ja", "en"]
    f = tmp_path / "c.toml"
    f.write_text('[asr]\nlanguages = ["ja"]\n[vad]\nmin_silence = 1\n[overlay]\nfont_size = 30\n')
    cfg = load_config(f)
    assert cfg.asr.languages == ["ja"] and cfg.vad.min_silence == 1.0 and cfg.overlay.font_size == 30
    assert cfg.translate == Config().translate


@pytest.mark.parametrize("body", ['[asr]\nlangauges = ["ja"]\n', "[overlay]\nfont_size = 'big'\n", "asr = 3\n"])
def test_config_rejects_typos(tmp_path, body):
    f = tmp_path / "c.toml"
    f.write_text(body)
    with pytest.raises(ValueError):
        load_config(f)


def job(utt, final, text="x"):
    return _Job(utt, text, "ja", [], final, 0.0)


def test_jobbox_finals_first_and_only_newest_partial():
    box = _JobBox()
    box.put(job(1, False, "a"))
    box.put(job(1, False, "ab"))
    assert box.get().source == "ab"
    box.put(job(2, False, "p2"))
    box.put(job(2, True, "f2"))  # supersedes its own partial
    box.put(job(3, False, "p3"))
    assert [box.get().source for _ in range(2)] == ["f2", "p3"]
    box.close()
    assert box.get() is None


def test_jobbox_drops_stale_finals():
    box = _JobBox()
    for i in range(1, 7):
        box.put(job(i, True, f"f{i}"))
    assert [box.get().source for _ in range(3)] == ["f4", "f5", "f6"]


def test_hy_mt_prompt_follows_the_model_card():
    g = Glossary.load()
    text = "アイソがロー"
    msgs = build_messages(text, [(h.source, h.zh) for h in g.find(text)], TranslateConfig())
    assert msgs == [{"role": "user", "content":
        "参考下面的翻译：\nアイソ 翻译成 壹决\nロー 翻译成 残血\n\n"
        "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\nアイソがロー"}]
    plain = build_messages("こんにちは", [], TranslateConfig())
    assert plain[0]["content"].startswith("将以下文本翻译为中文")


def test_generic_prompt_carries_glossary():
    g = Glossary.load()
    msgs = build_messages("Jett low", [(h.source, h.zh) for h in g.find("Jett low")],
                          TranslateConfig(prompt="generic"))
    assert msgs[0]["role"] == "system" and "Jett = 捷风" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "Jett low"}


def test_clean_output():
    assert clean_output(" “捷风残血” \n") == "捷风残血"
    assert clean_output("捷风残血\n\n（注：Jett 是角色名）") == "捷风残血"


def test_example_config_documents_the_real_defaults():
    from pathlib import Path

    example = Path(__file__).parent.parent / "config.example.toml"
    assert load_config(example) == Config()


def test_tray_choices_override_config(tmp_path, monkeypatch):
    from valoroyce import state

    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "s.json")
    cfg = Config()
    state.apply(cfg)
    assert cfg == Config()  # no file: nothing changes
    state.save(gpu=False, font_size=28)
    state.save(device="CABLE", x=10, bottom=900)
    state.apply(cfg)
    assert (cfg.translate.gpu, cfg.overlay.font_size, cfg.audio.device) == (False, 28, "CABLE")
    (tmp_path / "s.json").write_text("not json")
    state.apply(cfg2 := Config())
    assert cfg2 == Config()


def test_gpu_switch_controls_llama_layers(tmp_path, monkeypatch):
    import valoroyce.translate as tr

    (tmp_path / "llama").mkdir()
    exe = tmp_path / "llama" / ("llama-server.exe" if tr.os.name == "nt" else "llama-server")
    exe.write_text("")
    (tmp_path / "m.gguf").write_text("")
    seen = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen.append(cmd)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(tr.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(tr.LlamaTranslator, "_wait_ready", lambda self: None)
    monkeypatch.chdir(tmp_path)
    g = Glossary.load()
    from pathlib import Path

    for gpu, layers in ((True, "99"), (False, "0")):
        t = tr.LlamaTranslator(TranslateConfig(gpu=gpu), g, Path("."))  # relative models dir
        t.close()
        cmd = seen[-1]
        assert cmd[cmd.index("-ngl") + 1] == layers
        assert Path(cmd[0]).is_absolute() and Path(cmd[cmd.index("-m") + 1]).is_absolute()


def test_hotkey_parsing():
    from valoroyce.hotkey import parse

    assert parse("ctrl+alt+t") == (0x2 | 0x1, ord("T"))
    assert parse("Ctrl + Shift + F8") == (0x2 | 0x4, 0x77)
    for bad in ("t", "ctrl+alt", "ctrl+nope", ""):
        with pytest.raises(ValueError):
            parse(bad)
    parse(Config().compose.hotkey)  # the default must be valid


def test_outgoing_prompt_names_the_target_language():
    from valoroyce.translate import OUTGOING

    g = Glossary.load()
    msgs = build_messages(*g.outgoing("壹决残血", "ja"), TranslateConfig(), target=OUTGOING["ja"])
    assert msgs[0]["content"] == (
        "参考下面的翻译：\n壹决 翻译成 アイソ\n残血 翻译成 ロー\n\n"
        "将以下文本翻译为日语，注意只需要输出翻译后的结果，不要额外解释：\n壹决残血")
    generic = build_messages("你好", [], TranslateConfig(prompt="generic"), target=OUTGOING["en"])
    assert "英语" in generic[0]["content"] and "发文字消息" in generic[0]["content"]


def test_glossary_only_backend_can_compose():
    from valoroyce.translate import GlossaryTranslator

    assert GlossaryTranslator(Glossary.load()).compose("壹决在B长廊", "en") == "Iso在B long"


def test_model_file_selection(tmp_path):
    from valoroyce.translate import CAPTION_MODEL, find_gguf, make_compose_translator

    with pytest.raises(FileNotFoundError):
        find_gguf(tmp_path)
    (tmp_path / "zzz.gguf").write_text("")
    assert find_gguf(tmp_path).name == "zzz.gguf"          # the only one there
    (tmp_path / "Aaa-big.gguf").write_text("")
    (tmp_path / CAPTION_MODEL).write_text("")
    assert find_gguf(tmp_path).name == CAPTION_MODEL       # not "whichever sorts first"
    assert find_gguf(tmp_path, "Aaa-big.gguf").name == "Aaa-big.gguf"
    with pytest.raises(FileNotFoundError):
        find_gguf(tmp_path, "missing.gguf")
    # No second model configured: callers fall back to the caption translator.
    assert make_compose_translator(Config(), Glossary.load(), tmp_path) is None


def test_messages_can_use_their_own_backend(tmp_path, monkeypatch):
    from valoroyce.translate import ChatTranslator, make_compose_translator

    g = Glossary.load()
    cfg = Config()
    cfg.compose.backend = "openai"
    cfg.compose.base_url = "https://api.example.com"
    cfg.compose.model = "some-chat-model"
    monkeypatch.setenv("VALOROYCE_COMPOSE_API_KEY", "from-env")
    t = make_compose_translator(cfg, g, tmp_path)
    assert type(t) is ChatTranslator  # nothing is started locally
    assert t.url == "https://api.example.com/v1/chat/completions"
    assert (t.cfg.api_key, t.cfg.model, t.cfg.prompt) == ("from-env", "some-chat-model", "generic")
    # The caption settings are untouched: captions stay on the local model.
    assert (cfg.translate.backend, cfg.translate.api_key) == ("llama", "")

    cfg.compose.api_key = "from-file"  # the config file wins over the environment
    assert make_compose_translator(cfg, g, tmp_path).cfg.api_key == "from-file"

    cfg.compose.backend = "llama"  # a second local model needs a file and its own port
    with pytest.raises(ValueError, match="model_file"):
        make_compose_translator(cfg, g, tmp_path)
    cfg.compose.model_file, cfg.compose.base_url = "big.gguf", cfg.translate.base_url
    with pytest.raises(ValueError, match="port"):
        make_compose_translator(cfg, g, tmp_path)
    cfg.compose.backend = "cloud"
    with pytest.raises(ValueError, match="compose.backend"):
        make_compose_translator(cfg, g, tmp_path)


def test_caption_model_takes_over_messages_the_other_model_fails_on(tmp_path):
    from valoroyce.translate import (DECLINED, ComposeWithFallback, Declined, GlossaryTranslator,
                                     build_messages, make_compose_translator)

    g = Glossary.load()
    cfg = Config()
    cfg.compose.backend, cfg.compose.base_url = "openai", "https://api.example.com"
    local = GlossaryTranslator(g)
    t = make_compose_translator(cfg, g, tmp_path, fallback=lambda: local)
    assert isinstance(t, ComposeWithFallback) and t.own.may_decline

    # The hosted model is told how to decline, and only when something can take over.
    asked = build_messages("你好", [], t.own.cfg, target="日语", may_decline=True)
    assert DECLINED in asked[0]["content"]
    assert DECLINED not in build_messages("你好", [], t.own.cfg, target="日语")[0]["content"]

    replies = iter(["Iso is B long", DECLINED, DECLINED + "\n(I cannot translate this)", ""])
    t.own._chat = lambda messages: next(replies).split("\n")[0]
    assert t.compose("壹决在B长廊", "en") == "Iso is B long" and not t.fell_back
    for _ in range(3):  # declined twice, then an empty reply: the caption translator answers
        assert t.compose("壹决在B长廊", "en") == "Iso在B long" and t.fell_back

    def unreachable(messages):
        raise RuntimeError("cannot reach api.example.com")
    t.own._chat = unreachable
    assert t.compose("壹决在B长廊", "en") == "Iso在B long" and "cannot reach" in t.fell_back

    # A sentence with a local_only word is never sent, whatever the other model would say.
    def must_not_be_called(messages):
        raise AssertionError("sent to the message model")
    cfg.compose.local_only = ["B长廊", "Secret"]
    kept = make_compose_translator(cfg, g, tmp_path, fallback=lambda: local)
    kept.own._chat = must_not_be_called
    assert kept.compose("壹决在B长廊", "en") == "Iso在B long" and "local_only" in kept.fell_back
    assert kept.compose("a SECRET plan", "en") == "a SECRET plan"
    not_ready = make_compose_translator(cfg, g, tmp_path, fallback=lambda: None)
    not_ready.own._chat = must_not_be_called
    with pytest.raises(RuntimeError, match="not ready"):
        not_ready.compose("壹决在B长廊", "en")

    # Nothing to fall back to (captions still loading): the failure is reported.
    alone = make_compose_translator(cfg, g, tmp_path, fallback=lambda: None)
    alone.own._chat = unreachable
    with pytest.raises(RuntimeError, match="cannot reach"):
        alone.compose("你好", "ja")
    alone.own._chat = lambda messages: ""
    with pytest.raises(Declined):
        alone.compose("你好", "ja")


def test_whisper_without_its_model_says_what_to_do(tmp_path, capsys):
    from valoroyce import speech

    cfg = Config().asr
    assert "valoroyce setup --whisper" in speech.whisper_unavailable(tmp_path, cfg)
    cfg.backend = "whisper"
    with pytest.raises(RuntimeError, match=r"setup --whisper.*sensevoice"):
        speech.make_recognizer(tmp_path, cfg)
    cfg.backend = "cloud"
    with pytest.raises(ValueError, match="asr.backend"):
        speech.make_recognizer(tmp_path, cfg)
    # "auto" goes on with the CPU recogniser and says why; here that one is missing too.
    cfg.backend = "auto"
    with pytest.raises(FileNotFoundError, match="valoroyce setup"):
        speech.make_recognizer(tmp_path, cfg)
    assert "Whisper is not used because" in capsys.readouterr().err


def test_setup_puts_whisper_where_the_recogniser_looks():
    from valoroyce.setup import WHISPER_ASSETS, WHISPER_DIR

    assert WHISPER_DIR == Config().asr.whisper_model
    names = {a.dest for a in WHISPER_ASSETS}
    assert f"{WHISPER_DIR}/model.bin" in names and len(names) == 5
    assert all(a.sha256 and a.size and a.urls["global"] and a.urls["cn"] for a in WHISPER_ASSETS)


def test_subtitle_credits_are_recognised_as_made_up():
    from valoroyce.speech import _CREDITS

    for text in ("ご視聴ありがとうございました", "チャンネル登録お願いします", "次回予告",
                 "Thanks for watching!", "Thank you for watching."):
        assert _CREDITS.search(text), text
    for text in ("ありがとう", "ナイス", "thank you", "watch mid"):
        assert not _CREDITS.search(text), text

def test_an_utterance_comes_with_the_audio_just_before_it():
    from valoroyce.config import SAMPLE_RATE as SR
    from valoroyce.speech import LEAD_SECONDS, Lead

    lead, n = Lead(keep_seconds=10), int(LEAD_SECONDS * SR)
    stream = np.arange(6 * SR, dtype=np.float32)
    lead.add(stream[:3 * SR])
    # In progress and finished, an utterance starts at the same place.
    early = lead.segment(SR, SR, finished=False)
    first = lead.segment(SR, 2 * SR, finished=True)
    assert early.start == first.start == SR - n and first.lead == n
    assert np.array_equal(first.samples, stream[SR - n:3 * SR])
    # The next one starts so soon that the full lead would repeat the first.
    lead.add(stream[3 * SR:])
    second = lead.segment(3 * SR + n // 2, SR, finished=True)
    assert second.start == 3 * SR and second.lead == n // 2
    assert np.array_equal(second.samples, stream[3 * SR:4 * SR + n // 2])
    # Speech at the very start of the stream has nothing before it.
    assert Lead(keep_seconds=10).segment(0, 0, finished=False).lead == 0


def test_the_kept_stream_stays_small_and_positions_stay_right():
    from valoroyce.config import SAMPLE_RATE as SR
    from valoroyce.speech import LEAD_SECONDS, Lead

    lead, n = Lead(keep_seconds=2), int(LEAD_SECONDS * SR)
    stream = np.arange(60 * SR, dtype=np.float32)
    for i in range(0, len(stream), SR // 10):
        lead.add(stream[i:i + SR // 10])
        assert len(lead._audio) <= 4 * SR
    seg = lead.segment(59 * SR, SR // 2, finished=True)
    assert np.array_equal(seg.samples, stream[59 * SR - n:59 * SR + SR // 2])

def test_full_speed_is_asked_for_on_windows_and_harmless_elsewhere():
    import sys

    from valoroyce.fullspeed import full_speed

    assert full_speed() is (sys.platform == "win32")

def test_the_caption_strip_never_holds_more_rows_than_allowed():
    from valoroyce.rows import ELLIPSIS, arrange, newest, wrap

    one = len  # every character is one unit wide
    assert wrap("一二三四五六七", one, 3) == ["一二三", "四五六", "七"]
    assert wrap("jett is low", one, 5) == ["jett", "is lo", "w"]
    assert wrap("", one, 3) == [] and wrap("短", one, 3) == ["短"]
    # Too long for the strip: the end, after an ellipsis, in exactly the rows there are.
    assert newest("一二三四五六七八九十", one, 3, 2) == [ELLIPSIS + "六七", "八九十"]
    assert newest("一二三四", one, 3, 2) == ["一二三", "四"]
    # Newest last. Older captions stay while they fit whole.
    assert arrange(["甲", "乙", "丙"], one, 3, 3) == [["甲"], ["乙"], ["丙"]]
    assert arrange(["甲", "乙乙乙乙", "丙"], one, 3, 3) == [[], ["乙乙乙", "乙"], ["丙"]]
    # One that does not fit whole leaves, and so does everything older.
    assert arrange(["甲", "乙乙乙乙乙乙乙", "丙"], one, 3, 3) == [[], [], ["丙"]]
    assert arrange(["甲", "乙乙乙乙乙乙乙乙乙乙"], one, 3, 3) == [[], [ELLIPSIS + "乙乙", "乙乙乙", "乙乙乙"]]
    for texts in (["甲" * 40, "乙" * 7, "丙" * 11], ["甲"] * 6, []):
        assert sum(len(rows) for rows in arrange(texts, one, 5, 3)) <= 3


def test_wallet_is_chamber_in_typed_messages():
    text, pairs = Glossary.load().outgoing("钱包在A点", "ja")
    assert ("尚勃勒", "チェンバー") in pairs and "钱包" not in text
