# ShopLens steel-section extraction and diagnostics

ShopLens builds on Firecrawl's open-source `pdf-inspector` and keeps its
construction-specific code in the separate `shoplens/` Python package. The
original attribution and MIT license remain unchanged.

## What it does

ShopLens reads a local native-text PDF, uses `pdf-inspector` to extract
positioned text, and detects common W-shapes, HSS, channels, angles, double
angles, and plates. Formatting variants such as `W18 x 35` and `W18×35` are
normalized to `W18X35`.

Each result contains the original matched text, normalized value, section
family, 1-based PDF page number, bounding box (`x`, `y`, `width`, `height` in
PDF points), and confidence. Coordinates use the PDF convention: the origin is
at the bottom-left of the page.

It also provides extraction diagnostics, duplicate-aware summaries, filters,
and installation checks. It does **not** compare drawings, perform OCR,
recognize grids or beam lines,
join labels split across text items, or use AI/external services.

## Repository and parser API

The upstream project is organized as a Rust crate in `src/`, with extraction in
`src/extractor/`, table handling in `src/tables/`, Markdown conversion in
`src/markdown/`, command-line binaries in `src/bin/`, and Rust integration tests
and PDF fixtures in `tests/`. Its PyO3 bindings live in `src/python.rs`; the
package metadata is in `pyproject.toml`, and `pdf_inspector.pyi`,
`docs/python.md`, `examples/basic_usage.py`, and `tests/test_python.py` document
and exercise the public Python interface.

ShopLens calls the public function below directly:

```python
items = pdf_inspector.extract_text_with_positions("drawing.pdf")
```

It returns `pdf_inspector.TextItem` objects. Each exposes `text`, `x`, `y`,
`width`, `height`, `font`, `font_size`, `page`, style flags, and `item_type`.
The source `page` value is already 1-based; ShopLens preserves it as the
human-readable page number. `x` and `y` are raw PDF points measured from the
bottom-left. The same API also has a bytes variant and an optional page filter.
Other public functions return plain text, Markdown, document classification, or
text within caller-supplied regions, but positioned extraction is the correct
API for this milestone.

The Rust project currently builds and tests with `cargo fmt`,
`cargo clippy -- -D warnings`, `cargo test`, and `cargo build --release`.
Python bindings are built and installed into the active environment by Maturin
and are tested with Pytest.

## macOS setup

From Terminal, change into this repository and create a Python virtual
environment. A virtual environment keeps this project's Python tools separate
from the rest of your Mac.

```bash
cd /path/to/shoplens
python3 -m venv .venv
source .venv/bin/activate
python -m pip install maturin pytest
python -m maturin develop --release
```

The final command compiles the Rust `pdf-inspector` extension and installs it
into the active virtual environment. ShopLens imports that extension directly;
the Rust parser is not copied or rewritten.

## Check the installation

```bash
python -m shoplens.cli doctor
python -m shoplens.cli doctor drawing.pdf
```

The first command checks Python, both imports, the native module location, and
the positioned-text function. Supplying a PDF also checks that the path exists,
that `pdf-inspector` can open it, and that positioned text is returned. Cargo is
not required when a working Python extension is already installed.

## Inspect steel labels

The default output is a summary so a large drawing does not flood Terminal:

```bash
python -m shoplens.cli inspect drawing.pdf
```

Add `--list` to print individual deduplicated records:

```bash
python -m shoplens.cli inspect drawing.pdf --list
python -m shoplens.cli inspect drawing.pdf --raw --list
```

`--raw` selects every accepted source detection, including duplicate PDF text
objects. Without `--raw`, records are deduplicated only when page, normalized
section, coordinates, width, and height match within 0.25 PDF points. A retained
record reports `duplicate_count`. Two `W24X62` labels at different drawing
locations remain distinct.

Summary JSON clearly identifies `record_mode` as `raw` or `deduplicated`:

```bash
python -m shoplens.cli inspect drawing.pdf --json
python -m shoplens.cli inspect drawing.pdf --json --list
```

The summary includes raw/displayed totals, unique values, counts by family and
page, pages containing detections, the ten most frequent values, duplicates,
negative coordinates, and rejected likely false positives. Filters work with
readable and JSON output and can be combined:

```bash
python -m shoplens.cli inspect drawing.pdf --page 39 --family W --contains W18
```

## Diagnose extracted text

