"""Extraction: what the PDF actually said, before anything interprets it."""

import hashlib
import os

import pytest

from conftest import FIXTURE_DIR, fixture_path
from pdf2editable_ppt.extract import (
    PasswordRequired,
    UnparseableDocument,
    extract_document,
)
from pdf2editable_ppt.extract.fonts import (
    load_substitutions,
    normalize,
    split_style,
    strip_subset_prefix,
)
from pdf2editable_ppt.ir import SegmentOp


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ABCDEF+NanumGothic", ("NanumGothic", False, False)),
        ("ABCDEF+NanumGothic-Bold", ("NanumGothic", True, False)),
        ("Helvetica-BoldOblique", ("Helvetica", True, True)),
        ("TimesNewRomanPS-ItalicMT", ("TimesNewRomanPS", False, True)),
        ("ArialMT", ("ArialMT", False, False)),
        ("Arial-BoldMT", ("Arial", True, False)),
        ("Arial", ("Arial", False, False)),
        ("MalgunGothicBold", ("MalgunGothic", True, False)),
        ("SomeFont-Regular", ("SomeFont", False, False)),
    ],
)
def test_font_name_normalisation(name, expected):
    assert split_style(name) == expected


def test_subset_prefix_needs_exactly_six_capitals():
    assert strip_subset_prefix("ABCDEF+Foo") == "Foo"
    assert strip_subset_prefix("ABCDE+Foo") == "ABCDE+Foo"
    assert strip_subset_prefix("abcdef+Foo") == "abcdef+Foo"


def test_bookman_keeps_its_book():
    assert split_style("Bookman")[0] == "Bookman"


def test_descriptor_flags_can_add_italic():
    family, bold, italic = normalize("PlainFont", flags=1 << 6)
    assert (family, bold, italic) == ("PlainFont", False, True)


def test_substitution_map_wins(tmp_path):
    mapping = tmp_path / "fonts.map"
    mapping.write_text("# comment\nnanumgothic = Malgun Gothic\n\n", encoding="utf-8")
    subs = load_substitutions(str(mapping))
    assert normalize("ABCDEF+NanumGothic-Bold", 0, subs) == ("Malgun Gothic", True, False)


def test_bezier_control_points_survive_extraction():
    pages, _assets, _warnings = extract_document(fixture_path("shapes"))
    cubics = [
        seg
        for rec in pages[0].paths
        for seg in rec.path.segments
        if seg.op is SegmentOp.CUBIC_TO
    ]
    assert cubics, "the shapes fixture draws curves"
    for seg in cubics:
        assert len(seg.points) == 3, "a cubic keeps both control points and the end point"


def test_dash_pattern_and_alpha_are_recovered():
    pages, _assets, _warnings = extract_document(fixture_path("shapes"))
    dashed = [r for r in pages[0].paths if r.dash]
    assert dashed, "the fixture draws a dashed line"
    assert dashed[0].dash[0] == [8.0, 4.0]

    translucent = [r for r in pages[0].paths if r.fill and r.fill_alpha < 0.99]
    assert translucent, "constant alpha from /ExtGState must reach the records"
    assert 0.4 < translucent[0].fill_alpha < 0.6


def test_jpeg_stream_is_copied_byte_for_byte():
    _pages, assets, _warnings = extract_document(fixture_path("images"))
    jpegs = [a for a in assets.values() if a.ext == "jpg"]
    assert jpegs, "the fixture embeds a JPEG"
    asset = jpegs[0]
    assert asset.passthrough
    source = os.path.join(FIXTURE_DIR, "_photo.jpg")
    expected = hashlib.sha256(open(source, "rb").read()).hexdigest()
    assert asset.output_sha256 == expected
    assert asset.source_sha256 == asset.output_sha256


def test_alpha_png_keeps_its_transparency():
    _pages, assets, _warnings = extract_document(fixture_path("images"))
    with_alpha = [a for a in assets.values() if a.has_alpha]
    assert with_alpha, "the /SMask must become a real alpha channel"
    assert with_alpha[0].ext == "png"


def test_repeated_bitmap_becomes_one_asset():
    pages, assets, _warnings = extract_document(fixture_path("images"))
    placements = [i for i in pages[0].images if i.asset_id]
    ids = {i.asset_id for i in placements}
    assert len(placements) > len(ids), "the fixture places one bitmap several times"


def test_paint_order_is_monotonic_across_object_kinds():
    pages, _assets, _warnings = extract_document(fixture_path("shapes"))
    page = pages[0]
    orders = [c.paint_order for c in page.chars]
    orders += [p.paint_order for p in page.paths]
    assert len(set(orders)) == len(orders), "every paint operation gets its own order"


def test_shading_is_recorded_rather_than_dropped():
    pages, _assets, _warnings = extract_document(fixture_path("clip_gradient"))
    assert pages[0].shadings, "an sh operator must surface, not vanish"


def test_clip_is_tracked():
    pages, _assets, _warnings = extract_document(fixture_path("clip_gradient"))
    clipped = [p for p in pages[0].paths if p.clip is not None]
    assert clipped, "paths drawn inside W n must carry the clip rectangle"


def test_wrong_password_is_reported_clearly():
    with pytest.raises(PasswordRequired):
        extract_document(fixture_path("encrypted"), password="not-the-password")


def test_correct_password_opens_the_document():
    pages, _assets, _warnings = extract_document(
        fixture_path("encrypted"), password="secret"
    )
    assert "".join(c.text for c in pages[0].chars).strip()


def test_truncated_file_raises_a_typed_error():
    with pytest.raises(UnparseableDocument):
        extract_document(fixture_path("corrupt"))


def test_a_broken_page_does_not_lose_the_document():
    pages, _assets, warnings = extract_document(fixture_path("damaged_page"))
    assert len(pages) == 2
    assert pages[0].chars, "the intact page still extracts"
    assert not pages[1].chars, "the damaged page yields nothing"
    assert any("page 2" in w for w in warnings)


def test_page_selection_is_honoured():
    pages, _assets, _warnings = extract_document(fixture_path("mixed_sizes"), pages=[1])
    assert [p.index for p in pages] == [1]
