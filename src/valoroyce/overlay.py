"""The desktop app: a caption strip plus a system-tray icon to control it.

The strip is an ordinary transparent, always-on-top, click-through window. It
does not hook, inject into or read the game, which also means the game must
run in "Windowed Fullscreen" (exclusive fullscreen hides every other window).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from importlib import resources

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import state
from .audio import list_devices, open_source
from .compose import ComposeWindow
from .config import Config, OverlayConfig
from .glossary import Glossary
from .hotkey import GlobalHotkey
from .pipeline import Caption, Pipeline
from .speech import make_recognizer
from .record import Recording, describe
from .rows import arrange, newest
from .translate import make_compose_translator, make_translator

PAD_X, PAD_Y, GAP = 14, 6, 6
FONT_SIZES = [("小", 16), ("中", 22), ("大", 28), ("特大", 36)]


def app_icon() -> QIcon:
    pix = QPixmap()
    pix.loadFromData(resources.files("valoroyce.data").joinpath("icon.png").read_bytes())
    return QIcon(pix)


@dataclass
class _Line:
    utt: int
    text: str
    source: str
    final: bool
    expires: float  # monotonic; partial lines get a generous value and are replaced


class CaptionWindow(QWidget):
    caption = Signal(object)  # Caption, emitted from the translator thread

    def __init__(self, cfg: OverlayConfig):
        super().__init__()
        self.cfg = cfg
        self.lines: list[_Line] = []
        self.moving = False
        self.paused = False
        self._drag: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._apply_flags()
        self.set_font_size(cfg.font_size)
        self.caption.connect(self.on_caption)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._expire)
        self._timer.start(250)

    # -- window plumbing -----------------------------------------------------

    def _apply_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if not self.moving:
            flags |= Qt.WindowType.WindowTransparentForInput  # clicks go to the game
        self.setWindowFlags(flags)

    def set_font_size(self, size: int) -> None:
        self.cfg.font_size = size
        self.font_main = QFont(self.cfg.font_family, size)
        self.font_main.setBold(True)
        self.font_src = QFont(self.cfg.font_family, max(8, int(size * 0.6)))
        self._place()
        self.update()

    def _place(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        width = int(screen.width() * self.cfg.width)
        # Room for max_lines rows of captions, each with a row of the original
        # and a box of its own: the tallest the strip can ever be.
        line_h = QFontMetrics(self.font_main).height() + QFontMetrics(self.font_src).height()
        height = (line_h + 2 * PAD_Y + GAP) * self.cfg.max_lines
        x = screen.x() + int(screen.width() * self.cfg.x) - width // 2
        bottom = screen.y() + int(screen.height() * self.cfg.y)
        saved = state.load()
        if isinstance(saved.get("x"), int) and isinstance(saved.get("bottom"), int):
            x, bottom = saved["x"], saved["bottom"]
        self.setGeometry(x, bottom - height, width, height)

    def set_moving(self, on: bool) -> None:
        self.moving = on
        self._apply_flags()
        self.show()  # changing flags hides the window
        self.update()

    def mousePressEvent(self, e) -> None:
        if self.moving:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self.moving and self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        if self.moving:
            self._drag = None
            geo = self.geometry()
            state.save(x=geo.x(), bottom=geo.y() + geo.height())

    # -- captions --------------------------------------------------------------

    def on_caption(self, c: Caption) -> None:
        if self.paused:
            return
        now = time.monotonic()
        line = _Line(c.utt, c.text, c.source, c.final, now + (self.cfg.linger if c.final else 15.0))
        for i, old in enumerate(self.lines):
            if old.utt == c.utt:
                self.lines[i] = line
                break
        else:
            self.lines.append(line)
        del self.lines[:-self.cfg.max_lines]
        self.update()

    def notice(self, text: str, seconds: float = 5.0) -> None:
        """A message from the app itself; replaces everything on screen."""
        self.lines = [_Line(-1, text, "", True, time.monotonic() + seconds)]
        self.update()

    def _expire(self) -> None:
        now = time.monotonic()
        kept = [ln for ln in self.lines if ln.expires > now]
        if len(kept) != len(self.lines):
            self.lines = kept
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.moving:
            p.fillRect(self.rect(), QColor(80, 160, 255, 70))
            p.setPen(QColor(255, 255, 255))
            p.setFont(self.font_src)
            p.drawText(self.rect().adjusted(8, 4, -8, -4),
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                       "拖动以移动字幕位置，完成后在托盘菜单取消“调整位置”")
        centre = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        max_w = self.width() - 2 * PAD_X
        fm_main, fm_src = QFontMetrics(self.font_main), QFontMetrics(self.font_src)
        # A fixed number of rows: the strip never grows towards the crosshair.
        shown = arrange([ln.text for ln in self.lines], fm_main.horizontalAdvance, max_w,
                        self.cfg.max_lines)
        y = self.height()
        for line, main_rows in zip(reversed(self.lines), reversed(shown)):  # newest at the bottom
            if not main_rows:
                continue
            src_rows = []
            if self.cfg.show_source and line.source:
                # The original gets as many rows as its caption, and shows its end too.
                src_rows = newest(line.source, fm_src.horizontalAdvance, max_w, len(main_rows))
            widest = max([fm_main.horizontalAdvance(r) for r in main_rows]
                         + [fm_src.horizontalAdvance(r) for r in src_rows])
            box_w = widest + 2 * PAD_X
            box_h = len(main_rows) * fm_main.height() + len(src_rows) * fm_src.height() + 2 * PAD_Y
            y -= box_h
            box = QRectF((self.width() - box_w) / 2, y, box_w, box_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(255 * self.cfg.opacity)))
            p.drawRoundedRect(box, 6, 6)
            top = int(y + PAD_Y)
            p.setFont(self.font_src)
            p.setPen(QColor(200, 200, 200))
            for row in src_rows:
                p.drawText(QRect(0, top, self.width(), fm_src.height()), centre, row)
                top += fm_src.height()
            p.setFont(self.font_main)
            # Captions still being spoken are dimmer until they settle.
            p.setPen(QColor(255, 255, 255) if line.final else QColor(210, 210, 210))
            for row in main_rows:
                p.drawText(QRect(0, top, self.width(), fm_main.height()), centre, row)
                top += fm_main.height()
            y -= GAP
        p.end()


class Controller(QObject):
    """Owns the pipeline and the translator, and rebuilds them when a menu
    choice changes what they run on. Loading models takes seconds, so it
    happens on a worker thread and reports back through signals."""

    ready = Signal(str)   # name of the audio source now being captioned
    failed = Signal(str)  # why (re)starting did not work

    def __init__(self, app: QApplication, cfg: Config, glossary: Glossary, echo=None):
        super().__init__()
        self.app, self.cfg, self.glossary, self.echo = app, cfg, glossary, echo
        self.window = CaptionWindow(cfg.overlay)
        self.pipeline: Pipeline | None = None
        self.recognizer = None
        self.translator = None
        self.busy = False
        self.exit_code = 0
        self._reported: Pipeline | None = None  # pipeline whose error was already shown
        self._welcomed = False
        self.from_file = cfg.audio.source == "file"
        self.recording: Recording | None = None
        # Optional separate model for outgoing messages, loaded on first use.
        self._big = None
        self._big_wanted = bool(cfg.compose.backend)
        self._big_loading = False
        self.compose = ComposeWindow(self._compose_translator, cfg.compose.target)
        self.hotkey = None
        if cfg.compose.hotkey:
            try:
                self.hotkey = GlobalHotkey(app, cfg.compose.hotkey, self.open_compose)
            except Exception as e:  # a shortcut is a convenience; never block start-up
                print(f"warning: shortcut not available: {e}", file=sys.stderr)

        self.tray = QSystemTrayIcon(app_icon(), app)
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Valoroyce 实时字幕")
        self.tray.activated.connect(self._on_tray_click)

        self.ready.connect(self._on_ready)
        self.failed.connect(self._on_failed)
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._watch)
        self._watchdog.start(200)

    # -- menu ----------------------------------------------------------------

    def _build_menu(self) -> None:
        m, cfg = self.menu, self.cfg
        self.status = QAction("正在启动…", m, enabled=False)
        m.addAction(self.status)
        m.addSeparator()

        label = "发消息（中文 → 日语 / 英语）…"
        if self.hotkey and self.hotkey.registered:
            label += "\t" + self.cfg.compose.hotkey.title()
        send = QAction(label, m)
        send.triggered.connect(self.open_compose)
        m.addAction(send)
        m.addSeparator()

        self.pause = QAction("暂停字幕", m, checkable=True)
        self.pause.toggled.connect(self._set_paused)
        move = QAction("调整位置", m, checkable=True)
        move.toggled.connect(self.window.set_moving)
        show_src = QAction("显示原文", m, checkable=True, checked=cfg.overlay.show_source)
        show_src.toggled.connect(self._set_show_source)
        self.record = QAction("录音用于反馈问题（最长 10 分钟）", m, checkable=True)
        self.record.toggled.connect(self._set_recording)
        m.addActions([self.pause, move, show_src, self.record])

        sizes = m.addMenu("字号")
        group = QActionGroup(sizes)
        for label, size in FONT_SIZES:
            a = QAction(label, sizes, checkable=True, checked=size == cfg.overlay.font_size)
            a.triggered.connect(lambda _=False, s=size: self._set_font_size(s))
            group.addAction(a)
            sizes.addAction(a)

        # Menus whose choice restarts something; greyed out while that happens.
        self.restarting: list[QMenu] = []
        if cfg.translate.backend == "llama":
            run_on = m.addMenu("翻译运行在")
            group = QActionGroup(run_on)
            for label, gpu in (("显卡（更快）", True), ("CPU（不占用显卡）", False)):
                a = QAction(label, run_on, checkable=True, checked=cfg.translate.gpu == gpu)
                a.triggered.connect(lambda _=False, g=gpu: self._set_gpu(g))
                group.addAction(a)
                run_on.addAction(a)
            self.restarting.append(run_on)
        if not self.from_file:
            self.devices = m.addMenu("声音来源")
            self.devices.aboutToShow.connect(self._fill_devices)
            self.restarting.append(self.devices)

        m.addSeparator()
        quit_action = QAction("退出", m)
        quit_action.triggered.connect(self.app.quit)
        m.addAction(quit_action)

    def _fill_devices(self) -> None:
        """Rebuilt on every open: headsets get plugged in mid-session."""
        menu, current = self.devices, self.cfg.audio.device
        menu.clear()
        try:
            names = [name for kind, name, _ in list_devices() if kind == "loopback"]
        except Exception as e:
            menu.addAction(QAction(f"无法列出设备：{e}", menu, enabled=False))
            return
        default = QAction("跟随系统默认设备", menu, checkable=True, checked=not current)
        default.triggered.connect(lambda _=False: self._set_device(""))
        menu.addAction(default)
        menu.addSeparator()
        for name in names:
            chosen = bool(current) and current.lower() in name.lower()
            a = QAction(name, menu, checkable=True, checked=chosen)
            a.triggered.connect(lambda _=False, n=name: self._set_device(n))
            menu.addAction(a)

    def open_compose(self) -> None:
        self._compose_translator()  # start loading the message model, if any
        self.compose.open()

    def _compose_translator(self):
        """Translator for outgoing messages; None while their own model loads."""
        if not self._big_wanted:
            return self.translator
        if self._big is None and not self._big_loading:
            self._big_loading = True
            threading.Thread(target=self._load_big, daemon=True).start()
        return self._big

    def _load_big(self) -> None:
        try:
            self._big = make_compose_translator(self.cfg, self.glossary, self.cfg.models_path,
                                                fallback=lambda: self.translator)
        except Exception as e:
            # Messages still work, on the caption model.
            print(f"warning: message model not available ({e}); using the caption model",
                  file=sys.stderr, flush=True)
            self._big_wanted = False
        self._big_loading = False

    def _set_recording(self, on: bool) -> None:
        """Save what is heard and what is shown, for a problem report."""
        if on:
            try:
                self.recording = Recording(
                    header=describe(self.cfg, getattr(self, "source_name", "")))
            except OSError as e:
                self.window.notice(f"无法开始录音：{e}")
                self.record.setChecked(False)
                return
            if self.pipeline:
                self.pipeline.recording = self.recording
            self.window.notice("开始录音 · 在托盘菜单里再点一次停止")
        elif self.recording:
            done, self.recording = self.recording, None
            if self.pipeline:
                self.pipeline.recording = None
            done.close()
            folder = done.wav_path.parent.resolve()
            self.window.notice(f"录音已保存到 {folder}")
            self.tray.showMessage("录音已保存", f"{done.wav_path.name} 和 {done.log_path.name}\n{folder}",
                                  app_icon(), 8000)
            if hasattr(os, "startfile"):  # Windows: show the two files in Explorer
                try:
                    os.startfile(folder)
                except OSError:
                    pass

    def _on_tray_click(self, reason) -> None:
        # Left click opens the same menu as right click.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.popup(QCursor.pos())

    # -- menu actions ----------------------------------------------------------

    def _set_paused(self, on: bool) -> None:
        self.window.paused = on
        if self.pipeline:
            self.pipeline.paused = on
        self.window.notice("字幕已暂停" if on else "字幕已恢复", 3.0)
        self._refresh_status()

    def _set_show_source(self, on: bool) -> None:
        self.cfg.overlay.show_source = on
        state.save(show_source=on)
        self.window.update()

    def _set_font_size(self, size: int) -> None:
        state.save(font_size=size)
        self.window.set_font_size(size)
        self.window.notice("字幕大小预览", 3.0)

    def _set_gpu(self, gpu: bool) -> None:
        if gpu == self.cfg.translate.gpu:
            return
        self.cfg.translate.gpu = gpu
        state.save(gpu=gpu)
        self.start(new_translator=True, note="正在切换到" + ("显卡" if gpu else " CPU") + "…")

    def _set_device(self, name: str) -> None:
        if name == self.cfg.audio.device:
            return
        self.cfg.audio.device = name
        state.save(device=name)
        self.start(new_translator=False, note="正在切换声音来源…")

    # -- starting and restarting -------------------------------------------------

    def start(self, new_translator: bool = True, note: str = "正在加载模型，请稍候…") -> None:
        if self.busy:
            return
        self.busy = True
        for menu in self.restarting:
            menu.setEnabled(False)
        self.status.setText(note)
        self.window.notice(note, 120.0)
        threading.Thread(target=self._boot, args=(new_translator,), daemon=True).start()

    def _boot(self, new_translator: bool) -> None:
        try:
            if self.pipeline:
                self.pipeline.stop()
                self.pipeline.join(3.0)
                self.pipeline = None
            if new_translator and self.translator:
                self.translator.close()
                self.translator = None
            if self.translator is None:
                self.translator = make_translator(
                    self.cfg.translate, self.glossary, self.cfg.models_path
                )
            source = open_source(self.cfg.audio)
            if self.recognizer is None:  # loaded once: restarts keep it
                self.recognizer = make_recognizer(self.cfg.models_path, self.cfg.asr)
            pipeline = Pipeline(self.cfg, source, self.translator, self.glossary, self._emit,
                                recognizer=self.recognizer)
            pipeline.paused = self.window.paused
            pipeline.recording = self.recording  # a restart does not end a recording
            pipeline.start()
            self.pipeline = pipeline
            self.ready.emit(str(getattr(source, "name", self.cfg.audio.file)))
        except Exception as e:
            self.failed.emit(str(e))

    def _emit(self, c: Caption) -> None:
        if self.echo:
            self.echo(c)
        self.window.caption.emit(c)

    def _done_restarting(self) -> None:
        self.busy = False
        for menu in self.restarting:
            menu.setEnabled(True)

    def _on_ready(self, name: str) -> None:
        self._done_restarting()
        self.source_name = name
        self._refresh_status()
        self.window.notice(f"Valoroyce 已就绪 · 正在监听 {name} · 语音识别 {self.recognizer.label}")
        if not self._welcomed:
            self._welcomed = True
            self.tray.showMessage(
                "Valoroyce 正在运行",
                "点击这个图标可以调整字幕位置、切换声音来源或退出。",
                app_icon(), 6000,
            )

    def _on_failed(self, why: str) -> None:
        self._done_restarting()
        print(f"error: {why}", file=sys.stderr, flush=True)
        self.status.setText("未在运行（见提示）")
        self.window.notice(f"启动失败：{why}", 60.0)
        self.tray.showMessage("Valoroyce 启动失败", why, QSystemTrayIcon.MessageIcon.Warning, 10000)
        if self.from_file:
            self.exit_code = 1
            self.app.quit()

    def _refresh_status(self) -> None:
        name = getattr(self, "source_name", "")
        if name:
            self.status.setText(("已暂停 · " if self.window.paused else "正在监听 · ") + name)

    def _watch(self) -> None:
        if self.recording and self.recording.closed:  # it reached its time limit
            self.record.setChecked(False)
        p = self.pipeline
        if p is None or self.busy:
            return
        if p.error is not None and self._reported is not p:
            self._reported = p
            self._on_failed(str(p.error))
        elif self.from_file and p.done.is_set():
            self.app.quit()

    def shutdown(self) -> None:
        self.tray.hide()
        self.compose.hide()
        if self._big:
            self._big.close()
        if self.hotkey:
            self.hotkey.release()
        if self.pipeline:
            self.pipeline.stop()
        if self.recording:
            self.recording.close()
        if self.translator:
            self.translator.close()


def run_overlay(cfg: Config, glossary: Glossary, echo=None, record: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    controller = Controller(app, cfg, glossary, echo)

    # Let Ctrl+C in the console reach Python while Qt owns the main loop.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    if QSystemTrayIcon.isSystemTrayAvailable():
        controller.tray.show()
    else:
        print("no system tray on this desktop: press Ctrl+C here to quit", file=sys.stderr)

    controller.window.show()
    if record:
        controller.record.setChecked(True)
    controller.start()
    try:
        app.exec()
    finally:
        controller.shutdown()
    return controller.exit_code
