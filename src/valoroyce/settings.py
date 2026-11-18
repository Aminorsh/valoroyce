"""The settings window.

One page for the models (download, order of use, switch off, remove) and one
each for the caption strip, sound and recognition, translation and outgoing
messages. Every change is remembered at once (`state.py`); the ones that need
models reloaded wait for "应用更改" or for the window to be closed.
"""

from __future__ import annotations

import os
import tempfile
import threading
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import catalog, hotkey, state
from .setup import Cancelled
from .translate import CAPTION_MODEL, CaptionWithFallback, is_online

OK, WARN, OFF = "#5BD6A0", "#F5B85A", "#7E8A96"
PAGES = [("models", "模型"), ("captions", "字幕"), ("sound", "声音与识别"),
         ("translate", "翻译"), ("compose", "发消息"), ("about", "关于")]
MIRRORS = [("auto", "自动选择最快的"), ("cn", "国内镜像（ModelScope、清华）"),
           ("global", "国际源（Hugging Face、PyPI）")]
LANGUAGES = [(["ja", "en"], "日语为主，也识别英语"), (["ja"], "只有日语（最稳定）"),
             (["en", "ja"], "英语为主，也识别日语"), (["en"], "只有英语")]
NO_THINKING = {"type": "disabled"}
DEEPSEEK = {"compose.base_url": "https://api.deepseek.com", "compose.model": "deepseek-flash"}
# Ready-made addresses for the own caption model: (button, address, model name or None).
CUSTOM_PRESETS = [("Ollama", "http://127.0.0.1:11434", None),
                  ("LM Studio", "http://127.0.0.1:1234", None),
                  ("DeepSeek（在线）", "https://api.deepseek.com", "deepseek-flash")]
CUSTOM_KINDS = [("", "不使用（只用 Hy-MT2）"),
                ("openai", "兼容 OpenAI 接口的服务（Ollama、LM Studio、在线服务）"),
                ("llama", "自己的 .gguf 模型文件")]
CUSTOM_PORT = "http://127.0.0.1:8179"  # an own .gguf is served here, beside Hy-MT2 on 8177
ONLINE_RISK = ("这是在线服务：队友说的每一句话（识别出的文字）都会发给它，不再只在本机处理。"
               "有的在线服务会在某些话题上悄悄改写句子，意思可能和原话相反，字幕上看不出来。")

STYLE = """
QWidget#settings, QMessageBox { background: #0F1923; }
QWidget { color: #D5DBE1; font-size: 14px;
          font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif; }
QListWidget#nav { background: #0B131B; border: none; outline: none; padding-top: 10px; }
QListWidget#nav::item { padding: 11px 18px; color: #9AA4AE; border-left: 3px solid transparent; }
QListWidget#nav::item:hover { color: #FFFFFF; }
QListWidget#nav::item:selected { color: #FFFFFF; background: #16222D;
                                 border-left: 3px solid #FF4655; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #2C3C4C; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#h1 { color: #FFFFFF; font-size: 21px; font-weight: bold; }
QLabel#h2 { color: #FFFFFF; font-size: 15px; font-weight: bold; padding-top: 12px; }
QLabel#hint { color: #7E8A96; font-size: 12px; }
QLabel#title { color: #FFFFFF; font-size: 15px; font-weight: bold; }
QLabel#badge { background: #FF4655; color: #FFFFFF; border-radius: 3px; padding: 1px 6px;
               font-size: 11px; }
QFrame#card { background: #17232E; border: 1px solid #263544; border-radius: 8px; }
QFrame#banner { background: #2B1B24; border: 1px solid #FF4655; border-radius: 8px; }
QFrame#bar { background: #0B131B; }
QPushButton { background: #263544; color: #FFFFFF; border: none; border-radius: 5px;
              padding: 7px 14px; }
QPushButton:hover { background: #31445A; }
QPushButton:checked { background: #FF4655; }
QPushButton:disabled { color: #5E6A76; background: #1B2733; }
QPushButton#primary { background: #FF4655; }
QPushButton#primary:hover { background: #FF5E6B; }
QPushButton#primary:disabled { background: #5A2A33; color: #A0868B; }
QPushButton#danger { background: transparent; color: #FF7480; border: 1px solid #5A2A33; }
QPushButton#danger:hover { background: #2B1B24; }
QPushButton#arrow { padding: 6px 9px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #1F2B36; color: #FFFFFF; border: 1px solid #364452; border-radius: 5px;
    padding: 6px 8px; selection-background-color: #FF4655; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #FF4655; }
QLineEdit:disabled, QComboBox:disabled { color: #5E6A76; background: #16212B; }
QComboBox QAbstractItemView { background: #1F2B36; color: #FFFFFF; border: 1px solid #364452;
                              selection-background-color: #FF4655; }
QProgressBar { background: #1F2B36; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #FF4655; border-radius: 3px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; background: #1F2B36;
                       border: 1px solid #4A5B6C; border-radius: 4px; }
QCheckBox::indicator:checked { background: #FF4655; border-color: #FF4655;
                               image: url(CHECK_MARK); }
QSlider::groove:horizontal { background: #1F2B36; height: 6px; border-radius: 3px; }
QSlider::handle:horizontal { background: #FF4655; width: 16px; margin: -5px 0; border-radius: 8px; }
"""


def check_mark() -> str:
    """A white tick as a picture file: style sheets take pictures only from files."""
    path = Path(tempfile.gettempdir()) / "valoroyce-check.png"
    pix = QPixmap(34, 34)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#FFFFFF"), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(8, 18), QPointF(14, 24), QPointF(26, 10)])
    p.end()
    pix.save(str(path))
    return path.as_posix()


