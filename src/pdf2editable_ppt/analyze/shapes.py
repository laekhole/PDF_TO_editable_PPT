"""Path classification.

A PDF path carries no notion of "this is a circle".  We recover the small set
of shapes we can prove — line, rectangle, rounded rectangle, ellipse — and
send everything else to a DrawingML custom geometry with the original Bezier
control points intact.  Nothing is ever *approximated* into a preset: a curve
that is not provably an ellipse stays a freeform, because a wrong preset is a
damaged drawing and a freeform is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..ir import Path, Segment, SegmentOp
from ..units import Rect, rect_from_points

Point = Tuple[float, float]

# Kappa: the Bezier control-point offset that approximates a quarter circle.
KAPPA = 0.5522847498307936

# Absolute tolerance in points for "these two coordinates are the same".
POINT_TOL = 0.35
# Relative tolerance for control-point placement in curve fitting.
CURVE_REL_TOL = 0.12


def _close(a: float, b: float, tol: float = POINT_TOL) -> bool:
    return abs(a - b) <= tol


def _pt_close(a: Point, b: Point, tol: float = POINT_TOL) -> bool:
    return _close(a[0], b[0], tol) and _close(a[1], b[1], tol)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class Subpath:
    segments: List[Segment]
    closed: bool

    def points(self) -> List[Point]:
        return [p for s in self.segments for p in s.points]

    def start(self) -> Optional[Point]:
        for s in self.segments:
            if s.points:
                return s.points[0]
        return None

    def has_curves(self) -> bool:
        return any(s.op is SegmentOp.CUBIC_TO for s in self.segments)

    def anchors(self) -> List[Point]:
        """End points of each segment (control points excluded)."""
        out: List[Point] = []
        for s in self.segments:
            if s.op is SegmentOp.CLOSE:
                continue
            if s.points:
                out.append(s.points[-1])
        return out


def split_subpaths(path: Path) -> List[Subpath]:
    """Split a path into subpaths at every moveTo."""
    out: List[Subpath] = []
    current: List[Segment] = []
    closed = False
    for seg in path.segments:
        if seg.op is SegmentOp.MOVE_TO:
            if current:
                out.append(Subpath(current, closed))
            current = [seg]
            closed = False
        elif seg.op is SegmentOp.CLOSE:
            closed = True
            current.append(seg)
        else:
            current.append(seg)
    if current:
        out.append(Subpath(current, closed))
    return out


# ── shape recognisers ────────────────────────────────────────────────────────


@dataclass
class ShapeMatch:
    kind: str  # "line" | "rect" | "roundRect" | "ellipse" | "freeform"
    bbox: Rect
    rotation_deg: float = 0.0
    adjust: Optional[float] = None  # roundRect corner radius as a fraction
    confidence: float = 1.0
    reason: str = ""


def _simplify(pts: Sequence[Point], tol: float = POINT_TOL) -> List[Point]:
    """Drop repeated and collinear anchors.

    Real writers emit rectangles with a duplicated closing point, and often
    with an extra vertex in the middle of an edge.  Those are the same
    rectangle; keeping them would push every such shape into a freeform.
    """
    out: List[Point] = []
    for p in pts:
        if out and _pt_close(out[-1], p, tol):
            continue
        out.append(p)
    if len(out) > 1 and _pt_close(out[0], out[-1], tol):
        out.pop()
    if len(out) < 3:
        return out
    trimmed: List[Point] = []
    n = len(out)
    for i in range(n):
        a, b, c = out[(i - 1) % n], out[i], out[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        span = max(_dist(a, b), _dist(b, c))
        if span > 0 and abs(cross) / span <= tol:
            continue  # b sits on the segment a-c
        trimmed.append(b)
    return trimmed if len(trimmed) >= 3 else out


def _fits_bbox_ellipse(pts: Sequence[Point], bbox: Rect) -> bool:
    """Do all anchors lie on the ellipse inscribed in ``bbox``?

    This catches an ellipse however the writer subdivided it -- LibreOffice
    emits arcs mixed with straight segments rather than four kappa curves --
    without ever accepting a shape that merely has a similar bounding box.
    """
    rx, ry = bbox.width / 2.0, bbox.height / 2.0
    if rx <= 1.0 or ry <= 1.0 or len(pts) < 6:
        return False
    cx, cy = bbox.cx, bbox.cy
    tol = max(0.02, POINT_TOL / min(rx, ry))
    touched = {"l": False, "r": False, "t": False, "b": False}
    for x, y in pts:
        u = (x - cx) / rx
        v = (y - cy) / ry
        if abs(u * u + v * v - 1.0) > tol * 2.2:
            return False
        if u < -0.98:
            touched["l"] = True
        if u > 0.98:
            touched["r"] = True
        if v < -0.98:
            touched["b"] = True
        if v > 0.98:
            touched["t"] = True
    return all(touched.values())


def _quad_is_rect(pts: Sequence[Point], tol: float = POINT_TOL) -> Optional[float]:
    """If four points form a rectangle, return its rotation in degrees, else None."""
    if len(pts) != 4:
        return None
    a, b, c, d = pts
    # opposite sides equal, diagonals equal
    if not _close(_dist(a, b), _dist(c, d), tol):
        return None
    if not _close(_dist(b, c), _dist(d, a), tol):
        return None
    if not _close(_dist(a, c), _dist(b, d), tol):
        return None
    if _dist(a, b) < 1e-6 or _dist(b, c) < 1e-6:
        return None
    # right angle at a
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (d[0] - a[0], d[1] - a[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    if abs(dot) > tol * max(_dist(a, b), _dist(a, d)):
        return None
    angle = math.degrees(math.atan2(v1[1], v1[0]))
    # normalise to (-45, 45] so we report the smallest rotation
    while angle > 45.0:
        angle -= 90.0
    while angle <= -45.0:
        angle += 90.0
    return angle


def _match_ellipse(sub: Subpath, bbox: Rect) -> Optional[ShapeMatch]:
    """Four cubics whose anchors sit on the bbox edge midpoints and whose
    control points sit at the kappa offsets = an axis-aligned ellipse."""
    cubics = [s for s in sub.segments if s.op is SegmentOp.CUBIC_TO]
    if len(cubics) != 4:
        return None
    if any(s.op is SegmentOp.LINE_TO for s in sub.segments):
        return None
    rx, ry = bbox.width / 2.0, bbox.height / 2.0
    if rx <= 0.1 or ry <= 0.1:
        return None
    cx, cy = bbox.cx, bbox.cy
    expected = {
        (round(cx + rx, 1), round(cy, 1)),
        (round(cx - rx, 1), round(cy, 1)),
        (round(cx, 1), round(cy + ry, 1)),
        (round(cx, 1), round(cy - ry, 1)),
    }
    anchors = sub.anchors()
    tol = max(POINT_TOL, CURVE_REL_TOL * min(rx, ry))
    matched = 0
    for a in anchors:
        for e in expected:
            if _pt_close(a, e, tol):
                matched += 1
                break
    if matched < 4:
        return None
    # verify one control-point offset magnitude
    start = sub.start()
    if start is None:
        return None
    prev = start
    for seg in sub.segments:
        if seg.op is not SegmentOp.CUBIC_TO:
            continue
        c1, _c2, end = seg.points
        off = _dist(prev, c1)
        expected_off = KAPPA * (ry if _close(prev[1], cy, tol) else rx)
        if expected_off > 0 and abs(off - expected_off) > max(0.5, CURVE_REL_TOL * expected_off * 2):
            return None
        prev = end
    return ShapeMatch("ellipse", bbox, confidence=0.97, reason="4 kappa-fitted cubics")


def _match_round_rect(sub: Subpath, bbox: Rect) -> Optional[ShapeMatch]:
    """Four straight edges joined by four equal quarter-arc corners."""
    ops = [s.op for s in sub.segments if s.op is not SegmentOp.CLOSE]
    cubics = [s for s in sub.segments if s.op is SegmentOp.CUBIC_TO]
    lines = [s for s in sub.segments if s.op is SegmentOp.LINE_TO]
    if len(cubics) != 4 or not (3 <= len(lines) <= 5):
        return None
    if bbox.width <= 0.5 or bbox.height <= 0.5:
        return None
    # every anchor must lie on the bbox border
    radii: List[float] = []
    start = sub.start()
    if start is None:
        return None
    prev = start
    for seg in sub.segments:
        if seg.op is SegmentOp.CUBIC_TO:
            c1, _c2, end = seg.points
            radii.append(max(_dist(prev, c1) / KAPPA, 0.0))
            prev = end
        elif seg.op is SegmentOp.LINE_TO:
            prev = seg.points[-1]
    if not radii:
        return None
    r = sum(radii) / len(radii)
    if r <= 0.2:
        return None
    if any(abs(x - r) > max(0.5, 0.15 * r) for x in radii):
        return None
    half_min = min(bbox.width, bbox.height) / 2.0
    if r > half_min * 1.05:
        return None
    adj = min(0.5, r / max(1e-6, min(bbox.width, bbox.height)))
    return ShapeMatch(
        "roundRect", bbox, adjust=adj, confidence=0.93, reason="4 equal corner arcs"
    )


def classify(path: Path, stroke_only: bool) -> ShapeMatch:
    """Classify a whole path.  Multi-subpath paths are always freeform."""
    subs = split_subpaths(path)
    pts = path.points()
    bbox = rect_from_points(pts)
    if bbox is None:
        return ShapeMatch("freeform", Rect(0, 0, 0, 0), confidence=0.0, reason="empty path")

    if len(subs) != 1:
        return ShapeMatch(
            "freeform",
            bbox,
            confidence=0.9,
            reason="%d subpaths (holes or compound outline) stay one custom geometry"
            % len(subs),
        )

    sub = subs[0]
    anchors = sub.anchors()

    # single straight segment -> line
    if (
        stroke_only
        and not sub.closed
        and len(anchors) == 2
        and all(s.op is not SegmentOp.CUBIC_TO for s in sub.segments)
    ):
        return ShapeMatch("line", bbox, confidence=1.0, reason="two-point open stroke")

    if not sub.has_curves():
        quad = _simplify(anchors)
        if len(quad) == 4 and (sub.closed or _pt_close(anchors[0], anchors[-1])):
            rot = _quad_is_rect(quad)
            if rot is not None:
                if abs(rot) < 0.05:
                    return ShapeMatch("rect", bbox, confidence=1.0, reason="axis-aligned quad")
                # rotated rectangle: report the upright box plus the angle
                w = _dist(quad[0], quad[1])
                h = _dist(quad[1], quad[2])
                up = Rect(bbox.cx - w / 2, bbox.cy - h / 2, bbox.cx + w / 2, bbox.cy + h / 2)
                return ShapeMatch(
                    "rect", up, rotation_deg=rot, confidence=0.95, reason="rotated quad"
                )
        return ShapeMatch(
            "freeform", bbox, confidence=0.95, reason="polygon with %d anchors" % len(anchors)
        )

    ellipse = _match_ellipse(sub, bbox)
    if ellipse is not None:
        return ellipse
    round_rect = _match_round_rect(sub, bbox)
    if round_rect is not None:
        return round_rect
    if sub.closed and _fits_bbox_ellipse(sub.anchors(), bbox):
        return ShapeMatch(
            "ellipse", bbox, confidence=0.9, reason="every anchor lies on the inscribed ellipse"
        )
    return ShapeMatch(
        "freeform",
        bbox,
        confidence=0.9,
        reason="curved outline kept as Bezier custom geometry",
    )
