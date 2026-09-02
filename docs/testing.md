# Testing

## Running the tests

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]' -c constraints.txt
.venv/bin/python -m pytest tests -q
```

The PDF corpus regenerates automatically when `fixtures/make_fixtures.py` is
newer than the fixtures. To rebuild it by hand:

```bash
.venv/bin/python fixtures/make_fixtures.py
```

Visual and round-trip tests need LibreOffice with Impress
(`libreoffice-impress`). Without it they **skip** — they do not silently pass.
Korean fixtures need a Korean TrueType font (`fonts-nanum` or
`fonts-noto-cjk`); without one the fixture generator falls back to the base-14
fonts and the Korean assertions will fail rather than quietly test nothing.

Last full run on this machine: **186 passed in 73 s** (Linux x86-64,
Python 3.11.15, LibreOffice 24.2, Impress + Nanum fonts installed).

## What each layer covers

| File | Tests | Covers |
|---|---|---|
| `test_units.py` | 16 | points ↔ EMU, matrix maths, rectangle algebra, page `/Rotate` direction, y-flip, crop offset, letterboxing |
| `test_extract.py` | 26 | subset prefixes and style suffixes, the site font map, Bézier control points surviving extraction, dash arrays, `/ExtGState` alpha, byte-identical JPEG passthrough, `/SMask` → alpha, asset de-duplication, paint-order uniqueness, shading detection, clip tracking, passwords, truncated files, a damaged page |
| `test_analyze.py` | 23 | line/rect/rotated-rect/ellipse/roundRect/freeform classification, the refusal to force a preset, multi-subpath handling, character loss, Korean+Latin spacing, multi-run lines, rotated text angle and point size, alignment inference, column splitting, baseline placement, line pitch, table grid, merges, per-edge borders, cell fills, cell text assignment, the refusal to invent a borderless table |
| `test_build.py` | 23 | alpha emission, dash preset round-tripping, rotation sign, control-character stripping, `custGeom` segment types and local coordinates, multi-subpath paths, per-edge `a:lnL/R/T/B`, merge flags, the no-grid table style, package shape, slide size, letterboxing, spTree order, JPEG-in-package, no inherited placeholders |
| `test_editability.py` | 17 | **mutation tests** — see below |
| `test_integration.py` | 34 | page→slide mapping, page selection, every element type produced, the non-destructive rules, the report's completeness, behaviour without a renderer, the CLI and its exit codes, no network access |
| `test_visual.py` | 14 | per-page perceptual comparison for six fixtures, every page of the multi-page fixtures, fallback pixel fidelity, scan fidelity, z-order in the overlap block, image colour drift, **and two negative controls** |
| `test_roundtrip.py` | 5 | PPTX → PDF → PPTX against a deck whose contents are known exactly |
| `test_ocr.py` | 14 | CER metric, Hangul pitch-based word joining (syllable grid, narrow syllables, real spaces), column gutters, reading order, noise policy (long low-confidence kept, short dropped, all recorded), engine discovery; with Tesseract installed: **CER < 5 % on the ground-truth Korean scan**, word boundaries within ±1.5/line, the main deck unchanged by OCR, the draft deck's shape (no bitmap, banner, notes), the CLI flag and its default-off |
| `test_report_schema.py` | 14 | every report validates against `docs/report-schema.json`; the schema rejects a fallback with its reason removed; absorbed objects point at a live object on the same page |

## Editability tests

These are the tests that answer "is this actually editable?" rather than "does
the XML look right". Each opens the produced deck, changes something a user
would change, saves, reopens, and asserts the change stuck.

- text content, size and colour rewritten
- several style runs in one paragraph keep their individual formatting
- a text box moved and resized
- shape fill colour, line colour and line width changed
- `a:custGeom` present with real `a:cubicBezTo` triples, and a vertex moved
  in place with the file still loading
- preset geometries reported as `rect` / `ellipse` / `roundRect` / `line`
- non-solid dashes and alpha present on the slide
- picture crop changed
- a source clip present as `a:srcRect`
- a picture's image replaced while its frame stays put
- every picture's placed aspect ratio matches the ratio the source page used,
  crop included (not the bitmap's pixel ratio — the fixture stretches one
  placement on purpose and reproducing that stretch is correct)
- a native `a:tbl` with the right row and column count
- cell text, cell fill and a new merge applied and re-read
- existing merges readable through PowerPoint's own `span_width` /
  `span_height` / `is_spanned` semantics
- an existing merge split again
- every cell states all four of its own edges

## Visual regression

Both sides are rendered at 150 dpi — the source through PDFium, the deck
through LibreOffice headless → PDF → PDFium — and compared on four independent
measures (`ink_missing`, `ink_added`, `edge_iou`, `mean_delta`). A single score
is not used, because a missing curve averages away against a large correct
background.

Both images are blurred slightly before comparison. Two rasterisers never agree
on glyph hinting or antialiasing; the question worth asking is whether the same
ink is in the same place at the same weight. Text uses a looser threshold
profile than geometry for the same reason.

**A metric that never fails is not a check**, so two negative controls run
alongside the real comparisons: one erases a third of the rebuilt slide, the
other shifts it ~7 pt down. Both must be rejected. If a future change loosens
the thresholds too far, these fail first.

Side-by-side PNGs land in `tests/_artifacts/*.compare.png` for every visual
test, so a failure is inspectable rather than just a number.

### Corpus results

Page-level metrics from the committed samples (150 dpi, page threshold
profile). Every page passes.

| Fixture | Pass | ink missing | ink added | edge IoU | mean Δ |
|---|---|---|---|---|---|
| chart | yes | 0.0000 | 0.0000 | 0.59 | 2.3 |
| clip_gradient | yes | 0.0000 | 0.0000 | 0.90 | 1.9 |
| damaged_page | yes | 0.0000 | 0.0004 | 0.96 | 2.0 |
| dense_vector | yes | 0.0000 | 0.0000 | 0.93 | 6.9 |
| encrypted | yes | 0.0000 | 0.0000 | 0.97 | 15.6 |
| images | yes | 0.0000 | 0.0000 | 0.88 | 2.0 |
| mixed_sizes | yes | 0.0000 | 0.0000 | 0.97 | 2.1 |
| rotated_pages | yes | 0.0000 | 0.0000 | 0.98 | 1.4 |
| scanned | yes | 0.0000 | 0.0000 | 0.99 | 1.8 |
| shapes | yes | 0.0007 | 0.0002 | 0.96 | 1.2 |
| table_borderless | yes | 0.0000 | 0.0000 | 0.89 | 11.5 |
| table_lattice | yes | 0.0009 | 0.0011 | 0.86 | 4.7 |
| text_mixed | yes | 0.0827 | 0.0839 | 0.55 | 39.0 |

`text_mixed` is the interesting row: it is nearly all Korean and Latin glyphs,
so the residual is font substitution and hinting, not misplaced objects. Its
edge IoU of 0.55 is the floor the text profile is calibrated against — see the
negative controls above for evidence that this is still a working check.

`chart`'s low edge IoU comes from 50 thin objects (axis ticks, gridlines, a
2.5 pt trend curve) whose one-pixel edges rarely land on the same pixel twice;
its ink and colour agreement are exact.

### Outcome counts

Across all 13 converted fixtures: **117 native, 4 raster-fallback, 1
page-fallback, 0 unsupported**, and text is character-identical on every one.

The five non-native outcomes are all deliberate:

| Fixture | Outcome | Why |
|---|---|---|
| clip_gradient | 3 × raster-fallback | two clipped vectors and one axial shading — no faithful DrawingML equivalent |
| dense_vector | 1 × raster-fallback | 1 200 shapes, over the per-slide budget |
| damaged_page | 1 × page-fallback | that page's content stream is unreadable |

## Round trip

`test_roundtrip.py` builds a PowerPoint deck with python-pptx — a title, a
multi-line body, a filled and stroked rectangle, an oval, and a 3×3 table with
header shading — exports it to PDF through LibreOffice, converts it back, and
checks the result against **the original deck**, not only against the PDF.

The rebuilt deck comes back with 11 native objects (2 text boxes, 4 rects, 2
ellipses, 2 pictures for the shapes' drop shadows, 1 native table), every
character preserved, no page fallback, and passes the perceptual comparison
against the original deck's own render.

This is the test that found the most real bugs, because a LibreOffice-authored
PDF does not look like a reportlab-authored one: rectangles arrive with extra
collinear vertices, ovals arrive as arcs mixed with straight segments, and the
slide background is a page-sized white rectangle that a naive table-fill rule
happily adopts as nine cell fills.

## Text placement calibration

The constant `DESCENT_EM = 0.21` in `analyze/text.py` was measured, not
guessed. A probe deck was rendered with `HHH` (no descenders, so the ink bottom
*is* the baseline) at several sizes with and without an explicit `lnSpc`:

| size | lnSpc | baseline below box top | as (L − k·size) |
|---|---|---|---|
| 9 | auto | 8.875 pt | −0.214 em |
| 9 | 11 | 9.125 pt | −0.208 em |
| 12 | auto | 11.875 pt | −0.210 em |
| 12 | 16 | 13.375 pt | −0.219 em |
| 12 | 24 | 21.375 pt | −0.219 em |
| 18 | auto | 17.875 pt | −0.207 em |
| 20 | 26 | 21.875 pt | −0.206 em |
| 30 | auto | 29.875 pt | −0.204 em |

The relationship `baseline = boxTop + L − 0.21·size` holds to about ±0.01 em
across the range (L is the line height: the explicit `lnSpc`, or 1.2·size when
absent). `test_analyze.py::test_text_box_is_placed_from_the_baseline` pins it.

This is LibreOffice's layout. PowerPoint's descent for its own substituted font
may differ slightly; the manual checklist below is how that gets confirmed.

## OCR accuracy

`fixtures/pdf/scanned_korean.pdf` is a typeset page (title, five lines of
Korean and one of English, a three-row ruled table) rendered at 200 dpi,
JPEG-compressed at quality 80, rotated 0.6° and speckled like a phone scan,
then wrapped back into a PDF. Its exact text sits beside it in
`scanned_korean.truth.txt`, so an engine is scored, not eyeballed:

```bash
.venv/bin/python tools/ocr_benchmark.py
```

For a hands-off run that produces one file to hand to a reviewer -- every
engine found, its score on the fixture, and how it did on a document of your
own -- use the report script instead:

```bash
.venv/bin/python tools/ocr_report.py --pdf your-scan.pdf --out ocr-report.md
```

It writes a single Markdown file with the environment, the engine table, the
truth compared line by line against each engine, and a sample of what each
read from your document. An engine that is missing or whose models will not
load is recorded as such and the run continues with the ones that work.

Tesseract 5.3.4, `kor+eng`, on that fixture:

| dpi | psm | words found | CER (whitespace ignored) |
|---|---|---|---|
| 300 | 3 (auto) | 73 | **0.633** — auto segmentation drops half the page |
| 300 | 4 (single column, variable sizes) | 134 | 0.141 |
| 300 | 11 (sparse text) | 138 | 0.169 |
| 400 | 4 | 137 | 0.121 |
| 400 | 11 | 148 | 0.129 |
| 400 | 4, with the noise policy that keeps long low-confidence words | — | **0.016** |

The last row is the shipped default. The jump from 0.121 to 0.016 is one
line: `BOT(Build-Operate-Transfer)이며` came back at confidence 8 and was
being discarded as noise; the policy now keeps a low-confidence word when it
is long enough to be real text and only drops short marks.

Word spacing is a separate problem from characters. Tesseract's Korean model
boxes most syllables one at a time, and a narrow syllable leaves an ink gap
inside a word as wide as a real space. Judging a space by the *pitch* between
successive left edges (Hangul sits on a one-em grid; a space adds a third of
an em) instead of by the ink gap brought the per-line space count from
`[5,10,11,7,17,6,2,13,9,8]` to `[5,5,6,2,5,6,2,13,9,7]` against a truth of
`[5,4,5,3,6,6,2,13,9,8]`.

On the real six-page scanned construction-plan proposal used during
development (no ground truth), Tesseract at 400 dpi, psm 4, `kor+eng`, with
the shipped noise policy:

| page | lines | words | mean confidence | low-confidence lines | dropped marks |
|---|---|---|---|---|---|
| 1 | 57 | 446 | 88 | 7 | 33 |
| 2 | 53 | 222 | 74 | 16 | 66 |
| 3 | 75 | 499 | 86 | 2 | 77 |
| 4 | 73 | 751 | 87 | 11 | 67 |
| 5 | 51 | 534 | 87 | 5 | 67 |
| 6 | 107 | 700 | 85 | 23 | 110 |

Whole document: 416 lines, 3 152 words, 5 025 characters, mean confidence
85.8, 46 s for six pages. These are the same per-page confidences the first
run produced, re-measured from a fresh clone on 2026-09-02 with
`tools/ocr_report.py`, so the pipeline is reproducible. Body text is readable
(`한국도로공사가 보유한 성토부의 가용자산을 활용하여 신재생에너지 생산 및
전기 보급` came back verbatim at 92); the errors are where they were before:
labels inside diagrams, letter-spaced headings (`사 업번 신청느 목목적즈` for a
spaced-out heading), and Latin inside Korean lines (`Transper` for
`Transfer`). Page 2, the lowest, is the page that is mostly a diagram.

### PaddleOCR status

**PaddleOCR is still not scored, and the reason is now pinned down rather
than assumed.** Re-attempted on 2026-09-02:

- `pip install paddlepaddle paddleocr` succeeds (PaddlePaddle 3.3.1,
  PaddleOCR 3.7.0; PyPI is reachable).
- The engine wrapper then failed *before* touching the network:
  `PaddleEngine` used the 2.x constructor arguments (`use_angle_cls`,
  `show_log`) and the 2.x `.ocr(cls=True)` call, which PaddleOCR 3.x rejects
  with `ValueError: Unknown argument: show_log`. `ocr.py` now uses the 3.x
  API (`use_textline_orientation`, `.predict()`, `rec_polys` / `rec_texts` /
  `rec_scores`) and falls back to the 2.x calls on an older install.
- With that fixed, PaddleOCR reaches its model download and stops there.
  Its own connectivity check reports every hoster unreachable — HuggingFace
  (`huggingface.co`), ModelScope (`modelscope.cn`), AIStudio
  (`aistudio.baidu.com`) and BOS (`paddle-model-ecology.bj.bcebos.com`) — and
  a direct `curl` to each gives the same answer: the outbound proxy answers
  `403` to the CONNECT for all four. `tools/ocr_report.py` records this as
  `failed` in the engine table and carries on with Tesseract.
- The one PyPI-only route to PP-OCR weights was also checked and does not
  help: the `rapidocr` wheel bundles PP-OCRv6 detection and recognition
  models, but its recognition dictionary contains no Hangul at all (37 415
  characters, 0 in U+AC00–U+D7A3); the Korean recogniser it lists is fetched
  from ModelScope, which is blocked.

To get the PaddleOCR column filled in, run the same command on a machine that
can reach one of those hosts, or copy an already-populated
`~/.paddlex/official_models/` (the `korean_PP-OCRv5_mobile_rec`,
`PP-OCRv5_mobile_det` and `PP-LCNet_x1_0_textline_ori` directories) into the
runner's home first. No code change is needed for either.

## What has NOT been tested

**A fresh clone cannot run the full suite.** `.gitignore` had an unanchored
`build/` rule, which also matched `src/pdf2editable_ppt/build/` — the package
that writes the deck (`pptx_writer`, `drawingml`, `ocr_deck`). That directory
was never committed, so `import pdf2editable_ppt` fails on a checkout that did
not originate on the development machine. The rule is now `/build/`; the
package itself still has to be added from a working copy that has it (`git
add src/pdf2editable_ppt/build`). Until then, only `test_ocr.py` has been
re-run from a clone (11 passed; the 3 failures are the ones that build a
deck), by stubbing the missing module at import time.

**Microsoft PowerPoint has never opened a file this tool produced.** No Windows
or macOS host was available in this environment. Every rendering check went
through LibreOffice. The "zero repair warnings" acceptance criterion is
therefore **unverified**.

### Manual PowerPoint checklist

Run this on the PowerPoint version your organisation actually uses, against
`samples/*.pptx`:

1. Open each deck. **Any "PowerPoint found a problem with content" repair
   prompt is a failure** — capture the repair log.
2. Compare each slide against the matching `fixtures/pdf/*.pdf` side by side at
   100 %. Look for shifted or wrapped text, missing shapes, changed colours,
   and clipped edges.
3. Click into a text box: the text is selectable, the runs keep their own
   colours and sizes, the font name is what you expect.
4. Right-click a freeform → **Edit Points**. Vertices and Bézier handles should
   appear.
5. Click a shape → Format: fill, line colour, dash and width should be editable
   and should already show the source's values.
6. Click a picture → Format → Crop: crop handles present; the crop already set
   on the clipped picture in `samples/images.pptx` should be visible.
7. Click the table in `samples/table_lattice.pptx`: it should be a real table
   (Table Design ribbon appears). Check the merged "Revenue" header and merged
   "APAC" cell, then change one cell's border and one cell's fill.
8. Save as `.pptx`, close, reopen. Nothing should change or warn.
9. Export to PDF from PowerPoint and diff that against the source PDF.

Record the results in this file. If step 1 fails anywhere, that is a bug in
this tool, not in the checklist.

### Adding an automated PowerPoint adapter

The intended shape is a Windows runner that drives PowerPoint through COM
(`Presentation.Open` with `Repair:=msoFalse` so a repair is an error rather
than a silent fix, then `ExportAsFixedFormat`), and feeds the resulting PDF
into the existing `verify/compare.py`. `verify/render.py` already isolates
rasterisation behind two functions, so the adapter slots in beside
`render_pptx_pages` without touching the comparison code.

## Other gaps in coverage

- PaddleOCR has never produced a result here; only Tesseract's numbers are
  real. See *PaddleOCR status* above for exactly where it stops.
- **SVG fallback is not implemented**, so nothing tests it.
- RTL text, vertical CJK text, JBIG2/CCITT images, blend modes, transparency
  groups and annotation appearance streams have no fixtures.
- No performance or memory test on large documents.
