# Limitations

Read this before trusting the tool with a document that matters. Everything
here is a real boundary, not a caveat about polish.

## What PDF cannot tell us

A PDF is a description of marks on a page. These things were destroyed when
the deck was exported and **cannot be recovered by any tool**, this one
included:

- **Group and hierarchy relationships.** Shapes come back as independent
  objects.
- **SmartArt.** It exports as ordinary shapes and text; it comes back as
  ordinary shapes and text.
- **Chart data and series.** A chart is lines, shapes and labels in the PDF.
  This tool rebuilds it as an editable set of lines, shapes and labels — it
  does **not** produce a native PowerPoint chart, because there is no data
  behind it and a chart object with invented numbers would be a lie you could
  not see.
- **Table style names**, banding rules, and theme table styles. Only the
  painted result survives; the rebuilt table carries explicit borders and
  fills, not a style.
- **The theme.** Colours come back as literal `srgbClr`, not theme references,
  so changing the deck's theme will not recolour them.
- **Semantic shape identity.** A "process arrow" is a path. If it is not
  provably a rectangle, rounded rectangle, ellipse or line, it becomes a
  freeform with the exact geometry.
- **Anything that was flattened into a bitmap before export.** See below.

## Images: what "editable" means here

An independent bitmap in the PDF becomes an independent PowerPoint picture. You
can move it, resize it, rotate it, crop it, change its transparency and replace
it.

What the tool will **not** do is take a bitmap that already contains text,
shapes or chart parts and split it into pieces. That would require
re-generating pixels the source never had. The bitmap stays one picture. If you
need the inside of a bitmap reconstructed, that is a different job and belongs
in a separate, clearly-labelled output — it is not in this tool.

- CMYK JPEGs are decoded and re-encoded as lossless PNG rather than passed
  through, because CMYK JPEG rendering in PowerPoint is inconsistent enough to
  produce visibly wrong colours.
- JBIG2 and CCITT-encoded images are not decoded. Those regions fall back to a
  render.
- Stencil masks (`/ImageMask`) are handled as 1-bit grey; a stencil painted in
  a non-black colour will lose that colour and should fall back — if you have
  such a file, treat this as untested.

## Text

- **Fonts are not embedded.** Embedding requires knowing the font's licence
  permits it, and the tool cannot know that. The deck references font families
  by name. On a machine without the source font, PowerPoint substitutes, and
  the text will not be metrically identical.
- Provide `--font-map` to route source families to fonts your organisation
  actually has installed. This is the single highest-value knob for real
  documents.
- Each source line becomes its own paragraph and wrapping is disabled, so line
  breaks are exactly the source's. The consequence is that **editing a
  paragraph will not reflow it** the way a natively-authored text box would;
  added text extends past the box instead of wrapping.
- Vertical writing modes (CJK vertical text) are not handled specially.
- Right-to-left text is extracted in the order the PDF paints it. No bidi
  reordering is applied. Arabic and Hebrew documents are **untested**.
- Text rendering modes other than fill and stroke (clip modes, `Tr 7`) are not
  modelled.
- The baseline placement constant is calibrated against LibreOffice's layout
  (see `docs/testing.md`). PowerPoint's font metrics differ slightly, so expect
  sub-point vertical differences that the LibreOffice-based check cannot see.

## Shapes and vectors

- **Dash patterns are approximated.** DrawingML offers ten preset dashes,
  expressed as multiples of the line width; PDF dash arrays are absolute and
  arbitrary. The tool picks the preset whose proportions match best. A custom
  `a:custDash` would be exact but PowerPoint's UI cannot edit it, which trades
  one kind of brokenness for another.
- **Even-odd fill rules are not expressible.** `a:custGeom` has no fill-rule
  attribute. A path relying on even-odd to punch a hole may fill the hole. The
  even-odd flag is preserved in the IR but the writer cannot use it.
- **Gradients and tiling patterns fall back to a render.** PDF has seven
  shading types with arbitrary transfer functions and mesh shadings;
  DrawingML's gradient model cannot express them without inventing colours.
  These are detected and reported, never dropped silently.
- **Clipped vectors fall back to a render.** A native shape would paint outside
  the source's clip region.
- **Soft masks, blend modes and transparency groups beyond constant alpha are
  not modelled.** A page using them will differ, and the visual check should
  catch it and fall back.
- **Line joins, caps and miter limits** are mapped approximately.
- Above a per-slide shape budget (default 900) vector artwork becomes a
  rendered region, because a slide with thousands of shapes makes PowerPoint
  slow to open and painful to edit. Raise it with `--vector-budget` if you want
  editability more than responsiveness.
- **SVG fallback is not implemented.** The brief lists SVG as the preferred
  first fallback, ahead of raster. Only raster region fallback exists today.
  This is a real gap and the top item in "next steps".

## Tables

- Only **lattice** tables are rebuilt — tables the PDF drew rules for. A
  borderless or whitespace-aligned table stays as text boxes. This is
  deliberate: a wrong grid is a damaged slide, and the text boxes already
  reproduce the layout exactly.
- Nested tables are not detected.
- Diagonal cell borders (`a:lnTlToBr`) are not recovered.
- Cell text that the source wrapped is reproduced as separate paragraphs; the
  cell will not re-wrap it the way it was originally authored.
- Rotated tables are not detected.

## Scanned pages

- A page detected as a scan keeps its bitmap unchanged and gains **no** text.
- **OCR is not implemented.** The brief's OCR requirements — local-only engine,
  Tesseract vs PaddleOCR comparison for Korean, no double-drawn text, no
  inpainting, a separate experimental output — are described but nothing is
  built. Neither engine is installed in this environment. Do not expect
  searchable text from a scan.

## Pages and documents

- Mixed page sizes are letterboxed into one slide size at 1:1, because PPTX has
  a single slide size per deck. Smaller pages are centred with margins.
- Only `/Rotate` values of 0, 90, 180 and 270 are handled.
- Annotations, form fields, links, bookmarks, layers (OCGs), attachments and
  JavaScript are ignored. Annotation appearance streams are **not** drawn, so a
  document whose content lives in annotations will come back missing it, and
  the visual check will report the difference.
- Tagged-PDF structure is not used.

## The verification step

- It needs LibreOffice. Without it the tool still converts, but marks every
  object `native-with-warning` and says the check did not run. It never claims
  fidelity it did not measure.
- LibreOffice is not PowerPoint. A slide that renders correctly in LibreOffice
  can still differ in PowerPoint, most likely in text metrics.
- The thresholds are tuned against the synthetic corpus. Real documents may
  need them re-tuned; the numbers are in `verify/compare.py` and every
  comparison's metrics are in the report so you can see where you sit.

## Not verified at all

- **Microsoft PowerPoint has never opened a file this tool produced.** No
  Windows or macOS host was available. The "no repair warnings" criterion is
  therefore **unverified**, not met. `docs/testing.md` has the checklist.
- No real company document has been converted. The corpus is synthetic.
- Performance has not been profiled on large documents.
