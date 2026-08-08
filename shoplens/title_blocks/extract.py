"""Deterministic positioned-text title-block extraction."""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from shoplens.sheets.extract import is_sheet_number, normalize_sheet_name
from shoplens.sheets.models import SheetEntry

from .models import TitleBlockPage, TitleBlockResult

BOX_TOLERANCE = 0.25
ACCEPTANCE_SCORE = 0.65
AMBIGUITY_MARGIN = 0.12
LAYOUT_COORDINATE_TOLERANCE = 60.0


class PositionedText(Protocol):
    text: str
    page: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class _Candidate:
    item: PositionedText
    number: str
    score: float
    reasons: List[str]
    rejected: List[str]
    label: Optional[PositionedText]
    declared_match: bool
    source_fragments: List[str]
    layout_id: Optional[str] = None


@dataclass
class _JoinedText:
    text: str
    page: int
    x: float
    y: float
    width: float
    height: float
    font: Optional[str]
    font_size: float
    source_fragments: List[str]


@dataclass
class _Layout:
    layout_id: str
    rotated: bool
    x: float
    y: float
    width: float
    height: float
    pages: List[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout_id": self.layout_id,
            "orientation": "rotated" if self.rotated else "standard",
            "median_x": self.x,
            "median_y": self.y,
            "median_width": self.width,
            "median_height": self.height,
            "supporting_pages": self.pages,
        }


def extract_title_blocks(
    items: Iterable[PositionedText],
    source_file: str,
    pages: Sequence[int],
    declared_entries: Sequence[SheetEntry] = (),
    *,
    declared_total: Optional[int] = None,
    sheet_list_pages: Sequence[int] = (),
) -> TitleBlockResult:
    """Extract one actual title-block identity per requested PDF page."""

    page_numbers = sorted(set(int(page) for page in pages))
    retained, duplicate_item_count = _deduplicate_items(list(items))
    by_page: Dict[int, List[PositionedText]] = defaultdict(list)
    for item in retained:
        if int(item.page) in page_numbers:
            by_page[int(item.page)].append(item)

    declared = {entry.sheet_number: entry for entry in declared_entries}
    text_page_frequency: Counter = Counter()
    for page, page_items in by_page.items():
        for value in {normalize_number(item.text) for item in page_items if is_sheet_number(item.text)}:
            text_page_frequency[value] += 1

    candidates_by_page: Dict[int, List[_Candidate]] = {}
    for page in page_numbers:
        page_items = by_page.get(page, [])
        candidates_by_page[page] = _generate_candidates(
            list(page_items) + _join_number_fragments(page_items), declared, text_page_frequency
        )

    seed_candidates = [candidate for values in candidates_by_page.values() for candidate in values]
    layouts = _discover_layouts(seed_candidates)
    for values in candidates_by_page.values():
        for candidate in values:
            layout = _matching_layout(candidate, layouts)
            if layout is not None:
                candidate.layout_id = layout.layout_id
                layout_bonus = 0.65 if candidate.label is None else 0.1
                candidate.score = min(1.0, candidate.score + layout_bonus)
                candidate.reasons.append("RECURRING_LAYOUT_SUPPORT")
                if "NO_TITLE_BLOCK_LABEL_CONTEXT" in candidate.rejected:
                    candidate.rejected.remove("NO_TITLE_BLOCK_LABEL_CONTEXT")
                    candidate.reasons.append("RECURRING_LAYOUT_WITHOUT_LITERAL_LABEL")
                if (
                    candidate.label is not None
                    and "REPEATED_PROJECT_OR_TEMPLATE_IDENTIFIER" in candidate.rejected
                ):
                    candidate.rejected.remove("REPEATED_PROJECT_OR_TEMPLATE_IDENTIFIER")
                    candidate.score = min(1.0, candidate.score + 0.6)
                    candidate.reasons.append("TITLE_BLOCK_CONTEXT_OVERRIDES_PAGE_FREQUENCY")

    results: List[TitleBlockPage] = []
    debug: List[Dict[str, Any]] = []
    for page in page_numbers:
        page_result, page_debug = _select_page(
            page,
            by_page.get(page, []),
            candidates_by_page[page],
        )
        results.append(page_result)
        debug.append(page_debug)

    results = _reconstruct_residual_declared_identity(
        results,
        by_page,
        declared_entries,
        declared_total,
    )
    results = _mark_declared_sheet_index_pages(results, declared_entries, sheet_list_pages)

    intentional_non_title_block_pages = [
        page.pdf_page for page in results if page.title_block_status == "NOT_PRESENT"
    ]
    identified = [
        page for page in results
        if page.sheet_number is not None and page.title_block_status != "NOT_PRESENT"
    ]
    grouped: Dict[str, List[int]] = defaultdict(list)
    for page in identified:
        grouped[page.sheet_number or ""].append(page.pdf_page)
    duplicates = {
        number: page_values
        for number, page_values in sorted(grouped.items())
        if len(page_values) > 1
    }
    warnings: List[str] = []
    if duplicate_item_count:
        warnings.append(f"EXACT_DUPLICATE_TEXT_ITEMS_SUPPRESSED: {duplicate_item_count}")
    if duplicates:
        warnings.append(f"DUPLICATE_ACTUAL_SHEET_NUMBERS: {len(duplicates)}")
    unidentified = [page.pdf_page for page in results if page.sheet_number is None]
    low_confidence = [
        page.pdf_page for page in results if "LOW_CONFIDENCE_SHEET_NUMBER" in page.warnings
    ]
    return TitleBlockResult(
        source_file=str(Path(source_file)),
        total_pdf_pages_processed=len(page_numbers),
        identified_page_count=len(identified),
        unidentified_pages=unidentified,
        low_confidence_pages=low_confidence,
        layouts_discovered=[layout.to_dict() for layout in layouts],
        duplicate_sheet_numbers=duplicates,
        pages=results,
        warnings=warnings,
        debug=debug,
        intentional_non_title_block_pages=intentional_non_title_block_pages,
    )


