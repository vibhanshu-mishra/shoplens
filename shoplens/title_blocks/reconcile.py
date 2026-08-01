"""Reconcile declared Sheet List rows with actual title-block identities."""

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from shoplens.sheets.models import SheetListResult

from .models import (
    ReconciliationEntry,
    ReconciliationResult,
    ReconciliationStatus,
    TitleBlockPage,
    TitleBlockResult,
)

ABBREVIATIONS = {
    "DET": "DETAILS",
    "DETAIL": "DETAILS",
    "ELEV": "ELEVATIONS",
    "FLR": "FLOOR",
    "FRMG": "FRAMING",
    "TYP": "TYPICAL",
}


def reconcile_sheets(
    declared: SheetListResult, actual: TitleBlockResult
) -> ReconciliationResult:
    """Create actionable declared-versus-actual reconciliation records."""

    actual_by_number: Dict[str, List[TitleBlockPage]] = defaultdict(list)
    for page in actual.pages:
        if page.sheet_number:
            actual_by_number[page.sheet_number].append(page)
    declared_numbers = {entry.sheet_number for entry in declared.entries}
    entries: List[ReconciliationEntry] = []
    missing: List[str] = []
    mismatches: List[str] = []

    for declared_entry in declared.entries:
        pages = actual_by_number.get(declared_entry.sheet_number, [])
        if not pages:
            missing.append(declared_entry.sheet_number)
            entries.append(
                _entry(
                    declared_entry.sheet_number,
                    declared_entry.sheet_name,
                    [],
                    None,
                    None,
                    None,
                    ReconciliationStatus.DECLARED_BUT_MISSING,
                    None,
                    0.0,
                    [],
                )
            )
            continue
        primary = max(pages, key=lambda page: page.confidence)
        similarity, title_status, title_warnings = compare_titles(
            declared_entry.sheet_name, primary.sheet_title
        )
        status = (
            ReconciliationStatus.DUPLICATE_SHEET_NUMBER
            if len(pages) > 1
            else title_status
        )
        warnings = list(primary.warnings) + title_warnings
        if len(pages) > 1 and title_status != ReconciliationStatus.MATCH:
            warnings.append(f"TITLE_STATUS_{title_status.value}")
        if title_status == ReconciliationStatus.TITLE_MISMATCH:
            mismatches.append(declared_entry.sheet_number)
        entries.append(
            _entry(
                declared_entry.sheet_number,
                declared_entry.sheet_name,
                [page.pdf_page for page in pages],
                primary.sheet_number,
                primary.sheet_title,
                primary.revision,
                status,
                similarity,
                primary.confidence,
                warnings,
            )
        )

    undeclared = sorted(number for number in actual_by_number if number not in declared_numbers)
    for number in undeclared:
        pages = actual_by_number[number]
        primary = max(pages, key=lambda page: page.confidence)
        entries.append(
            _entry(
                None,
                None,
                [page.pdf_page for page in pages],
                number,
                primary.sheet_title,
                primary.revision,
                ReconciliationStatus.PRESENT_BUT_UNDECLARED,
                None,
                primary.confidence,
                list(primary.warnings),
            )
        )

    for page in actual.pages:
        if page.sheet_number is not None:
            continue
        status = (
            ReconciliationStatus.LOW_CONFIDENCE
            if "LOW_CONFIDENCE_SHEET_NUMBER" in page.warnings
            else ReconciliationStatus.UNIDENTIFIED_PAGE
        )
        entries.append(
            _entry(
                None,
                None,
                [page.pdf_page],
                None,
                None,
                None,
                status,
                None,
                page.confidence,
                list(page.warnings),
            )
        )

    warnings = list(declared.warnings) + list(actual.warnings)
    return ReconciliationResult(
        source_file=actual.source_file,
        declared_sheet_count=len(declared.entries),
        total_pdf_pages_processed=actual.total_pdf_pages_processed,
        identified_page_count=actual.identified_page_count,
        unidentified_pages=actual.unidentified_pages,
        missing_declared_sheets=missing,
        undeclared_actual_sheets=undeclared,
        duplicate_actual_sheet_numbers=actual.duplicate_sheet_numbers,
        title_mismatches=sorted(set(mismatches)),
        entries=entries,
        warnings=list(dict.fromkeys(warnings)),
    )


def compare_titles(
    declared: str, actual: Optional[str]
) -> Tuple[Optional[float], ReconciliationStatus, List[str]]:
    if not actual:
        return None, ReconciliationStatus.LOW_CONFIDENCE, ["MISSING_SHEET_TITLE"]
    strict_declared = normalize_title(declared)
    strict_actual = normalize_title(actual)
    similarity = SequenceMatcher(None, strict_declared, strict_actual).ratio()
    if strict_declared == strict_actual:
        return similarity, ReconciliationStatus.MATCH, []
    if _variation_form(strict_declared) == _variation_form(strict_actual):
        return similarity, ReconciliationStatus.TITLE_VARIATION, []
    return similarity, ReconciliationStatus.TITLE_MISMATCH, []


def normalize_title(value: str) -> str:
    result = value.upper().strip()
    result = re.sub(r"\s*-\s*", "-", result)
    result = re.sub(r"\s+", " ", result)
    return result


def _variation_form(value: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", value)
    return " ".join(ABBREVIATIONS.get(token, token) for token in tokens)


def _entry(
    declared_number: Optional[str],
    declared_title: Optional[str],
    pages: Sequence[int],
    actual_number: Optional[str],
    actual_title: Optional[str],
    revision: Optional[str],
    status: ReconciliationStatus,
    similarity: Optional[float],
    confidence: float,
    warnings: List[str],
) -> ReconciliationEntry:
    return ReconciliationEntry(
        declared_sheet_number=declared_number,
        declared_sheet_title=declared_title,
        actual_pdf_pages=list(pages),
        actual_sheet_number=actual_number,
        actual_sheet_title=actual_title,
        revision=revision,
        status=status,
        title_similarity=similarity,
        confidence=confidence,
        warnings=list(dict.fromkeys(warnings)),
    )
