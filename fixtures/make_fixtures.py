#!/usr/bin/env python3
"""Generate the synthetic PDF corpus.

Every fixture is built from a script so the ground truth is known exactly:
the tests assert against the values written here, not against a golden file
someone eyeballed once.  Run with::

    python fixtures/make_fixtures.py [outdir]

Requires reportlab (an extra of the ``dev`` install) and, for the Korean
fixtures, a Korean TrueType font on the system (NanumGothic or Noto Sans CJK).
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from PIL import Image
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, LETTER, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))

KOREAN_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "NanumGothic"),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "NanumGothic-Bold"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJK"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJK"),
]

KOREAN_REGULAR = "Helvetica"
KOREAN_BOLD = "Helvetica-Bold"


def register_fonts() -> Tuple[str, str]:
    """Register a Korean-capable font pair; fall back to the base 14."""
    global KOREAN_REGULAR, KOREAN_BOLD
    regular = bold = None
    for path, name in KOREAN_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except Exception:
            continue
        if "Bold" in name and bold is None:
            bold = name
        elif regular is None:
            regular = name
    if regular:
        KOREAN_REGULAR = regular
        KOREAN_BOLD = bold or regular
    return KOREAN_REGULAR, KOREAN_BOLD


# ── shared assets ────────────────────────────────────────────────────────────


def make_photo(path: str, size=(240, 160)) -> str:
    """A JPEG with smooth gradients (compresses well, decodes identically)."""
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (int(255 * x / w), int(255 * y / h), 160)
    img.save(path, "JPEG", quality=92, subsampling=0)
    return path


def make_logo(path: str, size=(160, 160)) -> str:
    """A PNG with a real alpha channel (becomes an /SMask in the PDF)."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    px = img.load()
    cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 4
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d <= r:
                px[x, y] = (230, 60, 90, 255 if d < r - 2 else 128)
    img.save(path, "PNG")
    return path


# ── fixtures ─────────────────────────────────────────────────────────────────


def fx_text_mixed(path: str) -> dict:
    """Korean + Latin, several runs in one line, bullets, rotated text."""
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("text-mixed")

    c.setFont(KOREAN_BOLD, 22)
    c.setFillColorRGB(0.05, 0.12, 0.42)
    c.drawString(56, 720, "2026 회의 자료 Meeting Deck")

    # one visual line, four style runs
    x = 56.0
    y = 680.0
    runs = [
        ("프로젝트 ", KOREAN_REGULAR, 13, (0.1, 0.1, 0.1), False),
        ("Alpha", KOREAN_BOLD, 13, (0.80, 0.15, 0.10), True),
        (" 진행률 ", KOREAN_REGULAR, 13, (0.1, 0.1, 0.1), False),
        ("87%", KOREAN_BOLD, 17, (0.05, 0.45, 0.20), True),
    ]
    for text, font, size, rgb, _bold in runs:
        c.setFont(font, size)
        c.setFillColorRGB(*rgb)
        c.drawString(x, y, text)
        x += c.stringWidth(text, font, size)

    bullets = [
        "• 첫 번째 항목: Q1 산출물 정리",
        "• Second item: milestone review",
        "• 세 번째 항목 — risk register update",
    ]
    c.setFont(KOREAN_REGULAR, 12)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    for i, line in enumerate(bullets):
        c.drawString(64, 640 - i * 19, line)

    # a centred paragraph of several lines
    c.setFont(KOREAN_REGULAR, 11)
    c.setFillColorRGB(0.25, 0.25, 0.3)
    para = [
        "이 문단은 여러 줄로 구성되어 있으며",
        "가운데 정렬되어 있습니다.",
        "It also mixes Latin script in the same block.",
    ]
    for i, line in enumerate(para):
        c.drawCentredString(306, 560 - i * 16, line)

    # right-aligned block
    c.setFont(KOREAN_REGULAR, 10)
    for i, line in enumerate(["Prepared by: Planning", "Revision 3", "2026-09-01"]):
        c.drawRightString(556, 500 - i * 13, line)

    # rotated text: 90 and 45 degrees
    c.saveState()
    c.translate(80, 300)
    c.rotate(90)
    c.setFont(KOREAN_BOLD, 14)
    c.setFillColorRGB(0.4, 0.1, 0.5)
    c.drawString(0, 0, "세로 회전 텍스트 90")
    c.restoreState()

    c.saveState()
    c.translate(330, 300)
    c.rotate(45)
    c.setFont(KOREAN_REGULAR, 13)
    c.setFillColorRGB(0.1, 0.35, 0.55)
    c.drawString(0, 0, "diagonal 45 대각선")
    c.restoreState()

    c.save()
    return {
        "expect_text": [
            "Meeting Deck",
            "87%",
            "milestone review",
            "세로 회전 텍스트 90",
        ],
        "expect_rotations": [0, 90, 45],
    }


