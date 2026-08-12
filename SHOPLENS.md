ShopLens

ShopLens is a structural-drawing intelligence layer built on top of Firecrawl's open-source pdf-inspector.

It extracts and organizes structural information from native-text/vector PDF drawing sets using deterministic rules, positioned text, and vector geometry. The original pdf-inspector attribution and MIT license remain unchanged.

What ShopLens does

ShopLens currently supports:

structural steel section-label extraction and normalization

declared Sheet List extraction

title-block discovery and reconciliation

package-level sheet classification and indexing

classified section-label inventories

structural grid-system detection

grid-relative section localization

neutral member-line candidate detection

repetitive linear-pattern clustering

package-level validation and regression reporting

JSON, CSV, debug, and SVG diagnostic output

ShopLens is intentionally conservative. It preserves uncertainty, exposes evidence and warnings, and avoids converting ambiguous drawing content into asserted engineering meaning.

It does not currently:

perform OCR

infer missing engineering information

associate section labels with specific physical members

compare design drawings with shop drawings

perform structural analysis or design checks

replace engineering review

Repository and parser API

The upstream parser is organized as a Rust crate in src/, with extraction in
src/extractor/, table handling in src/tables/, Markdown conversion in
src/markdown/, command-line binaries in src/bin/, and Rust integration tests
in tests/.

Its PyO3 bindings live in src/python.rs. ShopLens-specific Python code lives in
the separate shoplens/ package.

ShopLens uses positioned extraction through the public pdf-inspector API:

items = pdf_inspector.extract_text_with_positions("drawing.pdf")

Returned positioned-text records expose text, coordinates, dimensions, font
metadata, page information, style flags, and item type.

ShopLens uses a public one-based page convention in its own CLI and models.

Requirements

Python 3.9+

pypdf>=6.15,<7

a locally installed pdf-inspector Python extension for the native parser path

The Rust extension currently retains abi3-py38 compatibility independently of
the Python package's supported minimum version.

macOS setup

From Terminal:

cd /path/to/shoplens
python3 -m venv .venv
source .venv/bin/activate
python -m pip install maturin pytest
python -m maturin develop --release

The final command builds the local Rust extension and installs it into the active
virtual environment.

Check the installation

python -m shoplens.cli doctor
python -m shoplens.cli doctor drawing.pdf

The first command checks the Python environment, imports, native module location,
and positioned-text API.

Supplying a PDF also checks that the file can be opened and that positioned text
can be returned.

Steel-section extraction

ShopLens detects common structural-steel section labels from positioned PDF text.

Supported families include:

W-shapes

HSS

channels

angles

double angles

plates

Formatting variants are normalized to stable values.

Example:

W18 x 35
W18×35
W18X35

All normalize to:

W18X35

Each detection retains:

original text

normalized value

section family

one-based PDF page

bounding box

confidence

duplicate information

Coordinates remain in PDF user space.

Inspect steel labels

python -m shoplens.cli inspect drawing.pdf
python -m shoplens.cli inspect drawing.pdf --list
python -m shoplens.cli inspect drawing.pdf --raw --list

JSON:

python -m shoplens.cli inspect drawing.pdf --json
python -m shoplens.cli inspect drawing.pdf --json --list

Filters may be combined:

python -m shoplens.cli inspect drawing.pdf \
  --page <PDF_PAGE> \
  --family W \
  --contains W18

Positioned-text diagnostics

debug-text displays source positioned-text items and their extraction metadata.

python -m shoplens.cli debug-text drawing.pdf
python -m shoplens.cli debug-text drawing.pdf --json
python -m shoplens.cli debug-text drawing.pdf --page <PDF_PAGE>
python -m shoplens.cli debug-text drawing.pdf --contains W18
python -m shoplens.cli debug-text drawing.pdf --family HSS --matches-only
python -m shoplens.cli debug-text drawing.pdf --candidates-only

--page uses ShopLens' public one-based PDF-page convention.

Declared Sheet List extraction

