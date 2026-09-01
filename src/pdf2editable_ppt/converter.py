"""The conversion driver: extract, analyse, build, verify, fall back, report.

The verify/fall-back loop is the whole point of the tool.  A first deck is
built from everything we believe we can rebuild natively; it is then rendered
and compared against the source page.  Wherever the rebuild does not match,
the offending *region* -- and everything painted in it -- is replaced by a
render of the source, so the slide looks right even where it is no longer
editable.  Nothing is ever drawn twice: an element replaced by a fallback is
removed from the slide.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from .analyze import tables as tablemod
from .analyze.text import normalize_for_compare
from .build.pptx_writer import build_deck
from .extract.content import UnparseableDocument, extract_document
from .ir import (
    Document,
    Element,
    ElementType,
    ImageAsset,
    ImageContent,
    Outcome,
    Page,
)
from .pipeline import ConvertOptions, apply_vector_budget, build_page
from .report import ReportBuilder, write_report
from .units import Rect
from .verify import compare as cmpmod
from .verify import render as rendermod

# Elements smaller than this (points) are never verified individually; the
# comparison is dominated by antialiasing at that size.
MIN_VERIFY_DIM_PT = 4.0
# How many times a failing region may grow to swallow its overlaps.
REGION_GROW_ROUNDS = 4
# Above this share of the page, a region fallback becomes a page fallback.
PAGE_FALLBACK_AREA = 0.85


class ConversionResult:
    def __init__(
        self,
        document: Document,
        output_path: str,
        report: Dict,
        renders: Optional[List[Image.Image]] = None,
    ) -> None:
        self.document = document
        self.output_path = output_path
        self.report = report
        self.renders = renders


def convert(
    pdf_path: str,
    output_path: str,
    options: Optional[ConvertOptions] = None,
    pages: Optional[Sequence[int]] = None,
    password: str = "",
    font_substitutions: Optional[Dict[str, str]] = None,
    report_path: Optional[str] = None,
) -> ConversionResult:
    opts = options or ConvertOptions()
    try:
        raw_pages, assets, warnings = extract_document(
            pdf_path, pages=pages, password=password, substitutions=font_substitutions
        )
    except UnparseableDocument as exc:
        # A damaged file still has pages a renderer can recover.  Producing a
        # faithful picture of them beats refusing to convert; the report says
        # plainly that nothing on these slides is editable.
        return _render_only_conversion(
            pdf_path, output_path, opts, pages, str(exc), report_path, password
        )
    document = Document(assets=assets, source_path=pdf_path, warnings=list(warnings))

    for raw in raw_pages:
        page = build_page(raw, assets, opts, document.warnings)
        if opts.detect_tables and not page.scanned:
            counter = [0]

            def next_id(prefix: str) -> str:
                counter[0] += 1
                return "p%d-%s%d" % (page.index + 1, prefix, counter[0])

            for table in tablemod.detect_tables(page.elements, next_id):
                page.elements.append(table)
            page.elements.sort(key=lambda e: e.source_paint_order)
        apply_vector_budget(page, opts.vector_budget, document.warnings)
        document.pages.append(page)

    reporter = ReportBuilder(
        pdf_path,
        {
            "mode": opts.mode,
            "verify": opts.verify,
            "dpi": opts.dpi,
            "fallbackDpi": opts.fallback_dpi,
            "vectorBudget": opts.vector_budget,
            "detectTables": opts.detect_tables,
            "pages": list(pages) if pages is not None else "all",
        },
    )

    # Pages that already know they cannot be rebuilt get their fallback before
    # the first build, so we never ship a slide we know is wrong.
    for page in document.pages:
        if page.scanned:
            _keep_scanned_page_intact(page)
        elif page.degraded:
            _page_fallback(
                document,
                page,
                pdf_path,
                opts,
                reporter,
                page.degraded_reason or "page could not be parsed",
                password,
            )

    _materialise_fallbacks(document, pdf_path, opts, password)
    build_deck(document, output_path)

    if opts.verify:
        _verify_and_repair(document, pdf_path, output_path, opts, reporter, password)

    text_check = _text_integrity(raw_pages, document)
    report = reporter.build(document, output_path, text_check=text_check)
    if report_path:
        write_report(report, report_path)
    return ConversionResult(document, output_path, report)


def _render_only_conversion(
    pdf_path: str,
    output_path: str,
    opts: ConvertOptions,
    pages: Optional[Sequence[int]],
    reason: str,
    report_path: Optional[str],
    password: str = "",
) -> ConversionResult:
    """Last resort: one rendered image per page, and say so loudly."""
    document = Document(source_path=pdf_path)
    document.warnings.append(
        "the PDF could not be parsed structurally (%s); every page is a render "
        "of the source and nothing on these slides is editable" % reason
    )
    try:
        total = rendermod.page_count(pdf_path, password)
    except Exception as exc:
        raise UnparseableDocument(
            "the PDF could not be parsed or rendered (%s)" % exc
        ) from exc
    wanted = sorted(set(pages)) if pages is not None else list(range(total))
    reporter = ReportBuilder(
        pdf_path,
        {"mode": opts.mode, "verify": False, "renderOnly": True, "fallbackDpi": opts.fallback_dpi},
    )
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_path, password=password or None)
    try:
        for index in wanted:
            if index >= total:
                continue
            width_pt, height_pt = doc[index].get_size()
            page = Page(
                index=index,
                width_pt=float(width_pt),
                height_pt=float(height_pt),
                degraded=True,
                degraded_reason=reason,
            )
            document.pages.append(page)
            _page_fallback(document, page, pdf_path, opts, reporter, reason, password)
    finally:
        doc.close()
    build_deck(document, output_path)
    report = reporter.build(document, output_path, text_check=None)
    if report_path:
        write_report(report, report_path)
    return ConversionResult(document, output_path, report)


# ── fallback materialisation ─────────────────────────────────────────────────


def _keep_scanned_page_intact(page: Page) -> None:
    """A scan is already a bitmap; leave the picture object exactly as it is."""
    for el in page.elements:
        if el.type is ElementType.IMAGE:
            el.note(
                "scanned page: the source bitmap is placed unchanged, no text is "
                "reconstructed over it"
            )
            el.outcome = Outcome.NATIVE


def _materialise_fallbacks(
    document: Document, pdf_path: str, opts: ConvertOptions, password: str = ""
) -> None:
    """Render the source region behind every pending fallback element."""
    for page in document.pages:
        pending = [
            el
            for el in page.elements
            if el.type in (ElementType.RASTER_FALLBACK, ElementType.VECTOR_FALLBACK)
            and not el.consumed
            and (el.content is None or not isinstance(el.content, ImageContent))
        ]
        if not pending:
            continue
        # Merge overlapping pending regions so we render each area once.
        regions = _merge_regions([el.render_bounds() for el in pending])
        for region in regions:
            members = [el for el in pending if el.render_bounds().intersection(region) is not None]
            asset = _render_region_asset(
                document, pdf_path, page, region, opts.fallback_dpi, password
            )
            if asset is None:
                for el in members:
                    el.outcome = Outcome.UNSUPPORTED
                    el.note("the region could not be rendered; nothing was emitted")
                    el.consumed = True
                continue
            anchor = min(members, key=lambda e: e.source_paint_order)
            anchor.bbox = region
            anchor.paint_bbox = region
            anchor.rotation_deg = 0.0
            anchor.content = ImageContent(asset_id=asset.asset_id)
            anchor.source_asset_id = asset.asset_id
            anchor.type = ElementType.RASTER_FALLBACK
            anchor.outcome = Outcome.RASTER_FALLBACK
            anchor.note(
                "region rendered from the source PDF at %.0f dpi" % opts.fallback_dpi
            )
            for el in members:
                if el is anchor:
                    continue
                el.consumed = True
                el.absorbed_by = anchor.id
                el.outcome = Outcome.RASTER_FALLBACK
                el.fallback_reason = el.fallback_reason or anchor.fallback_reason
                el.note("covered by fallback region %s" % anchor.id)


def _merge_regions(boxes: Sequence[Rect], pad: float = 1.0) -> List[Rect]:
    regions: List[Rect] = []
    for box in boxes:
        box = box.expanded(pad)
        merged = True
        while merged:
            merged = False
            for i, existing in enumerate(regions):
                if existing.intersection(box) is not None:
                    box = existing.union(box)
                    regions.pop(i)
                    merged = True
                    break
        regions.append(box)
    return regions


def _render_region_asset(
    document: Document,
    pdf_path: str,
    page: Page,
    region: Rect,
    dpi: float,
    password: str = "",
) -> Optional[ImageAsset]:
    try:
        img = rendermod.render_pdf_region(
            pdf_path, page.index, region, page.height_pt, dpi=dpi, password=password
        )
    except Exception:
        return None
    if img.width < 1 or img.height < 1:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    asset_id = "fb%04d" % (sum(1 for k in document.assets if k.startswith("fb")) + 1)
    asset = ImageAsset(
        asset_id=asset_id,
        data=data,
        ext="png",
        width_px=img.width,
        height_px=img.height,
        passthrough=False,
        source_sha256="",
        output_sha256=hashlib.sha256(data).hexdigest(),
        note="lossless render of the source page region (fallback)",
    )
    document.assets[asset_id] = asset
    return asset


def _page_fallback(
    document: Document,
    page: Page,
    pdf_path: str,
    opts: ConvertOptions,
    reporter: ReportBuilder,
    reason: str,
    password: str = "",
) -> None:
    """Replace an entire page with a render of the source page."""
    region = Rect(0, 0, page.width_pt, page.height_pt)
    asset = _render_region_asset(
        document, pdf_path, page, region, opts.fallback_dpi, password
    )
    fallback_id = "p%d-pagefallback" % (page.index + 1)
    for el in page.elements:
        el.consumed = True
        el.absorbed_by = fallback_id
        el.outcome = Outcome.PAGE_FALLBACK
        el.fallback_reason = reason
    if asset is None:
        document.warnings.append(
            "page %d could not be rebuilt and could not be rendered either; "
            "the slide is left empty" % (page.index + 1)
        )
        return
    el = Element(
        id=fallback_id,
        type=ElementType.RASTER_FALLBACK,
        bbox=region,
        z_index=0,
        source_paint_order=-1,
        content=ImageContent(asset_id=asset.asset_id),
        source_asset_id=asset.asset_id,
        confidence=0.0,
        fallback_reason=reason,
        outcome=Outcome.PAGE_FALLBACK,
    )
    el.note("whole page rendered from the source at %.0f dpi" % opts.fallback_dpi)
    page.elements = [e for e in page.elements] + [el]
    page.elements.sort(key=lambda e: e.source_paint_order)
    reporter.page(page).fallback_regions.append(
        {"scope": "page", "reason": reason, "bboxPt": [0, 0, page.width_pt, page.height_pt]}
    )


# ── verification ─────────────────────────────────────────────────────────────


def _verify_and_repair(
    document: Document,
    pdf_path: str,
    output_path: str,
    opts: ConvertOptions,
    reporter: ReportBuilder,
    password: str = "",
) -> None:
    with tempfile.TemporaryDirectory(prefix="p2ep-verify-") as tmp:
        rebuilt = rendermod.render_pptx_pages(output_path, dpi=opts.dpi, work_dir=tmp)
        if rebuilt is None:
            note = (
                "no PPTX renderer (LibreOffice) is available, so the visual "
                "verification step was skipped; every element is reported as built, "
                "not as verified"
            )
            document.warnings.append(note)
            for page in document.pages:
                for el in page.elements:
                    if el.outcome is Outcome.NATIVE and not el.consumed:
                        el.outcome = Outcome.NATIVE_WITH_WARNING
                        el.note("not visually verified: no PPTX renderer available")
            return

        repaired = False
        for page in document.pages:
            if page.index >= len(rebuilt):
                continue
            source = rendermod.render_pdf_page(
                pdf_path, page.index, dpi=opts.dpi, password=password
            )
            slide = _slide_region_for_page(rebuilt[page.index], document, page)
            comparison = cmpmod.compare_images(
                source, slide, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
            )
            ok, why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
            pr = reporter.page(page)
            pr.verification = {
                "pass": ok,
                "detail": why,
                "metrics": comparison.to_dict(),
                "dpi": opts.dpi,
            }
            if ok:
                continue
            culprits = _find_culprits(source, slide, document, page, opts)
            if not culprits:
                _page_fallback(
                    document,
                    page,
                    pdf_path,
                    opts,
                    reporter,
                    "the rebuilt slide does not match the source and no single "
                    "region explains the difference (%s)" % why,
                    password,
                )
                repaired = True
                continue
            for el, detail in culprits:
                el.type = ElementType.VECTOR_FALLBACK
                el.outcome = Outcome.RASTER_FALLBACK
                el.confidence = min(el.confidence, 0.3)
                el.fallback_reason = "visual check failed: %s" % detail
                pr.fallback_regions.append(
                    {
                        "scope": "element",
                        "elementId": el.id,
                        "reason": el.fallback_reason,
                        "bboxPt": [round(v, 2) for v in el.render_bounds().as_tuple()],
                    }
                )
            repaired = True

        if repaired:
            _materialise_fallbacks(document, pdf_path, opts, password)
            build_deck(document, output_path)
            rebuilt2 = rendermod.render_pptx_pages(output_path, dpi=opts.dpi, work_dir=tmp)
            if rebuilt2 is None:
                return
            for page in document.pages:
                if page.index >= len(rebuilt2):
                    continue
                source = rendermod.render_pdf_page(
                    pdf_path, page.index, dpi=opts.dpi, password=password
                )
                slide = _slide_region_for_page(rebuilt2[page.index], document, page)
                comparison = cmpmod.compare_images(
                    source, slide, smooth_px=cmpmod.PAGE_THRESHOLDS.smooth_px
                )
                ok, why = cmpmod.evaluate(comparison, cmpmod.PAGE_THRESHOLDS)
                pr = reporter.page(page)
                pr.verification = {
                    "pass": ok,
                    "detail": why,
                    "metrics": comparison.to_dict(),
                    "dpi": opts.dpi,
                    "afterRepair": True,
                }
                if not ok:
                    document.warnings.append(
                        "page %d still differs from the source after the region "
                        "fallbacks (%s); see the page's verification metrics"
                        % (page.index + 1, why)
                    )


def _slide_region_for_page(slide: Image.Image, document: Document, page: Page) -> Image.Image:
    """Crop a rendered slide back to the area this page occupies.

    Mixed page sizes letterbox smaller pages inside the deck's slide size, so
    the comparison must look at the page's own rectangle, not the whole slide.
    """
    deck_w = max((p.width_pt for p in document.pages), default=page.width_pt)
    deck_h = max((p.height_pt for p in document.pages), default=page.height_pt)
    if abs(deck_w - page.width_pt) < 0.5 and abs(deck_h - page.height_pt) < 0.5:
        return slide
    sx = slide.width / deck_w
    sy = slide.height / deck_h
    ox = (deck_w - page.width_pt) / 2.0
    oy = (deck_h - page.height_pt) / 2.0
    return slide.crop(
        (
            int(round(ox * sx)),
            int(round(oy * sy)),
            int(round((ox + page.width_pt) * sx)),
            int(round((oy + page.height_pt) * sy)),
        )
    )


def _find_culprits(
    source: Image.Image,
    slide: Image.Image,
    document: Document,
    page: Page,
    opts: ConvertOptions,
) -> List[Tuple[Element, str]]:
    """Score each rebuilt element's own region and return the ones that fail."""
    out: List[Tuple[Element, str]] = []
    scale_x = source.width / max(1e-6, page.width_pt)
    scale_y = source.height / max(1e-6, page.height_pt)
    slide_fit = slide.resize(source.size, Image.LANCZOS) if slide.size != source.size else slide
    for el in page.elements:
        if el.consumed or el.type in (
            ElementType.RASTER_FALLBACK,
            ElementType.VECTOR_FALLBACK,
        ):
            continue
        box = el.render_bounds()
        if box.width < MIN_VERIFY_DIM_PT or box.height < MIN_VERIFY_DIM_PT:
            continue
        crop = _crop(source, box, page.height_pt, scale_x, scale_y)
        rebuilt_crop = _crop(slide_fit, box, page.height_pt, scale_x, scale_y)
        if crop is None or rebuilt_crop is None:
            continue
        thresholds = (
            cmpmod.TEXT_THRESHOLDS
            if el.type in (ElementType.TEXT, ElementType.TABLE)
            else cmpmod.Thresholds()
        )
        comparison = cmpmod.compare_images(
            crop, rebuilt_crop, smooth_px=thresholds.smooth_px
        )
        ok, why = cmpmod.evaluate(comparison, thresholds)
        if not ok:
            out.append((el, why))
    return out


