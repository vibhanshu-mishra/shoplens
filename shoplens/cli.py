"""Command-line interface for ShopLens."""

import argparse
import json
import multiprocessing
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from shoplens.doctor import run_doctor
from shoplens.classification import (
    SheetKind,
    StructuralSubject,
    build_package_index,
    filter_sheets,
)
from shoplens.extraction import (
    PdfInspectorUnavailableError,
    extract_positioned_text,
    get_pdf_page_count,
)
from shoplens.inventory import (
    InventoryFilters,
    build_section_inventory,
    export_inventory_csv,
    filter_inventory_sheets,
    matching_detections,
)
from shoplens.geometry import extract_page_geometry
from shoplens.grids import detect_grid_system, export_grid_svg
from shoplens.localization import (
    export_localization_svg,
    filter_localizations,
    localize_section_detections,
    with_filtered_detections,
)
from shoplens.members import (
    LineOrientation,
    detect_member_line_candidates,
    export_member_candidates_svg,
    filter_member_candidates,
)
from shoplens.models import SectionFamily, SteelLabel, TextDiagnostic
from shoplens.reporting import (
    build_summary,
    deduplicate_detections,
    filter_detections,
    filter_diagnostics,
)
from shoplens.sheets import extract_sheet_list
from shoplens.sheets.extract import sheet_prefix_counts
from shoplens.steel.detect import analyze_positioned_text
from shoplens.title_blocks import (
    ReconciliationStatus,
    extract_title_blocks,
    reconcile_sheets,
)
from shoplens.validation import (
    compare_reports,
    load_config,
    run_validation_suite,
    write_csv as write_validation_csv,
    write_json as write_validation_json,
    write_markdown as write_validation_markdown,
)


@dataclass(frozen=True)
class _TextSnapshot:
    text: str
    page: int
    x: float
    y: float
    width: float
    height: float
    font: Optional[str]
    font_size: float


def _extract_batch(payload):
    path, pages = payload
    return [
        (
            item.text,
            int(item.page),
            float(item.x),
            float(item.y),
            float(item.width),
            float(item.height),
            getattr(item, "font", None),
            float(getattr(item, "font_size", item.height)),
        )
        for item in extract_positioned_text(path, pages=pages)
    ]


def _add_filters(parser: argparse.ArgumentParser, diagnostics: bool = False) -> None:
    parser.add_argument("--page", type=_one_based_page, help="one-based drawing page number")
    parser.add_argument("--contains", help="case-insensitive text filter")
    parser.add_argument(
        "--family",
        action="append",
        choices=[family.value for family in SectionFamily if family is not SectionFamily.UNKNOWN],
        help="section family; repeat to select several",
    )
    if diagnostics:
        parser.add_argument("--candidates-only", action="store_true")
        parser.add_argument("--matches-only", action="store_true")


def _one_based_page(value: str) -> int:
    page = int(value)
    if page < 1:
        raise argparse.ArgumentTypeError("page must be 1 or greater")
    return page


