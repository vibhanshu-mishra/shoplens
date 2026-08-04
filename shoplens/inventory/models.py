"""Typed classified steel-label inventory models."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from shoplens.classification.models import SheetKind, StructuralSubject
from shoplens.models import SectionFamily


@dataclass(frozen=True)
class InventoryCount:
    detection_count: int
    sheet_count: int

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class InventoryFilters:
    sheet_number: Optional[str] = None
    page: Optional[int] = None
    kind: Optional[SheetKind] = None
    subject: Optional[StructuralSubject] = None
    level: Optional[str] = None
    segment: Optional[str] = None
    area: Optional[str] = None
    family: Optional[SectionFamily] = None
    section: Optional[str] = None
    with_detections: bool = False
    without_detections: bool = False

    def matches_identity(self, sheet: "SheetSectionInventory") -> bool:
        return (
            (self.sheet_number is None or sheet.sheet_number == self.sheet_number.strip().upper())
            and (self.page is None or self.page in sheet.pdf_pages)
        )

    def matches_classification(self, sheet: "SheetSectionInventory") -> bool:
        return (
            (self.kind is None or sheet.sheet_kind == self.kind)
            and (self.subject is None or sheet.sheet_subject == self.subject)
            and (self.level is None or sheet.level == self.level.strip().upper())
            and (self.segment is None or sheet.segment == self.segment.strip().upper())
            and (self.area is None or self.area.strip().upper() in sheet.area)
        )

    def matches_detections(self, sheet: "SheetSectionInventory") -> bool:
        normalized_section = self.section.strip().upper() if self.section else None
        return (
            (self.family is None or self.family.value in sheet.counts_by_family)
            and (normalized_section is None or normalized_section in sheet.counts_by_section)
            and (not self.with_detections or bool(sheet.detections))
            and (not self.without_detections or not sheet.detections)
        )


@dataclass(frozen=True)
class ClassifiedSectionDetection:
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_kind: Optional[SheetKind]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    area: List[str]
    original_text: str
    normalized_section: str
    section_family: SectionFamily
    raw_x: float
    raw_y: float
    raw_width: float
    raw_height: float
    confidence: float
    duplicate_count: int
    record_mode: str
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["sheet_kind"] = self.sheet_kind.value if self.sheet_kind else None
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        result["section_family"] = self.section_family.value
        return result


@dataclass(frozen=True)
class SheetSectionInventory:
    pdf_page: Optional[int]
    pdf_pages: List[int]
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_kind: SheetKind
    sheet_subject: StructuralSubject
    level: Optional[str]
    segment: Optional[str]
    area: List[str]
    raw_detection_count: int
    deduplicated_detection_count: int
    unique_section_count: int
    counts_by_family: Dict[str, int]
    counts_by_section: Dict[str, int]
    detections: List[ClassifiedSectionDetection]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["sheet_kind"] = self.sheet_kind.value
        result["sheet_subject"] = self.sheet_subject.value
        result["detections"] = [item.to_dict() for item in self.detections]
        return result


@dataclass(frozen=True)
class PackageSectionInventory:
    source_file: str
    total_indexed_sheets: int
    sheets_with_detections: int
    sheets_without_detections: int
    raw_detection_count: int
    deduplicated_detection_count: int
    unique_section_count: int
    counts_by_family: Dict[str, InventoryCount]
    counts_by_section: Dict[str, InventoryCount]
    counts_by_subject: Dict[str, InventoryCount]
    counts_by_level: Dict[str, InventoryCount]
    counts_by_segment: Dict[str, InventoryCount]
    counts_by_area: Dict[str, InventoryCount]
    sheets: List[SheetSectionInventory]
    unmatched_detections: List[ClassifiedSectionDetection]
    warnings: List[str]
    inventory_version: str
    record_mode: str
    duplicate_suppression_count: int
    unmatched_detection_count: int

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        for field_name in (
            "counts_by_family",
            "counts_by_section",
            "counts_by_subject",
            "counts_by_level",
            "counts_by_segment",
            "counts_by_area",
        ):
            values = getattr(self, field_name)
            result[field_name] = {key: value.to_dict() for key, value in values.items()}
        result["sheets"] = [sheet.to_dict() for sheet in self.sheets]
        result["unmatched_detections"] = [item.to_dict() for item in self.unmatched_detections]
        return result