def fx_shapes(path: str) -> dict:
    """Every shape family plus overlap, alpha, dash and stroke weights."""
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("shapes")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 740, "Shape matrix")

    # row 1: straight primitives
    c.setStrokeColorRGB(0.1, 0.1, 0.1)
    c.setLineWidth(1)
    c.line(56, 700, 200, 700)                       # horizontal hairline
    c.setLineWidth(4)
    c.setStrokeColorRGB(0.85, 0.2, 0.2)
    c.line(220, 690, 340, 715)                      # thick diagonal
    c.setDash(8, 4)
    c.setLineWidth(2)
    c.setStrokeColorRGB(0.1, 0.5, 0.2)
    c.line(360, 690, 540, 715)                      # dashed diagonal
    c.setDash()

    # row 2: closed primitives
    c.setLineWidth(2)
    c.setStrokeColorRGB(0.2, 0.2, 0.6)
    c.setFillColorRGB(0.95, 0.85, 0.35)
    c.rect(56, 590, 120, 70, stroke=1, fill=1)
    c.setFillColorRGB(0.55, 0.82, 0.95)
    c.roundRect(200, 590, 120, 70, 14, stroke=1, fill=1)
    c.setFillColorRGB(0.95, 0.55, 0.75)
    c.ellipse(344, 590, 464, 660, stroke=1, fill=1)
    c.setFillColorRGB(0.6, 0.9, 0.6)
    c.circle(520, 625, 35, stroke=1, fill=1)

    # row 3: freeform polygon + bezier
    p = c.beginPath()
    p.moveTo(56, 470)
    p.lineTo(120, 545)
    p.lineTo(190, 500)
    p.lineTo(160, 430)
    p.lineTo(80, 425)
    p.close()
    c.setFillColorRGB(0.55, 0.45, 0.85)
    c.setStrokeColorRGB(0.2, 0.1, 0.4)
    c.drawPath(p, stroke=1, fill=1)

    p2 = c.beginPath()
    p2.moveTo(230, 440)
    p2.curveTo(270, 545, 350, 380, 400, 470)
    p2.curveTo(430, 520, 470, 430, 520, 500)
    c.setStrokeColorRGB(0.9, 0.35, 0.05)
    c.setLineWidth(3)
    c.drawPath(p2, stroke=1, fill=0)

    # row 4: overlap and alpha (z-order matters)
    c.setFillColorRGB(0.15, 0.35, 0.75)
    c.rect(56, 280, 160, 100, stroke=0, fill=1)
    c.setFillColor(Color(0.95, 0.75, 0.1, alpha=0.55))
    c.rect(130, 320, 160, 100, stroke=0, fill=1)
    c.setFillColor(Color(0.9, 0.2, 0.3, alpha=0.45))
    c.circle(250, 330, 60, stroke=0, fill=1)

    # row 5: stroke-only shapes at several weights
    c.setFillColorRGB(1, 1, 1)
    for i, w in enumerate((0.25, 0.75, 1.5, 3.0, 6.0)):
        c.setLineWidth(w)
        c.setStrokeColorRGB(0.1, 0.1, 0.1)
        c.rect(340 + i * 44, 300, 34, 60, stroke=1, fill=0)

    c.save()
    return {"expect_min_shapes": 18}


