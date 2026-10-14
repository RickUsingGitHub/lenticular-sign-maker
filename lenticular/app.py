"""Lenticular Sign Maker - main window."""

from __future__ import annotations

import math
import os
import sys
import time

import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPen,
                           QPixmap, QSurfaceFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
                               QSplitter, QStyle, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import __version__
from .geometry import SignDesign
from .params import (BLOCK, EVERYWHERE, EYELETS, FILAMENTS, MAGNETS, NO_MOUNT, WHERE_NEEDED, SignParams,
                     load_design, outline_size, save_design)
from .preview import StripPreview
from .stl import write_stl
from .threemf import write_3mf
from .viewer import Viewer3D, standing_transform
from .viewing import LEAST, SAFETY, angle_to_use, seen_angles

APP_NAME = "Lenticular Sign Maker"
FILE_EXT = ".lenticular"
MESSAGE_1, MESSAGE_2, PRINT_LAYOUT = 0, 1, 2
DISPLAY_FOV = 9.0       # a narrow view, as if standing back from the sign
PRINT_FOV = 30.0
TURN_TIME = 0.7         # seconds to swing from one message to the other
MAGNET_COLOR = (0.72, 0.74, 0.78)

SIDE_NAMES = {0: ["Above", "Below"], 1: ["The left", "The right"]}

TEXT_RUNS = [(0, "Across"), (1, "Down, letters upright"), (2, "Turned, reading upwards"),
             (3, "Turned, reading downwards")]
PICTURE_AT = [(0, "Left of the text"), (1, "Right of the text"), (2, "Above the text"),
              (3, "Below the text")]


def _message(k: int, example: str, extra: str = "") -> tuple:
    """The settings of message k, as a group for SPEC."""
    return (f"Message {k}", [
        (f"text{k}", "Text", "text", None, None, None, f"e.g. {example}",
         "The words of this message. Press Enter for a second line." + extra),
        (f"font{k}", "Font", "font", None, None, None, "",
         "Typeface of the letters. Heavy, simple fonts survive being cut into ridges best."),
        (f"bold{k}", "Style", "style", None, None, None, f"italic{k}", ""),
        (f"size{k}", "Letter height", "float", 0.0, 1000.0, 1.0, " mm|Auto",
         "Height of the capital letters - or of the picture, when there is no text.\n"
         "Auto makes the message as big as fits the sign. With two messages of words on\n"
         "Auto, both get the same letter height."),
        (f"image{k}", "Picture", "picture", None, None, None, "",
         "A picture to go with the text, or instead of it: an image file or an SVG drawing.\n"
         "It is reduced to two tones, picture and background. One with see-through parts\n"
         "counts as picture wherever it is solid; one without, wherever it is dark."),
        (f"image_at{k}", "Picture goes", "choice", PICTURE_AT, None, None, "",
         "Where the picture sits beside the text. It is made as tall as the block of text."),
        (f"image_invert{k}", "Swap its tones", "bool", None, None, None, "",
         "Use the light (or see-through) parts of the picture instead of the dark (or solid) ones."),
        (f"text_dir{k}", "Text runs", "choice", TEXT_RUNS, None, None, "",
         "Which way this message runs on the sign.\n"
         "Across: ordinary lines. Down: the letters are stacked one under another, for a tall\n"
         "narrow sign. Turned: the whole line is turned a quarter turn, like a book's spine."),
        (f"spacing{k}", "Letter spacing", "float", -30.0, 200.0, 5.0, " %",
         "Spread the letters out (+) or close them up (-). 0 is the font's own spacing."),
        (f"dx{k}", "Move", "pair", -1000.0, 1000.0, 1.0, f"dy{k}",
         "Move this message from the middle of the sign: right (+) or left, and up (+) or\n"
         "down, as it is read. On Auto the letters shrink if that takes them near an edge."),
        (f"text_fil{k}", "Letters", "filament", None, None, None, "",
         f"The filament the letters or picture of message {k} are printed in."),
        (f"back_fil{k}", "Behind them", "filament", None, None, None, "",
         f"The filament round the letters, on message {k}'s side of the ridges."),
    ])


