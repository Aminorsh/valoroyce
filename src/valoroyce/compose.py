"""The message box: type Chinese, get Japanese or English on the clipboard.

The result is only copied. Pasting it into the game's chat is left to the
player; this program never sends keystrokes to the game.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QVBoxLayout, QWidget,
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
"""


class ComposeWindow(QWidget):
    _finished = Signal(int, str, str, bool)  # request number, result, error, used the fallback

    def __init__(self, get_translator, target: str = "ja"):
        super().__init__()
        self.get_translator = get_translator
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
        self.status = QLabel("回车翻译 · Esc 关闭")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(self.input)
        layout.addLayout(langs)
        layout.addWidget(self.result)
        layout.addWidget(self.status)
        self._finished.connect(self._on_finished)

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
            self.hide()
        else:
            super().keyPressEvent(e)

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
