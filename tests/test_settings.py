"""Settings that are remembered, the catalogue of models, and the window for both."""

import http.server
import itertools
import os
import subprocess
import sys
import threading
import types
import urllib.request
from pathlib import Path

import pytest

from valoroyce import catalog, setup, speech, state
from valoroyce.config import Config


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")
    return tmp_path / "state.json"


# -- remembered settings ---------------------------------------------------------

def test_settings_are_remembered_by_their_config_names(saved):
    state.put("asr.order", ["sensevoice"])
    state.put("overlay.linger", 4)        # a whole number where the config has a fraction
    state.put("compose.api_key", "k")
    state.save(x=10, bottom=900)
    state.apply(cfg := Config())
    assert cfg.asr.order == ["sensevoice"] and cfg.overlay.linger == 4.0
    assert cfg.compose.api_key == "k"
    assert state.load()["x"] == 10


def test_a_file_from_an_older_version_still_applies(saved):
    saved.write_text('{"font_size": 28, "gpu": false, "compose_target": "en", "x": 5}')
    state.apply(cfg := Config())
    assert (cfg.overlay.font_size, cfg.translate.gpu, cfg.compose.target) == (28, False, "en")
    state.put("overlay.font_size", 30)  # rewritten under the new name, once
    data = state.load()
    assert data["settings"]["overlay.font_size"] == 30 and "font_size" not in data and data["x"] == 5


def test_values_that_do_not_fit_are_ignored(saved):
    saved.write_text('{"settings": {"overlay.font_size": "big", "asr.nothing": 1, "nowhere.x": 1,'
                     ' "translate.gpu": 1, "compose.target": "xx", "overlay.max_lines": 2}}')
    state.apply(cfg := Config())
    expected = Config()
    expected.overlay.max_lines = 2
    assert cfg == expected


def test_forgetting(saved):
    state.put("overlay.font_size", 30)
    state.save(x=1, bottom=2)
    state.forget("x", "bottom")
    assert "x" not in state.load() and state.load()["settings"]
    state.forget()
    assert state.load()["settings"] == {}


# -- the order recognisers are tried in --------------------------------------------

def test_the_first_recogniser_that_can_run_is_used(tmp_path, monkeypatch, capsys):
    class A:
        def __init__(self, models, cfg): pass

    class B(A):
        pass

    monkeypatch.setattr(speech, "RECOGNIZERS", {
        "a": ("A", A, lambda models, cfg: "it is broken"),
        "b": ("B", B, lambda models, cfg: ""),
    })
    cfg = Config().asr
    cfg.order = ["a", "b"]
    assert type(speech.make_recognizer(tmp_path, cfg)) is B
    assert "A is not used because it is broken" in capsys.readouterr().err
    cfg.order = ["b", "a"]
    assert type(speech.make_recognizer(tmp_path, cfg)) is B
    cfg.order = ["a"]  # the only one allowed: tried, so that its own error is shown
    assert type(speech.make_recognizer(tmp_path, cfg)) is A
    for bad in ([], ["a", "nope"]):
        cfg.order = bad
        with pytest.raises(ValueError, match="asr.order"):
            speech.make_recognizer(tmp_path, cfg)
    cfg.backend = "b"  # a fixed choice does not look at the order
    assert type(speech.make_recognizer(tmp_path, cfg)) is B


def test_the_catalogue_and_the_recognisers_use_the_same_names():
    assert {i.id for i in catalog.RECOGNIZERS} == set(speech.RECOGNIZERS) == set(Config().asr.order)
    assert all(cls.id == name for name, (_label, cls, _why) in speech.RECOGNIZERS.items())


# -- the catalogue -------------------------------------------------------------------

def test_what_is_missing_follows_the_settings(tmp_path, monkeypatch):
    here: set[str] = set()
    for item in catalog.ITEMS.values():
        monkeypatch.setattr(type(item), "installed", lambda self, models: self.id in here)
    monkeypatch.setattr(catalog, "nvidia_gpu", lambda: catalog.Gpu("Some card", 8000))
    cfg = Config()
    cfg.models_dir = str(tmp_path)
    assert [i.id for i in catalog.missing(cfg)] == ["vad", "whisper", "hy-mt2"]
    assert [i.id for i in catalog.recommended()] == ["vad", "whisper", "hy-mt2"]
    here.update({"vad", "sensevoice"})
    assert [i.id for i in catalog.missing(cfg)] == ["hy-mt2"]  # the second recogniser will do
    cfg.asr.order = ["whisper"]
    assert [i.id for i in catalog.missing(cfg)] == ["whisper", "hy-mt2"]
    cfg.translate.backend = "none"
    cfg.asr.backend = "sensevoice"
    assert catalog.missing(cfg) == []

    monkeypatch.setattr(catalog, "nvidia_gpu", lambda: None)  # a PC without such a card
    monkeypatch.setattr(catalog, "graphics_cards", lambda: ())  # nor any other
    here.clear()
    cfg = Config()
    cfg.models_dir = str(tmp_path)
    assert [i.id for i in catalog.missing(cfg)] == ["vad", "sensevoice", "hy-mt2"]
    assert [i.id for i in catalog.recommended()] == ["vad", "sensevoice", "hy-mt2"]
    assert "NVIDIA" in catalog.WHISPER.blocked()


