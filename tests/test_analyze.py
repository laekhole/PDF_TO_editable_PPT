"""Shape classification, text reconstruction and table recovery."""

import math

import pytest

from conftest import fixture_path
from pdf2editable_ppt.analyze.shapes import KAPPA, classify, split_subpaths
from pdf2editable_ppt.analyze.text import (
    DESCENT_EM,
    build_text_blocks,
    normalize_for_compare,
)
from pdf2editable_ppt.extract import extract_document
from pdf2editable_ppt.ir import ElementType, Path, Segment, SegmentOp
from pdf2editable_ppt.pipeline import ConvertOptions, build_page
from pdf2editable_ppt.units import Rect


def _p(*pts):
    return tuple(pts)


def line_path(x0, y0, x1, y1) -> Path:
    return Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, _p((x0, y0))),
            Segment(SegmentOp.LINE_TO, _p((x1, y1))),
        ]
    )


def rect_path(x0, y0, x1, y1) -> Path:
    return Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, _p((x0, y0))),
            Segment(SegmentOp.LINE_TO, _p((x1, y0))),
            Segment(SegmentOp.LINE_TO, _p((x1, y1))),
            Segment(SegmentOp.LINE_TO, _p((x0, y1))),
            Segment(SegmentOp.CLOSE),
        ]
    )


def ellipse_path(cx, cy, rx, ry) -> Path:
    kx, ky = KAPPA * rx, KAPPA * ry
    return Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, _p((cx + rx, cy))),
            Segment(SegmentOp.CUBIC_TO, _p((cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry))),
            Segment(SegmentOp.CUBIC_TO, _p((cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy))),
            Segment(SegmentOp.CUBIC_TO, _p((cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry))),
            Segment(SegmentOp.CUBIC_TO, _p((cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy))),
            Segment(SegmentOp.CLOSE),
        ]
    )


# ── shape classification ────────────────────────────────────────────────────


def test_two_point_stroke_is_a_line():
    assert classify(line_path(0, 0, 100, 40), stroke_only=True).kind == "line"


def test_axis_aligned_quad_is_a_rect():
    m = classify(rect_path(10, 10, 110, 60), stroke_only=False)
    assert m.kind == "rect"
    assert m.rotation_deg == pytest.approx(0.0)
    assert m.bbox == Rect(10, 10, 110, 60)


def test_rotated_quad_keeps_its_angle_and_upright_size():
    angle = math.radians(30)
    c, s = math.cos(angle), math.sin(angle)
    w, h = 100.0, 40.0
    cx, cy = 200.0, 300.0
    corners = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        corners.append((cx + dx * c - dy * s, cy + dx * s + dy * c))
    path = Path(
        segments=[Segment(SegmentOp.MOVE_TO, _p(corners[0]))]
        + [Segment(SegmentOp.LINE_TO, _p(p)) for p in corners[1:]]
        + [Segment(SegmentOp.CLOSE)]
    )
    m = classify(path, stroke_only=False)
    assert m.kind == "rect"
    assert abs(m.rotation_deg - 30.0) < 0.5
    assert m.bbox.width == pytest.approx(w, abs=0.5)
    assert m.bbox.height == pytest.approx(h, abs=0.5)


def test_kappa_fitted_curves_are_an_ellipse():
    m = classify(ellipse_path(100, 100, 60, 40), stroke_only=False)
    assert m.kind == "ellipse"
    assert m.bbox == Rect(40, 60, 160, 140)


def test_a_curve_that_is_not_an_ellipse_stays_freeform():
    """A wrong preset damages the drawing; a freeform never does."""
    path = ellipse_path(100, 100, 60, 40)
    # nudge one control point well outside the kappa fit
    seg = path.segments[1]
    path.segments[1] = Segment(
        SegmentOp.CUBIC_TO, ((seg.points[0][0] + 40, seg.points[0][1]),) + seg.points[1:]
    )
    assert classify(path, stroke_only=False).kind == "freeform"


