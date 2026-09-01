"""Rasterisation helpers used by both the fallback path and the visual checks.

The PDF side uses PDFium (pypdfium2), which is the same engine Chrome ships,
so a "source render" here is what a reader actually shows.  The PPTX side goes
through LibreOffice headless, because no library renders PPTX faithfully in
process; when LibreOffice is absent the visual checks degrade to structural
checks and say so instead of silently passing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence

from PIL import Image

from ..units import Rect

DEFAULT_DPI = 150.0


class RenderError(RuntimeError):
    pass


def _scale_for(dpi: float) -> float:
    return dpi / 72.0


def render_pdf_page(
    pdf_path: str, page_index: int, dpi: float = DEFAULT_DPI, password: str = ""
) -> Image.Image:
    """Render one PDF page (0-based) at ``dpi`` into an RGB image."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_path, password=password or None)
    try:
        page = doc[page_index]
        bitmap = page.render(scale=_scale_for(dpi), draw_annots=True)
        return bitmap.to_pil().convert("RGB")
    finally:
        doc.close()


def render_pdf_region(
    pdf_path: str,
    page_index: int,
    region: Rect,
    page_height_pt: float,
    dpi: float = DEFAULT_DPI,
    transparent: bool = False,
    password: str = "",
) -> Image.Image:
    """Render just ``region`` (PDF points, y-up) of a page.

    Used to produce a pixel-accurate fallback for an area we refuse to rebuild
    natively; the crop is taken from a full-page render so clipping, blend
    modes and overlaps stay exactly as the source draws them.
    """
    import pypdfium2 as pdfium

    scale = _scale_for(dpi)
    doc = pdfium.PdfDocument(pdf_path, password=password or None)
    try:
        page = doc[page_index]
        bitmap = page.render(scale=scale, draw_annots=True, may_draw_forms=True)
        img = bitmap.to_pil().convert("RGBA" if transparent else "RGB")
    finally:
        doc.close()
    left = int(max(0, round(region.x0 * scale)))
    right = int(min(img.width, round(region.x1 * scale)))
    top = int(max(0, round((page_height_pt - region.y1) * scale)))
    bottom = int(min(img.height, round((page_height_pt - region.y0) * scale)))
    if right <= left:
        right = min(img.width, left + 1)
    if bottom <= top:
        bottom = min(img.height, top + 1)
    return img.crop((left, top, right, bottom))


def page_count(pdf_path: str, password: str = "") -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_path, password=password or None)
    try:
        return len(doc)
    finally:
        doc.close()


# ── PPTX rendering ───────────────────────────────────────────────────────────


def find_soffice() -> Optional[str]:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def pptx_to_pdf(pptx_path: str, out_dir: str, timeout: int = 300) -> Optional[str]:
    """Convert a .pptx to .pdf with LibreOffice headless.  None when absent."""
    soffice = find_soffice()
    if soffice is None:
        return None
    os.makedirs(out_dir, exist_ok=True)
    profile = os.path.join(out_dir, "_loprofile")
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--invisible",
        "-env:UserInstallation=file://%s" % os.path.abspath(profile),
        "--convert-to",
        "pdf",
        "--outdir",
        out_dir,
        pptx_path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False, env={**os.environ}
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    target = os.path.join(
        out_dir, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf"
    )
    if os.path.exists(target):
        return target
    if proc.returncode != 0:
        return None
    return None


def render_pptx_pages(
    pptx_path: str, dpi: float = DEFAULT_DPI, work_dir: Optional[str] = None
) -> Optional[List[Image.Image]]:
    """Render every slide.  None when no renderer is available."""
    tmp = work_dir or tempfile.mkdtemp(prefix="p2ep-render-")
    pdf = pptx_to_pdf(pptx_path, tmp)
    if pdf is None:
        return None
    out: List[Image.Image] = []
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        for i in range(len(doc)):
            out.append(doc[i].render(scale=_scale_for(dpi)).to_pil().convert("RGB"))
    finally:
        doc.close()
    return out
