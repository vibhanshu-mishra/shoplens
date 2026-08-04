"""CSV export for classified section detections."""

import csv
from pathlib import Path
from typing import Iterable, Union

from .models import ClassifiedSectionDetection

CSV_FIELDS = (
    "pdf_page", "sheet_number", "sheet_title", "sheet_kind", "sheet_subject",
    "level", "segment", "area", "original_text", "normalized_section",
    "section_family", "x", "y", "width", "height", "confidence",
    "duplicate_count", "record_mode",
)


def export_inventory_csv(
    path: Union[str, Path], detections: Iterable[ClassifiedSectionDetection]
) -> int:
    rows = list(detections)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in rows:
            writer.writerow(
                {
                    "pdf_page": item.pdf_page,
                    "sheet_number": item.sheet_number or "",
                    "sheet_title": item.sheet_title or "",
                    "sheet_kind": item.sheet_kind.value if item.sheet_kind else "",
                    "sheet_subject": item.sheet_subject.value if item.sheet_subject else "",
                    "level": item.level or "",
                    "segment": item.segment or "",
                    "area": ";".join(item.area),
                    "original_text": item.original_text,
                    "normalized_section": item.normalized_section,
                    "section_family": item.section_family.value,
                    "x": item.raw_x,
                    "y": item.raw_y,
                    "width": item.raw_width,
                    "height": item.raw_height,
                    "confidence": item.confidence,
                    "duplicate_count": item.duplicate_count,
                    "record_mode": item.record_mode,
                }
            )
    return len(rows)
