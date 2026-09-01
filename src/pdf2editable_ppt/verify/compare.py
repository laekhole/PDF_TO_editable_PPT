"""Visual comparison of a source page against the rebuilt slide.

A single global score hides exactly the failures that matter here, so every
comparison reports four independent measures:

- ``ink_missing``   painted source pixels with nothing painted on top of them
- ``ink_added``     painted rebuilt pixels where the source painted nothing
- ``edge_iou``      overlap of the two edge maps (catches shifted geometry)
- ``mean_delta``    average per-channel colour difference over painted area

A region passes only when all four stay within their thresholds; that way a
missing curve cannot be averaged away by a large correct background.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

# A pixel differs from the page background by more than this (0-255) to count
# as "ink".
INK_THRESHOLD = 24
# Per-channel difference above this counts as a changed pixel.
DELTA_THRESHOLD = 40


@dataclass
class Comparison:
    ink_missing: float
    ink_added: float
    edge_iou: float
    mean_delta: float
    source_ink_ratio: float
    width: int
    height: int

    def to_dict(self) -> Dict[str, float]:
        return {k: round(float(v), 5) for k, v in asdict(self).items()}


def _as_array(img: Image.Image, size: Tuple[int, int], smooth_px: float = 0.0) -> np.ndarray:
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    img = img.convert("RGB")
    if smooth_px > 0:
        # Two rasterisers never agree on glyph hinting or edge antialiasing.
        # Blurring first asks "is the same ink in the same place, at the same
        # weight" instead of "are these pixels identical" -- which is the
        # question that actually matters for a slide.
        img = img.filter(ImageFilter.GaussianBlur(radius=smooth_px))
    return np.asarray(img, dtype=np.int16)


def _ink_mask(arr: np.ndarray) -> np.ndarray:
    """Pixels that differ from white by more than the ink threshold."""
    return (255 - arr).max(axis=2) > INK_THRESHOLD


def _edges(arr: np.ndarray) -> np.ndarray:
    """Cheap gradient-magnitude edge map, thresholded."""
    gray = arr.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return (gx + gy) > 28


def _dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """Binary dilation with a square kernel, via shifted ORs."""
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def compare_images(
    source: Image.Image,
    rebuilt: Image.Image,
    tolerance_px: int = 2,
    smooth_px: float = 1.2,
) -> Comparison:
    """Compare two renders of the same area.

    ``tolerance_px`` dilates the masks before differencing so a sub-pixel
    rasteriser disagreement does not register as missing ink; ``smooth_px``
    blurs both inputs first so glyph hinting differences do not either.
    """
    size = source.size
    a = _as_array(source, size, smooth_px)
    b = _as_array(rebuilt, size, smooth_px)

    ink_a = _ink_mask(a)
    ink_b = _ink_mask(b)
    ink_a_d = _dilate(ink_a, tolerance_px)
    ink_b_d = _dilate(ink_b, tolerance_px)

    a_count = int(ink_a.sum())
    b_count = int(ink_b.sum())
    missing = float((ink_a & ~ink_b_d).sum()) / a_count if a_count else 0.0
    added = float((ink_b & ~ink_a_d).sum()) / b_count if b_count else 0.0

    ea = _dilate(_edges(a), tolerance_px)
    eb = _dilate(_edges(b), tolerance_px)
    union = float((ea | eb).sum())
    iou = float((ea & eb).sum()) / union if union else 1.0

    both = ink_a | ink_b
    if both.any():
        delta = np.abs(a - b).max(axis=2)
        mean_delta = float(delta[both].mean())
    else:
        mean_delta = 0.0

    total = size[0] * size[1]
    return Comparison(
        ink_missing=missing,
        ink_added=added,
        edge_iou=iou,
        mean_delta=mean_delta,
        source_ink_ratio=(a_count / total) if total else 0.0,
        width=size[0],
        height=size[1],
    )


@dataclass
class Thresholds:
    """Pass/fail limits.

    Two profiles exist because the two failure modes differ.  A shape either
    lands where it should or it does not, so the geometric profile is tight.
    Text is redrawn by a different rasteriser with a possibly substituted
    font, so its profile tolerates stroke-level disagreement while still
    catching a line that moved, wrapped, vanished or changed weight.
    """

    max_ink_missing: float = 0.10
    max_ink_added: float = 0.14
    min_edge_iou: float = 0.50
    max_mean_delta: float = 34.0
    # Areas with almost no ink cannot be judged by ratios.
    min_source_ink_ratio: float = 0.0006
    smooth_px: float = 1.2


TEXT_THRESHOLDS = Thresholds(
    max_ink_missing=0.24,
    max_ink_added=0.28,
    min_edge_iou=0.30,
    max_mean_delta=62.0,
    min_source_ink_ratio=0.0006,
    smooth_px=2.4,
)

PAGE_THRESHOLDS = Thresholds(
    max_ink_missing=0.10,
    max_ink_added=0.14,
    min_edge_iou=0.45,
    max_mean_delta=40.0,
    min_source_ink_ratio=0.0004,
    smooth_px=1.8,
)


def evaluate(cmp: Comparison, thresholds: Optional[Thresholds] = None) -> Tuple[bool, str]:
    t = thresholds or Thresholds()
    if cmp.source_ink_ratio < t.min_source_ink_ratio:
        return True, "region is effectively blank; nothing to verify"
    reasons = []
    if cmp.ink_missing > t.max_ink_missing:
        reasons.append("%.1f%% of the source ink is missing" % (cmp.ink_missing * 100))
    if cmp.ink_added > t.max_ink_added:
        reasons.append("%.1f%% of the rebuilt ink is not in the source" % (cmp.ink_added * 100))
    if cmp.edge_iou < t.min_edge_iou:
        reasons.append("edge overlap is only %.2f" % cmp.edge_iou)
    if cmp.mean_delta > t.max_mean_delta:
        reasons.append("mean colour delta is %.0f/255" % cmp.mean_delta)
    if reasons:
        return False, "; ".join(reasons)
    return True, "within tolerance"
