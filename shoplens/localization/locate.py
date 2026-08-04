"""Deterministic, geometry-first localization of section annotations to grids."""

from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from shoplens.grids.models import GridAxis, GridSystem
from shoplens.inventory.models import ClassifiedSectionDetection
from shoplens.models import SectionFamily

from .models import GridPointLocation, GridRelativeSectionDetection, SheetGridSectionLocalization

ON_AXIS_TOLERANCE = 6.0
NEAR_INTERSECTION_TOLERANCE = 18.0
LOW_GRID_CONFIDENCE = 0.70


@dataclass(frozen=True)
class _LocalizationContext:
    grid: GridSystem
    anchor_x: float
    anchor_y: float
    nearest_h: Optional[GridAxis]
    nearest_v: Optional[GridAxis]
    lower_h: Optional[GridAxis]
    upper_h: Optional[GridAxis]
    left_v: Optional[GridAxis]
    right_v: Optional[GridAxis]
    horizontal_interval: Optional[str]
    vertical_interval: Optional[str]
    inside: bool
    on_h: bool
    on_v: bool
    ambiguous_system: bool

    @property
    def complete_bay(self) -> bool:
        return bool(
            self.inside
            and self.horizontal_interval
            and self.vertical_interval
            and not self.on_h
            and not self.on_v
        )

    @property
    def bay_id(self) -> Optional[str]:
        if self.inside and self.horizontal_interval and self.vertical_interval:
            return f"{self.vertical_interval} / {self.horizontal_interval}"
        return None


def localize_section_detections(
    source_file: str,
    detections: Iterable[ClassifiedSectionDetection],
    grids: Union[GridSystem, Sequence[GridSystem], None],
    record_mode: str = "deduplicated",
) -> SheetGridSectionLocalization:
    records = list(detections)
    systems = _grid_list(grids)
    localized = [_localize(item, systems) for item in records]
    selected_grid = systems[0] if systems else None
    warnings = []
    if not systems:
        warnings.append("NO_GRID_SYSTEM")
    if len(systems) > 1 or any("MULTIPLE_SIMILAR_GRID_SYSTEMS" in grid.warnings for grid in systems):
        warnings.append("MULTIPLE_GRID_SYSTEMS")
    return SheetGridSectionLocalization(
        source_file=source_file,
        pdf_page=(selected_grid.pdf_page if selected_grid else (records[0].pdf_page if records else 0)),
        sheet_number=(selected_grid.sheet_number if selected_grid else (records[0].sheet_number if records else None)),
        sheet_title=(selected_grid.sheet_title if selected_grid else (records[0].sheet_title if records else None)),
        grid_system=selected_grid,
        total_section_detections=len(records),
        localized_detection_count=sum(item.nearest_horizontal_axis is not None or item.nearest_vertical_axis is not None for item in localized),
        inside_grid_count=sum(item.inside_grid_bounds for item in localized),
        outside_grid_count=sum(not item.inside_grid_bounds for item in localized),
        detections_with_complete_bay=sum(item.inside_valid_bay for item in localized),
        detections_on_axes=sum("ON_HORIZONTAL_AXIS" in item.warnings or "ON_VERTICAL_AXIS" in item.warnings for item in localized),
        ambiguous_detection_count=sum(item.ambiguous for item in localized),
        detections=localized,
        warnings=warnings,
        record_mode=record_mode,
    )


def locate_point_to_grid(grid: GridSystem, x: float, y: float) -> GridPointLocation:
    """Describe one raw page-coordinate point relative to a grid system."""

    context = _localization_context([grid], x, y)
    return GridPointLocation(
        x=x,
        y=y,
        nearest_horizontal_axis=(context.nearest_h.normalized_label if context.nearest_h else None),
        nearest_horizontal_distance=(y - context.nearest_h.coordinate if context.nearest_h else None),
        nearest_vertical_axis=(context.nearest_v.normalized_label if context.nearest_v else None),
        nearest_vertical_distance=(x - context.nearest_v.coordinate if context.nearest_v else None),
        horizontal_interval=context.horizontal_interval,
        vertical_interval=context.vertical_interval,
        bay_id=context.bay_id,
        inside_grid_bounds=context.inside,
        on_horizontal_axis=context.on_h,
        on_vertical_axis=context.on_v,
    )