def test_removing_a_linked_folder_leaves_what_it_points_at(tmp_path):
    kept = tmp_path / "elsewhere"
    kept.mkdir()
    (kept / "model.bin").write_text("precious")
    models = tmp_path / "models"
    models.mkdir()
    link = models / setup.WHISPER_DIR
    if sys.platform == "win32":  # a junction: what a user makes without administrator rights
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(kept)], check=True,
                       capture_output=True)
    else:
        os.symlink(kept, link, target_is_directory=True)
    assert catalog.WHISPER.on_disk(models)
    catalog.delete(link)
    assert not link.exists() and (kept / "model.bin").read_text() == "precious"


def test_removing_deletes_files_folders_and_unfinished_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "libraries_installed", lambda: False)
    (tmp_path / "sense-voice").mkdir()
    (tmp_path / "sense-voice" / "model.int8.onnx.part").write_text("half")
    (tmp_path / "silero_vad.onnx").write_text("stays")
    gguf = tmp_path / setup.LLM_ASSET.dest
    Path(str(gguf) + ".part").write_text("half")
    (tmp_path / "llama").mkdir()
    (tmp_path / "llama" / "llama-server.exe").write_text("")
    assert catalog.SENSEVOICE.on_disk(tmp_path) and not catalog.SENSEVOICE.installed(tmp_path)
    assert catalog.SENSEVOICE.remove(tmp_path) == "" and catalog.TRANSLATION.remove(tmp_path) == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["silero_vad.onnx"]
    assert not catalog.SENSEVOICE.on_disk(tmp_path)


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A folder served over HTTP on this machine, reached without any proxy."""
    folder = tmp_path / "www"
    folder.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(folder), **kw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(setup, "_openers", lambda: {"": direct})
    yield folder, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_download_can_be_stopped_and_taken_up_again(served, tmp_path):
    folder, url = served
    body = os.urandom(900_000)
    (folder / "big.bin").write_bytes(body)
    asset = setup.Asset("sub/big.bin", {"global": [f"{url}/big.bin"]}, size=len(body),
                        sha256=setup.hashlib.sha256(body).hexdigest())
    models, said, cancel = tmp_path / "models", [], threading.Event()
    cancel.set()
    with pytest.raises(setup.Cancelled):
        setup.download(asset, models, "auto", said.append, lambda *p: None, cancel)
    part = models / "sub" / "big.bin.part"
    assert not setup.present(asset, models) and part.exists()  # kept for the next try
    part.write_bytes(body[:300_000])
    cancel.clear()
    setup.download(asset, models, "auto", said.append, lambda *p: None, cancel)
    assert (models / "sub" / "big.bin").read_bytes() == body and not part.exists()
    assert any(line.startswith("download sub/big.bin") for line in said)


def test_an_item_downloads_all_its_files_with_one_running_total(served, tmp_path, monkeypatch):
    folder, url = served
    files = {"a.bin": os.urandom(700_000), "b.bin": os.urandom(500_000)}
    assets = []
    for name, body in files.items():
        (folder / name).write_bytes(body)
        assets.append(setup.Asset(f"thing/{name}", {"global": [f"{url}/{name}"]}, size=len(body)))

    class Thing(catalog.Item):
        id, paths = "thing", ("thing",)

        def assets(self):
            return assets

    thing, seen = Thing(), []
    assert thing.download_bytes(tmp_path) == 1_200_000 and not thing.on_disk(tmp_path)
    # A clock of the downloader's own that jumps a second per look: every chunk is reported.
    monkeypatch.setattr(setup, "time", types.SimpleNamespace(monotonic=itertools.count().__next__))
    thing.install(tmp_path, "auto", lambda line: None, lambda *p: seen.append(p), None)
    assert thing.installed(tmp_path) and thing.download_bytes(tmp_path) == 0
    assert thing.disk_bytes(tmp_path) == 1_200_000
    totals = {total for _got, total, _speed in seen}
    assert totals == {1_200_000} and max(got for got, _t, _s in seen) > 700_000
    assert [got for got, _t, _s in seen] == sorted(got for got, _t, _s in seen)


def test_the_installer_is_told_which_packages_and_stops_when_asked(tmp_path, monkeypatch):
    """The Whisper libraries: the locked versions, into the running environment."""
    calls = []

    def fake_run(cmd, say, cancel, cwd=None, env=None):
        calls.append(cmd)
        if cmd[1] == "export":
            Path(cmd[cmd.index("-o") + 1]).write_text("faster-whisper==1.2.1\n")

    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "pyproject.toml").write_text("")
    uv = tmp_path / ("uv.exe" if os.name == "nt" else "uv")
    uv.write_text("")
    monkeypatch.setenv("VALOROYCE_UV", str(uv))
    monkeypatch.setattr(catalog, "project_root", lambda: tmp_path)
    monkeypatch.setattr(catalog, "_run", fake_run)
    monkeypatch.setattr(catalog.urllib.request, "getproxies", lambda: {})
    catalog.install_libraries("cn", lambda line: None)
    export, install = calls
    assert export[:2] == [str(uv), "export"] and "--frozen" in export
    assert export[export.index("--extra") + 1] == "whisper"
    assert install[1:3] == ["pip", "install"] and install[install.index("--python") + 1] == sys.executable
    assert install[install.index("--default-index") + 1] == catalog._TUNA[0] and "-r" in install

    monkeypatch.delenv("VALOROYCE_UV")
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(catalog.shutil, "which", lambda name: None)
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    monkeypatch.setattr(catalog, "project_root", lambda: None)
    with pytest.raises(RuntimeError, match="start.bat"):
        catalog.install_libraries("cn", lambda line: None)


def test_a_tool_that_is_cancelled_is_stopped():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(setup.Cancelled):
        catalog._run([sys.executable, "-c", "import time; time.sleep(30)"], lambda line: None, cancel)
    said = []
    catalog._run([sys.executable, "-c", "print('one'); print('two')"], said.append, None)
    assert said == ["one", "two"]
    with pytest.raises(RuntimeError, match="boom"):
        catalog._run([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
                     lambda line: None, None)


# -- the window ------------------------------------------------------------------------

@pytest.fixture
def window(saved, tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication

    from valoroyce.settings import SettingsWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(catalog, "nvidia_gpu", lambda: catalog.Gpu("Some card", 8000))

    class Host:
        busy, pipeline, recognizer, from_file = False, None, None, False

        def __init__(self):
            self.cfg, self.pending, self.changed, self.calls = Config(), set(), [], []
            self.cfg.models_dir = str(tmp_path / "models")
            self.move = QAction("move", checkable=True)

        def uses(self, item):
            return False

        def setting_changed(self, name):
            self.changed.append(name)

        def __getattr__(self, name):  # halt, resume, apply_pending, reset_position
            return lambda *a: self.calls.append(name)

    host = Host()
    win = SettingsWindow(host)
    yield win
    win.deleteLater()
    app.processEvents()


def test_a_change_in_the_window_is_applied_remembered_and_passed_on(window):
    host = window.host
    window.set("overlay.font_size", 30)
    window.set("overlay.font_size", 30)  # no change: nothing happens
    window.set("translate.gpu", False)
    assert host.cfg.overlay.font_size == 30 and host.cfg.translate.gpu is False
    assert host.changed == ["overlay.font_size", "translate.gpu"]
    state.apply(again := Config())
    assert again.overlay.font_size == 30 and again.translate.gpu is False


def test_recognisers_can_be_reordered_and_switched_off_but_not_all(window):
    page, cfg = window.models, window.host.cfg
    whisper, sensevoice = catalog.WHISPER, catalog.SENSEVOICE
    assert page.order() == ["whisper", "whispercpp", "sensevoice"]
    page.toggle(catalog.WHISPER_VULKAN, False)
    assert page.order() == ["whisper", "sensevoice"]
    page.move(whisper, 1)
    assert cfg.asr.order == ["sensevoice", "whisper"]
    assert page.asr.itemAt(0).widget() is page.cards["sensevoice"]
    page.move(whisper, 1)  # already last
    assert cfg.asr.order == ["sensevoice", "whisper"]
    page.toggle(sensevoice, False)
    assert cfg.asr.order == ["whisper"] and not page.enabled(sensevoice)
    page.toggle(whisper, False)  # the last one stays
    assert cfg.asr.order == ["whisper"] and page.cards["whisper"].enable.isChecked()
    page.toggle(sensevoice, True)
    assert cfg.asr.order == ["whisper", "sensevoice"]
    state.apply(again := Config())
    assert again.asr.order == ["whisper", "sensevoice"]

    cfg.asr.backend = "sensevoice"  # fixed by hand in config.toml: shown as the only one in use
    assert page.order() == ["sensevoice"]
    page.toggle(whisper, True)
    assert (cfg.asr.backend, cfg.asr.order) == ("auto", ["sensevoice", "whisper"])


def test_switching_translation_off_and_on(window):
    page, cfg = window.models, window.host.cfg
    page.toggle(catalog.TRANSLATION, False)
    assert cfg.translate.backend == "none" and not page.enabled(catalog.TRANSLATION)
    page.toggle(catalog.TRANSLATION, True)
    assert cfg.translate.backend == "llama"


def test_the_first_start_offers_what_this_pc_needs(window):
    page = window.models
    assert not page.banner.isHidden()
    assert "Whisper" in page.banner_text.text() and "Hy-MT2" in page.banner_text.text()
    started = []
    page.start = lambda item: started.append(item.id)
    page.download_recommended()
    assert started == ["vad", "whisper", "hy-mt2"]


def test_a_finished_download_starts_captions_and_a_failed_one_says_why(window, monkeypatch):
    page, host = window.models, window.host
    page.jobs["sensevoice"] = threading.Event()
    page._on_done("sensevoice", "no network", False)
    assert "no network" in page.cards["sensevoice"].detail.text() and not page.jobs
    assert host.calls == ["resume"]  # resume() itself checks what is still missing
    host.pipeline = object()  # captions are running: a new recogniser waits for "apply"
    page.jobs["whisper"] = threading.Event()
    page._on_done("whisper", "", False)
    assert host.pending == {"asr"} and host.calls == ["resume"]


def test_every_page_builds_and_the_window_can_be_reopened(window):
    from valoroyce.settings import PAGES

    assert window.stack.count() == len(PAGES)
    window.open("compose")
    assert window.nav.currentRow() == [key for key, _ in PAGES].index("compose")
    window.hide()
    window.open("models")  # pages are built anew; the models page is the same object
    assert window.stack.count() == len(PAGES) and window.models.parent() is not None
    assert window.stack.widget(0).widget().findChild(type(window.models)) is window.models


def test_an_own_caption_model_is_switched_on_set_up_and_warned_about(window):
    from valoroyce.settings import CUSTOM_PORT, PAGES, custom_problem

    page, cfg = window.models, window.host.cfg
    custom = cfg.translate.custom
    assert page.custom.status.text() == "未启用" and not page.custom.enable.isChecked()
    page.toggle_custom(True)  # a service by default, and the page to set it up opens
    assert custom.backend == "openai" and page.custom.enable.isChecked()
    assert window.nav.currentRow() == [key for key, _ in PAGES].index("translate")
    assert custom_problem(cfg) == "还没填模型名称" and "还没填模型名称" in page.custom.status.text()

    window.set("translate.custom.model", "qwen2.5:7b")  # Ollama on this computer: nothing more
    assert custom_problem(cfg) == "" and "本机服务 127.0.0.1:11434" in page.custom.status.text()
    window.set("translate.custom.base_url", "https://api.deepseek.com")
    assert custom_problem(cfg) == "在线服务需要 API Key"
    window.set("translate.custom.api_key", "sk-test")
    page.refresh()
    assert "在线服务 api.deepseek.com" in page.custom.status.text()
    window._build_pages()
    risk = [w for w in window.stack.widget(3).findChildren(type(page.custom.status))
            if "每一句话" in w.text()]
    assert risk and not risk[0].isHidden()  # the risk is stated next to the address

    page.toggle_custom(False)
    assert custom.backend == "" and page.custom.status.text() == "未启用"
    state.save(custom_kind="llama")  # a file of one's own was chosen last time
    page.toggle_custom(True)
    assert (custom.backend, custom.base_url) == ("llama", CUSTOM_PORT)
    assert custom_problem(cfg) == "还没选模型文件"
    state.apply(again := Config())
    assert (again.translate.custom.backend, again.translate.custom.api_key) == ("llama", "sk-test")
    assert window.host.changed[-1].startswith("translate.custom.")  # models reload on apply
