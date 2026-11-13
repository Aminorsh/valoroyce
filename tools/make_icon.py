"""Draw the app icon: a white caption bubble on a red tile.

Run from the repo root to regenerate src/valoroyce/data/icon.png:
    QT_QPA_PLATFORM=offscreen python tools/make_icon.py
"""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPolygonF

RED = QColor("#FF4655")
WHITE = QColor("#FFFFFF")


def draw(size: int = 256) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 256, size / 256)
    p.setPen(Qt.PenStyle.NoPen)

    p.setBrush(RED)
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)

    # Speech bubble with a tail at the lower left.
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(44, 58, 168, 112), 26, 26)
    tail = QPainterPath()
    tail.addPolygon(QPolygonF([QPointF(76, 160), QPointF(70, 208), QPointF(120, 164)]))
    p.setBrush(WHITE)
    p.drawPath(bubble.united(tail))

    # Two caption lines inside it.
    p.setBrush(RED)
    p.drawRoundedRect(QRectF(70, 88, 116, 18), 9, 9)
    p.drawRoundedRect(QRectF(70, 122, 76, 18), 9, 9)
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    out = Path(__file__).parent.parent / "src" / "valoroyce" / "data"
    icon = draw(256)
    icon.save(str(out / "icon.png"))
    print("wrote", out / "icon.png")
    if len(sys.argv) > 1:  # preview sheet: python tools/make_icon.py preview.png
        sheet = QImage(420, 280, QImage.Format.Format_ARGB32)
        sheet.fill(QColor("#1b1f24"))
        p = QPainter(sheet)
        p.drawImage(12, 12, icon)
        for i, s in enumerate((64, 32, 24, 16)):
            p.drawImage(290, 12 + i * 70, draw(s))
        p.fillRect(350, 0, 70, 280, QColor("#e8e8e8"))
        for i, s in enumerate((64, 32, 24, 16)):
            p.drawImage(360, 12 + i * 70, draw(s))
        p.end()
        sheet.save(sys.argv[1])
