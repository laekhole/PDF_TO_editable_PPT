"""Font-name normalisation and style inference.

Embedded PDF fonts carry a six-letter subset tag ("ABCDEF+NanumGothic") and
frequently encode weight and slant in the family name itself.  PowerPoint
needs a plain family plus separate bold/italic flags, so we split them apart
here and let a caller-supplied mapping have the final say.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")

# Style words that appear as a suffix of a PostScript family name.  Order
# matters: the longest match is stripped first.
_STYLE_TOKENS: list[tuple[str, bool, bool]] = [
    ("bolditalic", True, True),
    ("boldoblique", True, True),
    ("semibolditalic", True, True),
    ("blackitalic", True, True),
    ("heavyitalic", True, True),
    ("extrabold", True, False),
    ("semibold", True, False),
    ("demibold", True, False),
    ("ultrabold", True, False),
    ("oblique", False, True),
    ("italic", False, True),
    ("black", True, False),
    ("heavy", True, False),
    ("bold", True, False),
    ("medium", False, False),
    ("regular", False, False),
    ("roman", False, False),
    ("book", False, False),
    ("light", False, False),
    ("thin", False, False),
    ("normal", False, False),
]

# PDF font descriptor flag bits (PDF 32000-1 table 123).
FLAG_ITALIC = 1 << 6
FLAG_FORCE_BOLD = 1 << 18
FLAG_SERIF = 1 << 1


def strip_subset_prefix(name: str) -> str:
    """Remove the ``ABCDEF+`` subset tag PDF writers prepend to embedded fonts."""
    return _SUBSET_RE.sub("", name or "")


def split_style(name: str) -> Tuple[str, bool, bool]:
    """Split ``NanumGothic-Bold`` into ``("NanumGothic", True, False)``."""
    base = strip_subset_prefix(name or "").strip()
    bold = False
    italic = False
    # Peel style tokens off the tail, separated by '-', ',', or CamelCase.
    # Monotype/Adobe PostScript names append a foundry tag after the style
    # ("TimesNewRomanPS-ItalicMT"), so each token is also tried with one.
    changed = True
    while changed and base:
        changed = False
        for token, is_bold, is_italic in _STYLE_TOKENS:
            for tech in ("", "mt", "psmt"):
                for sep in ("-", ",", " ", ""):
                    suffix = sep + token + tech
                    if len(base) <= len(suffix) or not base.lower().endswith(suffix):
                        continue
                    # Only strip a bare (sep == "") suffix on a CamelCase edge,
                    # so "Bookman" does not lose its "Book".
                    if sep == "" and not base[-len(suffix)].isupper():
                        continue
                    base = base[: -len(suffix)].rstrip("-, ")
                    bold = bold or is_bold
                    italic = italic or is_italic
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
    if not base:
        base = strip_subset_prefix(name or "").strip() or "Arial"
    return base, bold, italic


def normalize(
    fontname: str,
    flags: int = 0,
    substitutions: Optional[Dict[str, str]] = None,
) -> Tuple[str, bool, bool]:
    """Return ``(family, bold, italic)`` for a PDF font name.

    ``substitutions`` maps a normalised source family (case-insensitive) to the
    family PowerPoint should use; this is the site-configurable font map.
    """
    family, bold, italic = split_style(fontname)
    if flags:
        italic = italic or bool(flags & FLAG_ITALIC)
        bold = bold or bool(flags & FLAG_FORCE_BOLD)
    if substitutions:
        mapped = substitutions.get(family.lower())
        if mapped:
            family = mapped
    return family, bold, italic


def descriptor_flags(font: object) -> int:
    """Best-effort read of the /Flags entry of a pdfminer font's descriptor."""
    desc = getattr(font, "descriptor", None)
    if isinstance(desc, dict):
        value = desc.get("Flags")
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def load_substitutions(path: Optional[str]) -> Dict[str, str]:
    """Load a ``source family = target family`` mapping file (INI-ish, UTF-8)."""
    if not path:
        return {}
    out: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if "=" not in line:
                continue
            src, dst = line.split("=", 1)
            out[src.strip().lower()] = dst.strip()
    return out
