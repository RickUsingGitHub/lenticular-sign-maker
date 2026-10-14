"""Geometry of a lenticular sign.

The sign prints flat on its back.  Its face is a row of ridges with a
symmetric saw-tooth section.  One flank of every ridge carries message 1 and
the other flank message 2, so from one side of straight-on only message 1 is
in view and from the other side only message 2.  Each ridge is one line of
both pictures.

Everything is built in *ridge coordinates* - u across the ridges, v along
them, z up from the bed - and turned into x, y, z at the end, so ridges that
run across the sign and ridges that run up and down share all the code.

The outline is a rectangle whose corners can be rounded - all the way to
semicircular ends, or a circle.  Where it curves, the ridges are cut to follow
it: the end of a ridge is sliced into narrow strips, each with a flat end, and
the frame's inner edge runs through the same corners so the two meet exactly.

The model is a set of closed shells sorted by filament: the back, the frame
and every stretch of ridge.  Shells of one filament touch face to face and
the slicer joins them; no shell overlaps another.
"""

from __future__ import annotations

import math
import os

import numpy as np

from .params import (BLOCK, EVERYWHERE, EYELETS, FILAMENTS, MAGNETS, WHERE_NEEDED, SignParams,
                     outline_size)
from .textimage import ACROSS, STACKED, TextShape, load_picture

PLUS, MINUS = 0, 1        # the ridge flank facing +u / facing -u
PLA_DENSITY = 1.24        # g/cm3
MIN_PX = 0.1              # finest raster of the text (mm)
MAX_RASTER = 4096         # ... but never more pixels than this along the longer side
ARC_TOL = 0.02            # how far a flat facet may stray from a round outline (mm)
WELD = 1e-6               # ends closer together than this are one point (mm)
POCKET_FIT = 0.1          # a magnet pocket is this much bigger all round than its magnet (mm)
POCKET_ROOF = 0.6         # back left over a magnet pocket (mm)
EYELET_WALL = 3.0         # width of the ring round an eyelet's hole (mm)
EYELET_GAP = 1.5          # from the sign's edge to the hole (mm)