def size_text(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e7 else f"{n / 1e6:.1f} MB"


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    out = QLabel(text)
    if name:
        out.setObjectName(name)
    out.setWordWrap(wrap)
    return out


def button(text: str, name: str = "", on_click=None) -> QPushButton:
    out = QPushButton(text)
    if name:
        out.setObjectName(name)
    out.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        out.clicked.connect(lambda _=False: on_click())
    return out


def progress_line(line: str) -> str:
    """What the downloader says, for the player."""
    line = line.strip()
    if line.startswith("testing"):
        return "正在测速，选择最快的下载源…"
    if line.startswith("from "):
        return f"下载源：{line[5:]}"
    if line.startswith("download "):
        return f"正在下载 {line[9:].split('  - ')[0]}"
    if line.endswith("MB/s"):  # one source timed: "host (direct):   1.67 MB/s"
        return "正在测速 · " + " ".join(line.split())
    if line.startswith("installing"):  # "installing from HOST", then the installer's own lines
        return f"正在安装显卡运行库（约 1.3 GB，这一步没有进度条，请耐心等待）· {line[11:].strip()}"
    return line


# -- the models page ------------------------------------------------------------


class ModelCard(QFrame):
    def __init__(self, page: "ModelsPage", item: catalog.Item):
        super().__init__()
        self.page, self.item = page, item
        self.setObjectName("card")
        self.enable = QCheckBox("启用")
        self.enable.setVisible(not item.required)
        self.enable.clicked.connect(lambda on: page.toggle(item, on))
        self.badge = label("推荐", "badge")
        self.status = QLabel()
        top = QHBoxLayout()
        top.addWidget(label(item.title, "title"))
        top.addWidget(self.badge)
        top.addStretch(1)
        top.addWidget(self.status)

        self.up = button("▲", "arrow", lambda: page.move(item, -1))
        self.down = button("▼", "arrow", lambda: page.move(item, 1))
        self.action = button("下载", "primary", lambda: page.act(item))
        self.remove = button("删除", "danger", lambda: page.remove(item))
        low = QHBoxLayout()
        low.addWidget(label(item.hardware, "hint"), 1)
        for w in (self.enable, self.up, self.down, self.action, self.remove):
            low.addWidget(w)
        for w in (self.up, self.down):
            w.setVisible(item.role == "asr")
            w.setToolTip("调整使用顺序：排在前面的优先")

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.detail = label("", "hint", wrap=True)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        box.addLayout(top)
        box.addWidget(label(item.summary, wrap=True))
        box.addLayout(low)
        box.addWidget(self.bar)
        box.addWidget(self.detail)
        self.bar.hide()
        self.detail.hide()

    def say(self, text: str, colour: str = OFF) -> None:
        self.detail.setText(text)
        self.detail.setStyleSheet(f"color: {colour};")
        self.detail.setVisible(bool(text))

    def progress(self, got: int, total: int, speed: float) -> None:
        self.bar.show()
        if total <= 0:
            self.bar.setRange(0, 0)  # a moving bar: size not known
            return
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(got * 1000 / total))
        self.status.setText(f"{got / 1e6:.0f} / {total / 1e6:.0f} MB · {speed / 1e6:.1f} MB/s")
        self.status.setStyleSheet(f"color: {OFF};")

    def refresh(self, recommended: bool) -> None:
        item, page = self.item, self.page
        models = page.win.cfg.models_path
        busy = item.id in page.jobs
        installed, on_disk, blocked = item.installed(models), item.on_disk(models), item.blocked()
        enabled = page.enabled(item)
        self.badge.setVisible(recommended and not installed)
        self.enable.setChecked(enabled)
        self.action.setText("取消" if busy else "继续下载" if on_disk else "下载")
        if self.action.objectName() != ("" if busy else "primary"):
            self.action.setObjectName("" if busy else "primary")
            self.action.style().unpolish(self.action)  # the style goes by the name
            self.action.style().polish(self.action)
        self.action.setVisible(busy or not installed)
        self.remove.setVisible(on_disk and not busy and not item.required)
        if busy:
            return
        self.bar.hide()
        if installed and blocked:
            text, colour = f"已下载 · {blocked}", WARN
        elif installed:
            text, colour = f"已安装 · {size_text(item.disk_bytes(models))}", OK
            if not item.required and not enabled:
                text, colour = text.replace("已安装", "已停用"), OFF
            elif page.win.host.uses(item):
                text += " · 正在使用"
        elif blocked:
            text, colour = blocked, WARN
        else:
            need = size_text(item.download_bytes(models))
            text, colour = (f"未完成 · 还需下载约 {need}" if on_disk else f"未下载 · 约 {need}"), OFF
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour};")


def custom_problem(cfg) -> str:
    """What keeps the own caption model from being used; "" if nothing does."""
    c = cfg.translate.custom
    if c.backend == "openai":
        if not c.base_url:
            return "还没填服务地址"
        if not c.model:
            return "还没填模型名称"
        if is_online(c.base_url) and not c.api_key:
            return "在线服务需要 API Key"
    elif c.backend == "llama":
        if not c.model_file:
            return "还没选模型文件"
        if not (cfg.models_path / c.model_file).exists():
            return f"找不到 {c.model_file}"
    return ""


def custom_where(cfg) -> str:
    c = cfg.translate.custom
    if c.backend == "llama":
        return f"模型文件 {c.model_file}"
    host = urlparse(c.base_url).netloc or c.base_url
    return f"在线服务 {host}" if is_online(c.base_url) else f"本机服务 {host}"