ShopLens can build a structured declared index from native-text tables such as:

SHEET LIST

DRAWING LIST

INDEX OF DRAWINGS

SHEET INDEX

By default, the first few pages are inspected because drawing indexes commonly
appear near the front of a package.

python -m shoplens.cli sheet-list drawing.pdf
python -m shoplens.cli sheet-list drawing.pdf --pages 1-5
python -m shoplens.cli sheet-list drawing.pdf --list
python -m shoplens.cli sheet-list drawing.pdf --json
python -m shoplens.cli sheet-list drawing.pdf --debug

Column locations are inferred from table headers instead of hard-coded page
coordinates.

Example readable output:

Sheet List found on PDF page(s): <PAGE>
<COUNT> sheet entries extracted

<SHEET_ID> | <SHEET_TITLE> | source page <PAGE>

Example JSON shape:

{
  "source_file": "drawing.pdf",
  "pages_scanned": [1, 2, 3, 4, 5],
  "sheet_list_pages": ["<PAGE>"],
  "entries": [
    {
      "sheet_number": "<SHEET_ID>",
      "sheet_name": "<SHEET_TITLE>",
      "source_page": "<PAGE>",
      "number_original_text": "<SOURCE_TEXT>",
      "name_original_text": "<SOURCE_TITLE_TEXT>",
      "number_x": 100.0,
      "number_y": 700.0,
      "number_width": 60.0,
      "number_height": 10.0,
      "name_x": 400.0,
      "name_y": 700.0,
      "name_width": 280.0,
      "name_height": 10.0,
      "confidence": 0.95,
      "warnings": []
    }
  ],
  "duplicate_sheet_numbers": [],
  "warnings": []
}

Supported sheet-number syntax is deliberately broad enough for multiple
structural-document conventions while remaining conservative enough to avoid
ordinary prose, dimensions, and totals.

Exact overlapping PDF text objects and exact duplicate rows are suppressed and
reported.

Title-block extraction and reconciliation

Title-block extraction identifies likely sheet numbers and titles using evidence
such as:

label context

declared-list support

font prominence

nearby title text

repeated coordinate regions

standard and rotated layouts

recurring unlabeled title-block regions

spatially compatible split fragments

Supported labels include forms such as:

SHEET
SHEET NO.
SHEET NUMBER
DRAWING NO.
DWG NO.
DOCUMENT NO.

ShopLens can learn more than one title-block layout within the same package.

Run:

python -m shoplens.cli title-blocks drawing.pdf --list
python -m shoplens.cli title-blocks drawing.pdf --page <PDF_PAGE>
python -m shoplens.cli title-blocks drawing.pdf --json
python -m shoplens.cli title-blocks drawing.pdf --debug

Reconcile declared and actual identities:

python -m shoplens.cli reconcile-sheets drawing.pdf --list
python -m shoplens.cli reconcile-sheets drawing.pdf --json

--page filters displayed records after package-level layout discovery.

Reconciliation statuses

MATCH

TITLE_VARIATION

TITLE_MISMATCH

DECLARED_BUT_MISSING

PRESENT_BUT_UNDECLARED

DUPLICATE_SHEET_NUMBER

UNIDENTIFIED_PAGE

LOW_CONFIDENCE

TITLE_BLOCK_ONLY_INDEX

Declared-index status can distinguish:

AVAILABLE

PARTIAL_DECLARED_SHEET_LIST

NO_DECLARED_SHEET_LIST

A package without a usable Sheet List can still retain and classify confident
title-block-only records.

Example title-block JSON:

{
  "pdf_page": "<PDF_PAGE>",
  "sheet_number": "<SHEET_ID>",
  "sheet_title": "<SHEET_TITLE>",
  "revision": null,
  "confidence": 1.0,
  "layout_id": "layout-1",
  "number_x": 100.0,
  "number_y": 200.0,
  "warnings": []
}

Example reconciliation JSON:

{
  "declared_sheet_number": "<SHEET_ID>",
  "actual_pdf_pages": ["<PDF_PAGE>"],
  "actual_sheet_number": "<SHEET_ID>",
  "status": "MATCH",
  "title_similarity": 1.0,
  "confidence": 1.0,
  "warnings": []
}

Structural sheet classification

package-index adds deterministic searchable metadata to indexed structural
sheets.

It preserves original identifiers and titles while adding classification fields.

The initial kind taxonomy includes:

GENERAL
NOTES
PLAN
ELEVATION
DETAIL
SECTION
SCHEDULE
DIAGRAM
VIEW
COVER
UNKNOWN

Structural subjects cover categories such as:

general notes

loading

foundations

floor framing

roof framing

platform framing

stair framing

braced frames

wind bracing

connections

steel framing

steel columns

base plates

shear connections

platforms

stairs

other structural content

unknown content

Rules are deterministic and declarative.

Run:

python -m shoplens.cli package-index drawing.pdf
python -m shoplens.cli package-index drawing.pdf --list
python -m shoplens.cli package-index drawing.pdf --json

Example filters:

python -m shoplens.cli package-index drawing.pdf --sheet <SHEET_ID> --debug
python -m shoplens.cli package-index drawing.pdf --page <PDF_PAGE>
python -m shoplens.cli package-index drawing.pdf --kind PLAN
python -m shoplens.cli package-index drawing.pdf --subject FOUNDATION_PLAN
python -m shoplens.cli package-index drawing.pdf --level "<LEVEL>"
python -m shoplens.cli package-index drawing.pdf --segment <SEGMENT>
python -m shoplens.cli package-index drawing.pdf --area "<AREA>"
python -m shoplens.cli package-index drawing.pdf --unknown-only --list

Example JSON:

{
  "pdf_page": "<PDF_PAGE>",
  "sheet_number": "<SHEET_ID>",
  "declared_title": "<DECLARED_TITLE>",
  "actual_title": "<ACTUAL_TITLE>",
  "sheet_kind": "PLAN",
  "subject": "FLOOR_FRAMING",
  "level": "<LEVEL>",
  "segment": "<SEGMENT>",
  "classification_confidence": 0.98,
  "matched_rule": "<RULE_ID>",
  "warnings": []
}

Classified section inventory

section-inventory joins accepted steel-section labels to indexed sheets using
the physical PDF page.

It does not infer physical members. Repeated section labels remain annotation
detections rather than beam, column, or piece quantities.

python -m shoplens.cli section-inventory drawing.pdf
python -m shoplens.cli section-inventory drawing.pdf --list
python -m shoplens.cli section-inventory drawing.pdf \
  --sheet <SHEET_ID> \
  --list
python -m shoplens.cli section-inventory drawing.pdf \
  --subject ROOF_FRAMING \
  --family W \
  --list
python -m shoplens.cli section-inventory drawing.pdf \
  --section W18X35 \
  --list
python -m shoplens.cli section-inventory drawing.pdf \
  --without-detections \
  --list

CSV export:

python -m shoplens.cli section-inventory drawing.pdf \
  --csv /tmp/shoplens-section-inventory.csv

Structural grid-system extraction

grid-system extracts a structural grid from one selected plan page.

It reports:

contextual grid-bubble labels

horizontal logical axes

vertical logical axes

merged source segments

approximate extents

perpendicular intersections

evidence

rejected candidates

confidence

coherent primary and secondary grid systems

ShopLens combines positioned text and vector evidence rather than accepting
grid-like text alone.

The detector supports:

repeated bubble labels

multiple bubble-size families

dashed and fragmented axes

spatially disconnected systems

shared row or column coordinates

candidate-local fragmented-axis recovery

fixed-point recovery across orientations

final symmetric intersection recomputation

Ambiguous short-stroke bridges are deliberately prevented from connecting
otherwise separate grid systems.

The geometry adapter uses the native parser when available and falls back to:

pypdf>=6.15,<7

when required geometry is not exposed by the installed native binding.

Run:

python -m shoplens.cli grid-system drawing.pdf \
  --sheet <SHEET_ID> \
  --list

