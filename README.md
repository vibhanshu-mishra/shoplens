# ShopLens

**Structural drawing intelligence for steel construction PDFs.**

ShopLens turns structural drawing sets into structured, explainable data.

It reads native structural PDFs, identifies sheets, extracts structural steel section labels, detects drawing grid systems, and localizes section references relative to those grids — while preserving the evidence behind each result.

ShopLens is designed for structural drawings from different engineers, projects, and drawing conventions rather than a single fixed template.

> **Current focus:** deterministic structural drawing understanding. ShopLens does not use OCR or AI to guess missing drawing information.

---

## What ShopLens Does

A structural PDF is more than text on a page.

Useful information is distributed across:

* sheet lists
* title blocks
* drawing classifications
* grid bubbles and grid lines
* steel section labels
* section/detail references
* positioned text
* vector geometry
* multiple drawing regions on the same sheet

ShopLens combines those signals into a structured representation of the drawing package.

```text
Structural PDF package
        │
        ▼
PDF inspection
        │
        ├── positioned text
        └── vector geometry
        │
        ▼
Sheet discovery
        │
        ├── sheet list
        ├── title blocks
        ├── sheet-number reconstruction
        └── physical PDF page reconciliation
        │
        ▼
Package index
        │
        ├── sheet identity
        ├── PDF page
        └── drawing classification
        │
        ├──────────────────────┐
        ▼                      ▼
Steel sections             Grid systems
        │                      │
        │                      ├── grid bubbles
        │                      ├── grid axes
        │                      ├── intersections
        │                      └── secondary systems
        │                      │
        └──────────┬───────────┘
                   ▼
          Section localization
                   │
                   ├── complete bay
                   ├── on axis
                   ├── outside grid
                   ├── ambiguous
                   └── unlocalized
```

---

## Features

### Structural package indexing

ShopLens builds a normalized index of structural sheets inside a PDF package.

It can use:

* declared sheet lists
* detected title blocks
* physical PDF page numbers
* reconstructed sheet numbers
* sheet reconciliation
* drawing classification

Sheet identity is kept separate from physical PDF page identity so downstream tools can reliably request a structural sheet by its drawing number.

ShopLens also supports packages without a usable declared sheet list by retaining title-block-derived sheet identities.

---

### Sheet-number reconstruction

Structural drawing numbers are frequently split into multiple positioned-text fragments.

ShopLens includes structural sheet-number grammar and reconstruction. Fragmented text can be joined and ranked so complete structural sheet identities win over incomplete prefixes.

---

### Steel section extraction

ShopLens detects and normalizes common structural-steel section labels from positioned PDF text.

Supported families include:

* W shapes
* HSS
* channels
* angles
* double angles
* plates

Detected sections retain source information including page and PDF coordinates rather than becoming detached text strings.

---

### Grid-system detection

ShopLens detects structural grid systems from physical drawing evidence.

Grid detection combines:

* positioned grid labels
* bubble geometry
* collinear vector segments
* line continuity
* perpendicular intersections
* repeated labels
* spatial coherence

The detector does not create axes merely because a label resembles a grid name.

Each accepted grid axis retains evidence explaining why it was accepted.

Example:

```text
VERTICAL | 1 | x=1208.50 | labels=2 | intersections=18 | confidence=0.899
HORIZONTAL | J | y=-1888.00 | labels=1 | intersections=7 | confidence=0.760
```

---

### Fragmented-grid recovery

Real structural grids are not always represented by one continuous PDF line.

They may contain:

* dashed lines
* many short vector fragments
* interruptions around drawing content
* nested detail geometry
* partial line segments

ShopLens can recover label-supported fragmented axes when local geometry and perpendicular-intersection evidence support the reconstruction.

Recovery is deliberately conservative: unsupported geometry is not promoted into a grid axis.

---

### Multiple grid systems

A sheet may contain more than one independent grid region.

ShopLens builds coherent grid candidates before selecting the dominant system and can retain disconnected secondary systems when the geometry supports them.

Systems receive stable identities such as:

```text
PAGE_27_DOMINANT_GRID
PAGE_27_SECONDARY_GRID_1
```