def test_open_polygon_is_freeform_not_rect():
    path = Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, _p((0, 0))),
            Segment(SegmentOp.LINE_TO, _p((50, 80))),
            Segment(SegmentOp.LINE_TO, _p((100, 0))),
            Segment(SegmentOp.CLOSE),
        ]
    )
    assert classify(path, stroke_only=False).kind == "freeform"


def test_multiple_subpaths_stay_one_freeform():
    path = Path(segments=rect_path(0, 0, 100, 100).segments + rect_path(20, 20, 80, 80).segments)
    m = classify(path, stroke_only=False)
    assert m.kind == "freeform"
    assert "subpath" in m.reason
    assert len(split_subpaths(path)) == 2


def test_rounded_rect_is_recognised_with_its_radius():
    r = 15.0
    k = KAPPA * r
    x0, y0, x1, y1 = 0.0, 0.0, 200.0, 100.0
    segs = [
        Segment(SegmentOp.MOVE_TO, _p((x0 + r, y0))),
        Segment(SegmentOp.LINE_TO, _p((x1 - r, y0))),
        Segment(SegmentOp.CUBIC_TO, _p((x1 - r + k, y0), (x1, y0 + r - k), (x1, y0 + r))),
        Segment(SegmentOp.LINE_TO, _p((x1, y1 - r))),
        Segment(SegmentOp.CUBIC_TO, _p((x1, y1 - r + k), (x1 - r + k, y1), (x1 - r, y1))),
        Segment(SegmentOp.LINE_TO, _p((x0 + r, y1))),
        Segment(SegmentOp.CUBIC_TO, _p((x0 + r - k, y1), (x0, y1 - r + k), (x0, y1 - r))),
        Segment(SegmentOp.LINE_TO, _p((x0, y0 + r))),
        Segment(SegmentOp.CUBIC_TO, _p((x0, y0 + r - k), (x0 + r - k, y0), (x0 + r, y0))),
        Segment(SegmentOp.CLOSE),
    ]
    m = classify(Path(segments=segs), stroke_only=False)
    assert m.kind == "roundRect"
    assert m.adjust == pytest.approx(r / 100.0, abs=0.02)


def test_every_shape_family_in_the_fixture_is_recognised():
    pages, _assets, _warnings = extract_document(fixture_path("shapes"))
    kinds = set()
    for rec in pages[0].paths:
        kinds.add(classify(rec.path, rec.stroke and not rec.fill).kind)
    assert {"line", "rect", "ellipse", "roundRect", "freeform"} <= kinds


# ── text reconstruction ─────────────────────────────────────────────────────


def _blocks(name):
    pages, _assets, _warnings = extract_document(fixture_path(name))
    return build_text_blocks(pages[0].chars)


def test_no_characters_are_lost_or_duplicated():
    pages, _assets, _warnings = extract_document(fixture_path("text_mixed"))
    source = normalize_for_compare("".join(c.text for c in pages[0].chars))
    rebuilt = normalize_for_compare("".join(b.content.text for b in _blocks("text_mixed")))
    assert rebuilt == source


def test_korean_and_latin_do_not_gain_phantom_spaces():
    texts = [b.content.text for b in _blocks("text_mixed")]
    assert "2026 회의 자료 Meeting Deck" in texts
    assert "프로젝트 Alpha 진행률 87%" in texts


def test_one_line_can_hold_several_style_runs():
    block = next(b for b in _blocks("text_mixed") if "87%" in b.content.text)
    runs = block.content.lines[0].runs
    assert len(runs) >= 3
    sizes = {round(r.size_pt) for r in runs}
    colours = {r.color for r in runs}
    assert len(sizes) > 1 and len(colours) > 1


def test_rotated_text_keeps_its_angle_and_point_size():
    blocks = {round(b.rotation_deg): b for b in _blocks("text_mixed")}
    assert 90 in blocks and 45 in blocks
    assert blocks[90].content.text == "세로 회전 텍스트 90"
    sizes = {round(r.size_pt) for r in blocks[90].content.lines[0].runs}
    assert sizes == {14}, "a rotated glyph box must not be read as its point size"