python -m shoplens.cli grid-system drawing.pdf \
  --sheet <SHEET_ID> \
  --debug

python -m shoplens.cli grid-system drawing.pdf \
  --sheet <SHEET_ID> \
  --svg /tmp/shoplens-grid.svg

A physical PDF page can also be selected:

python -m shoplens.cli grid-system drawing.pdf \
  --page <PDF_PAGE> \
  --list

The SVG contains generated diagnostic geometry and text. It does not embed a
source-page raster image.

Grid-relative section localization

grid-locate-sections places positioned section-label annotations relative to
accepted grid axes on a selected sheet.

Every detection receives one exclusive localization status:

COMPLETE_BAY
ON_AXIS
OUTSIDE_GRID
AMBIGUOUS
UNLOCALIZED

Axes are ordered spatially by page coordinate.

Raw annotation coordinates remain unchanged.

Run:

python -m shoplens.cli grid-locate-sections drawing.pdf \
  --sheet <SHEET_ID> \
  --list

python -m shoplens.cli grid-locate-sections drawing.pdf \
  --sheet <SHEET_ID> \
  --section W18X35 \
  --detections

python -m shoplens.cli grid-locate-sections drawing.pdf \
  --sheet <SHEET_ID> \
  --svg /tmp/shoplens-grid-sections.svg

Localization does not yet associate an annotation with a specific beam, column,
joist, brace, wall, or other physical member.

Member-line candidates

member-line-candidates reviews PDF vector segments inside one selected framing
plan.

Its output intentionally uses:

MEMBER_LINE_CANDIDATE

A candidate is not asserted to be a beam, joist, brace, column, wall, or other
confirmed structural member.

Before scoring, ShopLens can reject geometry such as:

accepted grid-axis lines

page borders

geometry outside the plan region

deterministic dimension lines

short bent leaders

insignificant segments

Remaining candidates gain deterministic rule strength from evidence such as:

plan containment

substantial length

grid-adjacent endpoints

crossed grids

compatible collinear chains

plausible orientation

Accounting is separated into:

raw segments
→ duplicate suppression
→ unique segments
→ primitive rejection
→ segments entering merge
→ evaluated chains
→ accepted candidates / rejected chains

Run:

python -m shoplens.cli member-line-candidates drawing.pdf \
  --sheet <SHEET_ID> \
  --list

python -m shoplens.cli member-line-candidates drawing.pdf \
  --sheet <SHEET_ID> \
  --orientation HORIZONTAL \
  --min-confidence 0.80

python -m shoplens.cli member-line-candidates drawing.pdf \
  --sheet <SHEET_ID> \
  --svg /tmp/shoplens-member-lines.svg

No nearby steel-section label is currently associated with a candidate.

Repetitive linear-pattern clustering

ShopLens can cluster accepted MEMBER_LINE_CANDIDATE records into neutral
repetitive linear patterns without changing upstream candidate geometry.

Pattern taxonomy includes:

PARALLEL_LINE_GROUP
REGULAR_SPACING_FIELD
DOUBLE_LINE_PAIR_GROUP
ORTHOGONAL_NETWORK
COLLINEAR_CHAIN_GROUP
DENSE_LINEAR_FIELD
ISOLATED_CANDIDATE_GROUP
MIXED_LINEAR_PATTERN
UNKNOWN_LINEAR_PATTERN

Patterns remain neutral geometric observations.

A broad dense field is not automatically called joists, deck, walls, hatching,
or confirmed framing.

Run:

python -m shoplens.cli linear-patterns drawing.pdf \
  --sheet <SHEET_ID> \
  --list

python -m shoplens.cli linear-patterns drawing.pdf \
  --sheet <SHEET_ID> \
  --svg /tmp/shoplens-linear-patterns.svg

Local validation suite

validate-suite recursively discovers PDFs and runs package-level structural
checks.

Current package stages:

