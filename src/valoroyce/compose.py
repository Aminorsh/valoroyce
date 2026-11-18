"""The message box: type or say Chinese, get Japanese or English on the clipboard.

Said with the voice shortcut, the message is translated and copied at once;
said with Tab (or the microphone button) in the box, it waits to be checked.

The result is only copied. Pasting it into the game's chat is left to the
player; this program never sends keystrokes to the game.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from . import state

LANGUAGES = [("ja", "日语"), ("en", "English")]

STYLE = """
QWidget#compose { background: #0F1923; }
QLabel { color: #9AA4AE; font-size: 13px; }
QLabel#result { color: #FFFFFF; font-size: 18px; padding: 6px 0; }
QLineEdit { background: #1F2B36; color: #FFFFFF; border: 1px solid #364452;
            border-radius: 6px; padding: 8px 10px; font-size: 16px; }
QLineEdit:focus { border-color: #FF4655; }
QRadioButton { color: #D5DBE1; font-size: 13px; spacing: 6px; }
QPushButton#mic { background: #1F2B36; color: #FFFFFF; border: 1px solid #364452;
                  border-radius: 6px; font-size: 16px; min-width: 40px; min-height: 36px; }
QPushButton#mic:checked { background: #FF4655; border-color: #FF4655; }
"""


class ComposeWindow(QWidget):
    _finished = Signal(int, str, str, bool)  # request number, result, error, used the fallback
    _heard_state = Signal(int, str)  # listening number, progress text
    _heard = Signal(int, str, str)   # listening number, text, error

    def __init__(self, get_translator, target: str = "ja", voice=None):
        super().__init__()
        self.get_translator = get_translator
        # voice(on_state, on_done) starts listening and returns the listening;
        # None: no spoken messages.
        self.voice = voice
        self._listening = None
        self._heard_number = 0
        self._translate_heard = False
        self.target = target if target in dict(LANGUAGES) else "ja"
        self._request = 0
        self.setObjectName("compose")
        self.setWindowTitle("Valoroyce · 发消息")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(520)

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入中文，按回车翻译")
        self.input.returnPressed.connect(self.translate)
        self.input.installEventFilter(self)  # Tab: talk instead of moving the focus
        self.mic = QPushButton("🎤")
        self.mic.setObjectName("mic")
        self.mic.setCheckable(True)
        self.mic.setToolTip("说中文（Tab）：识别后先在框里检查，再按回车翻译")
        self.mic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic.clicked.connect(lambda: self.listen(translate_after=False))
        self.mic.setVisible(voice is not None)
        line = QHBoxLayout()
        line.addWidget(self.input, 1)
        line.addWidget(self.mic)

        langs = QHBoxLayout()
        langs.addWidget(QLabel("翻译成"))
        self._group = QButtonGroup(self)
        self._buttons = {}
        for code, label in LANGUAGES:
            button = self._buttons[code] = QRadioButton(label)
            button.setChecked(code == self.target)
            button.toggled.connect(lambda on, c=code: on and self._set_target(c))
            self._group.addButton(button)
            langs.addWidget(button)
        langs.addStretch(1)

        self.result = QLabel("")
        self.result.setObjectName("result")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = "回车翻译 · Tab 说话 · Esc 关闭" if voice is not None else "回车翻译 · Esc 关闭"
        self.status = QLabel(self.hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(line)
        layout.addLayout(langs)
        layout.addWidget(self.result)
        layout.addWidget(self.status)
        self._finished.connect(self._on_finished)
        self._heard_state.connect(self._on_heard_state)
        self._heard.connect(self._on_heard)

    def set_target(self, code: str) -> None:
        """Choose the language from outside the box (the settings window)."""
        if code in self._buttons and code != self.target:
            self._buttons[code].setChecked(True)

    def _set_target(self, code: str) -> None:
        self.target = code
        state.save(compose_target=code)
        if self.input.text().strip():
            self.translate()

    def open(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.input.selectAll()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.stop_listening()
            self.hide()
        else:
            super().keyPressEvent(e)

    def eventFilter(self, obj, e) -> bool:
        if obj is self.input and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Tab:
            if self.voice is not None:
                self.listen(translate_after=False)
            return True
        return super().eventFilter(obj, e)

    # -- speaking instead of typing ---------------------------------------------

    def open_and_listen(self) -> None:
        """The voice shortcut: listen, then translate and copy at once.
        Pressed again while listening, it ends the sentence."""
        if self.listening:
            self.stop_listening()
            return
        self.open()
        self.listen(translate_after=True)

    @property
    def listening(self) -> bool:
        return self._listening is not None and self._listening.active

    def stop_listening(self) -> None:
        if self.listening:
            self._listening.stop()

    def listen(self, translate_after: bool) -> None:
        if self.listening:  # the second press ends the sentence
            self._listening.stop()
            return
        self._heard_number += 1
        number = self._heard_number
        self._translate_heard = translate_after
        try:
            self._listening = self.voice(lambda text: self._heard_state.emit(number, text),
                                         lambda text, error: self._heard.emit(number, text, error))
        except Exception as e:
            self._listening = None
            self.mic.setChecked(False)
            self.status.setText(str(e))
            return
        self.mic.setChecked(True)
        self.status.setStyleSheet("color: #FF4655;")  # red while the microphone is open

    def _on_heard_state(self, number: int, text: str) -> None:
        if number == self._heard_number:
            self.status.setText(text)

    def _on_heard(self, number: int, text: str, error: str) -> None:
        if number != self._heard_number:
            return
        self.mic.setChecked(False)
        self.status.setStyleSheet("")
        if error:
            self.status.setText(error)
            return
        if self._translate_heard:
            self.input.setText(text)
            self.translate()
        else:  # at the cursor: part typed, part said
            self.input.insert(text)
            self.input.setFocus()
            self.status.setText("检查一下，没问题按回车翻译")

    def translate(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        translator = self.get_translator()
        if translator is None:
            self.status.setText("翻译模型还在加载，请稍候再按回车")
            return
        self._request += 1
        number, target = self._request, self.target
        self.status.setText("翻译中…")

        def work() -> None:
            try:
                result = translator.compose(text, target)
                self._finished.emit(number, result, "", bool(getattr(translator, "fell_back", "")))
            except Exception as e:
                self._finished.emit(number, "", str(e), False)

        threading.Thread(target=work, daemon=True).start()

    def _on_finished(self, number: int, result: str, error: str, fell_back: bool = False) -> None:
        if number != self._request:
            return  # an older request; a newer one is on its way
        if error or not result:
            self.status.setText(f"翻译失败：{error or '模型没有返回内容'}")
            return
        self.result.setText(result)
        QGuiApplication.clipboard().setText(result)
        if fell_back:  # the caption model did this one: see ComposeWithFallback
            self.status.setText("已复制 · 这句是本地字幕模型翻译的")
        else:
            self.status.setText("已复制 · 回到游戏聊天框按 Ctrl+V 粘贴")
        self.input.selectAll()
