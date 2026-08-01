"""Command-line interface for ShopLens."""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from shoplens.doctor import run_doctor
from shoplens.extraction import PdfInspectorUnavailableError, extract_positioned_text
from shoplens.models import SectionFamily, SteelLabel, TextDiagnostic
from shoplens.reporting import (
    build_summary,
    deduplicate_detections,
    filter_detections,
    filter_diagnostics,
)
from shoplens.steel.detect import analyze_positioned_text


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
    return parser


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


def _load_items(path: Path):
    error = _validate_pdf(path)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None, 2
    try:
        items = extract_positioned_text(path)
    except PdfInspectorUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None, 3
    except Exception as exc:
        print(f"Error: pdf-inspector could not read this PDF: {exc}", file=sys.stderr)
        return None, 4
    if not items:
        print(
            "No extractable text was found. ShopLens currently supports native-text PDFs only; "
            "OCR is not included in this milestone.",
            file=sys.stderr,
        )
        return None, 5
    return items, 0


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "debug-text":
        return _run_debug_text(args)
    return _run_doctor(args)


if __name__ == "__main__":
    raise SystemExit(main())
