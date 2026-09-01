"""Character -> run -> line -> text-box reconstruction.

PDF has no paragraphs, no words and often no spaces: it has glyphs at
coordinates.  We rebuild the visual structure only, never a semantic one.
Each source line becomes one paragraph so the deck keeps the original line
breaks exactly; nothing is reflowed.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..extract.content import CharRecord
from ..ir import TextContent, TextLine, TextRun
from ..units import Rect, rect_from_points, union_all

# A gap wider than this fraction of the font size means the writer used
# positioning instead of a space character.
SPACE_GAP_RATIO = 0.28
# Baselines within this fraction of the font size belong to the same line.
BASELINE_TOL_RATIO = 0.32
# A pen gap this large is a column boundary, not a word space: the writer
# jumped across a layout gutter and the two sides are separate text boxes.
COLUMN_GAP_RATIO = 2.2
COLUMN_GAP_MIN_PT = 12.0
# Lines further apart than this multiple of the line pitch start a new block.
BLOCK_GAP_RATIO = 1.75
# Two stacked segments belong to the same block when this share of the
# narrower one overlaps the other horizontally.
BLOCK_OVERLAP_RATIO = 0.45
# Left edges within this many points count as the same column.
BLOCK_LEFT_TOL_PT = 3.0
# Alignment is only claimed when this share of lines agrees.
ALIGN_AGREE = 0.8
# Where PowerPoint puts the first baseline inside a top-anchored text box:
# one line height down, minus the font's descent.  Measured against rendered
# output (see docs/testing.md, "Text placement calibration"); the value is the
# descent of the sans-serif fallback and is stable to about +/-0.01 em across
# sizes.  tests/test_text_placement.py pins it.
DESCENT_EM = 0.21
# Line height DrawingML uses when no explicit lnSpc is given.
AUTO_LINE_RATIO = 1.2


def _angle_of(char: CharRecord) -> float:
    a, b = char.matrix[0], char.matrix[1]
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(b, a))


def _quantize_angle(deg: float) -> int:
    """Snap to the nearest degree, folding -180..180 into a stable bucket."""
    q = int(round(deg))
    if q == -180:
        q = 180
    return q


def _rotate(p: Tuple[float, float], deg: float) -> Tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return (
        0x1100 <= cp <= 0x11FF
        or 0x3040 <= cp <= 0x30FF
        or 0x3130 <= cp <= 0x318F
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xAC00 <= cp <= 0xD7AF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFF00 <= cp <= 0xFF60
    )


@dataclass
class _PlacedChar:
    """A character with coordinates in the *unrotated* frame of its text run."""

    src: CharRecord
    x0: float
    x1: float
    y: float  # baseline in the unrotated frame
    top: float
    bottom: float
    pen_x: float = 0.0
    """Pen position (text-space origin) of this glyph."""
    pen_next: float = 0.0
    """Where the pen lands after this glyph's advance."""


def _style_key(c: CharRecord) -> Tuple:
    return (
        c.font_family,
        round(c.size_pt, 2),
        c.color,
        c.bold,
        c.italic,
        round(c.alpha, 2),
    )


def _build_lines(placed: List[_PlacedChar]) -> List[List[_PlacedChar]]:
    """Bucket characters into visual lines by baseline, then order each by x."""
    if not placed:
        return []
    remaining = sorted(placed, key=lambda p: (-p.y, p.x0))
    lines: List[List[_PlacedChar]] = []
    current: List[_PlacedChar] = [remaining[0]]
    ref_y = remaining[0].y
    ref_size = max(1.0, remaining[0].src.size_pt)
    for p in remaining[1:]:
        tol = max(0.6, BASELINE_TOL_RATIO * max(ref_size, p.src.size_pt))
        if abs(p.y - ref_y) <= tol:
            current.append(p)
        else:
            lines.append(sorted(current, key=lambda q: q.x0))
            current = [p]
            ref_y = p.y
            ref_size = max(1.0, p.src.size_pt)
    lines.append(sorted(current, key=lambda q: q.x0))
    return lines


