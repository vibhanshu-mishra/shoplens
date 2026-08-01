"""Typed title-block extraction and reconciliation models."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ReconciliationStatus(str, Enum):
    MATCH = "MATCH"
    TITLE_VARIATION = "TITLE_VARIATION"
    TITLE_MISMATCH = "TITLE_MISMATCH"
    DECLARED_BUT_MISSING = "DECLARED_BUT_MISSING"
    PRESENT_BUT_UNDECLARED = "PRESENT_BUT_UNDECLARED"
    DUPLICATE_SHEET_NUMBER = "DUPLICATE_SHEET_NUMBER"
    UNIDENTIFIED_PAGE = "UNIDENTIFIED_PAGE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass(frozen=True)
class TitleBlockPage:
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    revision: Optional[str]
    confidence: float
    layout_id: Optional[str]
    number_original_text: Optional[str]
    title_original_text: Optional[str]
    number_x: Optional[float]
    number_y: Optional[float]
    number_width: Optional[float]
    number_height: Optional[float]
    title_x: Optional[float]
    title_y: Optional[float]
    title_width: Optional[float]
    title_height: Optional[float]
    evidence: List[str]
    candidate_count: int
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TitleBlockResult:
    source_file: str
    total_pdf_pages_processed: int
    identified_page_count: int
    unidentified_pages: List[int]
    low_confidence_pages: List[int]
    layouts_discovered: List[Dict[str, Any]]
    duplicate_sheet_numbers: Dict[str, List[int]]
    pages: List[TitleBlockPage]
    warnings: List[str]
    debug: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_debug: bool = False) -> Dict[str, Any]:
        result = asdict(self)
        result["pages"] = [page.to_dict() for page in self.pages]
        if not include_debug:
            result.pop("debug", None)
        return result


@dataclass(frozen=True)
class ReconciliationEntry:
    declared_sheet_number: Optional[str]
    declared_sheet_title: Optional[str]
    actual_pdf_pages: List[int]
    actual_sheet_number: Optional[str]
    actual_sheet_title: Optional[str]
    revision: Optional[str]
    status: ReconciliationStatus
    title_similarity: Optional[float]
    confidence: float
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass(frozen=True)
class ReconciliationResult:
    source_file: str
    declared_sheet_count: int
    total_pdf_pages_processed: int
    identified_page_count: int
    unidentified_pages: List[int]
    missing_declared_sheets: List[str]
    undeclared_actual_sheets: List[str]
    duplicate_actual_sheet_numbers: Dict[str, List[int]]
    title_mismatches: List[str]
    entries: List[ReconciliationEntry]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["entries"] = [entry.to_dict() for entry in self.entries]
        return result