class CustomCard(QFrame):
    """The player's own caption model: not downloaded, only set up."""

    def __init__(self, page: "ModelsPage"):
        super().__init__()
        self.page = page
        self.setObjectName("card")
        self.enable = QCheckBox("启用")
        self.enable.clicked.connect(page.toggle_custom)
        self.status = QLabel()
        top = QHBoxLayout()
        top.addWidget(label("自定义翻译模型", "title"))
        top.addStretch(1)
        top.addWidget(self.status)
        low = QHBoxLayout()
        low.addWidget(label("本机的 Ollama、LM Studio，自己的 .gguf 文件，或在线服务", "hint"), 1)
        low.addWidget(self.enable)
        low.addWidget(button("设置…", on_click=lambda: page.win.open("translate")))
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        box.addLayout(top)
        box.addWidget(label("用你自己选的模型翻译字幕。它出错、超时或拒绝翻译的句子，自动改用下面的 Hy-MT2。",
                            wrap=True))
        box.addLayout(low)

    def refresh(self) -> None:
        cfg = self.page.win.cfg
        on = bool(cfg.translate.custom.backend)
        self.enable.setChecked(on)
        problem = custom_problem(cfg) if on else ""
        if not on:
            text, colour = "未启用", OFF
        elif problem:
            text, colour = f"已启用 · {problem}", WARN
        else:
            text, colour = f"已启用 · {custom_where(cfg)}", OK
            if isinstance(getattr(self.page.win.host, "translator", None), CaptionWithFallback):
                text += " · 正在使用"
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour};")