Recovery runs within coherent candidates so one grid system cannot borrow unsupported geometry from another.

Connected offset regions may remain part of one expanded grid when their geometry shows they belong to the same system.

---

### Section localization

Detected structural sections can be localised against the detected grid system.

Every detection receives one exclusive localization status:

```text
COMPLETE_BAY
ON_AXIS
OUTSIDE_GRID
AMBIGUOUS
UNLOCALIZED
```

This gives downstream tools a deterministic answer about where a section occurs without pretending that every drawing condition can be resolved perfectly.

A section localized inside a bay can be associated with the surrounding grid axes.

---

### Evidence and confidence

ShopLens is designed to be inspectable.

Detection results retain evidence such as:

```text
ALIGNED_GRID_LABEL_BUBBLES
COLLINEAR_SEGMENTS
SEGMENT_COVERAGE
LABEL_REPEATED_AT_OPPOSITE_ENDS
PERPENDICULAR_INTERSECTIONS
```

Rejected and unassigned candidates are also retained diagnostically.

The goal is not only to answer:

> What did ShopLens detect?

but also:

> Why did ShopLens detect it?

---

### SVG diagnostics

Grid and localization results can be rendered as SVG overlays for visual inspection.

These are useful when validating:

* accepted grid axes
* rejected candidates
* grid-system boundaries
* section positions
* bay assignments
* localization failures

Visual diagnostics are particularly important when developing against drawings from different engineering firms.

---

### JSON output

ShopLens exposes machine-readable output for downstream automation.

Grid output includes structured information for:

* systems
* axes
* coordinates
* labels
* intersections
* confidence
* evidence
* rejected candidates

Localization output includes both the selected grid hierarchy and a flat grid-system collection for downstream consumers.

---

## Quick Start

### Requirements

* Python 3.9+
* `pypdf>=6.15,<7`
* pdf-inspector native bindings for the primary positioned-text and geometry path

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install ShopLens from the repository:

```bash
pip install -e .
```

---

## CLI

ShopLens provides command-line tools for inspecting structural drawing packages and individual sheets.

Start by viewing the available commands:

```bash
python -m shoplens.cli --help
```

### Inspect a package

Use the package-level tools to inspect structural PDFs, sheet identities, title blocks, reconciliation, and classification.

```bash
python -m shoplens.cli <command> drawing-set.pdf
```

### Inspect a grid system

Grid extraction can operate on a resolved structural sheet rather than requiring the user to determine its physical PDF page manually.

```bash
python -m shoplens.cli grid-system drawing-set.pdf --sheet <SHEET_ID>
```

Readable output reports information such as:

```text
Sheet: <SHEET_ID>
PDF page: <PAGE>
Grid systems: <COUNT>
Secondary grid systems: <COUNT>
Horizontal grid axes: <COUNT>
Vertical grid axes: <COUNT>
Unassigned grid labels: <COUNT>
Rejected candidates: <COUNT>
```

Use the CLI help for the exact output, JSON, SVG, filtering, and diagnostic options supported by the current checkout:

```bash
python -m shoplens.cli grid-system --help
```

---

## Example Grid Result

A detected grid system conceptually contains:

```json
{
  "system_id": "PAGE_9_DOMINANT_GRID",
  "horizontal_axes": [
    {
      "label": "A",
      "coordinate": 752.0,
      "intersection_count": 6,
      "confidence": 0.83,
      "evidence": [
        "ALIGNED_GRID_LABEL_BUBBLES",
        "COLLINEAR_SEGMENTS",
        "PERPENDICULAR_INTERSECTIONS:6"
      ]
    }
  ],
  "vertical_axes": [],
  "secondary_grid_systems": []
}
```

The exact schema may contain additional diagnostic fields.

---

## Localization Model

Grid localization intentionally distinguishes geometrically different outcomes.

| Status         | Meaning                                                     |
| -------------- | ----------------------------------------------------------- |
| `COMPLETE_BAY` | Detection lies within a resolved grid bay                   |
| `ON_AXIS`      | Detection lies on or sufficiently near a grid axis          |
| `OUTSIDE_GRID` | Detection lies outside the selected grid system's extent    |
| `AMBIGUOUS`    | Available evidence does not support one unique localization |
| `UNLOCALIZED`  | No supported localization could be produced                 |