PDF_HEALTH
SHEET_LIST
TITLE_BLOCKS
SHEET_RECONCILIATION
PACKAGE_CLASSIFICATION

The package validator intentionally does not claim geometry correctness.

python -m shoplens.cli validate-suite \
  "/path/to/evaluation-folder" \
  --json /tmp/shoplens-validation/current.json \
  --markdown /tmp/shoplens-validation/current.md \
  --csv /tmp/shoplens-validation/current.csv

Compare with an earlier result:

python -m shoplens.cli validate-suite \
  "/path/to/evaluation-folder" \
  --compare /path/to/previous-validation.json

Normal reports are designed to use relative package names rather than exposing
absolute local source paths.

A PASS means the stage executed and met its structural output checks. It does
not mean a human has reviewed every engineering result.

Validation philosophy

ShopLens development follows a few rules:

Reproduce a failure before changing detection logic.

Fix the smallest demonstrated root cause.

Add focused synthetic regression coverage.

Validate against multiple drawing conventions.

Preserve prior supported geometry unless a result is demonstrated to be a
false positive.

Treat increased detection counts and decreased detection counts with equal
skepticism.

Keep package validation separate from geometry validation.

Preserve diagnostic evidence for ambiguous or rejected results.

Testing

Run ShopLens unit tests:

python -m pytest tests/unit -q

Run the Python binding tests:

python -m pytest tests/test_python.py tests/unit

Run Rust checks:

cargo fmt --check
cargo clippy -- -D warnings
cargo test
cargo build --release

Compile-check Python:

python -m compileall shoplens

Check the diff:

git diff --check

Current limitations

Native-text/vector PDFs are the primary target.

OCR is not currently part of the extraction pipeline.

Steel-section recognition is syntax-based and intentionally conservative.

Some valid labels may remain unrecognized when text is fragmented in unusual
ways.

Title-block discovery depends on recurring and/or strongly labeled evidence.

Sheet classification is primarily title-based.

Grid detection requires physical bubble/vector evidence.

Fragmented-axis recovery requires an already coherent candidate system.

Secondary grids are emitted only when independent geometry supports them.

Grid localization does not establish physical member identity.

Member-line detection remains candidate-level rather than semantic member
classification.

Linear-pattern clustering remains neutral and does not infer drafting meaning.

The package validation suite does not validate grid/member/pattern geometry.

ShopLens does not currently compare drawing revisions or design drawings with
fabrication/shop drawings.

ShopLens does not perform structural analysis, design, or code checking.

Documentation examples

Public documentation should use synthetic examples only.

Use placeholders such as:

drawing.pdf
<SHEET_ID>
<SHEET_TITLE>
<PDF_PAGE>
<LEVEL>
<SEGMENT>
<AREA>

Do not place machine-specific absolute paths, project filenames, client/project
identifiers, or evaluation-dataset sheet identities into README files, public
documentation, tests intended for publication, screenshots, or examples.

Local evaluation inputs and generated validation artifacts should remain outside
the repository.

Design principles

Evidence over guessing

A text string that resembles a structural object is not enough by itself.

ShopLens prefers physical drawing evidence and multiple independent signals.

Preserve uncertainty

Unsupported observations remain unassigned, ambiguous, or unknown.

Cross-layout behavior

Rules should generalize across different drawing conventions rather than target
one package's coordinates, filenames, or naming scheme.

Deterministic first

Current ShopLens interpretation is rule- and geometry-based.

Diagnostics are first-class

Warnings, rejected candidates, confidence, evidence, JSON, and SVG diagnostics
are part of the development workflow.

Roadmap

The current foundation supports future work in areas such as:

PDF understanding
      ↓
sheet identity
      ↓
structural section labels
      ↓
grid systems
      ↓
grid-relative localization
      ↓
member association
      ↓
drawing relationships
      ↓
cross-sheet structural context

Future capabilities should remain evidence-backed and independently testable.

License

See LICENSE for repository licensing information.

ShopLens builds on Firecrawl's open-source pdf-inspector; upstream attribution
and licensing are preserved.
