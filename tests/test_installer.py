"""The installed copy: the starter's check for an up-to-date .venv, and the
models the player ticked in the installer."""

import re
from pathlib import Path

import pytest

from valoroyce import catalog, starter

REPO = Path(__file__).resolve().parent.parent


def _install(root: Path) -> Path:
    (root / "src" / "valoroyce").mkdir(parents=True)
    (root / "src" / "valoroyce" / "cli.py").write_text("print('v1')\n")
    (root / "uv.lock").write_text("lock\n")
    (root / "pyproject.toml").write_text("[project]\n")
    return root


def test_the_fingerprint_follows_the_program_and_its_packages(tmp_path):
    root = _install(tmp_path)
    first = starter.fingerprint(root)
    assert starter.fingerprint(root) == first

    cache = root / "src" / "valoroyce" / "__pycache__"
    cache.mkdir()
    (cache / "cli.cpython-313.pyc").write_bytes(b"\0")
    assert starter.fingerprint(root) == first  # run-time leftovers do not count

    (root / "src" / "valoroyce" / "cli.py").write_text("print('v2')\n")
    second = starter.fingerprint(root)
    assert second != first
    (root / "uv.lock").write_text("other lock\n")
    assert starter.fingerprint(root) != second


def test_ready_only_when_installed_for_this_version(tmp_path):
    root = _install(tmp_path)
    assert not starter.ready(root)  # nothing installed yet

    (root / starter.GUI).parent.mkdir(parents=True)
    (root / starter.GUI).write_bytes(b"")
    assert not starter.ready(root)  # no marker: an install that did not finish

    (root / starter.MARKER).write_text(starter.fingerprint(root) + "\n")
    assert starter.ready(root)

    (root / "src" / "valoroyce" / "cli.py").write_text("print('update')\n")
    assert not starter.ready(root)  # an update came: install again


def test_the_installers_choice_is_read_once(tmp_path):
    choice = tmp_path / "models-to-download.txt"
    assert catalog.take_choice(choice) is None  # no installer, no choice

    # Inno writes CRLF; a BOM or a stray id must not break it.
    choice.write_bytes("﻿whisper\r\nhy-mt2\r\nwhisper\r\nsomething-old\r\n".encode())
    assert [i.id for i in catalog.take_choice(choice)] == ["whisper", "hy-mt2"]
    assert not choice.exists()
    assert catalog.take_choice(choice) is None

    choice.write_text("")  # every model unticked: still a choice, of nothing
    assert catalog.take_choice(choice) == []


def test_the_installer_offers_only_models_the_program_knows():
    script = (REPO / "installer" / "valoroyce.iss").read_text(encoding="utf-8")
    offered = re.findall(r"Chosen \+ '([^']+)'", script)
    assert offered and set(offered) <= set(catalog.ITEMS)


# -- the first start: packages fetched with a measure, then installed ---------------

PLAYER_PACKAGES = [("cffi", "2.1.1"), ("numpy", "2.5.4"), ("pycparser", "3.12"),
                   ("PySide6-Essentials", "6.12.0"), ("sherpa-onnx", "2.0.1"),
                   ("sherpa_onnx_core", "2.0.1"), ("shiboken6", "6.12.0"), ("soundcard", "0.4.6")]


def test_each_package_gets_the_wheel_that_fits_the_shipped_python():
    tags = starter._tags("win_amd64", (3, 13))
    wheels = starter.locked_wheels(REPO / "uv.lock", PLAYER_PACKAGES, tags)
    if wheels is None or len(wheels) != len(PLAYER_PACKAGES):  # the lock moved on
        pytest.skip("uv.lock no longer pins these versions")
    names = {w.filename for w in wheels}
    assert "numpy-2.5.4-cp313-cp313-win_amd64.whl" in names
    assert "pycparser-3.12-py3-none-any.whl" in names
    assert any(n.startswith("pyside6_essentials-6.12.0-") and n.endswith("abi3-win_amd64.whl")
               for n in names)
    for w in wheels:
        assert w.path.startswith("/packages/") and len(w.sha256) == 64 and w.size > 0


