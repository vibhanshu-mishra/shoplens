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
    observation_id: Optional[str] = None
    bubble_alternative_count: int = 0

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
    bubble_diagnostics: Dict[str, int] = field(default_factory=dict)
    grid_version: str = "1.0"
    grid_system_id: str = ""
    system_evidence: List[str] = field(default_factory=list)
    secondary_grid_systems: List["GridSystem"] = field(default_factory=list)

    @property
    def all_grid_systems(self) -> List["GridSystem"]:
        """Return this primary system followed by independently supported systems."""

        return [self, *self.secondary_grid_systems]

    @property
    def system_bounds(self) -> Optional[Dict[str, float]]:
        if not self.horizontal_axes or not self.vertical_axes:
            return None
        return {
            "x_min": min(axis.coordinate for axis in self.vertical_axes),
            "x_max": max(axis.coordinate for axis in self.vertical_axes),
            "y_min": min(axis.coordinate for axis in self.horizontal_axes),
            "y_max": max(axis.coordinate for axis in self.horizontal_axes),
        }

    def to_dict(self, include_hierarchy: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        result.pop("secondary_grid_systems", None)
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
        result["system_bounds"] = self.system_bounds
        if include_hierarchy:
            result["secondary_grid_systems"] = [
                system.to_dict(include_hierarchy=False)
                for system in self.secondary_grid_systems
            ]
            result["grid_systems"] = [
                _grid_system_summary(system) for system in self.all_grid_systems
            ]
            result["primary_grid_system_id"] = self.grid_system_id
            result["secondary_grid_system_count"] = len(self.secondary_grid_systems)
        else:
            result["secondary_grid_systems"] = []
            result["grid_systems"] = [_grid_system_summary(self)]
            result["primary_grid_system_id"] = self.grid_system_id
            result["secondary_grid_system_count"] = 0
        return result


def _grid_system_summary(system: GridSystem) -> Dict[str, Any]:
    return {
        "grid_system_id": system.grid_system_id,
        "horizontal_axis_count": len(system.horizontal_axes),
        "vertical_axis_count": len(system.vertical_axes),
        "confidence": system.confidence,
        "system_bounds": system.system_bounds,
    }
