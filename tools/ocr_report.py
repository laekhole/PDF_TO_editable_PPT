#!/usr/bin/env python3
"""Run the whole OCR comparison in one go and write a single report file.

The point of this script is that it needs no supervision: run it once, hand
the resulting Markdown file to whoever is reviewing, and every number they
need is in it -- which engines were found, what each scored on the
ground-truth fixture, and how each did on your own document.

    python tools/ocr_report.py                          # fixture only
    python tools/ocr_report.py --pdf path/to/scan.pdf   # plus your document
    python tools/ocr_report.py --pdf scan.pdf --out report.md

Nothing here downloads anything or phones home.  PaddleOCR, if installed,
loads models from its local cache; if that cache is empty and its hosts are
unreachable, the script records that and carries on with the engines that do
work rather than failing.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FIXTURE = os.path.join(ROOT, "fixtures", "pdf", "scanned_korean.pdf")
TRUTH = os.path.join(ROOT, "fixtures", "pdf", "scanned_korean.truth.txt")


def _versions() -> Dict[str, str]:
    out = {
        "python": sys.version.split()[0],
        "platform": "%s %s" % (platform.system(), platform.release()),
    }
    binary = shutil.which("tesseract")
    if binary:
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            out["tesseract"] = (proc.stdout + proc.stderr).splitlines()[0].strip()
        except Exception:
            out["tesseract"] = "present, version unreadable"
    else:
        out["tesseract"] = "not installed"
    for name in ("paddleocr", "paddle"):
        try:
            module = __import__(name)
            out[name] = getattr(module, "__version__", "unknown")
        except Exception:
            out[name] = "not installed"
    return out


def _ensure_fixture() -> Optional[str]:
    if os.path.exists(FIXTURE) and os.path.exists(TRUTH):
        return None
    script = os.path.join(ROOT, "fixtures", "make_fixtures.py")
    try:
        subprocess.run([sys.executable, script], check=True, capture_output=True, timeout=900)
    except Exception as exc:
        return "could not generate fixtures: %s" % exc
    if not os.path.exists(FIXTURE):
        return "fixture generation ran but produced no scanned_korean.pdf"
    return None


def _score(engine, pdf: str, truth: Optional[str], dpi: float) -> Dict:
    from pdf2editable_ppt import ocr

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    total = len(doc)
    sizes = [doc[i].get_size() for i in range(total)]
    doc.close()

    started = time.time()
    pages = []
    for index in range(total):
        width, height = sizes[index]
        pages.append(ocr.ocr_page(pdf, index, width, height, engine, dpi=dpi))
    elapsed = time.time() - started

    text = "\n".join(p.text for p in pages)
    lines = sum(len(p.lines) for p in pages)
    words = sum(len(ln.words) for p in pages for ln in p.lines)
    low = sum(1 for p in pages for ln in p.lines if ln.confidence < ocr.LOW_CONFIDENCE)
    dropped = sum(len(p.dropped) for p in pages)
    confidences = [w.confidence for p in pages for ln in p.lines for w in ln.words]
    result = {
        "engine": engine.name,
        "seconds": round(elapsed, 1),
        "pages": total,
        "lines": lines,
        "words": words,
        "characters": len(text.replace(" ", "").replace("\n", "")),
        "meanConfidence": round(sum(confidences) / len(confidences), 1) if confidences else 0.0,
        "lowConfidenceLines": low,
        "droppedAsNoise": dropped,
        "pageObjects": pages,
        "text": text,
    }
    if truth is not None:
        result["cer"] = round(ocr.character_error_rate(truth, text), 4)
    return result


def _engines(preference: str) -> List:
    from pdf2editable_ppt import ocr

    candidates = {"tesseract": ocr.TesseractEngine(), "paddleocr": ocr.PaddleEngine()}
    if preference != "all":
        candidates = {preference: candidates[preference]}
    return list(candidates.values())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", help="your own scanned PDF to run alongside the fixture")
    parser.add_argument("--out", default="ocr-report.md", help="report file to write")
    parser.add_argument("--dpi", type=float, default=None, help="OCR render dpi (default 400)")
    parser.add_argument(
        "--engine", choices=("all", "tesseract", "paddleocr"), default="all"
    )
    parser.add_argument(
        "--max-pages", type=int, default=0, help="limit your PDF to this many pages (0 = all)"
    )
    args = parser.parse_args(argv)

    from pdf2editable_ppt import ocr

    dpi = args.dpi or ocr.DEFAULT_OCR_DPI
    report: List[str] = []
    w = report.append

    w("# OCR engine comparison")
    w("")
    w("Generated by `tools/ocr_report.py`. Paste this whole file back for review.")
    w("")
    w("## Environment")
    w("")
    w("| Component | Version |")
    w("| --- | --- |")
    for key, value in _versions().items():
        w("| %s | %s |" % (key, value))
    w("")
    w("OCR render dpi: %g" % dpi)
    w("")

    problem = _ensure_fixture()
    if problem:
        w("> **Fixture unavailable**: %s" % problem)
        w("")

    available = []
    w("## Engines found")
    w("")
    for engine in _engines(args.engine):
        try:
            ok = engine.available()
        except Exception as exc:
            ok = False
            w("- **%s**: check failed (%s: %s)" % (engine.name, type(exc).__name__, exc))
            continue
        w("- **%s**: %s" % (engine.name, "available" if ok else "not available"))
        if ok:
            available.append(engine)
    w("")
    if not available:
        w("No OCR engine could be used, so there is nothing to compare.")
        w("")
        w("Install at least one:")
        w("")
        w("```bash")
        w("sudo apt-get install -y tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng")
        w("pip install paddlepaddle paddleocr")
        w("```")
        _write(args.out, report)
        return 1

    truth = open(TRUTH, encoding="utf-8").read() if os.path.exists(TRUTH) else None

    # ── the ground-truth fixture ────────────────────────────────────────────
    fixture_rows = []
    fixture_details = []
    if os.path.exists(FIXTURE):
        w("## Ground-truth fixture (`fixtures/pdf/scanned_korean.pdf`)")
        w("")
        w("A typeset Korean page rendered at 200 dpi, JPEG-compressed, skewed 0.6 degrees")
        w("and speckled to look photographed. Its exact text is known, so CER is measured,")
        w("not estimated. Lower CER is better; 0 would be perfect.")
        w("")
        for engine in available:
            try:
                scored = _score(engine, FIXTURE, truth, dpi)
            except Exception as exc:
                fixture_rows.append(
                    "| %s | failed | | | | %s: %s |"
                    % (engine.name, type(exc).__name__, str(exc).splitlines()[0][:90])
                )
                fixture_details.append(
                    ("%s (failed)" % engine.name, traceback.format_exc().strip().splitlines()[-6:])
                )
                continue
            fixture_rows.append(
                "| %s | **%.3f** | %d | %.1f | %.1fs | |"
                % (
                    engine.name,
                    scored.get("cer", float("nan")),
                    scored["lines"],
                    scored["meanConfidence"],
                    scored["seconds"],
                )
            )
            fixture_details.append(
                (engine.name, ["%3.0f  %s" % (ln.confidence, ln.text)
                               for p in scored["pageObjects"] for ln in p.lines])
            )
        w("| engine | CER | lines | mean confidence | time | note |")
        w("| --- | --- | --- | --- | --- | --- |")
        for row in fixture_rows:
            w(row)
        w("")
        if truth:
            w("<details><summary>Truth vs each engine, line by line</summary>")
            w("")
            w("**Truth**")
            w("")
            w("```")
            w(truth.rstrip())
            w("```")
            w("")
            for name, lines in fixture_details:
                w("**%s**" % name)
                w("")
                w("```")
                for line in lines:
                    w(line)
                w("```")
                w("")
            w("</details>")
            w("")

    # ── the operator's own document ─────────────────────────────────────────
    if args.pdf:
        pdf = os.path.abspath(args.pdf)
        w("## Your document (`%s`)" % os.path.basename(pdf))
        w("")
        if not os.path.exists(pdf):
            w("> File not found: `%s`" % pdf)
            w("")
        else:
            w("No ground truth here, so there is no CER. What the numbers show is how much")
            w("text each engine found and how sure it was; read the sample lines to judge")
            w("quality.")
            w("")
            w("| engine | pages | lines | words | characters | mean confidence | low-confidence lines | dropped | time |")
            w("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            samples = []
            for engine in available:
                try:
                    scored = _score(engine, pdf, None, dpi)
                except Exception as exc:
                    w("| %s | failed: %s |" % (engine.name, str(exc).splitlines()[0][:80]))
                    continue
                w(
                    "| %s | %d | %d | %d | %d | %.1f | %d | %d | %.1fs |"
                    % (
                        scored["engine"],
                        scored["pages"],
                        scored["lines"],
                        scored["words"],
                        scored["characters"],
                        scored["meanConfidence"],
                        scored["lowConfidenceLines"],
                        scored["droppedAsNoise"],
                        scored["seconds"],
                    )
                )
                first = scored["pageObjects"][0]
                samples.append(
                    (
                        engine.name,
                        ["%3.0f  %s" % (ln.confidence, ln.text) for ln in first.lines[:30]],
                    )
                )
            w("")
            w("<details><summary>First 30 recognised lines of page 1, per engine</summary>")
            w("")
            for name, lines in samples:
                w("**%s**" % name)
                w("")
                w("```")
                for line in lines:
                    w(line)
                w("```")
                w("")
            w("</details>")
            w("")

    w("## What to do with this")
    w("")
    w("- The CER column is the one that decides which engine to default to.")
    w("- A big gap between engines on your document but not on the fixture means the")
    w("  fixture is not representative; say so, and a better fixture can be built.")
    w("- If an engine failed to load models, that is a network or install problem, not")
    w("  an accuracy result.")
    w("")

    _write(args.out, report)
    return 0


def _write(path: str, lines: List[str]) -> None:
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print("=" * 70)
    print("Report written to: %s" % os.path.abspath(path))
    print("Paste that file back for review.")


if __name__ == "__main__":
    raise SystemExit(main())
