# ShopLens steel-section extractor (first milestone)

ShopLens builds on Firecrawl's open-source `pdf-inspector` and keeps its
construction-specific code in the separate `shoplens/` Python package. The
original attribution and MIT license remain unchanged.

## What it does

This milestone reads a local native-text PDF, uses `pdf-inspector` to extract
positioned text, and detects common W-shapes, HSS, channels, angles, double
angles, and plates. Formatting variants such as `W18 x 35` and `W18×35` are
normalized to `W18X35`.

Each result contains the original matched text, normalized value, section
family, 1-based PDF page number, bounding box (`x`, `y`, `width`, `height` in
PDF points), and confidence. Coordinates use the PDF convention: the origin is
at the bottom-left of the page.

It does **not** compare drawings, perform OCR, recognize grids or beam lines,
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
The page value is 1-based; `x` and `y` are PDF points measured from the
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

## Run ShopLens

Readable Terminal output:

```bash
python -m shoplens.cli inspect path/to/drawing.pdf
```

Example:

```text
Page 3 | W18X35 | family=W | x=124.50 | y=388.20
```

Complete JSON output:

```bash
python -m shoplens.cli inspect path/to/drawing.pdf --json
```

```json
[
  {
    "page_number": 3,
    "original_text": "W18 x 35",
    "normalized_text": "W18X35",
    "section_family": "W",
    "x": 124.5,
    "y": 388.2,
    "width": 47.1,
    "height": 9.0,
    "confidence": 1.0
  }
]
```

An empty JSON list means text was extracted but no supported labels were found.
The readable mode says this explicitly. Missing files, non-PDF filenames,
unreadable PDFs, PDFs with no extractable text, and an unbuilt extension produce
plain-English error messages.

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
