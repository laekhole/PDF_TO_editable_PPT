"""Image XObject extraction with raw-stream passthrough.

The hard rule from the conversion policy is that an independent bitmap must
never be recompressed.  A DCTDecode (JPEG) or JPXDecode (JPEG 2000) stream is
handed to PowerPoint byte for byte whenever PowerPoint can read it; everything
else is decoded once and written as *lossless* PNG.
"""

from __future__ import annotations

import hashlib
import io
import zlib
from typing import Any, Optional, Tuple

from PIL import Image

from ..ir import ImageAsset

_DCT = ("DCTDecode", "DCT")
_JPX = ("JPXDecode",)
_JBIG2 = ("JBIG2Decode",)
_CCITT = ("CCITTFaxDecode", "CCF")


def _name_of(obj: Any) -> str:
    """PSLiteral / PSKeyword / str -> a bare PDF name without the ``/`` or quotes."""
    from pdfminer.psparser import PSLiteral

    if isinstance(obj, PSLiteral):
        name = obj.name
        return name.decode("latin-1") if isinstance(name, bytes) else str(name)
    if isinstance(obj, bytes):
        return obj.decode("latin-1").lstrip("/")
    return str(obj).lstrip("/") if obj is not None else ""


def _filter_names(stream: Any) -> list[str]:
    names: list[str] = []
    try:
        for f, _params in stream.get_filters():
            names.append(_name_of(f))
    except Exception:
        pass
    return names


def _attr(stream: Any, key: str, default: Any = None) -> Any:
    try:
        from pdfminer.pdftypes import resolve1

        return resolve1(stream.attrs.get(key, default))
    except Exception:
        return stream.attrs.get(key, default) if hasattr(stream, "attrs") else default


def _colorspace_name(cs: Any) -> str:
    from pdfminer.pdftypes import resolve1

    cs = resolve1(cs)
    if isinstance(cs, list) and cs:
        return _name_of(resolve1(cs[0]))
    return _name_of(cs)


def _bits_to_mode(ncomp: int) -> str:
    return {1: "L", 3: "RGB", 4: "CMYK"}.get(ncomp, "L")


def _decode_smask(stream: Any) -> Optional[Image.Image]:
    """Decode the /SMask of an image XObject into an 8-bit L-mode mask."""
    smask = _attr(stream, "SMask")
    if smask is None:
        return None
    try:
        w = int(_attr(smask, "Width"))
        h = int(_attr(smask, "Height"))
        bpc = int(_attr(smask, "BitsPerComponent", 8) or 8)
        data = smask.get_data()
        filters = _filter_names(smask)
        if any(f in _DCT for f in filters):
            return Image.open(io.BytesIO(data)).convert("L")
        if bpc == 8:
            if len(data) < w * h:
                data = data + b"\xff" * (w * h - len(data))
            return Image.frombytes("L", (w, h), data[: w * h])
        if bpc == 1:
            img = Image.frombytes("1", (w, h), data)
            return img.convert("L")
    except Exception:
        return None
    return None


