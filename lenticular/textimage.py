"""A message as a picture: text outlines from Qt, scaled to the sign and rasterised.

The text is laid out once as an outline (a QPainterPath in font units), so it
can be scaled to any letter height and drawn at any resolution.  A message can
also carry a picture - any image file, reduced to "ink or not" - beside its
text or instead of it.  ``render`` gives a coverage mask of the whole sign
face: 0 = background, 1 = letter or picture.
"""

from __future__ import annotations

import os

import numpy as np

FONT_PX = 256.0           # size the outlines are built at (font units = pixels)
STACK_STEP = 1.35         # stacked letters: baseline step / capital height
STACK_GAP = 0.35          # stacked letters: gap between columns / capital height
PICTURE_GAP = 0.35        # between a picture and its text / capital height
PICTURE_MAX = 2048        # pictures are worked on at up to this many pixels a side

ACROSS, STACKED, TURNED_UP, TURNED_DOWN = 0, 1, 2, 3
LEFT, RIGHT, ABOVE, BELOW = 0, 1, 2, 3
_app = None               # keeps a QApplication alive when none exists (tests, scripts)
_pictures = {}            # (path, modified, inverted) -> ink image


def _ensure_app():
    from PySide6.QtGui import QGuiApplication
    global _app
    if QGuiApplication.instance() is None:
        # outside the app (scripts, tests): Qt needs an application object to
        # use fonts.  A full QApplication, so widgets can still be made later.
        from PySide6.QtWidgets import QApplication
        _app = QApplication([])


