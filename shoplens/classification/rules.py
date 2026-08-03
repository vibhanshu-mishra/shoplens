"""Deterministic ordered rules for structural sheet titles."""

import re
from dataclasses import dataclass
from typing import List, Optional, Pattern, Sequence, Tuple

from shoplens.title_blocks.reconcile import normalize_title

from .models import SheetKind, StructuralSubject


@dataclass(frozen=True)
class ClassificationRule:
    rule_id: str
    priority: int
    pattern: Pattern[str]
    sheet_kind: SheetKind
    subject: StructuralSubject
    confidence: float
    secondary_kinds: Tuple[SheetKind, ...] = ()
    secondary_subjects: Tuple[StructuralSubject, ...] = ()

    def matches(self, title: str) -> bool:
        return self.pattern.search(title) is not None


def _rule(
    rule_id: str,
    priority: int,
    pattern: str,
    kind: SheetKind,
    subject: StructuralSubject,
    confidence: float,
    secondary_kinds: Sequence[SheetKind] = (),
    secondary_subjects: Sequence[StructuralSubject] = (),
) -> ClassificationRule:
    return ClassificationRule(
        rule_id,
        priority,
        re.compile(pattern),
        kind,
        subject,
        confidence,
        tuple(secondary_kinds),
        tuple(secondary_subjects),
    )