def fx_images(path: str, photo: str, logo: str) -> dict:
    """Independent bitmaps: plain, scaled, rotated, cropped, alpha, repeated."""
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("images")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 740, "Image handling")

    c.drawImage(photo, 56, 600, width=180, height=120)                 # 1:1 aspect
    c.drawImage(photo, 270, 600, width=90, height=120)                 # stretched
    c.drawImage(logo, 400, 600, width=120, height=120, mask="auto")    # alpha PNG

    # rotated placement
    c.saveState()
    c.translate(150, 470)
    c.rotate(30)
    c.drawImage(photo, -90, -60, width=180, height=120)
    c.restoreState()

    # clipped (crop) placement
    c.saveState()
    p = c.beginPath()
    p.rect(320, 400, 110, 80)
    c.clipPath(p, stroke=0, fill=0)
    c.drawImage(photo, 300, 380, width=180, height=120)
    c.restoreState()

    # the same bitmap again: must share one media part
    c.drawImage(photo, 56, 250, width=120, height=80)
    c.save()
    return {"expect_images": 6, "expect_unique_assets": 2}


def _table_grid(
    c: canvas.Canvas,
    x: float,
    y_top: float,
    col_w: List[float],
    row_h: List[float],
    merges: List[Tuple[int, int, int, int]],
    fills,
    texts,
    border_color=(0.25, 0.25, 0.3),
    border_width=1.0,
) -> None:
    """Draw a ruled table, skipping the rules that a merge swallows."""
    xs = [x]
    for w in col_w:
        xs.append(xs[-1] + w)
    ys = [y_top]
    for h in row_h:
        ys.append(ys[-1] - h)
    covered = {}
    for r, col, rs, cs in merges:
        for rr in range(r, r + rs):
            for cc in range(col, col + cs):
                covered[(rr, cc)] = (r, col)

    for (r, col), color in fills.items():
        anchor = covered.get((r, col), (r, col))
        if anchor != (r, col):
            continue
        rs, cs = 1, 1
        for mr, mc, mrs, mcs in merges:
            if (mr, mc) == (r, col):
                rs, cs = mrs, mcs
        c.setFillColorRGB(*color)
        c.rect(xs[col], ys[r + rs], xs[col + cs] - xs[col], ys[r] - ys[r + rs], stroke=0, fill=1)

    c.setStrokeColorRGB(*border_color)
    c.setLineWidth(border_width)
    n_rows, n_cols = len(row_h), len(col_w)
    for r in range(n_rows + 1):
        for col in range(n_cols):
            if 0 < r < n_rows:
                above = covered.get((r - 1, col))
                below = covered.get((r, col))
                if above is not None and above == below:
                    continue
            c.line(xs[col], ys[r], xs[col + 1], ys[r])
    for col in range(n_cols + 1):
        for r in range(n_rows):
            if 0 < col < n_cols:
                left = covered.get((r, col - 1))
                right = covered.get((r, col))
                if left is not None and left == right:
                    continue
            c.line(xs[col], ys[r], xs[col], ys[r + 1])

    for (r, col), (text, font, size, rgb, align) in texts.items():
        anchor = covered.get((r, col), (r, col))
        if anchor != (r, col):
            continue
        rs, cs = 1, 1
        for mr, mc, mrs, mcs in merges:
            if (mr, mc) == (r, col):
                rs, cs = mrs, mcs
        c.setFont(font, size)
        c.setFillColorRGB(*rgb)
        cx0, cx1 = xs[col], xs[col + cs]
        cy0, cy1 = ys[r + rs], ys[r]
        baseline = (cy0 + cy1) / 2 - size * 0.34
        if align == "c":
            c.drawCentredString((cx0 + cx1) / 2, baseline, text)
        elif align == "r":
            c.drawRightString(cx1 - 6, baseline, text)
        else:
            c.drawString(cx0 + 6, baseline, text)


