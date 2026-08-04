"""Typed, conservative member-line candidate results."""

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from shoplens.classification.models import StructuralSubject
from shoplens.geometry.models import LineSegment, PageGeometry
from shoplens.grids.models import GridSystem


class LineOrientation(str, Enum):
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"
    DIAGONAL = "DIAGONAL"
    OTHER = "OTHER"


class MemberCandidateType(str, Enum):
    LINEAR_MEMBER_CANDIDATE = "LINEAR_MEMBER_CANDIDATE"
    DIAGONAL_MEMBER_CANDIDATE = "DIAGONAL_MEMBER_CANDIDATE"
    SHORT_MEMBER_CANDIDATE = "SHORT_MEMBER_CANDIDATE"
    UNKNOWN_LINEAR_CANDIDATE = "UNKNOWN_LINEAR_CANDIDATE"


@dataclass(frozen=True)
class RejectedMemberLine:
    pdf_page: int
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    length: float
    orientation_class: LineOrientation
    line_width: Optional[float]
    dash_pattern: Tuple[float, ...]
    geometry_source: str
    rejection_reason: str
    evidence: List[str] = field(default_factory=list)
    nearest_grid_information: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["orientation_class"] = self.orientation_class.value
        return result


@dataclass(frozen=True)
class MemberLineCandidate:
    candidate_id: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    grid_system_id: str
    original_start_x: float
    original_start_y: float
    original_end_x: float
    original_end_y: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    length: float
    orientation_angle: float
    orientation_class: LineOrientation
    source_segments: List[LineSegment]
    source_segment_count: int
    duplicate_count: int
    line_width: Optional[float]
    dash_pattern: Tuple[float, ...]
    geometry_source: str
    nearest_start_horizontal_grid: Optional[str]
    nearest_start_vertical_grid: Optional[str]
    nearest_end_horizontal_grid: Optional[str]
    nearest_end_vertical_grid: Optional[str]
    start_grid_location: str
    end_grid_location: str
    crossed_horizontal_grids: List[str]
    crossed_vertical_grids: List[str]
    intersection_count: int
    inside_dominant_grid: bool
    start_near_grid: bool
    end_near_grid: bool
    near_grid_aligned: bool
    candidate_type: MemberCandidateType
    confidence: float
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        result["orientation_class"] = self.orientation_class.value
        result["candidate_type"] = self.candidate_type.value
        return result


@dataclass
class MemberLineCandidateResult:
    source_file: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    page_geometry: PageGeometry
    grid_system: GridSystem
    plan_region_bounds: Tuple[float, float, float, float]
    plan_region_margin: float
    raw_segment_count: int
    duplicate_segment_count: int
    deduplicated_segment_count: int
    primitive_segments_rejected_count: int
    primitive_segments_entering_merge_count: int
    merged_chain_count: int
    accepted_candidate_count: int
    rejected_chain_count: int
    # Deprecated: combined duplicate, primitive-rejection, and chain-rejection records.
    rejected_candidate_count: int
    candidates: List[MemberLineCandidate]
    rejected_candidates: List[RejectedMemberLine]
    warnings: List[str]
    active_filters: Dict[str, Any] = field(default_factory=dict)
    candidate_version: str = "1.0"

    def to_dict(self, include_rejected: bool = True) -> Dict[str, Any]:
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        geometry = self.page_geometry.to_dict()
        geometry["line_count"] = len(self.page_geometry.lines)
        geometry["shape_count"] = len(self.page_geometry.shapes)
        geometry.pop("lines", None)
        geometry.pop("shapes", None)
        result["page_geometry"] = geometry
        result["grid_system"] = self.grid_system.to_dict()
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        result["candidates"] = [item.to_dict() for item in self.candidates]
        result["rejected_candidates"] = (
            [item.to_dict() for item in self.rejected_candidates] if include_rejected else []
        )
        return result
