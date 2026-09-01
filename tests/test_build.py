"""DrawingML generation and the structure of the produced package."""

import zipfile

import pytest
from lxml import etree

from conftest import ARTIFACT_DIR, fixture_path
from pdf2editable_ppt.build import drawingml as dml
from pdf2editable_ppt.build.pptx_writer import DeckWriter
from pdf2editable_ppt.ir import (
    CellBorder,
    Path,
    Segment,
    SegmentOp,
    Style,
    TextContent,
    TextLine,
    TextRun,
)
from pdf2editable_ppt.units import PageGeometry, Rect, pt_to_emu

A = dml.A_NS
P = dml.P_NS
NS = {"a": A, "p": P, "r": dml.R_NS}


def parse(xml: str):
    return etree.fromstring(xml.encode("utf-8"))


# ── colours, dashes, rotation ───────────────────────────────────────────────


def test_solid_fill_carries_alpha_only_when_translucent():
    assert "alpha" not in dml.solid_fill("FF0000", 1.0)
    assert '<a:alpha val="40000"/>' in dml.solid_fill("FF0000", 0.4)


@pytest.mark.parametrize(
    "pattern,width,expected",
    [
        ([4, 3], 1.0, "dash"),
        ([1, 3], 1.0, "dot"),
        ([8, 3], 1.0, "lgDash"),
        ([3, 1], 1.0, "sysDash"),
        ([1, 1], 1.0, "sysDot"),
        ([4, 3, 1, 3], 1.0, "dashDot"),
    ],
)
def test_preset_dashes_round_trip_to_themselves(pattern, width, expected):
    assert dml.dash_preset(pattern, width) == expected


def test_no_dash_array_means_no_preset():
    assert dml.dash_preset(None, 1.0) is None
    assert dml.dash_preset([], 1.0) is None
    assert dml.dash_preset([0], 1.0) is None


def test_rotation_is_converted_to_clockwise_sixtieths():
    assert dml.rot_attr(0) == ""
    assert dml.rot_attr(-90) == ' rot="5400000"'   # clockwise quarter turn
    assert dml.rot_attr(90) == ' rot="16200000"'   # counter-clockwise quarter turn


def test_control_characters_are_stripped_from_text():
    assert dml.esc("a\x00b\x08c") == "abc"
    assert dml.esc("<&>") == "&lt;&amp;&gt;"


# ── custom geometry ─────────────────────────────────────────────────────────


def test_custom_geometry_keeps_every_segment_type():
    path = Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, ((0.0, 100.0),)),
            Segment(SegmentOp.LINE_TO, ((50.0, 100.0),)),
            Segment(SegmentOp.CUBIC_TO, ((60.0, 90.0), (70.0, 60.0), (80.0, 50.0))),
            Segment(SegmentOp.CLOSE),
        ]
    )
    geom = PageGeometry(0, 0, 200, 200, 0)
    xml = dml.custom_geometry(path, origin=(0, 0), size=(100000, 100000), to_emu_xy=geom.point_to_emu)
    root = parse('<w xmlns:a="%s">%s</w>' % (A, xml))
    assert root.findall(".//a:moveTo", NS)
    assert root.findall(".//a:lnTo", NS)
    cubic = root.findall(".//a:cubicBezTo", NS)
    assert len(cubic) == 1
    assert len(cubic[0].findall("a:pt", NS)) == 3, "both control points survive"
    assert root.findall(".//a:close", NS)


def test_custom_geometry_coordinates_are_local_to_the_shape():
    path = Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, ((100.0, 200.0),)),
            Segment(SegmentOp.LINE_TO, ((150.0, 200.0),)),
        ]
    )
    geom = PageGeometry(0, 0, 400, 400, 0)
    x, y, cx, cy = geom.rect_to_emu(Rect(100, 190, 150, 200))
    xml = dml.custom_geometry(path, origin=(x, y), size=(cx, cy), to_emu_xy=geom.point_to_emu)
    pts = parse('<w xmlns:a="%s">%s</w>' % (A, xml)).findall(".//a:pt", NS)
    assert pts[0].get("x") == "0" and pts[0].get("y") == "0"
    assert int(pts[1].get("x")) == pt_to_emu(50)


def test_multi_subpath_geometry_emits_one_path_element_each():
    path = Path(
        segments=[
            Segment(SegmentOp.MOVE_TO, ((0.0, 0.0),)),
            Segment(SegmentOp.LINE_TO, ((10.0, 0.0),)),
            Segment(SegmentOp.CLOSE),
            Segment(SegmentOp.MOVE_TO, ((20.0, 0.0),)),
            Segment(SegmentOp.LINE_TO, ((30.0, 0.0),)),
            Segment(SegmentOp.CLOSE),
        ]
    )
    geom = PageGeometry(0, 0, 100, 100, 0)
    xml = dml.custom_geometry(path, (0, 0), (1000, 1000), geom.point_to_emu)
    assert len(parse('<w xmlns:a="%s">%s</w>' % (A, xml)).findall(".//a:path", NS)) == 2


# ── table cells ─────────────────────────────────────────────────────────────