def _raw_pixels(stream: Any) -> Optional[Image.Image]:
    """Decode an uncompressed / Flate / LZW / RunLength image into a PIL image."""
    try:
        w = int(_attr(stream, "Width"))
        h = int(_attr(stream, "Height"))
    except Exception:
        return None
    bpc = _attr(stream, "BitsPerComponent", 8)
    bpc = int(bpc) if isinstance(bpc, (int, float)) else 8
    cs = _attr(stream, "ColorSpace")
    csname = _colorspace_name(cs)
    data = stream.get_data()

    if _attr(stream, "ImageMask") or csname == "":
        # A stencil mask: 1 bit per pixel, 1 == do not paint by default.
        try:
            img = Image.frombytes("1", (w, h), data)
            decode = _attr(stream, "Decode")
            if isinstance(decode, list) and decode and float(decode[0]) == 1:
                img = img.point(lambda v: 255 - v)
            return img.convert("L")
        except Exception:
            return None

    if csname == "Indexed":
        from pdfminer.pdftypes import resolve1

        arr = resolve1(cs)
        try:
            base = _colorspace_name(arr[1])
            lookup = resolve1(arr[3])
            palette = lookup.get_data() if hasattr(lookup, "get_data") else bytes(lookup)
            ncomp = {"DeviceRGB": 3, "DeviceGray": 1, "DeviceCMYK": 4}.get(base, 3)
            if ncomp != 3:
                return None
            img = Image.frombytes("P", (w, h), data)
            pal = list(palette[: 256 * 3])
            pal += [0] * (768 - len(pal))
            img.putpalette(pal)
            return img.convert("RGB")
        except Exception:
            return None

    ncomp = {"DeviceRGB": 3, "DeviceGray": 1, "DeviceCMYK": 4, "CalRGB": 3, "CalGray": 1}.get(
        csname
    )
    if ncomp is None and csname == "ICCBased":
        from pdfminer.pdftypes import resolve1

        arr = resolve1(cs)
        try:
            ncomp = int(resolve1(arr[1])["N"])
        except Exception:
            ncomp = 3
    if ncomp is None:
        return None

    if bpc != 8:
        if bpc == 1 and ncomp == 1:
            try:
                return Image.frombytes("1", (w, h), data).convert("L")
            except Exception:
                return None
        return None
    need = w * h * ncomp
    if len(data) < need:
        return None
    mode = _bits_to_mode(ncomp)
    img = Image.frombytes(mode, (w, h), data[:need])
    if mode == "CMYK":
        # PDF CMYK images are stored inverted relative to PIL's expectation
        # only when a /Decode array says so; the common case is direct.
        img = img.convert("RGB")
    return img


def extract_image(stream: Any, asset_id: str) -> Optional[ImageAsset]:
    """Build an ImageAsset from a PDF image XObject.

    Returns None when the stream cannot be turned into something PowerPoint
    can display; the caller then falls back to rasterising the region.
    """
    try:
        width = int(_attr(stream, "Width"))
        height = int(_attr(stream, "Height"))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None

    filters = _filter_names(stream)
    try:
        payload = stream.get_data()
    except Exception:
        return None
    source_sha = hashlib.sha256(payload).hexdigest()
    smask = _decode_smask(stream)

    # ── passthrough path: keep the original compressed bytes ────────────────
    if any(f in _DCT for f in filters) and smask is None:
        csname = _colorspace_name(_attr(stream, "ColorSpace"))
        if csname != "DeviceCMYK":
            return ImageAsset(
                asset_id=asset_id,
                data=payload,
                ext="jpg",
                width_px=width,
                height_px=height,
                passthrough=True,
                source_sha256=source_sha,
                output_sha256=source_sha,
                note="original JPEG stream copied without re-encoding",
            )
        # CMYK JPEGs render with inverted colours in PowerPoint often enough
        # that we convert once, losslessly, instead of shipping a wrong image.

    # ── decode path: one decode, then lossless PNG ──────────────────────────
    img: Optional[Image.Image] = None
    if any(f in _DCT for f in filters) or any(f in _JPX for f in filters):
        try:
            img = Image.open(io.BytesIO(payload))
            img.load()
        except Exception:
            img = None
    if img is None and any(f in _JBIG2 or f in _CCITT for f in filters):
        return None
    if img is None:
        img = _raw_pixels(stream)
    if img is None:
        return None

    if img.mode == "CMYK":
        img = img.convert("RGB")
    has_alpha = False
    if smask is not None:
        base = img.convert("RGB")
        if smask.size != base.size:
            smask = smask.resize(base.size, Image.LANCZOS)
        base.putalpha(smask)
        img = base
        has_alpha = True
    elif img.mode not in ("RGB", "L", "P", "RGBA", "LA", "1"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    return ImageAsset(
        asset_id=asset_id,
        data=data,
        ext="png",
        width_px=img.width,
        height_px=img.height,
        passthrough=False,
        source_sha256=source_sha,
        output_sha256=hashlib.sha256(data).hexdigest(),
        has_alpha=has_alpha,
        note=(
            "decoded once and written as lossless PNG"
            + (" with the source soft mask applied as alpha" if has_alpha else "")
        ),
    )
