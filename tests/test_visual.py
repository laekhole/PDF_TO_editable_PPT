"""Visual regression: render both sides and compare what a reader would see.

These tests need a PPTX renderer (LibreOffice headless).  Without one they
skip rather than pass vacuously -- a converter that claims fidelity it never
measured is exactly the failure mode this project exists to avoid.
"""

import os

import numpy as np
import pytest
from PIL import Image

from conftest import ARTIFACT_DIR, fixture_path
from pdf2editable_ppt.verify import compare as cmpmod
from pdf2editable_ppt.verify import render as rendermod

DPI = 150.0


@pytest.fixture(scope="session")
def renderer(have_renderer):
    if not have_renderer:
        pytest.skip("no PPTX renderer (LibreOffice) available")
    return True


def _pair(conversions, name, page=0):
    result = conversions.get(name, verify=True)
    slides = rendermod.render_pptx_pages(result.output_path, dpi=DPI)
    assert slides is not None
    source = rendermod.render_pdf_page(fixture_path(name), page, dpi=DPI)
    return source, slides[page], result


def _save_diff(name, source, rebuilt):
    """Keep a side-by-side for a human to look at when a test fails."""
    rebuilt = rebuilt.resize(source.size)
    canvas = Image.new("RGB", (source.width * 2 + 8, source.height), "#cccccc")
    canvas.paste(source, (0, 0))
    canvas.paste(rebuilt, (source.width + 8, 0))
    canvas.save(os.path.join(ARTIFACT_DIR, "%s.compare.png" % name))


@pytest.mark.parametrize(
    "name",
    ["shapes", "chart", "images", "text_mixed", "table_lattice", "table_borderless"],
)
def test_page_matches_the_source(renderer, conversions, name):
    source, rebuilt, _result = _pair(conversions, name)
    _save_diff(name, source, rebuilt)
    comparison = cmpmod.compare_images(
        source, rebuilt, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
    )
    ok, why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
    assert ok, "%s: %s (%s)" % (name, why, comparison.to_dict())


@pytest.mark.parametrize("name,pages", [("rotated_pages", 2), ("mixed_sizes", 3)])
def test_every_page_matches(renderer, conversions, name, pages):
    result = conversions.get(name, verify=True)
    slides = rendermod.render_pptx_pages(result.output_path, dpi=DPI)
    assert slides is not None and len(slides) == pages
    from pdf2editable_ppt.converter import _slide_region_for_page

    for index in range(pages):
        source = rendermod.render_pdf_page(fixture_path(name), index, dpi=DPI)
        page = result.document.pages[index]
        rebuilt = _slide_region_for_page(slides[index], result.document, page)
        comparison = cmpmod.compare_images(
            source, rebuilt, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
        )
        ok, why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
        assert ok, "%s page %d: %s" % (name, index + 1, why)


def test_a_fallback_region_is_pixel_faithful(renderer, conversions):
    """The point of falling back is that the picture is right."""
    source, rebuilt, _result = _pair(conversions, "clip_gradient")
    _save_diff("clip_gradient", source, rebuilt)
    comparison = cmpmod.compare_images(source, rebuilt, smooth_px=1.0)
    assert comparison.ink_missing < 0.02
    assert comparison.mean_delta < 12


def test_a_scan_is_reproduced_without_being_altered(renderer, conversions):
    source, rebuilt, _result = _pair(conversions, "scanned")
    _save_diff("scanned", source, rebuilt)
    comparison = cmpmod.compare_images(source, rebuilt, smooth_px=1.0)
    assert comparison.ink_missing < 0.05
    assert comparison.ink_added < 0.05


def test_overlapping_objects_keep_their_z_order(renderer, conversions):
    """The fixture stacks an opaque slab, a translucent slab and a disc."""
    source, rebuilt, _result = _pair(conversions, "shapes")
    box = (56, 280, 320, 430)  # the overlap block, in PDF points
    scale = DPI / 72.0
    height = 792.0

    def crop(img):
        return img.crop(
            (
                int(box[0] * scale),
                int((height - box[3]) * scale),
                int(box[2] * scale),
                int((height - box[1]) * scale),
            )
        )

    comparison = cmpmod.compare_images(crop(source), crop(rebuilt), smooth_px=1.0)
    assert comparison.mean_delta < 18, comparison.to_dict()


def test_colours_are_not_shifted_by_the_image_path(renderer, conversions):
    source, rebuilt, _result = _pair(conversions, "images")
    a = np.asarray(source.convert("RGB"), dtype=np.int16)
    b = np.asarray(rebuilt.resize(source.size).convert("RGB"), dtype=np.int16)
    painted = (255 - a).max(axis=2) > 24
    assert painted.any()
    assert np.abs(a - b).max(axis=2)[painted].mean() < 12


def test_the_comparison_actually_fails_on_a_damaged_rebuild(renderer, conversions):
    """A metric that never fails is not a check.  Break one and watch it fire."""
    source, rebuilt, _result = _pair(conversions, "shapes")
    broken = rebuilt.copy()
    # erase the middle third: the sort of loss a dropped object produces
    broken.paste(
        Image.new("RGB", (broken.width, broken.height // 3), "white"),
        (0, broken.height // 3),
    )
    comparison = cmpmod.compare_images(
        source, broken, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
    )
    ok, _why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
    assert not ok


def test_a_shifted_rebuild_is_caught(renderer, conversions):
    source, rebuilt, _result = _pair(conversions, "text_mixed")
    shifted = Image.new("RGB", rebuilt.size, "white")
    shifted.paste(rebuilt, (0, 14))  # ~7pt down at 150 dpi
    comparison = cmpmod.compare_images(
        source, shifted, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
    )
    ok, _why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
    assert not ok
