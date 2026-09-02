# pdf2editable-ppt

Converts a PDF into a PowerPoint deck of **native, editable objects** —
without damaging the original artwork. One PDF page becomes one slide.

The governing rule is one sentence:

> Nothing is damaged in order to make it editable. Anything that cannot be
> rebuilt safely is kept as a faithful render of the source, not shipped as a
> broken native object — and the report always says which is which.

Everything runs locally. No network calls, no telemetry, no external service.

## What you actually get

| Source object | Result | Support |
|---|---|---|
| Text | Editable text box, one paragraph per source line, per-run family / size / colour / bold / italic, alignment and rotation preserved | **Good** |
| Bitmap images | Independent PowerPoint picture. JPEG streams are reused **byte for byte**; anything else is decoded once and written as lossless PNG. Position, size, rotation, crop, transparency and replacement are all editable | **Good** |
| Lines, rectangles, rounded rectangles, ellipses | Preset shapes with fill, stroke colour, stroke width, dash and alpha | **Good** |
| Curves, polygons, arbitrary paths | `a:custGeom` freeform with the original Bézier control points — editable via Edit Points | **Good** |
| Ruled tables | Native PowerPoint table: rows, columns, merges, per-cell fills, **per-edge borders**, alignment and cell margins | **Good** |
| Borderless tables | Left as exactly-positioned text boxes. A grid is not invented from whitespace | **By design** |
| Gradients, tiling patterns | Rendered region, reported | **Fallback** |
| Clipped vectors | Rendered region, reported | **Fallback** |
| Very dense vector artwork | Rendered region above a per-slide shape budget, reported | **Fallback** |
| Scanned pages | Bitmap kept intact, no text invented over it | **Fallback by design** |
| OCR for scans | `--ocr experimental`: local Tesseract reads the scan into a **separate** draft deck plus a JSON sidecar; the main deck is untouched. Korean CER 1.6 % on the ground-truth fixture; real scans are noisier | **Experimental** |
| Charts | Editable lines, shapes and text. **Not** a native PowerPoint chart — the data does not exist in a PDF | **Partial, honestly** |
| SmartArt, groups, themes, chart data, table styles | Not recoverable from a PDF by any tool | **Not supported** |
| SVG fallback | **Not implemented** — only raster fallback exists | **Not supported** |

Read `docs/limitations.md` before trusting this with a document that matters.

## Install

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/bin/pip install -e . -c constraints.txt
```

For the fidelity check and the visual tests, install LibreOffice with Impress:

```bash
sudo apt-get install -y libreoffice-impress          # Debian/Ubuntu
brew install --cask libreoffice                      # macOS
```

For the test suite and the fixture generator:

```bash
.venv/bin/pip install -e '.[dev]' -c constraints.txt
sudo apt-get install -y fonts-nanum                  # Korean fixtures
```

## Use

```bash
pdf2editable-ppt input.pdf -o output.pptx
pdf2editable-ppt input.pdf -o output.pptx --report output.report.json
pdf2editable-ppt input.pdf -o output.pptx --pages 1-5
pdf2editable-ppt input.pdf -o output.pptx --mode fidelity
pdf2editable-ppt input.pdf -o output.pptx --debug-assets ./debug-assets
pdf2editable-ppt scan.pdf  -o scan.pptx  --ocr experimental
```

`--mode fidelity` is the default. It renders the produced deck, compares it
against the source, and replaces anything that does not match with a render of
the source region.

| Option | Meaning |
|---|---|
| `-o, --output` | destination `.pptx` (required) |
| `--report PATH` | write the JSON conversion report |
| `--pages SPEC` | `1-5`, `1,3,7-9`, `2-` |
| `--mode {fidelity,fast}` | `fast` skips the visual check; its output is **not verified** |
| `--password PW` | for an encrypted PDF |
| `--font-map PATH` | `source family = PowerPoint family`, one per line |
| `--dpi N` | verification render resolution (default 150) |
| `--fallback-dpi N` | resolution of rendered fallbacks (default 220) |
| `--vector-budget N` | native shapes per slide before vector art falls back (default 900) |
| `--no-tables` | do not rebuild ruled tables natively |
| `--debug-assets DIR` | dump extracted images and the report |
| `--ocr {off,experimental}` | run local OCR on scanned pages into `<output>.ocr.pptx` + `<output>.ocr.json` (default off) |
| `--ocr-engine {auto,tesseract,paddleocr}` | engine; `auto` takes the first one installed |
| `--ocr-lang LANGS` | `+`-separated Tesseract languages (default `kor+eng`) |
| `--ocr-dpi N` / `--ocr-psm N` | OCR render dpi (400) and Tesseract segmentation mode (4) |
| `--ocr-all-pages` | OCR every page, not only the ones classified as scans |
| `-q, --quiet` | errors only |

Exit codes: `0` success · `1` conversion failed · `2` bad arguments ·
`3` wrong or missing password · `4` file unreadable by parser and renderer.

### Font map

The single highest-value knob for real documents. The converter **never embeds
fonts** (it cannot know a font's embedding permission), so it writes family
names and PowerPoint resolves them. Route them at your installed fonts:

```ini
# fonts.map
nanumgothic   = Malgun Gothic
helvetica     = Arial
timesnewromanps = Times New Roman
```

```bash
pdf2editable-ppt deck.pdf -o deck.pptx --font-map fonts.map
```

### Experimental OCR

A scanned page has no text to recover, only pixels. `--ocr experimental` runs
**Tesseract locally** (nothing leaves the machine) and writes what it read to a
*separate* draft deck: one slide per scanned page with editable text boxes at
the positions the words were read from, on a blank background, low-confidence
lines in red, and the full text with confidences in the speaker notes. A JSON
sidecar carries every word, its box and its confidence, plus what was
discarded as noise.

The scan itself is never painted over, and the main deck is byte-for-byte the
same with or without `--ocr`. The draft is a starting point for a person to
correct, not a reconstruction of the page.

Install the engine first:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng
```