def fx_table_lattice(path: str) -> dict:
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("table-lattice")
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(56, 740, "Quarterly summary")

    col_w = [120.0, 90.0, 90.0, 120.0]
    row_h = [26.0, 24.0, 24.0, 24.0, 26.0]
    merges = [(0, 1, 1, 2), (2, 0, 2, 1)]
    fills = {
        (0, 0): (0.18, 0.26, 0.44),
        (0, 1): (0.18, 0.26, 0.44),
        (0, 3): (0.18, 0.26, 0.44),
        (1, 0): (0.90, 0.92, 0.96),
        (1, 1): (0.90, 0.92, 0.96),
        (1, 2): (0.90, 0.92, 0.96),
        (1, 3): (0.90, 0.92, 0.96),
        (4, 0): (0.96, 0.90, 0.72),
        (4, 1): (0.96, 0.90, 0.72),
        (4, 2): (0.96, 0.90, 0.72),
        (4, 3): (0.96, 0.90, 0.72),
    }
    W = (1, 1, 1)
    D = (0.1, 0.1, 0.15)
    texts = {
        (0, 0): ("Region", "Helvetica-Bold", 11, W, "l"),
        (0, 1): ("Revenue", "Helvetica-Bold", 11, W, "c"),
        (0, 3): ("Owner", "Helvetica-Bold", 11, W, "l"),
        (1, 0): ("", "Helvetica", 10, D, "l"),
        (1, 1): ("Q1", "Helvetica-Bold", 10, D, "c"),
        (1, 2): ("Q2", "Helvetica-Bold", 10, D, "c"),
        (1, 3): ("", "Helvetica", 10, D, "l"),
        (2, 0): ("APAC", "Helvetica-Bold", 11, D, "l"),
        (2, 1): ("1,240", "Helvetica", 10, D, "r"),
        (2, 2): ("1,455", "Helvetica", 10, D, "r"),
        (2, 3): ("H. Park", "Helvetica", 10, D, "l"),
        (3, 1): ("980", "Helvetica", 10, D, "r"),
        (3, 2): ("1,102", "Helvetica", 10, D, "r"),
        (3, 3): ("J. Lee", "Helvetica", 10, D, "l"),
        (4, 0): ("Total", "Helvetica-Bold", 11, D, "l"),
        (4, 1): ("2,220", "Helvetica-Bold", 10, D, "r"),
        (4, 2): ("2,557", "Helvetica-Bold", 10, D, "r"),
        (4, 3): ("", "Helvetica", 10, D, "l"),
    }
    _table_grid(c, 56, 700, col_w, row_h, merges, fills, texts)
    c.save()
    return {
        "rows": len(row_h),
        "cols": len(col_w),
        "merges": merges,
        "expect_tables": 1,
    }


def fx_table_borderless(path: str) -> dict:
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("table-borderless")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 740, "Borderless layout table")
    rows = [
        ("Item", "Owner", "Status"),
        ("Design review", "H. Park", "Done"),
        ("Data migration", "J. Lee", "In progress"),
        ("Launch checklist", "S. Kim", "Blocked"),
    ]
    for r, row in enumerate(rows):
        c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 11)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        for col, (text, x) in enumerate(zip(row, (56, 220, 380))):
            c.drawString(x, 700 - r * 26, text)
    c.save()
    return {"expect_tables": 0}


