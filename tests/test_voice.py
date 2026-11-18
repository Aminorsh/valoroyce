"""Speaking a message instead of typing it."""

import threading
import time

import numpy as np
import pytest

from valoroyce import voice
from valoroyce.config import Config
from valoroyce.speech import Segment, Transcript

BLOCK = 1600  # 0.1 s


class Mic:
    """Blocks of a fake microphone, at the pace of time."""

    def __init__(self, blocks=None):
        self.blocks = blocks
        self.stopped = False

    def __iter__(self):
        n = 0
        while not self.stopped and (self.blocks is None or n < self.blocks):
            time.sleep(0.01)
            n += 1
            yield np.full(BLOCK, 0.1 if n > 2 else 0.0, dtype=np.float32)

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


class Detector:
    """Speech from block 3; the sentence ends at block `ends` (None: never)."""

    def __init__(self, ends=None, speech=True):
        self.ends, self.speech, self.n = ends, speech, 0
        self.flushed = False

    def feed(self, block):
        self.n += 1
        if self.ends is not None and self.n == self.ends:
            return [Segment(np.ones(self.n * BLOCK, dtype=np.float32), 0)]
        return []

    def current(self):
        if self.speech and self.n >= 3:
            return Segment(np.ones(self.n * BLOCK, dtype=np.float32), 0)
        return None

    def flush(self):
        self.flushed = True
        return [Segment(np.ones(self.n * BLOCK, dtype=np.float32), 0)] if self.speech else []


class Ears:
    def __init__(self, text="对面两个人在A点"):
        self.text, self.asked = text, []

    def transcribe(self, samples, language="", prompt=""):
        self.asked.append((len(samples), language))
        return Transcript(self.text, language)


def run(detector, mic=None, ears=None, stop_after=None):
    ears = ears or Ears()
    states, done = [], []
    finished = threading.Event()
    listening = voice.Listening(Config(), ears, states.append,
                                lambda text, error: (done.append((text, error)), finished.set()),
                                source=mic or Mic(), segmenter=detector).start()
    if stop_after is not None:
        time.sleep(stop_after)
        listening.stop()
    assert finished.wait(10)
    return done[0], states, ears


def test_a_pause_ends_the_sentence_and_it_is_heard_as_chinese():
    (text, error), states, ears = run(Detector(ends=8))
    assert (text, error) == ("对面两个人在A点", "")
    assert ears.asked == [(8 * BLOCK, "zh")]
    assert states[0].startswith("● 正在听") and states[-1] == "识别中…"


def test_the_shortcut_pressed_again_ends_it_at_once():
    detector = Detector(ends=None)
    (text, error), _states, ears = run(detector, stop_after=0.3)
    assert text and detector.flushed and ears.asked[0][1] == "zh"


def test_silence_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(voice, "WAIT_SECONDS", 0.2)
    (text, error), _states, ears = run(Detector(speech=False))
    assert text == "" and "没有听到" in error and ears.asked == []


def test_nothing_recognised_asks_to_say_it_again():
    (text, error), _states, _ears = run(Detector(ends=6), ears=Ears(""))
    assert text == "" and "再说一次" in error


# -- the message box ------------------------------------------------------------

@pytest.fixture
def box(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from valoroyce import compose, state

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(state, "save", lambda **kw: None)
    asked = []

    class Heard:
        active = False

        def stop(self):
            pass

    def fake_voice(on_state, on_done):  # answers at once, on this thread
        on_state("● 正在听…")
        on_done("我去B点", "")
        return Heard()

    window = compose.ComposeWindow(lambda: asked.append(1) or None, "ja", voice=fake_voice)
    window.asked = asked
    return window


def test_tab_in_the_box_leaves_the_text_to_check(box):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    box.open()
    box.input.setText("快走，")
    box.input.end(False)
    box.eventFilter(box.input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
    assert box.input.text() == "快走，我去B点"  # at the cursor
    assert box.asked == [] and "回车" in box.status.text()


def test_the_voice_shortcut_translates_at_once(box):
    box.input.setText("old message")
    box.open_and_listen()
    assert box.input.text() == "我去B点"
    assert box.asked == [1]  # translation was asked for (the model is still loading here)


def test_without_voice_there_is_no_microphone_button(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from valoroyce import compose

    QApplication.instance() or QApplication([])
    window = compose.ComposeWindow(lambda: None, "ja")
    assert window.mic.isHidden() and "Tab" not in window.status.text()


def test_names_said_in_chinese_are_put_right_by_sound():
    pytest.importorskip("pypinyin")
    from valoroyce.glossary import Glossary

    g = Glossary.load()
    assert voice.tidy("目蝶在中路,小心他的技能。", g) == "暮蝶在中路，小心他的技能。"
    assert voice.tidy("一觉得大招好了", g).startswith("壹决")
    assert voice.tidy("B点下包了,快回房。", g) == "B点下包了，快回防。"
    # tones keep ordinary words apart; numbers keep their commas
    assert voice.tidy("我起了全甲,还有1,500", g) == "我起了全甲，还有1,500"
    assert voice.tidy("今天晚上我们去吃火锅吧。", g) == "今天晚上我们去吃火锅吧。"