def _confidence_value(value: str) -> float:
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise argparse.ArgumentTypeError("confidence must be between 0 and 1")
    return confidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect native-text PDFs for steel labels.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="summarize steel labels in one PDF")
    inspect_parser.add_argument("pdf", type=Path)
    inspect_parser.add_argument("--json", action="store_true", help="output structured JSON")
    inspect_parser.add_argument("--list", action="store_true", help="list individual records")
    inspect_parser.add_argument("--raw", action="store_true", help="use raw records, including duplicates")
    _add_filters(inspect_parser)

    debug_parser = subparsers.add_parser("debug-text", help="show positioned extraction diagnostics")
    debug_parser.add_argument("pdf", type=Path)
    debug_parser.add_argument("--json", action="store_true", help="output structured JSON")
    _add_filters(debug_parser, diagnostics=True)

    doctor_parser = subparsers.add_parser("doctor", help="check the ShopLens installation")
    doctor_parser.add_argument("pdf", type=Path, nargs="?", help="optional PDF to test")
    doctor_parser.add_argument("--json", action="store_true", help="output structured JSON")

    sheets_parser = subparsers.add_parser("sheet-list", help="extract the declared drawing Sheet List")
    sheets_parser.add_argument("pdf", type=Path)
    sheets_parser.add_argument(
        "--pages",
        type=_page_range,
        default=list(range(1, 6)),
        help="one-based page range (default: 1-5)",
    )
    sheets_parser.add_argument("--json", action="store_true", help="output all model fields as JSON")
    sheets_parser.add_argument("--list", action="store_true", help="list individual sheet entries")
    sheets_parser.add_argument("--debug", action="store_true", help="show table-detection evidence")

    title_parser = subparsers.add_parser("title-blocks", help="extract actual page title blocks")
    title_parser.add_argument("pdf", type=Path)
    title_parser.add_argument("--page", type=_one_based_page, help="show one PDF page after global layout discovery")
    title_parser.add_argument("--json", action="store_true", help="output all model fields as JSON")
    title_parser.add_argument("--list", action="store_true", help="list individual page identities")
    title_parser.add_argument("--debug", action="store_true", help="show candidate and layout evidence")

    reconcile_parser = subparsers.add_parser(
        "reconcile-sheets", help="compare declared sheets with actual title blocks"
    )
    reconcile_parser.add_argument("pdf", type=Path)
    reconcile_parser.add_argument("--json", action="store_true", help="output all model fields as JSON")
    reconcile_parser.add_argument("--list", action="store_true", help="list reconciliation records")
    reconcile_parser.add_argument("--debug", action="store_true", help="show title-block candidate evidence")

    index_parser = subparsers.add_parser(
        "package-index", help="classify and index reconciled structural sheets"
    )
    index_parser.add_argument("pdf", type=Path)
    index_parser.add_argument("--list", action="store_true", help="list classified sheets")
    index_parser.add_argument("--json", action="store_true", help="output structured JSON")
    index_parser.add_argument("--debug", action="store_true", help="explain considered classification rules")
    index_parser.add_argument("--sheet", help="filter by sheet number")
    index_parser.add_argument("--page", type=_one_based_page, help="filter by one-based PDF page")
    index_parser.add_argument("--kind", choices=[value.value for value in SheetKind])
    index_parser.add_argument("--subject", choices=[value.value for value in StructuralSubject])
    index_parser.add_argument("--level", help="filter by normalized building level")
    index_parser.add_argument("--segment", help="filter by segment identifier")
    index_parser.add_argument("--area", help="filter by named physical area")
    index_parser.add_argument("--unknown-only", action="store_true")

    inventory_parser = subparsers.add_parser(
        "section-inventory", help="join classified sheets to detected steel labels"
    )
    inventory_parser.add_argument("pdf", type=Path)
    inventory_parser.add_argument("--list", action="store_true", help="list per-sheet detection counts")
    inventory_parser.add_argument("--detections", action="store_true", help="show positioned detection records")
    inventory_parser.add_argument("--json", action="store_true", help="output structured JSON")
    inventory_parser.add_argument("--debug", action="store_true", help="explain joins and duplicate suppression")
    inventory_parser.add_argument("--raw", action="store_true", help="use every accepted source detection")
    inventory_parser.add_argument("--csv", type=Path, help="export matching detections to CSV")
    inventory_parser.add_argument("--sheet", help="filter by sheet number")
    inventory_parser.add_argument("--page", type=_one_based_page, help="filter by one-based PDF page")
    inventory_parser.add_argument("--kind", choices=[value.value for value in SheetKind])
    inventory_parser.add_argument("--subject", choices=[value.value for value in StructuralSubject])
    inventory_parser.add_argument("--level", help="filter by normalized building level")
    inventory_parser.add_argument("--segment", help="filter by segment identifier")
    inventory_parser.add_argument("--area", help="filter by named physical area")
    inventory_parser.add_argument(
        "--family", choices=[value.value for value in SectionFamily if value is not SectionFamily.UNKNOWN]
    )
    inventory_parser.add_argument("--section", help="filter by normalized steel section")
    presence = inventory_parser.add_mutually_exclusive_group()
    presence.add_argument("--with-detections", action="store_true")
    presence.add_argument("--without-detections", action="store_true")

    grid_parser = subparsers.add_parser(
        "grid-system", help="extract the dominant grid system from one plan sheet"
    )
    grid_parser.add_argument("pdf", type=Path)
    grid_selector = grid_parser.add_mutually_exclusive_group(required=True)
    grid_selector.add_argument("--sheet", help="select a reconciled sheet number")
    grid_selector.add_argument("--page", type=_one_based_page, help="select a one-based PDF page")
    grid_parser.add_argument("--list", action="store_true", help="list detected grid axes")
    grid_parser.add_argument("--json", action="store_true", help="output structured JSON")
    grid_parser.add_argument("--debug", action="store_true", help="show candidate and geometry evidence")
    grid_parser.add_argument("--svg", type=Path, help="write a geometry-only diagnostic SVG")

    locate_parser = subparsers.add_parser(
        "grid-locate-sections", help="locate section annotations relative to one sheet's grid"
    )
    locate_parser.add_argument("pdf", type=Path)
    locate_selector = locate_parser.add_mutually_exclusive_group(required=True)
    locate_selector.add_argument("--sheet", help="select a reconciled sheet number")
    locate_selector.add_argument("--page", type=_one_based_page, help="select a one-based PDF page")
    locate_parser.add_argument("--list", action="store_true", help="summarize sections by grid bay")
    locate_parser.add_argument("--detections", action="store_true", help="show every positioned localization")
    locate_parser.add_argument("--json", action="store_true", help="output structured JSON")
    locate_parser.add_argument("--debug", action="store_true", help="show confidence evidence and warnings")
    locate_parser.add_argument("--raw", action="store_true", help="use every accepted source detection")
    locate_parser.add_argument(
        "--family", choices=[value.value for value in SectionFamily if value is not SectionFamily.UNKNOWN]
    )
    locate_parser.add_argument("--section", help="filter by normalized steel section")
    location = locate_parser.add_mutually_exclusive_group()
    location.add_argument("--inside-only", action="store_true")
    location.add_argument("--outside-only", action="store_true")
    locate_parser.add_argument("--ambiguous-only", action="store_true")
    locate_parser.add_argument("--svg", type=Path, help="write a geometry-only localization SVG")

    member_parser = subparsers.add_parser(
        "member-line-candidates", help="extract conservative non-grid linear candidates"
    )
    member_parser.add_argument("pdf", type=Path)
    member_selector = member_parser.add_mutually_exclusive_group(required=True)
    member_selector.add_argument("--sheet", help="select a reconciled sheet number")
    member_selector.add_argument("--page", type=_one_based_page, help="select a one-based PDF page")
    member_parser.add_argument("--list", action="store_true", help="list accepted candidates")
    member_parser.add_argument("--json", action="store_true", help="output structured JSON")
    member_parser.add_argument("--debug", action="store_true", help="show scoring and rejection evidence")
    member_parser.add_argument("--svg", type=Path, help="write a geometry-only diagnostic SVG")
    member_parser.add_argument(
        "--orientation", choices=[value.value for value in LineOrientation if value is not LineOrientation.OTHER]
    )
    member_parser.add_argument("--inside-only", action="store_true")
    member_parser.add_argument("--min-confidence", type=_confidence_value, default=0.0)
    member_parser.add_argument("--include-rejected", action="store_true")
    member_parser.add_argument("--candidate", help="show one candidate ID")

    validation_parser = subparsers.add_parser(
        "validate-suite", help="run package-level checks across a local PDF corpus"
    )
    validation_parser.add_argument("evaluation_root", type=Path)
    validation_parser.add_argument("--json", type=Path, help="write a structured JSON report")
    validation_parser.add_argument("--markdown", type=Path, help="write a Markdown report")
    validation_parser.add_argument("--csv", type=Path, help="write a package CSV summary")
    validation_parser.add_argument("--config", type=Path, help="load local JSON configuration")
    validation_parser.add_argument("--debug", action="store_true", help="include absolute paths in JSON")
    validation_parser.add_argument("--file", action="append", default=[], help="select a relative file name")
    validation_parser.add_argument("--max-files", type=int)
    validation_parser.add_argument("--timeout-per-stage", type=float)
    validation_parser.add_argument("--stop-on-error", action="store_true")
    validation_parser.add_argument("--package-only", action="store_true")
    validation_parser.add_argument("--deep", action="store_true")
    validation_parser.add_argument("--compare", type=Path, help="compare against prior JSON")
    return parser


def _page_range(value: str) -> List[int]:
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("pages must look like 1-5 or 3")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise argparse.ArgumentTypeError("pages must be one-based and ascending")
    return list(range(start, end + 1))


def _families(values: Optional[Sequence[str]]) -> List[SectionFamily]:
    return [SectionFamily(value) for value in values or []]


def _validate_pdf(path: Path) -> Optional[str]:
    if not path.exists():
        return f"file does not exist: {path}"
    if not path.is_file():
        return f"path is not a file: {path}"
    if path.suffix.lower() != ".pdf":
        return "input must be a PDF file (a name ending in .pdf)."
    return None


def _load_items(
    path: Path,
    pages: Optional[Sequence[int]] = None,
    allow_empty: bool = False,
):
    error = _validate_pdf(path)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None, 2
    try:
        items = extract_positioned_text(path, pages=pages)
    except PdfInspectorUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None, 3
    except Exception as exc:
        print(f"Error: pdf-inspector could not read this PDF: {exc}", file=sys.stderr)
        return None, 4
    if not items and not allow_empty:
        print(
            "No extractable text was found. ShopLens currently supports native-text PDFs only; "
            "OCR is not included in this milestone.",
            file=sys.stderr,
        )
        return None, 5
    return items, 0


