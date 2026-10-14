"""Render the application icon to assets/icon.png and assets/icon.ico."""

import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPolygonF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    p.scale(s, s)

    bg = QLinearGradient(0, 0, 0, 256)
    bg.setColorAt(0, QColor(52, 60, 74))
    bg.setColorAt(1, QColor(22, 25, 31))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 44, 44)

    # the sign in section: a back with three ridges, one flank green and one red
    p.setBrush(QColor(243, 241, 232))
    p.drawRoundedRect(QRectF(34, 168, 188, 34), 6, 6)
    for k in range(3):
        x0 = 40 + k * 60
        p.setBrush(QColor(47, 162, 79))
        p.drawPolygon(QPolygonF([QPointF(x0, 170), QPointF(x0 + 28, 70), QPointF(x0 + 28, 170)]))
        p.setBrush(QColor(216, 54, 43))
        p.drawPolygon(QPolygonF([QPointF(x0 + 28, 170), QPointF(x0 + 28, 70), QPointF(x0 + 56, 170)]))
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841
    out = os.path.join(ROOT, "assets")
    os.makedirs(out, exist_ok=True)
    render(256).save(os.path.join(out, "icon.png"))
    render(256).save(os.path.join(out, "icon.ico"))
    print("icon written to", out)


if __name__ == "__main__":
    main()
