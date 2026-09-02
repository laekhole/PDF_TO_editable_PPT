"""The separate OCR draft deck.

OCR text is a guess with a confidence, not a recovered source, so it never
goes into the fidelity deck.  This deck has one slide per recognised page
holding only text boxes -- no scan underneath them, so nothing is drawn
twice and nothing pretends to be verified -- a banner naming what it is, and
the full recognised text in the slide notes for copy-and-paste.
"""

from __future__ import annotations

from typing import Dict, Sequence

from ..ir import Document, TextContent, TextLine, TextRun
from ..ocr import LOW_CONFIDENCE, OcrLine, OcrPage
from ..units import Rect
from . import drawingml as dml
from .pptx_writer import DEFAULT_PAGE_PT, DeckWriter, page_geometry

# A Korean-capable face PowerPoint has on every Windows install; the text is
# a draft, so what matters is that it renders and is easy to restyle.
DRAFT_FONT = "Malgun Gothic"
BANNER_FONT = "Arial"
BANNER_COLOR = "9A3412"
LOW_CONFIDENCE_COLOR = "B00020"
BANNER_SIZE_PT = 8.0
BANNER_HEIGHT_PT = 14.0
# Ink height of a text line is about this share of its em: Hangul fills the
# em nearly completely, Latin with descenders a little less.
INK_TO_EM = 0.92
MIN_SIZE_PT = 5.0
MAX_SIZE_PT = 96.0


def _font_size(line: OcrLine) -> float:
    return max(MIN_SIZE_PT, min(MAX_SIZE_PT, line.height_pt * INK_TO_EM))


def _line_box(line: OcrLine, size_pt: float) -> Rect:
    """A top-anchored, inset-free box whose first baseline sits on the ink's.

    Same model as the text analyser: the baseline lies one pitch below the
    top, less the descent; the ink box's bottom is the baseline for Hangul
    and just under it for Latin, so the bottom edge plus a little is used.
    """
    from ..analyze.text import AUTO_LINE_RATIO, DESCENT_EM

    pitch = AUTO_LINE_RATIO * size_pt
    baseline = line.bbox.y0 + 0.08 * line.height_pt
    top = baseline + (pitch - DESCENT_EM * size_pt)
    width = max(line.bbox.width, size_pt)
    return Rect(line.bbox.x0, top - pitch, line.bbox.x0 + width, top)


def _line_content(line: OcrLine, size_pt: float) -> TextContent:
    low = line.confidence < LOW_CONFIDENCE
    run = TextRun(
        text=line.text,
        font_family=DRAFT_FONT,
        size_pt=size_pt,
        color=LOW_CONFIDENCE_COLOR if low else "000000",
        bbox=line.bbox,
    )
    return TextContent(lines=[TextLine(runs=[run], bbox=line.bbox, baseline_y=line.bbox.y0)])


def _banner_content(page: OcrPage) -> TextContent:
    text = (
        "OCR draft (experimental) - engine %s, %d lines, mean confidence %.0f. "
        "This text was recognised from a scan and may be wrong; %d line(s) are "
        "below confidence %.0f and are shown in red. The fidelity deck is untouched."
        % (
            page.engine,
            len(page.lines),
            page.mean_confidence(),
            sum(1 for ln in page.lines if ln.confidence < LOW_CONFIDENCE),
            LOW_CONFIDENCE,
        )
    )
    run = TextRun(text=text, font_family=BANNER_FONT, size_pt=BANNER_SIZE_PT, color=BANNER_COLOR, italic=True)
    return TextContent(lines=[TextLine(runs=[run])])


def _notes(page: OcrPage) -> str:
    lines = ["OCR (experimental) - engine %s, %g dpi, mean confidence %.1f" % (
        page.engine, page.dpi, page.mean_confidence()
    ), ""]
    for ln in page.lines:
        marker = "  [low confidence %.0f]" % ln.confidence if ln.confidence < LOW_CONFIDENCE else ""
        lines.append(ln.text + marker)
    if page.dropped:
        lines.append("")
        lines.append("Dropped as noise (%d): %s" % (len(page.dropped), " | ".join(w.text for w in page.dropped)))
    return "\n".join(lines)


def build_ocr_deck(document: Document, pages: Sequence[OcrPage], output_path: str) -> None:
    by_index: Dict[int, object] = {p.index: p for p in document.pages}
    sizes = []
    for ocr_page in pages:
        page = by_index.get(ocr_page.index)
        sizes.append((page.width_pt, page.height_pt) if page is not None else DEFAULT_PAGE_PT)
    if not sizes:
        sizes = [DEFAULT_PAGE_PT]
    deck_w = max(w for w, _h in sizes)
    deck_h = max(h for _w, h in sizes)
    writer = DeckWriter(deck_w, deck_h)

    for ocr_page, (width, height) in zip(pages, sizes):
        slide = writer.add_slide()
        page = by_index.get(ocr_page.index)
        if page is None:
            from ..ir import Page

            page = Page(index=ocr_page.index, width_pt=width, height_pt=height)
        geom = page_geometry(page, deck_w, deck_h)

        banner_box = Rect(2.0, height - BANNER_HEIGHT_PT, width - 2.0, height)
        x, y, cx, cy = geom.rect_to_emu(banner_box)
        writer.append(
            slide,
            dml.textbox_xml(writer.alloc_id(), "OCR banner", dml.xfrm(x, y, cx, cy), _banner_content(ocr_page)),
        )

        for number, line in enumerate(ocr_page.lines, start=1):
            size = _font_size(line)
            x, y, cx, cy = geom.rect_to_emu(_line_box(line, size))
            writer.append(
                slide,
                dml.textbox_xml(
                    writer.alloc_id(),
                    "OCR line %d" % number,
                    dml.xfrm(x, y, cx, cy),
                    _line_content(line, size),
                ),
            )

        slide.notes_slide.notes_text_frame.text = _notes(ocr_page)

    writer.save(output_path)
