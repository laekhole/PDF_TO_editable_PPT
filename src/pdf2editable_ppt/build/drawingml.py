"""DrawingML fragments.

Every function here returns a string of XML.  Fragments carry no namespace
declarations of their own (the writer wraps them when it parses them into the
slide); the one exception is :func:`table_xml`, whose result is a complete
``p:graphicFrame`` that parses on its own.

Why raw XML rather than python-pptx's shape API: the API cannot express
per-edge table borders, Bézier custom geometry, dash patterns, picture crops
or constant alpha, and those are exactly the things this converter exists to
preserve.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..ir import CellBorder, Path, SegmentOp, Style, TextContent, TextLine, TextRun
from ..units import pt_to_emu

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

NSDECL = 'xmlns:a="%s" xmlns:p="%s" xmlns:r="%s"' % (A_NS, P_NS, R_NS)

# PowerPoint's built-in "No Style, No Grid" table style: no theme borders, no
# banding, so the only borders on a cell are the ones written on it.
TABLE_STYLE_NO_GRID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"

# Preset dash patterns, in multiples of the line width (ECMA-376 20.1.10.48).
DASH_PRESETS: Dict[str, Tuple[float, ...]] = {
    "dash": (4, 3),
    "dot": (1, 3),
    "lgDash": (8, 3),
    "sysDash": (3, 1),
    "sysDot": (1, 1),
    "dashDot": (4, 3, 1, 3),
    "lgDashDot": (8, 3, 1, 3),
    "lgDashDotDot": (8, 3, 1, 3, 1, 3),
    "sysDashDot": (3, 1, 1, 1),
    "sysDashDotDot": (3, 1, 1, 1, 1, 1),
}
# A pattern is called a preset when every element is within this relative
# error of the preset's.
DASH_MATCH_TOL = 0.34

# A PDF line width of 0 means "thinnest line the device can draw"; this is
# what that comes out as on paper and on screen.
HAIRLINE_WIDTH_PT = 0.3

# PDF line cap / join numbers -> DrawingML names.
LINE_CAPS = {0: "flat", 1: "rnd", 2: "sq"}

_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")
_HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")


# ── primitives ──────────────────────────────────────────────────────────────


def esc(text: str) -> str:
    """Escape text for an XML text node and drop what XML 1.0 cannot carry."""
    text = _CONTROL_CHARS.sub("", text or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def pct(fraction: float) -> int:
    """A 0..1 fraction as DrawingML's 1000ths of a percent."""
    return int(round(fraction * 100000))


def rot_attr(rotation_deg: float) -> str:
    """``rot`` attribute for a counter-clockwise PDF angle.

    DrawingML rotates clockwise in 60000ths of a degree, PDF space rotates
    counter-clockwise, so the sign flips.
    """
    if abs(rotation_deg) < 0.005:
        return ""
    sixtieths = int(round(((-rotation_deg) % 360.0) * 60000))
    if sixtieths % 21600000 == 0:
        return ""
    return ' rot="%d"' % sixtieths


def xfrm(
    x: int,
    y: int,
    cx: int,
    cy: int,
    rotation_deg: float = 0.0,
    flip_h: bool = False,
    flip_v: bool = False,
) -> str:
    attrs = rot_attr(rotation_deg)
    if flip_h:
        attrs += ' flipH="1"'
    if flip_v:
        attrs += ' flipV="1"'
    return '<a:xfrm%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>' % (
        attrs,
        x,
        y,
        max(1, cx),
        max(1, cy),
    )


def solid_fill(color: str, alpha: float = 1.0) -> str:
    inner = ""
    if alpha < 0.999:
        inner = '<a:alpha val="%d"/>' % pct(max(0.0, alpha))
    return '<a:solidFill><a:srgbClr val="%s">%s</a:srgbClr></a:solidFill>' % (
        (color or "000000").upper(),
        inner,
    )


