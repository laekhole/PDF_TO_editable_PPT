"""Coordinate arithmetic: the layer everything else trusts."""

import math

import pytest

from pdf2editable_ppt.units import (
    EMU_PER_PT,
    PageGeometry,
    Rect,
    apply_matrix,
    emu_to_pt,
    is_axis_aligned,
    mat_multiply,
    matrix_rotation_deg,
    matrix_scale,
    pt_to_emu,
    rect_from_points,
    union_all,
)


def test_point_to_emu_roundtrip():
    assert pt_to_emu(72) == 914400
    assert pt_to_emu(1) == EMU_PER_PT
    assert emu_to_pt(914400) == 72
    for pt in (0.0, 0.5, 11.75, 612.0, 841.89):
        assert abs(emu_to_pt(pt_to_emu(pt)) - pt) < 1e-4


def test_pt_to_emu_rounds_to_nearest():
    assert pt_to_emu(1.000001) == EMU_PER_PT  # 12700.01 EMU
    assert pt_to_emu(1.00004) == EMU_PER_PT + 1  # 12700.5 EMU rounds up
    assert pt_to_emu(0.5) == 6350


def test_apply_matrix_translation_and_scale():
    assert apply_matrix((2, 0, 0, 3, 10, 20), (1, 1)) == (12, 23)


def test_apply_matrix_rotation():
    m = (0, 1, -1, 0, 0, 0)  # 90 degrees counter-clockwise
    x, y = apply_matrix(m, (1, 0))
    assert abs(x - 0) < 1e-9 and abs(y - 1) < 1e-9


def test_matrix_multiply_order():
    scale = (2, 0, 0, 2, 0, 0)
    translate = (1, 0, 0, 1, 5, 7)
    combined = mat_multiply(scale, translate)
    assert apply_matrix(combined, (1, 1)) == apply_matrix(translate, apply_matrix(scale, (1, 1)))


def test_matrix_scale_and_rotation():
    assert abs(matrix_scale((3, 0, 0, 3, 0, 0)) - 3) < 1e-9
    assert abs(matrix_rotation_deg((0, 1, -1, 0, 0, 0)) - 90) < 1e-9
    assert is_axis_aligned((2, 0, 0, 4, 1, 1))
    assert not is_axis_aligned((0, 1, -1, 0, 0, 0))


def test_rect_helpers():
    r = Rect(10, 20, 30, 50)
    assert (r.width, r.height, r.area) == (20, 30, 600)
    assert (r.cx, r.cy) == (20, 35)
    assert r.union(Rect(0, 0, 5, 5)) == Rect(0, 0, 30, 50)
    assert r.intersection(Rect(25, 25, 100, 100)) == Rect(25, 25, 30, 50)
    assert r.intersection(Rect(100, 100, 200, 200)) is None
    assert r.contains(Rect(11, 21, 29, 49))
    assert not r.contains(Rect(9, 21, 29, 49))
    assert Rect(30, 50, 10, 20).normalized() == r
    assert rect_from_points([(1, 2), (5, 0), (3, 9)]) == Rect(1, 0, 5, 9)
    assert union_all([Rect(0, 0, 1, 1), Rect(4, 4, 5, 5)]) == Rect(0, 0, 5, 5)
    assert union_all([]) is None


@pytest.mark.parametrize(
    "rotation,expected_size",
    [(0, (612, 792)), (90, (792, 612)), (180, (612, 792)), (270, (792, 612))],
)
def test_visual_page_size_follows_rotation(rotation, expected_size):
    geom = PageGeometry(0, 0, 612, 792, rotation)
    assert (geom.visual_width, geom.visual_height) == expected_size


def test_page_rotation_is_clockwise():
    """/Rotate turns the page clockwise, so the bottom-left goes to the top-left."""
    geom = PageGeometry(0, 0, 612, 792, 90)
    # bottom-left of the unrotated page
    assert geom.to_visual(0, 0) == (0, 612)
    # bottom-right
    assert geom.to_visual(612, 0) == (0, 0)
    # top-left
    assert geom.to_visual(0, 792) == (792, 612)


def test_rotation_180_is_a_point_reflection():
    geom = PageGeometry(0, 0, 612, 792, 180)
    assert geom.to_visual(0, 0) == (612, 792)
    assert geom.to_visual(612, 792) == (0, 0)


def test_rect_to_emu_flips_the_y_axis():
    geom = PageGeometry(0, 0, 612, 792, 0)
    x, y, cx, cy = geom.rect_to_emu(Rect(72, 692, 172, 792))
    assert x == pt_to_emu(72)
    assert y == 0  # the rect touches the top of the page
    assert cx == pt_to_emu(100)
    assert cy == pt_to_emu(100)


def test_rect_to_emu_honours_the_letterbox_offset():
    geom = PageGeometry(0, 0, 300, 400, 0, offset_x_pt=50, offset_y_pt=25)
    x, y, _cx, _cy = geom.rect_to_emu(Rect(0, 390, 10, 400))
    assert x == pt_to_emu(50)
    assert y == pt_to_emu(25)


def test_crop_box_offset_moves_the_origin():
    geom = PageGeometry(20, 30, 100, 200, 0)
    assert geom.to_visual(20, 30) == (0, 0)
    assert geom.to_visual(120, 230) == (100, 200)
