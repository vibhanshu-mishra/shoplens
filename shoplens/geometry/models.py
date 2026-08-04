"""Provider-neutral PDF page geometry models."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class LineSegment:
    page: int
    x1: float
    y1: float
    x2: float
    y2: float
    width: Optional[float] = None
    dash: Tuple[float, ...] = ()
    source: str = "pdf_path"
    confidence: float = 1.0

    @property
    def length(self) -> float:
        return ((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2) ** 0.5


@dataclass(frozen=True)
class ShapeGeometry:
    page: int
    kind: str
    bounds: Box
    source: str
    confidence: float = 1.0


@dataclass(frozen=True)
class PageGeometry:
    pdf_page: int
    width: float
    height: float
    rotation: int
    media_box: Box
    crop_box: Box
    coordinate_system: str
    provider: str
    lines: List[LineSegment] = field(default_factory=list)
    shapes: List[ShapeGeometry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    conversion: str = "identity"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