def _relative_dash(pattern: Optional[Sequence[float]], width_pt: float) -> List[float]:
    if not pattern:
        return []
    values = [float(v) for v in pattern if v is not None and float(v) >= 0.0]
    if not values or all(v <= 0.0 for v in values):
        return []
    unit = width_pt if width_pt > 0.0 else 1.0
    rel = [v / unit for v in values]
    if len(rel) % 2:
        rel = rel * 2  # PDF cycles an odd-length array on and off
    return rel


def dash_preset(pattern: Optional[Sequence[float]], width_pt: float) -> Optional[str]:
    """The preset name a PDF dash array is, or None when it is not one."""
    rel = _relative_dash(pattern, width_pt)
    if not rel:
        return None
    best: Optional[Tuple[str, float]] = None
    for name, preset in DASH_PRESETS.items():
        if len(preset) != len(rel):
            continue
        err = max(abs(a - b) / max(b, 1e-6) for a, b in zip(rel, preset))
        if err <= DASH_MATCH_TOL and (best is None or err < best[1]):
            best = (name, err)
    return best[0] if best else None


def dash_xml(pattern: Optional[Sequence[float]], width_pt: float) -> str:
    """A ``prstDash`` when the array is a preset, else an exact ``custDash``."""
    rel = _relative_dash(pattern, width_pt)
    if not rel:
        return ""
    preset = dash_preset(pattern, width_pt)
    if preset:
        return '<a:prstDash val="%s"/>' % preset
    stops = []
    for i in range(0, len(rel), 2):
        d = max(1, pct(rel[i]))
        sp = max(1, pct(rel[i + 1]))
        stops.append('<a:ds d="%d" sp="%d"/>' % (d, sp))
    return "<a:custDash>%s</a:custDash>" % "".join(stops)


def fill_xml(style: Style, opacity: float = 1.0) -> str:
    if style.has_fill:
        return solid_fill(style.fill_color, style.fill_alpha * opacity)
    return "<a:noFill/>"


def line_xml(style: Style, opacity: float = 1.0) -> str:
    if not style.has_stroke:
        return "<a:ln><a:noFill/></a:ln>"
    width = style.stroke_width_pt if style.stroke_width_pt > 0.0 else HAIRLINE_WIDTH_PT
    cap = LINE_CAPS.get(style.line_cap, "flat")
    parts = [solid_fill(style.stroke_color, style.stroke_alpha * opacity)]
    if style.dash:
        pattern, _phase = style.dash
        parts.append(dash_xml(pattern, width))
    if style.line_join == 1:
        parts.append("<a:round/>")
    elif style.line_join == 2:
        parts.append("<a:bevel/>")
    else:
        parts.append('<a:miter lim="800000"/>')
    return '<a:ln w="%d" cap="%s" cmpd="sng" algn="ctr">%s</a:ln>' % (
        pt_to_emu(width),
        cap,
        "".join(parts),
    )


def nv_sp_pr(shape_id: int, name: str, text_box: bool = False) -> str:
    return (
        '<p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr%s/><p:nvPr/></p:nvSpPr>'
        % (shape_id, esc(name), ' txBox="1"' if text_box else "")
    )


# ── geometry ────────────────────────────────────────────────────────────────


def preset_geometry(prst: str, adjust: Optional[float] = None) -> str:
    if prst == "roundRect" and adjust is not None:
        # roundRect's adj is the corner radius over the shorter side, in
        # 1000ths of a percent, capped at half of it.
        val = max(0, min(50000, pct(adjust)))
        return '<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst></a:prstGeom>' % val
    return '<a:prstGeom prst="%s"><a:avLst/></a:prstGeom>' % prst