`debug-text` displays every source positioned-text item, including its source
page, human-readable page, text, raw box, font metadata, candidate/match flags,
accepted section data, and practical rejection reasons.

```bash
python -m shoplens.cli debug-text drawing.pdf
python -m shoplens.cli debug-text drawing.pdf --json
python -m shoplens.cli debug-text drawing.pdf --page 39
python -m shoplens.cli debug-text drawing.pdf --contains W18
python -m shoplens.cli debug-text drawing.pdf --family HSS --matches-only
python -m shoplens.cli debug-text drawing.pdf --candidates-only
```

Family options may be repeated. `--page` always means the visible one-based PDF
page number.

## Extract a declared Sheet List

ShopLens can build a structured index from a native-text `SHEET LIST`,
`DRAWING LIST`, `INDEX OF DRAWINGS`, or `SHEET INDEX` table. This is the
architect's or engineer's declared index. The title-block commands described
below compare that declared index with the identity printed on each actual page.

By default, only PDF pages 1–5 are parsed because drawing indexes are normally
near the front of a package and large packages can contain hundreds of pages.
Page ranges are human-readable and one-based:

```bash
python -m shoplens.cli sheet-list drawing.pdf
python -m shoplens.cli sheet-list drawing.pdf --pages 1-5
python -m shoplens.cli sheet-list drawing.pdf --pages 2-8
```

The normal output is a concise summary. Add `--list` for declared rows, use
`--json` for every model field and raw bounding-box coordinate, or use `--debug`
to see heading candidates, header candidates, inferred column boundaries, row
Y positions, and rejected-row reasons:

```bash
python -m shoplens.cli sheet-list drawing.pdf --list
python -m shoplens.cli sheet-list drawing.pdf --json
python -m shoplens.cli sheet-list drawing.pdf --debug
python -m shoplens.cli sheet-list drawing.pdf --json --debug
```

Example readable output:

```text
Sheet List found on PDF page(s): 3
92 sheet entries extracted

Prefix summary:
S0: 10
S1: 35

S1-20A | SECOND FLOOR FRAMING PLAN - SEGMENT A | source page 3
```

The JSON result has this overall shape:

```json
{
  "source_file": "drawing.pdf",
  "pages_scanned": [1, 2, 3, 4, 5],
  "sheet_list_pages": [3],
  "entries": [
    {
      "sheet_number": "S1-20A",
      "sheet_name": "SECOND FLOOR FRAMING PLAN - SEGMENT A",
      "source_page": 3,
      "number_original_text": "S1-20A",
      "name_original_text": "SECOND FLOOR FRAMING PLAN - SEGMENT A",
      "number_x": 100.0,
      "number_y": 700.0,
      "number_width": 60.0,
      "number_height": 10.0,
      "name_x": 400.0,
      "name_y": 700.0,
      "name_width": 280.0,
      "name_height": 10.0,
      "confidence": 0.95,
      "warnings": [],
      "name_comparison_text": "SECOND FLOOR FRAMING PLAN-SEGMENT A"
    }
  ],
  "duplicate_sheet_numbers": [],
  "warnings": [],
  "declared_total": 92
}
```

Supported number styles include `S0-00`, `S1-20A`, `S-101`, `S101A`,
`SK-01`, and `SSK-001`. The configurable syntax requires an alphabetic prefix
and digits; it intentionally excludes ordinary prose, dimensions, and totals.

Column locations come from the `SHEET NUMBER`/`SHEET NAME` headers rather than
fixed page coordinates. A confirmed list may continue onto the next selected
page with repeated headers or with rows using the prior page's column layout.
An unrelated two-column schedule is not treated as a continuation unless it
immediately follows a confirmed list page and contains valid sheet rows.

Exact overlapping PDF text objects and exact duplicate rows are suppressed and
reported. Repeated sheet numbers at different locations remain in the result.
Identical titles receive `DUPLICATE_SHEET_NUMBER`; differing titles receive
`CONFLICTING_SHEET_TITLES`. Missing names, invalid row candidates, suspiciously
small results, missing column headers, and footer rows are also diagnosed.
When a `Grand total` footer is present, JSON exposes it as `declared_total` and
ShopLens reports `DECLARED_TOTAL_MISMATCH` if it differs from the unique entry
count. Invalid-row details remain in `--debug`; normal output uses an aggregated
warning count.

## Extract and reconcile title blocks

