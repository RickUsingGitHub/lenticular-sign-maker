"""The two messages drawn flat, as the ridges will show them.

Every ridge is one line of each picture, so this is what each message is
reduced to: letters cut into strips one ridge wide, with the ridge tops
showing as stripes between them.  Message 2 of a sign that is turned over is
shown the way it will be read, not the way it is built.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .geometry import MINUS, PLUS

CAPTION = 22          # height of the line of text over each picture (px)
PAD = 10


class StripPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = None
        self.setMinimumSize(260, 120)

    def set_design(self, design):
        self.design = design
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().base())
        d = self.design
        if d is None:
            return
        w, h = self.width() - 2 * PAD, self.height() - 2 * PAD
        full_w, full_h = d.W + 2 * d.reach[0], d.H + 2 * d.reach[1]      # with any eyelets

        def scale(cols, rows):          # px per mm when the pictures sit in cols x rows
            return min((w - (cols - 1) * PAD) / cols / full_w,
                       (h - rows * CAPTION - (rows - 1) * PAD) / rows / full_h)

        side_by_side = scale(2, 1) >= scale(1, 2)
        sc = max(scale(2, 1) if side_by_side else scale(1, 2), 0.01)
        pw, ph = full_w * sc, full_h * sc
        for k in (0, 1):
            if side_by_side:                    # each picture is centred in its own cell
                cw = (w - PAD) / 2.0
                cx = PAD + k * (cw + PAD)
                y = PAD + 0.5 * (h - CAPTION - ph)
            else:
                cw, ch = w, (h - PAD) / 2.0
                cx = PAD
                y = PAD + k * (ch + PAD) + 0.5 * (ch - CAPTION - ph)
            caption = p.fontMetrics().elidedText(f"{'①②'[k]}  “{d.label(k)}”  –  {d.seen(k)}",
                                                 Qt.ElideRight, int(cw))
            p.setPen(self.palette().color(self.foregroundRole()))
            p.drawText(QRectF(cx, y, cw, CAPTION), Qt.AlignHCenter | Qt.AlignVCenter, caption)
            self._draw(p, d, k, QRectF(cx + 0.5 * (cw - pw), y + CAPTION, pw, ph), sc)
        p.end()

    @staticmethod
    def _draw(p: QPainter, d, k: int, rect: QRectF, sc: float):
        colors = {f: QColor.fromRgbF(*rgb) for f, rgb in d.p.colors().items()}
        side = d.side_of[k]
        text_fil, back_fil = d.fil[side]
        p.save()
        p.setClipRect(rect)
        p.translate(rect.center())
        if k == 1 and d.p.flip:                    # as it is read: the sign turned upside down
            p.rotate(180.0)
        p.scale(sc, -sc)                           # mm, y up
        p.translate(-0.5 * d.W, -0.5 * d.H)

        def box(u0, u1, v0, v1):                   # ridge coordinates -> the sign's face
            return QRectF(u0, v0, u1 - u0, v1 - v0) if d.vertical else QRectF(v0, u0, v1 - v0, u1 - u0)

        def outline(points):                       # from the sign's centre -> the sign's face
            return QPolygonF([QPointF(x + 0.5 * d.W, y + 0.5 * d.H) for x, y in points])

        edge, face = QPainterPath(), QPainterPath()
        edge.addRoundedRect(QRectF(0, 0, d.W, d.H), d.r, d.r)
        face.addRoundedRect(QRectF(d.b, d.b, d.W - 2 * d.b, d.H - 2 * d.b), d.ri, d.ri)
        p.setRenderHint(QPainter.Antialiasing)
        for rim, hole, _ in d.tabs:                # eyelets, behind the sign
            tab = QPainterPath()
            tab.addPolygon(outline(rim))
            tab.addPolygon(outline(hole))          # odd-even fill: a hole
            p.fillPath(tab, colors[d.p.base_fil].darker(108))
        p.fillPath(edge, colors[d.p.base_fil])                              # back and frame
        p.fillPath(face, colors[back_fil])
        p.setRenderHint(QPainter.Antialiasing, False)
        p.save()
        p.setClipPath(face, Qt.IntersectClip)                               # ridges stop at the frame
        # Beyond its flank each ridge shows its top, as a stripe along the far edge of the
        # line: one plain filament, or else half of it is the other message showing through
        other = MINUS if side == PLUS else PLUS
        stripe = d.pitch * (d.crest_share() if d.cap > 0 else 0.5 * d.c / d.pitch)
        for r, u0 in enumerate(float(u) for u in d.ridge_u):
            for a, b in d.runs[side][r]:
                p.fillRect(box(u0, u0 + d.pitch, a, b), colors[text_fil])
            if stripe * sc < 0.3:
                continue
            s0, s1 = (u0, u0 + stripe) if side == PLUS else (u0 + d.pitch - stripe, u0 + d.pitch)
            if d.cap > 0:
                p.fillRect(box(s0, s1, d.v_lo, d.v_hi), colors[d.p.crest_fil])
            else:
                p.fillRect(box(s0, s1, d.v_lo, d.v_hi), colors[d.fil[other][1]])
                for a, b in d.runs[other][r]:
                    p.fillRect(box(s0, s1, a, b), colors[d.fil[other][0]])
        p.restore()
        if d.pockets:                              # magnets, behind the face: dashed rings
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(40, 44, 52, 150), 0, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for x, y in d.pockets:
                p.drawEllipse(QPointF(x + 0.5 * d.W, y + 0.5 * d.H), d.pocket_r, d.pocket_r)
        p.restore()
