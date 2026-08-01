"""Typed models for declared drawing Sheet Lists."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SheetEntry:
    """One declared row in a drawing package's Sheet List."""

    sheet_number: str
    sheet_name: str
    source_page: int
    number_original_text: str
    name_original_text: str
    number_x: float
    number_y: float
    number_width: float
    number_height: float
    name_x: float
    name_y: float
    name_width: float
    name_height: float
    confidence: float
    warnings: List[str] = field(default_factory=list)
    name_comparison_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SheetListResult:
    """Complete declared Sheet List extraction result."""

    source_file: str
    pages_scanned: List[int]
    sheet_list_pages: List[int]
    entries: List[SheetEntry]
    duplicate_sheet_numbers: List[str]
    warnings: List[str]
    declared_total: Optional[int] = None
    debug: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_debug: bool = False) -> Dict[str, Any]:
        result = asdict(self)
        result["entries"] = [entry.to_dict() for entry in self.entries]
        if not include_debug:
            result.pop("debug", None)
        return result
