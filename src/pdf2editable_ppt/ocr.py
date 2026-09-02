"""Local OCR for scanned pages (experimental).

Rules, from the conversion policy:

- Engines run **locally**; nothing is sent anywhere.  Tesseract is driven
  through its CLI (no Python binding required); PaddleOCR is optional and
  used only when importable.
- OCR output never touches the fidelity deck.  It goes to a *separate*
  experimental deck and a JSON sidecar, so the scan is never painted over
  and the operator can see exactly what was guessed.
- Every word keeps its confidence.  Low-confidence text is flagged, not
  hidden and not silently dropped.

Tesseract's Korean model reports most syllables as separate "words".  Since
Hangul is written with spaces between *words*, the geometry decides: two
boxes on one line are joined when the gap between them is small relative to
the text height, and a space is written only where the gap is a real one.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from .units import Rect

DEFAULT_OCR_DPI = 400.0
# Tesseract page segmentation: 4 = "single column of text of variable sizes".
# Measured on the ground-truth Korean scan (tools/ocr_benchmark.py): the
# default auto mode (3) silently drops about half the words (CER 0.63), sparse
# mode (11) loses characters inside lines (CER 0.13), and 4 reaches CER 0.016.
# Multi-column pages are fine because line grouping here re-splits a row at
# wide gaps anyway.
DEFAULT_TESSERACT_PSM = 4
# Hangul is set on a square em, so two boxes are one word when the second
# starts where the first's syllables' advances end.  This is the pen-gap
# tolerance, as a fraction of the em; an ink-box gap cannot do this because
# a narrow syllable such as "도" leaves a wide visual gap inside a word.
HANGUL_SPACE_RATIO = 0.22
# An ink gap this small (as a fraction of the height) is certainly inside a
# word; those pairs calibrate the em of the line.
WITHIN_WORD_GAP_RATIO = 0.2
# For non-Hangul runs and engine-grouped words the ink gap is the signal.
LATIN_SPACE_RATIO = 0.28
# Words with confidence below this are flagged in the deck and the report.
LOW_CONFIDENCE = 60.0
# Below this a word is only kept when it is long enough to be real text that
# the engine merely scored badly (a mixed-script run such as
# "BOT(Build-Operate-Transfer)") rather than a stray mark on a chart.  What is
# dropped stays in the JSON sidecar.
NOISE_CONFIDENCE = 30.0
NOISE_KEEP_MIN_CHARS = 4


@dataclass
class OcrWord:
    text: str
    bbox: Rect  # PDF points, y-up, visual page space
    confidence: float  # 0..100


@dataclass
class OcrLine:
    words: List[OcrWord]
    bbox: Rect
    text: str
    confidence: float

    @property
    def height_pt(self) -> float:
        return self.bbox.height


@dataclass
class OcrPage:
    index: int
    engine: str
    dpi: float
    lines: List[OcrLine] = field(default_factory=list)
    dropped: List[OcrWord] = field(default_factory=list)
    """Words rejected as noise -- kept so the sidecar shows what was thrown away."""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def mean_confidence(self) -> float:
        words = [w for line in self.lines for w in line.words]
        if not words:
            return 0.0
        return sum(w.confidence for w in words) / len(words)


# ── engines ──────────────────────────────────────────────────────────────────


class OcrEngine:
    name = "none"

    def available(self) -> bool:
        return False

    def recognise(self, image: Image.Image, languages: Sequence[str]) -> List[Tuple[str, Tuple[int, int, int, int], float]]:
        """Return (text, (x0, y0, x1, y1) pixels y-down, confidence 0-100)."""
        raise NotImplementedError


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, binary: Optional[str] = None, psm: int = DEFAULT_TESSERACT_PSM) -> None:
        self.binary = binary or shutil.which("tesseract")
        self.psm = psm

    def available(self) -> bool:
        if not self.binary:
            return False
        try:
            out = subprocess.run(
                [self.binary, "--list-langs"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return "kor" in out.stdout or "kor" in out.stderr

    def languages(self) -> List[str]:
        if not self.binary:
            return []
        out = subprocess.run([self.binary, "--list-langs"], capture_output=True, text=True)
        langs = []
        for line in (out.stdout + out.stderr).splitlines():
            token = line.strip()
            if token and " " not in token and token not in ("osd",):
                langs.append(token)
        return langs

    def recognise(self, image, languages):
        assert self.binary
        with tempfile.TemporaryDirectory(prefix="p2ep-ocr-") as tmp:
            src = os.path.join(tmp, "page.png")
            image.save(src, "PNG")
            base = os.path.join(tmp, "out")
            lang = "+".join(languages)
            cmd = [self.binary, src, base, "-l", lang, "--psm", str(self.psm), "tsv"]
            subprocess.run(cmd, capture_output=True, check=True, timeout=600)
            with open(base + ".tsv", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
        words = []
        for row in rows:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                conf = float(row.get("conf") or 0)
                left, top = int(row["left"]), int(row["top"])
                width, height = int(row["width"]), int(row["height"])
            except (KeyError, ValueError):
                continue
            words.append((text, (left, top, left + width, top + height), conf))
        return words


class PaddleEngine(OcrEngine):
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None

    def available(self) -> bool:
        try:
            import importlib

            importlib.import_module("paddleocr")
        except Exception:
            return False
        return True

    def _instance(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR

            # Models are loaded from the local cache.  The first ever run of
            # PaddleOCR downloads them; that is a one-time install step, not a
            # per-document network call, and it is documented as such.
            #
            # PaddleOCR 3.x renamed the constructor arguments and rejects the
            # 2.x ones with a ValueError, so try the current names first and
            # fall back to the old ones for an older install.
            try:
                self._ocr = PaddleOCR(lang="korean", use_textline_orientation=True)
            except (ValueError, TypeError):
                self._ocr = PaddleOCR(lang="korean", use_angle_cls=True, show_log=False)
        return self._ocr

    def recognise(self, image, languages):
        import numpy as np

        arr = np.asarray(image.convert("RGB"))
        instance = self._instance()
        words = []
        if hasattr(instance, "predict"):
            # 3.x: one result object per image, dict-like, with parallel
            # lists of polygons, texts and scores.
            for res in instance.predict(arr) or []:
                polys = res.get("rec_polys") or res.get("dt_polys") or []
                texts = res.get("rec_texts") or []
                scores = res.get("rec_scores") or []
                for box, text, conf in zip(polys, texts, scores):
                    words.append((text, _poly_bbox(box), float(conf) * 100))
            return words
        # 2.x: a list per page of [box, (text, confidence)].
        result = instance.ocr(arr, cls=True)
        for page in result or []:
            for entry in page or []:
                box, (text, conf) = entry[0], entry[1]
                words.append((text, _poly_bbox(box), float(conf) * 100))
        return words


def _poly_bbox(box) -> Tuple[int, int, int, int]:
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def pick_engine(preference: str = "auto") -> Optional[OcrEngine]:
    candidates: List[OcrEngine]
    if preference == "tesseract":
        candidates = [TesseractEngine()]
    elif preference == "paddleocr":
        candidates = [PaddleEngine()]
    else:
        candidates = [TesseractEngine(), PaddleEngine()]
    for engine in candidates:
        if engine.available():
            return engine
    return None


# ── line reconstruction ──────────────────────────────────────────────────────


def _is_hangul(ch: str) -> bool:
    return bool(ch) and (
        0xAC00 <= ord(ch) <= 0xD7A3 or 0x1100 <= ord(ch) <= 0x11FF or 0x3130 <= ord(ch) <= 0x318F
    )


def _hangul_count(text: str) -> int:
    return sum(1 for ch in text if _is_hangul(ch))


def _needs_space(prev: OcrWord, cur: OcrWord, em: float) -> bool:
    """Is there a real space between two boxes on one line?

    Tesseract's Korean model boxes many syllables one at a time.  A narrow
    syllable such as "도" leaves an ink gap inside a word as wide as a real
    space, so for a single-syllable box the decision uses the *pitch* between
    successive left edges instead: Hangul sits on a one-em grid, and a space
    adds a third of an em to it.  Boxes the engine already grouped into
    multi-syllable words carry its own word-break decision, and there the
    plain ink gap is reliable.
    """
    h = max(prev.bbox.height, cur.bbox.height, 1.0)
    ink_gap = cur.bbox.x0 - prev.bbox.x1
    n_prev = _hangul_count(prev.text)
    single_hangul = n_prev == 1 and len(prev.text) == 1 and _is_hangul(cur.text[:1])
    if single_hangul and em > 0:
        pitch = cur.bbox.x0 - prev.bbox.x0
        return pitch > (1.0 + HANGUL_SPACE_RATIO) * em
    return ink_gap > LATIN_SPACE_RATIO * h


def _line_em(words: Sequence[OcrWord]) -> float:
    """Estimate the em of a line from pairs that are certainly one word.

    Two Hangul boxes whose ink gap is tiny are inside a word, and the pitch
    between their left edges is then exactly ``n`` advances.  The median of
    those advances is the em.  A line with no such pair (every box a whole
    word) falls back to the ink height, which for Hangul is about 0.9 em.
    """
    advances = []
    heights = []
    ordered = sorted(words, key=lambda w: w.bbox.x0)
    for a, b in zip(ordered, ordered[1:]):
        n = _hangul_count(a.text)
        if not n or n != len(a.text) or not _is_hangul(b.text[:1]):
            continue
        heights.append(a.bbox.height)
        h = max(a.bbox.height, b.bbox.height, 1.0)
        if b.bbox.x0 - a.bbox.x1 <= WITHIN_WORD_GAP_RATIO * h:
            advances.append((b.bbox.x0 - a.bbox.x0) / n)
    if advances:
        advances.sort()
        return advances[len(advances) // 2]
    if heights:
        heights.sort()
        return heights[len(heights) // 2] / 0.9
    return 0.0


def group_lines(words: List[OcrWord]) -> List[OcrLine]:
    """Group word boxes into lines and rebuild spacing from geometry."""
    if not words:
        return []
    remaining = sorted(words, key=lambda w: (-w.bbox.cy, w.bbox.x0))
    rows: List[List[OcrWord]] = []
    for w in remaining:
        placed = False
        for row in rows:
            ref = row[-1]
            overlap = min(ref.bbox.y1, w.bbox.y1) - max(ref.bbox.y0, w.bbox.y0)
            if overlap > 0.5 * min(ref.bbox.height, w.bbox.height):
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])

    lines: List[OcrLine] = []
    for row in rows:
        row.sort(key=lambda w: w.bbox.x0)
        # split a row at large gaps (columns)
        segments: List[List[OcrWord]] = [[row[0]]]
        for prev, cur in zip(row, row[1:]):
            h = max(prev.bbox.height, cur.bbox.height, 1.0)
            if cur.bbox.x0 - prev.bbox.x1 > 2.5 * h:
                segments.append([cur])
            else:
                segments[-1].append(cur)
        for seg in segments:
            em = _line_em(seg)
            parts: List[str] = []
            for i, w in enumerate(seg):
                if i > 0:
                    parts.append(" " if _needs_space(seg[i - 1], w, em) else "")
                parts.append(w.text)
            text = unicodedata.normalize("NFC", "".join(parts))
            box = seg[0].bbox
            for w in seg[1:]:
                box = box.union(w.bbox)
            conf = sum(w.confidence for w in seg) / len(seg)
            lines.append(OcrLine(words=seg, bbox=box, text=text, confidence=conf))
    lines.sort(key=lambda ln: (-ln.bbox.y1, ln.bbox.x0))
    return lines


# ── page driver ──────────────────────────────────────────────────────────────


def ocr_page(
    pdf_path: str,
    page_index: int,
    page_width_pt: float,
    page_height_pt: float,
    engine: OcrEngine,
    languages: Sequence[str] = ("kor", "eng"),
    dpi: float = DEFAULT_OCR_DPI,
    password: str = "",
) -> OcrPage:
    from .verify.render import render_pdf_page

    image = render_pdf_page(pdf_path, page_index, dpi=dpi, password=password)
    sx = page_width_pt / image.width
    sy = page_height_pt / image.height
    raw = engine.recognise(image, languages)
    words: List[OcrWord] = []
    dropped: List[OcrWord] = []
    for text, (x0, y0, x1, y1), conf in raw:
        box = Rect(x0 * sx, page_height_pt - y1 * sy, x1 * sx, page_height_pt - y0 * sy)
        word = OcrWord(text=text, bbox=box, confidence=conf)
        alnum = sum(1 for ch in text if ch.isalnum())
        if alnum == 0 or (conf < NOISE_CONFIDENCE and alnum < NOISE_KEEP_MIN_CHARS):
            dropped.append(word)
        else:
            words.append(word)
    page = OcrPage(index=page_index, engine=engine.name, dpi=dpi, lines=group_lines(words), dropped=dropped)
    return page


def page_to_dict(page: OcrPage) -> Dict:
    return {
        "pageNumber": page.index + 1,
        "engine": page.engine,
        "dpi": page.dpi,
        "meanConfidence": round(page.mean_confidence(), 1),
        "lines": [
            {
                "text": ln.text,
                "confidence": round(ln.confidence, 1),
                "lowConfidence": ln.confidence < LOW_CONFIDENCE,
                "bboxPt": [round(v, 2) for v in ln.bbox.as_tuple()],
                "words": [
                    {
                        "text": w.text,
                        "confidence": round(w.confidence, 1),
                        "bboxPt": [round(v, 2) for v in w.bbox.as_tuple()],
                    }
                    for w in ln.words
                ],
            }
            for ln in page.lines
        ],
        "droppedAsNoise": [
            {"text": w.text, "confidence": round(w.confidence, 1), "bboxPt": [round(v, 2) for v in w.bbox.as_tuple()]}
            for w in page.dropped
        ],
    }


# ── accuracy measurement ─────────────────────────────────────────────────────


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over the reference length, whitespace ignored."""
    ref = [c for c in unicodedata.normalize("NFC", reference) if not c.isspace()]
    hyp = [c for c in unicodedata.normalize("NFC", hypothesis) if not c.isspace()]
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / len(ref)
