# Samples

Each `.pptx` here was produced from the matching PDF in `fixtures/pdf/` by:

```bash
pdf2editable-ppt fixtures/pdf/<name>.pdf -o samples/<name>.pptx --report samples/<name>.report.json
```

`encrypted.pptx` additionally needs `--password secret`, and `scanned_korean`
was run with `--ocr experimental` as well. `corrupt.pdf` has no
sample: it is damaged beyond what either the parser or the renderer can
recover, and the tool exits with code 4 rather than writing a file.

The `.report.json` beside each deck records what every object became and, where
it is not a native PowerPoint object, why. It validates against
`docs/report-schema.json`.

| Sample | What it demonstrates |
|---|---|
| `text_mixed` | Korean + Latin, several style runs on one line, bullets, centred and right-aligned blocks, text rotated 90° and 45° |
| `shapes` | line, dashed diagonal, rect, rounded rect, ellipse, circle, polygon, Bézier, overlapping alpha fills, five stroke weights |
| `images` | JPEG reused byte for byte, stretched placement, alpha PNG, 30°-rotated placement, a clip recovered as a crop, one bitmap placed twice |
| `table_lattice` | native table with a 2-column header merge, a 2-row merge, per-cell fills and per-edge borders, mixed alignment |
| `table_borderless` | a whitespace table deliberately left as text boxes |
| `chart` | 50 native objects — axes, gridlines, bars, a Bézier trend curve, markers, legend |
| `clip_gradient` | clipped vectors and an axial shading falling back to rendered regions, reported |
| `dense_vector` | 1 200 shapes exceeding the per-slide budget, falling back to one region |
| `scanned` | a full-page bitmap kept intact with no invented text |
| `scanned_korean` | a photographed Korean page with known ground truth; `scanned_korean.pptx` keeps the bitmap, and `--ocr experimental` added `scanned_korean.ocr.pptx` (editable text draft, blank background) and `scanned_korean.ocr.json` (every word with its confidence) |
| `rotated_pages` | `/Rotate 90` and `/Rotate 270` rebuilt natively |
| `mixed_sizes` | A4 portrait, Letter landscape and a 300×400 page letterboxed into one deck |
| `damaged_page` | page 1 native, page 2 unreadable and rendered, document not lost |
| `encrypted` | an encrypted PDF opened with `--password` |
