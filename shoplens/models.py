"""Public data models for ShopLens results."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class SectionFamily(str, Enum):
    """Steel section families supported by the first milestone."""

    W = "W"
    HSS = "HSS"
    C = "C"
    L = "L"
    DOUBLE_ANGLE = "DOUBLE_ANGLE"
    PL = "PL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SteelLabel:
    """A steel designation and its location in a PDF."""

    page_number: int
    original_text: str
    normalized_text: str
    section_family: SectionFamily
    x: float
    y: float
    width: float
    height: float
    confidence: float
    duplicate_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-ready representation."""

        result = asdict(self)
        result["section_family"] = self.section_family.value
        result["raw_x"] = self.x
        result["raw_y"] = self.y
        result["raw_width"] = self.width
        result["raw_height"] = self.height
        return result


@dataclass(frozen=True)
class RejectedCandidate:
    """A section-like text fragment rejected by ShopLens validation."""

    page_number: int
    original_text: str
    reason: str
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TextDiagnostic:
    """Diagnostic view of one source pdf-inspector TextItem."""

    source_page: int
    page_number: int
    text: str
    x: float
    y: float
    width: float
    height: float
    font: Optional[str]
    font_size: Optional[float]
    is_candidate: bool
    section_detected: bool
    detections: List[SteelLabel]
    rejection_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["raw_x"] = self.x
        result["raw_y"] = self.y
        result["raw_width"] = self.width
        result["raw_height"] = self.height
        result["detections"] = [item.to_dict() for item in self.detections]
        return result
