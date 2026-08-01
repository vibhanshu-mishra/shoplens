"""Command-line interface for ShopLens."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from shoplens.extraction import PdfInspectorUnavailableError, extract_positioned_text
from shoplens.steel import detect_steel_labels


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect native-text PDFs for steel labels.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect one PDF")
    inspect_parser.add_argument("pdf", type=Path, help="path to a native-text PDF")
    inspect_parser.add_argument("--json", action="store_true", help="output JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    path: Path = args.pdf
    if not path.exists():
        print(f"Error: file does not exist: {path}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"Error: path is not a file: {path}", file=sys.stderr)
        return 2
    if path.suffix.lower() != ".pdf":
        print("Error: input must be a PDF file (a name ending in .pdf).", file=sys.stderr)
        return 2

    try:
        items = extract_positioned_text(path)
    except PdfInspectorUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # The native extension reports malformed/non-PDF input here.
        print(f"Error: pdf-inspector could not read this PDF: {exc}", file=sys.stderr)
        return 4

    if not items:
        print(
            "No extractable text was found. ShopLens currently supports native-text PDFs only; "
            "OCR is not included in this milestone.",
            file=sys.stderr,
        )
        return 5

    detections = detect_steel_labels(items)
    if args.json:
        print(json.dumps([result.to_dict() for result in detections], indent=2))
    elif not detections:
        print("No steel section labels were found.")
    else:
        for result in detections:
            print(
                f"Page {result.page_number} | {result.normalized_text} | "
                f"family={result.section_family.value} | x={result.x:.2f} | y={result.y:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