def _load_all_pages_batched(path: Path, batch_size: int = 3):
    error = _validate_pdf(path)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None, None, 2
    try:
        page_count = get_pdf_page_count(path)
        items = []
        batches = [
            (str(path), list(range(start, min(page_count, start + batch_size - 1) + 1)))
            for start in range(1, page_count + 1, batch_size)
        ]
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=1, maxtasksperchild=1) as pool:
            for extracted in pool.imap(_extract_batch, batches):
                items.extend(_TextSnapshot(*values) for values in extracted)
    except PdfInspectorUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None, None, 3
    except Exception as exc:
        print(f"Error: pdf-inspector could not read this PDF: {exc}", file=sys.stderr)
        return None, None, 4
    return items, page_count, 0


def _print_detection(item: SteelLabel) -> None:
    duplicate_note = f" | copies={item.duplicate_count}" if item.duplicate_count > 1 else ""
    print(
        f"Page {item.page_number} | {item.normalized_text} | "
        f"family={item.section_family.value} | x={item.x:.2f} | y={item.y:.2f}{duplicate_note}"
    )


def _run_inspect(args: argparse.Namespace) -> int:
    items, status = _load_items(args.pdf)
    if items is None:
        return status
    raw, rejections, _ = analyze_positioned_text(items)
    deduplicated, duplicate_count = deduplicate_detections(raw)
    selected = raw if args.raw else deduplicated
    displayed = filter_detections(selected, args.page, args.contains, _families(args.family))
    mode = "raw" if args.raw else "deduplicated"
    summary = build_summary(raw, displayed, rejections, duplicate_count, mode)

    if args.json:
        payload = {"summary": summary}
        if args.list:
            payload["records"] = [item.to_dict() for item in displayed]
        print(json.dumps(payload, indent=2))
        return 0

    print(f"ShopLens extraction summary (display mode: {mode})")
    print(f"Raw detections: {summary['total_raw_detections']}")
    print(f"Displayed detections: {summary['total_displayed_detections']}")
    print(f"Unique sections: {summary['total_unique_section_values']}")
    print(f"Duplicates suppressed: {summary['duplicate_count']}")
    print(f"Families: {summary['count_by_family']}")
    print(f"Pages with detections: {summary['pages_containing_detections']}")
    print(f"Negative X / Y: {summary['negative_x_detections']} / {summary['negative_y_detections']}")
    print(f"Rejected likely false positives: {summary['rejected_likely_false_positives']}")
    print(f"Most frequent sections: {summary['most_frequent_section_values']}")
    if args.list:
        print(f"\nIndividual {mode} records:")
        for item in displayed:
            _print_detection(item)
    else:
        print("Individual records are hidden; add --list to show them.")
    return 0


def _print_diagnostic(item: TextDiagnostic) -> None:
    matches = ",".join(match.normalized_text for match in item.detections) or "-"
    reasons = ",".join(item.rejection_reasons) or "-"
    print(
        f"Page {item.page_number} (source={item.source_page}) | text={item.text!r} | "
        f"x={item.x:.2f} y={item.y:.2f} w={item.width:.2f} h={item.height:.2f} | "
        f"font={item.font or '-'} size={item.font_size if item.font_size is not None else '-'} | "
        f"candidate={item.is_candidate} matched={item.section_detected} | "
        f"sections={matches} rejection={reasons}"
    )


def _run_debug_text(args: argparse.Namespace) -> int:
    items, status = _load_items(args.pdf)
    if items is None:
        return status
    _, _, diagnostics = analyze_positioned_text(items)
    displayed = filter_diagnostics(
        diagnostics,
        args.page,
        args.contains,
        _families(args.family),
        args.candidates_only,
        args.matches_only,
    )
    if args.json:
        print(json.dumps([item.to_dict() for item in displayed], indent=2))
    else:
        print(f"Positioned text items: {len(displayed)}")
        for item in displayed:
            _print_diagnostic(item)
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    checks = run_doctor(args.pdf)
    if args.json:
        print(json.dumps({"passed": all(item.passed for item in checks), "checks": [item.to_dict() for item in checks]}, indent=2))
    else:
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} | {check.name} | {check.detail}")
        if any(not check.passed for check in checks):
            print("One or more checks failed. Follow the correction shown above.")
    return 0 if all(item.passed for item in checks) else 1


def _run_sheet_list(args: argparse.Namespace) -> int:
    items, status = _load_items(args.pdf, pages=args.pages, allow_empty=True)
    if items is None:
        return status
    result = extract_sheet_list(items, str(args.pdf), args.pages)
    if args.json:
        print(json.dumps(result.to_dict(include_debug=args.debug), indent=2))
        return 0

    if result.sheet_list_pages:
        pages = ", ".join(str(page) for page in result.sheet_list_pages)
        print(f"Sheet List found on PDF page(s): {pages}")
    else:
        print("No extractable native-text Sheet List was found in the selected pages.")
    print(f"{len(result.entries)} sheet entries extracted")
    if result.declared_total is not None:
        print(f"Declared total: {result.declared_total}")
    print("\nPrefix summary:")
    prefix_counts = sheet_prefix_counts(result.entries)
    if prefix_counts:
        for prefix, count in prefix_counts.items():
            print(f"{prefix}: {count}")
    else:
        print("None")
    print("\nWarnings:")
    if result.warnings:
        for warning in result.warnings:
            print(f"- {warning}")
    else:
        print("None")
    if args.list:
        print("\nDeclared sheet entries:")
        for entry in result.entries:
            warning = f" | warnings={','.join(entry.warnings)}" if entry.warnings else ""
            print(
                f"{entry.sheet_number} | {entry.sheet_name or '[missing name]'} | "
                f"source page {entry.source_page}{warning}"
            )
    if args.debug:
        print("\nDebug evidence:")
        for page in result.debug:
            print(json.dumps(page, sort_keys=True))
    return 0


def _extract_package_title_blocks(path: Path):
    declared, actual, _, status = _extract_package_title_blocks_with_items(path)
    return declared, actual, status


def _extract_package_title_blocks_with_items(path: Path):
    items, page_count, status = _load_all_pages_batched(path)
    if items is None or page_count is None:
        return None, None, None, status
    sheet_items = [item for item in items if int(item.page) <= 5]
    declared = extract_sheet_list(sheet_items, str(path), list(range(1, min(5, page_count) + 1)))
    actual = extract_title_blocks(
        items,
        str(path),
        list(range(1, page_count + 1)),
        declared.entries,
    )
    return declared, actual, items, 0