def normalize_number(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _reconstruct_residual_declared_identity(
    results: Sequence[TitleBlockPage],
    by_page: Dict[int, List[PositionedText]],
    declared_entries: Sequence[SheetEntry],
    declared_total: Optional[int],
) -> List[TitleBlockPage]:
    """Resolve one textless residual page only from an exhaustive Sheet List.

    This deliberately requires a closed declared set and exactly one remaining
    identity.  It cannot turn an ambiguous page or an incomplete Sheet List
    into a guessed drawing record.
    """

    declared = {entry.sheet_number: entry for entry in declared_entries}
    identified = {page.sheet_number for page in results if page.sheet_number is not None}
    missing_numbers = sorted(set(declared) - identified)
    textless_pages = [
        page for page in results
        if page.sheet_number is None and not by_page.get(page.pdf_page)
    ]
    if (
        declared_total is None
        or declared_total != len(declared)
        or len(missing_numbers) != 1
        or len(textless_pages) != 1
        or len(identified) != len(declared) - 1
    ):
        return list(results)

    source = declared[missing_numbers[0]]
    replacement = replace(
        textless_pages[0],
        sheet_number=source.sheet_number,
        sheet_title=source.sheet_name,
        confidence=0.90,
        evidence=[
            "DECLARED_SHEET_LIST_EXHAUSTIVE",
            "SINGLE_RESIDUAL_DECLARED_IDENTITY",
            "NO_POSITIONED_TEXT_ON_PAGE",
        ],
        warnings=["RECONSTRUCTED_FROM_DECLARED_SHEET_LIST"],
        identity_source="DECLARED_SHEET_LIST",
        title_block_status="RECONSTRUCTED",
    )
    return [replacement if page.pdf_page == replacement.pdf_page else page for page in results]


def _mark_declared_sheet_index_pages(
    results: Sequence[TitleBlockPage],
    declared_entries: Sequence[SheetEntry],
    sheet_list_pages: Sequence[int],
) -> List[TitleBlockPage]:
    """Mark a self-referential Sheet Index row as known non-title-block identity."""

    index_pages = {int(page) for page in sheet_list_pages}
    replacements: Dict[int, TitleBlockPage] = {}
    for page_number in index_pages:
        matches = [
            entry for entry in declared_entries
            if entry.source_page == page_number and _sheet_index_title(entry.sheet_name) is not None
        ]
        if len(matches) != 1:
            continue
        entry = matches[0]
        # The identity comes from the declared row, rather than from any
        # title-block-like text elsewhere on the sheet.
        replacements[page_number] = TitleBlockPage(
            pdf_page=page_number,
            sheet_number=entry.sheet_number,
            sheet_title=_sheet_index_title(entry.sheet_name),
            revision=None,
            confidence=entry.confidence,
            layout_id=None,
            number_original_text=None,
            title_original_text=None,
            number_x=None,
            number_y=None,
            number_width=None,
            number_height=None,
            title_x=None,
            title_y=None,
            title_width=None,
            title_height=None,
            evidence=["DECLARED_SHEET_LIST", "SHEET_INDEX_PAGE"],
            candidate_count=0,
            warnings=["NO_CONVENTIONAL_TITLE_BLOCK"],
            identity_source="DECLARED_SHEET_LIST",
            title_block_status="NOT_PRESENT",
            page_role="SHEET_INDEX",
        )
    return [replacements.get(page.pdf_page, page) for page in results]


def _sheet_index_title(value: str) -> Optional[str]:
    """Return a canonical declared Sheet Index title, ignoring table markers."""

    compact = re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
    return compact if compact in {"SHEET INDEX", "SHEET LIST", "DRAWING LIST", "INDEX OF DRAWINGS"} else None


def _number_profile(value: str) -> Optional[str]:
    compact = normalize_number(value)
    if is_sheet_number(compact):
        return "STRUCTURAL_SHEET_NUMBER"
    if (
        8 <= len(compact) <= 40
        and re.fullmatch(r"(?:[A-Z]{1,4}-)?S[A-Z0-9]+(?:-[A-Z0-9]+){2,}", compact)
        and sum(char.isdigit() for char in compact) >= 3
    ):
        return "CODED_SHEET_NUMBER"
    if (
        12 <= len(compact) <= 80
        and compact.count("-") >= 4
        and re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+){4,}", compact)
        and any(char.isalpha() for char in compact)
        and sum(char.isdigit() for char in compact) >= 2
    ):
        return "LONG_DOCUMENT_NUMBER"
    return None


