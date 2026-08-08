"""Position-based extraction of declared drawing Sheet Lists."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from .models import SheetEntry, SheetListResult

HEADING_NAMES = ("SHEET LIST", "DRAWING LIST", "INDEX OF DRAWINGS", "SHEET INDEX")
NUMBER_HEADER_RE = re.compile(r"^(?:SHEET\s*)?(?:NUMBER|NO\.?|#)$", re.IGNORECASE)
NAME_HEADER_RE = re.compile(
    r"^(?:SHEET\s*)?(?:NAME|TITLE)|^(?:DRAWING\s+TITLE|DESCRIPTION)$", re.IGNORECASE
)
# Structural sheet identifiers commonly carry compact letter/digit suffixes
# (S11-OPL1), multiple hyphen fields (S01-10-P1), or an underscore revision
# family (BS11-00_FR). Contextual title-block evidence still decides whether a
# syntactically valid value is an actual sheet number.
SHEET_NUMBER_RE = re.compile(
    r"^[A-Z]{1,4}-?\d{1,4}[A-Z]?(?:-[A-Z0-9]{1,4})*(?:_[A-Z]{1,4})?$", re.IGNORECASE
)
FOOTER_RE = re.compile(r"\b(?:GRAND\s+TOTAL|TOTAL\s+SHEETS?|SHEET\s+COUNT)\b", re.IGNORECASE)
DECLARED_TOTAL_RE = re.compile(r"\bGRAND\s+TOTAL\s*:\s*(\d+)\b", re.IGNORECASE)
BOX_TOLERANCE = 0.25
MIN_CONTINUATION_ROWS = 5
MIN_PRIMARY_ROWS = 1


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
    def center_y(self) -> float:
        return sum(float(item.y) + float(item.height) / 2.0 for item in self.items) / len(self.items)

    @property
    def typical_height(self) -> float:
        return median(float(item.height) for item in self.items)

    @property
    def text(self) -> str:
        return _join_items(self.items)


@dataclass(frozen=True)
class _Layout:
    boundary_x: float
    header_y: float
    number_header_x: float
    name_header_x: float
    number_left: float
    title_start_x: float
    lower_y: float
    row_spacing: float


def is_sheet_number(value: str) -> bool:
    """Return whether text matches the configurable structural-sheet syntax."""

    compact = re.sub(r"\s+", "", value).upper()
    return bool(SHEET_NUMBER_RE.fullmatch(compact)) and sum(char.isdigit() for char in compact) >= 2


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
    declared_totals: List[int] = []

    if source_duplicate_count:
        warnings.append(f"EXACT_DUPLICATE_TEXT_ITEMS_SUPPRESSED: {source_duplicate_count}")

    for page in requested_pages:
        rows = _group_rows(by_page.get(page, []))
        headings = [row for row in rows if _heading_name(row.text) is not None]
        header = _find_header_layout(rows)
        page_total = _declared_total(rows)
        debug.append(
            {
                "page": page,
                "heading_candidates": [row.text for row in headings],
                "header_candidates": [row.text for row in rows if _is_header_row(row)],
                "column_boundary_x": header.boundary_x if header else None,
                "number_header_x": header.number_header_x if header else None,
                "name_header_x": header.name_header_x if header else None,
                "number_column_left": header.number_left if header else None,
                "title_column_start": header.title_start_x if header else None,
                "table_lower_y": header.lower_y if header else None,
                "inferred_row_spacing": header.row_spacing if header else None,
                "declared_total": page_total,
                "row_y_positions": [row.y for row in rows],
                "rejected_rows": [],
            }
        )

        layout = header
        primary_evidence = bool(headings and header)
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
            rows, layout, continuation=not primary_evidence
        )
        debug[-1]["rejected_rows"] = rejected
        debug[-1]["valid_row_pairs"] = len(page_entries)
        continuation_ok = (
            may_continue
            and len(page_entries) >= MIN_CONTINUATION_ROWS
            and _spacing_matches(page_entries, previous_layout)
        )
        primary_ok = primary_evidence and len(page_entries) >= MIN_PRIMARY_ROWS
        if not primary_ok and not continuation_ok:
            debug[-1]["page_rejection_reason"] = (
                "INSUFFICIENT_PRIMARY_EVIDENCE"
                if headings or header
                else "INSUFFICIENT_CONTINUATION_EVIDENCE"
            )
            continue
        list_pages.append(page)
        previous_layout = layout
        previous_list_page = page
        entries.extend(page_entries)
        warnings.extend(page_warnings)
        if page_total is not None:
            declared_totals.append(page_total)

    entries, duplicate_row_count = _deduplicate_entries(entries)
    if duplicate_row_count:
        warnings.append(f"EXACT_DUPLICATE_ROWS_SUPPRESSED: {duplicate_row_count}")
    entries, duplicate_numbers, duplicate_warnings = _mark_duplicate_numbers(entries)
    warnings.extend(duplicate_warnings)
    if not list_pages:
        warnings.append("NO_NATIVE_TEXT_SHEET_LIST_FOUND")
    elif len(entries) < 2:
        warnings.append("SUSPICIOUSLY_LOW_ENTRY_COUNT")
    declared_total = declared_totals[0] if declared_totals else None
    if len(set(declared_totals)) > 1:
        warnings.append("CONFLICTING_DECLARED_TOTALS")
    unique_entry_count = len({entry.sheet_number for entry in entries})
    if declared_total is not None and declared_total != unique_entry_count:
        warnings.append(
            f"DECLARED_TOTAL_MISMATCH: declared={declared_total} extracted_unique={unique_entry_count}"
        )

    return SheetListResult(
        source_file=str(Path(source_file)),
        pages_scanned=requested_pages,
        sheet_list_pages=sorted(set(list_pages)),
        entries=entries,
        duplicate_sheet_numbers=duplicate_numbers,
        warnings=_unique(warnings),
        declared_total=declared_total,
        debug=debug,
    )


def _group_rows(items: Iterable[PositionedText]) -> List[_Row]:
    rows: List[_Row] = []
    for item in sorted(
        items,
        key=lambda value: (-(float(value.y) + float(value.height) / 2.0), float(value.x)),
    ):
        item_center = float(item.y) + float(item.height) / 2.0
        row = next(
            (
                candidate
                for candidate in rows
                if abs(candidate.center_y - item_center)
                <= max(1.25, 0.45 * min(candidate.typical_height, float(item.height)))
            ),
            None,
        )
        if row is None:
            rows.append(_Row(page=int(item.page), items=[item]))
        else:
            row.items.append(item)
    for row in rows:
        row.items.sort(key=lambda value: float(value.x))
    return sorted(rows, key=lambda value: -value.center_y)


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
        footer_rows = [candidate for candidate in rows if _row_has_footer(candidate)]
        footer_y = max(
            (candidate.y for candidate in footer_rows if candidate.y < row.y),
            default=float("-inf"),
        )
        possible_rows = [
            candidate
            for candidate in rows
            if candidate.y < row.y and candidate.y > footer_y
        ]
        numbered: List[Tuple[_Row, PositionedText]] = []
        for candidate in possible_rows:
            for item in candidate.items:
                if is_sheet_number(item.text) and float(item.x) < name_x:
                    numbered.append((candidate, item))
        if numbered:
            number_anchor = _dominant_coordinate(
                [float(item.x) for _, item in numbered],
                tolerance=max(2.0, row.typical_height),
            )
            aligned = [
                (candidate, item)
                for candidate, item in numbered
                if abs(float(item.x) - number_anchor) <= max(2.0, row.typical_height)
            ]
        else:
            number_anchor = number_x
            aligned = []
        title_starts: List[float] = []
        for candidate, number_item in aligned:
            following = [
                item
                for item in candidate.items
                if float(item.x) >= float(number_item.x) + float(number_item.width)
            ]
            if following:
                title_starts.append(min(float(item.x) for item in following))
        title_start = (
            _dominant_coordinate(title_starts, tolerance=max(2.0, row.typical_height))
            if title_starts
            else name_x
        )
        number_rights = [float(item.x) + float(item.width) for _, item in aligned]
        number_right = max(number_rights) if number_rights else max(
            float(item.x) + float(item.width) for item in number_items
        )
        boundary = (number_right + title_start) / 2.0
        aligned_ys = sorted({candidate.center_y for candidate, _ in aligned}, reverse=True)
        spacings = [
            aligned_ys[index] - aligned_ys[index + 1]
            for index in range(len(aligned_ys) - 1)
            if aligned_ys[index] > aligned_ys[index + 1]
        ]
        row_spacing = median(spacings) if spacings else row.typical_height * 1.35
        lower_y = footer_y if footer_y != float("-inf") else (
            min(aligned_ys) - row_spacing * 0.75 if aligned_ys else row.y - row_spacing
        )
        return _Layout(
            boundary_x=boundary,
            header_y=row.y,
            number_header_x=number_x,
            name_header_x=name_x,
            number_left=number_anchor - max(row.typical_height * 2.0, 4.0),
            title_start_x=title_start,
            lower_y=lower_y,
            row_spacing=row_spacing,
        )
    return None


def _extract_page_rows(
    rows: Sequence[_Row], layout: _Layout, continuation: bool
) -> Tuple[List[SheetEntry], List[str], List[Dict[str, Any]]]:
    entries: List[SheetEntry] = []
    warnings: List[str] = []
    rejected: List[Dict[str, Any]] = []
    invalid_row_count = 0
    for row in rows:
        # A declared row such as ``S005 | SHEET INDEX`` contains a heading
        # phrase, but remains a real Sheet List entry.  Only a heading-only
        # row is structural table chrome.
        heading_only = _heading_name(row.text) is not None and not any(
            is_sheet_number(item.text) for item in row.items
        )
        if heading_only or _is_header_row(row) or _row_has_footer(row):
            reason = "HEADING_OR_HEADER" if not _row_has_footer(row) else "FOOTER_ROW"
            rejected.append({"y": row.y, "text": row.text, "reason": reason})
            continue
        if row.center_y >= layout.header_y or row.center_y <= layout.lower_y:
            continue
        number_items = [
            item
            for item in row.items
            if float(item.x) >= layout.number_left and _center_x(item) < layout.boundary_x
        ]
        name_items = _title_items(row.items, layout)
        number_original = _join_items(number_items)
        number = re.sub(r"\s+", "", number_original).upper()
        name_original = _join_items(name_items)
        name = normalize_sheet_name(name_original)
        if not is_sheet_number(number) or _looks_like_equipment_tag(number, name):
            if name and len(name) >= 3:
                invalid_row_count += 1
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
    if invalid_row_count:
        warnings.append(f"ROWS_WITHOUT_VALID_SHEET_NUMBER: {invalid_row_count}")
    return entries, warnings, rejected


def _title_items(items: Sequence[PositionedText], layout: _Layout) -> List[PositionedText]:
    ordered = sorted(
        (item for item in items if _center_x(item) >= layout.boundary_x),
        key=lambda item: float(item.x),
    )
    if not ordered:
        return []
    tolerance = max(2.0, median(float(item.height) for item in ordered) * 2.0)
    start_index = next(
        (
            index
            for index, item in enumerate(ordered)
            if abs(float(item.x) - layout.title_start_x) <= tolerance
        ),
        None,
    )
    if start_index is None:
        return []
    selected = [ordered[start_index]]
    for item in ordered[start_index + 1 :]:
        previous = selected[-1]
        gap = float(item.x) - (float(previous.x) + float(previous.width))
        allowed_gap = max(4.0, 6.0 * min(float(previous.height), float(item.height)))
        if gap > allowed_gap:
            break
        selected.append(item)
    return selected


def _looks_like_equipment_tag(number: str, name: str) -> bool:
    if not number.startswith("RTU"):
        return False
    return bool(
        re.search(r"\bROOF\s+TOP\s+UNIT\b", name, re.IGNORECASE)
        or re.fullmatch(r"[\d,.'\"\s-]+", name)
    )


def _declared_total(rows: Sequence[_Row]) -> Optional[int]:
    for row in rows:
        for item in row.items:
            match = DECLARED_TOTAL_RE.search(item.text)
            if match:
                return int(match.group(1))
    return None


def _row_has_footer(row: _Row) -> bool:
    return any(FOOTER_RE.search(item.text) for item in row.items)


def _spacing_matches(entries: Sequence[SheetEntry], layout: Optional[_Layout]) -> bool:
    if layout is None or len(entries) < MIN_CONTINUATION_ROWS:
        return False
    centers = sorted(
        {entry.number_y + entry.number_height / 2.0 for entry in entries},
        reverse=True,
    )
    spacings = [
        centers[index] - centers[index + 1]
        for index in range(len(centers) - 1)
        if centers[index] > centers[index + 1]
    ]
    if not spacings:
        return False
    observed = median(spacings)
    return layout.row_spacing * 0.65 <= observed <= layout.row_spacing * 1.5


def _dominant_coordinate(values: Sequence[float], tolerance: float) -> float:
    if not values:
        raise ValueError("at least one coordinate is required")
    groups: List[List[float]] = []
    for value in sorted(values):
        group = next(
            (candidate for candidate in groups if abs(median(candidate) - value) <= tolerance),
            None,
        )
        if group is None:
            groups.append([value])
        else:
            group.append(value)
    largest = max(groups, key=len)
    return float(median(largest))


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
    ordered = sorted(
        (item for item in items if item.text.strip()),
        key=lambda value: float(value.x),
    )
    if not ordered:
        return ""
    combined = ordered[0].text.strip()
    previous = ordered[0]
    for item in ordered[1:]:
        explicit_space = previous.text[-1:].isspace() or item.text[:1].isspace()
        overlaps = float(item.x) < float(previous.x) + float(previous.width)
        separator = " " if explicit_space or overlaps else ""
        combined += separator + item.text.strip()
        previous = item
    return normalize_sheet_name(combined)


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