Measured on the ground-truth Korean scan fixture (`tools/ocr_benchmark.py`):
character error rate **1.6 %** (whitespace ignored), word boundaries within
±1 per line. Real scans with mixed graphics score lower; see `docs/testing.md`.
PaddleOCR has an adapter but could not be evaluated here (its model hosts
were unreachable), so it is unverified. To score every engine on your own
machine in one command:

```bash
.venv/bin/python tools/ocr_report.py --pdf your-scan.pdf --out ocr-report.md
```

## The report

Every object appears with what it became and, when it is not native, why:

```json
{
  "id": "p1-shp4",
  "type": "rasterFallback",
  "outcome": "raster-fallback",
  "confidence": 0.3,
  "bboxPt": [89.0, 519.0, 251.0, 681.0],
  "fallbackReason": "the path is clipped; a native shape would paint outside the source's clip region"
}
```

Outcomes are `native`, `native-with-warning`, `svg-fallback`,
`raster-fallback`, `page-fallback` and `unsupported`. The schema is
`docs/report-schema.json`, and the schema *requires* a `fallbackReason` on
every non-native outcome — silent failure is not representable.

A source object that another object took over — a ruling absorbed into a
table, an element replaced by a fallback region — is not dropped from the
report. It moves to the page's `absorbed` list with an `absorbedBy` pointing at
whatever adopted it, so nothing disappears without a paper trail.

The report also records, per image asset, the source and output SHA-256 and
whether the original compressed stream was reused unchanged.

## Verification

In fidelity mode each slide is rendered (LibreOffice → PDF → PDFium) and
compared against a PDFium render of the source page on four independent
measures: missing ink, added ink, edge overlap, and mean colour difference. A
single score is not used, because a missing curve averages away against a large
correct background.

Where a region does not match, it — and everything painted in it — is replaced
by a render of the source. Nothing is drawn twice.

**If LibreOffice is not available the check does not run**, and every object is
reported as `native-with-warning` with an explanatory warning. The tool never
claims fidelity it did not measure.

## Samples

`samples/` holds a converted `.pptx` and report for each fixture in
`fixtures/pdf/`. Regenerate the corpus with
`python fixtures/make_fixtures.py`.

Across the 13 converted fixtures: **117 native objects, 4 raster fallbacks, 1
page fallback, 0 unsupported**, with character-identical text on every one.
Numbers and the metric table are in `docs/testing.md`.

## Status

Verified: 186 automated tests pass, including editability mutation tests,
visual regression against the source, and a PPTX → PDF → PPTX round trip.

**Not verified: Microsoft PowerPoint has never opened a file this tool
produced.** No Windows or macOS host was available. All rendering checks went
through LibreOffice. `docs/testing.md` has the manual checklist that closes
this gap, and it should be run before the tool is used on anything real.

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | the pipeline, the IR, and why each stage exists |
| [`docs/oss-evaluation.md`](docs/oss-evaluation.md) | what was evaluated, what was run, and the evidence |
| [`docs/decision-record.md`](docs/decision-record.md) | six ADRs, with their consequences and revisit conditions |
| [`docs/limitations.md`](docs/limitations.md) | every real boundary, including what is unimplemented |
| [`docs/testing.md`](docs/testing.md) | coverage, corpus results, calibration, PowerPoint checklist |
| [`docs/license-review.md`](docs/license-review.md) | dependency licences and the open questions |
| [`docs/report-schema.json`](docs/report-schema.json) | JSON Schema for the report |

## Licence

Apache-2.0 — see [`LICENSE`](LICENSE). All runtime dependencies are permissive
(MIT, BSD or Apache-2.0); no GPL or AGPL code is used. See
[`docs/license-review.md`](docs/license-review.md).