def _run_title_blocks(args: argparse.Namespace) -> int:
    _, result, status = _extract_package_title_blocks(args.pdf)
    if result is None:
        return status
    displayed = [page for page in result.pages if args.page is None or page.pdf_page == args.page]
    if args.json:
        payload = result.to_dict(include_debug=args.debug)
        if args.page is not None:
            payload["pages"] = [page.to_dict() for page in displayed]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"Total PDF pages processed: {result.total_pdf_pages_processed}")
    print(f"Pages identified: {result.identified_page_count}")
    print(f"Pages unidentified: {len(result.unidentified_pages)}")
    print(f"Layouts discovered: {len(result.layouts_discovered)}")
    print(f"Low-confidence pages: {len(result.low_confidence_pages)}")
    print(f"Duplicate sheet numbers: {len(result.duplicate_sheet_numbers)}")
    if args.list or args.page is not None:
        print("\nActual title blocks:")
        for page in displayed:
            print(
                f"PDF page {page.pdf_page} | {page.sheet_number or '[unidentified]'} | "
                f"{page.sheet_title or '[missing title]'} | confidence={page.confidence:.2f}"
            )
    if args.debug:
        print("\nLayouts:")
        for layout in result.layouts_discovered:
            print(json.dumps(layout, sort_keys=True))
        print("\nCandidate evidence:")
        for page in result.debug:
            if args.page is None or page["pdf_page"] == args.page:
                print(json.dumps(page, sort_keys=True))
    return 0


def _run_reconcile_sheets(args: argparse.Namespace) -> int:
    declared, actual, status = _extract_package_title_blocks(args.pdf)
    if declared is None or actual is None:
        return status
    result = reconcile_sheets(declared, actual)
    if args.json:
        payload = result.to_dict()
        if args.debug:
            payload["title_block_debug"] = actual.debug
            payload["layouts_discovered"] = actual.layouts_discovered
        print(json.dumps(payload, indent=2))
        return 0
    counts = {status: 0 for status in ReconciliationStatus}
    for entry in result.entries:
        counts[entry.status] += 1
    print(f"Declared sheets: {result.declared_sheet_count}")
    print(f"PDF pages processed: {result.total_pdf_pages_processed}")
    print(f"Actual sheets identified: {result.identified_page_count}")
    print(f"Matches: {counts[ReconciliationStatus.MATCH]}")
    print(f"Title variations: {counts[ReconciliationStatus.TITLE_VARIATION]}")
    print(f"Title mismatches: {counts[ReconciliationStatus.TITLE_MISMATCH]}")
    print(f"Declared but missing: {counts[ReconciliationStatus.DECLARED_BUT_MISSING]}")
    print(f"Present but undeclared: {counts[ReconciliationStatus.PRESENT_BUT_UNDECLARED]}")
    print(f"Duplicate sheet numbers: {len(result.duplicate_actual_sheet_numbers)}")
    print(f"Unidentified PDF pages: {len(result.unidentified_pages)}")
    if args.list:
        print("\nReconciliation records:")
        for entry in result.entries:
            number = entry.actual_sheet_number or entry.declared_sheet_number or "[unidentified]"
            pages = ",".join(str(page) for page in entry.actual_pdf_pages) or "-"
            detail = ""
            if entry.status in {ReconciliationStatus.TITLE_VARIATION, ReconciliationStatus.TITLE_MISMATCH}:
                detail = f' | declared="{entry.declared_sheet_title}" | actual="{entry.actual_sheet_title}"'
            print(f"{entry.status.value} | {number} | PDF page(s) {pages}{detail}")
    if args.debug:
        print("\nLayouts:")
        for layout in actual.layouts_discovered:
            print(json.dumps(layout, sort_keys=True))
        print("\nCandidate evidence:")
        for page in actual.debug:
            print(json.dumps(page, sort_keys=True))
    return 0


def _run_package_index(args: argparse.Namespace) -> int:
    declared, actual, status = _extract_package_title_blocks(args.pdf)
    if declared is None or actual is None:
        return status
    result = build_package_index(reconcile_sheets(declared, actual))
    displayed = filter_sheets(
        result.sheets,
        sheet_number=args.sheet,
        page=args.page,
        kind=SheetKind(args.kind) if args.kind else None,
        subject=StructuralSubject(args.subject) if args.subject else None,
        level=args.level,
        segment=args.segment,
        area=args.area,
        unknown_only=args.unknown_only,
    )
    active_filters = _active_index_filters(args)
    if args.json:
        payload = result.to_dict(include_debug=args.debug)
        payload["sheets"] = [sheet.to_dict(include_debug=args.debug) for sheet in displayed]
        payload["displayed_sheet_count"] = len(displayed)
        if active_filters:
            payload["filtered_count"] = len(displayed)
            payload["active_filters"] = active_filters
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Package: {args.pdf.name}")
    print(f"Total indexed sheets: {result.indexed_sheet_count}")
    if active_filters:
        print(f"Matching sheets: {len(displayed)}")
        filters = ", ".join(
            f"{key}={str(value).lower() if isinstance(value, bool) else value}"
            for key, value in active_filters.items()
        )
        print(f"Active filters: {filters}")
        print("\nWhole-package summary:")
    print(f"Classified sheets: {result.classified_sheet_count}")
    print(f"Unknown sheets: {result.unknown_sheet_count}")
    _print_counts("By kind", result.counts_by_kind)
    _print_counts("By subject", result.counts_by_subject)
    _print_counts("By level", result.counts_by_level)
    _print_counts("By segment", result.counts_by_segment)
    _print_counts("By area", result.counts_by_area)
    if displayed and (args.list or active_filters):
        print("\nMatching sheets:" if active_filters else "\nIndexed sheets:")
        for sheet in displayed:
            fields = [
                f"PDF {sheet.pdf_page if sheet.pdf_page is not None else '-'}",
                sheet.sheet_number or "[unidentified]",
                sheet.sheet_kind.value,
                sheet.subject.value,
            ]
            if sheet.level:
                fields.append(f"level={sheet.level}")
            if sheet.segment:
                fields.append(f"segment={sheet.segment}")
            if sheet.area:
                fields.append(f"area={','.join(sheet.area)}")
            print(" | ".join(fields))
    elif active_filters:
        print("\nNo sheets matched the selected filters.")
    if args.debug and displayed:
        print("\nClassification explanations:")
        for sheet in displayed:
            print(f"\n{sheet.sheet_number or '[unidentified]'} | PDF {sheet.pdf_page}")
            print(f"Original actual title: {sheet.actual_title or '-'}")
            print(f"Original declared title: {sheet.declared_title or '-'}")
            print(f"Normalized title: {sheet.classification_title or '-'}")
            print(f"Candidate rules: {', '.join(sheet.candidate_rules) or '-'}")
            print(f"Matched rule: {sheet.matched_rule or '-'}")
            print(f"Evidence: {', '.join(sheet.classification_evidence) or '-'}")
            print(f"Confidence: {sheet.classification_confidence:.2f}")
            print(f"Warnings: {', '.join(sheet.warnings) or '-'}")
    return 0