def _lines(text: str) -> list:
    lines = [ln.rstrip() for ln in (text or "").replace("\r", "").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def load_picture(path: str, invert: bool = False):
    """A picture file as ink: white where the picture is, clear elsewhere, cropped to it.

    A picture with see-through parts counts as ink wherever it is solid.  One
    without counts as ink wherever it is dark.  ``invert`` swaps the two.
    Returns a QImage, or None if the file cannot be read or has no ink (or is
    all ink).
    """
    _ensure_app()
    from PySide6.QtCore import QRectF, QSize, Qt
    from PySide6.QtGui import QImage, QPainter

    try:
        key = (os.path.abspath(path), os.path.getmtime(path), bool(invert))
    except OSError:
        return None
    if key in _pictures:
        return _pictures[key]
    if path.lower().endswith((".svg", ".svgz")):
        from PySide6.QtSvg import QSvgRenderer
        svg = QSvgRenderer(path)
        size = svg.defaultSize()
        if not svg.isValid() or size.isEmpty():
            img = QImage()
        else:
            size = size.scaled(QSize(PICTURE_MAX, PICTURE_MAX), Qt.KeepAspectRatio)
            img = QImage(size, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            svg.render(painter, QRectF(0, 0, size.width(), size.height()))
            painter.end()
    else:
        img = QImage(path)
        if not img.isNull() and max(img.width(), img.height()) > PICTURE_MAX:
            img = img.scaled(PICTURE_MAX, PICTURE_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ink_img = None
    if not img.isNull():
        img = img.convertToFormat(QImage.Format_ARGB32)
        w, h = img.width(), img.height()
        px = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4]
        px = px.reshape(h, w, 4).astype(np.float32)                   # blue, green, red, alpha
        alpha = px[..., 3] / 255.0
        if alpha.min() < 0.98:                                        # see-through parts: solid = ink
            ink = alpha
        else:                                                         # none: dark = ink
            ink = 1.0 - (0.114 * px[..., 0] + 0.587 * px[..., 1] + 0.299 * px[..., 2]) / 255.0
        if invert:
            ink = 1.0 - ink
        on = ink >= 0.5
        if on.any() and not on.all():
            rows, cols = np.nonzero(on.any(axis=1))[0], np.nonzero(on.any(axis=0))[0]
            ink = ink[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
            out = np.full(ink.shape + (4,), 255, dtype=np.uint8)
            out[..., 3] = np.round(ink * 255.0)
            ink_img = QImage(out.tobytes(), ink.shape[1], ink.shape[0], ink.shape[1] * 4,
                             QImage.Format_ARGB32).copy()
    _pictures[key] = ink_img
    return ink_img


class TextShape:
    """One message - text, a picture, or both - centred later on the sign by its ink box."""

    def __init__(self, text: str, family: str, bold: bool, italic: bool, direction: int = ACROSS,
                 spacing: float = 0.0, picture=None, picture_at: int = LEFT, name: str = ""):
        _ensure_app()
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath

        font = QFont(family)
        font.setPixelSize(int(FONT_PX))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        spread = max(1.0 + spacing / 100.0, 0.3)
        if direction != STACKED and abs(spread - 1.0) > 1e-6:
            font.setLetterSpacing(QFont.PercentageSpacing, 100.0 * spread)
        fm = QFontMetricsF(font)
        self.cap = max(fm.capHeight(), 1.0)          # capital height in font units
        self.lines = _lines(text)

        path = QPainterPath()
        if direction == STACKED:
            # every line is a column of upright letters; columns run left to right
            x = 0.0
            for line in self.lines:
                glyphs = []
                for i, ch in enumerate(line):
                    g = QPainterPath()
                    g.addText(QPointF(0.0, 0.0), font, ch)
                    glyphs.append((i, g, g.boundingRect()))
                wide = max((r.width() for _, _, r in glyphs), default=0.0) or 0.6 * self.cap
                for i, g, r in glyphs:
                    if r.width() <= 0:                # a space: just a gap in the column
                        continue
                    g.translate(x + 0.5 * wide - r.center().x(), i * STACK_STEP * spread * self.cap)
                    path.addPath(g)
                x += wide + STACK_GAP * self.cap
        else:
            for i, line in enumerate(self.lines):
                if line.strip():
                    path.addText(QPointF(-0.5 * fm.horizontalAdvance(line), i * fm.lineSpacing()),
                                 font, line)
        path.setFillRule(Qt.WindingFill)             # overlapping strokes stay solid
        self.path = path
        # a turned line is laid out level and turned when it is drawn, picture and all
        self.turn = {TURNED_UP: -90.0, TURNED_DOWN: 90.0}.get(direction, 0.0)

        ink = path.boundingRect()
        has_text = not path.isEmpty() and ink.width() > 0 and ink.height() > 0
        self.picture, self.picture_rect = picture, None
        if picture is not None:
            aspect = picture.width() / picture.height()
            if not has_text:                         # alone: "letter height" is the picture's
                self.cap = FONT_PX
                self.picture_rect = QRectF(0.0, 0.0, aspect * FONT_PX, FONT_PX)
                ink = self.picture_rect
            else:                                    # with text: as tall as the block of text
                h, gap = ink.height(), PICTURE_GAP * self.cap
                w = aspect * h
                x, y = ink.center().x() - 0.5 * w, ink.top()
                if picture_at == LEFT:
                    x = ink.left() - gap - w
                elif picture_at == RIGHT:
                    x = ink.right() + gap
                elif picture_at == ABOVE:
                    y = ink.top() - gap - h
                else:
                    y = ink.bottom() + gap
                self.picture_rect = QRectF(x, y, w, h)
                ink = ink.united(self.picture_rect)
        self.empty = not has_text and picture is None
        self._centre = ink.center()                  # of the ink, as laid out
        # the ink box as it sits on the sign
        self.rect = (QRectF(0.0, 0.0, ink.height(), ink.width()) if self.turn
                     else QRectF(0.0, 0.0, ink.width(), ink.height()))
        self.name = (self.lines[0].strip() if self.lines else name or "picture") if not self.empty else ""

    def fit_scale(self, avail_w: float, avail_h: float) -> float:
        """mm per font unit that makes the ink just fit a box of avail_w x avail_h mm."""
        if self.empty:
            return 0.0
        return max(0.0, min(avail_w / self.rect.width(), avail_h / self.rect.height()))

    def size(self, scale: float):
        """(width, height) of the ink in mm at this scale."""
        return self.rect.width() * scale, self.rect.height() * scale

    def render(self, scale: float, width: float, height: float, px: float,
               upside_down: bool = False, dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
        """Coverage mask of a width x height mm face, the ink centred on it.

        Returns float32 (rows, columns) in 0..1; row 0 is the top edge.  dx, dy
        move the ink right and up (mm).  With ``upside_down`` the whole face,
        ink moved and all, is turned 180 degrees about its centre.
        """
        from PySide6.QtGui import QColor, QImage, QPainter

        nx = max(1, int(round(width / px)))
        ny = max(1, int(round(height / px)))
        img = QImage(nx, ny, QImage.Format_Grayscale8)
        img.fill(QColor(0, 0, 0))
        if not self.empty and scale > 0:
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.translate(nx / 2.0, ny / 2.0)
            if upside_down:
                painter.rotate(180.0)
            painter.translate(dx * nx / width, -dy * ny / height)
            painter.scale(scale * nx / width, scale * ny / height)
            painter.rotate(self.turn)
            painter.translate(-self._centre.x(), -self._centre.y())
            painter.fillPath(self.path, QColor(255, 255, 255))
            if self.picture is not None:
                painter.drawImage(self.picture_rect, self.picture)
            painter.end()
        buf = np.frombuffer(img.constBits(), dtype=np.uint8)
        return buf.reshape(ny, img.bytesPerLine())[:, :nx].astype(np.float32) / 255.0