Title-block extraction finds likely sheet numbers, scores their label context,
declared-list support, font prominence, and nearby title text, then discovers
repeated coordinate clusters within the package. A cluster must be supported by
at least two pages; its signed raw coordinates are compared with a 60-point
tolerance. More than one standard or rotated layout can be discovered. Coordinates
are never converted with `abs()` or tied to an assumed lower-right page corner.

The declared Sheet List is useful supporting evidence, but it is not treated as
truth by itself. Small references, Sheet List rows, and frequently repeated project
identifiers are rejected unless independent title-block label and recurring-layout
evidence establishes the context. Ambiguous candidates remain unidentified and
receive a low-confidence warning. Revision is blank unless an explicit nearby
`REV` or `REVISION` field supplies a clear value.

On macOS, run these commands from the repository directory:

```bash
python -m shoplens.cli title-blocks \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --list
python -m shoplens.cli title-blocks \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --page 27
python -m shoplens.cli title-blocks \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --json
python -m shoplens.cli title-blocks \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --debug

python -m shoplens.cli reconcile-sheets \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --list
python -m shoplens.cli reconcile-sheets \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --json
```

`--page` filters displayed title-block records only after layouts are discovered
from the complete package. The reader uses short-lived, bounded extraction workers
to keep large native-text drawing sets within predictable memory. `--debug` exposes
all candidate scores and reasons, rejections, selected title fragments, confidence,
and discovered layout clusters; normal output remains concise.

Reconciliation uses these statuses:

- `MATCH`: number and normalized title agree.
- `TITLE_VARIATION`: number agrees and only harmless punctuation, spacing,
  hyphenation, or a supported abbreviation differs.
- `TITLE_MISMATCH`: number agrees but the titles differ meaningfully.
- `DECLARED_BUT_MISSING`: a declared number has no identified actual page.
- `PRESENT_BUT_UNDECLARED`: an identified actual number is absent from the list.
- `DUPLICATE_SHEET_NUMBER`: one actual number appears on multiple PDF pages.
- `UNIDENTIFIED_PAGE`: no reliable candidate exists.
- `LOW_CONFIDENCE`: a candidate or title exists but evidence is insufficient.

Title comparison uppercases text, collapses whitespace, normalizes hyphen spacing,
and compares a small explicit abbreviation vocabulary. The JSON
`title_similarity` value is Python's deterministic character-sequence ratio after
strict normalization; status does not rely on an LLM or semantic embedding.

Example title-block JSON fields:

```json
{
  "pdf_page": 27,
  "sheet_number": "S1-20A",
  "sheet_title": "SECOND FLOOR FRAMING PLAN - SEGMENT A",
  "revision": null,
  "confidence": 1.0,
  "layout_id": "layout-1",
  "number_x": 2839.68,
  "number_y": -2138.76,
  "warnings": []
}
```

Example reconciliation record:

```json
{
  "declared_sheet_number": "S1-20A",
  "actual_pdf_pages": [27],
  "actual_sheet_number": "S1-20A",
  "status": "MATCH",
  "title_similarity": 1.0,
  "confidence": 1.0,
  "warnings": []
}
```

## Classify and index structural sheets

`package-index` adds deterministic searchable metadata to reconciled sheets. It
never replaces the sheet number, PDF page, declared title, or actual title. The
actual title is preferred for classification; the declared title is a fallback,
and a missing source remains `UNKNOWN` with a warning.

The initial kind taxonomy is `GENERAL`, `NOTES`, `PLAN`, `ELEVATION`, `DETAIL`,
`SECTION`, `SCHEDULE`, `DIAGRAM`, `VIEW`, `COVER`, and `UNKNOWN`. Explicit
drawing-view forms such as `3D VIEW`, `ISOMETRIC VIEW`, `AXONOMETRIC VIEW`, and
`PERSPECTIVE VIEW` use `VIEW`; a bare `VIEW` or a similar word such as `VIEWING`
does not. The structural subject
taxonomy covers general notes, loading, foundation plans/details, floor/roof/
platform/stair framing, braced frames, wind bracing, connections, steel framing,
steel columns, base plates, shear connections, platforms, stairs, other structural
content, and unknown content. Mixed sheets keep one primary kind/subject plus
secondary values; for example, sections-and-details uses `DETAIL` with secondary
kind `SECTION`.