def filter_localizations(
    detections: Iterable[GridRelativeSectionDetection],
    family: Optional[SectionFamily] = None,
    section: Optional[str] = None,
    inside_only: bool = False,
    outside_only: bool = False,
    ambiguous_only: bool = False,
) -> List[GridRelativeSectionDetection]:
    normalized = section.strip().upper() if section else None
    return [
        item
        for item in detections
        if (family is None or item.section_family == family)
        and (normalized is None or item.normalized_section == normalized)
        and (not inside_only or item.inside_grid_bounds)
        and (not outside_only or not item.inside_grid_bounds)
        and (not ambiguous_only or item.ambiguous)
    ]


def with_filtered_detections(
    result: SheetGridSectionLocalization,
    detections: List[GridRelativeSectionDetection],
    active_filters: Dict[str, object],
) -> SheetGridSectionLocalization:
    return replace(result, detections=detections, active_filters=active_filters)


def _localize(
    detection: ClassifiedSectionDetection, systems: Sequence[GridSystem]
) -> GridRelativeSectionDetection:
    anchor_x = detection.raw_x + detection.raw_width / 2.0
    anchor_y = detection.raw_y + detection.raw_height / 2.0
    if not systems:
        return _empty_localization(detection, anchor_x, anchor_y)
    context = _localization_context(systems, anchor_x, anchor_y)
    label_ambiguity = any(
        _ambiguous_label(axis.normalized_label)
        for axis in (context.nearest_h, context.nearest_v)
        if axis
    )
    warnings, evidence = _explain_localization(context, systems, label_ambiguity)
    score = _confidence(context, label_ambiguity)
    if score < 0.60:
        warnings.append("LOW_LOCALIZATION_CONFIDENCE")
    ambiguous = context.ambiguous_system or label_ambiguity or score < 0.60
    if context.complete_bay:
        evidence.extend(["SURROUNDING_HORIZONTAL_AXES", "SURROUNDING_VERTICAL_AXES", "COMPLETE_GRID_BAY"])
    return _localized_detection(detection, context, score, ambiguous, evidence, warnings)


def _localized_detection(
    detection: ClassifiedSectionDetection,
    context: _LocalizationContext,
    score: float,
    ambiguous: bool,
    evidence: List[str],
    warnings: List[str],
) -> GridRelativeSectionDetection:
    return GridRelativeSectionDetection(
        pdf_page=detection.pdf_page, sheet_number=detection.sheet_number, sheet_title=detection.sheet_title,
        sheet_subject=detection.sheet_subject, level=detection.level, segment=detection.segment,
        original_text=detection.original_text, normalized_section=detection.normalized_section,
        section_family=detection.section_family, detection_x=detection.raw_x, detection_y=detection.raw_y,
        detection_width=detection.raw_width, detection_height=detection.raw_height,
        detection_anchor_x=context.anchor_x, detection_anchor_y=context.anchor_y,
        nearest_horizontal_axis=context.nearest_h.normalized_label if context.nearest_h else None,
        nearest_horizontal_distance=context.anchor_y - context.nearest_h.coordinate if context.nearest_h else None,
        nearest_vertical_axis=context.nearest_v.normalized_label if context.nearest_v else None,
        nearest_vertical_distance=context.anchor_x - context.nearest_v.coordinate if context.nearest_v else None,
        lower_horizontal_axis=context.lower_h.normalized_label if context.lower_h else None,
        upper_horizontal_axis=context.upper_h.normalized_label if context.upper_h else None,
        left_vertical_axis=context.left_v.normalized_label if context.left_v else None,
        right_vertical_axis=context.right_v.normalized_label if context.right_v else None,
        horizontal_interval=context.horizontal_interval, vertical_interval=context.vertical_interval,
        bay_id=context.bay_id, inside_grid_bounds=context.inside,
        inside_valid_bay=context.complete_bay, grid_system_id=_grid_id(context.grid),
        grid_confidence=context.grid.confidence, localization_confidence=score,
        coordinate_system=context.grid.page_geometry.coordinate_system,
        ambiguous=ambiguous, evidence=evidence, warnings=list(dict.fromkeys(warnings)),
    )


