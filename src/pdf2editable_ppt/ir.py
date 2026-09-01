"""Intermediate representation.

The IR is deliberately independent of both the PDF parser and the PPTX
writer: extraction fills it in, analysis rewrites it, and the builder reads
it.  Nothing in this module imports pdfminer or python-pptx.

Coordinates are PDF points in *visual page space*: y-up, origin at the
bottom-left of the visually upright page (page /Rotate already applied).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .units import IDENTITY, Matrix, Rect


class ElementType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    LINE = "line"
    RECT = "rect"
    ELLIPSE = "ellipse"
    FREEFORM = "freeform"
    TABLE = "table"
    GROUP = "group"
    VECTOR_FALLBACK = "vectorFallback"
    RASTER_FALLBACK = "rasterFallback"


class Outcome(str, enum.Enum):
    """Final disposition of one element in the produced deck."""

    NATIVE = "native"
    NATIVE_WITH_WARNING = "native-with-warning"
    SVG_FALLBACK = "svg-fallback"
    RASTER_FALLBACK = "raster-fallback"
    PAGE_FALLBACK = "page-fallback"
    UNSUPPORTED = "unsupported"


# ── path geometry ────────────────────────────────────────────────────────────


class SegmentOp(str, enum.Enum):
    MOVE_TO = "moveTo"
    LINE_TO = "lineTo"
    CUBIC_TO = "cubicBezierTo"
    CLOSE = "closePath"


@dataclass
class Segment:
    """One path command.  ``points`` holds the operand points in order.

    - moveTo / lineTo: 1 point
    - cubicBezierTo:   3 points (control1, control2, end)
    - closePath:       0 points
    """

    op: SegmentOp
    points: Tuple[Tuple[float, float], ...] = ()

    def transformed(self, fn) -> "Segment":
        return Segment(self.op, tuple(fn(p) for p in self.points))


@dataclass
class Path:
    """A full PDF path: an ordered list of segments plus its paint parameters."""

    segments: List[Segment] = field(default_factory=list)
    even_odd: bool = False

    def has_curves(self) -> bool:
        return any(s.op is SegmentOp.CUBIC_TO for s in self.segments)

    def subpath_count(self) -> int:
        return sum(1 for s in self.segments if s.op is SegmentOp.MOVE_TO)

    def points(self) -> List[Tuple[float, float]]:
        return [p for s in self.segments for p in s.points]


@dataclass
class Style:
    """Paint style shared by shape elements."""

    fill_color: Optional[str] = None  # "RRGGBB"
    fill_alpha: float = 1.0
    stroke_color: Optional[str] = None  # "RRGGBB"
    stroke_alpha: float = 1.0
    stroke_width_pt: float = 0.0
    dash: Optional[Tuple[Sequence[float], float]] = None  # (pattern, phase)
    line_cap: int = 0
    line_join: int = 0

    @property
    def has_fill(self) -> bool:
        return self.fill_color is not None and self.fill_alpha > 0.0

    @property
    def has_stroke(self) -> bool:
        return (
            self.stroke_color is not None
            and self.stroke_alpha > 0.0
            and self.stroke_width_pt >= 0.0
        )


# ── text ─────────────────────────────────────────────────────────────────────


@dataclass
class TextRun:
    """A maximal stretch of characters sharing every visual attribute."""

    text: str
    font_family: str
    size_pt: float
    color: str = "000000"
    bold: bool = False
    italic: bool = False
    char_space_pt: float = 0.0
    bbox: Optional[Rect] = None
    source_font: str = ""


@dataclass
class TextLine:
    runs: List[TextRun] = field(default_factory=list)
    bbox: Optional[Rect] = None
    baseline_y: float = 0.0
    rotation_deg: float = 0.0

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class TextContent:
    lines: List[TextLine] = field(default_factory=list)
    align: str = "l"  # l | ctr | r | just
    line_spacing_pt: Optional[float] = None
    rotation_deg: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


# ── images ───────────────────────────────────────────────────────────────────


@dataclass
class ImageAsset:
    """A decoded-or-passthrough image payload plus its provenance."""

    asset_id: str
    data: bytes
    ext: str  # "jpg" | "png"
    width_px: int
    height_px: int
    passthrough: bool = False
    """True when ``data`` is the PDF's own compressed stream, byte for byte."""
    source_sha256: str = ""
    output_sha256: str = ""
    has_alpha: bool = False
    note: str = ""


@dataclass
class ImageContent:
    asset_id: str
    crop: Optional[Tuple[float, float, float, float]] = None
    """Fractional (left, top, right, bottom) crop, DrawingML srcRect order."""
    flip_h: bool = False
    flip_v: bool = False


# ── tables ───────────────────────────────────────────────────────────────────


@dataclass
class CellBorder:
    color: str = "000000"
    width_pt: float = 0.0
    dash: Optional[str] = None
    present: bool = False


@dataclass
class TableCell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    merged_by: Optional[Tuple[int, int]] = None
    text: Optional[TextContent] = None
    fill_color: Optional[str] = None
    fill_alpha: float = 1.0
    v_align: str = "t"  # t | ctr | b
    margins_pt: Tuple[float, float, float, float] = (2.0, 1.0, 2.0, 1.0)  # l,t,r,b
    borders: Dict[str, CellBorder] = field(default_factory=dict)  # l/t/r/b
    bbox: Optional[Rect] = None


@dataclass
class TableContent:
    rows: int
    cols: int
    col_widths_pt: List[float] = field(default_factory=list)
    row_heights_pt: List[float] = field(default_factory=list)
    cells: List[TableCell] = field(default_factory=list)


# ── elements ─────────────────────────────────────────────────────────────────


@dataclass
class Element:
    """One IR element.  ``content`` is typed by ``type``."""

    id: str
    type: ElementType
    bbox: Rect
    transform: Matrix = IDENTITY
    z_index: int = 0
    clip_path: Optional[Rect] = None
    opacity: float = 1.0
    style: Style = field(default_factory=Style)
    content: Any = None
    source_asset_id: Optional[str] = None
    confidence: float = 1.0
    fallback_reason: Optional[str] = None
    source_paint_order: int = 0
    rotation_deg: float = 0.0
    outcome: Outcome = Outcome.NATIVE
    notes: List[str] = field(default_factory=list)
    consumed: bool = False
    """Set when another element (e.g. a table) absorbed this one."""
    paint_bbox: Optional[Rect] = None
    """Axis-aligned page-space bounds of the painted result.

    ``bbox`` is what DrawingML needs -- for a rotated shape that is the
    *unrotated* extent placed at the rotated centre, which is not where the ink
    lands.  Anything that crops pixels (fallback renders, visual checks) must
    use :meth:`render_bounds` instead.
    """

    def render_bounds(self) -> Rect:
        return self.paint_bbox if self.paint_bbox is not None else self.bbox

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


@dataclass
class Page:
    index: int  # 0-based
    width_pt: float
    height_pt: float
    rotation: int = 0
    crop_box: Optional[Rect] = None
    background: Optional[str] = None
    elements: List[Element] = field(default_factory=list)
    scanned: bool = False
    degraded: bool = False
    degraded_reason: Optional[str] = None

    def live_elements(self) -> List[Element]:
        return [e for e in self.elements if not e.consumed]


@dataclass
class Document:
    pages: List[Page] = field(default_factory=list)
    assets: Dict[str, ImageAsset] = field(default_factory=dict)
    source_path: str = ""
    warnings: List[str] = field(default_factory=list)
