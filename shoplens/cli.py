"""Command-line interface for ShopLens."""

import argparse
import json
import multiprocessing
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from shoplens.doctor import run_doctor
from shoplens.extraction import (
    PdfInspectorUnavailableError,
    extract_positioned_text,
    get_pdf_page_count,
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
    items, page_count, status = _load_all_pages_batched(path)
    if items is None or page_count is None:
        return None, None, status
    sheet_items = [item for item in items if int(item.page) <= 5]
    declared = extract_sheet_list(sheet_items, str(path), list(range(1, min(5, page_count) + 1)))
    actual = extract_title_blocks(
        items,
        str(path),
        list(range(1, page_count + 1)),
        declared.entries,
    )
    return declared, actual, 0


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
    return _run_reconcile_sheets(args)


if __name__ == "__main__":
    raise SystemExit(main())