def _crop(
    img: Image.Image, box: Rect, page_height_pt: float, sx: float, sy: float
) -> Optional[Image.Image]:
    left = int(max(0, round(box.x0 * sx)))
    right = int(min(img.width, round(box.x1 * sx)))
    top = int(max(0, round((page_height_pt - box.y1) * sy)))
    bottom = int(min(img.height, round((page_height_pt - box.y0) * sy)))
    if right - left < 2 or bottom - top < 2:
        return None
    return img.crop((left, top, right, bottom))


# ── text integrity ───────────────────────────────────────────────────────────


def _text_integrity(raw_pages, document: Document) -> Dict:
    """Compare the source's characters with what the deck actually carries."""
    source_text: List[str] = []
    for raw in raw_pages:
        for c in sorted(raw.chars, key=lambda c: c.paint_order):
            source_text.append(c.text)
    source_norm = normalize_for_compare("".join(source_text))

    out_text: List[str] = []
    for page in document.pages:
        for el in page.elements:
            if el.consumed:
                continue
            if el.type is ElementType.TEXT and el.content is not None:
                out_text.append(el.content.text)
            elif el.type is ElementType.TABLE and el.content is not None:
                for cell in el.content.cells:
                    if cell.text is not None:
                        out_text.append(cell.text.text)
    out_norm = normalize_for_compare("".join(out_text))

    # Rasterised regions legitimately carry their text as pixels; count them so
    # a difference can be attributed rather than silently tolerated.
    rasterised = sum(
        1
        for page in document.pages
        for el in page.elements
        if el.consumed and el.type is ElementType.TEXT
    )
    return {
        "sourceChars": len(source_norm),
        "outputChars": len(out_norm),
        "identical": source_norm == out_norm,
        "textElementsRasterised": rasterised,
        "note": (
            "counts ignore whitespace and use NFC normalisation; a difference is "
            "expected only where text was deliberately kept as a rendered region"
        ),
    }
