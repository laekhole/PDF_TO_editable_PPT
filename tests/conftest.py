"""Shared fixtures.

The PDF corpus is generated on demand (``fixtures/make_fixtures.py``) and the
conversions are cached per session, because a fidelity-mode conversion runs
LibreOffice and is far too slow to repeat per test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, Optional

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FIXTURE_DIR = os.path.join(ROOT, "fixtures", "pdf")
ARTIFACT_DIR = os.path.join(ROOT, "tests", "_artifacts")


def _ensure_fixtures() -> None:
    marker = os.path.join(FIXTURE_DIR, "text_mixed.pdf")
    script = os.path.join(ROOT, "fixtures", "make_fixtures.py")
    newest_source = os.path.getmtime(script)
    if os.path.exists(marker) and os.path.getmtime(marker) >= newest_source:
        return
    subprocess.run(
        [sys.executable, script, FIXTURE_DIR], check=True, capture_output=True
    )


@pytest.fixture(scope="session", autouse=True)
def fixtures_built() -> None:
    _ensure_fixtures()
    os.makedirs(ARTIFACT_DIR, exist_ok=True)


def fixture_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, name + ".pdf")


@pytest.fixture(scope="session")
def have_renderer() -> bool:
    from pdf2editable_ppt.verify.render import find_soffice

    return find_soffice() is not None


class _ConversionCache:
    def __init__(self) -> None:
        self._cache: Dict[tuple, object] = {}

    def get(self, name: str, verify: bool = False, password: str = "", **kwargs):
        from pdf2editable_ppt.converter import convert
        from pdf2editable_ppt.pipeline import ConvertOptions

        key = (name, verify, password, tuple(sorted(kwargs.items())))
        if key in self._cache:
            return self._cache[key]
        suffix = "verified" if verify else "fast"
        out = os.path.join(ARTIFACT_DIR, "%s.%s.pptx" % (name, suffix))
        report = os.path.join(ARTIFACT_DIR, "%s.%s.report.json" % (name, suffix))
        options = ConvertOptions(
            mode="fidelity" if verify else "fast", verify=verify, **kwargs
        )
        result = convert(
            fixture_path(name),
            out,
            options=options,
            password=password,
            report_path=report,
        )
        self._cache[key] = result
        return result


@pytest.fixture(scope="session")
def conversions() -> _ConversionCache:
    return _ConversionCache()
