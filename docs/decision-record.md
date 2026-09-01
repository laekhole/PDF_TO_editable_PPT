# Architecture decision record

## ADR-001 — Build on a permissive Python stack rather than adapt GenOffice

**Date** 2026-09-01 · **Status** Accepted

### Context

The brief named GenOffice (`genspark-ai/genoffice`, Apache-2.0) as the primary
hypothesis and required verification against the source and the real output
rather than the README.

GenOffice was cloned at commit
`3548a808170616c2b13b1dc7c6c81cb49205d504`, built, its PPTX test suite run
(7 passing), and a probe PDF converted through its own public entry point.

### What the evidence showed

The probe contained seven objects. The produced slide contained three: a
rectangle with its stroke dropped, a text box, and a picture whose source JPEG
had been re-encoded to PNG. A cubic Bézier, a dashed diagonal, an ellipse and a
translucent rectangle were **silently absent** — no warning, no report entry.

Reading the source showed this is structural rather than a defect:

- `packages/pdf2docx/src/ir.ts` stores subpaths as flat point lists with a
  `hasCurves` boolean. Bézier control points never enter the IR.
- `packages/pdf2docx/src/analyze/vector.ts` exists specifically to find curved
  regions and hand them to the extraction layer *to rasterise*.
- `packages/pdf2docx/src/rebuild-pptx/rebuild.ts` emits four things: text
  boxes, axis-aligned rects, pictures, and grid tables.
- `packages/pptx-engine/src/insert.ts`'s `NewTableCellSpec` has no per-edge
  border fields, and `rebuild-pptx/table.ts` writes one uniform border for the
  whole table.
- No render-compare-fallback stage exists anywhere in the path.

### Decision

Build the converter on **pdfminer.six + pypdfium2 + python-pptx + lxml**, and
do not adopt GenOffice as the engine.

### Reasoning

Adopting it would have meant replacing its extraction layer (to keep control
points, dash, alpha and clip), replacing its PPTX rebuild layer (to emit shapes
at all), extending its table writer (per-edge borders), and adding a
verification stage — i.e. keeping the layout heuristics and rewriting
everything the brief actually asks for, inside a 70 MB Electron monorepo whose
tooling (TypeScript, wasm PDFium, vitest) the receiving team would then own.

The capability test that settled it was run before any converter code existed:
pdfminer's `paint_path` hands back real `('c', x1,y1, x2,y2, x3,y3)` segments,
dash arrays, and colours, and `PDFStream.get_data()` returns a DCTDecode
stream's JPEG bytes untouched. Two gaps (`do_gs` and `do_W` are stubs) were
closable in about eighty lines. That is a much smaller delta than the
GenOffice one.

### Consequences

- Every runtime dependency is permissive; no AGPL/GPL question to escalate.
- The layout analysis is much shallower than GenOffice's — no column detection,
  zones, panels, footnote or list reconstruction. This is a real loss, and the
  mitigation is that unrecognised structure stays as exactly-positioned text
  boxes rather than being mis-structured.
- Python, not TypeScript. Better for the imaging and test tooling here; worse
  if this ever has to run inside an Electron app.
- **Revisit if**: the layout analysis becomes the bottleneck. GenOffice's
  Apache-2.0 licence permits vendoring specific analysis passes at a pinned
  commit; `docs/license-review.md` lists what that would require.

---

## ADR-002 — Verify by rendering, and fall back per region

**Date** 2026-09-01 · **Status** Accepted

### Context

The brief's top priority is source fidelity, ahead of editability. Every
converter in this space has the same failure mode: it emits a native object
that is subtly wrong, and nobody notices until a meeting.

### Decision

Build a candidate deck, render it, compare it against a render of the source,
and replace anything that does not match with a render of the source region.
Record every outcome.

### Reasoning

Confidence heuristics alone are not enough — they measure how sure the
*classifier* was, not whether the result looks right. Rendering is the only
check that measures the thing the requirement is about.

Comparison uses four independent measures rather than one score, because a
single score hides exactly what matters here: a missing curve averages away
against a large correct background. Both sides are blurred slightly first, and
text gets its own looser profile, because two rasterisers never agree on glyph
hinting and treating that as failure would send every page of text to a raster.

### Consequences

- Conversion takes seconds per document rather than milliseconds, and needs
  LibreOffice. `--mode fast` skips it and is labelled unverified.