def _run_section_inventory(args: argparse.Namespace) -> int:
    declared, actual, items, status = _extract_package_title_blocks_with_items(args.pdf)
    if declared is None or actual is None or items is None:
        return status
    package_index = build_package_index(reconcile_sheets(declared, actual))
    raw_detections, _, _ = analyze_positioned_text(items)
    result = build_section_inventory(package_index, raw_detections, raw=args.raw)
    family = SectionFamily(args.family) if args.family else None
    inventory_filters = InventoryFilters(
        sheet_number=args.sheet,
        page=args.page,
        kind=SheetKind(args.kind) if args.kind else None,
        subject=StructuralSubject(args.subject) if args.subject else None,
        level=args.level,
        segment=args.segment,
        area=args.area,
        family=family,
        section=args.section,
        with_detections=args.with_detections,
        without_detections=args.without_detections,
    )
    displayed = filter_inventory_sheets(result.sheets, inventory_filters)
    active_filters = _active_inventory_filters(args)
    selected_detections = [
        detection
        for sheet in displayed
        for detection in matching_detections(sheet, family=family, section=args.section)
    ]

    csv_rows = None
    if args.csv:
        try:
            csv_rows = export_inventory_csv(args.csv, selected_detections)
        except OSError as exc:
            print(f"Error: could not write CSV: {exc}", file=sys.stderr)
            return 6

    if args.json:
        payload = result.to_dict()
        payload["sheets"] = [
            _inventory_sheet_payload(sheet, family, args.section) for sheet in displayed
        ]
        payload["filtered_sheet_count"] = len(displayed)
        payload["filtered_detection_count"] = len(selected_detections)
        payload["active_filters"] = active_filters
        if args.csv:
            payload["csv_export"] = {"path": str(args.csv), "rows_written": csv_rows}
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Package: {args.pdf.name}")
    print(f"Indexed sheets: {result.total_indexed_sheets}")
    print(f"Sheets with detected sections: {result.sheets_with_detections}")
    print(f"Sheets without detected sections: {result.sheets_without_detections}")
    print(f"Raw detections: {result.raw_detection_count}")
    print(f"Deduplicated detections: {result.deduplicated_detection_count}")
    print(f"Unique normalized sections: {result.unique_section_count}")
    print(f"Record mode: {result.record_mode}")
    if active_filters:
        print(f"Matching sheets: {len(displayed)}")
        print(f"Matching detections: {len(selected_detections)}")
        print(f"Active filters: {_format_filters(active_filters)}")

    if active_filters:
        print("\nWhole-package summary:")
    print("\nTop detected sections:")
    for section, counts in sorted(
        result.counts_by_section.items(),
        key=lambda value: (-value[1].detection_count, value[0]),
    )[:10]:
        print(
            f"{section}: {counts.detection_count} detections on "
            f"{counts.sheet_count} sheets"
        )
    print("\nBy family:")
    for family_name, counts in result.counts_by_family.items():
        print(
            f"{family_name}: {counts.detection_count} detections on "
            f"{counts.sheet_count} sheets"
        )

    if displayed and (args.list or active_filters or args.detections or args.debug):
        print("\nSheet inventories:")
        for sheet in displayed:
            detections = matching_detections(sheet, family=family, section=args.section)
            fields = [
                f"PDF {sheet.pdf_page if sheet.pdf_page is not None else '-'}",
                sheet.sheet_number or "[unidentified]",
                sheet.sheet_subject.value,
            ]
            if sheet.level:
                fields.append(sheet.level)
            if sheet.segment:
                fields.append(f"SEGMENT {sheet.segment}")
            if sheet.area:
                fields.append(",".join(sheet.area))
            print(" | ".join(fields))
            print(
                f"  detections={len(detections)} | "
                f"unique_sections={len({item.normalized_section for item in detections})}"
            )
            counts = Counter(item.normalized_section for item in detections)
            if counts:
                print("  " + " | ".join(f"{key}={value}" for key, value in counts.most_common()))
            if args.detections:
                for detection in detections:
                    print(
                        f"  PDF {detection.pdf_page} | {detection.normalized_section} | "
                        f"x={detection.raw_x:.2f} y={detection.raw_y:.2f} "
                        f"w={detection.raw_width:.2f} h={detection.raw_height:.2f} | "
                        f"copies={detection.duplicate_count}"
                    )
    elif active_filters:
        print("\nNo sheets matched the selected filters.")

    if args.debug:
        print("\nInventory diagnostics:")
        print(f"Package-index records used: {len(displayed)}")
        extraction_pages = sorted({page for sheet in displayed for page in sheet.pdf_pages})
        print(
            "Detection extraction pages used: "
            + (", ".join(str(page) for page in extraction_pages) or "none")
        )
        for sheet in displayed:
            print(
                f"{sheet.sheet_number or '[unidentified]'} | raw={sheet.raw_detection_count} | "
                f"deduplicated={sheet.deduplicated_detection_count} | "
                f"families={sheet.counts_by_family} | sections={sheet.counts_by_section}"
            )
            for warning in sheet.warnings:
                print(f"Sheet warning: {warning}")
        print(f"Duplicate records suppressed: {result.duplicate_suppression_count}")
        print(f"Unmatched or ambiguous detections: {result.unmatched_detection_count}")
        print(f"Applied filters: {_format_filters(active_filters) if active_filters else 'none'}")
        for warning in result.warnings:
            print(f"Warning: {warning}")
    if args.csv:
        print(f"CSV export: {csv_rows} detection rows written to {args.csv}")
    return 0


