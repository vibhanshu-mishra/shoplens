"""Public data models for ShopLens results."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict


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

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-ready representation."""

        result = asdict(self)
        result["section_family"] = self.section_family.value
        return result