def custom_geometry(
    path: Path,
    origin: Tuple[int, int],
    size: Tuple[int, int],
    to_emu_xy,
) -> str:
    """``a:custGeom`` for a path, one ``a:path`` per subpath.

    ``to_emu_xy(x, y)`` maps a visual-space point to slide EMU; ``origin`` is
    the shape's ``a:off`` so the coordinates come out local to the shape, and
    ``size`` is its ``a:ext`` so the path's own w/h match and nothing scales.
    Cubic segments keep both control points.
    """
    ox, oy = origin
    w, h = max(1, size[0]), max(1, size[1])

    def local(point: Tuple[float, float]) -> Tuple[int, int]:
        ex, ey = to_emu_xy(point[0], point[1])
        return (int(ex) - int(ox), int(ey) - int(oy))

    def pt(point: Tuple[float, float]) -> str:
        x, y = local(point)
        return '<a:pt x="%d" y="%d"/>' % (x, y)

    subpaths: List[List[str]] = []
    current: Optional[List[str]] = None
    for seg in path.segments:
        if seg.op is SegmentOp.MOVE_TO:
            current = ["<a:moveTo>%s</a:moveTo>" % pt(seg.points[0])]
            subpaths.append(current)
            continue
        if current is None:
            # a path that starts without moveTo: treat the first point as one
            if not seg.points:
                continue
            current = ["<a:moveTo>%s</a:moveTo>" % pt(seg.points[0])]
            subpaths.append(current)
            if seg.op is SegmentOp.LINE_TO:
                continue
        if seg.op is SegmentOp.LINE_TO:
            current.append("<a:lnTo>%s</a:lnTo>" % pt(seg.points[0]))
        elif seg.op is SegmentOp.CUBIC_TO:
            current.append("<a:cubicBezTo>%s</a:cubicBezTo>" % "".join(pt(p) for p in seg.points))
        elif seg.op is SegmentOp.CLOSE:
            current.append("<a:close/>")
    paths = "".join(
        '<a:path w="%d" h="%d">%s</a:path>' % (w, h, "".join(commands))
        for commands in subpaths
        if commands
    )
    return (
        "<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
        '<a:rect l="0" t="0" r="r" b="b"/>'
        "<a:pathLst>%s</a:pathLst></a:custGeom>" % paths
    )


# ── text ────────────────────────────────────────────────────────────────────


def _lang(text: str) -> str:
    return "ko-KR" if _HANGUL.search(text or "") else "en-US"


def run_xml(run: TextRun, opacity: float = 1.0) -> str:
    attrs = ' lang="%s" sz="%d"' % (_lang(run.text), max(100, int(round(run.size_pt * 100))))
    if run.bold:
        attrs += ' b="1"'
    if run.italic:
        attrs += ' i="1"'
    if abs(run.char_space_pt) > 0.005:
        attrs += ' spc="%d"' % int(round(run.char_space_pt * 100))
    face = esc(run.font_family or "Calibri")
    props = (
        "<a:rPr%s>%s"
        '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/>'
        "</a:rPr>" % (attrs, solid_fill(run.color, opacity), face, face, face)
    )
    return "<a:r>%s<a:t>%s</a:t></a:r>" % (props, esc(run.text))


def paragraph_xml(
    line: TextLine,
    align: str = "l",
    pitch_pt: Optional[float] = None,
    opacity: float = 1.0,
) -> str:
    ppr = ' algn="%s"' % (align if align in ("l", "ctr", "r", "just") else "l")
    spacing = ""
    if pitch_pt and pitch_pt > 0:
        spacing = '<a:lnSpc><a:spcPts val="%d"/></a:lnSpc>' % int(round(pitch_pt * 100))
    spacing += '<a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft>'
    runs = "".join(run_xml(r, opacity) for r in line.runs)
    size = max((r.size_pt for r in line.runs), default=10.0)
    end = '<a:endParaRPr lang="en-US" sz="%d"/>' % max(100, int(round(size * 100)))
    return "<a:p><a:pPr%s>%s</a:pPr>%s%s</a:p>" % (ppr, spacing, runs, end)


