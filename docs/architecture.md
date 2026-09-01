# Architecture

## Shape of the thing

```
PDF ──▶ extract ──▶ IR ──▶ analyse ──▶ build ──▶ verify ──▶ repair ──▶ PPTX
                     │                             │          │
                     └──────── report ◀────────────┴──────────┘
```

Ten stages, in the order they run:

1. **Preflight** — open the document, apply a password, decide whether it can
   be parsed at all.
2. **Classification** — born-digital or scanned, per page.
3. **Extraction** — walk each content stream, record every paint operation.
4. **IR** — a parser- and writer-independent description of the page.
5. **Analysis** — classify paths into shapes, group glyphs into text boxes,
   recover ruled tables.
6. **Build** — emit DrawingML into a PPTX package.
7. **Verification** — render both sides and compare.
8. **Fallback** — replace regions that did not match with a render of the
   source.
9. **Rebuild and re-verify** — one repair round, then re-measure.
10. **Report** — write down what every object became, and why.

## Why the stages are split this way

The whole design follows from one rule: *a rebuild that cannot be shown to be
right must not ship as a native object*. That forces two things. Extraction has
to keep enough information to reproduce the source exactly (so a fallback can
be exact), and the writer has to be re-runnable (so a failed region can be
swapped for a picture and the deck rebuilt). Everything else is consequence.

## Extraction (`extract/`)

`content.py` drives pdfminer.six with two subclasses:

- `_Device` overrides `paint_path`, `render_char` and `render_image` to record
  paint operations instead of laying them out. pdfminer's own `paint_path`
  flattens curves and splits subpaths; overriding it keeps the raw segment
  list, so `cubicBezierTo` reaches the IR with **both** control points.
- `_Interpreter` fills in what pdfminer leaves as stubs: `do_gs` reads `ca`/`CA`
  from `/ExtGState` (constant alpha), `do_W`/`do_W*` track the clip rectangle
  through `q`/`Q`, and `do_sh` records shadings that pdfminer would otherwise
  paint nowhere.

Two details matter downstream:

- **pdfminer already applies the page `/Rotate`** in `process_page`, using the
  MediaBox. Everything the extractor emits is therefore already in visually
  upright space. `pipeline._visualizer` relies on this and only has to fold in
  the CropBox, which pdfminer ignores.
- **Character point size comes from the text matrix**, not from `LTChar.size`.
  `LTChar.size` is the glyph box height in device space, so a rotated space
  measures 0 pt and a rotated digit measures its own width.

`images.py` implements the passthrough rule: a DCTDecode stream is handed to
PowerPoint byte for byte (`stream.get_data()` applies the transport filters and
leaves the JPEG alone), and anything else is decoded exactly once and written
as lossless PNG, with `/SMask` folded in as a real alpha channel. Both the
source and output SHA-256 go into the report.

## The IR (`ir.py`)

Coordinates are PDF points in *visual page space*: y-up, origin at the
bottom-left of the upright page. Nothing in `ir.py` imports pdfminer or
python-pptx.

```
Page(index, width_pt, height_pt, rotation, crop_box, background, elements[],
     scanned, degraded)

Element(id, type, bbox, transform, z_index, clip_path, opacity, style,
        content, source_asset_id, confidence, fallback_reason,
        source_paint_order, rotation_deg, outcome, notes, consumed,
        paint_bbox)
```

Element types: `text`, `image`, `line`, `rect`, `ellipse`, `freeform`, `table`,
`group`, `vectorFallback`, `rasterFallback`.

Two fields are easy to confuse and worth stating plainly:

- **`bbox` is what DrawingML needs.** For a rotated object that is the
  *unrotated* extent, positioned at the rotated centre, because DrawingML
  rotates a shape about the centre of its `a:ext`.
- **`paint_bbox` is where the ink lands** — the axis-aligned page-space bounds
  after rotation. Anything that crops pixels (fallback renders, visual checks)
  uses `render_bounds()`, never `bbox`.

`source_paint_order` is a per-operation counter from the extractor. Sorting by
it and appending to `spTree` in that order is how z-order survives.

## Analysis (`analyze/`)

**`shapes.py`** classifies a path, and refuses to guess. A shape becomes a
preset only when it can be *proven*:

- two-point open stroke → `line`
- four anchors forming a rectangle (after dropping repeated and collinear
  points) → `rect`, with the rotation reported separately
- four cubics whose anchors sit on the bbox edge midpoints at the kappa offsets
  → `ellipse`; failing that, any closed path whose anchors all satisfy the
  inscribed-ellipse equation and touch all four sides → `ellipse`
- four equal quarter-arc corners joined by straight edges → `roundRect`
- **everything else → `freeform`**, emitted as `a:custGeom` with the original
  Bézier control points

The collinear-point and inscribed-ellipse rules exist because real writers do
not draw the way a textbook does: PowerPoint-authored rectangles arrive with an
extra vertex mid-edge, and LibreOffice draws ovals as arcs mixed with straight
segments. A freeform is never wrong — it is the exact path — so any doubt
resolves to freeform.

**`text.py`** rebuilds glyphs into text boxes:

1. group by quantised rotation angle, then work in each group's unrotated frame
2. bucket into baselines
3. split each baseline at layout gutters (a pen gap over ~2.2 em) **and at the
   x positions of vertical rules the page actually draws**