def _run_grid_system(args: argparse.Namespace) -> int:
    declared, actual, items, status = _extract_package_title_blocks_with_items(args.pdf)
    if declared is None or actual is None or items is None:
        return status
    package = build_package_index(reconcile_sheets(declared, actual))
    selected = None
    if args.sheet:
        normalized = args.sheet.strip().upper()
        selected = next((sheet for sheet in package.sheets if sheet.sheet_number == normalized), None)
        if selected is None:
            print(f"Error: sheet {normalized} was not found in the package index.", file=sys.stderr)
            return 7
        page = selected.pdf_page
    else:
        page = args.page
        selected = next((sheet for sheet in package.sheets if page in sheet.actual_pdf_pages), None)
    if page is None:
        print("Error: the selected sheet has no reconciled PDF page.", file=sys.stderr)
        return 7
    page_items = [item for item in items if int(item.page) == page]
    try:
        geometry = extract_page_geometry(args.pdf, [page], page_items)[0]
    except (OSError, RuntimeError, ValueError, IndexError) as exc:
        print(f"Error: could not extract page geometry: {exc}", file=sys.stderr)
        return 8
    grid = detect_grid_system(str(args.pdf), geometry, page_items, selected)
    if args.svg:
        try:
            export_grid_svg(args.svg, grid, include_rejected=args.debug)
        except OSError as exc:
            print(f"Error: could not write SVG: {exc}", file=sys.stderr)
            return 9
    if args.json:
        payload = grid.to_dict()
        if args.svg:
            payload["svg_export"] = str(args.svg)
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Sheet: {grid.sheet_number or '[unidentified]'}")
    print(f"PDF page: {grid.pdf_page}")
    print(f"Horizontal grid axes: {len(grid.horizontal_axes)}")
    print(f"Vertical grid axes: {len(grid.vertical_axes)}")
    print(f"Unassigned grid labels: {len(grid.unassigned_labels)}")
    print(f"Rejected candidates: {len(grid.rejected_candidates)}")
    print(f"Grid confidence: {grid.confidence:.3f}")
    if args.list:
        print("\nGrid axes:")
        for axis in grid.vertical_axes + grid.horizontal_axes:
            print(
                f"{axis.orientation.value} | {axis.normalized_label} | "
                f"{'x' if axis.orientation.value == 'VERTICAL' else 'y'}={axis.coordinate:.2f} | "
                f"labels={len(axis.label_candidates)} | intersections={axis.intersection_count} | "
                f"confidence={axis.confidence:.3f}"
            )
    if args.debug:
        print("\nGrid diagnostics:")
        print(f"Geometry provider: {geometry.provider}")
        print(f"Page geometry: width={geometry.width:.2f} height={geometry.height:.2f} rotation={geometry.rotation}")
        print(f"Media box: {geometry.media_box}")
        print(f"Crop box: {geometry.crop_box}")
        print(f"Coordinate system: {geometry.coordinate_system}")
        print(f"Coordinate conversion: {geometry.conversion}")
        print(f"Line candidates: {len(geometry.lines)}")
        print(f"Shape candidates: {len(geometry.shapes)}")
        for axis in grid.vertical_axes + grid.horizontal_axes:
            print(
                f"Axis {axis.axis_id} | segments={len(axis.source_segments)} | "
                f"extent=({axis.start_x:.2f},{axis.start_y:.2f})-({axis.end_x:.2f},{axis.end_y:.2f}) | "
                f"evidence={','.join(axis.evidence)}"
            )
        for label in grid.unassigned_labels:
            print(f"Unassigned label: {label.normalized_label} at ({label.center_x:.2f},{label.center_y:.2f})")
        for candidate in grid.rejected_candidates:
            print(f"Rejected candidate: {candidate.original_text or '[empty]'} | reason={candidate.reason} | x={candidate.x:.2f} y={candidate.y:.2f}")
        for warning in grid.warnings:
            print(f"Warning: {warning}")
    if args.svg:
        print(f"SVG export: {args.svg}")
    return 0


def _run_grid_locate_sections(args: argparse.Namespace) -> int:
    declared, actual, items, status = _extract_package_title_blocks_with_items(args.pdf)
    if declared is None or actual is None or items is None:
        return status
    package = build_package_index(reconcile_sheets(declared, actual))
    selected = _selected_package_sheet(package.sheets, args.sheet, args.page)
    if selected is None:
        identity = args.sheet.strip().upper() if args.sheet else f"PDF page {args.page}"
        print(f"Error: {identity} was not found in the package index.", file=sys.stderr)
        return 7
    page = selected.pdf_page
    if page is None:
        print("Error: the selected sheet has no reconciled PDF page.", file=sys.stderr)
        return 7
    raw_detections, _, _ = analyze_positioned_text(items)
    inventory = build_section_inventory(package, raw_detections, raw=args.raw)
    sheet_inventory = next(
        (sheet for sheet in inventory.sheets if sheet.sheet_number == selected.sheet_number and page in sheet.pdf_pages),
        None,
    )
    if sheet_inventory is None:
        print("Error: the selected sheet could not be joined to section detections.", file=sys.stderr)
        return 7
    page_items = [item for item in items if int(item.page) == page]
    try:
        geometry = extract_page_geometry(args.pdf, [page], page_items)[0]
    except (OSError, RuntimeError, ValueError, IndexError) as exc:
        print(f"Error: could not extract page geometry: {exc}", file=sys.stderr)
        return 8
    grid = detect_grid_system(str(args.pdf), geometry, page_items, selected)
    result = localize_section_detections(
        str(args.pdf), sheet_inventory.detections, grid, record_mode=inventory.record_mode
    )
    family = SectionFamily(args.family) if args.family else None
    displayed = filter_localizations(
        result.detections, family=family, section=args.section,
        inside_only=args.inside_only, outside_only=args.outside_only,
        ambiguous_only=args.ambiguous_only,
    )
    detection_filters = {
        key: value for key, value in {
            "family": args.family, "section": args.section,
            "inside-only": True if args.inside_only else None,
            "outside-only": True if args.outside_only else None,
            "ambiguous-only": True if args.ambiguous_only else None,
        }.items() if value is not None
    }
    active_filters = {
        key: value for key, value in {
            "sheet": args.sheet.strip().upper() if args.sheet else None,
            "page": args.page,
            **detection_filters,
        }.items() if value is not None
    }
    output_result = with_filtered_detections(result, displayed, active_filters)
    if args.svg:
        try:
            export_localization_svg(args.svg, output_result)
        except (OSError, ValueError) as exc:
            print(f"Error: could not write SVG: {exc}", file=sys.stderr)
            return 9
    if args.json:
        payload = output_result.to_dict()
        payload["filtered_detection_count"] = len(displayed)
        payload["raw_mode"] = args.raw
        if args.svg:
            payload["svg_export"] = str(args.svg)
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Sheet: {result.sheet_number or '[unidentified]'}")
    print(f"PDF page: {result.pdf_page}")
    print(f"Grid axes: {len(grid.horizontal_axes)} horizontal, {len(grid.vertical_axes)} vertical")
    print(f"Section detections: {result.total_section_detections}")
    print(f"Localized detections: {result.localized_detection_count}")
    print(f"Complete grid bays: {result.detections_with_complete_bay}")
    print(f"On axes: {result.detections_on_axes}")
    print(f"Inside grid: {result.inside_grid_count}")
    print(f"Outside grid: {result.outside_grid_count}")
    print(f"Ambiguous: {result.ambiguous_detection_count}")
    print(f"Record mode: {result.record_mode}")
    if detection_filters:
        print(f"Matching detections: {len(displayed)}")
        print(f"Active filters: {_format_filters(detection_filters)}")
    if args.list:
        _print_localization_groups(displayed)
    if args.detections:
        print("\nSection localizations:")
        for item in displayed:
            nearest = f"{item.nearest_vertical_axis or '-'}, {item.nearest_horizontal_axis or '-'}"
            print(
                f"{item.normalized_section} | x={item.detection_anchor_x:.2f} | "
                f"y={item.detection_anchor_y:.2f} | bay={item.bay_id or _location_label(item)} | "
                f"nearest={nearest} | confidence={item.localization_confidence:.3f}"
            )
    if args.debug:
        print("\nLocalization diagnostics:")
        print(f"Coordinate system: {geometry.coordinate_system}")
        print("Detection anchor: bounding-box center")
        print("Signed distance: anchor coordinate minus axis coordinate, in PDF coordinate units")
        for item in displayed:
            print(
                f"{item.normalized_section} @ ({item.detection_anchor_x:.2f},{item.detection_anchor_y:.2f}) | "
                f"evidence={','.join(item.evidence) or '-'} | warnings={','.join(item.warnings) or '-'}"
            )
        for warning in result.warnings:
            print(f"Warning: {warning}")
    if args.svg:
        print(f"SVG export: {args.svg}")
    return 0