def mesh_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    """Signed volume of a closed mesh (positive when the faces point outwards)."""
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def edge_check(faces: np.ndarray):
    """(open edges, badly shared edges) of a mesh: both 0 when every shell is closed."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]).astype(np.int64)
    n = int(faces.max()) + 1 if len(faces) else 1
    fwd = np.unique(e[:, 0] * n + e[:, 1], return_counts=True)
    back = e[:, 1] * n + e[:, 0]
    unmatched = int((~np.isin(fwd[0], back)).sum())
    return unmatched, int((fwd[1] != 1).sum())


def _clean(pts, eps=1e-9):
    """A polygon without repeated corners."""
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) > eps or abs(q[1] - out[-1][1]) > eps:
            out.append(q)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < eps and abs(out[0][1] - out[-1][1]) < eps:
        out.pop()
    return out


def _between(pts, ua, ub):
    """The part of a convex (u, z) section that lies in ua <= u <= ub."""
    for sign, lim in ((1.0, ua), (-1.0, ub)):
        out = []
        for i, p in enumerate(pts):
            q = pts[(i + 1) % len(pts)]
            dp, dq = sign * (p[0] - lim), sign * (q[0] - lim)
            if dp >= -1e-12:
                out.append(p)
            if (dp > 1e-12 and dq < -1e-12) or (dp < -1e-12 and dq > 1e-12):
                out.append((lim, p[1] + dp / (dp - dq) * (q[1] - p[1])))
        pts = out
        if len(pts) < 3:
            return []
    return _clean(pts)


def rounded_outline(U: float, V: float, r: float) -> list:
    """Corners of a U x V rectangle with its corners rounded to radius r, anticlockwise."""
    if r <= 1e-9:
        return [(0.0, 0.0), (U, 0.0), (U, V), (0.0, V)]
    step = 2.0 * math.acos(max(1.0 - ARC_TOL / r, 0.0))
    n = max(2, int(math.ceil(0.5 * math.pi / step)))
    pts = []
    for cu, cv, a0 in ((U - r, r, -0.5 * math.pi), (U - r, V - r, 0.0),
                       (r, V - r, 0.5 * math.pi), (r, r, math.pi)):
        pts += [(cu + r * math.cos(a0 + 0.5 * math.pi * i / n),
                 cv + r * math.sin(a0 + 0.5 * math.pi * i / n)) for i in range(n + 1)]
    return _clean(pts, 1e-7)


class _Shells:
    """Closed shells gathered into one mesh (in ridge coordinates)."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.count = 0          # shells

    def prism(self, section, v0: float, v1: float):
        """Extrude a convex section from v0 to v1.

        section: (u, z) corners, anticlockwise with u to the right and z up.
        """
        pts = _clean(section)
        k = len(pts)
        if k < 3 or v1 - v0 <= 1e-9:
            return
        base = len(self.verts)
        self.verts += [(u, v0, z) for u, z in pts]
        self.verts += [(u, v1, z) for u, z in pts]
        for i in range(k):
            j = (i + 1) % k
            a, b, c, d = base + i, base + k + i, base + k + j, base + j
            self.faces += [(a, b, c), (a, c, d)]
        for i in range(1, k - 1):
            self.faces.append((base, base + i, base + i + 1))                  # end at v0
            self.faces.append((base + k, base + k + i + 1, base + k + i))      # end at v1
        self.count += 1

    def strips(self, section, cuts, start, stop):
        """A piece of ridge whose ends are not square.

        section: as for prism.  cuts: the u values to slice the ridge at.  start,
        stop: functions of u giving where the piece begins and ends along v.
        Between two cuts the ends are flat, straight from the curve at one cut
        to the curve at the next, so together they follow it in short facets.
        Where a slice has no length at one edge its two ends meet there, and it
        is a wedge.
        """
        pts = _clean(section)
        if len(pts) < 3:
            return
        for ua, ub in zip(cuts, cuts[1:]):
            part = _between(pts, ua, ub) if ub - ua > 1e-9 else []
            if len(part) < 3:
                continue
            (sa, ea), (sb, eb) = (start(ua), stop(ua)), (start(ub), stop(ub))
            spans = [(sa + (sb - sa) * t, ea + (eb - ea) * t)
                     for t in ((u - ua) / (ub - ua) for u, _ in part)]
            if all(v1 - v0 <= WELD for v0, v1 in spans):
                continue
            first, last = [], []                    # the corners at the two ends
            for (u, z), (v0, v1) in zip(part, spans):
                first.append(len(self.verts))
                self.verts.append((u, v0, z))
                if v1 - v0 <= WELD:
                    last.append(first[-1])
                else:
                    last.append(len(self.verts))
                    self.verts.append((u, v1, z))
            k = len(part)
            faces = []
            for i in range(k):
                j = (i + 1) % k
                faces += [(first[i], last[i], last[j]), (first[i], last[j], first[j])]
            for i in range(1, k - 1):
                faces.append((first[0], first[i], first[i + 1]))
                faces.append((last[0], last[i + 1], last[i]))
            self.faces += [f for f in faces if len(set(f)) == 3]
            self.count += 1

    def column(self, outline, z0: float, z1: float):
        """Extrude a convex outline - (u, v) corners, anticlockwise - from z0 up to z1."""
        pts = _clean(outline, 1e-7)
        k = len(pts)
        if k < 3 or z1 - z0 <= 1e-9:
            return
        base = len(self.verts)
        self.verts += [(u, v, z0) for u, v in pts] + [(u, v, z1) for u, v in pts]
        for i in range(k):
            j = (i + 1) % k
            self.faces += [(base + i, base + j, base + k + j), (base + i, base + k + j, base + k + i)]
        for i in range(1, k - 1):
            self.faces.append((base, base + i + 1, base + i))                  # underside
            self.faces.append((base + k, base + k + i, base + k + i + 1))      # top
        self.count += 1

    def plate(self, extent, cuts, holes, z0: float, z1: float):
        """A flat plate from z0 up to z1, with round holes through it.

        extent: function of u giving (start, end) of the plate along v.  cuts:
        the u values where its edge has a corner.  holes: (u, v, radius).  Built
        as strips across u, each split in two wherever it crosses a hole.
        """
        us = set(cuts)
        for uc, _, rho in holes:
            n = max(8, int(math.ceil(math.pi / (2.0 * math.acos(max(1.0 - ARC_TOL / rho, 0.0))))))
            us |= {uc - rho * math.cos(math.pi * i / n) for i in range(n + 1)}
        us = sorted(us)
        for ua, ub in zip(us, us[1:]):
            if ub - ua < 1e-9:
                continue
            mid = 0.5 * (ua + ub)
            box = [(ua, z0), (ub, z0), (ub, z1), (ua, z1)]
            start = lambda u: extent(u)[0]                               # noqa: E731
            for uc, vc, rho in sorted((h for h in holes if abs(mid - h[0]) < h[2]), key=lambda h: h[1]):
                half = lambda u, uc=uc, rho=rho: math.sqrt(max(rho * rho - (u - uc) ** 2, 0.0))  # noqa: E731
                self.strips(box, [ua, ub], start, lambda u, vc=vc, half=half: vc - half(u))
                start = lambda u, vc=vc, half=half: vc + half(u)         # noqa: E731
            self.strips(box, [ua, ub], start, lambda u: extent(u)[1])

    def ring(self, outer, inner, z0: float, z1: float, centre=None):
        """The solid between two convex outlines, one inside the other, from z0 up to z1.

        Both anticlockwise.  Top and underside are laced together by stepping
        round the two outlines in order of direction from ``centre`` - a point
        inside both; the middle of the outer one if not given.
        """
        outer, inner = _clean(outer, 1e-7), _clean(inner, 1e-7)
        na, nb = len(outer), len(inner)
        if na < 3 or nb < 3 or z1 - z0 <= 1e-9:
            return
        cu, cv = centre or (sum(q[0] for q in outer) / na, sum(q[1] for q in outer) / na)
        ang_a = np.unwrap([math.atan2(v - cv, u - cu) for u, v in outer])
        raw = np.array([math.atan2(v - cv, u - cu) for u, v in inner])
        off = np.angle(np.exp(1j * (raw - ang_a[0])))
        j0 = int(np.argmin(np.abs(off)))            # start the inner outline beside outer[0]
        inner = inner[j0:] + inner[:j0]
        ang_b = np.unwrap(np.roll(raw, -j0))
        ang_b += ang_a[0] + off[j0] - ang_b[0]
        ang_a = np.append(ang_a, ang_a[0] + 2.0 * math.pi)
        ang_b = np.append(ang_b, ang_b[0] + 2.0 * math.pi)

        base = len(self.verts)
        a_lo, a_hi, b_lo, b_hi = base, base + na, base + 2 * na, base + 2 * na + nb
        for z in (z0, z1):
            self.verts += [(u, v, z) for u, v in outer]
        for z in (z0, z1):
            self.verts += [(u, v, z) for u, v in inner]
        i = j = 0
        while i < na or j < nb:
            if j >= nb or (i < na and ang_a[i + 1] <= ang_b[j + 1]):
                tri = ((0, i % na), (0, (i + 1) % na), (1, j % nb))
                i += 1
            else:
                tri = ((1, j % nb), (0, i % na), (1, (j + 1) % nb))
                j += 1
            top = tuple((a_hi, b_hi)[which] + k for which, k in tri)
            under = tuple((a_lo, b_lo)[which] + k for which, k in tri)
            self.faces += [top, under[::-1]]
        for i in range(na):                          # outer wall
            j = (i + 1) % na
            self.faces += [(a_lo + i, a_lo + j, a_hi + j), (a_lo + i, a_hi + j, a_hi + i)]
        for i in range(nb):                          # inner wall, facing the middle
            j = (i + 1) % nb
            self.faces += [(b_lo + i, b_hi + j, b_lo + j), (b_lo + i, b_hi + i, b_hi + j)]
        self.count += 1


def _period_means(summed: np.ndarray, extent: float, a: float, b: float) -> np.ndarray:
    """Mean of a mask over u in a..b (mm), for every column along v.

    summed: the mask summed along u with a row of zeros in front (rows + 1, columns).
    """
    nu = summed.shape[0] - 1

    def upto(u):
        t = min(max(u / extent * nu, 0.0), float(nu))
        i = min(int(t), nu - 1)
        return summed[i] + (t - i) * (summed[i + 1] - summed[i])

    return (upto(b) - upto(a)) / max((b - a) / extent * nu, 1e-9)