def test_each_cell_edge_gets_its_own_line_element():
    borders = {
        "l": CellBorder(color="FF0000", width_pt=2.0, present=True),
        "r": CellBorder(present=False),
        "t": CellBorder(color="00FF00", width_pt=0.5, dash="dash", present=True),
        "b": CellBorder(present=False),
    }
    xml = dml.table_cell_xml(
        dml.cell_text_body(None), borders, "EEEEEE", 1.0, "ctr", (2, 1, 2, 1)
    )
    root = parse('<w xmlns:a="%s">%s</w>' % (A, xml))
    left = root.find(".//a:lnL", NS)
    assert left.get("w") == str(pt_to_emu(2.0))
    assert left.find(".//a:srgbClr", NS).get("val") == "FF0000"
    top = root.find(".//a:lnT", NS)
    assert top.find("a:prstDash", NS).get("val") == "dash"
    assert root.find(".//a:lnR", NS).find("a:noFill", NS) is not None
    assert root.find(".//a:tcPr", NS).get("anchor") == "ctr"


def test_merge_flags_are_written_on_covered_cells():
    xml = dml.table_cell_xml(
        dml.cell_text_body(None), {}, None, 1.0, "t", (0, 0, 0, 0), h_merge=True, v_merge=True
    )
    tc = parse('<w xmlns:a="%s">%s</w>' % (A, xml)).find("a:tc", NS)
    assert tc.get("hMerge") == "1" and tc.get("vMerge") == "1"


def test_table_uses_the_no_grid_style_so_our_borders_are_the_only_ones():
    xml = dml.table_xml(5, "T", 0, 0, 100, 100, [50, 50], [50], ["<a:tc/><a:tc/>"])
    root = parse(xml)
    assert root.find(".//a:tableStyleId", NS).text == dml.TABLE_STYLE_NO_GRID
    pr = root.find(".//a:tblPr", NS)
    assert pr.get("firstRow") == "0" and pr.get("bandRow") == "0"


# ── whole-package structure ─────────────────────────────────────────────────


def test_written_deck_has_the_expected_package_shape(conversions):
    result = conversions.get("shapes")
    with zipfile.ZipFile(result.output_path) as z:
        names = set(z.namelist())
        assert "[Content_Types].xml" in names
        assert "ppt/presentation.xml" in names
        assert "ppt/slides/slide1.xml" in names
        assert z.testzip() is None
        slide = etree.fromstring(z.read("ppt/slides/slide1.xml"))
    tree = slide.find(".//p:cSld/p:spTree", NS)
    assert tree is not None
    assert len(tree.findall("p:sp", NS)) >= 15


def test_slide_size_matches_the_page(conversions):
    result = conversions.get("shapes")
    with zipfile.ZipFile(result.output_path) as z:
        pres = etree.fromstring(z.read("ppt/presentation.xml"))
    size = pres.find("p:sldSz", NS)
    assert int(size.get("cx")) == pt_to_emu(612)
    assert int(size.get("cy")) == pt_to_emu(792)


def test_mixed_page_sizes_letterbox_into_one_slide_size(conversions):
    result = conversions.get("mixed_sizes")
    with zipfile.ZipFile(result.output_path) as z:
        pres = etree.fromstring(z.read("ppt/presentation.xml"))
    size = pres.find("p:sldSz", NS)
    widest = max(p.width_pt for p in result.document.pages)
    tallest = max(p.height_pt for p in result.document.pages)
    assert int(size.get("cx")) == pt_to_emu(widest)
    assert int(size.get("cy")) == pt_to_emu(tallest)


def test_shape_order_on_the_slide_is_the_source_paint_order(conversions):
    result = conversions.get("shapes")
    page = result.document.pages[0]
    live = [e for e in page.elements if not e.consumed]
    orders = [e.source_paint_order for e in live]
    assert orders == sorted(orders), "z-order is spTree order"


def test_jpeg_reaches_the_package_as_a_jpeg(conversions):
    result = conversions.get("images")
    with zipfile.ZipFile(result.output_path) as z:
        media = [n for n in z.namelist() if n.startswith("ppt/media/")]
        assert any(n.endswith(".jpg") or n.endswith(".jpeg") for n in media)
        blob = z.read(next(n for n in media if n.endswith((".jpg", ".jpeg"))))
    assert blob[:2] == b"\xff\xd8", "still a JPEG, not a re-encode"


def test_no_placeholders_are_inherited_onto_the_slides(conversions):
    result = conversions.get("text_mixed")
    with zipfile.ZipFile(result.output_path) as z:
        slide = etree.fromstring(z.read("ppt/slides/slide1.xml"))
    assert slide.findall(".//p:ph", NS) == []


def test_blank_deck_writes_without_error(tmp_path):
    writer = DeckWriter(400, 300)
    slide = writer.add_slide()
    content = TextContent(
        lines=[TextLine(runs=[TextRun(text="hi", font_family="Arial", size_pt=12)])]
    )
    writer.append(
        slide,
        dml.textbox_xml(writer.alloc_id(), "t", dml.xfrm(0, 0, 100, 100), content),
    )
    out = tmp_path / "deck.pptx"
    writer.save(str(out))
    assert out.stat().st_size > 0
