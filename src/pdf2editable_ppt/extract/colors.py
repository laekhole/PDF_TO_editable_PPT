"""PDF colour operands -> sRGB hex.

pdfminer hands back the raw operands of ``sc``/``scn``/``g``/``rg``/``k``
together with the colour space that was current.  We convert to sRGB and keep
the number of components as the only signal for untyped spaces.
"""

from __future__ import annotations

from typing import Any, Optional


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _hex(r: float, g: float, b: float) -> str:
    return "%02X%02X%02X" % (
        int(round(_clamp01(r) * 255)),
        int(round(_clamp01(g) * 255)),
        int(round(_clamp01(b) * 255)),
    )


def _components(color: Any) -> list[float]:
    if color is None:
        return []
    if isinstance(color, (int, float)):
        return [float(color)]
    if isinstance(color, (list, tuple)):
        out: list[float] = []
        for c in color:
            if isinstance(c, (int, float)):
                out.append(float(c))
        return out
    return []


def to_hex(color: Any, colorspace: Any = None) -> Optional[str]:
    """Convert a pdfminer colour operand to ``RRGGBB``, or None if unknown.

    ``colorspace`` is only consulted for its component count; separation and
    ICC spaces are treated by arity, which is what the renderers do for the
    device spaces we can actually reproduce in DrawingML.
    """
    comps = _components(color)
    n = len(comps)
    if n == 0:
        return None
    if n == 1:
        # A 1-component value in a Separation/DeviceN space is tint, where 1.0
        # means full ink (dark); in DeviceGray 1.0 means white.  Use the space
        # name to tell them apart, defaulting to gray.
        name = getattr(colorspace, "name", "") or ""
        if name in ("Separation", "DeviceN", "Indexed"):
            v = 1.0 - comps[0]
            return _hex(v, v, v)
        v = comps[0]
        return _hex(v, v, v)
    if n == 3:
        return _hex(comps[0], comps[1], comps[2])
    if n == 4:
        c, m, y, k = comps
        return _hex((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    # Unknown arity: average what we have rather than invent a colour.
    v = sum(comps) / n
    return _hex(v, v, v)


def is_near_white(hexcolor: Optional[str], threshold: int = 250) -> bool:
    if not hexcolor or len(hexcolor) != 6:
        return False
    r = int(hexcolor[0:2], 16)
    g = int(hexcolor[2:4], 16)
    b = int(hexcolor[4:6], 16)
    return r >= threshold and g >= threshold and b >= threshold


def luminance(hexcolor: str) -> float:
    r = int(hexcolor[0:2], 16) / 255.0
    g = int(hexcolor[2:4], 16) / 255.0
    b = int(hexcolor[4:6], 16) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def distance(a: str, b: str) -> float:
    """Euclidean sRGB distance in 0..1 (rough, but enough for equality tests)."""
    if not a or not b or len(a) != 6 or len(b) != 6:
        return 1.0
    d = 0.0
    for i in (0, 2, 4):
        d += (int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) ** 2
    return (d**0.5) / (255.0 * (3**0.5))