# (key, label, kind, min, max, step, suffix/special, tooltip)
SPEC = [
    _message(1, "CLEAN"),
    _message(2, "DIRTY", " Leave empty for a plain side."),
    ("Sign", [
        ("width", "Width", "float", 10.0, 2000.0, 5.0, " mm",
         "Size of the sign from left to right, as message 1 is read."),
        ("height", "Height", "float", 10.0, 2000.0, 5.0, " mm", "Size of the sign from bottom to top."),
        ("roundness", "Shape", "slider", 0.0, 2.0, 0.01, "",
         "Slide from square corners, through rounded corners, to semicircular ends in the\n"
         "middle - and on to a full circle at the far end.\n"
         "Past the middle the shorter side grows to match the longer one, so the circle is as\n"
         "wide as the sign's longer side. The ridges and frame follow the outline, and the\n"
         "letters shrink to stay inside it."),
        ("margin", "Margin", "float", 0.0, 200.0, 0.5, " mm", "Clear space between the frame and the messages."),
        ("frame", "Frame", "float", 0.0, 100.0, 0.5, " mm|None",
         "Width of the flat rim round the ridges, level with their tops. It protects the\n"
         "ridge ends and is printed in the same filament as the back."),
        ("base_thickness", "Back thickness", "float", 0.2, 50.0, 0.2, " mm",
         "Thickness of the solid back under the ridges. It is adjusted by up to half a layer\n"
         "so the whole sign is a whole number of layers thick, and made thicker if magnet\n"
         "pockets need it."),
    ]),
    ("Mounting", [
        ("mount", "Fix it with", "choice", [(NO_MOUNT, "Nothing built in"), (MAGNETS, "Magnets in the back"),
                                            (EYELETS, "Eyelets to hang it by")], None, None, "",
         "Magnets: round pockets in the back, open underneath, to glue disc magnets into. They\n"
         "are placed evenly, so the sign sits the same either way up.\n"
         "Eyelets: flat rings on the edge, as thick as the back, for a hook, nail or cord."),
        ("mount_count", "How many", "choice", [(1, "One"), (2, "Two"), (4, "Four")], None, None, "",
         "Magnets: one in the middle - the sign spins on it, which suits one that is turned\n"
         "over to change - or two at the ends of the longer side, or four in the corners.\n"
         "Eyelets: one at the top - and one at the bottom too, if the sign is turned over to\n"
         "change - or two, at the ends of the longer side."),
        ("magnet_d", "Magnet diameter", "float", 2.0, 100.0, 1.0, " mm",
         "Diameter of the disc magnets. The pockets are 0.2 mm bigger."),
        ("magnet_t", "Magnet thickness", "float", 0.5, 20.0, 0.5, " mm",
         "Thickness of the magnets. The pockets are a whole number of layers deep, just deeper\n"
         "than this, and the back is made thick enough to leave 0.6 mm over them."),
        ("mount_inset", "In from the edge", "float", 1.0, 500.0, 1.0, " mm",
         "Distance from the edge of the sign to the edge of each magnet."),
        ("hole_d", "Eyelet hole", "float", 1.5, 30.0, 0.5, " mm", "Diameter of the hole through each eyelet."),
    ]),
    ("Lenticular effect", [
        ("ridge_dir", "Ridges run", "choice", [(0, "Across (tilt up / down)"),
                                               (1, "Up and down (tilt left / right)")], None, None, "",
         "Across: the message depends on whether you look from above or below - right for a\n"
         "sign at one height, like a dishwasher door.\n"
         "Up and down: it depends on whether you look from the left or the right - right for\n"
         "a sign you walk past."),
        ("flip", "Turn over to change", "bool", None, None, None, "",
         "On: you always read from the same side, and turn the sign upside down (a half turn\n"
         "in its own plane) to change the message. Message 2 is built upside down for this.\n"
         "Off: the sign stays put. Message 1 shows from one side and message 2 from the other."),
        ("view_side", "Read from", "choice", [(0, "Above"), (1, "Below")], None, None, "",
         "The side message 1 is read from. A sign below eye level is read from above,\n"
         "one high on a wall from below."),
        ("ridges_where", "Ridges cover", "choice", [(EVERYWHERE, "The whole face"),
                                                    (BLOCK, "A block round the messages"),
                                                    (WHERE_NEEDED, "Only where needed")], None, None, "",
         "A groove between two ridges only matters where its two sides are different colours.\n"
         "Elsewhere it can be filled in, leaving the face flat, level with the ridge tops:\n"
         "cleaner to look at, and sturdier.\n"
         "A block round the messages: ridges in one block that holds both messages, flat\n"
         "outside it.\n"
         "Only where needed: ridges only under the letters. The other message's letters keep\n"
         "their ridges too, so they show as ridged patches in the plain colour."),
        ("view_angle", "Viewing angle", "angle", 10.0, 75.0, 5.0, "°",
         "How far off straight-on you must be to see one message alone. From there outwards\n"
         "the other message is hidden behind the ridges; closer to straight-on the two mix.\n"
         "A smaller angle makes taller, steeper ridges.\n"
         "Work out… finds it from where the sign hangs and where you stand."),
        ("pitch", "Ridge pitch", "float", 0.8, 50.0, 0.2, " mm",
         "Spacing of the ridges: each ridge is one line of both pictures. Smaller gives a finer\n"
         "picture, but each flank must still be wide enough to print in its own colour:\n"
         "about 3 mm suits a 0.4 mm nozzle. It is adjusted slightly so whole ridges fill the sign."),
        ("crest", "Ridge crest", "float", 0.0, 10.0, 0.1, " mm|Sharp",
         "Width of the flat on top of each ridge. About one nozzle width prints more cleanly\n"
         "than a knife edge. It is seen from both sides, so keep it small."),
    ]),
    ("Filaments", [
        ("color1", "Filament 1", "color", None, None, None, "",
         "Colour of filament 1 here and in the 3MF. Load the same colour in slot 1 of the slicer."),
        ("color2", "Filament 2", "color", None, None, None, "", "Colour of filament 2."),
        ("color3", "Filament 3", "color", None, None, None, "", "Colour of filament 3."),
        ("color4", "Filament 4", "color", None, None, None, "",
         "Colour of filament 4. It is only exported if something is set to use it."),
        ("base_fil", "Back and frame", "filament", None, None, None, "",
         "The filament of the back and the frame - most of the sign. Using the same one as\n"
         "behind the letters saves colour changes."),
        ("crest_fil", "Ridge tops", "tops", None, None, None, "",
         "The top of every ridge is seen from both sides, and is too narrow to print in two\n"
         "colours.\n"
         "A filament: the tops are plain, down to where each flank is a full line wide. They\n"
         "show as fine stripes of that colour and print cleanly - best in the colour behind\n"
         "the letters. A bigger ridge pitch makes the stripes a smaller share of the picture.\n"
         "Half from each message: fuller colour, but each message shows as faint lines in\n"
         "the other, and it needs the slicer's Arachne wall generator to print the narrow tops."),
    ]),
    ("Printer", [
        ("bed_x", "Bed width", "float", 30.0, 2000.0, 5.0, " mm",
         "Width of the printer bed. The sign is checked against it and placed at its centre."),
        ("bed_y", "Bed depth", "float", 30.0, 2000.0, 5.0, " mm", "Depth of the printer bed."),
        ("layer_height", "Layer height", "float", 0.04, 1.0, 0.04, " mm",
         "The layer height you will slice with. The sign is made a whole number of layers thick."),
        ("line_width", "Line width", "float", 0.1, 2.0, 0.01, " mm",
         "The slicer's line width: about 0.42 mm for a 0.4 mm nozzle. A slicer leaves out\n"
         "anything narrower, so each flank's colour stops where its half of the ridge gets\n"
         "this narrow, and the plain ridge top takes over from there."),
        ("min_detail", "Smallest detail", "float", 0.2, 5.0, 0.1, " mm",
         "Shortest piece of colour along a ridge. Shorter pieces of a letter are left out and\n"
         "shorter gaps closed, since the printer cannot make them. About one nozzle width."),
    ]),
]

# Settings shown in Simple mode; the rest appear only in Detailed mode.
SIMPLE_KEYS = {"text1", "font1", "bold1", "size1", "text_fil1", "back_fil1",
               "text2", "font2", "bold2", "size2", "text_fil2", "back_fil2",
               "width", "height", "roundness", "mount", "ridge_dir", "flip", "view_side", "ridges_where",
               "color1", "color2", "color3", "color4", "base_fil"}
PICTURE_FILES = "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;All files (*)"

REMEMBERED = ("bed_x", "bed_y", "layer_height", "line_width")   # printer settings kept for new designs


def swatch(color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QPen(QColor(20, 22, 26), 1))
    p.setBrush(QColor(color))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def glyph_icon(kind: str) -> QIcon:
    """Small light line icons that read well on the dark theme."""
    from PySide6.QtCore import QPointF, QRectF

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(225, 230, 236), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if kind == "export":
        p.drawLine(QPointF(32, 8), QPointF(32, 40))
        p.drawPolyline([QPointF(19, 28), QPointF(32, 41), QPointF(45, 28)])
        p.drawPolyline([QPointF(10, 40), QPointF(10, 54), QPointF(54, 54), QPointF(54, 40)])
    elif kind == "picture":
        p.drawRoundedRect(QRectF(8, 14, 48, 38), 4, 4)
        pen.setColor(QColor(240, 163, 86))
        p.setPen(pen)
        p.drawPolyline([QPointF(14, 44), QPointF(26, 30), QPointF(34, 38), QPointF(42, 26),
                        QPointF(50, 38)])
    p.end()
    return QIcon(pm)


def make_app_icon() -> QIcon:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "assets", "icon.png"),
                 os.path.join(getattr(sys, "_MEIPASS", here), "assets", "icon.png")):
        if os.path.exists(cand):
            return QIcon(cand)
    return QIcon()


