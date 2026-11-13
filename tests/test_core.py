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

    # Nothing to fall back to (captions still loading): the failure is reported.
    alone = make_compose_translator(cfg, g, tmp_path, fallback=lambda: None)
    alone.own._chat = unreachable
    with pytest.raises(RuntimeError, match="cannot reach"):
        alone.compose("你好", "ja")
    alone.own._chat = lambda messages: ""
    with pytest.raises(Declined):
        alone.compose("你好", "ja")
