"""Typed, explainable structural grid-system results."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from shoplens.classification.models import StructuralSubject
from shoplens.geometry.models import LineSegment, PageGeometry


class GridOrientation(str, Enum):
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"


@dataclass(frozen=True)
class GridLabel:
    original_text: str
    normalized_label: str
    page: int
    x: float
    y: float
    width: float
    height: float
    associated_shape: Optional[str]
    confidence: float
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


@dataclass(frozen=True)
class RejectedGridCandidate:
    original_text: str
    page: int
    x: float
    y: float
    reason: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class GridAxis:
    axis_id: str
    orientation: GridOrientation
    normalized_label: str
    alternate_labels: List[str]
    coordinate: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    source_segments: List[LineSegment]
    label_candidates: List[GridLabel]
    intersection_count: int
    confidence: float
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class GridSystem:
    source_file: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    page_geometry: PageGeometry
    horizontal_axes: List[GridAxis]
    vertical_axes: List[GridAxis]
    unassigned_labels: List[GridLabel]
    rejected_candidates: List[RejectedGridCandidate]
    confidence: float
    warnings: List[str]
    grid_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        geometry = self.page_geometry.to_dict()
        geometry["line_count"] = len(self.page_geometry.lines)
        geometry["shape_count"] = len(self.page_geometry.shapes)
        geometry.pop("lines", None)
        geometry.pop("shapes", None)
        result["page_geometry"] = geometry
        for field_name in ("horizontal_axes", "vertical_axes"):
            values = []
            for axis in getattr(self, field_name):
                value = asdict(axis)
                value["orientation"] = axis.orientation.value
                values.append(value)
            result[field_name] = values
        return result
