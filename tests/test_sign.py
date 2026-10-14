"""Self-tests: closed meshes, the right volume, and the right message from each side.

Run:  .venv\\Scripts\\python -m tests.test_sign

Run it on Qt's normal platform: the "offscreen" one has no fonts, so every
letter comes out as a box.
"""

import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lenticular.geometry import MINUS, PLUS, SignDesign, edge_check, mesh_volume  # noqa: E402
from lenticular.params import SignParams, load_design, save_design  # noqa: E402
from lenticular.stl import write_stl  # noqa: E402
from lenticular.textimage import _ensure_app  # noqa: E402
from lenticular.threemf import read_3mf, write_3mf  # noqa: E402
from lenticular.viewing import angle_to_use, seen_angles  # noqa: E402


def look(d: SignDesign, direction, turned=False, px=0.25):
    """What an eye far away in ``direction`` sees: filament numbers on a grid.

    A plain depth-buffer drawing of the real mesh, straight from the triangles.
    direction: from the sign towards the eye, in print coordinates (the sign's
    face looks along +z, its top edge is +y).  turned: the sign is first turned
    upside down in its own plane.  Returns (picture, heights, right, up): the
    picture's rows run down the sign, ``heights`` is how far above the bed each
    seen point is, and ``right`` and ``up`` are the picture's axes.
    """
    eye = np.array(direction, dtype=float)
    eye /= np.linalg.norm(eye)
    right = np.array([1.0, 0.0, 0.0])
    right -= eye * (right @ eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    half = 0.5 * math.hypot(d.W + 2 * d.reach[0], d.H + 2 * d.reach[1]) + 2.0
    n = int(2 * half / px)
    depth = np.full((n, n), -np.inf)
    seen = np.zeros((n, n), dtype=np.int8)
    heights = np.zeros((n, n))
    for fil, (verts, faces) in d.bodies.items():
        v = verts * (-1.0, -1.0, 1.0) if turned else verts
        tri = v[faces]
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        tri = tri[nrm @ eye > 1e-9]                       # faces turned away are never seen
        X = (tri @ right + half) / px
        Y = (half - tri @ up) / px
        Z = tri @ eye
        for x, y, z, high in zip(X, Y, Z, tri[:, :, 2]):
            c0, c1 = max(int(math.floor(x.min())), 0), min(int(math.ceil(x.max())), n - 1)
            r0, r1 = max(int(math.floor(y.min())), 0), min(int(math.ceil(y.max())), n - 1)
            if c1 < c0 or r1 < r0:
                continue
            gx, gy = np.meshgrid(np.arange(c0, c1 + 1) + 0.5, np.arange(r0, r1 + 1) + 0.5)
            den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
            if abs(den) < 1e-12:
                continue
            a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
            b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
            c = 1.0 - a - b
            zz = a * z[0] + b * z[1] + c * z[2]
            sub_d, sub_s = depth[r0:r1 + 1, c0:c1 + 1], seen[r0:r1 + 1, c0:c1 + 1]
            hit = (a >= 0) & (b >= 0) & (c >= 0) & (zz > sub_d)
            sub_d[hit] = zz[hit]
            sub_s[hit] = fil
            heights[r0:r1 + 1, c0:c1 + 1][hit] = (a * high[0] + b * high[1] + c * high[2])[hit]
    return seen, heights, right, up


def match(d: SignDesign, k: int, direction, turned=False, px=0.25):
    """Share of the text area where the eye sees message k as it should read.

    Plain ridge tops belong to neither message, so they are left out.
    """
    p = d.p
    seen, heights, right, up = look(d, direction, turned, px)
    n = seen.shape[0]
    half = 0.5 * n * px
    # where each pixel's line of sight meets the sign's face (the plane of the ridge tops)
    eye = np.array(direction, dtype=float)
    eye /= np.linalg.norm(eye)
    cols, rows = np.meshgrid(np.arange(n) + 0.5, np.arange(n) + 0.5)
    pts = ((cols * px - half)[..., None] * right + (half - rows * px)[..., None] * up)
    t = (d.top - 0.5 * d.h - pts[..., 2]) / eye[2]
    x = pts[..., 0] + t * eye[0]
    y = pts[..., 1] + t * eye[1]
    img = d.message_image(k)                              # the message as its reader expects it
    col = np.clip(((x + 0.5 * d.W) / d.W * img.shape[1]).astype(int), 0, img.shape[1] - 1)
    row = np.clip(((0.5 * d.H - y) / d.H * img.shape[0]).astype(int), 0, img.shape[0] - 1)
    want_text = img[row, col] >= 0.5
    text_fil, back_fil = (p.text_fil1, p.back_fil1) if k == 0 else (p.text_fil2, p.back_fil2)
    want = np.where(want_text, text_fil, back_fil)
    inside = d.in_text_area(x, y) & (seen > 0)
    if d.cap > 0:
        inside &= heights < d.top - d.cap - 1e-6
    return float((seen[inside] == want[inside]).mean()), float(want_text[inside].mean())


def eye_for(d: SignDesign, k: int, extra=4.0):
    """(direction, turned) for looking at message k from its designed side."""
    p = d.p
    a = math.radians(p.view_angle + extra)
    sign = 1.0 if p.view_side == 0 else -1.0             # above / the left first
    turned = bool(k == 1 and p.flip)
    if k == 1 and not p.flip:
        sign = -sign
    if d.vertical:
        return (-sign * math.sin(a), 0.0, math.cos(a)), turned
    return (0.0, sign * math.sin(a), math.cos(a)), turned


def heights_at(d: SignDesign, x: float, y: float) -> list:
    """Heights at which a line straight up through (x, y) meets the mesh, lowest first."""
    found = []
    for verts, faces in d.bodies.values():
        tri = verts[faces]
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(den) > 1e-12
        den = np.where(ok, den, 1.0)
        la = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / den
        lb = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / den
        hit = ok & (la >= 0) & (lb >= 0) & (la + lb <= 1)
        found += (la * a[:, 2] + lb * b[:, 2] + (1 - la - lb) * c[:, 2])[hit].tolist()
    return sorted(found)


def ink_middle(img: np.ndarray, d: SignDesign):
    """Middle of a mask's ink, in mm from the sign's centre (x right, y up)."""
    rows, cols = np.nonzero(img >= 0.5)
    return ((cols.mean() + 0.5) / img.shape[1] - 0.5) * d.W, (0.5 - (rows.mean() + 0.5) / img.shape[0]) * d.H


def check_meshes(p: SignParams, label: str) -> SignDesign:
    d = SignDesign(p)
    assert not d.errors, (label, d.errors)
    total = 0.0
    for fil, (verts, faces) in d.bodies.items():
        opened, shared = edge_check(faces)
        assert opened == 0 and shared == 0, (label, fil, opened, shared)
        vol = mesh_volume(verts, faces)
        assert vol > 0, (label, fil, vol)
        total += vol
        assert verts[:, 2].min() >= -1e-9 and verts[:, 2].max() <= d.top + 1e-9
        assert np.abs(verts[:, 0]).max() <= 0.5 * d.W + d.reach[0] + 1e-9
        assert np.abs(verts[:, 1]).max() <= 0.5 * d.H + d.reach[1] + 1e-9
        if not (d.tabs and fil == p.base_fil):            # ... nor past the round corners
            cx = np.maximum(np.abs(verts[:, 0]) - (0.5 * d.W - d.r), 0.0)
            cy = np.maximum(np.abs(verts[:, 1]) - (0.5 * d.H - d.r), 0.0)
            assert (np.hypot(cx, cy) <= d.r + 1e-6).all(), (label, fil)
    # square corners are exact; anything round is flat facets inside the true curve
    slack = 1e-6 if d.r <= 0 and not d.pockets and not d.tabs else 4e-3
    assert -slack * total < d.solid_volume() - total < slack * total, (label, total, d.solid_volume())
    assert min(verts[:, 2].min() for verts, _ in d.bodies.values()) == 0.0
    print(f"{label}: {d.prisms} shells, {d.triangles} triangles, closed, "
          f"volume {total / 1000:.2f} cm3 as designed  OK")
    return d


def check_views(d: SignDesign, label: str, need=0.86):
    for k in (0, 1):
        if d.shapes[k].empty:
            continue
        eye, turned = eye_for(d, k)
        good, ink = match(d, k, eye, turned)
        other, _ = match(d, 1 - k, eye, turned)
        assert 0.03 < ink < 0.9, (label, k, ink)
        assert good >= need, f"{label}: message {k + 1} only {good:.0%} right {d.seen(k)}"
        # the wrong message would get the letters wrong: a gap that grows with how much ink there is
        assert good - other > 0.5 * ink, (label, k, good, other, ink)
        print(f"{label}: message {k + 1} '{d.label(k)}' reads {good:.0%} right {d.seen(k)} "
              f"(the other message would score {other:.0%})  OK")


def check_files(d: SignDesign):
    p = d.p
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sign.3mf")
        parts = [(d.part_name(f), v, fc, f, p.colors()[f]) for f, (v, fc) in sorted(d.bodies.items())]
        write_3mf(path, p.name, parts, (p.bed_x / 2, p.bed_y / 2, 0.0))
        back = read_3mf(path)
        assert [b[3] for b in back] == sorted(d.bodies), [b[3] for b in back]
        for (name, v, fc, fil, pos), part in zip(back, parts):
            assert name == part[0] and pos == (p.bed_x / 2, p.bed_y / 2, 0.0)
            assert np.allclose(v, part[1], atol=1e-4) and (fc == part[2]).all()
        write_stl(os.path.join(tmp, "a.stl"), *d.bodies[p.base_fil])
        size = os.path.getsize(os.path.join(tmp, "a.stl"))
        assert size == 84 + 50 * len(d.bodies[p.base_fil][1])
        save_design(os.path.join(tmp, "a.lenticular"), p)
        assert load_design(os.path.join(tmp, "a.lenticular")) == p
    print(f"3MF ({len(parts)} parts, filaments, position), STL and design file round trips  OK")


def check_shapes(d: SignDesign):
    """Round outlines: corners, semicircular ends, on the way to a circle, a circle."""
    for roundness, label in ((0.4, "rounded corners"), (1.0, "semicircular ends"),
                             (1.5, "half way to a circle"), (2.0, "circle")):
        check_views(check_meshes(SignParams(roundness=roundness), label), label)
    pill, disc = SignDesign(SignParams(roundness=1.0)), SignDesign(SignParams(roundness=2.0))
    assert (pill.W, pill.H, pill.r) == (200.0, 60.0, 30.0) and pill.shape_name() == "semicircular ends"
    assert (disc.W, disc.H, disc.r) == (200.0, 200.0, 100.0) and disc.shape_name() == "a circle"
    assert pill.cap_mm[0] < d.cap_mm[0]                   # letters shrink to stay inside round ends
    assert any("height is 200 mm" in note for note in disc.notes), disc.notes
    check_views(check_meshes(SignParams(roundness=1.0, ridge_dir=1, flip=False), "round ends, upright ridges"),
                "round ends, left / right", need=0.80)
    check_views(check_meshes(SignParams(width=60.0, height=200.0, roundness=1.0, text_dir1=1, text_dir2=1),
                             "tall sign, round ends"), "tall, round ends", need=0.80)
    check_meshes(SignParams(roundness=0.05), "corners tighter than the frame")
    check_meshes(SignParams(roundness=2.0, frame=0.0, width=100.0), "circle, no frame")
    check_meshes(SignParams(roundness=1.0, text_fil1=1, back_fil1=2, text_fil2=1, back_fil2=3,
                            base_fil=2, crest_fil=0), "round ends, coloured grounds, shared tops")
    check_meshes(SignParams(roundness=2.0, width=60.0, height=60.0, pitch=5.0), "small circle, wide ridges")


def check_mounting():
    """Magnet pockets in the back, and eyelets to hang the sign by."""
    one = check_meshes(SignParams(mount=1), "one magnet in the middle, to spin the sign on")
    assert one.pockets == [(0.0, 0.0)] and "1 pocket " in one.mounting()
    mag = check_meshes(SignParams(mount=1, mount_count=2), "two magnets")
    assert len(mag.pockets) == 2 and abs(mag.pocket_depth - 2.2) < 1e-9 and abs(mag.pocket_r - 5.1) < 1e-9
    assert mag.T >= mag.pocket_depth + 0.6 - 1e-9 and any("magnet pockets" in n for n in mag.notes)
    assert "2 pockets" in mag.info()["Mounting"]
    x, y = mag.pockets[0]
    assert abs(heights_at(mag, x + 0.013, y + 0.021)[0] - mag.pocket_depth) < 1e-9    # open underneath
    assert abs(heights_at(mag, x + 4.9, y + 0.021)[0] - mag.pocket_depth) < 1e-9      # ... right across
    assert heights_at(mag, x + 5.3, y + 0.021)[0] == 0.0                              # solid round it
    assert heights_at(mag, 0.013, 0.021)[0] == 0.0
    check_views(mag, "two magnets")                       # the face is as it was
    for kw, label in ((dict(mount_count=4), "four magnets"), (dict(mount_count=1, roundness=2.0), "one magnet on a circle"),
                      (dict(mount_count=4, roundness=2.0, width=120.0), "four magnets on a circle"),
                      (dict(mount_count=2, roundness=1.0, ridge_dir=1), "two magnets, round ends, upright ridges"),
                      (dict(mount_count=4, roundness=0.5, magnet_d=6.0, magnet_t=3.0), "four small thick magnets")):
        m = check_meshes(SignParams(mount=1, **kw), label)
        assert len(m.pockets) == kw["mount_count"], (label, m.pockets, m.warnings)
        for x, y in m.pockets:
            assert abs(heights_at(m, x + 0.013, y + 0.021)[0] - m.pocket_depth) < 1e-9, label
    crowded = SignDesign(SignParams(mount=1, mount_count=4, magnet_d=30.0))
    assert not crowded.pockets and any("do not fit" in w for w in crowded.warnings)

    hung = check_meshes(SignParams(mount=2, mount_count=1), "eyelets, top and bottom")
    assert len(hung.tabs) == 2 and hung.reach == (0.0, 8.5), hung.reach
    (_, _, (cx, cy)) = hung.tabs[0]
    assert heights_at(hung, cx + 0.013, cy + 0.021) == []                             # the hole is open
    rim = heights_at(hung, cx + 3.5, cy + 0.021)
    assert rim[0] == 0.0 and abs(rim[-1] - hung.T) < 1e-9                             # its rim is solid
    assert len(check_meshes(SignParams(mount=2, mount_count=1, flip=False), "one eyelet").tabs) == 1
    side = check_meshes(SignParams(mount=2, mount_count=2), "eyelets at the sides")
    assert side.reach == (8.5, 0.0) and "each side" in side.mounting()
    wide = SignDesign(SignParams(mount=2, mount_count=2, width=250.0))
    assert not wide.fits_bed and any("267" in w for w in wide.warnings), wide.warnings
    for kw, label in ((dict(roundness=2.0, width=120.0, mount_count=1), "eyelets on a circle"),
                      (dict(roundness=1.0, mount_count=2), "eyelets on round ends"),
                      (dict(roundness=1.0, mount_count=2, ridge_dir=1), "eyelets, upright ridges")):
        check_meshes(SignParams(mount=2, **kw), label)


def check_pictures(tmp: str):
    """A picture with the text, or instead of it."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

    _ensure_app()
    dot = os.path.join(tmp, "dot.png")                    # no see-through parts: dark is ink
    img = QImage(200, 200, QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("black"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QPointF(100, 100), 70, 70)
    painter.end()
    img.save(dot)
    tick = os.path.join(tmp, "tick.png")                  # see-through: whatever is solid is ink
    img = QImage(300, 200, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setBrush(QColor(255, 200, 0))                 # a light colour, but solid
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(QPolygonF([QPointF(20, 110), QPointF(60, 70), QPointF(110, 120), QPointF(240, 20),
                                   QPointF(280, 60), QPointF(110, 190)]))
    painter.end()
    img.save(tick)
    ring = os.path.join(tmp, "ring.svg")
    with open(ring, "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                 '<circle cx="50" cy="50" r="35" fill="none" stroke="black" stroke-width="18"/></svg>')

    def share(d, k):                                      # how much of the ink's box is ink
        on = d.message_image(k) >= 0.5
        rows, cols = np.nonzero(on.any(axis=1))[0], np.nonzero(on.any(axis=0))[0]
        box = on[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        return float(box.mean()), box.shape[1] / box.shape[0]

    d = check_meshes(SignParams(text1="", image1=dot, text2="", image2=tick), "two pictures")
    assert d.label(0) == "dot" and d.label(1) == "tick" and not d.warnings, d.warnings
    filled, aspect = share(d, 0)
    assert abs(filled - math.pi / 4) < 0.02 and abs(aspect - 1.0) < 0.03, (filled, aspect)   # a disc
    assert abs(share(d, 1)[1] - 260 / 170) < 0.05                                             # the tick
    check_views(d, "two pictures")
    hole, aspect = share(SignDesign(SignParams(text1="", image1=dot, image_invert1=True)), 0)
    assert abs(hole - (1 - math.pi * 70 ** 2 / 200 ** 2)) < 0.02 and abs(aspect - 1.0) < 0.03     # its surround
    filled, aspect = share(SignDesign(SignParams(text1="", image1=ring)), 0)
    assert 0.5 < filled < 0.7 and abs(aspect - 1.0) < 0.03, (filled, aspect)                  # an SVG ring
    word = SignDesign(SignParams(size1=18.0)).shapes[0].rect
    for at, wider in ((0, True), (1, True), (2, False), (3, False)):
        both = SignDesign(SignParams(size1=18.0, image1=dot, image_at1=at))
        assert not both.warnings and both.label(0) == "CLEAN", both.warnings
        box = both.shapes[0].rect
        assert (box.width() > word.width() + 100) == wider and (box.height() > word.height() + 100) != wider
    check_views(check_meshes(SignParams(image1=tick, image2=dot, image_at2=1), "pictures beside the words"),
                "pictures beside the words")
    lost = SignDesign(SignParams(image1=os.path.join(tmp, "nothing.png")))
    assert any("could not be used" in w for w in lost.warnings) and lost.label(0) == "CLEAN"
    kept = os.path.join(tmp, "kept.lenticular")           # a picture beside its design moves with it
    save_design(kept, SignParams(image1=dot, image2=os.path.join(tmp, "nothing.png")))
    with open(kept, encoding="utf-8") as fh:
        assert '"image1": "dot.png"' in fh.read()
    assert os.path.samefile(load_design(kept).image1, dot)
    print("pictures: dark-is-ink, solid-is-ink, inverted, SVG, four placements, a missing file  OK")


def check_layout():
    """Each message moved, spaced and turned on its own."""
    plain = SignDesign(SignParams(size1=20.0, size2=20.0))
    moved = SignDesign(SignParams(size1=20.0, size2=20.0, dx1=30.0, dy1=5.0, dx2=-20.0, dy2=-6.0))
    assert not moved.warnings, moved.warnings
    for k, shift in ((0, (30.0, 5.0)), (1, (-20.0, -6.0))):
        was, now = ink_middle(plain.message_image(k), plain), ink_middle(moved.message_image(k), moved)
        assert abs(now[0] - was[0] - shift[0]) < 0.1 and abs(now[1] - was[1] - shift[1]) < 0.1, (k, was, now)
    # message 2 lies upside down on a sign that is turned over, moved the opposite way
    lying, read = ink_middle(moved.images[1], moved), ink_middle(moved.message_image(1), moved)
    assert abs(lying[0] + read[0]) < 0.1 and abs(lying[1] + read[1]) < 0.1
    check_views(check_meshes(SignParams(size1=20.0, size2=20.0, dx1=30.0, dy1=5.0, dx2=-20.0, dy2=-6.0),
                             "both messages moved"), "moved")
    far = SignDesign(SignParams(size1=30.0, dx1=80.0))
    assert any("where it has been moved" in w for w in far.warnings), far.warnings
    shrunk = SignDesign(SignParams(dx1=40.0))             # Auto shrinks to stay inside where it is
    assert not shrunk.warnings and shrunk.cap_mm[0] < SignDesign(SignParams()).cap_mm[0]

    wide = SignDesign(SignParams(size1=20.0, spacing1=40.0)).shapes[0].rect.width()
    tight = SignDesign(SignParams(size1=20.0, spacing1=-10.0)).shapes[0].rect.width()
    assert tight < plain.shapes[0].rect.width() < wide
    mixed = check_meshes(SignParams(width=120.0, height=120.0, text_dir2=1), "one across, one stacked")
    assert mixed.shapes[0].rect.width() > mixed.shapes[0].rect.height()
    assert mixed.shapes[1].rect.height() > mixed.shapes[1].rect.width()
    check_views(mixed, "across and stacked", need=0.80)
    old = SignParams.from_dict({"text_dir": 2, "width": 80})      # a design from before
    assert (old.text_dir1, old.text_dir2, old.width) == (2, 2, 80.0)
    assert SignParams.from_dict({"base_fil": 9, "mount": 7, "color4": "blue"}) == SignParams()
    print("layout: moved, spaced, one across and one stacked, old designs  OK")


def check_flat():
    """Ridges only where needed: grooves filled in where both messages show the same colour."""
    d = check_meshes(SignParams(ridges_where=2), "ridges only where needed")
    assert 0.3 < d.flat_share() < 0.95 and "Only where needed" in d.info()["Ridges"], d.flat_share()
    assert SignDesign(SignParams()).flat_area == 0.0
    # walk the grooves: filled exactly where the flanks either side of them match
    filled = left = 0
    for j in range(d.n - 1):
        y = float(d.ridge_u[j + 1]) - 0.5 * d.H + 0.02     # just off the bottom of the groove
        for x in np.arange(-90.0, 90.0, 7.3):
            v = x + 0.5 * d.W
            edges = [e for side, r in ((PLUS, j), (MINUS, j + 1)) for run in d.runs[side][r] for e in run]
            if any(abs(v - e) < 0.3 for e in edges):       # too close to a letter's edge to call
                continue
            same = d.flank_fil(j, PLUS, v) == d.flank_fil(j + 1, MINUS, v)
            assert (abs(heights_at(d, x, y)[-1] - d.top) < 1e-9) == same, (j, x, same)
            filled += same
            left += not same
    assert filled > 100 and left > 30, (filled, left)
    # against the frame too, where it matches it: flat right to the edge
    assert abs(heights_at(d, 0.013, -0.5 * d.H + d.b + 0.02)[-1] - d.top) < 1e-9
    check_views(d, "ridges only where needed")
    grounds = check_meshes(SignParams(ridges_where=2, text_fil1=1, back_fil1=2, text_fil2=1, back_fil2=3,
                                      base_fil=2), "only where needed, coloured grounds")
    assert 0.0 < grounds.flat_share() < 0.2                # only where two white letters cross
    check_views(grounds, "only where needed, coloured grounds")
    disc = check_meshes(SignParams(ridges_where=2, roundness=2.0, width=120.0), "only where needed, circle")
    assert disc.flat_share() > 0.5
    check_views(disc, "only where needed, circle")
    for kw, label in ((dict(roundness=1.0, ridge_dir=1, flip=False), "round ends, upright ridges"),
                      (dict(frame=0.0), "no frame"), (dict(crest_fil=0), "shared ridge tops"),
                      (dict(text2=""), "one message"), (dict(roundness=0.3, mount=1, base_fil=4), "magnet, black back"),
                      (dict(text1="", text2=""), "nothing on it")):
        check_meshes(SignParams(ridges_where=2, **kw), "only where needed, " + label)
    # a block round the messages: every groove inside it is kept, everything outside is flat
    blk = check_meshes(SignParams(ridges_where=1), "ridges in a block round the messages")
    first, last, v0, v1 = blk.block
    assert 0.1 < blk.flat_share() < d.flat_share() and "block" in blk.info()["Ridges"]
    for j in range(blk.n - 1):
        y = float(blk.ridge_u[j + 1]) - 0.5 * blk.H + 0.02
        for x in np.arange(-93.0, 93.0, 5.1):
            v = x + 0.5 * blk.W
            if abs(v - v0) < 0.3 or abs(v - v1) < 0.3:
                continue
            inside = first - 1 <= j <= last and v0 < v < v1
            assert (abs(heights_at(blk, x, y)[-1] - blk.top) < 1e-9) != inside, (j, x, inside)
    check_views(blk, "ridges in a block")
    for kw, label in ((dict(roundness=2.0, width=120.0), "circle"), (dict(ridge_dir=1, flip=False), "upright ridges"),
                      (dict(roundness=1.0, mount=2, mount_count=2), "round ends, eyelets")):
        check_meshes(SignParams(ridges_where=1, **kw), "ridges in a block, " + label)
    odd = SignDesign(SignParams(ridges_where=1, back_fil2=3))
    assert odd.flat_share() < 0.03 and any("Hardly any grooves" in n for n in odd.notes)
    blank = SignDesign(SignParams(ridges_where=2, text1="", text2=""))
    assert blank.flat_share() > 0.99                       # nothing to show: all flat


def check_viewing():
    """The angle a sign is really seen from."""
    near, middle, far = seen_angles(60.0, 800.0, 600.0)   # 80 cm below the eye, from 60 cm out
    assert abs(middle - math.degrees(math.atan(800 / 600))) < 1e-9 and near < middle < far
    assert seen_angles(60.0, -800.0, 600.0) == (near, middle, far)        # above or below alike
    assert angle_to_use(60.0, 800.0, 600.0) == math.floor(near - 5.0) == 47.0
    assert angle_to_use(60.0, 50.0, 1000.0) is None       # as good as straight-on
    assert angle_to_use(10.0, 5000.0, 100.0) == 75.0      # never past what the ridges can do
    print("viewing angles  OK")


def main():
    d = check_meshes(SignParams(), "CLEAN / DIRTY, turned over")
    assert d.n == 18 and abs(d.flank_angle - 60.0) < 1e-9, (d.n, d.flank_angle)
    assert sorted(d.bodies) == [1, 2, 3]
    assert abs(d.cap_mm[0] - d.cap_mm[1]) < 1e-9          # both "as big as fits": same height
    assert abs(d.top - d.layers * 0.2) < 1e-9 and abs(d.T - 2.0) <= 0.1 + 1e-9     # whole layers
    for fil in (2, 3):                                    # the tops of the ridges are plain
        assert abs(d.bodies[fil][0][:, 2].max() - (d.top - d.cap)) < 1e-9
    # ... from where each flank's half of the ridge is still a full printed line wide
    assert abs(0.5 * d.c + d.cap / math.tan(math.radians(d.flank_angle)) - d.p.line_width) < 1e-9
    check_views(d, "turned over, from above")
    check_files(d)
    shared = check_meshes(SignParams(crest_fil=0), "ridge tops half from each message")
    assert max(shared.bodies[f][0][:, 2].max() for f in (2, 3)) == shared.top
    check_views(shared, "shared ridge tops")

    check_views(check_meshes(SignParams(flip=False), "no flip"), "above / below")
    check_views(check_meshes(SignParams(view_side=1), "from below"), "turned over, from below")
    check_views(check_meshes(SignParams(ridge_dir=1, flip=False, height=80.0), "upright ridges"),
                "left / right", need=0.80)
    check_views(check_meshes(SignParams(ridge_dir=1, view_side=1, height=80.0), "upright, turned"),
                "turned over, from the right", need=0.80)
    for text_dir in (1, 2, 3):
        p = SignParams(width=60.0, height=200.0, text_dir1=text_dir, text_dir2=text_dir)
        check_views(check_meshes(p, f"tall sign, text direction {text_dir}"), f"text direction {text_dir}",
                    need=0.80)

    # shared letter colour on two coloured grounds; a third colour for the back
    p = SignParams(text_fil1=1, back_fil1=2, text_fil2=1, back_fil2=3, base_fil=2)
    check_views(check_meshes(p, "white letters on green / red"), "coloured grounds")
    four = check_meshes(SignParams(base_fil=4, crest_fil=4), "a fourth filament for the back and ridge tops")
    assert sorted(four.bodies) == [1, 2, 3, 4] and "back" in four.part_name(4)
    check_views(four, "four filaments")
    check_files(four)
    check_meshes(SignParams(frame=0.0, crest=0.0, pitch=3.0, view_angle=45.0), "no frame, sharp ridges")
    check_meshes(SignParams(text2="", size1=20.0, font1="Times New Roman", italic1=True), "one message")
    check_meshes(SignParams(text1="WET\nPAINT", text2="DRY", width=120.0, height=90.0), "two lines")

    check_shapes(d)
    check_mounting()
    with tempfile.TemporaryDirectory() as tmp:
        check_pictures(tmp)
    check_layout()
    check_flat()
    check_viewing()

    bad = SignDesign(SignParams(crest=2.6))
    assert bad.errors and not bad.bodies
    big = SignDesign(SignParams(size1=80.0))
    assert any("Message 1" in w for w in big.warnings), big.warnings
    print("all tests passed")


if __name__ == "__main__":
    main()