def _generate_candidates(
    items: Sequence[PositionedText],
    declared: Dict[str, SheetEntry],
    frequency: Counter,
) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    labels = [item for item in items if _is_sheet_label(item.text)]
    font_sizes = [float(getattr(item, "font_size", item.height)) for item in items if item.text.strip()]
    typical_font = median(font_sizes) if font_sizes else 10.0
    for item in items:
        profile = _number_profile(item.text)
        if profile is None:
            continue
        number = normalize_number(item.text)
        reasons = [profile]
        rejected: List[str] = []
        score = 0.2
        label = _nearest_label(item, labels)
        if label is not None:
            score += 0.4
            reasons.append("NEAR_SHEET_LABEL")
        if number in declared:
            score += 0.15
            reasons.append("DECLARED_LIST_MATCH")
        if len(getattr(item, "source_fragments", [item.text])) > 1:
            score += 0.16
            reasons.append("COMPLETE_SHEET_NUMBER_OVER_PREFIX")
        font_size = float(getattr(item, "font_size", item.height))
        if font_size >= typical_font * 1.2:
            score += 0.1
            reasons.append("PROMINENT_FONT")
        if font_size >= 30.0 and font_size >= typical_font * 1.2:
            reasons.append("VERY_PROMINENT_FONT")
        if frequency[number] >= 5:
            score -= 0.6
            rejected.append("REPEATED_PROJECT_OR_TEMPLATE_IDENTIFIER")
        if label is None:
            score -= 0.15
            rejected.append("NO_TITLE_BLOCK_LABEL_CONTEXT")
        if font_size < max(12.0, typical_font * 0.75):
            score -= 0.2
            rejected.append("SMALL_REFERENCE_TEXT")
        candidates.append(
            _Candidate(
                item=item,
                number=number,
                score=max(0.0, min(1.0, score)),
                reasons=reasons,
                rejected=rejected,
                label=label,
                declared_match=number in declared,
                source_fragments=list(getattr(item, "source_fragments", [item.text])),
            )
        )
    return candidates