def _localization_context(
    systems: Sequence[GridSystem], anchor_x: float, anchor_y: float
) -> _LocalizationContext:
    ranked = sorted(
        ((_system_score(grid, anchor_x, anchor_y), grid) for grid in systems),
        key=lambda value: value[0], reverse=True,
    )
    grid = ranked[0][1]
    horizontal = sorted(grid.horizontal_axes, key=lambda axis: axis.coordinate)
    vertical = sorted(grid.vertical_axes, key=lambda axis: axis.coordinate)
    nearest_h = _nearest(horizontal, anchor_y)
    nearest_v = _nearest(vertical, anchor_x)
    on_h = nearest_h is not None and abs(anchor_y - nearest_h.coordinate) <= ON_AXIS_TOLERANCE
    on_v = nearest_v is not None and abs(anchor_x - nearest_v.coordinate) <= ON_AXIS_TOLERANCE
    lower_h, upper_h = _surrounding(horizontal, anchor_y, on_h)
    left_v, right_v = _surrounding(vertical, anchor_x, on_v)
    return _LocalizationContext(
        grid, anchor_x, anchor_y, nearest_h, nearest_v, lower_h, upper_h, left_v,
        right_v, _interval(lower_h, upper_h, nearest_h if on_h else None),
        _interval(left_v, right_v, nearest_v if on_v else None),
        _inside_grid_extents(grid, anchor_x, anchor_y), on_h, on_v,
        len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.12,
    )


def _explain_localization(
    context: _LocalizationContext,
    systems: Sequence[GridSystem],
    label_ambiguity: bool,
) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []
    evidence = ["ANCHOR_BOUNDING_BOX_CENTER", f"GRID_CONFIDENCE:{context.grid.confidence:.3f}"]
    if context.inside:
        evidence.append("INSIDE_DOMINANT_GRID_EXTENTS")
    else:
        warnings.append("OUTSIDE_GRID_BOUNDS")
    if not context.on_h and (context.lower_h is None or context.upper_h is None):
        warnings.append("NO_SURROUNDING_HORIZONTAL_AXES")
    if not context.on_v and (context.left_v is None or context.right_v is None):
        warnings.append("NO_SURROUNDING_VERTICAL_AXES")
    if context.on_h:
        warnings.append("ON_HORIZONTAL_AXIS")
        evidence.append(f"ON_AXIS_TOLERANCE:{ON_AXIS_TOLERANCE:.1f}")
    if context.on_v:
        warnings.append("ON_VERTICAL_AXIS")
        evidence.append(f"ON_AXIS_TOLERANCE:{ON_AXIS_TOLERANCE:.1f}")
    if _near_intersection(context):
        warnings.append("NEAR_GRID_INTERSECTION")
    if context.grid.confidence < LOW_GRID_CONFIDENCE:
        warnings.append("LOW_GRID_CONFIDENCE")
    if label_ambiguity:
        warnings.append("GRID_LABEL_AMBIGUITY")
    if len(systems) > 1 or "MULTIPLE_SIMILAR_GRID_SYSTEMS" in context.grid.warnings:
        warnings.append("MULTIPLE_GRID_SYSTEMS")
    if context.ambiguous_system:
        warnings.append("AMBIGUOUS_GRID_SYSTEM")
    return warnings, evidence