def sign_parts(d: SignDesign):
    """The sign for write_3mf, as it lies on the bed: (parts, fits the bed)."""
    colors = d.p.colors()
    parts = []
    for fil in sorted(d.bodies):
        verts, faces = d.bodies[fil]
        if d.turn_on_bed:                       # a quarter turn makes it fit
            verts = verts[:, [1, 0, 2]] * (-1.0, 1.0, 1.0)
        parts.append((d.part_name(fil), verts, faces, fil, colors[fil]))
    return parts, d.fits_bed


def magnet_mesh(d: SignDesign, sides: int = 40):
    """The magnets sitting in their pockets, for the 3D view only: (verts, faces)."""
    radius, top = 0.5 * d.p.magnet_d, d.p.magnet_t
    ring = [(radius * math.cos(2 * math.pi * i / sides), radius * math.sin(2 * math.pi * i / sides))
            for i in range(sides)]
    verts, faces = [], []
    for x, y in d.pockets:
        base = len(verts)
        verts += [(x + u, y + v, 0.0) for u, v in ring] + [(x + u, y + v, top) for u, v in ring]
        verts += [(x, y, 0.0), (x, y, top)]
        low, high = base + 2 * sides, base + 2 * sides + 1
        for i in range(sides):
            j = (i + 1) % sides
            faces += [(low, base + j, base + i), (high, base + sides + i, base + sides + j),
                      (base + i, base + j, base + sides + j), (base + i, base + sides + j, base + sides + i)]
    return np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int32)


