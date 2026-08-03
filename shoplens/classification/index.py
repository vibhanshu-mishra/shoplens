"""Classify reconciled sheets and construct searchable package indexes."""

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from shoplens.title_blocks.models import ReconciliationEntry, ReconciliationResult

from .models import (
    ClassificationTitleSource,
    ClassifiedSheet,
    Discipline,
    PackageIndexResult,
    SheetKind,
    StructuralSubject,
)
from .rules import matching_rules, normalize_classification_title, select_rule

CLASSIFICATION_VERSION = "1.0"
AREAS = (
    "MECHANICAL PLATFORM",
    "OFFICE ROOF",
    "EAST STAIR TOWER",
    "WEST STAIR TOWER",
    "SERVICE YARD",
)
MODIFIERS = ("TYPICAL", "OVERALL", "ENLARGED", "PARTIAL")


def classify_entry(entry: ReconciliationEntry) -> ClassifiedSheet:
    title, source = _classification_title(entry)
    warnings: List[str] = []
    normalized = normalize_classification_title(title) if title is not None else None
    candidates = matching_rules(normalized) if normalized is not None else []
    rule, ambiguous = select_rule(candidates)
    if title is None:
        warnings.append("TITLE_SOURCE_MISSING")
    if ambiguous:
        warnings.append("MULTIPLE_PRIMARY_RULES")
        rule = None
    if rule is None:
        warnings.append("UNKNOWN_CLASSIFICATION")

    levels = _extract_levels(normalized) if normalized is not None else []
    level = levels[0] if len(levels) == 1 else None
    if len(levels) > 1:
        warnings.append("LEVEL_CONFLICT")
    areas = [area for area in AREAS if normalized is not None and area in normalized]
    modifiers = [
        modifier
        for modifier in MODIFIERS
        if normalized is not None and re.search(rf"\b{modifier}\b", normalized)
    ]
    segment = _extract_segment(normalized) if normalized is not None else None
    suffix = _sheet_suffix(entry.actual_sheet_number or entry.declared_sheet_number)
    evidence = [f"TITLE_SOURCE_{source.value}"]
    if rule:
        evidence.append(f"RULE_MATCH:{rule.rule_id}")
    if level:
        evidence.append(f"LEVEL:{level}")
    elif levels:
        evidence.append(f"LEVEL_CANDIDATES:{','.join(levels)}")
    if segment:
        evidence.append(f"SEGMENT:{segment}")
        if suffix == segment:
            evidence.append("SHEET_SUFFIX_SUPPORTS_SEGMENT")
        elif suffix:
            warnings.append("SEGMENT_CONFLICT")
            evidence.append(f"SHEET_SUFFIX:{suffix}")
    confidence = rule.confidence if rule else 0.0
    if confidence and confidence < 0.70:
        warnings.append("LOW_CLASSIFICATION_CONFIDENCE")
    kind = rule.sheet_kind if rule else SheetKind.UNKNOWN
    subject = rule.subject if rule else StructuralSubject.UNKNOWN
    secondary_kinds = list(rule.secondary_kinds) if rule else []
    secondary_subjects = list(rule.secondary_subjects) if rule else []
    groups = _group_keys(subject, kind, level, segment, normalized or "")
    return ClassifiedSheet(
        pdf_page=entry.actual_pdf_pages[0] if entry.actual_pdf_pages else None,
        actual_pdf_pages=list(entry.actual_pdf_pages),
        sheet_number=entry.actual_sheet_number or entry.declared_sheet_number,
        declared_title=entry.declared_sheet_title,
        actual_title=entry.actual_sheet_title,
        classification_title=normalized,
        classification_title_source=source,
        discipline=Discipline.STRUCTURAL,
        sheet_kind=kind,
        secondary_kinds=secondary_kinds,
        subject=subject,
        secondary_subjects=secondary_subjects,
        level=level,
        segment=segment,
        area=areas,
        modifiers=modifiers,
        classification_confidence=confidence,
        matched_rule=rule.rule_id if rule else None,
        classification_evidence=evidence,
        group_keys=groups,
        warnings=list(dict.fromkeys(list(entry.warnings) + warnings)),
        candidate_rules=[candidate.rule_id for candidate in candidates],
    )


