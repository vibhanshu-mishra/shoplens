"""Typed structural sheet classification and package-index models."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ClassificationTitleSource(str, Enum):
    ACTUAL_TITLE = "ACTUAL_TITLE"
    DECLARED_TITLE = "DECLARED_TITLE"
    NONE = "NONE"


class Discipline(str, Enum):
    STRUCTURAL = "STRUCTURAL"


class SheetKind(str, Enum):
    GENERAL = "GENERAL"
    NOTES = "NOTES"
    PLAN = "PLAN"
    ELEVATION = "ELEVATION"
    DETAIL = "DETAIL"
    SECTION = "SECTION"
    SCHEDULE = "SCHEDULE"
    DIAGRAM = "DIAGRAM"
    VIEW = "VIEW"
    COVER = "COVER"
    UNKNOWN = "UNKNOWN"


class StructuralSubject(str, Enum):
    GENERAL_NOTES = "GENERAL_NOTES"
    LOADING = "LOADING"
    FOUNDATION = "FOUNDATION"
    FOUNDATION_PLAN = "FOUNDATION_PLAN"
    FOUNDATION_DETAIL = "FOUNDATION_DETAIL"
    FLOOR_FRAMING = "FLOOR_FRAMING"
    ROOF_FRAMING = "ROOF_FRAMING"
    PLATFORM_FRAMING = "PLATFORM_FRAMING"
    STAIR_FRAMING = "STAIR_FRAMING"
    BRACED_FRAME = "BRACED_FRAME"
    WIND_BRACING = "WIND_BRACING"
    CONNECTION = "CONNECTION"
    STEEL_FRAMING = "STEEL_FRAMING"
    STEEL_COLUMN = "STEEL_COLUMN"
    BASE_PLATE = "BASE_PLATE"
    SHEAR_CONNECTION = "SHEAR_CONNECTION"
    PLATFORM = "PLATFORM"
    STAIR = "STAIR"
    OTHER_STRUCTURAL = "OTHER_STRUCTURAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ClassifiedSheet:
    pdf_page: Optional[int]
    actual_pdf_pages: List[int]
    sheet_number: Optional[str]
    declared_title: Optional[str]
    actual_title: Optional[str]
    classification_title: Optional[str]
    classification_title_source: ClassificationTitleSource
    discipline: Discipline
    sheet_kind: SheetKind
    secondary_kinds: List[SheetKind]
    subject: StructuralSubject
    secondary_subjects: List[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    area: List[str]
    modifiers: List[str]
    classification_confidence: float
    matched_rule: Optional[str]
    classification_evidence: List[str]
    group_keys: List[str]
    warnings: List[str]
    candidate_rules: List[str] = field(default_factory=list)

    def to_dict(self, include_debug: bool = False) -> Dict[str, Any]:
        result = asdict(self)
        for key in ("classification_title_source", "discipline", "sheet_kind", "subject"):
            result[key] = getattr(self, key).value
        result["secondary_kinds"] = [value.value for value in self.secondary_kinds]
        result["secondary_subjects"] = [value.value for value in self.secondary_subjects]
        if not include_debug:
            result.pop("candidate_rules", None)
        return result


@dataclass(frozen=True)
class PackageIndexResult:
    source_file: str
    total_pdf_pages: int
    declared_sheet_count: int
    indexed_sheet_count: int
    classified_sheet_count: int
    unknown_sheet_count: int
    classification_version: str
    counts_by_kind: Dict[str, int]
    counts_by_subject: Dict[str, int]
    counts_by_level: Dict[str, int]
    counts_by_segment: Dict[str, int]
    counts_by_area: Dict[str, int]
    sheets: List[ClassifiedSheet]
    warnings: List[str]

    def to_dict(self, include_debug: bool = False) -> Dict[str, Any]:
        result = asdict(self)
        result["sheets"] = [sheet.to_dict(include_debug=include_debug) for sheet in self.sheets]
        return result

    def by_sheet_number(self, number: str) -> Optional[ClassifiedSheet]:
        normalized = number.strip().upper()
        return next((sheet for sheet in self.sheets if sheet.sheet_number == normalized), None)

    def by_pdf_page(self, page: int) -> Optional[ClassifiedSheet]:
        return next((sheet for sheet in self.sheets if sheet.pdf_page == page), None)

    def by_kind(self, kind: SheetKind) -> List[ClassifiedSheet]:
        return [sheet for sheet in self.sheets if sheet.sheet_kind == kind]

    def by_subject(self, subject: StructuralSubject) -> List[ClassifiedSheet]:
        return [sheet for sheet in self.sheets if sheet.subject == subject]

    def by_level(self, level: str) -> List[ClassifiedSheet]:
        normalized = level.strip().upper()
        return [sheet for sheet in self.sheets if sheet.level == normalized]

    def by_segment(self, segment: str) -> List[ClassifiedSheet]:
        normalized = segment.strip().upper()
        return [sheet for sheet in self.sheets if sheet.segment == normalized]

    def by_area(self, area: str) -> List[ClassifiedSheet]:
        normalized = area.strip().upper()
        return [sheet for sheet in self.sheets if normalized in sheet.area]
