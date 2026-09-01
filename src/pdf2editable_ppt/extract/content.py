"""PDF content-stream extraction into raw paint records.

This module walks every page's content stream with pdfminer.six and records
*paint operations in source order*: characters, paths and image XObjects.  It
deliberately performs no interpretation — no line grouping, no shape guessing.
Everything it emits keeps the information needed to reconstruct or to fall
back, in particular full cubic Bezier control points, dash patterns, constant
alpha from /ExtGState, and the current clip rectangle.

pdfminer's own ``paint_path`` flattens curves and splits subpaths; we override
it so the raw segment list survives.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pdfminer.layout import LTChar
from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
from pdfminer.pdfinterp import PDFGraphicState, PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1
from pdfminer.converter import PDFLayoutAnalyzer

from ..ir import ImageAsset, Path, Segment, SegmentOp
from ..units import Matrix, Rect, apply_matrix, matrix_scale, rect_from_points
from . import colors as colormod
from . import fonts as fontmod
from .images import extract_image


class ExtractionError(RuntimeError):
    pass


class PasswordRequired(ExtractionError):
    pass


class UnparseableDocument(ExtractionError):
    """The PDF's structure could not be read at all (truncated, corrupt).

    The caller falls back to rendering the pages, which PDFium recovers from
    far more often than a structural parse does.
    """


# ── raw paint records ────────────────────────────────────────────────────────


@dataclass
class CharRecord:
    text: str
    bbox: Rect
    matrix: Matrix
    size_pt: float
    font_family: str
    source_font: str
    bold: bool
    italic: bool
    color: str
    alpha: float
    origin: Tuple[float, float]
    upright: bool
    paint_order: int
    clip: Optional[Rect]
    advance: float = 0.0
    render_mode: int = 0


@dataclass
class PathRecord:
    path: Path
    fill: bool
    stroke: bool
    fill_color: Optional[str]
    stroke_color: Optional[str]
    fill_alpha: float
    stroke_alpha: float
    line_width_pt: float
    dash: Optional[Tuple[Sequence[float], float]]
    line_cap: int
    line_join: int
    clip: Optional[Rect]
    bbox: Rect
    paint_order: int
    in_form: bool = False


@dataclass
class ShadingRecord:
    """A gradient / pattern fill.

    DrawingML has gradients, but PDF shadings (7 types, arbitrary function
    stops, mesh shadings) do not map onto them without inventing colours, so
    these become rendered regions instead of a guess.
    """

    bbox: Rect
    kind: str
    paint_order: int
    clip: Optional[Rect]


@dataclass
class ImageRecord:
    asset_id: Optional[str]
    ctm: Matrix
    bbox: Rect
    alpha: float
    clip: Optional[Rect]
    paint_order: int
    width_px: int = 0
    height_px: int = 0
    failed_reason: Optional[str] = None


@dataclass
class RawPage:
    index: int
    media_box: Rect
    crop_box: Rect
    rotation: int
    chars: List[CharRecord] = field(default_factory=list)
    paths: List[PathRecord] = field(default_factory=list)
    images: List[ImageRecord] = field(default_factory=list)
    shadings: List[ShadingRecord] = field(default_factory=list)


# ── the device ───────────────────────────────────────────────────────────────


class _Device(PDFLayoutAnalyzer):
    """Captures paint operations instead of laying them out."""

    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
        assets: Dict[str, ImageAsset],
        substitutions: Dict[str, str],
    ) -> None:
        super().__init__(rsrcmgr, pageno=1, laparams=None)
        self.assets = assets
        self.substitutions = substitutions
        self.page: Optional[RawPage] = None
        self.paint_order = 0
        self.alpha_fill = 1.0
        self.alpha_stroke = 1.0
        self.clip: Optional[Rect] = None
        self.form_depth = 0
        self._asset_by_hash: Dict[str, str] = {}
        self._font_cache: Dict[str, Tuple[str, bool, bool]] = {}
        self.page_box: Optional[Rect] = None

    # -- helpers ---------------------------------------------------------
    def _next_order(self) -> int:
        self.paint_order += 1
        return self.paint_order

    def _font_info(self, font: Any) -> Tuple[str, bool, bool]:
        name = getattr(font, "fontname", "") or ""
        key = name
        cached = self._font_cache.get(key)
        if cached is None:
            flags = fontmod.descriptor_flags(font)
            cached = fontmod.normalize(name, flags, self.substitutions)
            self._font_cache[key] = cached
        return cached

    # -- overrides -------------------------------------------------------
    def begin_figure(self, name: str, bbox, matrix) -> None:  # type: ignore[override]
        self.form_depth += 1

    def end_figure(self, name: str) -> None:  # type: ignore[override]
        self.form_depth = max(0, self.form_depth - 1)

    def render_char(  # type: ignore[override]
        self,
        matrix,
        font,
        fontsize,
        scaling,
        rise,
        cid,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        from pdfminer.pdffont import PDFUnicodeNotDefined

        try:
            text = font.to_unichr(cid)
            if not isinstance(text, str):
                text = ""
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        except Exception:
            text = ""
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix, font, fontsize, scaling, rise, text, textwidth, textdisp, ncs, graphicstate
        )
        # LTChar.size is the glyph box height in *device* space, so a rotated
        # or narrow glyph reports the wrong point size (a rotated space would
        # come out as 0pt).  Derive the em size from the text matrix instead.
        em_scale = math.hypot(matrix[2], matrix[3])
        size_pt = fontsize * em_scale if em_scale > 0 else float(item.size)
        if self.page is not None and text and text.strip("\x00"):
            family, bold, italic = self._font_info(font)
            color = colormod.to_hex(graphicstate.ncolor, getattr(graphicstate, "ncs", None))
            render_mode = int(getattr(graphicstate, "render_mode", 0) or 0)
            if render_mode in (1, 5):  # stroke-only text
                color = (
                    colormod.to_hex(graphicstate.scolor, getattr(graphicstate, "scs", None))
                    or color
                )
            self.page.chars.append(
                CharRecord(
                    text=text,
                    bbox=Rect(*item.bbox).normalized(),
                    matrix=tuple(item.matrix),  # type: ignore[arg-type]
                    size_pt=size_pt,
                    font_family=family,
                    source_font=getattr(font, "fontname", "") or "",
                    bold=bold,
                    italic=italic,
                    color=color or "000000",
                    alpha=self.alpha_fill,
                    origin=(matrix[4], matrix[5]),
                    upright=bool(item.upright),
                    paint_order=self._next_order(),
                    clip=self.clip,
                    advance=item.adv,
                    render_mode=render_mode,
                )
            )
        return item.adv

    def paint_path(  # type: ignore[override]
        self,
        gstate: PDFGraphicState,
        stroke: bool,
        fill: bool,
        evenodd: bool,
        path: Sequence[Any],
    ) -> None:
        if self.page is None or not path:
            return
        ctm = self.ctm
        segments: List[Segment] = []
        for op in path:
            code = str(op[0])
            operands = [float(v) for v in op[1:]]
            pts = [
                apply_matrix(ctm, (operands[i], operands[i + 1]))
                for i in range(0, len(operands) - 1, 2)
            ]
            if code == "m" and pts:
                segments.append(Segment(SegmentOp.MOVE_TO, (pts[0],)))
            elif code == "l" and pts:
                segments.append(Segment(SegmentOp.LINE_TO, (pts[0],)))
            elif code == "c" and len(pts) >= 3:
                segments.append(Segment(SegmentOp.CUBIC_TO, tuple(pts[:3])))
            elif code == "v" and len(pts) >= 2:
                # v: current point doubles as the first control point
                start = _current_point(segments)
                segments.append(Segment(SegmentOp.CUBIC_TO, (start, pts[0], pts[1])))
            elif code == "y" and len(pts) >= 2:
                # y: the end point doubles as the second control point
                segments.append(Segment(SegmentOp.CUBIC_TO, (pts[0], pts[1], pts[1])))
            elif code == "h":
                segments.append(Segment(SegmentOp.CLOSE))
        if not segments:
            return
        bbox = rect_from_points(_flatten_for_bbox(segments))
        if bbox is None:
            return

        fill_hex = colormod.to_hex(gstate.ncolor, getattr(gstate, "ncs", None))
        if fill and fill_hex is None:
            # A pattern or shading fill: the geometry is known but the paint is
            # not something DrawingML can reproduce faithfully.
            self.paint_shading("pattern fill", bbox)
            if not stroke:
                return

        scale = matrix_scale(ctm)
        lw = float(getattr(gstate, "linewidth", 0.0) or 0.0) * scale
        if stroke and lw <= 0.0:
            lw = 0.75  # a 0-width line is "thinnest renderable"; 0.75pt matches viewers
        dash = getattr(gstate, "dash", None)
        dash_norm: Optional[Tuple[Sequence[float], float]] = None
        if dash and isinstance(dash, tuple) and dash[0]:
            try:
                dash_norm = ([float(v) * scale for v in dash[0]], float(dash[1]) * scale)
            except Exception:
                dash_norm = None

        self.page.paths.append(
            PathRecord(
                path=Path(segments=segments, even_odd=bool(evenodd)),
                fill=bool(fill),
                stroke=bool(stroke),
                fill_color=fill_hex,
                stroke_color=colormod.to_hex(gstate.scolor, getattr(gstate, "scs", None)),
                fill_alpha=self.alpha_fill,
                stroke_alpha=self.alpha_stroke,
                line_width_pt=lw,
                dash=dash_norm,
                line_cap=int(getattr(gstate, "linecap", 0) or 0),
                line_join=int(getattr(gstate, "linejoin", 0) or 0),
                clip=self.clip,
                bbox=bbox,
                paint_order=self._next_order(),
                in_form=self.form_depth > 0,
            )
        )

    def paint_shading(self, kind: str, bbox: Optional[Rect]) -> None:
        """Called for ``sh`` and for pattern fills the writer cannot reproduce."""
        if self.page is None:
            return
        box = bbox or self.clip or self.page_box
        if box is None:
            return
        self.page.shadings.append(
            ShadingRecord(
                bbox=box, kind=kind, paint_order=self._next_order(), clip=self.clip
            )
        )

    def render_image(self, name: str, stream: Any) -> None:  # type: ignore[override]
        if self.page is None:
            return
        ctm: Matrix = tuple(self.ctm)  # type: ignore[assignment]
        corners = [
            apply_matrix(ctm, (0.0, 0.0)),
            apply_matrix(ctm, (1.0, 0.0)),
            apply_matrix(ctm, (1.0, 1.0)),
            apply_matrix(ctm, (0.0, 1.0)),
        ]
        bbox = rect_from_points(corners)
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            return
        order = self._next_order()
        try:
            raw = stream.get_data()
            digest = hashlib.sha256(raw).hexdigest()
        except Exception:
            digest = None
        asset_id: Optional[str] = None
        failed: Optional[str] = None
        w_px = h_px = 0
        if digest is not None and digest in self._asset_by_hash:
            asset_id = self._asset_by_hash[digest]
            asset = self.assets[asset_id]
            w_px, h_px = asset.width_px, asset.height_px
        else:
            candidate_id = "img%04d" % (len(self.assets) + 1)
            asset = extract_image(stream, candidate_id)
            if asset is None:
                failed = "image stream uses an encoding PowerPoint cannot display"
            else:
                self.assets[candidate_id] = asset
                if digest is not None:
                    self._asset_by_hash[digest] = candidate_id
                asset_id = candidate_id
                w_px, h_px = asset.width_px, asset.height_px
        self.page.images.append(
            ImageRecord(
                asset_id=asset_id,
                ctm=ctm,
                bbox=bbox,
                alpha=self.alpha_fill,
                clip=self.clip,
                paint_order=order,
                width_px=w_px,
                height_px=h_px,
                failed_reason=failed,
            )
        )


def _current_point(segments: List[Segment]) -> Tuple[float, float]:
    for seg in reversed(segments):
        if seg.points:
            return seg.points[-1]
    return (0.0, 0.0)


def _flatten_for_bbox(segments: List[Segment]) -> List[Tuple[float, float]]:
    """Control-point hull.  A Bezier never leaves its control hull, so this is a
    conservative (never-too-small) bbox and is cheap."""
    return [p for s in segments for p in s.points]


# ── the interpreter ──────────────────────────────────────────────────────────


class _Interpreter(PDFPageInterpreter):
    """Adds the state pdfminer leaves on the floor: ExtGState alpha and clips."""

    def __init__(self, rsrcmgr: PDFResourceManager, device: _Device) -> None:
        super().__init__(rsrcmgr, device)
        self.device: _Device = device
        self._clip_stack: List[Optional[Rect]] = []
        self._alpha_stack: List[Tuple[float, float]] = []

    def dup(self) -> "_Interpreter":  # pdfminer >= 2023 calls this subinterp()
        return self.subinterp()

    def subinterp(self) -> "_Interpreter":  # type: ignore[override]
        sub = _Interpreter(self.rsrcmgr, self.device)
        return sub

    def do_q(self) -> None:
        self._clip_stack.append(self.device.clip)
        self._alpha_stack.append((self.device.alpha_fill, self.device.alpha_stroke))
        super().do_q()

    def do_Q(self) -> None:
        if self._clip_stack:
            self.device.clip = self._clip_stack.pop()
        if self._alpha_stack:
            self.device.alpha_fill, self.device.alpha_stroke = self._alpha_stack.pop()
        super().do_Q()

    def _set_clip_from_curpath(self) -> None:
        pts: List[Tuple[float, float]] = []
        for op in getattr(self, "curpath", []) or []:
            operands = [float(v) for v in op[1:]]
            for i in range(0, len(operands) - 1, 2):
                pts.append(apply_matrix(self.ctm, (operands[i], operands[i + 1])))
        box = rect_from_points(pts)
        if box is None:
            return
        current = self.device.clip
        self.device.clip = box if current is None else (current.intersection(box) or box)

    def do_W(self) -> None:
        self._set_clip_from_curpath()

    def do_W_a(self) -> None:
        self._set_clip_from_curpath()

    def do_sh(self, name) -> None:  # type: ignore[override]
        """``sh`` paints a shading over the current clip; pdfminer ignores it."""
        self.device.paint_shading("shading (sh)", self.device.clip)

    def do_gs(self, name) -> None:  # type: ignore[override]
        from pdfminer.psparser import literal_name

        try:
            egs = resolve1(dict_get(self.resources, "ExtGState"))
            entry = resolve1(dict_get(egs, literal_name(name)))
        except Exception:
            entry = None
        if not isinstance(entry, dict):
            return
        ca = entry.get("ca")
        CA = entry.get("CA")
        if isinstance(ca, (int, float)):
            self.device.alpha_fill = max(0.0, min(1.0, float(ca)))
        if isinstance(CA, (int, float)):
            self.device.alpha_stroke = max(0.0, min(1.0, float(CA)))
        lw = entry.get("LW")
        if isinstance(lw, (int, float)):
            self.graphicstate.linewidth = float(lw)
        dash = entry.get("D")
        if isinstance(dash, list) and len(dash) == 2:
            try:
                self.graphicstate.dash = ([float(v) for v in resolve1(dash[0])], float(dash[1]))
            except Exception:
                pass


def dict_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return None


# ── page walk ────────────────────────────────────────────────────────────────


def _box(values: Any, fallback: Rect) -> Rect:
    try:
        v = [float(x) for x in resolve1(values)]
        if len(v) == 4:
            return Rect(min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))
    except Exception:
        pass
    return fallback


def extract_document(
    pdf_path: str,
    pages: Optional[Sequence[int]] = None,
    password: str = "",
    substitutions: Optional[Dict[str, str]] = None,
) -> Tuple[List[RawPage], Dict[str, ImageAsset], List[str]]:
    """Extract raw paint records for the requested 0-based page indices."""
    warnings: List[str] = []
    assets: Dict[str, ImageAsset] = {}
    out: List[RawPage] = []
    wanted = set(pages) if pages is not None else None

    with open(pdf_path, "rb") as fh:
        parser = PDFParser(fh)
        try:
            doc = PDFDocument(parser, password=password)
        except PDFPasswordIncorrect as exc:
            raise PasswordRequired(
                "the PDF is encrypted and the supplied password was rejected"
            ) from exc
        except Exception as exc:
            raise UnparseableDocument(
                "the PDF structure could not be read (%s: %s)" % (type(exc).__name__, exc)
            ) from exc
        if not doc.is_extractable:
            warnings.append(
                "the PDF declares that content extraction is not permitted; "
                "converting anyway is the operator's decision"
            )
        rsrcmgr = PDFResourceManager(caching=True)
        device = _Device(rsrcmgr, assets, substitutions or {})
        interpreter = _Interpreter(rsrcmgr, device)

        try:
            page_list = list(PDFPage.create_pages(doc))
        except Exception as exc:
            raise UnparseableDocument(
                "the PDF page tree could not be read (%s: %s)" % (type(exc).__name__, exc)
            ) from exc
        if not page_list:
            raise UnparseableDocument("the PDF contains no readable pages")

        for index, page in enumerate(page_list):
            if wanted is not None and index not in wanted:
                continue
            media = _box(page.mediabox, Rect(0, 0, 612, 792))
            crop = _box(page.cropbox, media)
            raw = RawPage(
                index=index,
                media_box=media,
                crop_box=crop,
                rotation=int(page.rotate or 0) % 360,
            )
            device.page = raw
            device.page_box = crop
            device.paint_order = 0
            device.alpha_fill = 1.0
            device.alpha_stroke = 1.0
            device.clip = None
            device.form_depth = 0
            try:
                interpreter.process_page(page)
            except Exception as exc:  # a broken page must not lose the whole deck
                warnings.append(
                    "page %d: content stream failed to parse (%s); "
                    "the page falls back to a rendered image" % (index + 1, type(exc).__name__)
                )
                raw.chars.clear()
                raw.paths.clear()
                raw.images.clear()
                raw.rotation = int(page.rotate or 0) % 360
                out.append(raw)
                device.page = None
                continue
            device.page = None
            out.append(raw)
    return out, assets, warnings