Rules are declarative and deterministic. Exact or highly specific title patterns
run before broader patterns, so `FOUNDATION PLAN` wins over generic foundation
content and roof-framing details retain their roof subject. Equally specific rules
with different assignments produce `MULTIPLE_PRIMARY_RULES` rather than relying on
declaration order. Confidence is rule strength, not a statistical probability:
approximately 0.98 is highly specific, 0.90–0.95 is strong, 0.80 is a broad safe
fallback, below 0.70 is warned, and 0.00 is unknown.

Levels are extracted only for explicit vertical context such as `FOUNDATION`,
`SECOND FLOOR`, or `ROOF`. Named zones such as `MECHANICAL PLATFORM`, `OFFICE
ROOF`, stair towers, and `SERVICE YARD` are areas. Segments come from `SEGMENT X`
in the title; a matching sheet-number suffix is supporting evidence only, and a
different suffix produces `SEGMENT_CONFLICT`. Controlled modifiers currently
include `TYPICAL`, `OVERALL`, `ENLARGED`, and `PARTIAL`.

Stable group keys combine useful metadata without replacing it, for example
`FOUNDATION_PLAN:SEGMENT_A`, `FLOOR_FRAMING:SECOND_FLOOR:SEGMENT_A`,
`CONNECTION:DOUBLE_ANGLE`, and `PLATFORM:DETAIL`.

Run the real package on macOS from the repository directory:

```bash
python -m shoplens.cli package-index \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf"
python -m shoplens.cli package-index \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --list
python -m shoplens.cli package-index \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --json > /tmp/shoplens-package-index.json
```

Filters apply to both readable and JSON records:

```bash
python -m shoplens.cli package-index drawing.pdf --sheet S1-20A --debug
python -m shoplens.cli package-index drawing.pdf --page 27
python -m shoplens.cli package-index drawing.pdf --kind PLAN
python -m shoplens.cli package-index drawing.pdf --subject FOUNDATION_PLAN
python -m shoplens.cli package-index drawing.pdf --level "SECOND FLOOR"
python -m shoplens.cli package-index drawing.pdf --segment A
python -m shoplens.cli package-index drawing.pdf --area "MECHANICAL PLATFORM"
python -m shoplens.cli package-index drawing.pdf --unknown-only --list
```

JSON preserves both original titles and includes stable enum values, rule ID,
confidence, evidence, warnings, secondary taxonomy, group keys, package counts,
and `classification_version`:

```json
{
  "pdf_page": 27,
  "sheet_number": "S1-20A",
  "declared_title": "SECOND FLOOR FRAMING PLAN - SEGMENT A",
  "actual_title": "SECOND FLOOR FRAMING PLAN - SEGMENT A",
  "sheet_kind": "PLAN",
  "subject": "FLOOR_FRAMING",
  "level": "SECOND FLOOR",
  "segment": "A",
  "classification_confidence": 0.98,
  "matched_rule": "SECOND_FLOOR_FRAMING_PLAN",
  "warnings": []
}
```

For example, `OVERALL 3D VIEW` classifies as kind `VIEW`, subject
`OTHER_STRUCTURAL`, modifier `OVERALL`, using the stable `DRAWING_VIEW` rule.

To add a safe rule, place a narrowly worded pattern in
`shoplens/classification/rules.py`, give it a stable ID and a higher priority than
any broader fallback it supersedes, then add synthetic positive, negative, and
ambiguity tests. Do not add a rule solely to eliminate an unknown count.

## Build a classified section-label inventory

`section-inventory` joins accepted steel-section labels to classified sheets using
the one-based PDF page number. It does not infer physical members: repeated
`W18X35` records are label detections and may be distinct annotations, schedules,
references, or duplicated PDF text. No count produced here is a beam or column
quantity.

The command builds the package index once, extracts positioned text in isolated
three-page batches, and reuses those lightweight records for title blocks and steel
detection. It does not parse the PDF separately for every sheet. Every indexed
sheet remains in the inventory, including sheets with zero recognized labels.
Unmatched detection pages and pages with multiple index records remain in an
explicit unmatched list with warnings; they are never joined by title or guessed
sheet number.

The default mode uses the existing safe coordinate deduplication rule: only the
same page, normalized section, and near-identical bounding box are collapsed.
`--raw` keeps every accepted source detection. In deduplicated mode, detection
counts are retained-record counts and do not add `duplicate_count` again.
`duplicate_count` remains evidence of how many overlapping source records were
represented. Package summaries report both detection occurrences and distinct
sheet counts. A sheet with several named areas contributes its detections to each
area bucket intentionally.

