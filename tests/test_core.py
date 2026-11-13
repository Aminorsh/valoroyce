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
    msgs = build_messages(text, g.find(text), TranslateConfig())
    assert msgs == [{"role": "user", "content":
        "参考下面的翻译：\nアイソ 翻译成 壹决\nロー 翻译成 残血\n\n"
        "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\nアイソがロー"}]
    plain = build_messages("こんにちは", [], TranslateConfig())
    assert plain[0]["content"].startswith("将以下文本翻译为中文")


def test_generic_prompt_carries_glossary():
    g = Glossary.load()
    msgs = build_messages("Jett low", g.find("Jett low"), TranslateConfig(prompt="generic"))
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