def _profile_runs(q: np.ndarray, length: float, lo: float, hi: float, min_len: float) -> list:
    """Stretches (mm along v) where the profile is above one half, kept inside lo..hi.

    q is sampled at pixel centres over 0..length.  Edges are placed between
    pixels where the profile crosses one half.  Gaps and pieces shorter than
    min_len cannot be printed: gaps are closed, short pieces dropped.
    """
    n = len(q)
    on = q >= 0.5
    if not on.any():
        return []
    px = length / n
    idx = np.nonzero(on[1:] != on[:-1])[0]
    cross = ((idx + 0.5 + (0.5 - q[idx]) / (q[idx + 1] - q[idx])) * px).tolist()
    if on[0]:
        cross.insert(0, 0.0)
    if on[-1]:
        cross.append(length)
    runs = []
    for a, b in zip(cross[::2], cross[1::2]):
        a, b = max(a, lo), min(b, hi)
        if a - lo < min_len:
            a = lo
        if hi - b < min_len:
            b = hi
        if b <= a:
            continue
        if runs and a - runs[-1][1] < min_len:
            runs[-1] = (runs[-1][0], b)
        else:
            runs.append((a, b))
    return [(a, b) for a, b in runs if b - a >= min_len]


class SignDesign:
    """A lenticular sign built from its parameters.

    After construction: ``errors`` (empty when the sign could be built),
    ``warnings``, ``notes`` and ``bodies`` - filament number -> (verts, faces)
    in print position, centred on x = y = 0 with the back on z = 0.
    """

    def __init__(self, p: SignParams):
        self.p = p
        self.errors, self.warnings, self.notes = [], [], []
        self.bodies = {}
        self._check()
        if not self.errors:
            self._layout()
        if not self.errors:
            self._messages()
            self._runs()
            self._build()
            self._advice()

    # ---------------------------------------------------------------- checks
    def _check(self):
        p, err = self.p, self.errors
        if not (10.0 <= p.width <= 2000.0 and 10.0 <= p.height <= 2000.0):
            err.append("The sign must be between 10 mm and 2 m on each side.")
        if p.pitch < 0.8:
            err.append("The ridge pitch must be at least 0.8 mm.")
        if not (10.0 <= p.view_angle <= 75.0):
            err.append("The viewing angle must be between 10° and 75°.")
        if p.base_thickness < 0.2:
            err.append("The back must be at least 0.2 mm thick.")
        if p.crest < 0 or p.frame < 0 or p.margin < 0:
            err.append("Crest, frame and margin cannot be negative.")
        elif p.crest > p.pitch - 0.6:
            err.append(f"The crest ({p.crest:g} mm) leaves no room for the ridge flanks: keep it "
                       f"at least 0.6 mm narrower than the pitch ({p.pitch:g} mm).")

    # ---------------------------------------------------------------- layout
    def _layout(self):
        p = self.p
        self.vertical = p.ridge_dir == 1          # ridges run up and down the sign
        self.W, self.H, self.r = outline_size(p)  # as built: roundness can make it bigger
        self.U, self.V = (self.W, self.H) if self.vertical else (self.H, self.W)
        U, V = self.U, self.V
        b = self.b = p.frame
        c = self.c = p.crest
        self.ri = max(self.r - b, 0.0)            # corner radius of the frame's inner edge
        self.theta = math.radians(p.view_angle)
        room = U - 2 * b
        if room < 0.75 * p.pitch or V - 2 * b < 4.0:
            self.errors.append("The frame leaves no room for the ridges: make it narrower "
                               "or the sign bigger.")
            return
        n = self.n = max(1, int(round(room / p.pitch)))
        # whole ridges only: the pitch is adjusted slightly so they fill the face exactly
        self.pitch = room / n
        s = self.s = 0.5 * (self.pitch - c)
        if s < 0.2:
            self.errors.append(f"The crest ({c:g} mm) leaves no room for the ridge flanks at "
                               f"{self.pitch:.2f} mm between ridges.")
            return
        self.h = s / math.tan(self.theta)         # ridge height
        self.flank_angle = 90.0 - p.view_angle    # of the flanks, from the sign's face
        # a whole number of layers thick, so the ridge tops land on a layer: the back takes
        # up the difference
        lh = self.layer = min(max(p.layer_height, 0.04), 1.0)
        least = 0.2                               # the thinnest the back may be
        if p.mount == MAGNETS:                    # pockets a whole number of layers deep, and a roof
            self.pocket_r = 0.5 * p.magnet_d + POCKET_FIT
            self.pocket_depth = math.ceil((p.magnet_t + 0.05) / lh - 1e-9) * lh
            least = self.pocket_depth + POCKET_ROOF
        self.layers = max(int(round((p.base_thickness + self.h) / lh)),
                          int(math.ceil((least + self.h) / lh - 1e-9)))
        self.top = self.layers * lh
        self.T = self.top - self.h
        # The top of every ridge is seen from both sides, and near the top each flank's half
        # of the ridge is narrower than a printed line - a slicer leaves that out, or prints
        # it too wide.  So the top can be one plain filament instead: everything above the
        # height where each half is still a full line wide, and at least one layer.
        line = min(max(p.line_width, 0.1), 2.0)
        self.cap = (min(max(lh, (line - 0.5 * c) * math.tan(math.pi / 2 - self.theta)), 0.6 * self.h)
                    if p.crest_fil else 0.0)
        self.ridge_u = b + self.pitch * np.arange(n)
        self.v_lo, self.v_hi = b, V - b
        # Where the outline curves, a ridge is shorter along one edge than the other.  Letters
        # are kept to the stretch that has the ridge's full width; beyond it is plain ground.
        self.full = []
        for u0 in self.ridge_u:
            (a0, e0), (a1, e1) = self.ends_at(u0), self.ends_at(u0 + self.pitch)
            self.full.append((max(a0, a1), min(e0, e1)))

        # the space for text: the outline moved in by the frame and the margin
        inset = b + p.margin
        self.text_w, self.text_h = self.W - 2 * inset, self.H - 2 * inset
        self.text_r = max(self.r - inset, 0.0)
        if self.text_w < 3.0 or self.text_h < 3.0:
            self.errors.append("The frame and margin leave no room for the text.")
            return
        m1_plus = (p.view_side == 1) if self.vertical else (p.view_side == 0)
        self.side_of = (PLUS, MINUS) if m1_plus else (MINUS, PLUS)       # flank of each message
        self.fil = {self.side_of[0]: (p.text_fil1, p.back_fil1),         # flank -> (letters, behind)
                    self.side_of[1]: (p.text_fil2, p.back_fil2)}
        self._mounting()

    def _mounting(self):
        """Where the magnet pockets or the hanging eyelets go (x, y from the sign's centre)."""
        p, W, H, r = self.p, self.W, self.H, self.r
        self.pockets = []                         # centres of the magnet pockets in the back
        self.tabs = []                            # eyelets: (rim outline, hole outline, centre)
        self.reach = (0.0, 0.0)                   # how far eyelets stick out past the outline
        if p.mount == MAGNETS:
            e = p.mount_inset + self.pocket_r     # from the outline in to a pocket's centre
            if p.mount_count == 1:
                pts = [(0.0, 0.0)]
            elif p.mount_count == 2:              # at the two ends of the longer side
                pts = ([(-(0.5 * W - e), 0.0), (0.5 * W - e, 0.0)] if W >= H
                       else [(0.0, -(0.5 * H - e)), (0.0, 0.5 * H - e)])
            else:                                 # in the corners, following a round one inwards
                k = (r - e) / math.sqrt(2.0) if r > e else None
                cx, cy = (0.5 * W - e, 0.5 * H - e) if k is None else (0.5 * W - r + k, 0.5 * H - r + k)
                pts = [(sx * cx, sy * cy) for sx in (-1, 1) for sy in (-1, 1)]
            apart = min((math.dist(a, c) for i, a in enumerate(pts) for c in pts[i + 1:]), default=1e9)
            if e > 0.5 * min(W, H) or apart < 2 * self.pocket_r + 2.0:
                self.warnings.append(
                    f"{p.mount_count} magnets of {p.magnet_d:g} mm do not fit this sign {p.mount_inset:g} "
                    f"mm in from its edge, so they are left out. Use smaller or fewer magnets.")
            else:
                self.pockets = pts
        elif p.mount == EYELETS:
            rh = 0.5 * p.hole_d
            rim, out = rh + EYELET_WALL, rh + EYELET_GAP          # rim radius; centre beyond the edge
            # (point on the outline, outward direction, straight edge either side of it)
            top = [((0.0, s * 0.5 * H), (0.0, float(s)), 0.5 * W - r) for s in (1, -1)]
            sides = [((s * 0.5 * W, 0.0), (float(s), 0.0), 0.5 * H - r) for s in (1, -1)]
            if p.mount_count == 1:                # one at the top - and one at the bottom, if the
                spots = top if p.flip else top[:1]    # sign is turned over to change
            else:                                 # at the two ends of the longer side
                spots = sides if W >= H else top
            arc = 2.0 * math.acos(max(1.0 - ARC_TOL / rim, 0.0))
            for (ax, ay), (nx, ny), straight in spots:
                cx, cy = ax + nx * out, ay + ny * out
                cut = out                         # the rim is cut off along the sign's edge ...
                if straight < 1e-6 and r > 0:     # ... or, on the tip of a round end, where the
                    far = r + out                 # two circles cross
                    cut = far - (far * far - rim * rim + r * r) / (2.0 * far)
                span = math.pi - math.acos(min(cut / rim, 1.0))
                n = max(8, int(math.ceil(2.0 * span / arc)))
                tx, ty = -ny, nx
                outer = [(cx + rim * (math.cos(a) * nx + math.sin(a) * tx),
                          cy + rim * (math.cos(a) * ny + math.sin(a) * ty))
                         for a in (span * (2.0 * i / n - 1.0) for i in range(n + 1))]
                m = max(12, int(math.ceil(2.0 * math.pi / (2.0 * math.acos(max(1.0 - ARC_TOL / rh, 0.0))))))
                hole = [(cx + rh * math.cos(2.0 * math.pi * i / m), cy + rh * math.sin(2.0 * math.pi * i / m))
                        for i in range(m)]
                self.tabs.append((outer, hole, (cx, cy)))
            stick = out + rim
            self.reach = (stick, 0.0) if spots is sides else (0.0, stick)

    def _extent(self, u: float, inset: float, radius: float):
        """(start, end) along v, at u, of the outline moved in by inset with corners of radius."""
        d = min(u - inset, self.U - inset - u)            # from the nearer edge that runs along
        pulled = 0.0
        if radius > 0 and d < radius:                     # in a round corner
            pulled = radius - math.sqrt(max(radius * radius - (radius - max(d, 0.0)) ** 2, 0.0))
        return inset + pulled, self.V - inset - pulled

    def ends_at(self, u: float):
        """(start, end) along v of the ridged face, on the line at u across the ridges."""
        return self._extent(u, self.b, self.ri)

    def outer_at(self, u: float):
        """(start, end) along v of the whole sign, on the line at u."""
        return self._extent(u, 0.0, self.r)

    def to_uv(self, x: float, y: float):
        """A point given from the sign's centre, in ridge coordinates."""
        return (x + 0.5 * self.W, y + 0.5 * self.H) if self.vertical else (y + 0.5 * self.H, x + 0.5 * self.W)

    def slices(self, u0: float, u1: float) -> list:
        """Where to cut a ridge lying between u0 and u1 so that flat ends follow the outline.

        Just its two edges where the outline is straight.  Across a round corner,
        cuts at equal steps of angle round the corner, close enough that a flat
        facet strays from the curve by no more than ARC_TOL.
        """
        ri, out = self.ri, {u0, u1}
        if ri > 0:
            step = 2.0 * math.acos(max(1.0 - ARC_TOL / ri, 0.0))
            for centre, way in ((self.b + ri, -1.0), (self.U - self.b - ri, 1.0)):
                # this row of corners spans u from centre to centre + way * ri
                span = sorted(min(max(way * (u - centre) / ri, 0.0), 1.0) for u in (u0, u1))
                if span[1] <= span[0]:
                    continue
                a0, a1 = math.acos(span[1]), math.acos(span[0])       # angle back from the tip
                n = max(1, int(math.ceil((a1 - a0) / step)))
                out |= {centre + way * ri * math.cos(a0 + (a1 - a0) * i / n) for i in range(n + 1)}
        return sorted(u for u in out if u0 - 1e-9 <= u <= u1 + 1e-9)

    def in_text_area(self, x, y):
        """Whether points (mm from the sign's centre; numbers or arrays) are in the space for text."""
        ax, ay = np.abs(x), np.abs(y)
        hw, hh, r = 0.5 * self.text_w, 0.5 * self.text_h, self.text_r
        cx, cy = np.maximum(ax - (hw - r), 0.0), np.maximum(ay - (hh - r), 0.0)
        return (ax <= hw) & (ay <= hh) & ((r <= 0) | (cx * cx + cy * cy <= r * r))

    def _fit_scale(self, shape: TextShape, dx: float = 0.0, dy: float = 0.0) -> float:
        """mm per font unit that makes a message's ink box, moved by dx, dy, just fit the space."""
        if shape.empty:
            return 0.0
        hw, hh = 0.5 * shape.rect.width(), 0.5 * shape.rect.height()
        dx, dy = abs(dx), abs(dy)                         # the corner that reaches furthest out
        lo, hi = 0.0, max(min((0.5 * self.text_w - dx) / hw, (0.5 * self.text_h - dy) / hh), 0.0)
        if self.in_text_area(hi * hw + dx, hi * hh + dy):
            return hi
        for _ in range(50):                               # round corners: close in on the size
            mid = 0.5 * (lo + hi)
            if self.in_text_area(mid * hw + dx, mid * hh + dy):
                lo = mid
            else:
                hi = mid
        return lo

    # ---------------------------------------------------------------- text
    def _messages(self):
        p = self.p
        get = lambda key, k: getattr(p, f"{key}{k + 1}")      # noqa: E731 - message k's own setting
        shapes = []
        for k in (0, 1):
            picture, path = None, get("image", k).strip()
            if path:
                picture = load_picture(path, get("image_invert", k))
                if picture is None:
                    self.warnings.append(
                        f"Message {k + 1}: the picture “{os.path.basename(path)}” could not be "
                        f"used. It is missing, not a picture, or all one shade.")
            shapes.append(TextShape(get("text", k), get("font", k), get("bold", k), get("italic", k),
                                    get("text_dir", k), get("spacing", k), picture, get("image_at", k),
                                    os.path.splitext(os.path.basename(path))[0]))
        self.shapes = shapes
        self.moved = [(get("dx", k), get("dy", k)) for k in (0, 1)]
        fits = [self._fit_scale(sh, *self.moved[k]) for k, sh in enumerate(shapes)]
        auto = [get("size", k) <= 0 for k in (0, 1)]
        scales = [fits[k] if auto[k] else get("size", k) / shapes[k].cap for k in (0, 1)]
        if all(auto) and not any(sh.empty for sh in shapes) and not any(sh.picture for sh in shapes):
            # both "as big as fits": the same letter height for the two messages
            cap = min(fits[k] * shapes[k].cap for k in (0, 1))
            scales = [cap / sh.cap for sh in shapes]
        self.scales = scales
        self.cap_mm = [0.0 if sh.empty else scales[k] * sh.cap for k, sh in enumerate(shapes)]
        self.px = max(MIN_PX, max(self.W, self.H) / MAX_RASTER)
        self.images = []
        for k, sh in enumerate(shapes):
            if sh.empty:
                self.notes.append(f"Message {k + 1} is empty, so that side of the ridges is plain.")
            else:
                w, h = sh.size(scales[k])
                dx, dy = (abs(m) for m in self.moved[k])
                if scales[k] <= 0 or not self.in_text_area(0.5 * w + dx - 0.03, 0.5 * h + dy - 0.03):
                    space = (f"there is only {self.text_w:.0f} × {self.text_h:.0f} mm for it"
                             if self.r <= 0 else "that does not fit inside the round outline")
                    if dx or dy:
                        space += " where it has been moved to"
                    self.warnings.append(
                        f"Message {k + 1} is {w:.0f} × {h:.0f} mm but {space}: make it smaller "
                        f"(or set the letter height to Auto), or make the sign bigger.")
            # message 2 of a sign that is turned over to change is built upside down
            self.images.append(self.message_image(k, built=True))

    def message_image(self, k: int, built: bool = False) -> np.ndarray:
        """Message k on the sign's face as a mask (rows from the top edge), as it is read.

        ``built``: as it lies on the sign instead - message 2 of a sign that is
        turned over to change is upside down.
        """
        return self.shapes[k].render(self.scales[k], self.W, self.H, self.px,
                                     built and k == 1 and self.p.flip, *self.moved[k])

    def _runs(self):
        """Per flank and ridge: the stretches along the ridge that are letter."""
        self.runs = {}
        for k in (0, 1):
            img = self.images[k][::-1]                          # row 0 = bottom edge (y = 0)
            cov = img.T if self.vertical else img               # indexed (u, v)
            summed = np.zeros((cov.shape[0] + 1, cov.shape[1]))
            np.cumsum(cov, axis=0, dtype=np.float64, out=summed[1:])
            # a ridge is one line of the picture: it shows what lies under its whole width
            self.runs[self.side_of[k]] = [
                _profile_runs(_period_means(summed, self.U, u0, u0 + self.pitch), self.V,
                              lo, hi, self.p.min_detail) if hi - lo >= self.p.min_detail else []
                for u0, (lo, hi) in zip(self.ridge_u, self.full)]

    # ---------------------------------------------------------------- solid
    def _build(self):
        p, U, V, b, s, T, top = self.p, self.U, self.V, self.b, self.s, self.T, self.top
        shells = {f: _Shells() for f in FILAMENTS}
        base = shells[p.base_fil]
        outline = rounded_outline(U, V, self.r)
        if self.pockets:                                        # the back: pockets underneath, ...
            holes = [self.to_uv(x, y) + (self.pocket_r,) for x, y in self.pockets]
            base.plate(self.outer_at, sorted({round(u, 9) for u, _ in outline}), holes,
                       0.0, self.pocket_depth)
            base.column(outline, self.pocket_depth, T)          # ... and a roof over them
        else:
            base.column(outline, 0.0, T)
        for rim, hole, centre in self.tabs:                     # eyelets, as thick as the back
            rim, hole = ([self.to_uv(x, y) for x, y in pts] for pts in (rim, hole))
            if not self.vertical:                               # mirrored: back to anticlockwise
                rim, hole = rim[::-1], hole[::-1]
            base.ring(rim, hole, 0.0, T, self.to_uv(*centre))

        half = 0.5 * self.pitch
        zc = top - self.cap                         # the flanks' colours stop here
        sc = s * (zc - T) / self.h                  # how far in the flanks are at that height
        grounds = (self.fil[MINUS][1], self.fil[PLUS][1])
        in_ridges = set()
        edge_u = []                                 # where the frame's inner edge has a corner
        for r, u0 in enumerate(self.ridge_u):
            u1, um = u0 + self.pitch, u0 + half
            section = {MINUS: [(u0, T), (um, T), (um, zc), (u0 + sc, zc)],
                       PLUS: [(um, T), (u1, T), (u1 - sc, zc), (um, zc)],
                       None: [(u0, T), (u1, T), (u1 - sc, zc), (u0 + sc, zc)]}    # the whole ridge
            crest = [(u0 + sc, zc), (u1 - sc, zc), (u1 - s, top), (u0 + s, top)] if self.cap > 0 else None
            lo, hi = self.full[r]                   # the stretch with square ends
            # beyond it the ridge follows the outline, in the same facets as the frame
            slices = self.slices(u0, u1)
            curved = any(abs(self.ends_at(u)[0] - lo) > WELD for u in slices)
            edge_u += slices

            def piece(fils, start, stop):
                """A square-ended stretch of the ridge: its two flanks, or the whole of it."""
                in_ridges.update(fils)
                if fils[0] == fils[1]:
                    shells[fils[0]].prism(section[None], start, stop)
                else:
                    shells[fils[0]].prism(section[MINUS], start, stop)
                    shells[fils[1]].prism(section[PLUS], start, stop)

            def round_end(start, stop):
                """Plain ground between two curves (functions of u), with its top."""
                in_ridges.update(grounds)
                parts = ([(grounds[0], section[None])] if grounds[0] == grounds[1]
                         else [(grounds[0], section[MINUS]), (grounds[1], section[PLUS])])
                for fil, sect in parts + ([(p.crest_fil, crest)] if crest else []):
                    shells[fil].strips(sect, slices, start, stop)

            if crest:
                in_ridges.add(p.crest_fil)
            if hi - lo < p.min_detail:              # all curve, no room for letters
                round_end(lambda u: self.ends_at(u)[0], lambda u: self.ends_at(u)[1])
                continue
            if curved:                              # out to the outline at each end
                round_end(lambda u: self.ends_at(u)[0], lambda u: lo)
                round_end(lambda u: hi, lambda u: self.ends_at(u)[1])
            if crest:
                shells[p.crest_fil].prism(crest, lo, hi)
            runs = {side: self.runs[side][r] for side in (PLUS, MINUS)}
            cuts = sorted({lo, hi} | {v for side in runs for run in runs[side] for v in run})
            pieces = []                                         # [v0, v1, (filament -, filament +)]
            for v0, v1 in zip(cuts, cuts[1:]):
                mid = 0.5 * (v0 + v1)
                fils = tuple(self.fil[side][0 if any(a < mid < e for a, e in runs[side]) else 1]
                             for side in (MINUS, PLUS))
                if pieces and pieces[-1][2] == fils:
                    pieces[-1][1] = v1
                else:
                    pieces.append([v0, v1, fils])
            for v0, v1, fils in pieces:
                piece(fils, v0, v1)

        edge_u = sorted({round(u, 9) for u in edge_u})
        if b > 0:                                               # the frame, flush with the crests
            inner = ([(u, self.ends_at(u)[0]) for u in edge_u]
                     + [(u, self.ends_at(u)[1]) for u in reversed(edge_u)])
            base.ring(outline, inner, T, top)
        self.flat_area = self.fill_volume = 0.0
        if p.ridges_where != EVERYWHERE:
            self._fill_grooves(shells, edge_u)

        self.prisms = sum(sh.count for sh in shells.values())
        for f, sh in shells.items():
            if not sh.faces:
                continue
            uvz = np.array(sh.verts, dtype=np.float64)
            faces = np.array(sh.faces, dtype=np.int32)
            if self.vertical:                                   # u = x, v = y
                verts = uvz - (0.5 * self.W, 0.5 * self.H, 0.0)
            else:                                               # u = y, v = x: a mirror, so
                verts = uvz[:, [1, 0, 2]] - (0.5 * self.W, 0.5 * self.H, 0.0)
                faces = faces[:, ::-1].copy()                   # ... the faces are turned back
            self.bodies[f] = (verts, faces)
        self.triangles = sum(len(f) for _, f in self.bodies.values())
        self.volumes = {f: mesh_volume(v, fc) for f, (v, fc) in self.bodies.items()}
        self.ridge_fils = in_ridges | ({p.base_fil} if b > 0 else set())

    def flank_fil(self, r: int, side: int, v: float) -> int:
        """The filament showing on one flank of ridge r, at v along it."""
        letters, ground = self.fil[side]
        return letters if any(a < v < e for a, e in self.runs[side][r]) else ground

    def _fill_grooves(self, shells, edge_u):
        """Ridges on part of the face only: the grooves of the rest are filled in.

        A groove is seen from both sides: one side sees the flank of the ridge
        before it, the other the flank of the ridge after it.  Where those two
        flanks are the same filament nothing changes with the viewing side, so
        the groove can be filled with that filament, flush with the ridge tops.
        The ridges themselves stay as they are underneath.

        WHERE_NEEDED fills every such stretch.  That leaves ridges under the
        letters of both messages, so each shows faintly in the other as a patch
        of ridges.  BLOCK keeps all the grooves in one block round the two
        messages instead, and fills only what lies outside it.
        """
        p, s, h, T, top = self.p, self.s, self.h, self.T, self.top
        facet_lo = [self.ends_at(u)[0] for u in edge_u]       # the face's ends, in the same flat
        facet_hi = [self.ends_at(u)[1] for u in edge_u]       # facets as the ridges and the frame
        start = lambda u: float(np.interp(u, edge_u, facet_lo))       # noqa: E731
        stop = lambda u: float(np.interp(u, edge_u, facet_hi))        # noqa: E731
        block = None                                      # (first ridge, last ridge, from v, to v)
        lettered = [r for r in range(self.n) if self.runs[PLUS][r] or self.runs[MINUS][r]]
        if p.ridges_where == BLOCK and lettered:
            along = [v for side in (PLUS, MINUS) for runs in self.runs[side] for run in runs for v in run]
            block = (lettered[0], lettered[-1], min(along) - self.pitch, max(along) + self.pitch)
        self.block = block

        def fill(section, flanks, groove, only=None):
            """Fill one groove; flanks: the (ridge, side) pairs that face into it.

            groove: its number - groove j lies between ridge j and ridge j + 1.
            """
            keep = block[2:] if block and block[0] - 1 <= groove <= block[1] else None
            ua, ub = min(q[0] for q in section), max(q[0] for q in section)
            deepest = section[0][0]                       # where the groove's bottom is
            cuts_u = [ua] + [u for u in edge_u if ua + 1e-9 < u < ub - 1e-9] + [ub]
            lo, hi = max(start(ua), start(ub)), min(stop(ua), stop(ub))      # square-ended stretch

            def same(v):                                  # the one filament every flank shows, if any
                fils = {self.flank_fil(r, side, v) for r, side in flanks}
                f = fils.pop() if len(fils) == 1 else None
                return f if only in (None, f) else None

            def curved(a, e):                             # out to a curved outline: plain ground
                f = same(-1.0)                            # (no letters there: runs start at 0 or more)
                if f is not None:
                    shells[f].strips(section, cuts_u, a, e)
                    xs = np.linspace(ua, ub, 81)
                    long = np.array([max(e(x) - a(x), 0.0) for x in xs])
                    deep = h * np.clip(1.0 - np.abs(xs - deepest) / s, 0.0, 1.0)
                    for of, total in ((long * deep, "fill_volume"), (long, "flat_area")):
                        setattr(self, total, getattr(self, total)
                                + float(((of[1:] + of[:-1]) * np.diff(xs)).sum() / 2.0))

            ends = [v for r, side in flanks for run in self.runs[side][r] for v in run]
            if keep:                                      # the block's own edges count as edges too
                ends += list(keep)
            if hi - lo < p.min_detail:                    # all curve
                if not ends:
                    curved(start, stop)
                return
            if not any(v < lo for v in ends):
                curved(start, lambda u: lo)
            if not any(v > hi for v in ends):
                curved(lambda u: hi, stop)
            cuts = sorted({lo, hi} | {v for v in ends if lo < v < hi})
            spans = []                                    # [v0, v1, filament or None]
            for v0, v1 in zip(cuts, cuts[1:]):
                mid = 0.5 * (v0 + v1)
                f = None if keep and keep[0] < mid < keep[1] else same(mid)
                if spans and spans[-1][2] == f:
                    spans[-1][1] = v1
                else:
                    spans.append([v0, v1, f])
            for v0, v1, f in spans:
                if f is not None:
                    shells[f].prism(section, v0, v1)
                    self.fill_volume += 0.5 * (ub - ua) * h * (v1 - v0)
                    self.flat_area += (ub - ua) * (v1 - v0)

        for j in range(self.n - 1):                       # between each ridge and the next
            u1 = float(self.ridge_u[j + 1])
            fill([(u1, T), (u1 + s, top), (u1 - s, top)], [(j, PLUS), (j + 1, MINUS)], j)
        if self.b > 0:                                    # against the frame: fill what matches it
            u0, u1 = float(self.ridge_u[0]), float(self.ridge_u[-1]) + self.pitch
            fill([(u0, T), (u0 + s, top), (u0, top)], [(0, MINUS)], -1, p.base_fil)
            fill([(u1, T), (u1, top), (u1 - s, top)], [(self.n - 1, PLUS)], self.n - 1, p.base_fil)

    def flat_share(self) -> float:
        """How much of the ridged face has been made flat (0..1)."""
        b = self.b
        face = (self.U - 2 * b) * (self.V - 2 * b) - (4.0 - math.pi) * self.ri ** 2
        # a filled groove leaves the crest of each ridge beside it showing: count that as flat too
        return min(self.flat_area * self.pitch / (2.0 * self.s) / face, 1.0) if face > 0 else 0.0

    def solid_volume(self) -> float:
        """Volume of the whole sign from its dimensions (mm3) - a check on the mesh.

        Exact for square corners.  Round ones are built from flat facets, so the
        mesh comes out a touch smaller than this.
        """
        b, s, h = self.b, self.s, self.h
        cut = 4.0 - math.pi                                     # what rounding takes off, per r2
        outer = self.U * self.V - cut * self.r ** 2
        vol = outer * self.T
        if b > 0:
            vol += (outer - (self.U - 2 * b) * (self.V - 2 * b) + cut * self.ri ** 2) * h
        # the ridges: their height across the ridge, times the length of the face there
        x = np.unique(np.concatenate([np.linspace(0.0, self.pitch, 241), [s, self.pitch - s]]))
        z = np.interp(x, [0.0, s, self.pitch - s, self.pitch], [0.0, h, h, 0.0])
        for u0 in self.ridge_u:
            ends = np.array([self.ends_at(u) for u in u0 + x])
            f = z * (ends[:, 1] - ends[:, 0])
            vol += float(((f[1:] + f[:-1]) * np.diff(x)).sum() / 2.0)
        vol += self.fill_volume                                 # grooves filled in
        if self.pockets:
            vol -= len(self.pockets) * math.pi * self.pocket_r ** 2 * self.pocket_depth

        def area(pts):                                          # of an outline, by its corners
            return 0.5 * abs(sum(a[0] * c[1] - c[0] * a[1] for a, c in zip(pts, pts[1:] + pts[:1])))
        return vol + sum(area(rim) - area(hole) for rim, hole, _ in self.tabs) * self.T

    def shape_name(self) -> str:
        if self.r <= 1e-9:
            return "square corners"
        if self.r < 0.5 * min(self.W, self.H) - 1e-6:
            return f"corners rounded to {self.r:.1f} mm"
        return "a circle" if abs(self.W - self.H) < 1e-6 else "semicircular ends"

    # ---------------------------------------------------------------- advice
    def _advice(self):
        p = self.p
        for k in (0, 1):
            if self.shapes[k].empty:
                continue
            if not self.shapes[k].lines:                        # a picture alone
                continue
            # how many ridges a letter spans: its height or (roughly) its width
            upright = getattr(p, f"text_dir{k + 1}") in (ACROSS, STACKED)
            span = self.cap_mm[k] * (1.0 if upright != self.vertical else 0.7)
            if span / self.pitch < 6.0:
                self.warnings.append(
                    f"Message {k + 1}: a letter is only about {span / self.pitch:.0f} ridges "
                    f"{'wide' if upright == self.vertical else 'tall'}, so thin strokes may break "
                    f"up. Use bigger letters, a bolder font or a smaller ridge pitch.")
        if self.cap > 0 and self.crest_share() > 0.45:
            self.warnings.append(
                f"The plain ridge tops take up {self.crest_share():.0%} of every line, so the "
                f"messages will look pale. The ridges are small for a {p.line_width:g} mm line: "
                f"use a bigger ridge pitch.")
        elif self.pitch < 5.0 * p.line_width:
            self.warnings.append(f"Ridges {self.pitch:.2f} mm apart are small for a {p.line_width:g} "
                                 f"mm line: each flank has little room for its colour. Use a bigger "
                                 f"ridge pitch.")
        if self.cap <= 0:
            self.notes.append(
                "Ridge tops are half from each message, so near the top each colour is narrower "
                "than a printed line. Slice with the Arachne wall generator, which prints narrow "
                "lines; the classic one leaves them out.")
        across, up = self.W + 2 * self.reach[0], self.H + 2 * self.reach[1]     # with any eyelets
        fits = (across <= p.bed_x and up <= p.bed_y) or (up <= p.bed_x and across <= p.bed_y)
        self.fits_bed = fits
        self.turn_on_bed = fits and not (across <= p.bed_x and up <= p.bed_y)
        if not fits:
            self.warnings.append(f"The sign ({across:.4g} × {up:.4g} mm) is bigger than the "
                                 f"printer bed ({p.bed_x:g} × {p.bed_y:g} mm).")
        if p.ridges_where != EVERYWHERE and self.flat_share() < 0.03:
            self.notes.append(
                "Hardly any grooves could be filled in: the two messages have different colours behind "
                "their letters, so every groove shows one colour from one side and another from "
                "the other.")
        elif p.ridges_where == WHERE_NEEDED and not any(sh.empty for sh in self.shapes):
            self.notes.append(
                "With ridges only where needed, the other message's letters keep their grooves. "
                "They are the plain colour, but show as patches of ridges on the flat face. "
                "A block round the messages hides them.")
        if self.pockets and self.T > p.base_thickness + 0.5 * self.layer + 1e-9:
            self.notes.append(
                f"The back is {self.T:.2f} mm thick rather than the {p.base_thickness:g} mm set, to "
                f"leave {POCKET_ROOF:g} mm over the {self.pocket_depth:.1f} mm magnet pockets.")
        if (self.W, self.H) != (p.width, p.height):
            side, was, now = (("height", p.height, self.H) if self.H != p.height
                              else ("width", p.width, self.W))
            self.notes.append(f"The shape is past semicircular ends, on its way to a circle, so "
                              f"the {side} is {now:.4g} mm rather than the {was:g} mm set.")
        spread = math.degrees(math.atan(0.5 * self.U / 500.0))
        if spread > 6.0:
            self.notes.append(
                f"The ridges are spread over {self.U:g} mm, so from 50 cm away the near and far "
                f"ridges are seen {spread:.0f}° either side of the middle one. Stand a little "
                f"further round than {p.view_angle:g}°, or further back, for a clean message.")

    def seen(self, k: int) -> str:
        """How message k (0 or 1) is looked at, e.g. 'from above'."""
        names = ("the left", "the right") if self.vertical else ("above", "below")
        first, other = names[self.p.view_side], names[1 - self.p.view_side]
        if k == 0:
            return f"from {first}"
        return f"from {first}, sign turned upside down" if self.p.flip else f"from {other}"

    def label(self, k: int) -> str:
        """Short name of message k: its first line, or its picture's name."""
        return self.shapes[k].name or "(empty)"

    def mounting(self) -> str:
        """The magnets or eyelets in words; empty if there are none."""
        p = self.p
        if self.pockets:
            n = len(self.pockets)
            return (f"{n} pocket{'s' if n > 1 else ''} in the back for {p.magnet_d:g} × {p.magnet_t:g} "
                    f"mm magnets: {2 * self.pocket_r:.1f} mm across, {self.pocket_depth:.1f} mm deep")
        if self.tabs:
            where = ("at the top and bottom" if len(self.tabs) == 2 and not self.reach[0]
                     else "at each side" if len(self.tabs) == 2 else "at the top")
            return (f"{'eyelets' if len(self.tabs) > 1 else 'an eyelet'} {where}, with a "
                    f"{p.hole_d:g} mm hole, as thick as the back")
        return ""

    def part_name(self, fil: int) -> str:
        p = self.p
        roles = []
        if p.base_fil == fil:
            roles.append("back")
        for k, (tf, bf) in enumerate(((p.text_fil1, p.back_fil1), (p.text_fil2, p.back_fil2))):
            if tf == fil and not self.shapes[k].empty:
                roles.append(self.label(k))
            if bf == fil and p.base_fil != fil:
                roles.append(f"behind {self.label(k)}")
        if self.cap > 0 and p.crest_fil == fil and p.base_fil != fil:
            roles.append("ridge tops")
        return f"Filament {fil} - " + ", ".join(roles or ["unused"])

    def crest_share(self) -> float:
        """Share of each ridge that the plain top layer takes up, seen from the viewing angle."""
        if self.cap <= 0:
            return 0.0
        return (self.c + 2.0 * self.s * self.cap / self.h) / self.pitch

    def colour_changes(self):
        """(filament changes, ridge layers, colours in those layers) - an estimate."""
        layers = max(1, int(math.ceil(self.h / self.layer - 1e-6)))
        colours = len(self.ridge_fils)
        mixed = int(math.ceil((self.h - self.cap) / self.layer - 1e-6))    # plain tops are one colour
        return max(mixed, 1) * max(colours - 1, 0), layers, colours

    def info(self) -> dict:
        p = self.p
        changes, layers, colours = self.colour_changes()
        along = "up and down" if self.vertical else "across"
        ridges = f"{self.n} running {along}, {self.pitch:.2f} mm apart, {self.h:.2f} mm tall"
        if p.ridges_where != EVERYWHERE:
            ridges += (f". {'In a block round the messages' if p.ridges_where == BLOCK else 'Only where needed'}"
                       f": {self.flat_share():.0%} of the face is flat, its grooves filled in")
        out = {
            "Sign": (f"a circle {self.W:.4g} mm across" if self.shape_name() == "a circle"
                     else f"{self.W:.4g} × {self.H:.4g} mm with {self.shape_name()}")
                    + f", {self.top:.2f} mm thick ({self.layers} layers of {self.layer:g} mm: a "
                      f"{self.T:.2f} mm back under the ridges)",
            "Ridges": ridges,
            "Ridge flanks": f"{self.flank_angle:.0f}° steep - one message alone from "
                            f"{p.view_angle:g}° off straight-on",
        }
        for k in (0, 1):
            sh = self.shapes[k]
            if not sh.empty:
                w, h = sh.size(self.scales[k])
                size = (f"in {self.cap_mm[k]:.1f} mm letters" if sh.lines and not sh.picture
                        else f"{w:.0f} × {h:.0f} mm")
                out[f"Message {k + 1}"] = f"“{self.label(k)}” {size}, seen {self.seen(k)}"
        if self.mounting():
            out["Mounting"] = self.mounting()
        if self.cap > 0:
            out["Ridge tops"] = (f"top {self.cap:.2f} mm in filament {p.crest_fil}: a plain stripe "
                                 f"over {self.crest_share():.0%} of each line")
        else:
            out["Ridge tops"] = (f"half from each message: the other message shows as lines over "
                                 f"{0.5 * self.c / self.pitch:.0%} of each line")
        out["Colour changes"] = (f"about {changes}, all in the {layers} ridge layers "
                                 f"({colours} colours). The back is one colour.")
        out["PLA estimate"] = ", ".join(
            f"{self.volumes[f] * PLA_DENSITY / 1000.0:.1f} g filament {f}" for f in sorted(self.volumes))
        out["Printer bed"] = ("does not fit" if not self.fits_bed
                              else "fits turned a quarter turn" if self.turn_on_bed else "fits")
        return out
