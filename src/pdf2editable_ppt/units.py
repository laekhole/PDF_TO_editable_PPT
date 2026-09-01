"""Coordinate and unit conversion between PDF space and DrawingML (EMU) space.

PDF space is y-up with the origin at the bottom-left of the page (after the
CropBox offset is removed).  DrawingML is y-down with the origin at the
top-left of the slide.  Every conversion in the writer goes through this
module so the flip lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

EMU_PER_PT = 12700
EMU_PER_INCH = 914400

Matrix = Tuple[float, float, float, float, float, float]
"""PDF transformation matrix (a, b, c, d, e, f)."""

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def pt_to_emu(pt: float) -> int:
    """Points to EMU, rounded to the nearest integer EMU."""
    return int(round(pt * EMU_PER_PT))


def emu_to_pt(emu: float) -> float:
    return emu / EMU_PER_PT


def apply_matrix(m: Matrix, point: Sequence[float]) -> Tuple[float, float]:
    """Apply a PDF matrix to a point."""
    a, b, c, d, e, f = m
    x, y = point[0], point[1]
    return (a * x + c * y + e, b * x + d * y + f)


def apply_matrix_norm(m: Matrix, vec: Sequence[float]) -> Tuple[float, float]:
    """Apply only the linear part of a matrix (for direction vectors)."""
    a, b, c, d, _e, _f = m
    x, y = vec[0], vec[1]
    return (a * x + c * y, b * x + d * y)


def mat_multiply(m1: Matrix, m2: Matrix) -> Matrix:
    """Return m1 * m2 in PDF row-vector convention (apply m1 first, then m2)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def matrix_scale(m: Matrix) -> float:
    """Average uniform scale factor of a matrix (used for line widths)."""
    a, b, c, d, _e, _f = m
    sx = (a * a + b * b) ** 0.5
    sy = (c * c + d * d) ** 0.5
    return (sx + sy) / 2.0


def matrix_rotation_deg(m: Matrix) -> float:
    """Rotation angle of a matrix in degrees, counter-clockwise in PDF space."""
    import math

    a, b, _c, _d, _e, _f = m
    return math.degrees(math.atan2(b, a))


def is_axis_aligned(m: Matrix, tol: float = 1e-6) -> bool:
    """True when the matrix has no rotation or skew component."""
    _a, b, c, _d, _e, _f = m
    return abs(b) <= tol and abs(c) <= tol


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in PDF points, y-up, x0 <= x1 and y0 <= y1."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def normalized(self) -> "Rect":
        return Rect(
            min(self.x0, self.x1),
            min(self.y0, self.y1),
            max(self.x0, self.x1),
            max(self.y0, self.y1),
        )

    def union(self, other: "Rect") -> "Rect":
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersection(self, other: "Rect") -> "Rect | None":
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1, y1)

    def contains(self, other: "Rect", tol: float = 0.0) -> bool:
        return (
            self.x0 - tol <= other.x0
            and self.y0 - tol <= other.y0
            and self.x1 + tol >= other.x1
            and self.y1 + tol >= other.y1
        )

    def expanded(self, pad: float) -> "Rect":
        return Rect(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


def rect_from_points(points: Iterable[Sequence[float]]) -> Rect | None:
    xs: list[float] = []
    ys: list[float] = []
    for p in points:
        xs.append(p[0])
        ys.append(p[1])
    if not xs:
        return None
    return Rect(min(xs), min(ys), max(xs), max(ys))


def union_all(rects: Iterable[Rect]) -> Rect | None:
    out: Rect | None = None
    for r in rects:
        out = r if out is None else out.union(r)
    return out


@dataclass(frozen=True)
class PageGeometry:
    """Maps one PDF page onto one slide.

    ``rotation`` is the /Rotate value of the page (0/90/180/270).  The slide
    always uses the *visual* page size, so a 90-degree rotated page produces a
    landscape slide and every element is rotated into place.
    """

    media_x0: float
    media_y0: float
    media_width: float
    media_height: float
    rotation: int = 0
    offset_x_pt: float = 0.0
    """Left inset of this page inside the slide (mixed page sizes letterbox)."""
    offset_y_pt: float = 0.0
    """Top inset of this page inside the slide."""

    @property
    def visual_width(self) -> float:
        return self.media_height if self.rotation in (90, 270) else self.media_width

    @property
    def visual_height(self) -> float:
        return self.media_width if self.rotation in (90, 270) else self.media_height

    def to_visual(self, x: float, y: float) -> Tuple[float, float]:
        """PDF page point -> visual page point (still y-up, origin bottom-left).

        /Rotate turns the page CLOCKWISE by that many degrees when it is
        displayed.  In a y-up frame a clockwise quarter turn is
        ``(x, y) -> (y, -x)``, translated back into the positive quadrant.
        """
        x -= self.media_x0
        y -= self.media_y0
        w, h = self.media_width, self.media_height
        r = self.rotation % 360
        if r == 90:
            return (y, w - x)
        if r == 180:
            return (w - x, h - y)
        if r == 270:
            return (h - y, x)
        return (x, y)

    def rect_to_emu(self, rect: Rect) -> Tuple[int, int, int, int]:
        """Visual-space y-up rect -> (x, y, cx, cy) EMU in slide space."""
        r = rect.normalized()
        x = pt_to_emu(self.offset_x_pt + r.x0)
        y = pt_to_emu(self.offset_y_pt + self.visual_height - r.y1)
        cx = max(1, pt_to_emu(r.width))
        cy = max(1, pt_to_emu(r.height))
        return (x, y, cx, cy)

    def point_to_emu(self, x: float, y: float) -> Tuple[int, int]:
        return (
            pt_to_emu(self.offset_x_pt + x),
            pt_to_emu(self.offset_y_pt + self.visual_height - y),
        )