def fx_chart(path: str) -> dict:
    """A hand-drawn bar/line chart: axes, ticks, bars, a curve, labels."""
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("chart")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 740, "Monthly throughput")

    x0, y0, w, h = 80.0, 420.0, 440.0, 250.0
    c.setStrokeColorRGB(0.3, 0.3, 0.35)
    c.setLineWidth(1)
    c.line(x0, y0, x0, y0 + h)
    c.line(x0, y0, x0 + w, y0)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.3, 0.3, 0.35)
    for i in range(6):
        y = y0 + h * i / 5
        c.setStrokeColorRGB(0.88, 0.88, 0.9)
        c.line(x0, y, x0 + w, y)
        c.setStrokeColorRGB(0.3, 0.3, 0.35)
        c.line(x0 - 4, y, x0, y)
        c.drawRightString(x0 - 7, y - 3, str(i * 20))

    values = [34, 58, 47, 72, 65, 88, 79, 94]
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]
    bar_w = w / (len(values) * 1.6)
    for i, v in enumerate(values):
        bx = x0 + 12 + i * (w - 24) / len(values)
        bh = h * v / 100.0
        c.setFillColorRGB(0.20, 0.45, 0.78)
        c.rect(bx, y0, bar_w, bh, stroke=0, fill=1)
        c.setFillColorRGB(0.25, 0.25, 0.3)
        c.setFont("Helvetica", 8)
        c.drawCentredString(bx + bar_w / 2, y0 - 12, labels[i])

    path_obj = c.beginPath()
    pts = []
    for i, v in enumerate(values):
        bx = x0 + 12 + i * (w - 24) / len(values) + bar_w / 2
        pts.append((bx, y0 + h * (v + 6) / 100.0))
    path_obj.moveTo(*pts[0])
    for i in range(1, len(pts)):
        px, py = pts[i - 1]
        qx, qy = pts[i]
        path_obj.curveTo(px + (qx - px) / 2, py, px + (qx - px) / 2, qy, qx, qy)
    c.setStrokeColorRGB(0.90, 0.40, 0.10)
    c.setLineWidth(2.5)
    c.drawPath(path_obj, stroke=1, fill=0)
    for px, py in pts:
        c.setFillColorRGB(0.90, 0.40, 0.10)
        c.circle(px, py, 3.2, stroke=0, fill=1)

    c.setFillColorRGB(0.20, 0.45, 0.78)
    c.rect(360, 700, 12, 8, stroke=0, fill=1)
    c.setFillColorRGB(0.2, 0.2, 0.25)
    c.setFont("Helvetica", 9)
    c.drawString(378, 700, "Completed")
    c.setStrokeColorRGB(0.90, 0.40, 0.10)
    c.setLineWidth(2.5)
    c.line(450, 704, 468, 704)
    c.drawString(474, 700, "Trend")
    c.save()
    return {"expect_min_shapes": 30}


def fx_clip_gradient(path: str) -> dict:
    """Clipping plus an axial shading -- both are known fallback triggers."""
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("clip-gradient")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 740, "Clipping and gradients")

    c.saveState()
    p = c.beginPath()
    p.circle(170, 600, 80)
    c.clipPath(p, stroke=0, fill=0)
    c.setFillColorRGB(0.2, 0.5, 0.85)
    c.rect(80, 520, 180, 160, stroke=0, fill=1)
    c.setFillColorRGB(0.95, 0.75, 0.2)
    c.rect(170, 520, 180, 160, stroke=0, fill=1)
    c.restoreState()

    c.saveState()
    gclip = c.beginPath()
    gclip.rect(330, 520, 210, 160)
    c.clipPath(gclip, stroke=0, fill=0)
    c.linearGradient(330, 520, 540, 680, (HexColor("#1b3a93"), HexColor("#e0431f")))
    c.restoreState()

    c.saveState()
    p2 = c.beginPath()
    p2.moveTo(90, 340)
    p2.lineTo(250, 460)
    p2.lineTo(400, 330)
    p2.close()
    c.clipPath(p2, stroke=0, fill=0)
    c.setFillColorRGB(0.15, 0.65, 0.45)
    c.rect(60, 300, 400, 200, stroke=0, fill=1)
    c.restoreState()
    c.save()
    return {"expect_fallbacks": True}


