"""Position-based extraction of declared drawing Sheet Lists."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from .models import SheetEntry, SheetListResult

HEADING_NAMES = ("SHEET LIST", "DRAWING LIST", "INDEX OF DRAWINGS", "SHEET INDEX")
NUMBER_HEADER_RE = re.compile(r"^(?:SHEET\s*)?(?:NUMBER|NO\.?|#)$", re.IGNORECASE)
NAME_HEADER_RE = re.compile(
    r"^(?:SHEET\s*)?(?:NAME|TITLE)|^(?:DRAWING\s+TITLE|DESCRIPTION)$", re.IGNORECASE
)
SHEET_NUMBER_RE = re.compile(
    r"^[A-Z]{1,3}-?\d{1,4}(?:-\d{1,3})?[A-Z]?$", re.IGNORECASE
)
FOOTER_RE = re.compile(r"\b(?:GRAND\s+TOTAL|TOTAL\s+SHEETS?|SHEET\s+COUNT)\b", re.IGNORECASE)
ROW_Y_TOLERANCE = 2.5
BOX_TOLERANCE = 0.25


class PositionedText(Protocol):
    text: str
    page: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class _Row:
    page: int
    items: List[PositionedText]

    @property
    def y(self) -> float:
        return sum(float(item.y) for item in self.items) / len(self.items)

    @property
    def text(self) -> str:
        return _join_items(self.items)


@dataclass(frozen=True)
class _Layout:
    boundary_x: float
    header_y: float
    number_header_x: float
    name_header_x: float


def is_sheet_number(value: str) -> bool:
    """Return whether text matches the configurable structural-sheet syntax."""

    compact = re.sub(r"\s+", "", value).upper()
    return bool(SHEET_NUMBER_RE.fullmatch(compact)) and any(char.isdigit() for char in compact)


def normalize_sheet_name(value: str) -> str:
    """Normalize whitespace only; preserve engineering wording and punctuation."""

    return re.sub(r"\s+", " ", value).strip()


def sheet_name_comparison(value: str) -> str:
    """Create a conservative comparison form without changing declared output."""

    return re.sub(r"\s*-\s*", "-", normalize_sheet_name(value)).upper()


def extract_sheet_list(
    items: Iterable[PositionedText],
    source_file: str,
    pages: Sequence[int],
) -> SheetListResult:
    """Extract a declared Sheet List from already positioned native text."""

    requested_pages = sorted(set(pages))
    filtered = [item for item in items if int(item.page) in requested_pages]
    filtered, source_duplicate_count = _deduplicate_items(filtered)
    by_page: Dict[int, List[PositionedText]] = defaultdict(list)
    for item in filtered:
        by_page[int(item.page)].append(item)

    entries: List[SheetEntry] = []
    warnings: List[str] = []
    debug: List[Dict[str, Any]] = []
    list_pages: List[int] = []
    previous_layout: Optional[_Layout] = None
    previous_list_page: Optional[int] = None

    if source_duplicate_count:
        warnings.append(f"EXACT_DUPLICATE_TEXT_ITEMS_SUPPRESSED: {source_duplicate_count}")

    for page in requested_pages:
        rows = _group_rows(by_page.get(page, []))
        headings = [row for row in rows if _heading_name(row.text) is not None]
        header = _find_header_layout(rows)
        debug.append(
            {
                "page": page,
                "heading_candidates": [row.text for row in headings],
                "header_candidates": [row.text for row in rows if _is_header_row(row)],
                "column_boundary_x": header.boundary_x if header else None,
                "number_header_x": header.number_header_x if header else None,
                "name_header_x": header.name_header_x if header else None,
                "row_y_positions": [row.y for row in rows],
                "rejected_rows": [],
            }
        )

        layout = header
        explicit_list_evidence = bool(headings or header)
        may_continue = (
            previous_layout is not None
            and previous_list_page is not None
            and page == previous_list_page + 1
        )
        if layout is None and may_continue:
            layout = previous_layout
        if layout is None:
            if headings:
                warnings.append(f"COLUMN_HEADERS_NOT_FOUND_PAGE_{page}")
            continue

        page_entries, page_warnings, rejected = _extract_page_rows(
            rows, layout, continuation=not explicit_list_evidence
        )
        debug[-1]["rejected_rows"] = rejected
        if not explicit_list_evidence and not page_entries:
            continue
        if explicit_list_evidence or page_entries:
            list_pages.append(page)
            previous_layout = layout
            previous_list_page = page
            entries.extend(page_entries)
            warnings.extend(page_warnings)

    entries, duplicate_row_count = _deduplicate_entries(entries)
    if duplicate_row_count:
        warnings.append(f"EXACT_DUPLICATE_ROWS_SUPPRESSED: {duplicate_row_count}")
    entries, duplicate_numbers, duplicate_warnings = _mark_duplicate_numbers(entries)
    warnings.extend(duplicate_warnings)
    if not list_pages:
        warnings.append("NO_NATIVE_TEXT_SHEET_LIST_FOUND")
    elif len(entries) < 2:
        warnings.append("SUSPICIOUSLY_LOW_ENTRY_COUNT")

    return SheetListResult(
        source_file=str(Path(source_file)),
        pages_scanned=requested_pages,
        sheet_list_pages=sorted(set(list_pages)),
        entries=entries,
        duplicate_sheet_numbers=duplicate_numbers,
        warnings=_unique(warnings),
        debug=debug,
    )


def _group_rows(items: Iterable[PositionedText]) -> List[_Row]:
    rows: List[_Row] = []
    for item in sorted(items, key=lambda value: (-float(value.y), float(value.x))):
        row = next((candidate for candidate in rows if abs(candidate.y - float(item.y)) <= ROW_Y_TOLERANCE), None)
        if row is None:
            rows.append(_Row(page=int(item.page), items=[item]))
        else:
            row.items.append(item)
    for row in rows:
        row.items.sort(key=lambda value: float(value.x))
    return sorted(rows, key=lambda value: -value.y)


def _heading_name(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", " ", text).strip().upper()
    return next((heading for heading in HEADING_NAMES if heading in compact), None)


def _is_header_row(row: _Row) -> bool:
    return _header_groups(row) is not None


def _header_groups(row: _Row) -> Optional[Tuple[List[PositionedText], List[PositionedText]]]:
    number_items = [item for item in row.items if NUMBER_HEADER_RE.fullmatch(_clean(item.text))]
    name_items = [item for item in row.items if NAME_HEADER_RE.fullmatch(_clean(item.text))]
    if not number_items or not name_items:
        return None
    return number_items, name_items


def _find_header_layout(rows: Sequence[_Row]) -> Optional[_Layout]:
    for row in rows:
        groups = _header_groups(row)
        if groups is None:
            continue
        number_items, name_items = groups
        number_x = min(float(item.x) for item in number_items)
        name_x = min(float(item.x) for item in name_items)
        if name_x <= number_x:
            continue
        number_center = sum(_center_x(item) for item in number_items) / len(number_items)
        name_center = sum(_center_x(item) for item in name_items) / len(name_items)
        return _Layout(
            boundary_x=(number_center + name_center) / 2.0,
            header_y=row.y,
            number_header_x=number_x,
            name_header_x=name_x,
        )
    return None


def _extract_page_rows(
    rows: Sequence[_Row], layout: _Layout, continuation: bool
) -> Tuple[List[SheetEntry], List[str], List[Dict[str, Any]]]:
    entries: List[SheetEntry] = []
    warnings: List[str] = []
    rejected: List[Dict[str, Any]] = []
    for row in rows:
        if _heading_name(row.text) or _is_header_row(row) or FOOTER_RE.search(row.text):
            reason = "HEADING_OR_HEADER" if not FOOTER_RE.search(row.text) else "FOOTER_ROW"
            rejected.append({"y": row.y, "text": row.text, "reason": reason})
            continue
        if not continuation and row.y >= layout.header_y - ROW_Y_TOLERANCE:
            continue
        number_items = [item for item in row.items if _center_x(item) < layout.boundary_x]
        name_items = [item for item in row.items if _center_x(item) >= layout.boundary_x]
        number_original = _join_items(number_items)
        number = re.sub(r"\s+", "", number_original).upper()
        name_original = _join_items(name_items)
        name = normalize_sheet_name(name_original)
        if not is_sheet_number(number):
            if name and len(name) >= 3:
                warning = f"ROW_WITHOUT_VALID_SHEET_NUMBER_PAGE_{row.page}_Y_{row.y:.2f}"
                warnings.append(warning)
                rejected.append({"y": row.y, "text": row.text, "reason": "INVALID_SHEET_NUMBER"})
            continue
        entry_warnings: List[str] = []
        confidence = 0.95
        if not name:
            entry_warnings.append("MISSING_SHEET_NAME")
            warnings.append(f"MISSING_SHEET_NAME: {number}")
            confidence = 0.6
        number_box = _box(number_items)
        name_box = _box(name_items)
        entries.append(
            SheetEntry(
                sheet_number=number,
                sheet_name=name,
                source_page=row.page,
                number_original_text=number_original,
                name_original_text=name_original,
                number_x=number_box[0],
                number_y=number_box[1],
                number_width=number_box[2],
                number_height=number_box[3],
                name_x=name_box[0],
                name_y=name_box[1],
                name_width=name_box[2],
                name_height=name_box[3],
                confidence=confidence,
                warnings=entry_warnings,
                name_comparison_text=sheet_name_comparison(name),
            )
        )
    return entries, warnings, rejected


def _deduplicate_items(items: Sequence[PositionedText]) -> Tuple[List[PositionedText], int]:
    retained: List[PositionedText] = []
    duplicates = 0
    for item in items:
        if any(
            int(item.page) == int(other.page)
            and item.text == other.text
            and all(
                abs(float(getattr(item, field)) - float(getattr(other, field))) <= BOX_TOLERANCE
                for field in ("x", "y", "width", "height")
            )
            for other in retained
        ):
            duplicates += 1
        else:
            retained.append(item)
    return retained, duplicates


def _deduplicate_entries(entries: Sequence[SheetEntry]) -> Tuple[List[SheetEntry], int]:
    retained: List[SheetEntry] = []
    duplicates = 0
    for entry in entries:
        if any(_same_entry_box(entry, other) for other in retained):
            duplicates += 1
        else:
            retained.append(entry)
    return retained, duplicates


def _same_entry_box(left: SheetEntry, right: SheetEntry) -> bool:
    return (
        left.source_page == right.source_page
        and left.sheet_number == right.sheet_number
        and left.name_comparison_text == right.name_comparison_text
        and all(
            abs(a - b) <= BOX_TOLERANCE
            for a, b in (
                (left.number_x, right.number_x),
                (left.number_y, right.number_y),
                (left.name_x, right.name_x),
                (left.name_y, right.name_y),
            )
        )
    )


def _mark_duplicate_numbers(
    entries: Sequence[SheetEntry],
) -> Tuple[List[SheetEntry], List[str], List[str]]:
    grouped: Dict[str, List[SheetEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.sheet_number].append(entry)
    duplicates = sorted(number for number, values in grouped.items() if len(values) > 1)
    warnings: List[str] = []
    marked: List[SheetEntry] = []
    for entry in entries:
        peers = grouped[entry.sheet_number]
        entry_warnings = list(entry.warnings)
        if len(peers) > 1:
            titles = {peer.name_comparison_text for peer in peers}
            code = "CONFLICTING_SHEET_TITLES" if len(titles) > 1 else "DUPLICATE_SHEET_NUMBER"
            entry_warnings.append(code)
            warnings.append(f"{code}: {entry.sheet_number}")
        marked.append(replace(entry, warnings=_unique(entry_warnings)))
    return marked, duplicates, warnings


def sheet_prefix_counts(entries: Sequence[SheetEntry]) -> Dict[str, int]:
    counts = Counter()
    for entry in entries:
        match = re.match(r"^[A-Z]+\d?", entry.sheet_number)
        counts[match.group(0) if match else "OTHER"] += 1
    return dict(sorted(counts.items()))


def _join_items(items: Sequence[PositionedText]) -> str:
    return normalize_sheet_name(" ".join(item.text.strip() for item in sorted(items, key=lambda value: float(value.x)) if item.text.strip()))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _center_x(item: PositionedText) -> float:
    return float(item.x) + float(item.width) / 2.0


def _box(items: Sequence[PositionedText]) -> Tuple[float, float, float, float]:
    if not items:
        return 0.0, 0.0, 0.0, 0.0
    left = min(float(item.x) for item in items)
    bottom = min(float(item.y) for item in items)
    right = max(float(item.x) + float(item.width) for item in items)
    top = max(float(item.y) + float(item.height) for item in items)
    return left, bottom, right - left, top - bottom


def _unique(values: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(values))