def _runs_for_line(line: List[_PlacedChar]) -> List[TextRun]:
    runs: List[TextRun] = []
    buf: List[str] = []
    boxes: List[Rect] = []
    key: Optional[Tuple] = None
    head: Optional[CharRecord] = None
    prev: Optional[_PlacedChar] = None

    def flush() -> None:
        nonlocal buf, boxes, key, head
        if head is not None and buf:
            text = "".join(buf)
            runs.append(
                TextRun(
                    text=text,
                    font_family=head.font_family,
                    size_pt=head.size_pt,
                    color=head.color,
                    bold=head.bold,
                    italic=head.italic,
                    bbox=union_all(boxes),
                    source_font=head.source_font,
                )
            )
        buf = []
        boxes = []
        key = None
        head = None

    for p in line:
        c = p.src
        gap_space = ""
        if prev is not None:
            # Measure the gap at the *pen*, not between glyph boxes: a Hangul
            # glyph's ink is narrower than its advance, so a bbox-to-bbox gap
            # invents a space at every CJK/Latin boundary.
            gap = p.pen_x - prev.pen_next
            size = max(1.0, max(prev.src.size_pt, c.size_pt))
            already_space = c.text[:1].isspace() or prev.src.text[-1:].isspace()
            if not already_space and gap > SPACE_GAP_RATIO * size:
                gap_space = " "
        k = _style_key(c)
        if key is None:
            key = k
            head = c
        elif k != key:
            if gap_space:
                buf.append(gap_space)
                gap_space = ""
            flush()
            key = k
            head = c
        if gap_space:
            buf.append(gap_space)
        buf.append(c.text)
        boxes.append(c.bbox)
        prev = p
    flush()
    return runs


def _infer_align(lines: List[Tuple[float, float]], box: Rect) -> str:
    """Guess paragraph alignment from how the line boxes sit inside the block."""
    if len(lines) < 2:
        return "l"
    n = len(lines)
    tol = 1.5
    left = sum(1 for x0, _x1 in lines if abs(x0 - box.x0) <= tol)
    right = sum(1 for _x0, x1 in lines if abs(x1 - box.x1) <= tol)
    center = sum(
        1 for x0, x1 in lines if abs(((x0 + x1) / 2.0) - box.cx) <= tol
    )
    if left / n >= ALIGN_AGREE and right / n >= ALIGN_AGREE and n >= 3:
        return "just"
    if center / n >= ALIGN_AGREE and left / n < ALIGN_AGREE:
        return "ctr"
    if right / n >= ALIGN_AGREE and left / n < ALIGN_AGREE:
        return "r"
    return "l"


@dataclass
class TextBlock:
    content: TextContent
    bbox: Rect
    """Text-box rectangle in the block's own (unrotated) frame."""
    paint_bbox: Rect
    """Axis-aligned page-space bounds of the ink, rotation included."""
    rotation_deg: float
    char_count: int
    paint_order: int
    clip: Optional[Rect]
    alpha: float


def _split_columns(
    line: List[_PlacedChar], boundaries: Sequence[float] = ()
) -> List[List[_PlacedChar]]:
    """Cut one baseline row at layout gutters and at drawn column rules.

    A column layout puts several independent cells on one baseline.  Joining
    them would turn "Item | Owner | Status" into one sentence and lose the
    columns, so a pen gap wider than a couple of ems starts a new segment.
    ``boundaries`` adds the x positions of vertical rules the page actually
    draws, which is what separates two table cells whose contents nearly touch
    across a ruling -- a gap too small for the generic rule to catch.
    """
    if len(line) < 2:
        return [line]
    segments: List[List[_PlacedChar]] = [[line[0]]]
    for prev, cur in zip(line, line[1:]):
        size = max(1.0, max(prev.src.size_pt, cur.src.size_pt))
        gap = cur.pen_x - prev.pen_next
        crossed = any(prev.x1 <= b <= cur.x0 for b in boundaries)
        if crossed or gap > max(COLUMN_GAP_MIN_PT, COLUMN_GAP_RATIO * size):
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


@dataclass
class _Segment:
    chars: List[_PlacedChar]
    y: float
    x0: float
    x1: float
    size: float