def fx_scanned(path: str, tmp_png: str) -> dict:
    """One full-page bitmap that happens to contain text-shaped ink."""
    w, h = 1275, 1650  # 150 dpi letter
    img = Image.new("RGB", (w, h), (250, 248, 242))
    px = img.load()
    for y in range(h):
        for x in range(0, w, 7):
            if (x // 7 + y // 90) % 11 == 0 and 120 < y % 900 < 760:
                for dx in range(4):
                    if x + dx < w:
                        px[x + dx, y] = (60, 58, 56)
    # a slight page skew and speckle, as a scan would have
    img = img.rotate(0.4, resample=Image.BICUBIC, fillcolor=(250, 248, 242))
    img.save(tmp_png, "PNG")
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("scanned")
    c.drawImage(tmp_png, 0, 0, width=612, height=792)
    c.save()
    return {"expect_scanned": True}


def fx_rotated_pages(path: str) -> dict:
    """Pages carrying /Rotate 90 and 270.

    reportlab's ``setPageRotation`` also rotates the content stream, which is
    not what a scanner or a "rotate page" command produces.  We draw plain
    upright pages and set /Rotate afterwards with PDFium, so the fixture has
    upright content plus a page rotation -- the real-world case.
    """
    import pypdfium2 as pdfium

    tmp = path + ".upright.pdf"
    c = canvas.Canvas(tmp, pagesize=LETTER)
    c.setTitle("rotated-pages")
    for angle in (90, 270):
        c.setFont("Helvetica-Bold", 20)
        c.setFillColorRGB(0.1, 0.2, 0.5)
        c.drawString(72, 700, "Rotate %d" % angle)
        c.setFillColorRGB(0.9, 0.6, 0.1)
        c.rect(72, 560, 200, 100, stroke=0, fill=1)
        c.setStrokeColorRGB(0.2, 0.6, 0.3)
        c.setLineWidth(3)
        c.line(72, 520, 400, 540)
        c.setFillColorRGB(0.2, 0.3, 0.7)
        c.circle(450, 300, 60, stroke=0, fill=1)
        c.showPage()
    c.save()

    doc = pdfium.PdfDocument(tmp, autoclose=True)
    for index, angle in enumerate((90, 270)):
        doc[index].set_rotation(angle)
    doc.save(path)
    doc.close()
    os.remove(tmp)
    return {"pages": 2, "rotations": [90, 270]}


def fx_mixed_sizes(path: str) -> dict:
    """A4 portrait, then Letter landscape, then a small custom page."""
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle("mixed-sizes")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(56, 760, "A4 portrait")
    c.setFillColorRGB(0.3, 0.5, 0.9)
    c.rect(56, 600, 200, 100, stroke=0, fill=1)
    c.showPage()

    c.setPageSize(landscape(LETTER))
    c.setFont("Helvetica-Bold", 18)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(56, 540, "Letter landscape")
    c.setFillColorRGB(0.9, 0.4, 0.3)
    c.ellipse(300, 300, 500, 420, stroke=0, fill=1)
    c.showPage()

    c.setPageSize((300, 400))
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(24, 360, "Small 300x400")
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.rect(24, 100, 250, 200, stroke=1, fill=0)
    c.showPage()
    c.save()
    return {"pages": 3}


def fx_encrypted(path: str) -> dict:
    from reportlab.lib import pdfencrypt

    enc = pdfencrypt.StandardEncryption("secret", canPrint=1)
    c = canvas.Canvas(path, pagesize=LETTER, encrypt=enc)
    c.setTitle("encrypted")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 700, "Encrypted fixture")
    c.save()
    return {"password": "secret"}


def fx_corrupt(path: str, source: str) -> dict:
    """A truncated PDF: the loader must fail cleanly, not crash."""
    with open(source, "rb") as fh:
        data = fh.read()
    with open(path, "wb") as fh:
        fh.write(data[: int(len(data) * 0.55)])
    return {"corrupt": True}