def _selected_package_sheet(sheets, sheet_number, page):
    if sheet_number:
        normalized = sheet_number.strip().upper()
        return next((sheet for sheet in sheets if sheet.sheet_number == normalized), None)
    return next((sheet for sheet in sheets if page in sheet.actual_pdf_pages), None)


def _location_label(item) -> str:
    if item.vertical_interval and item.horizontal_interval:
        return f"{item.vertical_interval} / {item.horizontal_interval}"
    return "outside grid" if not item.inside_grid_bounds else "incomplete bay"


def _print_localization_groups(detections) -> None:
    print("\nSection locations:")
    if not detections:
        print("No section detections matched the selected filters.")
        return
    by_section = {}
    for item in detections:
        by_section.setdefault(item.normalized_section, Counter())[_location_label(item)] += 1
    for section in sorted(by_section):
        counts = by_section[section]
        print(f"{section} | count={sum(counts.values())}")
        for location, count in counts.most_common():
            print(f"  {location}: {count}")


def _run_member_line_candidates(args: argparse.Namespace) -> int:
    declared, actual, items, status = _extract_package_title_blocks_with_items(args.pdf)
    if declared is None or actual is None or items is None:
        return status
    package = build_package_index(reconcile_sheets(declared, actual))
    selected = _selected_package_sheet(package.sheets, args.sheet, args.page)
    if selected is None:
        identity = args.sheet.strip().upper() if args.sheet else f"PDF page {args.page}"
        print(f"Error: {identity} was not found in the package index.", file=sys.stderr)
        return 7
    page = selected.pdf_page
    if page is None:
        print("Error: the selected sheet has no reconciled PDF page.", file=sys.stderr)
        return 7
    page_items = [item for item in items if int(item.page) == page]
    try:
        geometry = extract_page_geometry(args.pdf, [page], page_items)[0]
    except (OSError, RuntimeError, ValueError, IndexError) as exc:
        print(f"Error: could not extract page geometry: {exc}", file=sys.stderr)
        return 8
    grid = detect_grid_system(str(args.pdf), geometry, page_items, selected)
    result = detect_member_line_candidates(str(args.pdf), geometry, grid, page_items, selected)
    orientation = LineOrientation(args.orientation) if args.orientation else None
    displayed = filter_member_candidates(
        result.candidates, orientation, args.inside_only, args.min_confidence, args.candidate
    )
    active_filters = {
        key: value for key, value in {
            "sheet": args.sheet.strip().upper() if args.sheet else None,
            "page": args.page,
            "orientation": args.orientation,
            "inside-only": True if args.inside_only else None,
            "min-confidence": args.min_confidence if args.min_confidence else None,
            "candidate": args.candidate.strip().upper() if args.candidate else None,
        }.items() if value is not None
    }
    result.active_filters = active_filters
    if args.svg:
        try:
            export_member_candidates_svg(
                args.svg, result, displayed, include_rejected=args.include_rejected or args.debug
            )
        except OSError as exc:
            print(f"Error: could not write SVG: {exc}", file=sys.stderr)
            return 9
    if args.json:
        payload = result.to_dict(include_rejected=args.include_rejected or args.debug)
        payload["candidates"] = [item.to_dict() for item in displayed]
        payload["filtered_candidate_count"] = len(displayed)
        if args.svg:
            payload["svg_export"] = str(args.svg)
        print(json.dumps(payload, indent=2))
        return 0

    by_orientation = Counter(item.orientation_class.value for item in displayed)
    grid_to_grid = sum(_grid_endpoint_count(item) == 2 for item in displayed)
    one_end = sum(_grid_endpoint_count(item) == 1 for item in displayed)
    print(f"Sheet: {result.sheet_number or '[unidentified]'}")
    print(f"PDF page: {result.pdf_page}")
    print(f"Raw vector segments: {result.raw_segment_count}")
    print(f"Duplicate segments suppressed: {result.duplicate_segment_count}")
    print(f"Unique segments evaluated: {result.deduplicated_segment_count}")
    print(
        "Primitive segments rejected before merging: "
        f"{result.primitive_segments_rejected_count}"
    )
    print(
        "Segments entering chain construction: "
        f"{result.primitive_segments_entering_merge_count}"
    )
    print(f"Merged chains evaluated: {result.merged_chain_count}")
    print(f"Accepted member-line candidates: {result.accepted_candidate_count}")
    print(f"Rejected chains: {result.rejected_chain_count}")
    print(f"Displayed candidates: {len(displayed)}")
    print("\nBy orientation:")
    for value in ("HORIZONTAL", "VERTICAL", "DIAGONAL", "OTHER"):
        print(f"{value}: {by_orientation[value]}")
    print("\nBy grid relationship:")
    print(f"Grid-to-grid candidates: {grid_to_grid}")
    print(f"One-end-at-grid candidates: {one_end}")
    print(f"Inside-grid candidates: {sum(item.inside_dominant_grid for item in displayed)}")
    print(f"Outside-grid candidates: {sum(not item.inside_dominant_grid for item in displayed)}")
    print("\nConfidence distribution:")
    print(f"High (>=0.80): {sum(item.confidence >= 0.80 for item in displayed)}")
    print(f"Medium (0.65-0.79): {sum(0.65 <= item.confidence < 0.80 for item in displayed)}")
    print(f"Low (<0.65): {sum(item.confidence < 0.65 for item in displayed)}")
    if args.list:
        print("\nMember-line candidates:")
        if not displayed:
            print("No member-line candidates matched the selected filters.")
        for item in displayed:
            location = f"{item.start_grid_location} -> {item.end_grid_location}"
            print(
                f"{item.candidate_id} | {item.orientation_class.value} | "
                f"length={item.length:.2f} | {location} | confidence={item.confidence:.3f}"
            )
    if args.debug:
        print("\nCandidate diagnostics:")
        print("Stage accounting:")
        print(
            "  raw = duplicates + unique: "
            f"{result.raw_segment_count} = {result.duplicate_segment_count} + "
            f"{result.deduplicated_segment_count}"
        )
        print(
            "  unique = primitive rejected + entering merge: "
            f"{result.deduplicated_segment_count} = "
            f"{result.primitive_segments_rejected_count} + "
            f"{result.primitive_segments_entering_merge_count}"
        )
        print(
            "  chains = accepted + rejected: "
            f"{result.merged_chain_count} = {result.accepted_candidate_count} + "
            f"{result.rejected_chain_count}"
        )
        print(f"Plan-region bounds: {result.plan_region_bounds}")
        print(f"Plan-region margin: {result.plan_region_margin:.2f}")
        print(f"Coordinate system: {geometry.coordinate_system}")
        print(f"Coordinate conversion: {geometry.conversion}")
        for item in displayed:
            print(
                f"{item.candidate_id} | sources={item.source_segment_count} | "
                f"evidence={','.join(item.evidence)} | warnings={','.join(item.warnings) or '-'}"
            )
        reasons = Counter(item.rejection_reason for item in result.rejected_candidates)
        for reason, count in reasons.most_common():
            print(f"Rejected {reason}: {count}")
        if args.include_rejected:
            for item in result.rejected_candidates:
                print(
                    f"Rejected geometry | reason={item.rejection_reason} | "
                    f"({item.start_x:.2f},{item.start_y:.2f})-({item.end_x:.2f},{item.end_y:.2f}) | "
                    f"evidence={','.join(item.evidence)}"
                )
    if args.svg:
        print(f"SVG export: {args.svg}")
    return 0