def _segments_of(
    lines: List[List[_PlacedChar]], boundaries: Sequence[float] = ()
) -> List[_Segment]:
    out: List[_Segment] = []
    for line in lines:
        for part in _split_columns(line, boundaries):
            if not part:
                continue
            out.append(
                _Segment(
                    chars=part,
                    y=part[0].y,
                    x0=min(p.x0 for p in part),
                    x1=max(p.x1 for p in part),
                    size=max(p.src.size_pt for p in part),
                )
            )
    out.sort(key=lambda s: (-s.y, s.x0))
    return out


@dataclass
class _OpenBlock:
    segments: List[_Segment]
    x0: float
    x1: float
    last_y: float
    pitch: Optional[float]


def _group_blocks(
    lines: List[List[_PlacedChar]], boundaries: Sequence[float] = ()
) -> List[List[List[_PlacedChar]]]:
    """Group baseline segments into text boxes.

    A segment joins the block directly above it when the vertical step matches
    that block's rhythm and the two overlap horizontally (or share a left
    edge).  Everything else opens a new block, which is what keeps side-by-side
    columns apart.
    """
    blocks: List[_OpenBlock] = []
    for seg in _segments_of(lines, boundaries):
        best: Optional[_OpenBlock] = None
        best_gap = float("inf")
        for block in blocks:
            gap = block.last_y - seg.y
            if gap <= 0.01:
                continue
            pitch = block.pitch or max(seg.size, 1.0) * 1.2
            if gap > BLOCK_GAP_RATIO * pitch:
                continue
            overlap = min(block.x1, seg.x1) - max(block.x0, seg.x0)
            narrower = max(1e-6, min(block.x1 - block.x0, seg.x1 - seg.x0))
            aligned = abs(seg.x0 - block.x0) <= BLOCK_LEFT_TOL_PT
            centred = abs((seg.x0 + seg.x1) / 2 - (block.x0 + block.x1) / 2) <= BLOCK_LEFT_TOL_PT
            right_aligned = abs(seg.x1 - block.x1) <= BLOCK_LEFT_TOL_PT
            if not (
                overlap / narrower >= BLOCK_OVERLAP_RATIO
                or aligned
                or centred
                or right_aligned
            ):
                continue
            if gap < best_gap:
                best = block
                best_gap = gap
        if best is None:
            blocks.append(
                _OpenBlock(segments=[seg], x0=seg.x0, x1=seg.x1, last_y=seg.y, pitch=None)
            )
        else:
            best.segments.append(seg)
            best.x0 = min(best.x0, seg.x0)
            best.x1 = max(best.x1, seg.x1)
            best.pitch = best.last_y - seg.y
            best.last_y = seg.y
    return [[seg.chars for seg in block.segments] for block in blocks]