def line_pitch(content: TextContent) -> float:
    """The line pitch the text analyser sized the box for (see text.py)."""
    from ..analyze.text import AUTO_LINE_RATIO

    if content.line_spacing_pt and content.line_spacing_pt > 0:
        return content.line_spacing_pt
    first = content.lines[0] if content.lines else None
    size = max((r.size_pt for r in first.runs), default=10.0) if first else 10.0
    return AUTO_LINE_RATIO * size


def paragraphs_xml(content: Optional[TextContent], opacity: float = 1.0, pitch: bool = True) -> str:
    if content is None or not content.lines:
        return '<a:p><a:endParaRPr lang="en-US"/></a:p>'
    pitch_pt = line_pitch(content) if pitch else None
    return "".join(
        paragraph_xml(line, content.align, pitch_pt, opacity) for line in content.lines
    )


def text_body_xml(content: Optional[TextContent], opacity: float = 1.0) -> str:
    """A ``p:txBody`` that keeps the source's line breaks and sizes.

    No insets, top anchored, no wrapping and no autofit: the analyser placed
    the box so the first baseline lands on the source's, and every following
    line is pinned by an explicit line pitch, so nothing may reflow.
    """
    body = (
        '<a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t" rtlCol="0">'
        "<a:noAutofit/></a:bodyPr><a:lstStyle/>"
    )
    return "<p:txBody>%s%s</p:txBody>" % (body, paragraphs_xml(content, opacity))


def empty_text_body() -> str:
    return (
        '<p:txBody><a:bodyPr rtlCol="0" anchor="ctr"/><a:lstStyle/>'
        '<a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody>'
    )


def textbox_xml(
    shape_id: int,
    name: str,
    xfrm_xml: str,
    content: TextContent,
    opacity: float = 1.0,
) -> str:
    return (
        "<p:sp>%s<p:spPr>%s%s<a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>%s</p:sp>"
        % (
            nv_sp_pr(shape_id, name, text_box=True),
            xfrm_xml,
            preset_geometry("rect"),
            text_body_xml(content, opacity),
        )
    )


# ── shapes and pictures ─────────────────────────────────────────────────────


def shape_xml(
    shape_id: int,
    name: str,
    xfrm_xml: str,
    geometry_xml: str,
    style: Style,
    opacity: float = 1.0,
) -> str:
    """A ``p:sp`` with explicit fill and line, and an empty, editable text body."""
    return "<p:sp>%s<p:spPr>%s%s%s%s</p:spPr>%s</p:sp>" % (
        nv_sp_pr(shape_id, name),
        xfrm_xml,
        geometry_xml,
        fill_xml(style, opacity),
        line_xml(style, opacity),
        empty_text_body(),
    )


def picture_xml(
    shape_id: int,
    name: str,
    r_id: str,
    xfrm_xml: str,
    crop: Optional[Tuple[float, float, float, float]] = None,
    alpha: float = 1.0,
) -> str:
    blip_inner = ""
    if alpha < 0.999:
        blip_inner = '<a:alphaModFix amt="%d"/>' % pct(max(0.0, alpha))
    src_rect = ""
    if crop and any(abs(v) > 1e-6 for v in crop):
        left, top, right, bottom = crop
        src_rect = '<a:srcRect l="%d" t="%d" r="%d" b="%d"/>' % (
            pct(max(0.0, left)),
            pct(max(0.0, top)),
            pct(max(0.0, right)),
            pct(max(0.0, bottom)),
        )
    return (
        "<p:pic>"
        '<p:nvPicPr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="0"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="%s">%s</a:blip>%s<a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        "<p:spPr>%s%s</p:spPr>"
        "</p:pic>"
        % (shape_id, esc(name), r_id, blip_inner, src_rect, xfrm_xml, preset_geometry("rect"))
    )


# ── tables ──────────────────────────────────────────────────────────────────


def cell_text_body(content: Optional[TextContent], opacity: float = 1.0) -> str:
    """An ``a:txBody`` for a table cell (cells use the DrawingML flavour)."""
    return "<a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody>" % paragraphs_xml(
        content, opacity, pitch=bool(content and content.line_spacing_pt)
    )