Run the real package from macOS Terminal:

```bash
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf"
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" --list
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --list
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --subject ROOF_FRAMING --family W --list
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --section W18X35 --list
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --without-detections --list
```

Metadata and detection filters can be combined: `--sheet`, `--page`, `--kind`,
`--subject`, `--level`, `--segment`, `--area`, `--family`, and `--section`.
`--with-detections` and `--without-detections` select sheets by presence. Add
`--detections` only when individual coordinates are needed, or `--debug` to show
the joined package-index record, extraction pages, raw/deduplicated counts,
duplicate suppression, filters, and warnings. Framing plans and elevations are
often the most relevant future inputs for member association, but the inventory
does not hardcode a sheet-kind restriction.

JSON includes the complete package summary, inventory version, mode, warnings,
all selected sheets, individual detection coordinates, classification metadata,
and active filters:

```bash
python -m shoplens.cli section-inventory drawing.pdf --json
python -m shoplens.cli section-inventory drawing.pdf \
  --subject FLOOR_FRAMING --section W18X35 --json
```

Export one matching detection per CSV row with Python's standard CSV writer:

```bash
python -m shoplens.cli section-inventory \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --csv /tmp/shoplens-section-inventory.csv
```

The CSV preserves page, sheet metadata, original and normalized label text,
family, signed raw coordinates, confidence, duplicate count, and record mode. A
zero-result selection still writes a valid header-only CSV.

## Extract a plan grid system

`grid-system` extracts one selected plan page at a time. It reports contextual
grid-bubble labels, horizontal and vertical logical axes, merged source
segments, approximate extents, intersections, evidence, rejected candidates,
and confidence. It does not associate an axis with a beam, column, steel label,
or physical member.

ShopLens keeps `pdf_inspector` as its fast text, title-block, and classification
provider. The checked-in Rust parser already tracks the current transformation
matrix and internally extracts stroked `m`/`l` line segments and `re`
rectangles in the same space as positioned text. This branch adds an additive
geometry binding for that existing information. Until a local native extension
is rebuilt with the binding, the adapter falls back to `pypdf>=5,<6`, a
pure-Python library under the permissive BSD-3-Clause license. No PyMuPDF or
copyleft runtime dependency was added. The fallback is necessary only because
the previously published Python API exposes positioned text but not page boxes
or vector paths.

The geometry adapter preserves PDF user-space values and never applies
`abs(x)` or `abs(y)`. `/MediaBox`, `/CropBox`, and `/Rotate` are recorded
separately. A page's displayed rotation does not by itself prove that
`pdf_inspector` rotated extracted coordinates: ShopLens tests positioned-text
anchors against each explicitly transformed crop box and selects the matching
coordinate convention. This preserves the negative Y coordinates on layouts
that `pdf_inspector` normalizes by 90 degrees while leaving the other layout in
raw bottom-left PDF coordinates. Rotation and non-zero crop-offset conversions
have synthetic tests.

Grid candidates require contextual evidence. The current deterministic detector
uses consistently sized ellipse bubbles, aligned label runs, repeated labels at
opposite ends when present, collinear line coverage, and perpendicular
intersections. Dashed or split paths are retained as source segments and merged
into logical extents. Short lines, inconsistent bubbles, detail/section
references, and uncontextual schedule numbers do not become axes. Spatially
separate similarly strong label runs produce `MULTIPLE_SIMILAR_GRID_SYSTEMS`
instead of being silently merged.

Run one of the known sheets from macOS Terminal:

```bash
python -m shoplens.cli grid-system \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --list
python -m shoplens.cli grid-system \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --debug
python -m shoplens.cli grid-system \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --svg /tmp/shoplens-grid-S1-20A.svg
open /tmp/shoplens-grid-S1-20A.svg
```

`--page 27` can be used instead of `--sheet S1-20A`. Add `--json` for the
complete page geometry, axis source segments, label evidence, classification
metadata, rejected candidates, coordinate conversion, warnings, and grid
version. The SVG is standard-library XML containing geometry and text labels
only; it never embeds the confidential PDF page image.

Current limits are deliberate. Form XObject geometry is not recursively
expanded by the fallback. Dash patterns and line widths are available only
when the provider retains them. Bezier paths are reduced to explainable shape
bounds for bubble evidence, not treated as general-purpose CAD curves. The
detector returns the strongest spatial grid system and warns about close
alternatives; it does not process all 92 sheets by default, use OCR, or claim
that every architectural offset grid will be resolved.

