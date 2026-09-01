"""Command-line interface.

Local only: the converter opens the PDF, writes the PPTX and, if asked, a JSON
report.  It makes no network calls and emits no telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Sequence

from .converter import convert
from .extract.content import PasswordRequired, UnparseableDocument
from .extract.fonts import load_substitutions
from .pipeline import ConvertOptions, VECTOR_BUDGET_PER_PAGE
from .report import write_report


def parse_pages(spec: Optional[str], total: Optional[int] = None) -> Optional[List[int]]:
    """Parse ``1-5,8,11-`` into 0-based page indices."""
    if not spec:
        return None
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            start = int(lo) if lo.strip() else 1
            if hi.strip():
                end = int(hi)
            elif total is not None:
                end = total
            else:
                raise ValueError("open-ended range %r needs the document page count" % part)
            if end < start:
                raise ValueError("range %r is inverted" % part)
            out.extend(range(start - 1, end))
        else:
            out.append(int(part) - 1)
    bad = [p for p in out if p < 0]
    if bad:
        raise ValueError("page numbers start at 1")
    return sorted(set(out))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf2editable-ppt",
        description=(
            "Convert a PDF into an editable PowerPoint deck without damaging the "
            "original artwork. Objects that cannot be rebuilt safely are kept as "
            "a render of the source instead of a broken native shape."
        ),
    )
    p.add_argument("input", help="source PDF")
    p.add_argument("-o", "--output", required=True, help="destination .pptx")
    p.add_argument("--report", dest="report", help="write a JSON conversion report here")
    p.add_argument("--pages", help="pages to convert, e.g. 1-5 or 1,3,7-9")
    p.add_argument(
        "--mode",
        choices=("fidelity", "fast"),
        default="fidelity",
        help=(
            "fidelity (default) renders the result and falls back wherever it does "
            "not match the source; fast skips that check and is not verified"
        ),
    )
    p.add_argument("--password", default="", help="password for an encrypted PDF")
    p.add_argument(
        "--font-map",
        help="'source family = PowerPoint family' mapping file (one per line)",
    )
    p.add_argument(
        "--debug-assets",
        help="directory to write intermediate renders and extracted images into",
    )
    p.add_argument("--dpi", type=float, default=150.0, help="verification render dpi")
    p.add_argument(
        "--fallback-dpi", type=float, default=220.0, help="dpi for rendered fallbacks"
    )
    p.add_argument(
        "--vector-budget",
        type=int,
        default=VECTOR_BUDGET_PER_PAGE,
        help="native shapes per slide before vector artwork falls back to a render",
    )
    p.add_argument(
        "--no-tables", action="store_true", help="do not rebuild ruled tables natively"
    )
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.input):
        print("error: no such file: %s" % args.input, file=sys.stderr)
        return 2
    try:
        from .verify.render import page_count

        total = page_count(args.input, args.password)
    except Exception:
        total = None
    try:
        pages = parse_pages(args.pages, total)
    except ValueError as exc:
        print("error: --pages: %s" % exc, file=sys.stderr)
        return 2

    options = ConvertOptions(
        mode=args.mode,
        dpi=args.dpi,
        fallback_dpi=args.fallback_dpi,
        verify=args.mode == "fidelity",
        vector_budget=args.vector_budget,
        debug_assets=args.debug_assets,
        detect_tables=not args.no_tables,
    )
    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        result = convert(
            args.input,
            args.output,
            options=options,
            pages=pages,
            password=args.password,
            font_substitutions=load_substitutions(args.font_map),
            report_path=args.report,
        )
    except PasswordRequired as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3
    except UnparseableDocument as exc:
        print("error: %s" % exc, file=sys.stderr)
        print(
            "hint: the file is damaged beyond what either the parser or the "
            "renderer can recover; nothing was written.",
            file=sys.stderr,
        )
        return 4
    except Exception as exc:  # noqa: BLE001 - the CLI is the last line of defence
        print("error: conversion failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    if args.debug_assets:
        _write_debug_assets(result, args.debug_assets)

    if not args.quiet:
        _print_summary(result)
    return 0


def _print_summary(result) -> None:
    summary = result.report["summary"]
    print("wrote %s" % result.output_path)
    print(
        "  %d page(s), %d object(s)" % (summary["pages"], summary["elements"])
    )
    for outcome, count in sorted(summary["byOutcome"].items()):
        print("    %-22s %d" % (outcome, count))
    integrity = summary.get("textIntegrity")
    if integrity:
        state = "identical" if integrity["identical"] else "differs"
        print(
            "  text: %d source chars, %d in the deck (%s)"
            % (integrity["sourceChars"], integrity["outputChars"], state)
        )
    print(
        "  images: %d copied as-is, %d re-encoded losslessly"
        % (summary["imagesPassedThrough"], summary["imagesReEncoded"])
    )
    for warning in result.report["warnings"]:
        print("  warning: %s" % warning)


def _write_debug_assets(result, directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for asset in result.document.assets.values():
        path = os.path.join(directory, "%s.%s" % (asset.asset_id, asset.ext))
        with open(path, "wb") as fh:
            fh.write(asset.data)
    with open(os.path.join(directory, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(result.report, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
