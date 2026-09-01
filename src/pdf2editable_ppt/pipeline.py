"""Extraction -> IR -> classification.

This stage is where the non-destructive policy is enforced: every element
carries the confidence with which it was recognised and, when we decline to
rebuild it natively, the reason.  Nothing here writes a .pptx; the builder
does that from the IR the pipeline hands back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .analyze import shapes as shapemod
from .analyze.tables import RULE_MAX_THICKNESS_PT
from .analyze.text import build_text_blocks
from .extract.content import PathRecord, RawPage
from .ir import (
    Element,
    ElementType,
    ImageAsset,
    ImageContent,
    Outcome,
    Page,
    Path,
    Style,
)
from .units import Matrix, PageGeometry, Rect, apply_matrix, rect_from_points

# A page with no text and one image covering at least this share of it is a scan.
SCAN_IMAGE_COVER = 0.7
# ... and at most this many characters.
SCAN_MAX_CHARS = 12
# Above this many native shapes a slide starts to hurt PowerPoint; the densest
# vector regions fall back instead.
VECTOR_BUDGET_PER_PAGE = 900
# A stroke thinner than this is treated as a hairline rule, not a filled shape.
HAIRLINE_PT = 0.05


@dataclass
class ConvertOptions:
    mode: str = "fidelity"
    dpi: float = 150.0
    fallback_dpi: float = 220.0
    verify: bool = True
    vector_budget: int = VECTOR_BUDGET_PER_PAGE
    debug_assets: Optional[str] = None
    detect_tables: bool = True


def _visualizer(raw: RawPage) -> PageGeometry:
    """Geometry mapping the extractor's output onto the slide.

    pdfminer already bakes the page's /Rotate into the initial CTM (using the
    MediaBox), so everything the extractor reports is *already* in visually
    upright space -- rotating again here would turn every page twice.  What is
    left to do is honour the CropBox, which pdfminer ignores: rotate its
    corners the same way and use that as the page origin and size, so the
    coordinates line up with what a viewer (and PDFium) shows.
    """
    rot = raw.rotation % 360
    mb, cb = raw.media_box, raw.crop_box

    def to_vis(x: float, y: float) -> Tuple[float, float]:
        if rot == 90:
            return (y - mb.y0, mb.x1 - x)
        if rot == 180:
            return (mb.x1 - x, mb.y1 - y)
        if rot == 270:
            return (mb.y1 - y, x - mb.x0)
        return (x - mb.x0, y - mb.y0)

    visual_crop = rect_from_points(
        [
            to_vis(cb.x0, cb.y0),
            to_vis(cb.x1, cb.y0),
            to_vis(cb.x1, cb.y1),
            to_vis(cb.x0, cb.y1),
        ]
    )
    assert visual_crop is not None
    return PageGeometry(
        media_x0=visual_crop.x0,
        media_y0=visual_crop.y0,
        media_width=visual_crop.width,
        media_height=visual_crop.height,
        rotation=0,
    )


def _map_rect(geom: PageGeometry, rect: Rect) -> Rect:
    corners = [
        geom.to_visual(rect.x0, rect.y0),
        geom.to_visual(rect.x1, rect.y0),
        geom.to_visual(rect.x1, rect.y1),
        geom.to_visual(rect.x0, rect.y1),
    ]
    out = rect_from_points(corners)
    assert out is not None
    return out


def _map_path(geom: PageGeometry, path: Path) -> Path:
    return Path(
        segments=[s.transformed(lambda p: geom.to_visual(p[0], p[1])) for s in path.segments],
        even_odd=path.even_odd,
    )


def _rotated_aabb(box: Rect, rotation_deg: float) -> Rect:
    """Page-space bounds of ``box`` after DrawingML rotates it about its centre."""
    if abs(rotation_deg) < 0.01:
        return box
    r = math.radians(rotation_deg)
    c, s_ = math.cos(r), math.sin(r)
    cx, cy = box.cx, box.cy
    pts = []
    for px, py in (
        (box.x0, box.y0),
        (box.x1, box.y0),
        (box.x1, box.y1),
        (box.x0, box.y1),
    ):
        dx, dy = px - cx, py - cy
        pts.append((cx + dx * c - dy * s_, cy + dx * s_ + dy * c))
    out = rect_from_points(pts)
    assert out is not None
    return out


def _rotation_offset(geom: PageGeometry) -> float:
    """Extra rotation this geometry adds to every element, in PDF degrees.

    Always zero for pages coming out of the pdfminer extractor (see
    :func:`_visualizer`); kept as a hook so a future extractor that leaves
    /Rotate unapplied can plug in without touching the call sites.
    """
    return {0: 0.0, 90: -90.0, 180: 180.0, 270: 90.0}[geom.rotation % 360]


def _map_oriented(
    geom: PageGeometry, box: Rect, own_rotation_deg: float
) -> Tuple[Rect, float, Rect]:
    """Map a rotatable object into visual space.

    DrawingML stores a rotated shape as its *unrotated* extent plus an angle,
    and rotates about the extent's centre.  So the page's /Rotate must move the
    centre and be added to the angle -- it must NOT also transpose the extent,
    which is what mapping the corner box would do and would rotate the object
    twice.  Returns ``(extent, angle, painted bounds)``.
    """
    cx, cy = geom.to_visual(box.cx, box.cy)
    half_w, half_h = box.width / 2.0, box.height / 2.0
    frame = Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)
    angle = own_rotation_deg + _rotation_offset(geom)
    return frame, angle, _rotated_aabb(frame, angle)


# A drawn rule is at least this long; its maximum thickness is the table
# analyser's, so the two stages agree on what counts as a ruling.
RULE_MIN_LENGTH_PT = 6.0


def _vertical_rule_positions(paths: Sequence[PathRecord]) -> List[float]:
    """x positions of the vertical rules a page draws, in source page space.

    Text never crosses one of these, so they are hard split points for the
    line reconstruction: two table cells whose contents almost touch across a
    ruling are separate boxes even though the gap is small.
    """
    out: List[float] = []
    for rec in paths:
        if not (rec.fill or rec.stroke):
            continue
        box = rec.bbox
        if box.height < RULE_MIN_LENGTH_PT:
            continue
        if box.width <= RULE_MAX_THICKNESS_PT:
            out.append(box.cx)
        elif rec.stroke and not rec.fill and box.width > RULE_MAX_THICKNESS_PT:
            # a stroked rectangle contributes its two vertical edges
            if rec.path.subpath_count() == 1 and not rec.path.has_curves():
                out.append(box.x0)
                out.append(box.x1)
    return sorted(set(round(v, 2) for v in out))


def _style_from_path(rec: PathRecord) -> Style:
    return Style(
        fill_color=rec.fill_color if rec.fill else None,
        fill_alpha=rec.fill_alpha,
        stroke_color=rec.stroke_color if rec.stroke else None,
        stroke_alpha=rec.stroke_alpha,
        stroke_width_pt=rec.line_width_pt if rec.stroke else 0.0,
        dash=rec.dash if rec.stroke else None,
        line_cap=rec.line_cap,
        line_join=rec.line_join,
    )


def _clipped(bbox: Rect, clip: Optional[Rect]) -> Tuple[Rect, bool]:
    """Intersect a bbox with the active clip; report whether the clip bit."""
    if clip is None:
        return bbox, False
    inter = bbox.intersection(clip)
    if inter is None:
        return bbox, False
    bit = (
        inter.x0 > bbox.x0 + 0.25
        or inter.y0 > bbox.y0 + 0.25
        or inter.x1 < bbox.x1 - 0.25
        or inter.y1 < bbox.y1 - 0.25
    )
    return (inter if bit else bbox), bit


def _image_placement(
    ctm: Matrix,
) -> Tuple[Rect, float, bool, bool]:
    """Decompose an image CTM into an upright box, rotation and flips.

    PDF paints an image by mapping the unit square through the CTM; the image
    row order means the unit square's *top* edge (y = 1) carries the first
    scanline.  A plain upright placement therefore has d > 0 in PDF space.
    """
    a, b, c, d, e, f = ctm
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    rotation = math.degrees(math.atan2(b, a))
    # Determinant sign tells us whether the mapping mirrors.
    det = a * d - b * c
    flip_h = False
    flip_v = False
    if det < 0:
        flip_h = True
    corners = [
        apply_matrix(ctm, (0.0, 0.0)),
        apply_matrix(ctm, (1.0, 0.0)),
        apply_matrix(ctm, (1.0, 1.0)),
        apply_matrix(ctm, (0.0, 1.0)),
    ]
    cx = sum(p[0] for p in corners) / 4.0
    cy = sum(p[1] for p in corners) / 4.0
    box = Rect(cx - sx / 2.0, cy - sy / 2.0, cx + sx / 2.0, cy + sy / 2.0)
    return box, rotation, flip_h, flip_v


def _crop_from_clip(box: Rect, clip: Optional[Rect]) -> Tuple[Rect, Optional[Tuple[float, ...]]]:
    """Turn a clip that cuts an image into a DrawingML srcRect crop."""
    if clip is None or box.width <= 0 or box.height <= 0:
        return box, None
    inter = box.intersection(clip)
    if inter is None:
        return box, None
    l = (inter.x0 - box.x0) / box.width
    r = (box.x1 - inter.x1) / box.width
    # PDF y is up, srcRect top is the high-y edge.
    t = (box.y1 - inter.y1) / box.height
    b = (inter.y0 - box.y0) / box.height
    if max(l, r, t, b) <= 0.002:
        return box, None
    return inter, (l, t, r, b)


def build_page(
    raw: RawPage,
    assets: Dict[str, ImageAsset],
    options: ConvertOptions,
    warnings: List[str],
) -> Page:
    geom = _visualizer(raw)
    page = Page(
        index=raw.index,
        width_pt=geom.visual_width,
        height_pt=geom.visual_height,
        rotation=raw.rotation,
        crop_box=raw.crop_box,
    )
    counter = 0

    def next_id(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return "p%d-%s%d" % (raw.index + 1, prefix, counter)

    # ── images ──────────────────────────────────────────────────────────────
    for rec in raw.images:
        if rec.asset_id is None:
            page.elements.append(
                Element(
                    id=next_id("img"),
                    type=ElementType.RASTER_FALLBACK,
                    bbox=_map_rect(geom, rec.bbox),
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    confidence=0.0,
                    fallback_reason=rec.failed_reason
                    or "image stream could not be decoded",
                    outcome=Outcome.RASTER_FALLBACK,
                )
            )
            continue
        box, rotation, flip_h, flip_v = _image_placement(rec.ctm)
        box, crop = _crop_from_clip(box, rec.clip)
        frame, angle, painted = _map_oriented(geom, box, rotation)
        el = Element(
            id=next_id("img"),
            type=ElementType.IMAGE,
            bbox=frame,
            z_index=rec.paint_order,
            source_paint_order=rec.paint_order,
            opacity=rec.alpha,
            clip_path=rec.clip,
            content=ImageContent(asset_id=rec.asset_id, crop=crop, flip_h=flip_h, flip_v=flip_v),
            source_asset_id=rec.asset_id,
            rotation_deg=angle,
            transform=rec.ctm,
            paint_bbox=painted,
        )
        asset = assets[rec.asset_id]
        if asset.passthrough:
            el.note("original %s stream reused without re-encoding" % asset.ext.upper())
        else:
            el.note(asset.note)
        if crop:
            el.note("clip recovered as a picture crop")
        if rec.alpha < 0.999:
            el.note("constant alpha %.2f from /ExtGState" % rec.alpha)
        page.elements.append(el)

    # ── vector paths ────────────────────────────────────────────────────────
    for rec in raw.paths:
        if not rec.fill and not rec.stroke:
            continue  # a clip-only path paints nothing
        if rec.fill and rec.fill_alpha <= 0.001 and not rec.stroke:
            continue
        style = _style_from_path(rec)
        if not style.has_fill and not style.has_stroke:
            continue
        stroke_only = rec.stroke and not rec.fill
        match = shapemod.classify(rec.path, stroke_only)
        mapped_path = _map_path(geom, rec.path)
        bbox_v = _map_rect(geom, match.bbox)
        clipped_box, clip_bit = _clipped(bbox_v, _map_rect(geom, rec.clip) if rec.clip else None)

        el_id = next_id("shp")
        if clip_bit:
            # A clipped vector is not the shape it looks like; keeping the
            # unclipped geometry would paint outside the source's ink.
            page.elements.append(
                Element(
                    id=el_id,
                    type=ElementType.VECTOR_FALLBACK,
                    bbox=clipped_box,
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    style=style,
                    confidence=0.3,
                    fallback_reason="the path is clipped; a native shape would paint "
                    "outside the source's clip region",
                    outcome=Outcome.RASTER_FALLBACK,
                )
            )
            continue

        if match.kind == "line":
            pts = [p for s in mapped_path.segments for p in s.points]
            page.elements.append(
                Element(
                    id=el_id,
                    type=ElementType.LINE,
                    bbox=bbox_v,
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    style=style,
                    content=(pts[0], pts[-1]),
                    confidence=match.confidence,
                    notes=[match.reason],
                )
            )
        elif match.kind in ("rect", "roundRect"):
            frame, angle, painted = _map_oriented(geom, match.bbox, match.rotation_deg)
            page.elements.append(
                Element(
                    id=el_id,
                    type=ElementType.RECT,
                    bbox=frame,
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    style=style,
                    content={
                        "prst": "roundRect" if match.kind == "roundRect" else "rect",
                        "adjust": match.adjust,
                    },
                    rotation_deg=angle,
                    confidence=match.confidence,
                    notes=[match.reason],
                    paint_bbox=painted,
                )
            )
        elif match.kind == "ellipse":
            frame, angle, painted = _map_oriented(geom, match.bbox, 0.0)
            page.elements.append(
                Element(
                    id=el_id,
                    type=ElementType.ELLIPSE,
                    bbox=frame,
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    style=style,
                    rotation_deg=angle,
                    confidence=match.confidence,
                    notes=[match.reason],
                    paint_bbox=painted,
                )
            )
        else:
            page.elements.append(
                Element(
                    id=el_id,
                    type=ElementType.FREEFORM,
                    bbox=bbox_v,
                    z_index=rec.paint_order,
                    source_paint_order=rec.paint_order,
                    style=style,
                    content=mapped_path,
                    confidence=match.confidence,
                    notes=[match.reason],
                )
            )

    # ── shadings and pattern fills ──────────────────────────────────────────
    for rec in raw.shadings:
        box = rec.bbox
        if rec.clip is not None:
            clipped = box.intersection(rec.clip)
            if clipped is not None:
                box = clipped
        page.elements.append(
            Element(
                id=next_id("shd"),
                type=ElementType.VECTOR_FALLBACK,
                bbox=_map_rect(geom, box),
                z_index=rec.paint_order,
                source_paint_order=rec.paint_order,
                confidence=0.0,
                fallback_reason="%s: PDF shadings and tiling patterns have no "
                "faithful DrawingML equivalent, so the area is kept as a render "
                "of the source" % rec.kind,
                outcome=Outcome.RASTER_FALLBACK,
            )
        )

    # ── text ────────────────────────────────────────────────────────────────
    for block in build_text_blocks(raw.chars, _vertical_rule_positions(raw.paths)):
        frame, angle, painted = _map_oriented(geom, block.bbox, block.rotation_deg)
        page.elements.append(
            Element(
                id=next_id("txt"),
                type=ElementType.TEXT,
                bbox=frame,
                paint_bbox=painted,
                z_index=block.paint_order,
                source_paint_order=block.paint_order,
                opacity=block.alpha,
                content=block.content,
                rotation_deg=angle,
                confidence=1.0,
            )
        )

    page.elements.sort(key=lambda e: e.source_paint_order)

    # ── scanned / degraded classification ───────────────────────────────────
    _classify_page(page, raw, warnings)
    return page


def _classify_page(page: Page, raw: RawPage, warnings: List[str]) -> None:
    page_area = max(1.0, page.width_pt * page.height_pt)
    image_cover = 0.0
    for el in page.elements:
        if el.type is ElementType.IMAGE:
            image_cover = max(image_cover, el.bbox.area / page_area)
    char_count = len(raw.chars)
    if char_count <= SCAN_MAX_CHARS and image_cover >= SCAN_IMAGE_COVER:
        page.scanned = True
        warnings.append(
            "page %d looks like a scan (a single bitmap covers %.0f%% of the page and "
            "there are %d text characters); the bitmap is kept intact and no text is "
            "invented over it" % (page.index + 1, image_cover * 100, char_count)
        )
    if not page.elements and (raw.chars or raw.paths or raw.images):
        page.degraded = True
        page.degraded_reason = "nothing could be reconstructed from the page's paint records"
    if not page.elements and not raw.chars and not raw.paths and not raw.images:
        page.degraded = True
        page.degraded_reason = "the page's content stream yielded no paint operations"


def apply_vector_budget(page: Page, budget: int, warnings: List[str]) -> None:
    """Cap the number of native vector shapes on one slide.

    Beyond a few hundred shapes PowerPoint becomes slow to open and to edit.
    When a page exceeds the budget we do NOT drop shapes: the vector elements
    are marked for a rendered fallback covering exactly their own bounds, so
    the page still looks right and the report says why it is not editable.
    """
    vector_types = (
        ElementType.LINE,
        ElementType.RECT,
        ElementType.ELLIPSE,
        ElementType.FREEFORM,
    )
    vectors = [e for e in page.elements if e.type in vector_types and not e.consumed]
    if len(vectors) <= budget:
        return
    warnings.append(
        "page %d holds %d vector shapes, above the %d-shape budget; the vector "
        "artwork is kept as a rendered region so PowerPoint stays responsive"
        % (page.index + 1, len(vectors), budget)
    )
    for el in vectors:
        el.type = ElementType.VECTOR_FALLBACK
        el.outcome = Outcome.RASTER_FALLBACK
        el.fallback_reason = (
            "page exceeds the %d native-shape budget (%d shapes)" % (budget, len(vectors))
        )
        el.confidence = min(el.confidence, 0.4)
