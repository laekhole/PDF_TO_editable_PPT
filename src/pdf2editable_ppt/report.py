"""Conversion report.

Every object the converter touched appears here with what it became and, when
it is not a native PowerPoint object, why.  The rule the report enforces is
that a failure is never silent: an element that fell back, an image that was
re-encoded, a dash that was approximated -- all of it is written down.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir import Document, Element, ElementType, Page

SCHEMA_VERSION = "1.0"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _element_entry(el: Element) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": el.id,
        "type": el.type.value,
        "outcome": el.outcome.value,
        "confidence": round(el.confidence, 3),
        "zIndex": el.z_index,
        "sourcePaintOrder": el.source_paint_order,
        "bboxPt": [round(v, 2) for v in el.bbox.as_tuple()],
    }
    if abs(el.rotation_deg) > 0.01:
        entry["rotationDeg"] = round(el.rotation_deg, 2)
    if el.opacity < 0.999:
        entry["opacity"] = round(el.opacity, 3)
    if el.source_asset_id:
        entry["assetId"] = el.source_asset_id
    if el.fallback_reason:
        entry["fallbackReason"] = el.fallback_reason
    if el.notes:
        entry["notes"] = list(el.notes)
    if el.type is ElementType.TEXT and el.content is not None:
        entry["textChars"] = len(el.content.text)
        entry["lines"] = len(el.content.lines)
    if el.type is ElementType.TABLE and el.content is not None:
        entry["table"] = {
            "rows": el.content.rows,
            "cols": el.content.cols,
            "mergedCells": sum(
                1
                for c in el.content.cells
                if c.merged_by is None and (c.row_span > 1 or c.col_span > 1)
            ),
        }
    if el.type is ElementType.FREEFORM and el.content is not None:
        entry["segments"] = len(el.content.segments)
        entry["hasCurves"] = el.content.has_curves()
    return entry


@dataclass
class PageReport:
    index: int
    width_pt: float
    height_pt: float
    rotation: int
    scanned: bool = False
    degraded: bool = False
    degraded_reason: Optional[str] = None
    verification: Optional[Dict[str, Any]] = None
    fallback_regions: List[Dict[str, Any]] = field(default_factory=list)


class ReportBuilder:
    def __init__(self, source_path: str, options: Dict[str, Any]) -> None:
        self.source_path = source_path
        self.options = options
        self.page_reports: Dict[int, PageReport] = {}
        self.warnings: List[str] = []

    def page(self, page: Page) -> PageReport:
        pr = self.page_reports.get(page.index)
        if pr is None:
            pr = PageReport(
                index=page.index,
                width_pt=page.width_pt,
                height_pt=page.height_pt,
                rotation=page.rotation,
                scanned=page.scanned,
                degraded=page.degraded,
                degraded_reason=page.degraded_reason,
            )
            self.page_reports[page.index] = pr
        return pr

    def build(
        self,
        document: Document,
        output_path: str,
        text_check: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        by_outcome: Dict[str, int] = {}
        pages: List[Dict[str, Any]] = []
        total_elements = 0
        for page in document.pages:
            pr = self.page(page)
            entries = []
            for el in page.elements:
                if el.consumed and el.type is not ElementType.TABLE:
                    continue
                entry = _element_entry(el)
                by_outcome[entry["outcome"]] = by_outcome.get(entry["outcome"], 0) + 1
                entries.append(entry)
                total_elements += 1
            page_entry: Dict[str, Any] = {
                "index": page.index,
                "pageNumber": page.index + 1,
                "widthPt": round(page.width_pt, 2),
                "heightPt": round(page.height_pt, 2),
                "rotation": page.rotation,
                "scanned": page.scanned,
                "degraded": page.degraded,
                "elements": entries,
            }
            if page.degraded_reason:
                page_entry["degradedReason"] = page.degraded_reason
            if pr.verification is not None:
                page_entry["verification"] = pr.verification
            if pr.fallback_regions:
                page_entry["fallbackRegions"] = pr.fallback_regions
            pages.append(page_entry)

        assets = [
            {
                "assetId": a.asset_id,
                "format": a.ext,
                "widthPx": a.width_px,
                "heightPx": a.height_px,
                "passthrough": a.passthrough,
                "sourceSha256": a.source_sha256,
                "outputSha256": a.output_sha256,
                "hasAlpha": a.has_alpha,
                "note": a.note,
            }
            for a in document.assets.values()
        ]

        report: Dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "tool": {"name": "pdf2editable-ppt", "version": _version()},
            "source": {
                "path": os.path.abspath(self.source_path),
                "sha256": sha256_file(self.source_path),
                "pages": len(document.pages),
            },
            "output": {
                "path": os.path.abspath(output_path),
                "sha256": sha256_file(output_path) if os.path.exists(output_path) else "",
            },
            "options": self.options,
            "summary": {
                "pages": len(document.pages),
                "elements": total_elements,
                "byOutcome": by_outcome,
                "imagesPassedThrough": sum(1 for a in document.assets.values() if a.passthrough),
                "imagesReEncoded": sum(
                    1 for a in document.assets.values() if not a.passthrough
                ),
            },
            "warnings": list(document.warnings) + list(self.warnings),
            "assets": assets,
            "pages": pages,
        }
        if text_check is not None:
            report["summary"]["textIntegrity"] = text_check
        return report


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("pdf2editable-ppt")
    except Exception:
        return "0.1.0"


def write_report(report: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