def fx_damaged_page(path: str) -> dict:
    """Valid structure, one unreadable content stream.

    The file opens and its page tree is fine, but the last page's compressed
    content stream is scrambled in place -- same byte length, so every xref
    offset still resolves.  The converter must keep page 1 native and fall back
    to a render for page 2 rather than losing the whole document.
    """
    import re

    tmp = path + ".tmp"
    c = canvas.Canvas(tmp, pagesize=LETTER)
    c.setTitle("damaged-page")
    for page_no in (1, 2):
        c.setFont("Helvetica-Bold", 18)
        c.setFillColorRGB(0.1, 0.2, 0.5)
        c.drawString(72, 700, "Damaged fixture, page %d" % page_no)
        c.setFillColorRGB(0.2, 0.6, 0.9)
        c.rect(72, 520, 220, 120, stroke=0, fill=1)
        c.showPage()
    c.save()

    with open(tmp, "rb") as fh:
        raw = fh.read()
    os.remove(tmp)

    blocks = []
    for m in re.finditer(rb"stream\r?\n", raw):
        begin = m.end()
        stop = raw.find(b"endstream", begin)
        if stop < 0 or stop - begin < 80:
            continue
        if b"endobj" in raw[begin:stop]:
            continue
        blocks.append((begin, stop))
    if not blocks:
        raise RuntimeError("no page content stream found to damage")

    begin, stop = blocks[-1]
    data = bytearray(raw)
    middle = begin + (stop - begin) // 3
    # ASCII85-legal filler: the stream still decodes as ASCII85 but the Flate
    # payload underneath is nonsense, which is what a damaged file looks like.
    filler = b"z!<~Ru9%kQ$?bT+CO@8/hSa2E-#]Gp&Vd*Xn7Ym"
    span = min(len(filler), stop - middle)
    data[middle : middle + span] = filler[:span]
    with open(path, "wb") as fh:
        fh.write(bytes(data))
    return {"pages": 2, "damaged_page": 2}


def fx_dense_vector(path: str) -> dict:
    """Well past the per-slide shape budget: must trigger the region fallback."""
    import math

    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("dense-vector")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(56, 750, "Dense vector artwork")
    for i in range(1200):
        a = i * 0.11
        r = 16 + i * 0.16
        x = 306 + r * math.cos(a)
        y = 400 + r * math.sin(a)
        c.setFillColorRGB(0.4 + 0.5 * math.sin(a), 0.35, 0.8 - 0.4 * math.cos(a))
        c.circle(x, y, 2.4, stroke=0, fill=1)
    c.save()
    return {"expect_budget_fallback": True}


SCANNED_KOREAN_TEXT = [
    "고속도로 유휴부지 태양광 발전사업 안전관리 계획",
    "한국도로공사가 보유한 성토부의 가용자산을 활용하여",
    "신재생에너지 생산 및 전기 보급에 기여한다.",
    "사업방식은 BOT(Build-Operate-Transfer)이며 설치용량은 10MW,",
    "건설기간은 실시협약 체결 후 18개월, 운영기간은 20년이다.",
    "Safety first: no work without a permit.",
    "구분 사고유형 대책방안",
    "풍수해 산사태 우수로 인한 토사 유출 및 사면 붕괴 배수로 정비 및 사면 보강",
    "낙뢰 낙뢰로 인한 설비 손상 피뢰침 설치 및 접지 점검",
    "화재 산불 연소 확산 소화기 비치 및 방화선 확보",
]


