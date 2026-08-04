"""Typed, neutral repetitive linear-pattern results."""

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from shoplens.classification.models import StructuralSubject
from shoplens.geometry.models import PageGeometry
from shoplens.grids.models import GridSystem
from shoplens.members.models import MemberLineCandidate


class LinearPatternType(str, Enum):
    PARALLEL_LINE_GROUP = "PARALLEL_LINE_GROUP"
    REGULAR_SPACING_FIELD = "REGULAR_SPACING_FIELD"
    DOUBLE_LINE_PAIR_GROUP = "DOUBLE_LINE_PAIR_GROUP"
    ORTHOGONAL_NETWORK = "ORTHOGONAL_NETWORK"
    COLLINEAR_CHAIN_GROUP = "COLLINEAR_CHAIN_GROUP"
    DENSE_LINEAR_FIELD = "DENSE_LINEAR_FIELD"
    ISOLATED_CANDIDATE_GROUP = "ISOLATED_CANDIDATE_GROUP"
    MIXED_LINEAR_PATTERN = "MIXED_LINEAR_PATTERN"
    UNKNOWN_LINEAR_PATTERN = "UNKNOWN_LINEAR_PATTERN"


@dataclass
class LinearPatternMember:
    candidate_id: str
    primary_pattern_id: Optional[str]
    secondary_pattern_ids: List[str]
    membership_role: str
    membership_confidence: float
    distance_to_pattern_axis: float
    position_index: int
    pair_id: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class LinearPattern:
    pattern_id: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    pattern_type: LinearPatternType
    primary_orientation: str
    secondary_orientations: List[str]
    candidate_ids: List[str]
    candidate_count: int
    source_candidates: List[MemberLineCandidate]
    members: List[LinearPatternMember]
    bounding_box: Tuple[float, float, float, float]
    centroid_x: float
    centroid_y: float
    principal_angle: float
    orientation_spread: float
    minimum_length: float
    maximum_length: float
    mean_length: float
    median_length: float
    length_variation: float
    perpendicular_offsets: List[float]
    spacing_values: List[float]
    mean_spacing: Optional[float]
    median_spacing: Optional[float]
    spacing_variation: Optional[float]
    regular_spacing_score: float
    missing_slot_count: int
    spacing_outliers: List[float]
    start_endpoint_band: Tuple[float, float]
    end_endpoint_band: Tuple[float, float]
    endpoint_alignment_score: float
    pair_count: int
    pair_ids: List[str]
    pair_separations: List[float]
    mean_pair_separation: Optional[float]
    pair_separation_variation: Optional[float]
    unpaired_candidate_ids: List[str]
    horizontal_candidate_count: int
    vertical_candidate_count: int
    diagonal_candidate_count: int
    other_candidate_count: int
    grid_system_id: str
    grid_bays: List[str]
    crossed_horizontal_grids: List[str]
    crossed_vertical_grids: List[str]
    inside_grid_fraction: float
    density: float
    component_pattern_ids: List[str]
    unique_primary_candidate_count: int
    horizontal_component_count: int
    vertical_component_count: int
    component_intersection_count: int
    candidate_coverage_fraction: float
    network_signature: Optional[str]
    overlap_with_other_networks: List[str]
    intersection_count: int
    intersection_density: float
    nearby_section_labels: List[str]
    nearby_section_count: int
    nearby_unique_sections: List[str]
    distance_to_nearest_section_label: Optional[float]
    non_binding_text_context: str
    confidence: float
    evidence: List[str]
    warnings: List[str]

    def to_dict(self, include_candidates: bool = True) -> Dict[str, Any]:
        value = asdict(self)
        value["pattern_type"] = self.pattern_type.value
        value["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        value["source_candidates"] = (
            [item.to_dict() for item in self.source_candidates] if include_candidates else []
        )
        return value


@dataclass
class PatternResult:
    source_file: str
    pdf_page: int
    sheet_number: Optional[str]
    sheet_title: Optional[str]
    sheet_subject: Optional[StructuralSubject]
    level: Optional[str]
    segment: Optional[str]
    page_geometry: PageGeometry
    plan_region: Tuple[float, float, float, float]
    grid_system: GridSystem
    input_candidate_count: int
    primary_clustered_candidate_count: int
    unclustered_candidate_count: int
    secondary_membership_count: int
    network_candidates_unique_count: int
    secondary_network_membership_count: int
    orthogonal_network_count: int
    redundant_networks_suppressed_count: int
    pattern_count: int
    primary_patterns_by_type: Dict[str, int]
    hierarchical_pattern_count: int
    patterns: List[LinearPattern]
    hierarchical_patterns: List[LinearPattern]
    unclustered_candidates: List[MemberLineCandidate]
    warnings: List[str]
    stage_timings: Dict[str, float]
    active_filters: Dict[str, Any] = field(default_factory=dict)
    pattern_version: str = "1.0"

    def to_dict(self, include_candidates: bool = True, include_secondary: bool = True) -> Dict[str, Any]:
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        geometry = self.page_geometry.to_dict()
        geometry["line_count"] = len(self.page_geometry.lines)
        geometry["shape_count"] = len(self.page_geometry.shapes)
        geometry.pop("lines", None)
        geometry.pop("shapes", None)
        result["page_geometry"] = geometry
        result["grid_system"] = self.grid_system.to_dict()
        result["sheet_subject"] = self.sheet_subject.value if self.sheet_subject else None
        result["patterns"] = [item.to_dict(include_candidates) for item in self.patterns]
        result["hierarchical_patterns"] = (
            [item.to_dict(include_candidates) for item in self.hierarchical_patterns]
            if include_secondary else []
        )
        result["unclustered_candidates"] = (
            [item.to_dict() for item in self.unclustered_candidates] if include_candidates else []
        )
        return result