def _is_sheet_label(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:SHEET|SHEET\s+NO\.?|SHEET\s+NUMBER|DRAWING\s+NO\.?|DWG\.?\s*NO\.?|DOCUMENT\s+NO\.?)\s*:?\s*",
            value,
            re.IGNORECASE,
        )
    )


def _nearest_label(
    item: PositionedText, labels: Sequence[PositionedText]
) -> Optional[PositionedText]:
    if not labels:
        return None
    nearest = min(labels, key=lambda label: _distance(item, label))
    limit = max(240.0, float(item.height) * 12.0)
    return nearest if _distance(item, nearest) <= limit else None


def _distance(left: PositionedText, right: PositionedText) -> float:
    return math.hypot(_center_x(left) - _center_x(right), _center_y(left) - _center_y(right))


def _discover_layouts(candidates: Sequence[_Candidate]) -> List[_Layout]:
    groups: List[List[_Candidate]] = []
    eligible = [
        candidate for candidate in candidates
        if candidate.label is not None or "VERY_PROMINENT_FONT" in candidate.reasons
    ]
    for candidate in sorted(
        eligible,
        key=lambda value: (
            float(value.item.width) <= 1.0,
            float(value.item.x),
            float(value.item.y),
            int(value.item.page),
        ),
    ):
        rotated = float(candidate.item.width) <= 1.0
        group = next(
            (
                values
                for values in groups
                if (float(values[0].item.width) <= 1.0) == rotated
                and abs(median(float(value.item.x) for value in values) - float(candidate.item.x))
                <= LAYOUT_COORDINATE_TOLERANCE
                and abs(median(float(value.item.y) for value in values) - float(candidate.item.y))
                <= LAYOUT_COORDINATE_TOLERANCE
            ),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    recurring = [
        group for group in groups
        if len({item.item.page for item in group}) >= 2
        and (
            len({item.number for item in group}) >= 2
            or any(item.label is not None for item in group)
        )
        and (
            any(item.label is not None for item in group)
            or all("VERY_PROMINENT_FONT" in item.reasons for item in group)
        )
    ]
    layouts: List[_Layout] = []
    for index, group in enumerate(recurring, start=1):
        layouts.append(
            _Layout(
                layout_id=f"layout-{index}",
                rotated=float(group[0].item.width) <= 1.0,
                x=float(median(float(item.item.x) for item in group)),
                y=float(median(float(item.item.y) for item in group)),
                width=float(median(float(item.item.width) for item in group)),
                height=float(median(float(item.item.height) for item in group)),
                pages=sorted({int(item.item.page) for item in group}),
            )
        )
    return layouts


def _matching_layout(candidate: _Candidate, layouts: Sequence[_Layout]) -> Optional[_Layout]:
    rotated = float(candidate.item.width) <= 1.0
    matches = [
        layout
        for layout in layouts
        if layout.rotated == rotated
        and abs(layout.x - float(candidate.item.x)) <= LAYOUT_COORDINATE_TOLERANCE
        and abs(layout.y - float(candidate.item.y)) <= LAYOUT_COORDINATE_TOLERANCE
    ]
    return min(matches, key=lambda layout: abs(layout.x - float(candidate.item.x)) + abs(layout.y - float(candidate.item.y))) if matches else None


def _select_page(
    page: int,
    items: Sequence[PositionedText],
    candidates: Sequence[_Candidate],
) -> Tuple[TitleBlockPage, Dict[str, Any]]:
    ranked = sorted(candidates, key=lambda value: (-value.score, value.number, float(value.item.x)))
    viable = [candidate for candidate in ranked if not candidate.rejected or candidate.score >= ACCEPTANCE_SCORE]
    selected = viable[0] if viable and viable[0].score >= ACCEPTANCE_SCORE else None
    ambiguous = bool(
        selected is not None
        and len(viable) > 1
        and selected.score - viable[1].score < AMBIGUITY_MARGIN
        and (selected.label is None) == (viable[1].label is None)
    )
    debug = {
        "pdf_page": page,
        "candidates": [_candidate_dict(candidate) for candidate in ranked],
        "selected_sheet_number": selected.number if selected and not ambiguous else None,
        "layout_id": selected.layout_id if selected else None,
    }
    if selected is None or ambiguous:
        warnings = ["AMBIGUOUS_SHEET_NUMBER" if ambiguous else "UNIDENTIFIED_PAGE"]
        if ranked:
            warnings.append("LOW_CONFIDENCE_SHEET_NUMBER")
        return _empty_page(page, len(candidates), warnings), debug

    title_items = _labeled_title_fragments(items, selected)
    if not title_items:
        title_items = _title_fragments(items, selected)
    title = _join_title(title_items, float(selected.item.width) <= 1.0) if title_items else None
    warnings: List[str] = []
    if title is None:
        warnings.append("MISSING_SHEET_TITLE")
    title_box = _box(title_items)
    confidence = min(1.0, selected.score + (0.05 if title else 0.0))
    evidence = list(selected.reasons)
    if title:
        evidence.append("NEARBY_TITLE_FRAGMENTS")
    debug["selected_title"] = title
    debug["nearby_title_fragments"] = [item.text for item in title_items]
    debug["confidence"] = confidence
    return (
        TitleBlockPage(
            pdf_page=page,
            sheet_number=selected.number,
            sheet_title=title,
            revision=_extract_revision(items, selected),
            confidence=confidence,
            layout_id=selected.layout_id,
            number_original_text=selected.item.text,
            title_original_text=title,
            number_x=float(selected.item.x),
            number_y=float(selected.item.y),
            number_width=float(selected.item.width),
            number_height=float(selected.item.height),
            title_x=title_box[0],
            title_y=title_box[1],
            title_width=title_box[2],
            title_height=title_box[3],
            evidence=evidence,
            candidate_count=len(candidates),
            warnings=warnings,
            number_source_fragments=selected.source_fragments,
            title_source_fragments=[item.text for item in title_items],
        ),
        debug,
    )


def _title_fragments(items: Sequence[PositionedText], candidate: _Candidate) -> List[PositionedText]:
    number = candidate.item
    height = max(float(number.height), 1.0)
    rotated = float(number.width) <= 1.0
    fragments: List[PositionedText] = []
    for item in items:
        font_size = float(getattr(item, "font_size", item.height))
        if not (height * 0.5 <= font_size <= height * 0.85):
            continue
        if _excluded_title_text(item.text) or _number_profile(item.text) is not None:
            continue
        if rotated:
            in_region = (
                float(number.x) - height * 8.0 <= float(item.x) <= float(number.x) - height * 2.5
                and abs(float(item.y) - float(number.y)) <= height * 15.0
            )
        else:
            literal_sheet_layout = bool(
                candidate.label is not None
                and re.fullmatch(r"\s*SHEET\s*", candidate.label.text, re.I)
            )
            lower_multiplier = 3.0 if literal_sheet_layout else 0.8
            in_region = (
                abs(float(item.x) - float(candidate.label.x if candidate.label else number.x))
                <= height * 3.0
                and float(number.y) + height * lower_multiplier
                <= float(item.y)
                <= float(number.y) + height * 8.0
            )
        if in_region:
            fragments.append(item)
    return fragments


def _labeled_title_fragments(
    items: Sequence[PositionedText], candidate: _Candidate
) -> List[PositionedText]:
    labels = [
        item for item in items
        if re.fullmatch(r"\s*(?:TITLE|DRAWING\s+TITLE|SHEET\s+TITLE)\s*:?\s*", item.text, re.I)
    ]
    if not labels:
        return []
    label = min(labels, key=lambda item: _distance(item, candidate.item))
    number_height = max(float(candidate.item.height), 1.0)
    if _distance(label, candidate.item) > max(500.0, number_height * 15.0):
        return []
    label_size = float(getattr(label, "font_size", label.height))
    values = [
        item for item in items
        if item is not label
        and item is not candidate.item
        and not _excluded_title_text(item.text)
        and _number_profile(item.text) is None
        and float(getattr(item, "font_size", item.height)) >= label_size * 1.1
        and _distance(item, label) <= max(450.0, number_height * 14.0)
    ]
    if not values:
        return []
    nearest = min(values, key=lambda item: _distance(item, label))
    return [
        item for item in values
        if abs(float(item.y) - float(nearest.y)) <= max(float(nearest.height) * 2.0, 40.0)
    ]


def _excluded_title_text(value: str) -> bool:
    compact = re.sub(r"\s+", " ", value).strip().upper()
    return bool(
        not compact
        or re.fullmatch(r"[-–—_]+", compact) is not None
        or compact in {"SHEET", "TITLE", "JOB", "DATE", "REV", "REVISION", "ISSUES", "REVISIONS"}
        or re.fullmatch(
            r"(?:SHEET|DRAWING|DWG|DOCUMENT)\s+(?:NUMBER|NO\.?|TITLE)\s*:?",
            compact,
        )
        or re.search(
            r",\s*(?:ALABAMA|ALASKA|ARIZONA|ARKANSAS|CALIFORNIA|COLORADO|CONNECTICUT|DELAWARE|FLORIDA|GEORGIA|HAWAII|IDAHO|ILLINOIS|INDIANA|IOWA|KANSAS|KENTUCKY|LOUISIANA|MAINE|MARYLAND|MASSACHUSETTS|MICHIGAN|MINNESOTA|MISSISSIPPI|MISSOURI|MONTANA|NEBRASKA|NEVADA|OHIO|OKLAHOMA|OREGON|PENNSYLVANIA|TENNESSEE|TEXAS|UTAH|VERMONT|VIRGINIA|WASHINGTON|WISCONSIN|WYOMING)\b",
            compact,
        )
        or re.fullmatch(r"\d{4}[.-]\d{2}[.-]\d{2}", compact)
        or re.fullmatch(r"\d+(?:\.\d+)?", compact)
        or "ISSUE FOR" in compact
        or "ASSOCIATES" in compact
    )


def _join_title(items: Sequence[PositionedText], rotated: bool) -> str:
    ordered = sorted(items, key=(lambda item: float(item.x)) if rotated else (lambda item: -float(item.y)))
    return normalize_sheet_name(" ".join(item.text.strip() for item in ordered))


def _extract_revision(items: Sequence[PositionedText], candidate: _Candidate) -> Optional[str]:
    labels = [item for item in items if re.fullmatch(r"\s*(?:REV|REVISION)\s*", item.text, re.IGNORECASE)]
    for label in labels:
        if _distance(label, candidate.item) > float(candidate.item.height) * 6.0:
            continue
        values = [
            item
            for item in items
            if item is not label
            and _distance(item, label) <= max(40.0, float(candidate.item.height) * 2.0)
            and re.fullmatch(r"[A-Z0-9]{1,3}", item.text.strip(), re.IGNORECASE)
        ]
        if values:
            return min(values, key=lambda item: _distance(item, label)).text.strip()
    return None


def _candidate_dict(candidate: _Candidate) -> Dict[str, Any]:
    return {
        "text": candidate.item.text,
        "normalized": candidate.number,
        "x": float(candidate.item.x),
        "y": float(candidate.item.y),
        "width": float(candidate.item.width),
        "height": float(candidate.item.height),
        "score": candidate.score,
        "score_reasons": candidate.reasons,
        "rejection_reasons": candidate.rejected,
        "declared_match": candidate.declared_match,
        "layout_id": candidate.layout_id,
        "source_fragments": candidate.source_fragments,
    }


def _empty_page(page: int, candidate_count: int, warnings: List[str]) -> TitleBlockPage:
    return TitleBlockPage(
        pdf_page=page,
        sheet_number=None,
        sheet_title=None,
        revision=None,
        confidence=0.0,
        layout_id=None,
        number_original_text=None,
        title_original_text=None,
        number_x=None,
        number_y=None,
        number_width=None,
        number_height=None,
        title_x=None,
        title_y=None,
        title_width=None,
        title_height=None,
        evidence=[],
        candidate_count=candidate_count,
        warnings=warnings,
    )


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
            for other in retained[-200:]
        ):
            duplicates += 1
        else:
            retained.append(item)
    return retained, duplicates


