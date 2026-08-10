"""Typed grid-relative section-label localization results."""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from shoplens.classification.models import StructuralSubject
from shoplens.grids.models import GridSystem
from shoplens.models import SectionFamily


@dataclass(frozen=True)
class GridPointLocation:
    x: float
    y: float
    nearest_horizontal_axis: Optional[str]
    nearest_horizontal_distance: Optional[float]
    nearest_vertical_axis: Optional[str]
    nearest_vertical_distance: Optional[float]
    horizontal_interval: Optional[str]
    vertical_interval: Optional[str]
    bay_id: Optional[str]
    inside_grid_bounds: bool
    on_horizontal_axis: bool
    on_vertical_axis: bool

    @property
    def display(self) -> str:
        if self.bay_id:
            return self.bay_id
        return "outside grid" if not self.inside_grid_bounds else "incomplete grid location"


@dataclass(frozen=True)
class GridRelativeSectionDetection:
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    original_text: str
    normalized_section: str
    section_family: SectionFamily
    detection_x: float
    detection_y: float
    detection_width: float
    detection_height: float
    detection_anchor_x: float
    detection_anchor_y: float
    nearest_horizontal_axis: Optional[str]
    nearest_horizontal_distance: Optional[float]
    nearest_vertical_axis: Optional[str]
    nearest_vertical_distance: Optional[float]
    lower_horizontal_axis: Optional[str]
    upper_horizontal_axis: Optional[str]
    left_vertical_axis: Optional[str]
    right_vertical_axis: Optional[str]
    horizontal_interval: Optional[str]
    vertical_interval: Optional[str]
    bay_id: Optional[str]
    inside_grid_bounds: bool
    inside_valid_bay: bool
    grid_system_id: Optional[str]
    grid_confidence: float
    localization_confidence: float
    coordinate_system: str
    ambiguous: bool
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    outside_horizontal_bounds: bool = False
    outside_vertical_bounds: bool = False
    axis_extent_incomplete: bool = False
    localization_status: str = "UNLOCALIZED"

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        result["section_family"] = self.section_family.value
        return result


@dataclass
class SheetGridSectionLocalization:
    source_file: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    grid_system: Optional[GridSystem]
    total_section_detections: int
    localized_detection_count: int
    inside_grid_count: int
    outside_grid_count: int
    detections_with_complete_bay: int
    detections_on_axes: int
    ambiguous_detection_count: int
    unlocalized_detection_count: int
    detections: List[GridRelativeSectionDetection]
    warnings: List[str]
    record_mode: str
    active_filters: Dict[str, Any] = field(default_factory=dict)
    localization_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        result["grid_system"] = self.grid_system.to_dict() if self.grid_system else None
        result["detections"] = [item.to_dict() for item in self.detections]
        return result