def test_a_package_without_a_fitting_wheel_hands_the_work_back_to_uv(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text('''
[[package]]
name = "only-mac"
version = "1.0"
wheels = [{ url = "https://files.pythonhosted.org/packages/aa/only_mac-1.0-cp313-cp313-macosx_11_0_arm64.whl", hash = "sha256:00", size = 5 }]
''')
    assert starter.locked_wheels(lock, [("only-mac", "1.0")], starter._tags("win_amd64", (3, 13))) is None
    assert starter.locked_wheels(lock, [("missing", "2.0")], starter._tags("win_amd64", (3, 13))) is None


def test_downloads_fall_back_to_the_next_server_and_check_what_arrives(tmp_path, monkeypatch):
    import hashlib

    good, bad = b"wheel one" * 100, b"wheel two" * 50
    wheels = [starter.Wheel("a-1-py3-none-any.whl", "/packages/a", hashlib.sha256(good).hexdigest(), len(good)),
              starter.Wheel("b-1-py3-none-any.whl", "/packages/b", hashlib.sha256(bad).hexdigest(), len(bad))]
    served = {"https://slow/packages/a": OSError("timed out"), "https://fast/packages/a": good,
              "https://slow/packages/b": b"x" * len(bad), "https://fast/packages/b": bad}
    tried = []

    def fetch(_opener, url, part, size, progress, cancel):
        tried.append(url)
        body = served[url]
        if isinstance(body, Exception):
            raise body
        part.write_bytes(body)
        progress(len(body), size, 1e6)

    monkeypatch.setattr(starter.setup, "_fetch", fetch)
    seen = []
    routes = [("", "https://slow", "", {}, None), ("", "https://fast", "", {}, None)]
    starter.fetch_wheels(wheels, tmp_path, routes, lambda got, total, speed: seen.append((got, total)))
    assert (tmp_path / "a-1-py3-none-any.whl").read_bytes() == good
    assert (tmp_path / "b-1-py3-none-any.whl").read_bytes() == bad  # the wrong body was refused
    assert tried == ["https://slow/packages/a", "https://fast/packages/a",
                     "https://slow/packages/b", "https://fast/packages/b"]
    total = len(good) + len(bad)
    assert all(t == total for _, t in seen) and seen[-1][0] == total
    assert [g for g, _ in seen] == sorted(g for g, _ in seen)  # never goes backwards

    tried.clear()
    starter.fetch_wheels(wheels, tmp_path, routes, lambda *_: None)
    assert tried == []  # already here: nothing fetched again


def test_the_install_step_is_measured_by_what_it_writes(tmp_path):
    import sys

    target = tmp_path / "venv"
    script = ("import pathlib, sys, time; t = pathlib.Path(sys.argv[1]); t.mkdir()\n"
              "for i in range(3):\n"
              "    (t / str(i)).write_bytes(b'x' * 1000); time.sleep(0.6)\n")
    seen = []
    starter._watched([sys.executable, "-c", script, str(target)], tmp_path, None, lambda _l: None,
                     None, lambda got, total, speed: seen.append(got), 3000, [target])
    assert seen[-1] == 3000 and 0 < max(seen[:-1], default=1) <= 3000


def test_the_window_says_how_far_and_how_long():
    line = starter.describe("download", 45_000_000, 115_000_000, 3_500_000)
    assert line.startswith("45 / 115 MB · 3.5 MB/s · 还剩约 20 秒")
    assert starter.describe("download", 0, 115_000_000, 100_000).endswith("约 19 分钟")
    assert starter.describe("install", 200_000_000, 306_000_000, 0) == "200 / 306 MB"


def test_the_speed_shown_is_for_the_whole_step_and_waits_to_settle():
    now = [0.0]
    rate = starter.Rate(clock=lambda: now[0])
    assert rate("download", 0) == 0.0
    now[0] = 1.0
    assert rate("download", 1_000_000) == 0.0  # too early to say
    now[0] = 4.0
    assert rate("download", 20_000_000) == 5_000_000  # across files, from the step's start
    now[0] = 5.0
    assert rate("install", 0) == 0.0  # a new step starts over


def test_the_program_installs_from_the_shipped_wheel_without_a_server(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(starter.catalog, "_run", lambda cmd, *a, **k: ran.append(cmd))
    starter._install_program("uv", "py", tmp_path, {}, lambda _l: None, None, "https://index")
    assert ran[-1][-3:] == ["--default-index", "https://index", "."]  # no wheel: built from source

    (tmp_path / starter.PROGRAM).mkdir()
    (tmp_path / starter.PROGRAM / "valoroyce-0.2.0-py3-none-any.whl").write_bytes(b"")
    starter._install_program("uv", "py", tmp_path, {}, lambda _l: None, None)
    assert "--no-index" in ran[-1] and ran[-1][-1].endswith("valoroyce-0.2.0-py3-none-any.whl")


def test_an_update_with_every_package_in_place_needs_no_network(tmp_path, monkeypatch):
    asked = []

    def plan(uv, python, wanted, index, root, env):
        asked.append(index)
        return []

    monkeypatch.setattr(starter, "_plan", plan)
    assert not starter._complete_offline("uv", "py", tmp_path / "app.txt", tmp_path, {})  # no wheel
    (tmp_path / starter.PROGRAM).mkdir()
    (tmp_path / starter.PROGRAM / "valoroyce-0.2.0-py3-none-any.whl").write_bytes(b"")
    assert starter._complete_offline("uv", "py", tmp_path / "app.txt", tmp_path, {})
    assert asked == [""]  # looked at .venv only, offline

    monkeypatch.setattr(starter, "_plan", lambda *a: [("numpy", "2.5.4")])
    assert not starter._complete_offline("uv", "py", tmp_path / "app.txt", tmp_path, {})

    def offline_fails(*a):
        raise RuntimeError("cffi was not found in the cache")

    monkeypatch.setattr(starter, "_plan", offline_fails)
    assert not starter._complete_offline("uv", "py", tmp_path / "app.txt", tmp_path, {})
