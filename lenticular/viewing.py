"""Working out the angle a sign is really seen from, to set its ridges by."""

from __future__ import annotations

import math

SAFETY = 5.0              # degrees kept in hand between where you stand and the ridges' angle
LEAST, MOST = 10.0, 75.0  # the range of the viewing angle setting


def seen_angles(span: float, offset: float, distance: float):
    """Degrees off straight-on from which the eye sees the ridged face.

    span: the face's size across the ridges.  offset: how far the eye is from
    level with the middle of the sign, across the ridges - above it, or to one
    side.  distance: how far out from the face the eye is.  All in one unit.
    Returns (nearest edge, middle, furthest edge): the edge on the eye's own
    side is seen from closest to straight-on.
    """
    offset = abs(offset)
    return tuple(math.degrees(math.atan2(offset - e, distance)) for e in (0.5 * span, 0.0, -0.5 * span))


def angle_to_use(span: float, offset: float, distance: float):
    """The viewing angle setting for that eye: a little less than the flattest it sees.

    None if even that is too close to straight-on for one message to show alone.
    """
    flattest = seen_angles(span, offset, distance)[0] - SAFETY
    return None if flattest < LEAST else float(min(math.floor(flattest), MOST))