class AngleHelper(QDialog):
    """Works out the viewing angle from where the sign hangs and where it is read from."""

    def __init__(self, parent, design: SignDesign, settings: QSettings):
        super().__init__(parent)
        self.setWindowTitle("Work out the viewing angle")
        self.design, self.settings = design, settings
        self.angle, self.side = None, design.p.view_side
        self.fields = {}
        form = QFormLayout()

        def length(key, label, default, tip):
            s = QDoubleSpinBox()
            s.setRange(0.0, 2000.0)
            s.setDecimals(0)
            s.setSingleStep(5.0)
            s.setSuffix(" cm")
            s.setValue(float(settings.value(f"helper_{key}", default)))
            s.setToolTip(tip)
            s.valueChanged.connect(self._update)
            self.fields[key] = s
            form.addRow(label, s)

        if design.vertical:                       # ridges up and down: it is read from one side
            self.which = QComboBox()
            self.which.addItems(SIDE_NAMES[1])
            self.which.setCurrentIndex(int(settings.value("helper_which", design.p.view_side)))
            self.which.currentIndexChanged.connect(self._update)
            form.addRow("You read it from", self.which)
            length("aside", "How far to that side of it", 100.0,
                   "How far along the wall you are from the middle of the sign.")
            length("out", "How far out from the wall", 150.0,
                   "How far you stand from the wall the sign is on.")
        else:                                     # ridges across: from above or below
            length("sign", "Middle of the sign above the floor", 85.0,
                   "A dishwasher door is about 60 cm; a fridge door at eye level about 150 cm.")
            length("eye", "Your eyes above the floor", 165.0, "Standing eye height.")
            length("out", "How far back you stand", 60.0,
                   "From the face of the sign. Use the furthest you will read it from:\n"
                   "that is where you see it most nearly straight-on.")
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setMinimumWidth(380)
        self.result.setMinimumHeight(86)
        self.result.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addLayout(form)
        box.addWidget(self.result)
        box.addWidget(self.buttons)
        self._update()

    def _update(self):
        mm = {key: 10.0 * s.value() for key, s in self.fields.items()}
        if self.design.vertical:
            offset, self.side = mm["aside"], self.which.currentIndex()
        else:
            offset = mm["eye"] - mm["sign"]
            self.side = 0 if offset >= 0 else 1
        where = SIDE_NAMES[1 if self.design.vertical else 0][self.side].lower()
        out = max(mm["out"], 1.0)
        near, middle, far = seen_angles(self.design.U, offset, out)
        self.angle = angle_to_use(self.design.U, offset, out)
        if self.angle is None:
            text = (f"<p>From there the sign is seen almost straight-on: {max(near, 0):.0f}° at its nearer "
                    f"edge. Below about {LEAST + SAFETY:.0f}° the two messages mix.</p>"
                    f"<p>Stand closer, or fix the sign further from eye level.</p>")
        else:
            text = (f"<p>You see it from {where}, at <b>{middle:.0f}°</b> off straight-on: "
                    f"{near:.0f}° at its nearer edge and {far:.0f}° at its further edge.</p>"
                    f"<p>Viewing angle to use: <b>{self.angle:g}°</b>, read from {where}. That keeps "
                    f"{SAFETY:g}° in hand at the nearer edge.</p>")
        self.result.setText(text)
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(self.angle is not None)
        ok.setText(f"Use {self.angle:g}°" if self.angle is not None else "Use it")

    def accept(self):
        for key, s in self.fields.items():
            self.settings.setValue(f"helper_{key}", s.value())
        if self.design.vertical:
            self.settings.setValue("helper_which", self.which.currentIndex())
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("LenticularSign", "LenticularSignMaker")
        self.simple_mode = self.settings.value("simple_mode", True, type=bool)
        self.params = self._fresh_params()
        self.design: SignDesign | None = None
        self.current_file = None
        self.view = MESSAGE_1
        self.turn = 0.0               # how far the standing sign is turned in its plane (deg)
        self.anim = None
        self._free_camera = False     # the user has dragged the view away from a preset
        self._fitted = None           # (view kind, scene size) the camera distance was set for
        self._dirty = False
        self.widgets = {}
        self.slider_notes = {}        # key -> the label under a slider
        self.picture_clear = {}       # key -> the button that takes a picture away again

        self.regen_timer = QTimer(self)
        self.regen_timer.setSingleShot(True)
        self.regen_timer.timeout.connect(self.regenerate)
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(16)
        self.anim_timer.timeout.connect(self._anim_tick)

        self.setWindowIcon(make_app_icon())
        self._build_ui()
        self._build_actions()
        self.resize(1440, 920)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.regenerate()

    # ================================================================== UI
    def _build_ui(self):
        # ---------------- parameter panel
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(8)

        # Simple / Detailed switch
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self.simple_btn = QPushButton("Simple")
        self.detail_btn = QPushButton("Detailed")
        self.simple_btn.setToolTip("Just the essentials: the two messages, the sign's size and shape, "
                                   "and the colours.")
        self.detail_btn.setToolTip("Every setting: pictures, layout, ridges, mounting details and printer.")
        for b in (self.simple_btn, self.detail_btn):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setMinimumHeight(28)
            b.setObjectName("modeButton")
            mode_row.addWidget(b)
        (self.simple_btn if self.simple_mode else self.detail_btn).setChecked(True)
        self.simple_btn.clicked.connect(lambda: self.set_simple_mode(True))
        self.detail_btn.clicked.connect(lambda: self.set_simple_mode(False))
        pl.addLayout(mode_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(self.params.name)
        self.name_edit.setToolTip("Design name - used for exported file names.")
        self.name_edit.textChanged.connect(self._on_name)
        name_row.addWidget(self.name_edit)
        pl.addLayout(name_row)

        self._rows = {}       # key -> (form, field widget)
        self._groups = []     # (group box, [keys])
        for title, items in SPEC:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setContentsMargins(8, 12, 8, 8)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(5)
            for key, label, kind, lo, hi, step, extra, tip in items:
                w = self._make_widget(key, kind, lo, hi, step, extra)
                lab = QLabel(label)
                if tip:
                    lab.setToolTip(tip)
                    w.setToolTip(tip)
                form.addRow(lab, w)
                self._rows[key] = (form, w)
            self._groups.append((box, [it[0] for it in items]))
            pl.addWidget(box)
        pl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(430)

        # ---------------- viewer + flat preview + info
        self.viewer = Viewer3D()
        self.viewer.dragged.connect(self._on_dragged)
        self.viewer.reset_requested.connect(lambda: self.show_view(self.view))
        self.viewer.resized.connect(self._on_resized)
        self.preview = StripPreview()
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.info.setMinimumWidth(270)

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self.preview)
        bottom.addWidget(self.info)
        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 2)
        bottom.setSizes([640, 450])

        right = QSplitter(Qt.Vertical)
        right.addWidget(self._make_view_bar())
        right.addWidget(self.viewer)
        right.addWidget(bottom)
        right.setStretchFactor(1, 3)
        right.setStretchFactor(2, 2)
        right.setSizes([36, 520, 340])
        right.setCollapsible(0, False)
        right.handle(1).setEnabled(False)

        main = QSplitter(Qt.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        main.setSizes([350, 1090])
        self.setCentralWidget(main)
        self.statusBar().showMessage("Ready")
        self._refresh_widgets()

    def _make_view_bar(self):
        bar = QWidget()
        bar.setMaximumHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 4, 6, 4)
        h.addWidget(QLabel("Show"))
        row = QHBoxLayout()
        row.setSpacing(0)
        self.view_btns = []
        for idx, text in enumerate(("① Message 1", "② Message 2", "Print layout")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setMinimumHeight(28)
            b.setObjectName("modeButton")
            b.clicked.connect(lambda _=False, i=idx: self.show_view(i))
            row.addWidget(b)
            self.view_btns.append(b)
        self.view_btns[self.view].setChecked(True)
        self.view_btns[PRINT_LAYOUT].setToolTip("The sign as it prints: flat on its back on the bed (Ctrl+3)")
        h.addLayout(row)
        h.addSpacing(12)
        hint = QLabel("Drag to tilt the sign and watch the message change")
        hint.setStyleSheet("color: #9aa3ad;")
        h.addWidget(hint)
        h.addStretch(1)
        fit = QToolButton()
        fit.setText("Reset view")
        fit.setToolTip("Back to the chosen view (double-click the 3D view, or press F)")
        fit.clicked.connect(lambda: self.show_view(self.view))
        h.addWidget(fit)
        return bar

    def _make_widget(self, key, kind, lo, hi, step, extra):
        val = getattr(self.params, key)

        def spin(k, suffix, special="", prefix=""):
            s = QDoubleSpinBox()
            s.setDecimals(2)
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setPrefix(prefix)
            s.setSuffix(suffix)
            if special:
                s.setSpecialValueText(special)      # shown at the minimum (0)
            s.setValue(getattr(self.params, k))
            s.setKeyboardTracking(False)
            s.valueChanged.connect(lambda v, k=k: self._on_param(k, float(v)))
            self.widgets[k] = s
            return s

        def row(*parts):                          # several widgets side by side in one field
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            for part in parts:
                hl.addWidget(part, 0 if isinstance(part, QToolButton) else 1)
            return box

        if kind == "float":
            suffix, _, special = extra.partition("|")
            w = spin(key, suffix, special)
            w.setMinimumWidth(110)
            return w
        elif kind == "pair":                      # sideways and up, side by side
            return row(spin(key, " mm", prefix="→ "), spin(extra, " mm", prefix="↑ "))
        elif kind == "angle":                     # the viewing angle, and a way to work it out
            helper = QToolButton()
            helper.setText("Work out…")
            helper.setToolTip("Find the angle from where the sign hangs and where you stand.")
            helper.clicked.connect(self.work_out_angle)
            return row(spin(key, extra), helper)
        elif kind == "picture":                   # a file to choose, and a way to take it away
            w = QPushButton()
            w.setMinimumHeight(24)
            w.clicked.connect(lambda _=False, k=key: self._pick_picture(k))
            clear = QToolButton()
            clear.setText("✕")
            clear.setToolTip("Take the picture away")
            clear.clicked.connect(lambda _=False, k=key: self._on_param(k, ""))
            self.picture_clear[key] = clear
            self.widgets[key] = w
            return row(w, clear)
        elif kind == "bool":
            w = QCheckBox()
            w.setChecked(val)
            w.toggled.connect(lambda v, k=key: self._on_param(k, bool(v)))
        elif kind == "text":
            w = QPlainTextEdit(str(val))
            w.setPlaceholderText(extra)
            w.setTabChangesFocus(True)
            w.setFixedHeight(48)                  # two lines; scrolls for more
            w.textChanged.connect(lambda k=key, ww=w: self._on_param(k, ww.toPlainText()))
        elif kind == "font":
            w = QFontComboBox()
            w.setFontFilters(QFontComboBox.ScalableFonts)
            w.setCurrentFont(QFont(val))
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(12)
            w.currentFontChanged.connect(lambda f, k=key: self._on_param(k, f.family()))
        elif kind == "style":                     # bold and italic side by side
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            for k, text in ((key, "Bold"), (extra, "Italic")):
                c = QCheckBox(text)
                c.setChecked(getattr(self.params, k))
                c.toggled.connect(lambda v, k=k: self._on_param(k, bool(v)))
                hl.addWidget(c)
                self.widgets[k] = c
            hl.addStretch(1)
            return box
        elif kind == "choice":
            w = QComboBox()
            for value, label in lo:
                w.addItem(label, value)
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(12)
            w.setCurrentIndex(max(0, w.findData(val)))
            w.currentIndexChanged.connect(lambda i, k=key, ww=w: self._on_param(k, ww.itemData(i)))
        elif kind in ("filament", "tops"):
            w = QComboBox()
            for f in FILAMENTS:
                w.addItem(f"Filament {f}", f)
            if kind == "tops":
                w.addItem("Half from each message", 0)
            w.setCurrentIndex(max(0, w.findData(val)))
            w.currentIndexChanged.connect(lambda i, k=key, ww=w: self._on_param(k, ww.itemData(i)))
        elif kind == "color":
            w = QPushButton()
            w.setMinimumHeight(24)
            w.clicked.connect(lambda _=False, k=key: self._pick_color(k))
        elif kind == "slider":                    # in hundredths, with what it means underneath
            box = QWidget()
            vl = QVBoxLayout(box)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(1)
            w = QSlider(Qt.Horizontal)
            w.setRange(int(round(lo * 100)), int(round(hi * 100)))
            w.setPageStep(10)
            w.setTickInterval(100)
            w.setTickPosition(QSlider.TicksBelow)
            w.setValue(int(round(val * 100)))
            w.valueChanged.connect(lambda v, k=key: self._on_param(k, v / 100.0))
            self.slider_notes[key] = QLabel()
            self.slider_notes[key].setStyleSheet("color: #9aa3ad;")
            vl.addWidget(w)
            vl.addWidget(self.slider_notes[key])
            self.widgets[key] = w
            return box
        else:
            raise ValueError(kind)
        self.widgets[key] = w
        return w

    def _refresh_widgets(self):
        """Push self.params into the widgets without triggering updates."""
        for key, w in self.widgets.items():
            val = getattr(self.params, key)
            w.blockSignals(True)
            if isinstance(w, QFontComboBox):
                w.setCurrentFont(QFont(val))
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(val)))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            elif isinstance(w, QPlainTextEdit):
                if w.toPlainText() != str(val):
                    w.setPlainText(str(val))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(val)
            elif isinstance(w, QSlider):
                w.setValue(int(round(val * 100)))
            w.blockSignals(False)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(self.params.name)
        self.name_edit.blockSignals(False)
        self._update_title()
        self._sync_widgets()

    def _fresh_params(self) -> SignParams:
        """Default design, using the printer settings remembered from last time."""
        p = SignParams()
        # a first run borrows the bed size from Maze Tube Designer, if that has been used
        other = QSettings("MazeTube", "MazeTubeDesigner")
        for key in REMEMBERED:
            for v in (self.settings.value(f"default_{key}"), other.value(f"default_{key}")):
                try:
                    if v is not None:
                        setattr(p, key, float(v))
                        break
                except (TypeError, ValueError):
                    pass
        return p

    def _sync_widgets(self):
        """Colour swatches, labels and greyed-out settings that follow other settings."""
        p = self.params
        for k in FILAMENTS:
            color = getattr(p, f"color{k}")
            btn = self.widgets[f"color{k}"]
            dark = QColor(color).lightnessF() < 0.55
            btn.setText(color.upper())
            btn.setStyleSheet(f"background: {color}; color: {'white' if dark else 'black'}; "
                              f"border: 1px solid #3b4049; border-radius: 3px;")
        for key in ("text_fil1", "back_fil1", "text_fil2", "back_fil2", "base_fil", "crest_fil"):
            combo = self.widgets[key]
            for i in range(combo.count()):
                if combo.itemData(i) in FILAMENTS:
                    combo.setItemIcon(i, swatch(getattr(p, f"color{combo.itemData(i)}")))
        side = self.widgets["view_side"]
        for i, text in enumerate(SIDE_NAMES[p.ridge_dir]):
            side.setItemText(i, text)
        # the shape: what the slider's position means, and which side it has taken over
        w, h, r = outline_size(p)
        if p.roundness <= 0:
            shape = "Square corners"
        elif p.roundness < 1:
            shape = f"Rounded corners, {r:.1f} mm radius"
        elif w == h:
            shape = f"Circle, {w:.4g} mm across"
        elif p.roundness == 1:
            shape = "Semicircular ends"
        else:
            shape = f"Towards a circle: {w:.4g} × {h:.4g} mm"
        self.slider_notes["roundness"].setText(shape)
        self.widgets["width"].setEnabled(w == p.width)
        self.widgets["height"].setEnabled(h == p.height)
        # how many magnets or eyelets: said in full, since where they go depends on which
        if p.mount == EYELETS:
            places = {1: "One at the top, one at the bottom" if p.flip else "One, at the top",
                      2: "Two, at the ends"}
        else:
            places = {1: "One, in the middle", 2: "Two, at the ends", 4: "Four, in the corners"}
        counts = self.widgets["mount_count"]
        if [(counts.itemData(i), counts.itemText(i)) for i in range(counts.count())] != list(places.items()):
            counts.blockSignals(True)
            counts.clear()
            for value, text in places.items():
                counts.addItem(text, value)
            counts.setCurrentIndex(max(0, counts.findData(p.mount_count)))
            counts.blockSignals(False)
        for k in (1, 2):
            words = bool(getattr(p, f"text{k}").strip())
            picture = getattr(p, f"image{k}").strip()
            for key in (f"font{k}", f"bold{k}", f"italic{k}", f"spacing{k}", f"text_dir{k}"):
                self.widgets[key].setEnabled(words)
            for key in (f"size{k}", f"text_fil{k}", f"dx{k}", f"dy{k}"):
                self.widgets[key].setEnabled(words or bool(picture))
            self.widgets[f"image_at{k}"].setEnabled(words)
            button = self.widgets[f"image{k}"]
            button.setText(os.path.basename(picture) if picture else "Choose…")
            self.picture_clear[f"image{k}"].setEnabled(bool(picture))
        self._apply_mode()

    def _wanted(self, key) -> bool:
        """Whether a setting's row is shown.

        Simple mode keeps to the essentials.  In either mode, settings with
        nothing to act on are hidden, and ones a choice has just made necessary
        are shown.
        """
        p = self.params
        if key[-1] in "12" and key.startswith("image"):               # a message's picture
            if getattr(p, f"image{key[-1]}").strip():
                return True
            if key.startswith(("image_at", "image_invert")):
                return False
        if key in ("mount_count", "magnet_d", "magnet_t", "mount_inset", "hole_d"):
            if p.mount == NO_MOUNT or (key == "hole_d") != (p.mount == EYELETS):
                return key == "mount_count" and p.mount != NO_MOUNT
            if key != "mount_inset":
                return True
        return not self.simple_mode or key in SIMPLE_KEYS

    def _apply_mode(self):
        for key, (form, field) in self._rows.items():
            form.setRowVisible(field, self._wanted(key))
        for box, keys in self._groups:
            box.setVisible(any(self._wanted(k) for k in keys))

    def set_simple_mode(self, simple: bool, remember: bool = True):
        self.simple_mode = simple
        (self.simple_btn if simple else self.detail_btn).setChecked(True)
        if remember:
            self.settings.setValue("simple_mode", simple)
        self._apply_mode()

    def _pick_color(self, key):
        c = QColorDialog.getColor(QColor(getattr(self.params, key)), self, "Filament colour")
        if c.isValid():
            self._on_param(key, c.name())

    def _pick_picture(self, key):
        now = getattr(self.params, key).strip()
        start = os.path.dirname(now) if now else self.settings.value("picture_dir", self._last_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", start, PICTURE_FILES)
        if path:
            self.settings.setValue("picture_dir", os.path.dirname(path))
            self._on_param(key, path)

    def work_out_angle(self):
        """Ask where the sign hangs and where it is read from, and set the viewing angle by it."""
        if self.design is None:
            return
        helper = AngleHelper(self, self.design, self.settings)
        if helper.exec() == QDialog.Accepted and helper.angle is not None:
            self.params.view_angle, self.params.view_side = helper.angle, helper.side
            self._refresh_widgets()
            self._dirty = True
            self._update_title()
            self.regenerate()
            self.statusBar().showMessage(
                f"Viewing angle set to {helper.angle:g}°, read from "
                f"{SIDE_NAMES[self.params.ridge_dir][helper.side].lower()}", 8000)

    def _build_actions(self):
        st = self.style()
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        def act(text, slot, icon=None, shortcut=None, tip=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            return a

        self.a_new = act("New", self.new_design, QStyle.SP_FileIcon, "Ctrl+N", "Start again from the default sign")
        self.a_open = act("Open…", self.open_design, QStyle.SP_DialogOpenButton, "Ctrl+O",
                          "Open a saved sign design")
        self.a_save = act("Save", self.save_design, QStyle.SP_DialogSaveButton, "Ctrl+S", "Save the design")
        self.a_save_as = act("Save As…", lambda: self.save_design(as_new=True), None, "Ctrl+Shift+S")
        self.a_exp_3mf = act("Export 3MF…", self.export_3mf, None, "Ctrl+E",
                             "One 3MF file: the sign as one object with a part for each filament")
        self.a_exp_3mf.setIcon(glyph_icon("export"))
        self.a_exp_stl = act("Export STLs (one per filament)…", self.export_stl, None, "Ctrl+Shift+E",
                             "A separate STL for each filament, for slicers that do not read 3MF parts")
        self.a_snap = act("Save picture…", self.save_snapshot, None, "Ctrl+P",
                          "Save the 3D view as a PNG image")
        self.a_snap.setIcon(glyph_icon("picture"))
        self.a_quit = act("Exit", self.close, None, "Ctrl+Q")
        self.a_about = act("About", self.about)

        tb.addAction(self.a_new)
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        tb.addSeparator()
        exp_btn = QToolButton()
        exp_btn.setDefaultAction(self.a_exp_3mf)
        exp_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        exp_btn.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(exp_btn)
        m.addAction(self.a_exp_3mf)
        m.addAction(self.a_exp_stl)
        exp_btn.setMenu(m)
        tb.addWidget(exp_btn)
        tb.addAction(self.a_snap)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, None, self.a_exp_3mf,
                  self.a_exp_stl, self.a_snap, None, self.a_quit):
            fm.addAction(a) if a is not None else fm.addSeparator()
        vm = mb.addMenu("&View")
        self.view_actions = []
        for i, name in enumerate(("Message 1", "Message 2", "Print layout")):
            a = QAction(name, self)
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            a.triggered.connect(lambda _=False, i=i: self.show_view(i))
            vm.addAction(a)
            self.view_actions.append(a)
        vm.addSeparator()
        a = QAction("Reset view", self)
        a.setShortcut(QKeySequence("Ctrl+0"))
        a.triggered.connect(lambda: self.show_view(self.view))
        vm.addAction(a)
        hm = mb.addMenu("&Help")
        hm.addAction(self.a_about)

    # ============================================================ params
    def _on_param(self, key, value):
        setattr(self.params, key, value)
        if key in REMEMBERED:
            self.settings.setValue(f"default_{key}", float(value))
        if self.params.mount == EYELETS and self.params.mount_count == 4:     # eyelets: one or two
            self.params.mount_count = 2
            self._refresh_widgets()
        self._sync_widgets()
        self._dirty = True
        self._update_title()
        # typing waits for a pause; the shape slider follows the hand
        self.regen_timer.start(500 if key in ("text1", "text2") else 30 if key == "roundness" else 150)

    def _on_name(self, text):
        self.params.name = text.strip() or "sign"
        self._dirty = True
        self._update_title()

    def _update_title(self):
        f = os.path.basename(self.current_file) if self.current_file else "unsaved"
        star = " •" if self._dirty else ""
        self.setWindowTitle(f"{APP_NAME} — {self.params.name} ({f}){star}")

    # ============================================================ build
    def regenerate(self):
        t0 = time.perf_counter()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            d = SignDesign(self.params.copy())
        except Exception as exc:   # pragma: no cover - safety net for the GUI
            self._show_info(None, [f"Internal error: {exc}"], [])
            return
        finally:
            QApplication.restoreOverrideCursor()
        if d.errors:
            self._show_info(None, d.errors, d.warnings)
            self.statusBar().showMessage("This sign cannot be built - see the panel on the right")
            return

        self.design = d
        colors = d.p.colors()
        for fil in FILAMENTS:
            if fil in d.bodies:
                self.viewer.set_mesh(f"filament{fil}", *d.bodies[fil], colors[fil])
            else:
                self.viewer.remove_mesh(f"filament{fil}")
        if d.pockets:                          # the magnets themselves, so the back shows where they go
            self.viewer.set_mesh("magnets", *magnet_mesh(d), MAGNET_COLOR)
        else:
            self.viewer.remove_mesh("magnets")
        self.viewer.set_bed(d.p.bed_x, d.p.bed_y)
        self.preview.set_design(d)
        for k in (MESSAGE_1, MESSAGE_2):
            label = d.label(k)
            short = label if len(label) <= 16 else label[:15] + "…"
            self.view_btns[k].setText(f"{'①②'[k]} {short}")
            self.view_btns[k].setToolTip(f"The sign as “{label}” is read: {d.seen(k)} (Ctrl+{k + 1})")
            self.view_actions[k].setText(f"Message {k + 1}: {short}")
        self._apply_view()
        self._show_info(d, [], d.warnings)
        dt = time.perf_counter() - t0
        self.statusBar().showMessage(f"Built in {dt * 1000:.0f} ms — {d.triangles:,} triangles", 6000)

    def _show_info(self, d, errors, warnings):
        css = ("<style>td{padding:2px 6px;} .k{color:#9aa3ad;} .e{color:#ff6b6b;} "
               ".w{color:#f2c14e;} .ok{color:#6fdc8c;} h3{margin:4px 0 6px 0;}</style>")
        html = [css]
        if errors:
            html.append("<h3 class='e'>Cannot build this sign</h3><ul>")
            html += [f"<li class='e'>{e}</li>" for e in errors]
            html.append("</ul>")
        if d is not None:
            html.append("<h3>The sign</h3><table>")
            for k, v in d.info().items():
                html.append(f"<tr><td class='k'>{k}</td><td>{v}</td></tr>")
            html.append("</table>")
            for note in d.notes:
                html.append(f"<p class='k'>ℹ {note}</p>")
        if warnings:
            html.append("<h3 class='w'>Warnings</h3><ul>")
            html += [f"<li class='w'>{w}</li>" for w in warnings]
            html.append("</ul>")
        if d is not None:
            changes, layers, _ = d.colour_changes()
            html.append(
                "<p class='k'><b>Printing:</b> flat on its back, ridges up, no supports. Slice at "
                f"<b>{d.layer:g} mm</b> layers, the height the sign is built for. The colours only meet "
                f"in the top {layers} layers (the ridges), so there are about {changes} filament "
                "changes. Use 100% infill or 4+ top layers so the back prints solid under the "
                "ridges.</p>")
            if d.pockets:
                html.append(
                    "<p class='k'><b>Magnets:</b> the pockets are open underneath and bridge over "
                    f"{d.pocket_depth:.1f} mm up, with no supports. Glue the magnets in afterwards.</p>")
            fils = [str(f) for f in sorted(d.bodies)]
            listed = ", ".join(fils[:-1]) + (" and " if len(fils) > 1 else "") + fils[-1]
            html.append(
                "<p class='k'><b>3MF in Flash Studio / OrcaSlicer:</b> use <b>File → Import</b> "
                "(Ctrl+I). The sign opens as one object with a part for each filament, already "
                f"set to filament{'s' if len(fils) > 1 else ''} {listed}: load those colours in those "
                "slots. Opening the file as a "
                "project instead shows “loading geometry data only”. That notice is "
                "harmless: the file holds the shapes, and your printer and filament settings stay "
                "as they are.</p>")
        self.info.setHtml("".join(html))

    # ============================================================ view
    def _distances(self):
        """Camera distances for the standing sign: (it fills the view, it can turn right round)."""
        v, d = self.viewer, self.design
        tv = math.tan(math.radians(DISPLAY_FOV) / 2.0)
        th = tv * max(v.width(), 1) / max(v.height(), 1)
        face = 1.12 * max(0.5 * d.W / th, 0.5 * d.H / tv)
        return face, max(face, 1.06 * 0.5 * math.hypot(d.W, d.H) / min(tv, th))

    def _pose(self, k):
        """(yaw, pitch, turn) for reading message k from its designed side."""
        d = self.design
        p = d.p
        # a little past the design angle, so the far edge of the sign is past it too
        spread = math.degrees(math.atan(0.5 * d.U / self._distances()[0]))
        a = min(p.view_angle + spread + 2.0, 85.0)
        side = 1.0 if p.view_side == 0 else -1.0          # above / the left first
        if k == MESSAGE_2 and not p.flip:
            side = -side
        turn = 180.0 if (k == MESSAGE_2 and p.flip) else 0.0
        if d.vertical:
            return -90.0 - side * a, 0.0, turn
        return -90.0, side * a, turn

    def show_view(self, idx):
        """Go to one of the preset views (a view button, or Reset view)."""
        swing = idx != PRINT_LAYOUT and self.view != PRINT_LAYOUT      # between the two messages
        self.view = idx
        self._free_camera = False
        self._fitted = None
        self.view_btns[idx].setChecked(True)
        self._apply_view(animate=swing)

    def _apply_view(self, animate=False):
        """Place the sign and the camera for the current view.

        A camera the user has dragged somewhere is left alone, apart from
        stepping back when the sign has changed size.
        """
        d, v = self.design, self.viewer
        if d is None:
            return
        self._stop_anim()
        standing = self.view != PRINT_LAYOUT
        v.show_bed = not standing
        v.fov = DISPLAY_FOV if standing else PRINT_FOV
        if standing:
            v.set_model(standing_transform(self.turn, 0.5 * d.top))
        else:
            lie = np.identity(4)
            if d.turn_on_bed:                 # a quarter turn, as it is exported
                lie[:2, :2] = [[0, -1], [1, 0]]
            v.set_model(lie)
        size = float(np.linalg.norm([d.W, d.H, d.top]))
        if (self._fitted is None or self._fitted[0] != standing
                or abs(size - self._fitted[1]) > 0.1 * self._fitted[1]):
            if standing:
                v.target, v.dist = np.zeros(3), self._distances()[0]
            else:
                v.fit_view()
            self._fitted = (standing, size)
        if self._free_camera:
            return
        if not standing:
            v.yaw, v.pitch = -62.0, 38.0
            v.update()
            return
        yaw, pitch, turn = self._pose(self.view)
        if animate:
            self.anim = dict(t0=time.perf_counter(), start=(v.yaw, v.pitch, self.turn),
                             end=(yaw, pitch, turn))
            self.anim_timer.start()
        else:
            v.yaw, v.pitch, self.turn = yaw, pitch, turn
            v.set_model(standing_transform(turn, 0.5 * d.top))

    def _stop_anim(self):
        """End a swing early: the sign finishes its turn at once."""
        if self.anim is not None:
            self.anim_timer.stop()
            self.turn = self.anim["end"][2]
            self.anim = None
            if self.design is not None and self.view != PRINT_LAYOUT:
                self.viewer.set_model(standing_transform(self.turn, 0.5 * self.design.top))

    def _anim_tick(self):
        a, d = self.anim, self.design
        if a is None or d is None:
            self.anim_timer.stop()
            return
        t = min((time.perf_counter() - a["t0"]) / TURN_TIME, 1.0)
        e = t * t * (3.0 - 2.0 * t)                       # ease in and out
        yaw, pitch, turn = (s + (f - s) * e for s, f in zip(a["start"], a["end"]))
        self.viewer.yaw, self.viewer.pitch, self.turn = yaw, pitch, turn
        if a["start"][2] != a["end"][2]:                  # step back while the sign turns round
            face, clear = self._distances()
            self.viewer.dist = face + (clear - face) * math.sin(math.pi * e)
        self.viewer.set_model(standing_transform(turn, 0.5 * d.top))
        if t >= 1.0:
            self.anim_timer.stop()
            self.anim = None
            if self._fitted is None:        # the view changed shape meanwhile
                self._apply_view()

    def _on_dragged(self):
        self._stop_anim()
        self._free_camera = True

    def _on_resized(self):
        if self._free_camera:
            return
        self._fitted = None                 # the view has a new shape: fit the sign to it again
        if self.anim is None:               # (a swing in progress does so when it ends)
            self._apply_view()

    # ============================================================ files
    def _last_dir(self):
        return self.settings.value("last_dir", os.path.join(os.path.expanduser("~"), "Documents"))

    def _remember_dir(self, path):
        self.settings.setValue("last_dir", os.path.dirname(path))

    def _safe_name(self):
        name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in self.params.name).strip()
        return name or "sign"

    def new_design(self):
        if not self._confirm_discard():
            return
        self.params = self._fresh_params()
        self.current_file = None
        self._dirty = False
        self._refresh_widgets()
        self.regenerate()

    def _confirm_discard(self):
        if not self._dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes",
                                 "Discard unsaved changes to the current design?",
                                 QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        return r == QMessageBox.Discard

    def open_design(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open design", self._last_dir(),
            f"Lenticular sign designs (*{FILE_EXT} *.json);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            params = load_design(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"Could not open the design:\n{exc}")
            return
        self._remember_dir(path)
        self.params = params
        self.current_file = path
        self._dirty = False
        self._refresh_widgets()
        self.regenerate()
        self.statusBar().showMessage(f"Opened {path}", 6000)

    def save_design(self, as_new=False):
        path = self.current_file
        if as_new or not path:
            default = os.path.join(self._last_dir(), self._safe_name() + FILE_EXT)
            path, _ = QFileDialog.getSaveFileName(self, "Save design", default,
                                                  f"Lenticular sign designs (*{FILE_EXT})")
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path += FILE_EXT
        try:
            save_design(path, self.params)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._remember_dir(path)
        self.current_file = path
        self._dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 6000)

    def _current_design(self):
        """The design to export, rebuilt first if a change is still waiting."""
        if self.regen_timer.isActive():
            self.regen_timer.stop()
            self.regenerate()
        d = self.design
        if d is None or {**d.p.to_dict(), "name": ""} != {**self.params.to_dict(), "name": ""}:
            QMessageBox.warning(self, "Nothing to export", "The current settings do not make a sign.")
            return None
        return d

    def export_3mf(self):
        """The sign as one object with a part per filament, placed on the printer bed."""
        d = self._current_design()
        if d is None:
            return
        base = self._safe_name()
        path, _ = QFileDialog.getSaveFileName(self, "Export 3MF",
                                              os.path.join(self._last_dir(), base + ".3mf"),
                                              "3MF files (*.3mf)")
        if not path:
            return
        if not path.lower().endswith(".3mf"):
            path += ".3mf"
        parts, fits = sign_parts(d)
        try:
            write_3mf(path, base, parts, (d.p.bed_x / 2.0, d.p.bed_y / 2.0, 0.0))
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        msg = (f"Exported {os.path.basename(path)} ({len(parts)} filaments) to {os.path.dirname(path)}"
               " — in Flash Studio / OrcaSlicer load it with File → Import (Ctrl+I)")
        if not fits:
            msg += " — the sign is bigger than the bed"
        self.statusBar().showMessage(msg, 12000)

    def export_stl(self):
        """One STL per filament, sharing coordinates so they line up when loaded together."""
        d = self._current_design()
        if d is None:
            return
        base = self._safe_name()
        tags = ", ".join(f"_filament{f}.stl" for f in sorted(d.bodies))
        path, _ = QFileDialog.getSaveFileName(self, f"Export STLs (writes {tags})",
                                              os.path.join(self._last_dir(), base + ".stl"),
                                              "STL files (*.stl)")
        if not path:
            return
        root = path[:-4] if path.lower().endswith(".stl") else path
        written = []
        try:
            for name, verts, faces, fil, _ in sign_parts(d)[0]:
                out = f"{root}_filament{fil}.stl"
                write_stl(out, verts, faces, f"{base} filament {fil}")
                written.append(out)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        names = ", ".join(os.path.basename(w) for w in written)
        self.statusBar().showMessage(
            f"Exported {names} to {os.path.dirname(path)} — load them together as one object "
            f"with multiple parts and give each its filament", 12000)

    def save_snapshot(self):
        default = os.path.join(self._last_dir(), self._safe_name() + "_3d.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save picture", default, "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self.viewer.grabFramebuffer().save(path)
        self._remember_dir(path)
        self.statusBar().showMessage(f"Saved picture to {path}", 8000)

    def about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Makes signs that show one message from one side and another from the other, "
            "for a multi-colour 3D printer. The face is a row of ridges: one flank of every "
            "ridge carries the first message and the other flank the second.</p>"
            "<p><b>3D view:</b> left-drag tilt · right-drag / shift-drag pan · wheel zoom · "
            "double-click or F to go back to the chosen view.</p>"
            "<p>Exports are in millimetres, flat on the back and ready to print.</p>")

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(ev)