def _edge_xml(tag: str, border: Optional[CellBorder]) -> str:
    if border is None or not border.present:
        return '<a:%s w="12700" cap="flat" cmpd="sng" algn="ctr"><a:noFill/></a:%s>' % (tag, tag)
    width = border.width_pt if border.width_pt > 0 else HAIRLINE_WIDTH_PT
    dash = '<a:prstDash val="%s"/>' % border.dash if border.dash else '<a:prstDash val="solid"/>'
    return '<a:%s w="%d" cap="flat" cmpd="sng" algn="ctr">%s%s</a:%s>' % (
        tag,
        pt_to_emu(width),
        solid_fill(border.color),
        dash,
        tag,
    )


def table_cell_xml(
    body_xml: str,
    borders: Dict[str, CellBorder],
    fill_color: Optional[str],
    fill_alpha: float,
    v_align: str,
    margins_pt: Sequence[float],
    h_merge: bool = False,
    v_merge: bool = False,
    grid_span: int = 1,
    row_span: int = 1,
) -> str:
    """One ``a:tc``: its text, then every edge stated explicitly, then its fill."""
    attrs = ""
    if grid_span > 1:
        attrs += ' gridSpan="%d"' % grid_span
    if row_span > 1:
        attrs += ' rowSpan="%d"' % row_span
    if h_merge:
        attrs += ' hMerge="1"'
    if v_merge:
        attrs += ' vMerge="1"'
    left, top, right, bottom = (list(margins_pt) + [0, 0, 0, 0])[:4]
    anchor = v_align if v_align in ("t", "ctr", "b") else "t"
    edges = "".join(
        _edge_xml(tag, borders.get(key))
        for key, tag in (("l", "lnL"), ("r", "lnR"), ("t", "lnT"), ("b", "lnB"))
    )
    fill = solid_fill(fill_color, fill_alpha) if fill_color else "<a:noFill/>"
    tc_pr = '<a:tcPr marL="%d" marR="%d" marT="%d" marB="%d" anchor="%s">%s%s</a:tcPr>' % (
        pt_to_emu(max(0.0, left)),
        pt_to_emu(max(0.0, right)),
        pt_to_emu(max(0.0, top)),
        pt_to_emu(max(0.0, bottom)),
        anchor,
        edges,
        fill,
    )
    return "<a:tc%s>%s%s</a:tc>" % (attrs, body_xml, tc_pr)


def table_xml(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    col_widths_emu: Sequence[int],
    row_heights_emu: Sequence[int],
    rows_xml: Sequence[str],
) -> str:
    """A complete ``p:graphicFrame`` holding a native table.

    ``rows_xml`` holds one string per row: the concatenated ``a:tc`` cells of
    that row.  The frame declares its namespaces so it parses on its own.
    """
    grid = "".join('<a:gridCol w="%d"/>' % max(1, int(w)) for w in col_widths_emu)
    rows = "".join(
        '<a:tr h="%d">%s</a:tr>' % (max(1, int(h)), cells)
        for h, cells in zip(row_heights_emu, rows_xml)
    )
    return (
        "<p:graphicFrame %s>"
        '<p:nvGraphicFramePr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>'
        "<p:nvPr/></p:nvGraphicFramePr>"
        '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        '<a:tbl><a:tblPr firstRow="0" bandRow="0"><a:tableStyleId>%s</a:tableStyleId></a:tblPr>'
        "<a:tblGrid>%s</a:tblGrid>%s</a:tbl>"
        "</a:graphicData></a:graphic></p:graphicFrame>"
        % (NSDECL, shape_id, esc(name), x, y, max(1, cx), max(1, cy), TABLE_STYLE_NO_GRID, grid, rows)
    )


def background_xml(color: str) -> str:
    return "<p:bg><p:bgPr>%s<a:effectLst/></p:bgPr></p:bg>" % solid_fill(color)
