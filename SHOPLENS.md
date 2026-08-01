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
architect's or engineer's declared index. ShopLens does not yet compare it with
the title block printed on each actual drawing sheet.

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
  "warnings": []
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
- Sheet List extraction requires native positioned text and recognizable column
  headers. It does not read image-only tables, infer missing column positions,
  classify sheets, or verify declared entries against actual title blocks.
- Continuation without repeated headers is limited to the page immediately
  following a confirmed list page inside the selected range. Complex wrapped
  multi-line titles may require future row-continuation logic.
