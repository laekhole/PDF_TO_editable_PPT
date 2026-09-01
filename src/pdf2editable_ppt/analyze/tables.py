"""Lattice table recovery.

We only claim a table where the PDF actually *drew* one: a connected lattice
of horizontal and vertical rules.  The rules themselves (their colour, weight
and dash), the filled rectangles behind them and the text blocks inside them
are re-combined into a native PowerPoint table with per-edge borders, so the
result is editable cell by cell rather than a picture of a table.

Tables without rulings are left alone.  Inferring their structure from
whitespace is guesswork, and a wrong grid is a damaged slide -- the text
blocks already reproduce them exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..ir import (
    CellBorder,
    Element,
    ElementType,
    TableCell,
    TableContent,
    TextContent,
    TextLine,
)
from ..units import Rect, union_all

# Two rule coordinates within this many points are the same grid line.
SNAP_TOL_PT = 2.0
# A filled rectangle thinner than this is a ruling drawn as a rectangle.
RULE_MAX_THICKNESS_PT = 3.0
# A rule must cover at least this share of a cell edge to count as that border.
EDGE_COVER_RATIO = 0.7
# A candidate grid needs this share of its interior edges backed by real rules.
MIN_EDGE_COVERAGE = 0.5
# Grids smaller than this are not tables.
MIN_CELLS = 4
# A fill must cover this share of a cell to be read as the cell's shading...
FILL_COVER_RATIO = 0.75
# ...and be at most this many times the cell's own area, so a page-wide
# background is never mistaken for one cell's shading.
FILL_MAX_AREA_RATIO = 2.2
# Width a cell must leave for its text, as a multiple of the measured ink.
TEXT_WIDTH_HEADROOM = 1.08


@dataclass
class Rule:
    """One horizontal or vertical ruling in visual page space."""

    horizontal: bool
    pos: float          # y for horizontal, x for vertical
    start: float        # x0 / y0
    end: float          # x1 / y1
    color: str
    width_pt: float
    dash: Optional[str]
    element: Element

    def covers(self, a: float, b: float) -> float:
        lo, hi = min(a, b), max(a, b)
        span = hi - lo
        if span <= 0:
            return 1.0
        overlap = min(hi, self.end) - max(lo, self.start)
        return max(0.0, overlap) / span


def _dash_name(element: Element) -> Optional[str]:
    from ..build.drawingml import dash_preset

    if element.style.dash:
        return dash_preset(element.style.dash[0], element.style.stroke_width_pt)
    return None


def _rules_from_element(el: Element) -> List[Rule]:
    """Every ruling an element contributes: lines, thin fills, rect borders."""
    out: List[Rule] = []
    box = el.bbox
    if el.type is ElementType.LINE:
        p0, p1 = el.content
        color = el.style.stroke_color or "000000"
        w = max(0.25, el.style.stroke_width_pt)
        if abs(p0[1] - p1[1]) <= SNAP_TOL_PT and abs(p0[0] - p1[0]) > SNAP_TOL_PT:
            out.append(
                Rule(True, (p0[1] + p1[1]) / 2, min(p0[0], p1[0]), max(p0[0], p1[0]),
                     color, w, _dash_name(el), el)
            )
        elif abs(p0[0] - p1[0]) <= SNAP_TOL_PT and abs(p0[1] - p1[1]) > SNAP_TOL_PT:
            out.append(
                Rule(False, (p0[0] + p1[0]) / 2, min(p0[1], p1[1]), max(p0[1], p1[1]),
                     color, w, _dash_name(el), el)
            )
        return out

    if el.type is not ElementType.RECT:
        return out
    spec = el.content if isinstance(el.content, dict) else {}
    if spec.get("prst") != "rect" or abs(el.rotation_deg) > 0.5:
        return out

    # a thin filled bar is a ruling drawn as a rectangle
    if el.style.has_fill and not el.style.has_stroke:
        if box.height <= RULE_MAX_THICKNESS_PT < box.width:
            out.append(
                Rule(True, box.cy, box.x0, box.x1, el.style.fill_color or "000000",
                     max(0.25, box.height), None, el)
            )
        elif box.width <= RULE_MAX_THICKNESS_PT < box.height:
            out.append(
                Rule(False, box.cx, box.y0, box.y1, el.style.fill_color or "000000",
                     max(0.25, box.width), None, el)
            )
        return out

    # a stroked rectangle contributes its four edges
    if el.style.has_stroke and box.width > SNAP_TOL_PT and box.height > SNAP_TOL_PT:
        color = el.style.stroke_color or "000000"
        w = max(0.25, el.style.stroke_width_pt)
        dash = _dash_name(el)
        out.append(Rule(True, box.y0, box.x0, box.x1, color, w, dash, el))
        out.append(Rule(True, box.y1, box.x0, box.x1, color, w, dash, el))
        out.append(Rule(False, box.x0, box.y0, box.y1, color, w, dash, el))
        out.append(Rule(False, box.x1, box.y0, box.y1, color, w, dash, el))
    return out


def _snap(values: Sequence[float], tol: float = SNAP_TOL_PT) -> List[float]:
    """Collapse near-equal coordinates to their cluster mean."""
    if not values:
        return []
    ordered = sorted(values)
    clusters: List[List[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _cluster_rules(rules: List[Rule]) -> List[List[Rule]]:
    """Group rules into connected lattices by bbox adjacency."""
    boxes: List[Rect] = []
    for r in rules:
        if r.horizontal:
            boxes.append(Rect(r.start, r.pos - 1, r.end, r.pos + 1))
        else:
            boxes.append(Rect(r.pos - 1, r.start, r.pos + 1, r.end))
    n = len(rules)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    pad = SNAP_TOL_PT * 1.5
    for i in range(n):
        bi = boxes[i].expanded(pad)
        for j in range(i + 1, n):
            if bi.intersection(boxes[j]) is not None:
                union(i, j)
    groups: Dict[int, List[Rule]] = {}
    for i, r in enumerate(rules):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


@dataclass
class _Grid:
    xs: List[float]
    ys: List[float]  # descending (top first)
    h_rules: List[Rule]
    v_rules: List[Rule]


def _build_grid(cluster: List[Rule]) -> Optional[_Grid]:
    h = [r for r in cluster if r.horizontal]
    v = [r for r in cluster if not r.horizontal]
    if len(h) < 2 or len(v) < 2:
        return None
    xs = _snap([r.pos for r in v])
    ys = sorted(_snap([r.pos for r in h]), reverse=True)
    if len(xs) < 2 or len(ys) < 2:
        return None
    if (len(xs) - 1) * (len(ys) - 1) < MIN_CELLS and not (
        len(xs) >= 3 or len(ys) >= 3
    ):
        return None
    return _Grid(xs, ys, h, v)


def _edge_rule(
    rules: List[Rule], pos: float, a: float, b: float
) -> Optional[Rule]:
    """The rule sitting at ``pos`` and covering the span a..b, if any."""
    best: Optional[Rule] = None
    best_cover = 0.0
    for r in rules:
        if abs(r.pos - pos) > SNAP_TOL_PT:
            continue
        cover = r.covers(a, b)
        if cover >= EDGE_COVER_RATIO and cover > best_cover:
            best = r
            best_cover = cover
    return best


def _text_content_for(elements: Sequence[Element], cell: Rect) -> Tuple[Optional[TextContent], List[Element]]:
    picked: List[Element] = []
    lines: List[TextLine] = []
    for el in elements:
        if el.type is not ElementType.TEXT or el.consumed:
            continue
        b = el.bbox
        if not (cell.x0 - 1 <= b.cx <= cell.x1 + 1 and cell.y0 - 1 <= b.cy <= cell.y1 + 1):
            continue
        picked.append(el)
        lines.extend(el.content.lines)
    if not lines:
        return None, picked
    lines.sort(key=lambda ln: -(ln.bbox.cy if ln.bbox else 0.0))
    align = picked[0].content.align if picked else "l"
    return TextContent(lines=lines, align=align), picked


def _fill_for(
    elements: Sequence[Element], cell: Rect, table: Rect
) -> Tuple[Optional[Element], float]:
    """The filled rectangle that shades this cell, if there is one.

    A fill only counts as *this cell's* shading when it covers the cell and is
    not much bigger than it.  Without that second condition a page-wide white
    background covers every cell perfectly, gets adopted as nine cell fills,
    and disappears from the slide it was actually painting.
    """
    best: Optional[Element] = None
    best_cover = 0.0
    limit = cell.area * FILL_MAX_AREA_RATIO
    for el in elements:
        if el.type is not ElementType.RECT or el.consumed:
            continue
        if not el.style.has_fill:
            continue
        if el.bbox.area > limit:
            continue
        if not table.expanded(SNAP_TOL_PT).contains(el.bbox):
            continue
        inter = el.bbox.intersection(cell)
        if inter is None:
            continue
        cover = inter.area / max(1e-6, cell.area)
        if cover >= FILL_COVER_RATIO and cover > best_cover:
            best = el
            best_cover = cover
    return best, best_cover


def detect_tables(page_elements: List[Element], next_id) -> List[Element]:
    """Find lattice tables and return the table elements to add.

    Consumed source elements are flagged in place; the caller keeps them in
    the IR (they stay in the report) but the builder skips them.
    """
    rules: List[Rule] = []
    for el in page_elements:
        if el.consumed:
            continue
        rules.extend(_rules_from_element(el))
    if len(rules) < 4:
        return []

    tables: List[Element] = []
    for cluster in _cluster_rules(rules):
        grid = _build_grid(cluster)
        if grid is None:
            continue
        table = _grid_to_table(grid, page_elements, next_id)
        if table is not None:
            tables.append(table)
    return tables


def _grid_to_table(
    grid: _Grid, page_elements: List[Element], next_id
) -> Optional[Element]:
    xs, ys = grid.xs, grid.ys
    n_cols = len(xs) - 1
    n_rows = len(ys) - 1
    if n_rows < 1 or n_cols < 1 or n_rows * n_cols < MIN_CELLS:
        return None
    bbox = Rect(xs[0], ys[-1], xs[-1], ys[0])

    # Which interior/exterior edges are actually drawn?
    right_edge: Dict[Tuple[int, int], Optional[Rule]] = {}
    left_edge: Dict[Tuple[int, int], Optional[Rule]] = {}
    top_edge: Dict[Tuple[int, int], Optional[Rule]] = {}
    bottom_edge: Dict[Tuple[int, int], Optional[Rule]] = {}
    drawn = 0
    total = 0
    for r in range(n_rows):
        y_top, y_bot = ys[r], ys[r + 1]
        for c in range(n_cols):
            x_l, x_r = xs[c], xs[c + 1]
            left_edge[(r, c)] = _edge_rule(grid.v_rules, x_l, y_bot, y_top)
            right_edge[(r, c)] = _edge_rule(grid.v_rules, x_r, y_bot, y_top)
            top_edge[(r, c)] = _edge_rule(grid.h_rules, y_top, x_l, x_r)
            bottom_edge[(r, c)] = _edge_rule(grid.h_rules, y_bot, x_l, x_r)
            total += 4
            drawn += sum(
                1
                for e in (
                    left_edge[(r, c)],
                    right_edge[(r, c)],
                    top_edge[(r, c)],
                    bottom_edge[(r, c)],
                )
                if e is not None
            )
    coverage = drawn / total if total else 0.0
    if coverage < MIN_EDGE_COVERAGE:
        return None

    # ── merges: a missing interior edge means the cells are joined ──────────
    anchor_of: Dict[Tuple[int, int], Tuple[int, int]] = {}
    spans: Dict[Tuple[int, int], Tuple[int, int]] = {}
    visited: Set[Tuple[int, int]] = set()
    for r in range(n_rows):
        for c in range(n_cols):
            if (r, c) in visited:
                continue
            col_span = 1
            while (
                c + col_span < n_cols
                and right_edge[(r, c + col_span - 1)] is None
                and (r, c + col_span) not in visited
            ):
                col_span += 1
            row_span = 1
            while r + row_span < n_rows and all(
                bottom_edge[(r + row_span - 1, cc)] is None
                for cc in range(c, c + col_span)
            ):
                if any((r + row_span, cc) in visited for cc in range(c, c + col_span)):
                    break
                row_span += 1
            spans[(r, c)] = (row_span, col_span)
            for rr in range(r, r + row_span):
                for cc in range(c, c + col_span):
                    visited.add((rr, cc))
                    anchor_of[(rr, cc)] = (r, c)

    # ── build cells ─────────────────────────────────────────────────────────
    consumed: List[Element] = []
    cells: List[TableCell] = []
    for r in range(n_rows):
        for c in range(n_cols):
            anchor = anchor_of[(r, c)]
            cell_box = Rect(xs[c], ys[r + 1], xs[c + 1], ys[r])
            borders = {
                "l": _border(left_edge[(r, c)]),
                "r": _border(right_edge[(r, c)]),
                "t": _border(top_edge[(r, c)]),
                "b": _border(bottom_edge[(r, c)]),
            }
            fill_el, _cover = _fill_for(page_elements, cell_box, bbox)
            fill_color = fill_el.style.fill_color if fill_el else None
            fill_alpha = fill_el.style.fill_alpha if fill_el else 1.0
            if fill_el is not None:
                consumed.append(fill_el)
            if anchor != (r, c):
                cells.append(
                    TableCell(
                        row=r,
                        col=c,
                        merged_by=anchor,
                        borders=borders,
                        fill_color=fill_color,
                        fill_alpha=fill_alpha,
                        bbox=cell_box,
                    )
                )
                continue
            row_span, col_span = spans[(r, c)]
            span_box = Rect(
                xs[c], ys[min(n_rows, r + row_span)], xs[min(n_cols, c + col_span)], ys[r]
            )
            content, picked = _text_content_for(page_elements, span_box)
            consumed.extend(picked)
            v_align, margins = _placement(content, span_box)
            cells.append(
                TableCell(
                    row=r,
                    col=c,
                    row_span=row_span,
                    col_span=col_span,
                    text=content,
                    fill_color=fill_color,
                    fill_alpha=fill_alpha,
                    v_align=v_align,
                    margins_pt=margins,
                    borders=borders,
                    bbox=span_box,
                )
            )

    for rule in grid.h_rules + grid.v_rules:
        consumed.append(rule.element)
    for el in consumed:
        el.consumed = True

    content = TableContent(
        rows=n_rows,
        cols=n_cols,
        col_widths_pt=[xs[i + 1] - xs[i] for i in range(n_cols)],
        row_heights_pt=[ys[i] - ys[i + 1] for i in range(n_rows)],
        cells=cells,
    )
    z = min((r.element.source_paint_order for r in grid.h_rules + grid.v_rules), default=0)
    element = Element(
        id=next_id("tbl"),
        type=ElementType.TABLE,
        bbox=bbox,
        z_index=z,
        source_paint_order=z,
        content=content,
        confidence=coverage,
    )
    element.note(
        "lattice table: %d x %d, %.0f%% of cell edges backed by drawn rules"
        % (n_rows, n_cols, coverage * 100)
    )
    return element


def _border(rule: Optional[Rule]) -> CellBorder:
    if rule is None:
        return CellBorder(present=False)
    return CellBorder(
        color=rule.color, width_pt=rule.width_pt, dash=rule.dash, present=True
    )


def _placement(
    content: Optional[TextContent], cell: Rect
) -> Tuple[str, Tuple[float, float, float, float]]:
    """Vertical anchor and content insets measured from where the ink sits."""
    if content is None or not content.lines:
        return "t", (2.0, 1.0, 2.0, 1.0)
    boxes = [ln.bbox for ln in content.lines if ln.bbox]
    ink = union_all(boxes)
    if ink is None:
        return "t", (2.0, 1.0, 2.0, 1.0)
    top_gap = max(0.0, cell.y1 - ink.y1)
    bottom_gap = max(0.0, ink.y0 - cell.y0)
    left_gap = max(0.0, ink.x0 - cell.x0)
    right_gap = max(0.0, cell.x1 - ink.x1)
    # PowerPoint wraps cell text, and a substituted font sets a hair wider than
    # the source did.  Give the measured ink some headroom by shrinking the
    # insets rather than letting a one-word heading break onto two lines.
    budget = max(0.0, cell.width - ink.width * TEXT_WIDTH_HEADROOM)
    total = left_gap + right_gap
    if total > budget and total > 0:
        scale = budget / total
        left_gap *= scale
        right_gap *= scale
    height = max(1e-6, cell.height)
    if abs(top_gap - bottom_gap) <= 0.12 * height:
        return "ctr", (left_gap, 0.0, right_gap, 0.0)
    if bottom_gap < top_gap:
        return "b", (left_gap, 0.0, right_gap, bottom_gap)
    return "t", (left_gap, top_gap, right_gap, 0.0)