def fx_scanned_korean(path: str) -> dict:
    """A photographed Korean page with known ground truth, for OCR scoring.

    The text is typeset, rendered at 200 dpi, JPEG-compressed at a realistic
    quality, slightly rotated and noised the way a phone scan is, and wrapped
    back into a PDF as one bitmap per page.  The exact text is saved beside
    it so an OCR engine's character error rate can be measured, not eyeballed.
    """
    import io
    import random

    import pypdfium2 as pdfium

    typeset = path + ".typeset.pdf"
    c = canvas.Canvas(typeset, pagesize=LETTER)
    c.setTitle("scanned-korean")
    y = 720.0
    for i, line in enumerate(SCANNED_KOREAN_TEXT):
        if i == 0:
            c.setFont(KOREAN_BOLD, 18)
            c.drawString(56, y, line)
            y -= 34
            continue
        if i == 6:
            y -= 12
            c.setStrokeColorRGB(0.3, 0.3, 0.3)
            c.setLineWidth(0.8)
            c.line(56, y + 14, 556, y + 14)
        c.setFont(KOREAN_BOLD if i == 6 else KOREAN_REGULAR, 12)
        c.drawString(56, y, line)
        y -= 22
    c.save()

    doc = pdfium.PdfDocument(typeset)
    page_img = doc[0].render(scale=200 / 72.0).to_pil().convert("RGB")
    doc.close()
    os.remove(typeset)

    rng = random.Random(7)
    px = page_img.load()
    w, h = page_img.size
    for _ in range(w * h // 400):
        x, y_ = rng.randrange(w), rng.randrange(h)
        r, g, b = px[x, y_]
        d = rng.randint(-18, 18)
        px[x, y_] = (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)))
    page_img = page_img.rotate(0.6, resample=Image.BICUBIC, fillcolor=(248, 247, 244))
    buf = io.BytesIO()
    page_img.save(buf, "JPEG", quality=80)
    jpg = path + ".page.jpg"
    with open(jpg, "wb") as fh:
        fh.write(buf.getvalue())

    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("scanned-korean")
    c.drawImage(jpg, 0, 0, width=612, height=792)
    c.save()
    os.remove(jpg)
    with open(path[:-4] + ".truth.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(SCANNED_KOREAN_TEXT) + "\n")
    return {"expect_scanned": True, "truth": SCANNED_KOREAN_TEXT}


# ── driver ───────────────────────────────────────────────────────────────────


def build_all(outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    register_fonts()
    photo = make_photo(os.path.join(outdir, "_photo.jpg"))
    logo = make_logo(os.path.join(outdir, "_logo.png"))
    scan_png = os.path.join(outdir, "_scan.png")

    meta = {}
    meta["text_mixed"] = fx_text_mixed(os.path.join(outdir, "text_mixed.pdf"))
    meta["shapes"] = fx_shapes(os.path.join(outdir, "shapes.pdf"))
    meta["images"] = fx_images(os.path.join(outdir, "images.pdf"), photo, logo)
    meta["table_lattice"] = fx_table_lattice(os.path.join(outdir, "table_lattice.pdf"))
    meta["table_borderless"] = fx_table_borderless(
        os.path.join(outdir, "table_borderless.pdf")
    )
    meta["chart"] = fx_chart(os.path.join(outdir, "chart.pdf"))
    meta["clip_gradient"] = fx_clip_gradient(os.path.join(outdir, "clip_gradient.pdf"))
    meta["scanned"] = fx_scanned(os.path.join(outdir, "scanned.pdf"), scan_png)
    meta["scanned_korean"] = fx_scanned_korean(os.path.join(outdir, "scanned_korean.pdf"))
    meta["rotated_pages"] = fx_rotated_pages(os.path.join(outdir, "rotated_pages.pdf"))
    meta["mixed_sizes"] = fx_mixed_sizes(os.path.join(outdir, "mixed_sizes.pdf"))
    meta["encrypted"] = fx_encrypted(os.path.join(outdir, "encrypted.pdf"))
    meta["dense_vector"] = fx_dense_vector(os.path.join(outdir, "dense_vector.pdf"))
    meta["damaged_page"] = fx_damaged_page(os.path.join(outdir, "damaged_page.pdf"))
    meta["corrupt"] = fx_corrupt(
        os.path.join(outdir, "corrupt.pdf"), os.path.join(outdir, "shapes.pdf")
    )
    for tmp in (photo, logo, scan_png):
        pass  # keep the source bitmaps: the tests hash them
    return meta


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "pdf")
    info = build_all(target)
    print("wrote %d fixtures to %s" % (len(info), target))
    for name in sorted(info):
        print("  %s" % name)