def build_text_blocks(
    chars: Sequence[CharRecord], column_boundaries: Sequence[float] = ()
) -> List[TextBlock]:
    """Reconstruct text boxes from a page's characters.

    ``column_boundaries`` are x positions of vertical rules the page draws;
    upright text never spans one.
    """
    by_angle: Dict[int, List[CharRecord]] = {}
    for c in chars:
        if not c.text or c.text.isspace() and c.text != " ":
            continue
        by_angle.setdefault(_quantize_angle(_angle_of(c)), []).append(c)

    out: List[TextBlock] = []
    for angle, group in by_angle.items():
        placed: List[_PlacedChar] = []
        for c in group:
            corners = [
                (c.bbox.x0, c.bbox.y0),
                (c.bbox.x1, c.bbox.y0),
                (c.bbox.x1, c.bbox.y1),
                (c.bbox.x0, c.bbox.y1),
            ]
            rc = [_rotate(p, -angle) for p in corners]
            box = rect_from_points(rc)
            assert box is not None
            origin = _rotate(c.origin, -angle)
            placed.append(
                _PlacedChar(
                    src=c,
                    x0=box.x0,
                    x1=box.x1,
                    y=origin[1],
                    top=box.y1,
                    bottom=box.y0,
                    pen_x=origin[0],
                    pen_next=origin[0] + c.advance,
                )
            )
        bounds = column_boundaries if abs(angle) < 0.5 else ()
        for block_lines in _group_blocks(_build_lines(placed), bounds):
            ir_lines: List[TextLine] = []
            spans: List[Tuple[float, float]] = []
            boxes: List[Rect] = []
            baselines: List[float] = []
            n_chars = 0
            for line in block_lines:
                runs = _runs_for_line(line)
                if not runs:
                    continue
                lb = rect_from_points(
                    [(p.x0, p.bottom) for p in line] + [(p.x1, p.top) for p in line]
                )
                assert lb is not None
                ir_lines.append(
                    TextLine(runs=runs, bbox=lb, baseline_y=line[0].y, rotation_deg=angle)
                )
                spans.append((lb.x0, lb.x1))
                boxes.append(lb)
                baselines.append(line[0].y)
                n_chars += sum(len(p.src.text) for p in line)
            if not ir_lines:
                continue
            rot_box = union_all(boxes)
            assert rot_box is not None
            spacing = None
            if len(baselines) > 1:
                deltas = sorted(baselines[i] - baselines[i + 1] for i in range(len(baselines) - 1))
                spacing = deltas[len(deltas) // 2]
            content = TextContent(
                lines=ir_lines,
                align=_infer_align(spans, rot_box),
                line_spacing_pt=spacing,
                rotation_deg=float(angle),
            )
            # Place the box so the FIRST BASELINE lands where the PDF put it.
            # Sizing the box to the glyph bboxes instead drifts by the
            # difference between the source font's ascent and the substitute's.
            frame = _frame_box(rot_box, ir_lines, spacing)
            first = block_lines[0][0].src
            paint_bbox = rect_from_points(
                [
                    _rotate((frame.x0, frame.y0), angle),
                    _rotate((frame.x1, frame.y0), angle),
                    _rotate((frame.x1, frame.y1), angle),
                    _rotate((frame.x0, frame.y1), angle),
                ]
            )
            assert paint_bbox is not None
            out.append(
                TextBlock(
                    content=content,
                    bbox=frame if abs(angle) < 0.5 else _upright_box(frame, angle),
                    paint_bbox=paint_bbox,
                    rotation_deg=float(angle),
                    char_count=n_chars,
                    paint_order=min(p.src.paint_order for line in block_lines for p in line),
                    clip=first.clip,
                    alpha=first.alpha,
                )
            )
    out.sort(key=lambda b: b.paint_order)
    return out


def _line_size(line: TextLine) -> float:
    return max((r.size_pt for r in line.runs), default=10.0)


def _frame_box(ink: Rect, lines: List[TextLine], spacing: Optional[float]) -> Rect:
    """The text-box rectangle that puts the first baseline on the source's.

    DrawingML has no baseline control: a top-anchored box places its first
    baseline one line height below the top, less the font's descent.  Solving
    that for the box top is exact for the first line and keeps every following
    line right too, because the line pitch is written as an explicit lnSpc.
    """
    first = lines[0]
    size0 = _line_size(first)
    pitch = spacing if spacing and spacing > 0 else AUTO_LINE_RATIO * size0
    top = first.baseline_y + (pitch - DESCENT_EM * size0)
    height = max(pitch * len(lines), ink.height + 0.35 * size0)
    return Rect(ink.x0, top - height, ink.x1, top)


def _upright_box(rot_box: Rect, angle: float) -> Rect:
    """The un-rotated box, centred where the rotated box centre lands.

    DrawingML rotates a shape about the centre of its ``a:ext``, so a rotated
    text box is stored with its *unrotated* extent placed at the rotated
    centre.
    """
    cx, cy = _rotate((rot_box.cx, rot_box.cy), angle)
    w, h = rot_box.width, rot_box.height
    return Rect(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def visible_text(chars: Iterable[CharRecord]) -> str:
    """All source characters in paint order, for loss checks."""
    return "".join(c.text for c in sorted(chars, key=lambda c: c.paint_order))


def normalize_for_compare(text: str) -> str:
    """Whitespace-insensitive, NFC-normalised form used by the loss checks."""
    return "".join(unicodedata.normalize("NFC", text).split())