class ModelsPage(QWidget):
    progress = Signal(str, object, object, float)  # item, bytes so far, total, bytes per second
    said = Signal(str, str)                        # item, a line of status
    done = Signal(str, str, bool)                  # item, error, cancelled

    def __init__(self, win: "SettingsWindow"):
        super().__init__()
        self.win = win
        self.jobs: dict[str, threading.Event] = {}
        self._vad = threading.Lock()  # two downloads must not both fetch the voice detector
        self.cards = {item.id: ModelCard(self, item) for item in catalog.ITEMS.values()}
        self.custom = CustomCard(self)

        self.mirror = QComboBox()
        for value, text in MIRRORS:
            self.mirror.addItem(text, value)
        self.mirror.setCurrentIndex(max(0, self.mirror.findData(state.load().get("mirror", "auto"))))
        self.mirror.currentIndexChanged.connect(
            lambda _i: state.save(mirror=self.mirror.currentData()))
        source = QHBoxLayout()
        source.addWidget(label("下载源"))
        source.addWidget(self.mirror)
        source.addStretch(1)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        self.banner_text = label("", wrap=True)
        self.banner_button = button("", "primary", self.download_recommended)
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(16, 12, 16, 12)
        row.addWidget(self.banner_text, 1)
        row.addWidget(self.banner_button)

        self.asr = QVBoxLayout()
        self.asr.setSpacing(10)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addWidget(label(catalog.advice(), "hint", wrap=True))
        box.addLayout(source)
        box.addWidget(self.banner)
        box.addWidget(label("语音识别", "h2"))
        box.addWidget(label("从上到下依次尝试，使用第一个能用的；用 ▲ ▼ 调整顺序，取消「启用」的模型不会被使用。",
                            "hint", wrap=True))
        box.addLayout(self.asr)
        box.addWidget(label("字幕翻译", "h2"))
        box.addWidget(label("从上到下：先用自定义模型（如果启用），它翻译不了的句子交给 Hy-MT2。",
                            "hint", wrap=True))
        box.addWidget(self.custom)
        box.addWidget(self.cards["hy-mt2"])
        box.addWidget(label("基础组件", "h2"))
        box.addWidget(self.cards["vad"])
        box.addStretch(1)

        # Emitted by download threads; these methods run on the window's thread.
        self.progress.connect(self._on_progress)
        self.said.connect(self._on_said)
        self.done.connect(self._on_done)
        self.refresh()

    def _on_progress(self, item_id: str, got: int, total: int, speed: float) -> None:
        self.cards[item_id].progress(got, total, speed)

    def _on_said(self, item_id: str, line: str) -> None:
        self.cards[item_id].say(progress_line(line))

    # -- what is switched on, and in which order --------------------------------

    def order(self) -> list[str]:
        asr = self.win.cfg.asr
        return list(asr.order) if asr.backend == "auto" else [asr.backend]

    def enabled(self, item: catalog.Item) -> bool:
        if item.role == "asr":
            return item.id in self.order()
        if item.role == "translate":
            return self.win.cfg.translate.backend == "llama"
        return True

    def _set_order(self, order: list[str]) -> None:
        self.win.set("asr.backend", "auto")
        self.win.set("asr.order", order)
        self.refresh()

    def toggle(self, item: catalog.Item, on: bool) -> None:
        if item.role == "translate":
            self.win.set("translate.backend", "llama" if on else "none")
            self.refresh()
            return
        order = self.order()
        if on and item.id not in order:
            order.append(item.id)
        elif not on and item.id in order:
            if len(order) == 1:
                self.cards[item.id].say("至少要保留一个语音识别模型。", WARN)
                self.refresh()
                return
            order.remove(item.id)
        self._set_order(order)

    def toggle_custom(self, on: bool) -> None:
        kind = state.load().get("custom_kind", "openai") if on else ""
        self.win.set("translate.custom.backend", kind)
        if kind == "llama" and is_online(self.win.cfg.translate.custom.base_url):
            self.win.set("translate.custom.base_url", CUSTOM_PORT)
        self.win._build_pages()  # the translation page shows the new choice
        self.refresh()
        if on:
            self.win.open("translate")  # an address or a file still has to be filled in

    def move(self, item: catalog.Item, by: int) -> None:
        order = self.order()
        if item.id in order:
            i = order.index(item.id)
            j = i + by
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
                self._set_order(order)

    # -- downloading and removing -------------------------------------------------

    def act(self, item: catalog.Item) -> None:
        if item.id in self.jobs:
            self.jobs[item.id].set()  # cancel; the worker reports back
            self.cards[item.id].say("正在取消…")
        else:
            self.start(item)

    def start(self, item: catalog.Item) -> None:
        if item.id in self.jobs:
            return
        cancel = self.jobs[item.id] = threading.Event()
        models, mirror = self.win.cfg.models_path, self.mirror.currentData()
        card = self.cards[item.id]
        card.say("准备下载…")
        card.progress(0, 0, 0.0)

        def work() -> None:
            try:
                models.mkdir(parents=True, exist_ok=True)
                with self._vad:  # every recogniser needs it, and it is tiny
                    if item is not catalog.VAD:
                        catalog.VAD.install(models, mirror, lambda _line: None, None, cancel)
                item.install(models, mirror, lambda line: self.said.emit(item.id, line),
                             lambda *p: self.progress.emit(item.id, *p), cancel)
                self.done.emit(item.id, "", False)
            except Cancelled:
                self.done.emit(item.id, "", True)
            except Exception as e:
                self.done.emit(item.id, str(e) or type(e).__name__, False)

        threading.Thread(target=work, name=f"download-{item.id}", daemon=True).start()
        self.refresh()

    def download_recommended(self) -> None:
        models = self.win.cfg.models_path
        for item in dict.fromkeys([*catalog.missing(self.win.cfg), *catalog.recommended()]):
            if not item.installed(models):
                self.start(item)

    def _on_done(self, item_id: str, error: str, cancelled: bool) -> None:
        self.jobs.pop(item_id, None)
        card, host = self.cards[item_id], self.win.host
        if cancelled:
            card.say("已取消。已下载的部分会保留，下次接着下载。")
        elif error:
            card.say(f"下载失败：{error}", WARN)
        else:
            card.say("")
            if catalog.ITEMS[item_id].role == "asr" and host.pipeline is not None:
                host.pending.add("asr")  # a better recogniser may be available now
        self.refresh()
        if not self.jobs and host.pipeline is None and not host.busy:
            host.resume()  # starts captions once nothing is missing

    def remove(self, item: catalog.Item) -> None:
        host, models = self.win.host, self.win.cfg.models_path
        if host.busy:
            self.cards[item.id].say("正在加载模型，请稍后再试。", WARN)
            return
        freed = size_text(item.disk_bytes(models))
        answer = QMessageBox.question(
            self, "删除模型", f"删除 {item.title}？\n\n将释放约 {freed} 磁盘空间，之后可以随时重新下载。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        in_use = host.uses(item)
        if in_use:
            host.halt()
        try:
            note, colour = item.remove(models), OFF
        except Exception as e:
            note, colour = f"没能完全删除：{e}", WARN
        self.cards[item.id].say(note, colour)
        self.refresh()
        if in_use:
            host.resume()

    def refresh(self) -> None:
        models = self.win.cfg.models_path
        recommended = catalog.recommended()
        for card in self.cards.values():
            card.refresh(card.item in recommended)
        self.custom.refresh()
        order = self.order()
        ranked = sorted(catalog.RECOGNIZERS,
                        key=lambda i: order.index(i.id) if i.id in order else len(order))
        for position, item in enumerate(ranked):
            card = self.cards[item.id]
            self.asr.removeWidget(card)
            self.asr.insertWidget(position, card)
            card.up.setEnabled(item.id in order and position > 0)
            card.down.setEnabled(item.id in order and position < len(order) - 1)
        lacking = catalog.missing(self.win.cfg)
        self.banner.setVisible(bool(lacking))
        if not lacking:
            return
        names = "、".join(i.title for i in lacking)
        if self.jobs:
            self.banner_text.setText(f"正在下载，完成后字幕会自动启动。还缺少：{names}")
            self.banner_button.hide()
            return
        todo = [i for i in dict.fromkeys([*lacking, *recommended]) if not i.installed(models)]
        total = sum(i.download_bytes(models) for i in todo)
        self.banner_text.setText(f"还缺少运行需要的模型：{names}。下载完成后字幕会自动启动，之后可以离线使用。")
        self.banner_button.setText(f"一键下载推荐配置（约 {size_text(total)}）")
        self.banner_button.show()


# -- the window -----------------------------------------------------------------


class Form(QWidget):
    """Rows of "name, control", each with an optional line of explanation."""

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnMinimumWidth(0, 130)
        self.grid.setColumnStretch(1, 1)
        self._row = 0

    def add(self, name: str, control, hint: str = "", wide: bool = False) -> None:
        self.grid.addWidget(label(name), self._row, 0, Qt.AlignmentFlag.AlignTop)
        if isinstance(control, QWidget):
            if wide:
                self.grid.addWidget(control, self._row, 1)
            else:
                self.grid.addWidget(control, self._row, 1, Qt.AlignmentFlag.AlignLeft)
        else:
            control.setSpacing(8)
            self.grid.addLayout(control, self._row, 1)
        self.grid.setRowMinimumHeight(self._row, 36)
        if hint:
            self.grid.addWidget(label(hint, "hint", wrap=True), self._row + 1, 1)
        self.grid.setRowMinimumHeight(self._row + 2, 8)
        self._row += 3


class SettingsWindow(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host, self.cfg = host, host.cfg
        self.setObjectName("settings")
        self.setWindowTitle("Valoroyce 设置")
        self.setStyleSheet(STYLE.replace("CHECK_MARK", check_mark()))
        self.resize(940, 680)
        self.setMinimumSize(780, 520)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(150)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.addItems([title for _key, title in PAGES])
        self.stack = QStackedWidget()
        self.models = ModelsPage(self)
        self._build_pages()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.bar = QFrame()
        self.bar.setObjectName("bar")
        self.bar_text = label("")
        self.apply = button("应用更改", "primary", self.host.apply_pending)
        low = QHBoxLayout(self.bar)
        low.setContentsMargins(20, 10, 20, 10)
        low.addWidget(self.bar_text, 1)
        low.addWidget(self.apply)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addLayout(body, 1)
        box.addWidget(self.bar)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._using = None
        self._tick()

    def open(self, page: str = "") -> None:
        if not self.isVisible():
            self._build_pages()  # the tray menu may have changed things meanwhile
            self.models.refresh()
        keys = [key for key, _title in PAGES]
        if page in keys:
            self.nav.setCurrentRow(keys.index(page))
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self.host.apply_pending()
        super().closeEvent(event)

    def _tick(self) -> None:
        host = self.host
        waiting = bool(host.pending) and host.pipeline is not None
        self.bar.setVisible(waiting or host.busy)
        self.apply.setVisible(waiting)
        self.apply.setEnabled(not host.busy)
        self.bar_text.setText("正在加载模型…" if host.busy
                              else "有更改需要重新加载模型才会生效（几秒钟）。关闭这个窗口时也会自动应用。")
        using = (getattr(host.recognizer, "id", ""), host.busy)
        if using != self._using:  # show which recogniser is in use now
            self._using = using
            self.models.refresh()

    # -- reading and changing settings --------------------------------------------

    def value(self, name: str):
        obj = self.cfg
        for part in name.split("."):
            obj = getattr(obj, part)
        return obj

    def set(self, name: str, value) -> None:
        if self.value(name) != value and state.assign(self.cfg, name, value):
            state.put(name, value)
            self.host.setting_changed(name)
            if name.startswith("translate.custom."):
                self.models.custom.refresh()
            self._tick()

    def _spin(self, name: str, low, high, step=1, suffix: str = ""):
        current = self.value(name)
        w = QDoubleSpinBox() if isinstance(current, float) else QSpinBox()
        if isinstance(current, float):
            w.setDecimals(2)
        w.setRange(low, high)
        w.setSingleStep(step)
        w.setValue(current)
        w.setSuffix(suffix)
        w.setMinimumWidth(110)
        w.valueChanged.connect(lambda v: self.set(name, type(current)(v)))
        return w

    def _slider(self, name: str, low: int, high: int) -> QSlider:
        """A share between 0 and 1, shown in percent."""
        w = QSlider(Qt.Orientation.Horizontal)
        w.setRange(low, high)
        w.setValue(round(self.value(name) * 100))
        w.setMinimumWidth(260)
        w.valueChanged.connect(lambda v: self.set(name, v / 100))
        return w

    def _check(self, name: str, text: str) -> QCheckBox:
        w = QCheckBox(text)
        w.setChecked(self.value(name))
        w.toggled.connect(lambda on: self.set(name, on))
        return w

    def _line(self, name: str, placeholder: str = "", secret: bool = False) -> QLineEdit:
        w = QLineEdit(self.value(name))
        w.setPlaceholderText(placeholder)
        if secret:
            w.setEchoMode(QLineEdit.EchoMode.Password)
        w.editingFinished.connect(lambda: self.set(name, w.text().strip()))
        return w

    def _combo(self, name: str, choices: list, on_change=None) -> QComboBox:
        """`choices` are (value, text); the current value is added if it is not among them."""
        current = self.value(name)
        values = [value for value, _text in choices]
        w = QComboBox()
        w.addItems([text for _value, text in choices])
        if current not in values:
            values.append(current)
            w.addItem(f"{current}（来自 config.toml）")
        w.setCurrentIndex(values.index(current))
        w.setMinimumWidth(260)

        def changed(i: int) -> None:
            self.set(name, values[i])
            if on_change:
                on_change()

        w.currentIndexChanged.connect(changed)
        return w

    # -- pages -------------------------------------------------------------------

    def _build_pages(self) -> None:
        row = max(self.stack.currentIndex(), 0)
        self.models.setParent(None)  # it is kept (downloads may be running); its old page goes
        while self.stack.count():
            old = self.stack.widget(0)
            self.stack.removeWidget(old)
            old.deleteLater()
        pages = {"models": ("模型", self.models), "captions": ("字幕", self._captions()),
                 "sound": ("声音与识别", self._sound()), "translate": ("翻译", self._translate()),
                 "compose": ("发消息", self._compose()), "about": ("关于", self._about())}
        for key, _title in PAGES:
            title, content = pages[key]
            inner = QWidget()
            box = QVBoxLayout(inner)
            box.setContentsMargins(28, 22, 28, 22)
            box.setSpacing(12)
            box.addWidget(label(title, "h1"))
            box.addWidget(content)
            box.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(inner)
            self.stack.addWidget(scroll)
        self.models.show()  # taking it out of its old page hid it
        self.stack.setCurrentIndex(row)

    def _captions(self) -> QWidget:
        host, form = self.host, Form()
        form.add("字号", self._spin("overlay.font_size", 12, 48))
        form.add("最多显示行数", self._spin("overlay.max_lines", 1, 6),
                 "行数越多，字幕条越往上延伸，越容易挡住准星。")
        form.add("整句停留时间", self._spin("overlay.linger", 2.0, 20.0, 0.5, " 秒"))
        form.add("背景不透明度", self._slider("overlay.opacity", 0, 100))
        form.add("字幕条宽度", self._slider("overlay.width", 30, 90), "占屏幕宽度的比例。")
        form.add("原文", self._check("overlay.show_source", "在中文上方同时显示识别出的日语 / 英语"))
        move = button("拖动调整位置")
        move.setCheckable(True)
        move.setChecked(host.move.isChecked())
        move.toggled.connect(host.move.setChecked)
        host.move.toggled.connect(move.setChecked)
        row = QHBoxLayout()
        row.addWidget(move)
        row.addWidget(button("恢复默认位置", on_click=host.reset_position))
        row.addStretch(1)
        form.add("位置", row, "按下「拖动调整位置」后字幕条变成蓝色框，拖到合适的地方，再点一次按钮固定。")
        return form

    def _sound(self) -> QWidget:
        form, asr = Form(), self.cfg.asr
        devices = QComboBox()
        devices.setMinimumWidth(360)

        def fill() -> None:
            devices.blockSignals(True)
            devices.clear()
            devices.addItem("跟随系统默认设备", "")
            try:
                from .audio import list_devices

                names = [name for kind, name, _default in list_devices() if kind == "loopback"]
            except Exception as e:
                names = []
                devices.setToolTip(f"无法列出设备：{e}")
            current = self.cfg.audio.device
            for name in names:
                devices.addItem(name, name)
            match = next((n for n in names if current and current.lower() in n.lower()), "")
            if current and not match:
                devices.addItem(f"{current}（未连接）", current)
            devices.setCurrentIndex(devices.findData(match or current))
            devices.blockSignals(False)

        fill()
        devices.currentIndexChanged.connect(lambda _i: self.set("audio.device", devices.currentData()))
        row = QHBoxLayout()
        row.addWidget(devices)
        row.addWidget(button("刷新", on_click=fill))
        row.addStretch(1)
        form.add("声音来源", row,
                 "默认听电脑播放的全部声音，枪声和英雄台词也会混进来。把游戏里的语音聊天单独输出到一个设备，"
                 "再在这里选中它，字幕就只跟队友的声音走。")
        self._programs(form, devices)
        form.add("识别语言", self._combo("asr.languages", LANGUAGES))
        live = QCheckBox("说话过程中就显示字幕（关闭后只在一句话说完时显示）")
        live.setChecked(asr.partial_interval > 0)
        usual = asr.partial_interval or 1.0
        live.toggled.connect(lambda on: self.set("asr.partial_interval", usual if on else 0.0))
        form.add("实时字幕", live)
        form.add("断句停顿", self._spin("vad.min_silence", 0.2, 1.0, 0.05, " 秒"),
                 "安静多久算一句话说完。越短字幕出得越快，但更容易把一句话切碎。")
        return form

    def _programs(self, form, devices: QComboBox) -> None:
        """Which programs are listened to: all, one, or all but one."""
        audio = self.cfg.audio
        mode = QComboBox()
        for text, value in (("全部程序", ""), ("只听一个程序", "only"), ("除一个程序以外的全部程序", "except")):
            mode.addItem(text, value)
        mode.setCurrentIndex(mode.findData(audio.app_mode if audio.app else ""))
        mode.setMinimumWidth(260)
        apps = QComboBox()
        apps.setEditable(True)  # a program that is not making sound right now can be typed
        apps.setMinimumWidth(360)
        apps.lineEdit().setPlaceholderText("程序文件名，例如 KOOK.exe")

        def fill() -> None:
            from . import apploop

            apps.blockSignals(True)
            apps.clear()
            names = []
            try:
                names = [name for name, _now in apploop.playing()]
            except Exception as e:
                apps.setToolTip(f"无法列出正在发声的程序：{e}")
            for name in names:
                apps.addItem(apploop.label(name), name)
            for name in (audio.app, "RiotClientServices.exe"):
                if name and name.lower() not in (n.lower() for n in names):
                    apps.addItem(f"{apploop.label(name)} · 现在没有声音", name)
                    names.append(name)
            index = apps.findData(audio.app) if audio.app else -1
            apps.setCurrentIndex(index)
            if index < 0:
                apps.setEditText(audio.app)
            apps.blockSignals(False)

        def chosen() -> str:
            i = apps.currentIndex()
            if i >= 0 and apps.itemText(i) == apps.currentText():
                return apps.itemData(i)
            return apps.currentText().strip()

        def apply() -> None:
            how, name = mode.currentData(), chosen()
            if how and name:
                self.set("audio.app_mode", how)
                self.set("audio.app", name)
            elif not how:
                self.set("audio.app", "")
            apps.setEnabled(bool(how) and not self.host.from_file)
            devices.setEnabled(not how and not self.host.from_file)

        fill()
        mode.currentIndexChanged.connect(lambda _i: apply())
        apps.activated.connect(lambda _i: apply())
        apps.lineEdit().editingFinished.connect(apply)
        mode.setEnabled(not self.host.from_file)
        apps.setEnabled(bool(mode.currentData()) and not self.host.from_file)
        devices.setEnabled(not mode.currentData() and not self.host.from_file)
        form.add("听哪些程序", mode,
                 "如果你和朋友另外开了语音通话（QQ、KOOK、Discord 等）用中文聊天，选「除一个程序以外」再选中通话软件，"
                 "字幕就不会去翻译你们的聊天。只听某个程序时，上面的声音来源不起作用。")
        row = QHBoxLayout()
        row.addWidget(apps)
        row.addWidget(button("刷新", on_click=fill))
        row.addStretch(1)
        form.add("程序", row,
                 "列表里是现在打开了声音的程序，先让它出声再点「刷新」。程序没在运行时，字幕会等它启动。"
                 "VALORANT 的队伍语音据说由 Riot 客户端（RiotClientServices.exe）播放，还没有在对局里验证过。")

    def _translate(self) -> QWidget:
        form, backend = Form(), self.cfg.translate.backend
        if backend != "llama":
            form.add("当前状态", label(
                "字幕翻译已停用：只替换术语，其余显示原文。在「模型」页启用翻译模型。" if backend == "none"
                else "字幕翻译使用 config.toml 里设置的自定义服务，下面的选项对它不起作用。", wrap=True),
                wide=True)
        form.add("翻译模型运行在",
                 self._combo("translate.gpu", [(True, "显卡（更快，约占 1.5 GB 显存）"),
                                               (False, "CPU（不占显卡，短句约 1 秒）")]),
                 "开着字幕时游戏掉帧或卡顿，就改成 CPU。")
        form.add("未说完的句子", self._check("translate.partials", "说话过程中的半句字幕也翻译"),
                 "CPU 吃紧时可以关掉，只翻译说完的整句。")
        form.add("CPU 线程数", self._spin("translate.threads", 1, 32), "只在翻译模型运行在 CPU 时有影响。")
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        box.addWidget(form)
        box.addWidget(label("自定义翻译模型", "h2"))
        box.addWidget(label("启用后字幕先交给它翻译；它出错、超时或拒绝翻译的句子由 Hy-MT2 翻译"
                            "（Hy-MT2 停用时只替换术语）。", "hint", wrap=True))
        box.addWidget(self._custom())
        return page

    def _custom(self) -> QWidget:
        form, c = Form(), self.cfg.translate.custom
        kind = self._combo("translate.custom.backend", CUSTOM_KINDS, lambda: kind_changed())
        form.add("类型", kind)

        presets = QHBoxLayout()
        address = self._line("translate.custom.base_url", "http://127.0.0.1:11434")
        model = self._line("translate.custom.model", "例如 qwen2.5:7b、deepseek-flash")
        key = self._line("translate.custom.api_key", "本机服务一般不需要", secret=True)
        thinking = QCheckBox("关闭模型的「思考」阶段（DeepSeek、Qwen3 等需要，否则又慢又可能返回空）")
        thinking.setChecked(c.extra_body.get("thinking") == NO_THINKING)

        def set_thinking(on: bool) -> None:
            extra = {k: v for k, v in c.extra_body.items() if k != "thinking"}
            self.set("translate.custom.extra_body", {**extra, "thinking": NO_THINKING} if on else extra)

        thinking.toggled.connect(set_thinking)

        def preset(url: str, name) -> None:
            self.set("translate.custom.base_url", url)
            address.setText(url)
            if name:
                self.set("translate.custom.model", name)
                model.setText(name)
                thinking.setChecked(True)
            changed()

        service = [address, model, key, thinking]  # used only with a server at an address
        for text, url, name in CUSTOM_PRESETS:
            service.append(button(text, on_click=lambda u=url, n=name: preset(u, n)))
            presets.addWidget(service[-1])
        presets.addStretch(1)

        files = QComboBox()
        files.setMinimumWidth(260)
        models = self.cfg.models_path
        found = (sorted(f.name for f in models.glob("*.gguf") if f.name != CAPTION_MODEL)
                 if models.is_dir() else [])
        if c.model_file and c.model_file not in found:
            found.append(c.model_file)
        files.addItem("（选择文件）", "")
        for name in found:
            files.addItem(name, name)
        files.setCurrentIndex(max(0, files.findData(c.model_file)))
        files.currentIndexChanged.connect(
            lambda _i: (self.set("translate.custom.model_file", files.currentData()), changed()))
        prompt = self._combo("translate.custom.prompt", [("generic", "通用聊天模型"),
                                                         ("hy-mt", "Hy-MT 系列翻译模型")])
        timeout = self._spin("translate.custom.timeout", 0.5, 10.0, 0.5, " 秒")
        partials = self._check("translate.custom.partials", "说话过程中的半句字幕也用它翻译")
        risk = label(ONLINE_RISK, wrap=True)
        risk.setStyleSheet(f"color: {WARN};")
        problem = label("", wrap=True)
        problem.setStyleSheet(f"color: {WARN};")

        form.add("", presets)
        form.add("服务地址", address, wide=True)
        form.grid.addWidget(risk, form._row - 2, 1)
        form.add("模型名称", model, wide=True)
        form.add("API Key", key, "保存在本机的 valoroyce.state.json 里，别把这个文件发给别人。", wide=True)
        form.add("", thinking)
        form.add("模型文件", files, "把 .gguf 文件放进模型文件夹（「关于」页可以打开它），再重新打开这个窗口。"
                 "它和 Hy-MT2 各占一份显存。")
        form.add("提示词格式", prompt)
        form.add("超时", timeout, "超过这个时间还没译完，这一句改用 Hy-MT2。连续出错时暂停使用它 30 秒。"
                 "实测 DeepSeek 每句约 0.8 到 1.6 秒，Hy-MT2 约 0.7 秒。")
        form.add("半句字幕", partials, "关闭时，说话过程中的半句仍由 Hy-MT2 翻译，说完的整句才发给它。"
                 "打开后请求会多好几倍，在线服务的费用也会增加。")
        form.add("", problem, wide=True)

        def kind_changed() -> None:
            if c.backend:
                state.save(custom_kind=c.backend)  # what "启用" on the models page brings back
            if c.backend == "llama":  # served by us, on a port of its own
                self.set("translate.custom.base_url", CUSTOM_PORT)
                address.setText(CUSTOM_PORT)
            elif c.backend == "openai" and c.base_url == CUSTOM_PORT:
                self.set("translate.custom.base_url", CUSTOM_PRESETS[0][1])
                address.setText(c.base_url)
            changed()

        def changed() -> None:
            k = c.backend
            for w in service:
                w.setEnabled(k == "openai")
            files.setEnabled(k == "llama")
            for w in (prompt, timeout, partials):
                w.setEnabled(bool(k))
            risk.setVisible(k == "openai" and is_online(c.base_url))
            trouble = custom_problem(self.cfg) if k else ""
            problem.setText(f"还不能使用：{trouble}" if trouble else "")
            problem.setVisible(bool(trouble))
            self.models.refresh()

        for w in (address, model, key):
            w.editingFinished.connect(changed)
        changed()
        return form

    def _compose(self) -> QWidget:
        form, c = Form(), self.cfg.compose
        form.add("翻译成", self._combo("compose.target", [("ja", "日语"), ("en", "English")]))
        keys = QLineEdit(c.hotkey)
        keys.setPlaceholderText("例如 ctrl+alt+t；留空则不注册")
        keys_hint = label("", "hint", wrap=True)

        def set_keys() -> None:
            text = keys.text().strip().lower()
            try:
                if text:
                    hotkey.parse(text)
            except ValueError:
                keys_hint.setText("没看懂这个快捷键。写法：ctrl+alt+t、ctrl+shift+f8（至少一个 ctrl / alt / shift / win 加一个键）。")
                keys_hint.setStyleSheet(f"color: {WARN};")
                return
            keys_hint.setText("")
            self.set("compose.hotkey", text)

        keys.editingFinished.connect(set_keys)
        form.add("打开输入框的快捷键", keys)
        form.grid.addWidget(keys_hint, form._row - 2, 1)

        online = [self._line("compose.base_url", "https://api.deepseek.com"),
                  self._line("compose.model", "deepseek-flash"),
                  self._line("compose.api_key", "sk-…", secret=True)]
        thinking = QCheckBox("关闭模型的「思考」阶段（DeepSeek 等需要，否则又慢又可能返回空）")
        thinking.setChecked(c.extra_body.get("thinking") == NO_THINKING)

        def set_thinking(on: bool) -> None:
            extra = {k: v for k, v in c.extra_body.items() if k != "thinking"}
            self.set("compose.extra_body", {**extra, "thinking": NO_THINKING} if on else extra)

        thinking.toggled.connect(set_thinking)

        def deepseek() -> None:
            for name, value in DEEPSEEK.items():
                self.set(name, value)
            online[0].setText(c.base_url)
            online[1].setText(c.model)
            thinking.setChecked(True)

        preset = button("填入 DeepSeek 的设置", on_click=deepseek)
        choices = [("", "本地字幕模型（离线，不需要设置）"), ("openai", "在线服务（兼容 OpenAI 接口）")]
        if c.backend == "llama":
            choices.append(("llama", "本地的第二个模型（在 config.toml 里设置）"))

        def enable() -> None:
            for w in (*online, thinking, preset):
                w.setEnabled(c.backend == "openai")

        form.add("用哪个模型翻译", self._combo("compose.backend", choices, enable),
                 "在线服务翻得更地道。只有你在输入框里打的那句话会发给它；队友的语音和字幕始终在本机处理。"
                 "服务拒绝翻译、断网或 key 失效时，这一句会自动改用本地模型。")
        form.add("服务地址", online[0], wide=True)
        form.add("模型名称", online[1], wide=True)
        form.add("API Key", online[2], "保存在本机的 valoroyce.state.json 里，别把这个文件发给别人。", wide=True)
        form.add("", thinking)
        form.add("", preset)
        words = QLineEdit("、".join(c.local_only))
        words.editingFinished.connect(lambda: self.set("compose.local_only", [
            w.strip() for w in words.text().replace(",", "、").replace("，", "、").split("、")
            if w.strip()]))
        form.add("只在本机翻译的词", words, "包含这些词的句子不会发给在线服务，直接由本地模型翻译。用顿号或逗号分隔。",
                 wide=True)
        enable()
        return form

    def _about(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        try:
            version = metadata.version("valoroyce")
        except metadata.PackageNotFoundError:
            version = ""
        link = label(f"Valoroyce {version} · 无畏契约队内语音实时字幕 · "
                     '<a style="color:#FF7480" href="https://github.com/Aminorsh/valoroyce">GitHub</a>')
        link.setOpenExternalLinks(True)
        box.addWidget(link)
        box.addWidget(label(
            "字幕来自电脑播放的声音，显示在一个普通的置顶窗口里；程序不读取、不修改游戏。"
            "模型下载完成后，字幕可以完全离线使用。", "hint", wrap=True))
        box.addWidget(label(
            "这里改过的设置保存在 valoroyce.state.json，并且优先于 config.toml 里手写的配置；"
            "config.toml 本身不会被改动。", "hint", wrap=True))

        def reveal(path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(path.resolve())

        def reset() -> None:
            answer = QMessageBox.question(self, "恢复默认设置",
                                          "把这里改过的设置全部恢复为默认值（或 config.toml 里的值）？\n\n重启程序后生效。")
            if answer == QMessageBox.StandardButton.Yes:
                state.forget()

        row = QHBoxLayout()
        row.addWidget(button("打开模型文件夹", on_click=lambda: reveal(self.cfg.models_path)))
        row.addWidget(button("打开录音文件夹", on_click=lambda: reveal(Path("recordings"))))
        row.addWidget(button("恢复默认设置", "danger", reset))
        row.addStretch(1)
        box.addLayout(row)
        return page