- Without a renderer, every object drops to `native-with-warning`. The tool
  never claims fidelity it did not measure.
- The thresholds are corpus-calibrated and will need re-tuning on real
  documents. They live in one file and every measurement is in the report.
- Two negative controls in the test suite (an erased third, a 7 pt shift) keep
  a future loosening honest.

---

## ADR-003 — A fallback replaces its region; it never underlays it

**Date** 2026-09-01 · **Status** Accepted

### Context

The obvious way to fix a bad shape is to draw a picture of it on top. That
produces double-drawn ink wherever the picture is not fully opaque or does not
exactly cover what is underneath — the brief forbids it explicitly.

### Decision

A failing region grows until it covers every element it overlaps (repeatedly,
until stable), is rendered from the source PDF, and **consumes** those
elements. If it grows past most of the page it becomes a page fallback.

### Reasoning

The region render already contains everything the source painted there, so
anything left native in that area would show through it. Growing the region to
a fixed point is the only rule that guarantees no overlap without a per-object
transparent render, which would require rebuilding a PDF containing just that
object.

### Consequences

- One bad object can take its neighbours down with it. The report names both
  the anchor and every element it absorbed, so the cost is visible.
- Editability is lost over the whole region, not just the bad object. This is
  the accepted price of the priority order in the brief.
- A test asserts that no live element is ≥90 % covered by a raster fallback.

---

## ADR-004 — Only rebuild tables the PDF drew rules for

**Date** 2026-09-01 · **Status** Accepted

### Context

Much of the value of a "editable PPTX" is a real table. Many real tables have
no rulings at all.

### Decision

Rebuild lattice tables only. Require at least half of the candidate grid's cell
edges to be backed by a drawn rule. Leave whitespace-aligned tables as text
boxes.

### Reasoning

A whitespace-inferred grid is a guess, and a wrong grid is a damaged slide that
takes longer to fix than retyping. The text boxes already reproduce a
borderless table exactly — the reader sees the right thing, and only the
editing affordance is missing.

### Consequences

- Borderless tables come back as a grid of text boxes. Documented in
  `docs/limitations.md` and asserted by a test.
- The confidence (edge coverage) is in the report, so a low-confidence table is
  visible rather than implied.
- **Revisit if** real documents turn out to be mostly borderless. The upgrade
  path is to detect the structure, build the table, and let the existing
  verification decide whether to keep it — the machinery is already there.

---

## ADR-005 — Never embed fonts; expose a font map instead

**Date** 2026-09-01 · **Status** Accepted

### Context

Text fidelity would be best served by embedding the source's fonts. The brief
says not to embed when embedding permission is unclear.

### Decision

Never embed. Write family names, and provide `--font-map` so an operator can
route source families onto fonts the organisation has installed.

### Reasoning

A PDF's embedded font subset does not carry a reliable statement of embedding
permission, and the tool cannot make that call. The font map puts the decision
where the knowledge is.

### Consequences

- Text metrics differ on machines without the source fonts, and the visual
  check may push a text block to a fallback. The font map is the fix.
- Documented as the highest-value knob for real documents.

---

## ADR-006 — Preset only when proven; otherwise custom geometry

**Date** 2026-09-01 · **Status** Accepted

### Context

Preset shapes are nicer to edit than freeforms. The temptation is to snap
anything roughly rectangular to `rect`.

### Decision

Emit a preset only when the path *proves* it: a rectangle after dropping
repeated and collinear anchors, an ellipse either from four kappa-fitted cubics
or from anchors that all satisfy the inscribed-ellipse equation and touch all
four sides, a rounded rectangle from four equal corner arcs. Everything else is
`a:custGeom` with the original control points.

### Reasoning

A freeform is never wrong — it is the exact path — so every ambiguous case
resolves there at zero fidelity cost. The two "loose" recognisers exist because
real writers do not draw the way a textbook does: PowerPoint-authored
rectangles arrive with an extra mid-edge vertex, and LibreOffice draws ovals as
arcs mixed with straight segments. Both were found by the round-trip test, and
both are still proofs rather than guesses.

### Consequences

- Logos, outlined glyphs and chart paths stay freeforms, which is correct.
- Confidence is recorded per shape so a marginal recognition is visible.
- `a:custGeom` cannot express an even-odd fill rule; the flag is kept in the IR
  and the limitation is documented.