def _join_number_fragments(items: Sequence[PositionedText]) -> List[_JoinedText]:
    """Join only adjacent, font-compatible fragments that form a known number profile."""

    joined: List[_JoinedText] = []
    for rotated in (False, True):
        oriented = [item for item in items if (float(item.width) <= 1.0) == rotated]
        ordered = sorted(
            oriented,
            key=(
                (lambda item: (float(item.x), float(item.y)))
                if rotated
                else (lambda item: (float(item.y), float(item.x)))
            ),
        )
        for start in range(len(ordered)):
            fragments = [ordered[start]]
            for candidate in ordered[start + 1 : start + 4]:
                previous = fragments[-1]
                typical = max(float(previous.height), float(candidate.height), 1.0)
                same_font = getattr(previous, "font", None) == getattr(candidate, "font", None)
                if rotated:
                    aligned = abs(float(previous.x) - float(candidate.x)) <= typical * 0.6
                    gap = float(candidate.y) - (float(previous.y) + float(previous.height))
                else:
                    aligned = abs(float(previous.y) - float(candidate.y)) <= typical * 0.6
                    gap = float(candidate.x) - (float(previous.x) + float(previous.width))
                if not same_font or not aligned or not (-typical * 0.25 <= gap <= typical * 1.5):
                    break
                fragments.append(candidate)
                combined = "".join(item.text.strip() for item in fragments)
                if _number_profile(combined) and not all(
                    _number_profile(item.text) for item in fragments
                ):
                    left, bottom, width, height = _box(fragments)
                    joined.append(
                        _JoinedText(
                            text=combined,
                            page=int(fragments[0].page),
                            x=float(left or 0.0),
                            y=float(bottom or 0.0),
                            width=float(width or 0.0),
                            height=float(height or 0.0),
                            font=getattr(fragments[0], "font", None),
                            font_size=float(getattr(fragments[0], "font_size", fragments[0].height)),
                            source_fragments=[item.text for item in fragments],
                        )
                    )

    # Some title strips are represented as upright glyph runs stacked along a
    # common x-axis rather than as zero-width rotated text.  Treat that as a
    # separate orientation only when the fragments share a baseline column,
    # font, and compact reading order; this avoids joining ordinary vertical
    # lists elsewhere on the page.
    ordered = sorted(items, key=lambda item: (-float(item.y), float(item.x)))
    for start in range(len(ordered)):
        fragments = [ordered[start]]
        for candidate in ordered[start + 1 : start + 12]:
            previous = fragments[-1]
            typical = max(float(previous.height), float(candidate.height), 1.0)
            same_font = getattr(previous, "font", None) == getattr(candidate, "font", None)
            same_column = abs(float(previous.x) - float(candidate.x)) <= typical * 0.6
            vertical_gap = float(previous.y) - (float(candidate.y) + float(candidate.height))
            if not same_font or not same_column:
                continue
            if not (-typical * 0.8 <= vertical_gap <= typical * 1.75):
                break
            fragments.append(candidate)
            combined = "".join(item.text.strip() for item in fragments)
            if _number_profile(combined) and not all(_number_profile(item.text) for item in fragments):
                left, bottom, width, height = _box(fragments)
                joined.append(
                    _JoinedText(
                        text=combined,
                        page=int(fragments[0].page),
                        x=float(left or 0.0),
                        y=float(bottom or 0.0),
                        width=float(width or 0.0),
                        height=float(height or 0.0),
                        font=getattr(fragments[0], "font", None),
                        font_size=float(getattr(fragments[0], "font_size", fragments[0].height)),
                        source_fragments=[item.text for item in fragments],
                    )
                )
    return joined


def _box(items: Sequence[PositionedText]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if not items:
        return None, None, None, None
    left = min(float(item.x) for item in items)
    bottom = min(float(item.y) for item in items)
    right = max(float(item.x) + float(item.width) for item in items)
    top = max(float(item.y) + float(item.height) for item in items)
    return left, bottom, right - left, top - bottom


def _center_x(item: PositionedText) -> float:
    return float(item.x) + float(item.width) / 2.0


def _center_y(item: PositionedText) -> float:
    return float(item.y) + float(item.height) / 2.0