## Grid-relative section localization

`grid-locate-sections` places existing, positioned section-label annotations
relative to the accepted axes on one selected sheet. The annotation anchor is
the center of its original bounding box; raw X, Y, width, and height remain
unchanged in the result. Axes are ordered by page coordinate rather than by
their alphabetic or numeric labels. Signed distances are `anchor - axis` in
the page's recorded PDF coordinate units.

An annotation is inside the dominant grid only when its anchor lies between
both axis families and within accepted axis extents in both dimensions. Bay
names come from the spatially surrounding axes. Points within 6 PDF units of
an axis are reported explicitly as `ON <label>` and are not counted as
complete bays. Confidence is a deterministic rule-strength score built from
grid confidence, available axis families, extent containment, complete
surrounding intervals, on-axis evidence, and ambiguity penalties; it is not a
statistical probability.

```bash
python -m shoplens.cli grid-locate-sections \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --list
python -m shoplens.cli grid-locate-sections \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --section W24X55 --detections
python -m shoplens.cli grid-locate-sections \
  "/Users/vibhanshumishra/Desktop/07 STRUCTURAL - DD.pdf" \
  --sheet S1-20A --svg /tmp/shoplens-grid-sections-S1-20A.svg
```

The command defaults to the inventory's deduplicated records and supports
`--raw`, `--family`, `--section`, `--inside-only`, `--outside-only`, and
`--ambiguous-only`. JSON retains the selected record mode and active filters.
The SVG contains only grid geometry and annotation text. Localization does not
associate an annotation with a beam, column, joist, or other physical member.

## Tests

Run the ShopLens unit tests without drawings:

```bash
python -m unittest discover -s tests/unit -v
```

After building the extension, run all Python binding tests:

```bash
python -m pytest tests/test_python.py tests/unit
```

Run the upstream Rust quality checks:

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test
cargo build --release
```

## Current limitations

- Only native text is supported; scanned/image-only PDFs need OCR, which is out
  of scope for this milestone.
- A label must be complete within one extracted text item. The detector accepts
  a small positioned-text interface so nearby-item joining can be added later.
- For multiple labels inside one text item, ShopLens estimates each label's
  horizontal box from its character span. A label occupying the complete item
  retains the extractor's exact box.
- Supported syntax is intentionally conservative to avoid confusing scales,
  dates, sheet numbers, dimensions, and grid labels with steel sections.
- W-shapes require a whole-number nominal depth, while decimal weights such as
  `W6X8.5` remain valid. Notation such as `W2.9XW2.9` or extraction variants
  missing the second W, such as `W2.9X2.9`, are excluded from structural steel
  results and diagnosed as `WELDED_WIRE_REINFORCEMENT`. This is only a
  diagnostic exclusion; reinforcement extraction will be a separate future
  capability. W-shape checking remains syntax-based rather than a complete
  AISC catalog validation.
- Raw PDF coordinates may legitimately be negative because of page rotation,
  crop boxes, transformed CAD content, or shifted drawing origins. ShopLens
  preserves them exactly and never applies `abs()`. The public positioned-text
  API does not expose page width, page height, rotation, media box, or crop box,
  so reliable normalized top-left coordinates cannot yet be calculated.
- ShopLens detects label text and location, but still does not know which label
  belongs to which beam or other drawn member.
- Sheet List and title-block extraction require native positioned text. They do
  not read image-only tables, infer missing Sheet List columns, or classify sheets.
- Title-block extraction learns layouts only from the current PDF. Packages with
  fewer than two confidently labeled pages, unusually fragmented labels, or titles
  outside the nearby title region may remain low-confidence. Revision extraction
  is intentionally conservative.
- Classification is title-based and does not inspect drawing geometry or content.
  Unusual forms such as 3D views may remain unknown until a reusable taxonomy rule
  is justified. A title that names multiple levels produces `LEVEL_CONFLICT`
  instead of an arbitrary primary level.
- Section inventory remains label-based. Grid-relative localization can describe
  where an annotation lies, but it does not associate that annotation with drawn
  member lines, beams, columns, schedules, or physical quantities, and it does
  not compare structural sheets with shop drawings.
- Continuation without repeated headers is limited to the page immediately
  following a confirmed list page inside the selected range. Complex wrapped
  multi-line titles may require future row-continuation logic.