4. split into runs wherever family, size, colour, weight or slant changes,
   inserting a space only where the *pen* jumped (origin + advance), never
   where two glyph boxes merely have a gap
5. group segments into blocks by vertical rhythm plus horizontal overlap or a
   shared left/centre/right edge
6. infer alignment from how the line boxes sit in the block
7. place the box so the **first baseline** lands where the PDF put it

That last step deserves its own note. DrawingML has no baseline control: a
top-anchored box puts its first baseline one line height below the top, less
the font's descent. Solving that for the box top is exact for the first line,
and every following line stays right because the measured pitch is written as
an explicit `lnSpc`. Sizing the box to the glyph boxes instead — the obvious
approach — drifts by the difference between the source font's ascent and the
substitute's. The constant is measured, not guessed, and pinned by a test.

Each source line becomes one paragraph, and `wrap="none"` is set, so the source
line breaks are preserved exactly and a slightly wider substitute font cannot
reflow the block.

**`tables.py`** only claims a table where the PDF *drew* one. It collects
rulings (lines, thin filled bars, and the four edges of stroked rectangles),
clusters them into lattices, snaps them to a grid, and requires at least half
the cell edges to be backed by a real rule. Merges come from missing interior
edges. Per-cell borders, fills, vertical anchor and content insets are read
back from the rules and fills that were actually painted. A fill only counts as
a cell's shading if it is contained in the table and close to the cell's own
size — otherwise a page-wide white background gets adopted as every cell's fill
and vanishes from the slide.

Tables without rulings are **left alone**. Inferring a grid from whitespace is
guesswork; the text boxes already reproduce them exactly.

## Build (`build/`)

`drawingml.py` produces XML fragments; `pptx_writer.py` assembles the package.
python-pptx owns content types, relationships and media parts — the things that
are tedious and easy to get wrong — while every shape is written as raw
DrawingML, because python-pptx's shape API cannot express per-edge table
borders, Bézier custom geometry, dash patterns, picture crops or constant
alpha.

Slides use the **Blank** layout with every inherited placeholder removed, so a
slide contains exactly the source's ink. Tables use the "No Style, No Grid"
built-in table style, so the only borders on a cell are the ones we wrote.

One deck has one slide size, so the deck takes the largest page and centres
smaller pages inside it at 1:1. Scaling pages to fit would change every
measured size and defeat the point of measuring them.

## Verification and fallback (`verify/`, `converter.py`)

After the first build, each slide is rendered (LibreOffice headless → PDF →
PDFium) and compared against a PDFium render of the source page.

`compare.py` reports four independent measures rather than one score, because a
single score hides exactly the failures that matter — a missing curve averages
away against a large correct background:

- `ink_missing` — painted source pixels with nothing painted over them
- `ink_added` — painted rebuilt pixels the source never painted
- `edge_iou` — overlap of the two edge maps, which catches shifted geometry
- `mean_delta` — mean colour difference over painted area

Both inputs are blurred slightly first. Two rasterisers never agree on glyph
hinting or edge antialiasing, and the question worth asking is "is the same ink
in the same place at the same weight", not "are these pixels identical". Text
gets its own, looser threshold profile for the same reason; shapes keep the
tight one.

When a page fails, each element's own region is scored and the failures are
marked. A failing region then:

1. grows to swallow every element it overlaps (repeatedly, until stable)
2. is rendered from the **source PDF** at the fallback DPI
3. replaces those elements — they are marked consumed, not left underneath

That last point is the rule against double-drawing. The region render already
contains everything painted there, so anything still native in that area would
show through it. If the region grows past most of the page, it becomes a page
fallback. The deck is rebuilt and re-verified once; if it still differs, the
report says so with the numbers.

If no PPTX renderer is available, nothing is claimed: every object drops from
`native` to `native-with-warning` and a warning explains that the visual check
did not run.

## Report (`report.py`)

Every object appears with its type, outcome, confidence, bbox, and — when it is
not native — the reason. Every image asset appears with its source and output
SHA-256 and whether the original stream was reused. Page-level verification
metrics and fallback regions are included. The schema is
`docs/report-schema.json`.

## Module map

| Path | Responsibility |
|---|---|
| `units.py` | points ↔ EMU, matrices, rectangles, page geometry |
| `ir.py` | the intermediate representation |
| `extract/content.py` | content-stream walk; alpha, clip and shading recovery |
| `extract/images.py` | image XObjects, raw-stream passthrough, soft masks |
| `extract/colors.py` | PDF colour operands → sRGB |
| `extract/fonts.py` | subset prefixes, style suffixes, site font map |
| `analyze/shapes.py` | path → line/rect/roundRect/ellipse/freeform |
| `analyze/text.py` | glyphs → runs → lines → text boxes |
| `analyze/tables.py` | lattice tables and their per-cell style |
| `build/drawingml.py` | DrawingML fragments |
| `build/pptx_writer.py` | package assembly and element emission |
| `verify/render.py` | PDF and PPTX rasterisation |
| `verify/compare.py` | the four metrics and their thresholds |
| `pipeline.py` | raw records → IR, classification, shape budget |
| `converter.py` | the driver: build, verify, repair, report |
| `report.py` | report construction |
| `cli.py` | command line |