def test_alignment_is_inferred_from_the_line_geometry():
    aligns = {b.content.text.split("\n")[0]: b.content.align for b in _blocks("text_mixed")}
    assert aligns["이 문단은 여러 줄로 구성되어 있으며"] == "ctr"
    assert aligns["Prepared by: Planning"] == "r"


def test_columns_on_one_baseline_become_separate_boxes():
    texts = {b.content.text for b in _blocks("table_borderless")}
    assert {"Item", "Owner", "Status"} <= texts
    assert not any("Item Owner" in t for t in texts)


def test_text_box_is_placed_from_the_baseline():
    """The frame's top is one line height above the baseline, less the descent."""
    block = next(b for b in _blocks("text_mixed") if b.content.text.startswith("2026"))
    line = block.content.lines[0]
    size = max(r.size_pt for r in line.runs)
    expected_top = line.baseline_y + (1.2 * size - DESCENT_EM * size)
    assert block.bbox.y1 == pytest.approx(expected_top, abs=0.01)


def test_multi_line_block_records_its_line_pitch():
    block = next(b for b in _blocks("text_mixed") if b.content.text.startswith("이 문단"))
    assert len(block.content.lines) == 3
    assert block.content.line_spacing_pt == pytest.approx(16.0, abs=0.5)


# ── tables ──────────────────────────────────────────────────────────────────


def _table(name):
    pages, assets, _warnings = extract_document(fixture_path(name))
    page = build_page(pages[0], assets, ConvertOptions(), [])
    from pdf2editable_ppt.analyze import tables as tablemod

    counter = [0]

    def next_id(prefix):
        counter[0] += 1
        return "t%d" % counter[0]

    found = tablemod.detect_tables(page.elements, next_id)
    return page, found


def test_lattice_table_grid_matches_the_source():
    _page, tables = _table("table_lattice")
    assert len(tables) == 1
    grid = tables[0].content
    assert (grid.rows, grid.cols) == (5, 4)


def test_merged_cells_are_recovered():
    _page, tables = _table("table_lattice")
    grid = tables[0].content
    anchors = {(c.row, c.col): c for c in grid.cells if c.merged_by is None}
    assert anchors[(0, 1)].col_span == 2, "the Revenue header spans two columns"
    assert anchors[(2, 0)].row_span == 2, "the APAC cell spans two rows"
    covered = [c for c in grid.cells if c.merged_by is not None]
    assert (0, 2) in {(c.row, c.col) for c in covered}
    assert (3, 0) in {(c.row, c.col) for c in covered}


def test_cell_fill_and_per_edge_borders_are_recovered():
    _page, tables = _table("table_lattice")
    grid = tables[0].content
    header = next(c for c in grid.cells if (c.row, c.col) == (0, 0))
    assert header.fill_color is not None
    assert all(header.borders[side].present for side in "ltrb")
    assert header.borders["t"].width_pt > 0


def test_cell_text_lands_in_the_right_cell():
    _page, tables = _table("table_lattice")
    grid = tables[0].content
    by_pos = {(c.row, c.col): c for c in grid.cells}
    assert by_pos[(2, 1)].text.text == "1,240"
    assert by_pos[(2, 2)].text.text == "1,455"
    assert by_pos[(2, 3)].text.text == "H. Park"


def test_consumed_rules_and_text_are_flagged():
    page, tables = _table("table_lattice")
    assert tables, "a table was found"
    consumed = [e for e in page.elements if e.consumed]
    assert consumed, "the rules and cell text belong to the table now"
    assert all(e.type is not ElementType.TABLE for e in consumed)


def test_a_borderless_layout_is_not_forced_into_a_table():
    """Guessing a grid from whitespace would be a damaged slide, not a table."""
    _page, tables = _table("table_borderless")
    assert tables == []
