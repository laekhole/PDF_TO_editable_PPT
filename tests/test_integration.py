"""End-to-end conversions, the report, and the CLI."""

import json
import os

import pytest

from conftest import ARTIFACT_DIR, fixture_path
from pdf2editable_ppt.cli import main, parse_pages
from pdf2editable_ppt.converter import convert
from pdf2editable_ppt.ir import ElementType, Outcome
from pdf2editable_ppt.pipeline import ConvertOptions


def outcomes(result):
    counts = {}
    for page in result.document.pages:
        for el in page.elements:
            if el.consumed:
                continue
            counts[el.outcome] = counts.get(el.outcome, 0) + 1
    return counts


# ── page and object mapping ─────────────────────────────────────────────────


def test_one_page_becomes_one_slide(conversions):
    from pptx import Presentation

    for name, expected in (("shapes", 1), ("rotated_pages", 2), ("mixed_sizes", 3)):
        result = conversions.get(name)
        assert len(result.document.pages) == expected
        assert len(Presentation(result.output_path).slides) == expected


def test_page_selection_converts_only_those_pages(tmp_path):
    out = tmp_path / "sel.pptx"
    result = convert(
        fixture_path("mixed_sizes"),
        str(out),
        options=ConvertOptions(verify=False),
        pages=[0, 2],
    )
    assert [p.index for p in result.document.pages] == [0, 2]


def test_every_object_type_is_produced_somewhere(conversions):
    seen = set()
    for name in ("shapes", "images", "table_lattice", "text_mixed", "clip_gradient"):
        for page in conversions.get(name).document.pages:
            for el in page.elements:
                if not el.consumed:
                    seen.add(el.type)
    assert {
        ElementType.TEXT,
        ElementType.IMAGE,
        ElementType.LINE,
        ElementType.RECT,
        ElementType.ELLIPSE,
        ElementType.FREEFORM,
        ElementType.TABLE,
        ElementType.RASTER_FALLBACK,
    } <= seen


# ── the non-destructive policy ──────────────────────────────────────────────


def test_a_digital_page_is_not_turned_into_one_big_image(conversions):
    for name in ("shapes", "chart", "text_mixed", "table_lattice", "images"):
        result = conversions.get(name, verify=True)
        assert Outcome.PAGE_FALLBACK not in outcomes(result), name


def test_no_text_is_lost_or_duplicated(conversions):
    for name in ("text_mixed", "table_lattice", "table_borderless", "chart", "shapes"):
        integrity = conversions.get(name, verify=True).report["summary"]["textIntegrity"]
        assert integrity["identical"], "%s: %s" % (name, integrity)


def test_independent_bitmaps_are_never_recompressed(conversions):
    result = conversions.get("images", verify=True)
    jpegs = [a for a in result.document.assets.values() if a.ext == "jpg"]
    assert jpegs
    for asset in jpegs:
        assert asset.passthrough
        assert asset.source_sha256 == asset.output_sha256


def test_a_clipped_vector_falls_back_instead_of_overpainting(conversions):
    result = conversions.get("clip_gradient", verify=True)
    fallbacks = [
        el
        for page in result.document.pages
        for el in page.elements
        if el.fallback_reason and "clipped" in el.fallback_reason
    ]
    assert fallbacks, "a clip that a preset cannot express must fall back"


def test_a_gradient_is_reported_rather_than_dropped(conversions):
    result = conversions.get("clip_gradient", verify=True)
    reasons = [
        el.fallback_reason
        for page in result.document.pages
        for el in page.elements
        if el.fallback_reason
    ]
    assert any("shading" in (r or "") or "pattern" in (r or "") for r in reasons)


def test_a_fallback_replaces_rather_than_covers_the_native_object(conversions):
    """Nothing is drawn twice: an element behind a fallback is removed."""
    result = conversions.get("clip_gradient", verify=True)
    for page in result.document.pages:
        live = [e for e in page.elements if not e.consumed]
        rasters = [e for e in live if e.type is ElementType.RASTER_FALLBACK]
        others = [e for e in live if e.type is not ElementType.RASTER_FALLBACK]
        for raster in rasters:
            for other in others:
                overlap = raster.render_bounds().intersection(other.render_bounds())
                if overlap is None:
                    continue
                share = overlap.area / max(1e-6, other.render_bounds().area)
                assert share < 0.9, "%s is painted under %s" % (other.id, raster.id)


def test_a_scan_keeps_its_bitmap_and_gains_no_invented_text(conversions):
    result = conversions.get("scanned", verify=True)
    page = result.document.pages[0]
    assert page.scanned
    live = [e for e in page.elements if not e.consumed]
    assert len(live) == 1 and live[0].type is ElementType.IMAGE
    assert result.report["summary"]["textIntegrity"]["outputChars"] == 0
    assert any("scan" in w for w in result.report["warnings"])


def test_the_shape_budget_falls_back_instead_of_dropping_shapes(conversions):
    result = conversions.get("dense_vector", verify=True)
    assert any("budget" in w for w in result.report["warnings"])
    live = [e for p in result.document.pages for e in p.elements if not e.consumed]
    assert any(e.type is ElementType.RASTER_FALLBACK for e in live)
    assert any(e.type is ElementType.TEXT for e in live), "text is not swept up by the budget"