# ============================================================== bootstrap
def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(36, 39, 45))
    pal.setColor(QPalette.WindowText, c(222, 225, 230))
    pal.setColor(QPalette.Base, c(26, 28, 33))
    pal.setColor(QPalette.AlternateBase, c(44, 47, 54))
    pal.setColor(QPalette.ToolTipBase, c(52, 56, 64))
    pal.setColor(QPalette.ToolTipText, c(230, 230, 230))
    pal.setColor(QPalette.Text, c(222, 225, 230))
    pal.setColor(QPalette.Button, c(50, 54, 62))
    pal.setColor(QPalette.ButtonText, c(222, 225, 230))
    pal.setColor(QPalette.BrightText, c(255, 90, 90))
    pal.setColor(QPalette.Highlight, c(56, 132, 214))
    pal.setColor(QPalette.HighlightedText, c(255, 255, 255))
    pal.setColor(QPalette.Link, c(110, 180, 255))
    pal.setColor(QPalette.PlaceholderText, c(130, 135, 145))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, c(120, 124, 132))
    app.setPalette(pal)
    app.setStyleSheet("""
        QGroupBox { font-weight: 600; border: 1px solid #3b4049; border-radius: 6px;
                    margin-top: 12px; padding-top: 4px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #f0a356; }
        QTextBrowser { border: none; }
        QToolBar { border: none; spacing: 4px; padding: 3px; }
        QSplitter::handle { background: #2b2e34; }
        QPushButton#modeButton { border: 1px solid #3b4049; padding: 4px 12px; background: #2a2d33; }
        QPushButton#modeButton:checked { background: #3884d6; color: white; border-color: #3884d6; }
    """)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(8)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("LenticularSign")
    apply_dark_theme(app)
    settings = None
    if "--screenshot" in argv:          # a test run keeps its hands off the remembered settings
        import tempfile
        settings = QSettings(os.path.join(tempfile.gettempdir(), "LenticularSignMaker-test.ini"),
                             QSettings.IniFormat)
        settings.clear()
    win = MainWindow(settings)
    win.show()

    # optional: open a design given on the command line
    valued = ("--screenshot", "--view", "--export-test", "--yaw", "--pitch", "--zoom")
    files = [a for i, a in enumerate(argv) if i > 0 and not a.startswith("--")
             and argv[i - 1] not in valued and os.path.isfile(a)]
    if files:
        win.load_file(files[0])

    # developer hook: --screenshot out.png [--view N] [--yaw a] [--pitch a] [--zoom k]
    if "--screenshot" in argv:
        out = argv[argv.index("--screenshot") + 1]
        win.set_simple_mode("--detailed" not in argv, remember=False)
        if "--view" in argv:
            win.show_view(int(argv[argv.index("--view") + 1]))
        if "--export-test" in argv:          # write every export, without dialogs
            out_dir = argv[argv.index("--export-test") + 1]
            d = win.design
            parts, _ = sign_parts(d)
            write_3mf(os.path.join(out_dir, "t.3mf"), "t", parts, (d.p.bed_x / 2, d.p.bed_y / 2, 0.0))
            for name, verts, faces, fil, _ in parts:
                write_stl(os.path.join(out_dir, f"t_filament{fil}.stl"), verts, faces, name)

        def snap():
            win._stop_anim()
            win._apply_view(animate=False)
            for flag, attr in (("--yaw", "yaw"), ("--pitch", "pitch")):
                if flag in argv:
                    setattr(win.viewer, attr, float(argv[argv.index(flag) + 1]))
            if "--zoom" in argv:
                win.viewer.dist /= float(argv[argv.index("--zoom") + 1])
            win.viewer.repaint()
            win.grab().save(out)
            app.quit()
        QTimer.singleShot(1800, snap)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
