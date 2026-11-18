"""Listening to one program, or to everything but one."""

import subprocess
import sys
import time
import wave

import numpy as np
import pytest

from valoroyce import apploop, audio
from valoroyce.apploop import Proc, pick_root
from valoroyce.config import AudioConfig

SR = 16000


def test_the_top_of_a_program_s_tree_is_listened_to():
    procs = [
        Proc(1, 0, "explorer.exe"),
        Proc(10, 1, "chrome.exe"),  # the browser
        Proc(11, 10, "chrome.exe"),  # its audio service, tabs ...
        Proc(12, 10, "chrome.exe"),
        Proc(13, 11, "chrome.exe"),
        Proc(20, 1, "Chrome.exe"),  # a second, smaller instance
        Proc(21, 20, "chrome.exe"),
        Proc(30, 1, "KOOK.exe"),
    ]
    assert pick_root(procs, "chrome.exe") == 10
    assert pick_root(procs, "kook.exe") == 30
    assert pick_root(procs, "discord.exe") == 0
    # a parent id that was reused by a child of the same name does not loop
    assert pick_root([Proc(5, 6, "a.exe"), Proc(6, 5, "a.exe"), Proc(7, 7, "a.exe")], "a.exe") == 7


def test_a_program_is_listened_to_only_when_one_is_named(monkeypatch):
    made = []
    monkeypatch.setattr(audio, "AppSource", lambda app, exclude: made.append((app, exclude)) or "app")
    monkeypatch.setattr(audio, "DeviceSource", lambda device, loopback: "device")
    assert audio.open_source(AudioConfig()) == "device"
    assert audio.open_source(AudioConfig(app="KOOK.exe", app_mode="except")) == "app"
    assert audio.open_source(AudioConfig(app="RiotClientServices.exe")) == "app"
    assert made == [("KOOK.exe", True), ("RiotClientServices.exe", False)]
    assert audio.open_source(AudioConfig(source="mic", app="KOOK.exe")) == "device"
    with pytest.raises(ValueError, match="app_mode"):
        audio.open_source(AudioConfig(app="KOOK.exe", app_mode="all"))


def test_silence_goes_on_while_the_program_is_not_running(monkeypatch):
    monkeypatch.setattr(apploop, "supported", lambda: True)
    monkeypatch.setattr(apploop, "processes", lambda: [Proc(1, 0, "explorer.exe")])
    source = audio.AppSource("RiotClientServices.exe")
    assert "Riot" in source.name
    started, blocks = time.monotonic(), []
    for block in source:
        blocks.append(block)
        if len(blocks) == 5:
            break
    source.stop()
    source.join(2.0)
    took = time.monotonic() - started
    assert all(len(b) == SR * audio.BLOCK_SECONDS and not b.any() for b in blocks)
    assert 0.35 < took < 1.5  # at the pace of the clock, not all at once
    assert source.target == 0


def _speakers() -> bool:
    try:
        import soundcard

        return bool(soundcard.all_speakers())
    except Exception:
        return False


@pytest.mark.skipif(not (apploop.supported() and _speakers()), reason="needs Windows with an output device")
def test_one_program_is_heard_and_the_other_is_not(tmp_path):
    """Two programs play two tones at once; each mode hears only its own."""
    def tone(freq):
        path = tmp_path / f"{freq}.wav"
        t = np.arange(48000 * 3) / 48000
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes((0.1 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16).tobytes())
        return path

    def play(path):
        code = f"import time, winsound; time.sleep(0.5); winsound.PlaySound(r'{path}', winsound.SND_FILENAME)"
        return subprocess.Popen([sys.executable, "-c", code])

    low, high = tone(440), tone(660)
    for exclude, heard, unheard in ((False, 440, 660), (True, 660, 440)):
        players = [play(low), play(high)]
        loop = apploop.Loopback(players[0].pid, exclude=exclude)
        got, started = [], time.monotonic()
        try:
            while time.monotonic() - started < 3.0:
                loop.wait(0.1)
                got += [samples for samples, _position in loop.read()]
        finally:
            loop.close()
            for p in players:
                p.wait(10)
        x = np.concatenate(got)
        spectrum, freqs = np.abs(np.fft.rfft(x)), np.fft.rfftfreq(len(x), 1 / SR)

        def energy(f):
            return spectrum[(freqs > f - 5) & (freqs < f + 5)].max()

        assert len(x) > 2.5 * SR  # delivered steadily, silence included
        assert energy(heard) > 100 * energy(unheard), (exclude, energy(heard), energy(unheard))
