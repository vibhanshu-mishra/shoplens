"""Deterministic positioned-text title-block extraction."""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
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
    layout_id: Optional[str] = None


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
        candidates_by_page[page] = _generate_candidates(
            by_page.get(page, []), declared, text_page_frequency
        )

    seed_candidates = [
        candidate
        for values in candidates_by_page.values()
        for candidate in values
        if candidate.score >= ACCEPTANCE_SCORE and not candidate.rejected
    ]
    layouts = _discover_layouts(seed_candidates)
    for values in candidates_by_page.values():
        for candidate in values:
            layout = _matching_layout(candidate, layouts)
            if layout is not None:
                candidate.layout_id = layout.layout_id
                candidate.score = min(1.0, candidate.score + 0.1)
                candidate.reasons.append("RECURRING_LAYOUT_SUPPORT")
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

    identified = [page for page in results if page.sheet_number is not None]
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
    )


def normalize_number(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


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
        if not is_sheet_number(item.text):
            continue
        number = normalize_number(item.text)
        reasons = ["SUPPORTED_SHEET_NUMBER_SYNTAX"]
        rejected: List[str] = []
        score = 0.2
        label = _nearest_label(item, labels)
        if label is not None:
            score += 0.4
            reasons.append("NEAR_SHEET_LABEL")
        if number in declared:
            score += 0.15
            reasons.append("DECLARED_LIST_MATCH")
        font_size = float(getattr(item, "font_size", item.height))
        if font_size >= typical_font * 1.5:
            score += 0.1
            reasons.append("PROMINENT_FONT")
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
            )
        )
    return candidates


def _is_sheet_label(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:SHEET|SHEET\s+NO\.?|SHEET\s+NUMBER|DRAWING\s+NO\.?)\s*",
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
    limit = max(120.0, float(item.height) * 5.0)
    return nearest if _distance(item, nearest) <= limit else None


def _distance(left: PositionedText, right: PositionedText) -> float:
    return math.hypot(_center_x(left) - _center_x(right), _center_y(left) - _center_y(right))


def _discover_layouts(candidates: Sequence[_Candidate]) -> List[_Layout]:
    groups: List[List[_Candidate]] = []
    for candidate in sorted(
        candidates,
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
    recurring = [group for group in groups if len({item.item.page for item in group}) >= 2]
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
        if _excluded_title_text(item.text):
            continue
        if rotated:
            in_region = (
                float(number.x) - height * 8.0 <= float(item.x) <= float(number.x) - height * 2.5
                and abs(float(item.y) - float(number.y)) <= height * 3.0
            )
        else:
            in_region = (
                abs(float(item.x) - float(candidate.label.x if candidate.label else number.x))
                <= height * 3.0
                and float(number.y) + height * 3.0 <= float(item.y) <= float(number.y) + height * 8.0
            )
        if in_region:
            fragments.append(item)
    return fragments


def _excluded_title_text(value: str) -> bool:
    compact = re.sub(r"\s+", " ", value).strip().upper()
    return bool(
        not compact
        or compact in {"SHEET", "JOB", "DATE", "REV", "REVISION", "ISSUES", "REVISIONS"}
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