def _near_intersection(context: _LocalizationContext) -> bool:
    return bool(
        context.nearest_h
        and context.nearest_v
        and abs(context.anchor_y - context.nearest_h.coordinate) <= NEAR_INTERSECTION_TOLERANCE
        and abs(context.anchor_x - context.nearest_v.coordinate) <= NEAR_INTERSECTION_TOLERANCE
    )


def _empty_localization(detection: ClassifiedSectionDetection, x: float, y: float) -> GridRelativeSectionDetection:
    return GridRelativeSectionDetection(
        detection.pdf_page, detection.sheet_number, detection.sheet_title, detection.sheet_subject,
        detection.level, detection.segment, detection.original_text, detection.normalized_section,
        detection.section_family, detection.raw_x, detection.raw_y, detection.raw_width,
        detection.raw_height, x, y, None, None, None, None, None, None, None, None,
        None, None, None, False, False, None, 0.0, 0.0, "UNAVAILABLE", True,
        ["ANCHOR_BOUNDING_BOX_CENTER"], ["NO_GRID_SYSTEM", "LOW_LOCALIZATION_CONFIDENCE"],
    )


def _grid_list(grids: Union[GridSystem, Sequence[GridSystem], None]) -> List[GridSystem]:
    if grids is None:
        return []
    return [grids] if isinstance(grids, GridSystem) else list(grids)


def _nearest(axes: Sequence[GridAxis], coordinate: float) -> Optional[GridAxis]:
    return min(axes, key=lambda axis: abs(coordinate - axis.coordinate)) if axes else None


def _surrounding(axes: Sequence[GridAxis], coordinate: float, on_axis: bool) -> Tuple[Optional[GridAxis], Optional[GridAxis]]:
    if on_axis:
        return None, None
    lower = [axis for axis in axes if axis.coordinate < coordinate]
    upper = [axis for axis in axes if axis.coordinate > coordinate]
    return (lower[-1] if lower else None, upper[0] if upper else None)


def _interval(lower: Optional[GridAxis], upper: Optional[GridAxis], on: Optional[GridAxis]) -> Optional[str]:
    if on:
        return f"ON {on.normalized_label}"
    if lower and upper:
        return f"{lower.normalized_label}\u2013{upper.normalized_label}"
    return None


def _inside_grid_extents(grid: GridSystem, x: float, y: float) -> bool:
    if not grid.horizontal_axes or not grid.vertical_axes:
        return False
    x_low, x_high = min(axis.coordinate for axis in grid.vertical_axes), max(axis.coordinate for axis in grid.vertical_axes)
    y_low, y_high = min(axis.coordinate for axis in grid.horizontal_axes), max(axis.coordinate for axis in grid.horizontal_axes)
    within_region = x_low <= x <= x_high and y_low <= y <= y_high
    horizontal_extent = any(axis.start_x <= x <= axis.end_x for axis in grid.horizontal_axes)
    vertical_extent = any(axis.start_y <= y <= axis.end_y for axis in grid.vertical_axes)
    return within_region and horizontal_extent and vertical_extent


def _system_score(grid: GridSystem, x: float, y: float) -> float:
    return grid.confidence + (0.25 if _inside_grid_extents(grid, x, y) else 0.0)


def _grid_id(grid: GridSystem) -> str:
    return f"PAGE_{grid.pdf_page}_DOMINANT_GRID"


def _ambiguous_label(label: str) -> bool:
    return bool(label) and label.startswith("0") and label[1:].isdigit()


def _confidence(context: _LocalizationContext, ambiguous_label: bool) -> float:
    score = (
        0.35 * context.grid.confidence
        + 0.15 * bool(context.grid.horizontal_axes)
        + 0.15 * bool(context.grid.vertical_axes)
        + 0.10 * context.inside
        + 0.15 * context.complete_bay
        + 0.05 * context.on_h
        + 0.05 * context.on_v
    )
    if context.ambiguous_system:
        score -= 0.20
    if ambiguous_label:
        score -= 0.08
    return round(max(0.0, min(0.98, score)), 3)
