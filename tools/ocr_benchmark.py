#!/usr/bin/env python3
"""Score every available OCR engine on the ground-truth Korean scan.

    python tools/ocr_benchmark.py [fixtures/pdf/scanned_korean.pdf]

Prints the character error rate (CER) per engine, the mean confidence, the
wall-clock time, and the recognised lines next to the truth.  PaddleOCR is
scored only if it is importable *and* its models are cached locally -- this
script never downloads anything.
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from pdf2editable_ppt import ocr  # noqa: E402

import pypdfium2 as pdfium  # noqa: E402


def main(argv):
    pdf = argv[1] if len(argv) > 1 else os.path.join(ROOT, "fixtures", "pdf", "scanned_korean.pdf")
    truth_path = pdf[:-4] + ".truth.txt"
    truth = open(truth_path, encoding="utf-8").read() if os.path.exists(truth_path) else None

    doc = pdfium.PdfDocument(pdf)
    width, height = doc[0].get_size()
    doc.close()

    engines = [ocr.TesseractEngine(), ocr.PaddleEngine()]
    any_ran = False
    for engine in engines:
        if not engine.available():
            print("%-12s not available" % engine.name)
            continue
        started = time.time()
        try:
            page = ocr.ocr_page(pdf, 0, width, height, engine, dpi=300)
        except Exception as exc:  # noqa: BLE001
            print("%-12s failed: %s: %s" % (engine.name, type(exc).__name__, str(exc)[:120]))
            continue
        elapsed = time.time() - started
        any_ran = True
        print("=" * 72)
        print("%-12s %d lines, mean confidence %.1f, %.1fs" % (
            engine.name, len(page.lines), page.mean_confidence(), elapsed))
        if truth is not None:
            cer = ocr.character_error_rate(truth, page.text)
            print("%-12s CER = %.3f  (%.1f%% of characters wrong, whitespace ignored)" % (
                engine.name, cer, cer * 100))
        for line in page.lines:
            flag = "  " if line.confidence >= ocr.LOW_CONFIDENCE else "?!"
            print("  %s %3.0f  %s" % (flag, line.confidence, line.text))
    if not any_ran:
        print("no OCR engine could run")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
