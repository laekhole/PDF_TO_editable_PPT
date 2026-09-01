# Licence review

**Status: draft for legal/OSS-compliance review. Not a legal opinion.** The
facts below were read from the installed package metadata on 2026-09-01; the
conclusions are engineering conclusions and still need sign-off.

## Why this document exists

The brief is explicit that "internal use, not sold" does **not** excuse
skipping a GPL/AGPL review. Copyleft obligations can attach to internal
distribution and, for AGPL, to network-accessible use — neither of which is
settled by a decision to not sell the tool. So the design decision was to
avoid the question entirely: every runtime dependency is permissive.

## Runtime dependencies (shipped or installed with the tool)

| Package | Version | Licence | Copyleft? |
|---|---|---|---|
| `pdfminer.six` | 20260107 | MIT | No |
| `pdfplumber` | 0.11.10 | MIT | No |
| `pypdfium2` | 5.13.0 | BSD-3-Clause / Apache-2.0 (wheels bundle PDFium: BSD-3-Clause) | No |
| `python-pptx` | 1.0.2 | MIT | No |
| `Pillow` | 12.3.0 | MIT-CMU (HPND) | No |
| `lxml` | 6.1.2 | BSD-3-Clause (bundles libxml2/libxslt: MIT) | No |
| `numpy` | 2.4.6 | BSD-3-Clause | No |
| `charset-normalizer` | 3.5.1 | MIT | No |
| `cryptography` | 50.0.1 | Apache-2.0 OR BSD-3-Clause | No |
| `cffi` | 2.1.1 | MIT-0 | No |
| `pycparser` | 3.0 | BSD-3-Clause | No |
| `typing_extensions` | 4.16.0 | PSF-2.0 | No |
| `XlsxWriter` | 3.2.9 | BSD-2-Clause | No |

`XlsxWriter` and `Pygments` arrive as transitive dependencies of `pdfplumber`;
both are permissive. No GPL, LGPL or AGPL package is present.

Verify at any time with:

```bash
.venv/bin/python -c "import importlib.metadata as m; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or d.metadata.get('License')) for d in m.distributions()]"
```

## Development and test dependencies (not shipped)

| Package | Licence | Notes |
|---|---|---|
| `pytest` | MIT | tests only |
| `reportlab` | BSD-3-Clause | generates the fixture PDFs; not needed to run the converter |

## External tools invoked, not linked

| Tool | Licence | How it is used | Obligation |
|---|---|---|---|
| **LibreOffice** (`soffice --headless`) | MPL-2.0 | Renders the produced PPTX during the verification step and in the visual tests | Executed as a separate process. It is neither bundled nor linked, and no LibreOffice code is incorporated. MPL-2.0 is file-level copyleft and does not reach our sources. The tool degrades gracefully when it is absent. |

If LibreOffice is ever **bundled** with a distribution of this tool, that is a
different question and needs its own review.

## Fonts

The converter **does not embed fonts** into the output. It writes font family
names and lets PowerPoint resolve them. That is a deliberate licensing choice:
embedding requires knowing the font's licence permits it, and the tool cannot
determine that from a PDF. If font embedding is ever added, each family needs
its own permission check first.

The fixture generator uses whatever Korean font is installed on the build
machine (NanumGothic — SIL OFL 1.1 — or Noto Sans CJK — SIL OFL 1.1). No font
files are committed to this repository.

## Evaluated and not used

| Project | Licence | Why not used |
|---|---|---|
| **GenOffice** | Apache-2.0 | Rejected on capability grounds, not licensing (see `docs/oss-evaluation.md`). **No GenOffice code is present in this repository.** Apache-2.0 would permit vendoring specific analysis passes later; doing so would require keeping the `LICENSE` and `NOTICE`, adding attribution, stating changes, and not using the "GenOffice" or Mainfunc marks. The `ee/` directory was never read or used. |
| **PyMuPDF** and tools built on it | AGPL-3.0 or commercial | **Not evaluated, by decision.** AGPL obligations for internal and network use have not been assessed by this organisation, and the brief forbids waving that away. This is the one open licence question: if the operator obtains a commercial licence or an AGPL assessment, PyMuPDF's extraction is materially better than pdfminer's and would be worth revisiting. |
| **Poppler** (`pdftoppm`, `pdftocairo`) | GPL-2.0/GPL-3.0 | Not used. `pypdfium2` covers rendering under a permissive licence. |
| **MinerU2PPT** | unclear at review time | No code read with reuse in mind, none taken. |

## This project's own licence

**Apache-2.0**, declared in `pyproject.toml` and committed as `LICENSE`.

## Open questions for review

1. Confirm that MPL-2.0 LibreOffice invoked as a subprocess raises no
   obligation under the organisation's OSS policy.
2. Decide whether PyMuPDF is available under a licence the organisation
   accepts. If yes, revisit the extraction backend.
3. Confirm Apache-2.0 is the right licence for this project (it is currently
   declared and committed on the assumption that it is).
4. If any GenOffice code is vendored later, run the Apache-2.0 attribution
   checklist above and record the exact commit.
