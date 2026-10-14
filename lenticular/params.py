"""Design parameters for a lenticular sign, with JSON persistence."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

FILE_FORMAT = "lenticular-sign"
FILE_VERSION = 1
FILAMENTS = (1, 2, 3, 4)
NO_MOUNT, MAGNETS, EYELETS = 0, 1, 2
EVERYWHERE, BLOCK, WHERE_NEEDED = 0, 1, 2      # where the face has ridges


@dataclass
class SignParams:
    name: str = "clean_dirty"

    # --- message 1 ----------------------------------------------------------
    text1: str = "CLEAN"
    font1: str = "Arial"
    bold1: bool = True
    italic1: bool = False
    size1: float = 0.0            # height of capital letters (mm); 0 = as big as fits
    spacing1: float = 0.0         # letters spread out (+) or closed up (-), % of their advance
    text_dir1: int = 0            # 0 across, 1 stacked letters, 2 turned (reads upwards),
    #                               3 turned (reads downwards)
    dx1: float = 0.0              # moved right by this much (mm), as the message is read
    dy1: float = 0.0              # ... and up
    image1: str = ""              # a picture file, shown with the text or instead of it
    image_at1: int = 0            # where it goes: 0 left of the text, 1 right, 2 above, 3 below
    image_invert1: bool = False   # swap what counts as the picture and what as its background
    text_fil1: int = 2            # filament of the letters
    back_fil1: int = 1            # filament behind the letters

    # --- message 2 ----------------------------------------------------------
    text2: str = "DIRTY"
    font2: str = "Arial"
    bold2: bool = True
    italic2: bool = False
    size2: float = 0.0
    spacing2: float = 0.0
    text_dir2: int = 0
    dx2: float = 0.0
    dy2: float = 0.0
    image2: str = ""
    image_at2: int = 0
    image_invert2: bool = False
    text_fil2: int = 3
    back_fil2: int = 1

    margin: float = 4.0           # clear space between the frame and the messages (mm)

    # --- sign ---------------------------------------------------------------
    width: float = 200.0          # mm, left to right as message 1 is read
    height: float = 60.0          # mm
    roundness: float = 0.0        # 0 square corners ... 1 semicircular ends ... 2 a full circle
    #                               (past 1 the shorter side grows to match the longer one)
    base_thickness: float = 2.0   # solid back under the ridges (mm)
    frame: float = 3.0            # flat rim round the ridges (mm, 0 = none)

    # --- mounting -----------------------------------------------------------
    mount: int = NO_MOUNT         # 1 = pockets in the back for magnets, 2 = eyelets to hang it by
    mount_count: int = 1          # magnets: 1 (in the middle, to spin the sign on), 2 or 4;
    #                               eyelets: 1 or 2
    magnet_d: float = 10.0        # magnet diameter (mm)
    magnet_t: float = 2.0         # magnet thickness (mm)
    mount_inset: float = 5.0      # from the sign's edge to the edge of a magnet (mm)
    hole_d: float = 4.0           # hole through an eyelet (mm)

    # --- lenticular effect --------------------------------------------------
    ridge_dir: int = 0            # 0 = ridges run across (message changes with up / down),
    #                               1 = ridges run up and down (changes with left / right)
    flip: bool = True             # turn the sign upside down to change the message
    view_side: int = 0            # message 1 is seen from: 0 above / the left, 1 below / the right
    pitch: float = 3.0            # ridge spacing (mm) - the size of one picture line
    view_angle: float = 30.0      # degrees off straight-on from which one message shows alone
    crest: float = 0.4            # flat on top of each ridge (mm)
    ridges_where: int = 0         # 0 = ridges over the whole face; elsewhere the grooves are filled
    #                               in, level with the ridge tops: 1 = ridges in a block round the
    #                               messages, 2 = only where the two messages differ in colour

    # --- filaments ----------------------------------------------------------
    color1: str = "#F3F1E8"
    color2: str = "#2FA24F"
    color3: str = "#D8362B"
    color4: str = "#2B2E34"
    base_fil: int = 1             # filament of the back and the frame
    crest_fil: int = 1            # filament of the top of every ridge; 0 = half from each
    #                               message (fuller colour, but the messages ghost)

    # --- printer ------------------------------------------------------------
    bed_x: float = 256.0
    bed_y: float = 256.0
    layer_height: float = 0.2     # the sign is a whole number of these thick
    line_width: float = 0.42      # the slicer's line width: nothing narrower gets printed
    min_detail: float = 0.5       # shortest piece of colour along a ridge (mm)

    # ---------------------------------------------------------------- utils
    def copy(self) -> "SignParams":
        return SignParams(**asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)

    def colors(self) -> dict:
        """Filament number -> (r, g, b) in 0..1."""
        return {k: hex_to_rgb(getattr(self, f"color{k}")) for k in FILAMENTS}

    @classmethod
    def from_dict(cls, d: dict) -> "SignParams":
        p = cls()
        if "text_dir" in d:                 # designs from before each message had its own
            d = {"text_dir1": d["text_dir"], "text_dir2": d["text_dir"], **d}
        for f in fields(cls):
            if f.name not in d:
                continue
            cur, v = getattr(p, f.name), d[f.name]
            try:
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int):
                    v = int(v)
                elif isinstance(cur, float):
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            setattr(p, f.name, v)
        fresh = cls()
        allowed = {"crest_fil": (0,) + FILAMENTS, "mount": (NO_MOUNT, MAGNETS, EYELETS),
                   "mount_count": (1, 2, 4), "ridge_dir": (0, 1), "view_side": (0, 1),
                   "ridges_where": (EVERYWHERE, BLOCK, WHERE_NEEDED)}
        for key in ("text_fil1", "back_fil1", "text_fil2", "back_fil2", "base_fil"):
            allowed[key] = FILAMENTS
        for key in ("text_dir1", "text_dir2", "image_at1", "image_at2"):
            allowed[key] = (0, 1, 2, 3)
        for key, values in allowed.items():
            if getattr(p, key) not in values:
                setattr(p, key, getattr(fresh, key))
        p.roundness = min(max(p.roundness, 0.0), 2.0)
        for k in FILAMENTS:
            try:
                hex_to_rgb(getattr(p, f"color{k}"))
            except ValueError:
                setattr(p, f"color{k}", getattr(fresh, f"color{k}"))
        return p


def outline_size(p: SignParams):
    """(width, height, corner radius) of the sign's outline, from its size and roundness.

    Up to 1 the corners round off until the short ends are semicircles.  Past 1
    the shorter side grows towards the longer one, so that at 2 the sign is a
    circle as wide as its longer side.
    """
    t = min(max(p.roundness, 0.0), 2.0)
    w, h = p.width, p.height
    if t > 1.0:
        if w >= h:
            h += (w - h) * (t - 1.0)
        else:
            w += (h - w) * (t - 1.0)
    return w, h, 0.5 * min(w, h) * min(t, 1.0)


def hex_to_rgb(text: str):
    """'#RRGGBB' -> (r, g, b) in 0..1."""
    t = text.strip().lstrip("#")
    if len(t) != 6:
        raise ValueError(f"not a colour: {text!r}")
    return tuple(int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def save_design(path: str, params: SignParams):
    """Write a design.  Pictures kept beside it (or in a folder beside it) are noted by
    where they are from the design, so the two can be moved or shared together."""
    values = params.to_dict()
    home = os.path.dirname(os.path.abspath(path))
    for key in ("image1", "image2"):
        picture = values[key].strip()
        if picture and os.path.isabs(picture):
            try:
                beside = os.path.relpath(picture, home)
            except ValueError:                  # on another drive
                continue
            if not beside.startswith(".."):
                values[key] = beside.replace("\\", "/")
    data = {"format": FILE_FORMAT, "version": FILE_VERSION, "params": values}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load_design(path: str) -> SignParams:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != FILE_FORMAT:
        raise ValueError("Not a lenticular sign design file")
    params = SignParams.from_dict(data.get("params", {}))
    home = os.path.dirname(os.path.abspath(path))
    for key in ("image1", "image2"):            # pictures noted from the design: find them again
        picture = getattr(params, key).strip()
        if picture and not os.path.isabs(picture):
            setattr(params, key, os.path.normpath(os.path.join(home, picture)))
    return params