def test_a_damaged_page_falls_back_without_losing_the_good_one(conversions):
    result = conversions.get("damaged_page", verify=True)
    assert len(result.document.pages) == 2
    first = [e for e in result.document.pages[0].elements if not e.consumed]
    assert any(e.type is ElementType.TEXT for e in first)
    second = [e for e in result.document.pages[1].elements if not e.consumed]
    assert [e.outcome for e in second] == [Outcome.PAGE_FALLBACK]


def test_an_encrypted_pdf_opens_with_its_password(conversions):
    result = conversions.get("encrypted", password="secret")
    assert result.report["summary"]["textIntegrity"]["identical"]


def test_rotated_pages_stay_native(conversions):
    result = conversions.get("rotated_pages", verify=True)
    counts = outcomes(result)
    assert Outcome.PAGE_FALLBACK not in counts
    assert counts.get(Outcome.NATIVE, 0) >= 6


# ── report ──────────────────────────────────────────────────────────────────


def test_report_accounts_for_every_object(conversions):
    result = conversions.get("clip_gradient", verify=True)
    report = result.report
    assert report["schemaVersion"] == "1.0"
    assert report["source"]["sha256"] and report["output"]["sha256"]
    total = sum(report["summary"]["byOutcome"].values())
    assert total == report["summary"]["elements"]
    for page in report["pages"]:
        for element in page["elements"]:
            assert element["outcome"] in {
                "native",
                "native-with-warning",
                "svg-fallback",
                "raster-fallback",
                "page-fallback",
                "unsupported",
            }
            if element["outcome"] not in ("native", "native-with-warning"):
                assert element.get("fallbackReason"), element["id"]


def test_report_records_image_provenance(conversions):
    report = conversions.get("images", verify=True).report
    assets = report["assets"]
    assert assets
    for asset in assets:
        assert asset["outputSha256"]
        if asset["passthrough"]:
            assert asset["sourceSha256"] == asset["outputSha256"]


def test_report_records_the_visual_check(conversions, have_renderer):
    report = conversions.get("shapes", verify=True).report
    page = report["pages"][0]
    if have_renderer:
        assert page["verification"]["pass"] is True
        metrics = page["verification"]["metrics"]
        assert metrics["ink_missing"] < 0.05
        assert metrics["edge_iou"] > 0.7
    else:
        assert any("renderer" in w for w in report["warnings"])


def test_without_a_renderer_nothing_claims_to_be_verified(conversions, monkeypatch, tmp_path):
    import pdf2editable_ppt.verify.render as rendermod

    monkeypatch.setattr(rendermod, "find_soffice", lambda: None)
    result = convert(
        fixture_path("shapes"),
        str(tmp_path / "unverified.pptx"),
        options=ConvertOptions(verify=True),
    )
    live = [e for p in result.document.pages for e in p.elements if not e.consumed]
    assert all(
        e.outcome is not Outcome.NATIVE for e in live
    ), "unverified objects must not be reported as verified"
    assert any("renderer" in w for w in result.report["warnings"])


def test_report_json_is_valid_and_utf8(conversions):
    conversions.get("text_mixed", verify=True)
    path = os.path.join(ARTIFACT_DIR, "text_mixed.verified.report.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["source"]["pages"] == 1


# ── CLI ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,total,expected",
    [
        ("1", None, [0]),
        ("1,3", None, [0, 2]),
        ("2-4", None, [1, 2, 3]),
        ("2-", 5, [1, 2, 3, 4]),
        ("3,1-2", None, [0, 1, 2]),
    ],
)
def test_page_spec_parsing(spec, total, expected):
    assert parse_pages(spec, total) == expected


@pytest.mark.parametrize("spec", ["0", "5-2", "-"])
def test_bad_page_specs_are_rejected(spec):
    if spec == "-":
        with pytest.raises(ValueError):
            parse_pages(spec, None)
    else:
        with pytest.raises(ValueError):
            parse_pages(spec, 10)


def test_cli_converts_and_writes_a_report(tmp_path):
    out = tmp_path / "cli.pptx"
    report = tmp_path / "cli.report.json"
    code = main(
        [
            fixture_path("shapes"),
            "-o",
            str(out),
            "--report",
            str(report),
            "--mode",
            "fast",
            "-q",
        ]
    )
    assert code == 0
    assert out.exists() and report.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["summary"]["pages"] == 1


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.pdf"), "-o", str(tmp_path / "x.pptx")]) == 2


def test_cli_reports_a_wrong_password(tmp_path):
    assert (
        main(
            [fixture_path("encrypted"), "-o", str(tmp_path / "x.pptx"), "--password", "wrong"]
        )
        == 3
    )


def test_cli_reports_an_unreadable_file(tmp_path):
    assert main([fixture_path("corrupt"), "-o", str(tmp_path / "x.pptx")]) == 4


def test_cli_writes_debug_assets(tmp_path):
    debug = tmp_path / "debug"
    code = main(
        [
            fixture_path("images"),
            "-o",
            str(tmp_path / "d.pptx"),
            "--mode",
            "fast",
            "--debug-assets",
            str(debug),
            "-q",
        ]
    )
    assert code == 0
    files = os.listdir(debug)
    assert any(f.endswith(".jpg") for f in files)
    assert "report.json" in files


def test_the_tool_makes_no_network_calls(tmp_path, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the converter must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    offline = convert(
        fixture_path("shapes"),
        str(tmp_path / "offline.pptx"),
        options=ConvertOptions(verify=False),
    )
    assert offline.document.pages