def _grid_endpoint_count(item) -> int:
    return int(item.start_near_grid) + int(item.end_near_grid)


def _inventory_sheet_payload(sheet, family, section):
    payload = sheet.to_dict()
    detections = matching_detections(sheet, family=family, section=section)
    payload["detections"] = [item.to_dict() for item in detections]
    payload["matched_detection_count"] = len(detections)
    return payload


def _print_counts(label: str, values) -> None:
    print(f"\n{label}:")
    if not values:
        print("None")
        return
    for key, count in values.items():
        print(f"{key}: {count}")


def _active_index_filters(args: argparse.Namespace) -> Dict[str, object]:
    values = {
        "sheet": args.sheet,
        "page": args.page,
        "kind": args.kind,
        "subject": args.subject,
        "level": args.level,
        "segment": args.segment,
        "area": args.area,
        "unknown-only": True if args.unknown_only else None,
    }
    return {key: value for key, value in values.items() if value is not None}


def _active_inventory_filters(args: argparse.Namespace) -> Dict[str, object]:
    values = {
        "sheet": args.sheet,
        "page": args.page,
        "kind": args.kind,
        "subject": args.subject,
        "level": args.level,
        "segment": args.segment,
        "area": args.area,
        "family": args.family,
        "section": args.section,
        "with-detections": True if args.with_detections else None,
        "without-detections": True if args.without_detections else None,
    }
    return {key: value for key, value in values.items() if value is not None}


def _format_filters(filters: Dict[str, object]) -> str:
    return ", ".join(
        f"{key}={str(value).lower() if isinstance(value, bool) else value}"
        for key, value in filters.items()
    )


def _run_validate_suite(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: invalid validation configuration: {exc}", file=sys.stderr)
        return 2
    root = args.evaluation_root
    if not root.exists() or not root.is_dir():
        print("Error: evaluation root must be an existing directory.", file=sys.stderr)
        return 2
    if args.max_files is not None and args.max_files < 0:
        print("Error: --max-files must be nonnegative.", file=sys.stderr)
        return 2
    timeout = args.timeout_per_stage if args.timeout_per_stage is not None else config.timeout_per_stage
    if timeout is not None and timeout <= 0:
        print("Error: --timeout-per-stage must be greater than zero.", file=sys.stderr)
        return 2
    selected = args.file or config.selected_files
    max_files = args.max_files if args.max_files is not None else config.max_files
    result = run_validation_suite(
        root, include_patterns=config.include_patterns,
        exclude_patterns=config.exclude_patterns, selected_files=selected,
        max_files=max_files, timeout_per_stage=timeout, stop_on_error=args.stop_on_error,
    )
    if args.deep or config.deep_validation_enabled:
        result.warnings.append("DEEP_VALIDATION_NOT_IMPLEMENTED_PACKAGE_STAGES_ONLY")
    if args.compare:
        try:
            baseline = json.loads(args.compare.read_text(encoding="utf-8"))
            result.comparison = compare_reports(result.to_dict(debug=False), baseline)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: invalid comparison baseline: {exc}", file=sys.stderr)
            return 2
    if not any((args.json, args.markdown, args.csv)):
        output = Path(config.output_directory)
        args.json = output / "current.json"
        args.markdown = output / "current.md"
        args.csv = output / "current.csv"
    if args.json:
        write_validation_json(args.json, result, debug=args.debug)
    if args.markdown:
        write_validation_markdown(args.markdown, result)
    if args.csv:
        write_validation_csv(args.csv, result)
    print("ShopLens Validation Suite")
    print(f"\nEvaluation PDFs: {result.pdf_count}")
    print(f"Packages passed: {result.packages_passed}")
    print(f"Packages with warnings: {result.packages_with_warnings}")
    print(f"Packages failed: {result.packages_failed}")
    print(f"Total runtime: {result.runtime_seconds:.3f} seconds")
    for package in result.package_results:
        print(f"\n{package.overall_status.value} | {package.relative_path}")
        for stage in package.stages:
            detail = _validation_stage_detail(stage)
            print(f"  {stage.stage_name}: {stage.status.value}{detail}")
        for error in package.errors:
            print(f"  Error: {error}")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    return 1 if result.packages_failed else 0


def _validation_stage_detail(stage) -> str:
    metrics = stage.metrics
    if stage.stage_name == "SHEET_LIST" and "declared_sheet_count" in metrics:
        return f" | {metrics['declared_sheet_count']} declared sheets"
    if stage.stage_name == "TITLE_BLOCKS" and "page_count" in metrics:
        return f" | {metrics.get('identified_page_count', 0)}/{metrics['page_count']} identified"
    if stage.stage_name == "SHEET_RECONCILIATION" and "match_count" in metrics:
        return f" | {metrics['match_count']} matches"
    if stage.stage_name == "PACKAGE_CLASSIFICATION" and "classified_sheet_count" in metrics:
        return f" | {metrics['classified_sheet_count']} classified"
    return ""


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "debug-text":
        return _run_debug_text(args)
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "sheet-list":
        return _run_sheet_list(args)
    if args.command == "title-blocks":
        return _run_title_blocks(args)
    if args.command == "reconcile-sheets":
        return _run_reconcile_sheets(args)
    if args.command == "package-index":
        return _run_package_index(args)
    if args.command == "section-inventory":
        return _run_section_inventory(args)
    if args.command == "grid-system":
        return _run_grid_system(args)
    if args.command == "grid-locate-sections":
        return _run_grid_locate_sections(args)
    if args.command == "validate-suite":
        return _run_validate_suite(args)
    return _run_member_line_candidates(args)


if __name__ == "__main__":
    raise SystemExit(main())