def build_package_index(result: ReconciliationResult) -> PackageIndexResult:
    sheets = [classify_entry(entry) for entry in result.entries]
    sheets.sort(key=lambda sheet: (sheet.pdf_page is None, sheet.pdf_page or 0, sheet.sheet_number or ""))
    unknown = [sheet for sheet in sheets if sheet.sheet_kind == SheetKind.UNKNOWN]
    warnings = list(result.warnings)
    if unknown:
        warnings.append(f"UNKNOWN_CLASSIFICATIONS: {len(unknown)}")
    warnings.extend(
        f"{sheet.sheet_number or 'UNIDENTIFIED'}: {warning}"
        for sheet in sheets
        for warning in sheet.warnings
    )
    return PackageIndexResult(
        source_file=result.source_file,
        total_pdf_pages=result.total_pdf_pages_processed,
        declared_sheet_count=result.declared_sheet_count,
        indexed_sheet_count=len(sheets),
        classified_sheet_count=len(sheets) - len(unknown),
        unknown_sheet_count=len(unknown),
        classification_version=CLASSIFICATION_VERSION,
        counts_by_kind=_count(sheet.sheet_kind.value for sheet in sheets),
        counts_by_subject=_count(sheet.subject.value for sheet in sheets),
        counts_by_level=_count(sheet.level for sheet in sheets if sheet.level),
        counts_by_segment=_count(sheet.segment for sheet in sheets if sheet.segment),
        counts_by_area=_count(area for sheet in sheets for area in sheet.area),
        sheets=sheets,
        warnings=list(dict.fromkeys(warnings)),
    )


def filter_sheets(
    sheets: Sequence[ClassifiedSheet], sheet_number: Optional[str] = None,
    page: Optional[int] = None, kind: Optional[SheetKind] = None,
    subject: Optional[StructuralSubject] = None, level: Optional[str] = None,
    segment: Optional[str] = None, area: Optional[str] = None,
    unknown_only: bool = False,
) -> List[ClassifiedSheet]:
    return [
        sheet for sheet in sheets
        if (sheet_number is None or sheet.sheet_number == sheet_number.strip().upper())
        and (page is None or sheet.pdf_page == page)
        and (kind is None or sheet.sheet_kind == kind)
        and (subject is None or sheet.subject == subject)
        and (level is None or sheet.level == level.strip().upper())
        and (segment is None or sheet.segment == segment.strip().upper())
        and (area is None or area.strip().upper() in sheet.area)
        and (not unknown_only or sheet.sheet_kind == SheetKind.UNKNOWN)
    ]


def _classification_title(
    entry: ReconciliationEntry,
) -> Tuple[Optional[str], ClassificationTitleSource]:
    if entry.actual_sheet_title:
        return entry.actual_sheet_title, ClassificationTitleSource.ACTUAL_TITLE
    if entry.declared_sheet_title:
        return entry.declared_sheet_title, ClassificationTitleSource.DECLARED_TITLE
    return None, ClassificationTitleSource.NONE


def _extract_levels(title: str) -> List[str]:
    levels = []
    if "SECOND FLOOR" in title:
        levels.append("SECOND FLOOR")
    if "OFFICE ROOF" in title or re.search(r"\bROOF\b", title):
        levels.append("ROOF")
    if "FOUNDATION" in title:
        levels.append("FOUNDATION")
    return levels


def _extract_segment(title: str) -> Optional[str]:
    match = re.search(r"\bSEGMENT\s+([A-Z0-9]+)\b", title)
    return match.group(1) if match else None


def _sheet_suffix(number: Optional[str]) -> Optional[str]:
    if not number:
        return None
    match = re.search(r"([A-Z])$", number.upper())
    return match.group(1) if match else None


def _group_keys(
    subject: StructuralSubject,
    kind: SheetKind,
    level: Optional[str],
    segment: Optional[str],
    title: str,
) -> List[str]:
    parts = [subject.value]
    if level and subject in {StructuralSubject.FLOOR_FRAMING, StructuralSubject.ROOF_FRAMING}:
        parts.append(level.replace(" ", "_"))
    if segment:
        parts.append(f"SEGMENT_{segment}")
    elif kind in {SheetKind.DETAIL, SheetKind.ELEVATION}:
        parts.append(kind.value)
    keys = [":".join(parts)]
    if subject == StructuralSubject.CONNECTION and "DOUBLE ANGLE" in title:
        keys.append("CONNECTION:DOUBLE_ANGLE")
    return keys


def _count(values: Iterable[str]) -> Dict[str, int]:
    return dict(sorted(Counter(values).items()))