RULES = (
    _rule("GENERAL_NOTES", 100, r"^GENERAL NOTES$", SheetKind.NOTES, StructuralSubject.GENERAL_NOTES, 0.98),
    _rule("LOADING_DIAGRAM", 100, r"\b(?:LOAD|LOADING).*\bDIAGRAMS?\b", SheetKind.DIAGRAM, StructuralSubject.LOADING, 0.98),
    _rule("WIND_UPLIFT_DIAGRAM", 100, r"\bWIND UPLIFT DIAGRAMS?\b", SheetKind.DIAGRAM, StructuralSubject.LOADING, 0.98),
    _rule("DEFLECTION_DIAGRAM", 100, r"\bDEFLECTION DIAGRAMS?\b", SheetKind.DIAGRAM, StructuralSubject.LOADING, 0.98),
    _rule("DRAWING_VIEW", 100, r"\b(?:3D|ISOMETRIC|AXONOMETRIC|PERSPECTIVE) VIEW\b", SheetKind.VIEW, StructuralSubject.OTHER_STRUCTURAL, 0.98),
    _rule("FOUNDATION_PLAN", 100, r"\bFOUNDATION PLAN\b", SheetKind.PLAN, StructuralSubject.FOUNDATION_PLAN, 0.98),
    _rule("SECOND_FLOOR_FRAMING_PLAN", 100, r"\bSECOND FLOOR FRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.FLOOR_FRAMING, 0.98),
    _rule("ROOF_SECOND_FLOOR_FRAMING_PLAN", 105, r"\bROOF AND SECOND FLOOR FRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.ROOF_FRAMING, 0.98, secondary_subjects=(StructuralSubject.FLOOR_FRAMING,)),
    _rule("MECHANICAL_PLATFORM_FRAMING_PLAN", 100, r"\bMECHANICAL PLATFORM FRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.PLATFORM_FRAMING, 0.98),
    _rule("OFFICE_ROOF_PLATFORM_FRAMING_PLAN", 100, r"\bPLATFORM AND OFFICE ROOF FRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.ROOF_FRAMING, 0.98, secondary_subjects=(StructuralSubject.PLATFORM_FRAMING,)),
    _rule("OFFICE_ROOF_FRAMING_PLAN", 100, r"\bOFFICE ROOF\b.*\bFRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.ROOF_FRAMING, 0.98),
    _rule("ROOF_FRAMING_PLAN", 95, r"\bROOF FRAMING PLAN\b", SheetKind.PLAN, StructuralSubject.ROOF_FRAMING, 0.95),
    _rule("BRACED_FRAME_ELEVATION", 100, r"\bBRACED FRAME ELEVATIONS?\b", SheetKind.ELEVATION, StructuralSubject.BRACED_FRAME, 0.98),
    _rule("WIND_BRACING_DETAIL", 100, r"\bWIND BRACING DETAILS?\b", SheetKind.DETAIL, StructuralSubject.WIND_BRACING, 0.98),
    _rule("FOUNDATION_DETAIL", 100, r"\bFOUNDATION DETAILS?\b", SheetKind.DETAIL, StructuralSubject.FOUNDATION_DETAIL, 0.98),
    _rule("STEEL_COLUMN_BASE_PLATE", 100, r"\bSTEEL COLUMN DETAILS?\b.*\bBASE PLATE SCHEDULE\b", SheetKind.DETAIL, StructuralSubject.STEEL_COLUMN, 0.98, secondary_kinds=(SheetKind.SCHEDULE,), secondary_subjects=(StructuralSubject.BASE_PLATE,)),
    _rule("SHEAR_PLATE_CONNECTION", 100, r"\b(?:SHEAR PLATE|EXTENDED SHEAR PLATE)\b", SheetKind.DETAIL, StructuralSubject.CONNECTION, 0.98, secondary_subjects=(StructuralSubject.SHEAR_CONNECTION,)),
    _rule("CONNECTION_DETAIL", 95, r"\b(?:CONNECTION|DOUBLE ANGLE)\b", SheetKind.DETAIL, StructuralSubject.CONNECTION, 0.95),
    _rule("STEEL_ROOF_FRAMING_DETAIL", 100, r"\bSTEEL ROOF FRAMING SECTIONS? AND DETAILS?\b", SheetKind.DETAIL, StructuralSubject.ROOF_FRAMING, 0.98, secondary_kinds=(SheetKind.SECTION,)),
    _rule("STEEL_FLOOR_FRAMING_DETAIL", 100, r"\bSTEEL FLOOR FRAMING SECTIONS? AND DETAILS?\b", SheetKind.DETAIL, StructuralSubject.FLOOR_FRAMING, 0.98, secondary_kinds=(SheetKind.SECTION,)),
    _rule("STEEL_FRAMING_DETAIL", 95, r"\bSTEEL FRAMING SECTIONS? AND DETAILS?\b", SheetKind.DETAIL, StructuralSubject.STEEL_FRAMING, 0.95, secondary_kinds=(SheetKind.SECTION,)),
    _rule("STAIR_PLANS_AND_DETAILS", 100, r"\bSTAIR PLANS? AND DETAILS?\b", SheetKind.DETAIL, StructuralSubject.STAIR, 0.98, secondary_kinds=(SheetKind.PLAN,)),
    _rule("STAIR_TOWER_FRAMING_PLAN", 100, r"\bSTAIR TOWER FRAMING PLANS?\b", SheetKind.PLAN, StructuralSubject.STAIR_FRAMING, 0.98),
    _rule("STAIR_FRAMING_DETAIL", 100, r"\bSTAIR FRAMING DETAILS?\b", SheetKind.DETAIL, StructuralSubject.STAIR_FRAMING, 0.98),
    _rule("STEEL_PLATFORM_DETAIL", 100, r"\bSTEEL PLATFORM DETAILS?\b", SheetKind.DETAIL, StructuralSubject.PLATFORM, 0.98),
    _rule("GENERIC_FRAMING_PLAN", 70, r"\bFRAMING PLANS?\b", SheetKind.PLAN, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_DIAGRAM", 70, r"\bDIAGRAMS?\b", SheetKind.DIAGRAM, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_DETAIL", 60, r"\bDETAILS?\b", SheetKind.DETAIL, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_PLAN", 60, r"\bPLANS?\b", SheetKind.PLAN, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_ELEVATION", 60, r"\bELEVATIONS?\b", SheetKind.ELEVATION, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_SECTION", 60, r"\bSECTIONS?\b", SheetKind.SECTION, StructuralSubject.OTHER_STRUCTURAL, 0.80),
    _rule("GENERIC_SCHEDULE", 60, r"\bSCHEDULES?\b", SheetKind.SCHEDULE, StructuralSubject.OTHER_STRUCTURAL, 0.80),
)


def normalize_classification_title(value: str) -> str:
    return normalize_title(value)


def matching_rules(title: str) -> List[ClassificationRule]:
    return [rule for rule in RULES if rule.matches(title)]


def select_rule(matches: Sequence[ClassificationRule]) -> Tuple[Optional[ClassificationRule], bool]:
    if not matches:
        return None, False
    highest = max(rule.priority for rule in matches)
    finalists = [rule for rule in matches if rule.priority == highest]
    assignments = {(rule.sheet_kind, rule.subject) for rule in finalists}
    return finalists[0], len(assignments) > 1