These statuses are mutually exclusive.

Additional diagnostic facts may still be reported separately.

---

## Architecture

```text
shoplens/
  │
  ├── geometry/
  │     └── PDF geometry adapter and fallback extraction
  │
  ├── grids/
  │     ├── bubble/label association
  │     ├── line-component analysis
  │     ├── axis detection
  │     ├── coherent-system partitioning
  │     ├── fragmented-axis recovery
  │     ├── intersection graph
  │     ├── models
  │     └── SVG diagnostics
  │
  ├── localization/
  │     ├── section-to-grid association
  │     ├── localization models
  │     └── SVG diagnostics
  │
  ├── package / sheet analysis
  │     ├── sheet-list extraction
  │     ├── title-block detection
  │     ├── reconciliation
  │     └── classification
  │
  ├── structural section detection
  │
  └── cli.py
```

At a high level:

```text
pdf-inspector
     │
     ▼
positioned PDF evidence
     │
     ▼
ShopLens structural interpretation
     │
     ▼
typed + explainable drawing data
```

---

## pdf-inspector

ShopLens builds on [pdf-inspector](https://github.com/firecrawl/pdf-inspector) for native PDF inspection.

pdf-inspector provides fast local PDF classification and positioned text extraction. ShopLens adds a separate structural-drawing interpretation layer on top of that information.

The upstream parser, attribution, and license are preserved.

ShopLens also contains a `pypdf` fallback for required geometry extraction when the local native extension does not expose the necessary geometry binding.

The supported fallback dependency is:

```text
pypdf>=6.15,<7
```

---

## Design Principles

### Evidence over guessing

A text string that looks like a grid label is not enough.

ShopLens prefers multiple physical signals before promoting drawing information into structured data.

### Preserve uncertainty

Ambiguous or unsupported observations remain ambiguous or unassigned.

ShopLens should not fabricate a clean drawing model from incomplete evidence.

### Cross-firm behavior

The detector is developed against structural drawings from multiple sources and drawing conventions rather than tuning exclusively to one engineer's graphics.

### Deterministic first

Current interpretation is rule- and geometry-based.

AI is not used to silently repair uncertain structural information.

### Diagnostics are part of the product

Rejected candidates, evidence, confidence, SVGs, and validation reports are treated as first-class development tools.

---

## Validation

ShopLens includes both unit-level regression tests and real structural drawing acceptance checks.

The unit suite covers cases including:

* structural section syntax
* sheet-number reconstruction
* duplicate grid bubbles
* multiple bubble-size families
* decorative circles
* detail/section reference rejection
* decimal grid labels
* repeated grid labels
* disconnected grid systems
* side-by-side grids
* stacked grids
* dashed grids
* fragmented axes
* short detail-stroke bridges
* secondary-system recovery
* fixed-point axis recovery
* symmetric intersection metadata
* localization status accounting
* flat versus hierarchical serialization

Real-PDF validation is used to protect behavior across multiple structural drawing packages and engineering conventions.

The repository also includes a package validation suite covering:

```text
PDF_HEALTH
SHEET_LIST
TITLE_BLOCKS
SHEET_RECONCILIATION
PACKAGE_CLASSIFICATION
```

Validation is designed around regression evidence rather than assuming that a higher raw detection count is automatically better.

### Local geometry regression harness

`validate-geometry` is a separate local workflow for protecting grid and
grid-relative localization behavior on selected drawings. Its configuration and
baselines are caller-provided files and should remain outside the repository.

```json
{
  "schema_version": 1,
  "cases": [
    {
      "case_id": "case-001",
      "pdf": "/local/path/to/drawing.pdf",
      "sheet": "<SHEET_ID>",
      "checks": ["GRID", "LOCALIZATION"]
    }
  ]
}
```

Use a one-based `page` instead of `sheet` when the physical page is already
known; a case may include both, with `page` taking precedence.

Create a baseline explicitly, then compare later runs:

```bash
python -m shoplens.cli validate-geometry /path/to/local-config.json \
  --write-baseline /path/to/geometry-baseline.json

python -m shoplens.cli validate-geometry /path/to/local-config.json \
  --compare /path/to/geometry-baseline.json \
  --json /path/to/current.json --markdown /path/to/current.md --csv /path/to/current.csv
```

The compact baseline records grid axes, coordinate and intersection metadata,
secondary-system summaries, and localization counts. Axis matching uses label,
orientation, and coordinate-aware pairing with a configurable 2-point default
tolerance. Lost axes are regressions; new axes, coordinate moves, grid-system
changes, and localization shifts require review rather than being called
improvements automatically. Normal reports identify only `case_id`; `--debug`
is required to include configured source paths in JSON.

The command exits successfully only when every compared case is unchanged (or
when no comparison is requested). Regressions, review-required changes, new or
removed cases, and execution errors return a non-zero exit status.

---

## Why Structural PDFs Are Difficult

PDFs do not contain a structural model.

What visually appears to be:

```text
      1        2        3
      │        │        │
A ────┼────────┼────────┼────
      │        │        │
B ────┼────────┼────────┼────
```

may internally be represented as:

* separate text objects
* dozens or hundreds of line fragments
* reused drawing symbols
* Form XObjects
* clipped geometry
* duplicated circles
* unrelated detail references
* text in a different coordinate convention

ShopLens reconstructs structural meaning from that evidence while attempting to preserve the distinction between what the PDF actually proves and what merely looks plausible.

---

## Current Limitations

ShopLens is under active development.

Current limitations include:

* Native-text/vector PDFs are the primary target.
* ShopLens does not currently use OCR to recover scanned structural drawings.
* Grid recovery requires an already coherent candidate system; it does not invent wholly unsupported grids.
* Shared-axis and offset regions may remain one connected system when geometry does not support a clean independent partition.
* Some structural drawing conventions may require additional grammar or geometric evidence.
* PDF internals such as Form XObjects can require fallback geometry extraction.
* Detection confidence represents evidence quality, not engineering-design certainty.
* ShopLens interprets drawing information; it does not replace structural engineering review.

---

## What ShopLens Is Not

ShopLens is **not** currently:

* a structural analysis engine
* a code-checking engine
* a connection-design program
* an OCR system
* a Revit or BIM model generator
* a drawing-comparison engine
* an AI system that guesses missing engineering information

Those capabilities should not be inferred from the current extraction and localization pipeline.

---

## Roadmap

The current foundation makes higher-level structural drawing intelligence possible.

Potential future milestones include:

```text
PDF understanding
      ↓
sheet identity
      ↓
structural sections
      ↓
grid systems
      ↓
section localization
      ↓
member understanding
      ↓
drawing relationships
      ↓
cross-sheet structural context
```

Each layer should remain evidence-backed and independently testable.

---

## Development

Run the unit suite:

```bash
python -m pytest tests/unit -q
```

Compile-check the Python package:

```bash
python -m compileall shoplens
```

Check the working diff for whitespace errors:

```bash
git diff --check
```

Before merging geometry-sensitive changes, run the repository's real-PDF acceptance and validation workflow appropriate to the affected subsystem.

---

## Security

ShopLens processes complex PDF input and should be kept current with supported parser dependencies.

The current `pypdf` fallback requires:

```text
pypdf>=6.15,<7
```

Applications accepting untrusted PDFs should keep ShopLens and its PDF-processing dependencies updated.

---

## Contributing

ShopLens is evolving toward a reusable structural drawing intelligence layer.

When contributing:

1. Reproduce the problem before changing detection logic.
2. Identify the smallest root cause.
3. Add a focused regression test.
4. Preserve existing cross-firm acceptance cases.
5. Prefer physical drawing evidence over filename- or project-specific heuristics.
6. Do not fabricate structural information to improve headline detection counts.
7. Run the relevant unit and real-PDF validation before merging.

---

## License

See [LICENSE](LICENSE) for repository licensing information.

ShopLens builds on Firecrawl's `pdf-inspector`; upstream attribution and licensing are preserved.
