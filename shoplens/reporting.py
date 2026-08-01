"""Filtering, safe duplicate handling, and extraction summaries."""

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from shoplens.models import RejectedCandidate, SectionFamily, SteelLabel, TextDiagnostic

COORDINATE_TOLERANCE = 0.25


def deduplicate_detections(
    detections: Iterable[SteelLabel], tolerance: float = COORDINATE_TOLERANCE
) -> Tuple[List[SteelLabel], int]:
    """Suppress only same-page, same-section, near-identical bounding boxes."""

    retained: List[SteelLabel] = []
    groups: Dict[Tuple[int, str], List[int]] = defaultdict(list)
    duplicate_total = 0
    for detection in detections:
        key = (detection.page_number, detection.normalized_text)
        duplicate_index = next(
            (
                index
                for index in groups[key]
                if _boxes_near(retained[index], detection, tolerance)
            ),
            None,
        )
        if duplicate_index is None:
            groups[key].append(len(retained))
            retained.append(detection)
        else:
            current = retained[duplicate_index]
            retained[duplicate_index] = replace(
                current, duplicate_count=current.duplicate_count + detection.duplicate_count
            )
            duplicate_total += detection.duplicate_count
    return retained, duplicate_total


def _boxes_near(left: SteelLabel, right: SteelLabel, tolerance: float) -> bool:
    return all(
        abs(a - b) <= tolerance
        for a, b in (
            (left.x, right.x),
            (left.y, right.y),
            (left.width, right.width),
            (left.height, right.height),
        )
    )


def filter_detections(
    detections: Iterable[SteelLabel],
    page: Optional[int] = None,
    contains: Optional[str] = None,
    families: Optional[Sequence[SectionFamily]] = None,
) -> List[SteelLabel]:
    family_set = set(families or [])
    needle = contains.upper() if contains else None
    return [
        item
        for item in detections
        if (page is None or item.page_number == page)
        and (needle is None or needle in item.original_text.upper() or needle in item.normalized_text)
        and (not family_set or item.section_family in family_set)
    ]


def filter_diagnostics(
    diagnostics: Iterable[TextDiagnostic],
    page: Optional[int] = None,
    contains: Optional[str] = None,
    families: Optional[Sequence[SectionFamily]] = None,
    candidates_only: bool = False,
    matches_only: bool = False,
) -> List[TextDiagnostic]:
    family_set = set(families or [])
    needle = contains.upper() if contains else None
    return [
        item
        for item in diagnostics
        if (page is None or item.page_number == page)
        and (needle is None or needle in item.text.upper())
        and (not candidates_only or item.is_candidate)
        and (not matches_only or item.section_detected)
        and (
            not family_set
            or any(match.section_family in family_set for match in item.detections)
        )
    ]


def build_summary(
    raw: Sequence[SteelLabel],
    displayed: Sequence[SteelLabel],
    rejections: Sequence[RejectedCandidate],
    duplicate_count: int,
    mode: str,
) -> Dict[str, Any]:
    """Build JSON-ready counts from raw and currently displayed records."""

    family_counts = Counter(item.section_family.value for item in displayed)
    page_counts = Counter(item.page_number for item in displayed)
    value_counts = Counter(item.normalized_text for item in displayed)
    return {
        "record_mode": mode,
        "total_raw_detections": len(raw),
        "total_displayed_detections": len(displayed),
        "total_unique_section_values": len(value_counts),
        "count_by_family": dict(sorted(family_counts.items())),
        "count_by_page": {str(key): value for key, value in sorted(page_counts.items())},
        "pages_containing_detections": sorted(page_counts),
        "most_frequent_section_values": [
            {"normalized_text": value, "count": count}
            for value, count in value_counts.most_common(10)
        ],
        "duplicate_count": duplicate_count,
        "negative_x_detections": sum(item.x < 0 for item in raw),
        "negative_y_detections": sum(item.y < 0 for item in raw),
        "rejected_likely_false_positives": len(rejections),
        "rejection_counts": dict(sorted(Counter(item.reason for item in rejections).items())),
    }
