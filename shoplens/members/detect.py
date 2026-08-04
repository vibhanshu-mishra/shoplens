"""Deterministic extraction of conservative member-line candidates."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from shoplens.classification.models import ClassifiedSheet
from shoplens.geometry.models import LineSegment, PageGeometry
from shoplens.grids.models import GridAxis, GridOrientation, GridSystem
from shoplens.localization import locate_point_to_grid

from .models import (
    LineOrientation,
    MemberCandidateType,
    MemberLineCandidate,
    MemberLineCandidateResult,
    RejectedMemberLine,
)

ANGLE_TOLERANCE = 3.0
ENDPOINT_TOLERANCE = 0.25
STYLE_WIDTH_TOLERANCE = 0.05
CHAIN_LINE_TOLERANCE = 2.0
CHAIN_GAP_TOLERANCE = 6.0
GRID_COINCIDENCE_TOLERANCE = 2.0
GRID_ENDPOINT_TOLERANCE = 12.0
PLAN_MARGIN_RATIO = 0.03
MIN_ABSOLUTE_LENGTH = 15.0
MIN_CANDIDATE_CONFIDENCE = 0.58


@dataclass
class _SegmentRecord:
    segment: LineSegment
    start: Tuple[float, float]
    end: Tuple[float, float]
    angle: float
    orientation: LineOrientation
    duplicates: int = 1


@dataclass
class _SpatialEvidence:
    segment_cells: DefaultDict[Tuple[int, int], List[_SegmentRecord]]
    text_cells: DefaultDict[Tuple[int, int], List[Any]]


@dataclass(frozen=True)
class _DetectionContext:
    density: Counter
    spatial: _SpatialEvidence
    geometry: PageGeometry
    grid: GridSystem
    plan_bounds: Tuple[float, float, float, float]
    plan_margin: float


def detect_member_line_candidates(
    source_file: str,
    geometry: PageGeometry,
    grid: GridSystem,
    text_items: Iterable[Any] = (),
    sheet: Optional[ClassifiedSheet] = None,
) -> MemberLineCandidateResult:
    """Return explainable non-grid line candidates inside one dominant plan region."""

    raw = list(geometry.lines)
    plan_bounds = _plan_bounds(grid)
    margin = min(geometry.width, geometry.height) * PLAN_MARGIN_RATIO
    deduplicated, duplicate_rejections = _deduplicate(raw)
    context = _DetectionContext(
        _density_index(deduplicated), _spatial_evidence(deduplicated, text_items),
        geometry, grid, plan_bounds, margin,
    )
    eligible, primitive_rejections = _screen_records(deduplicated, context)

    chains = _merge_collinear(eligible, grid)
    candidates: List[MemberLineCandidate] = []
    chain_rejections: List[RejectedMemberLine] = []
    for chain in chains:
        candidate, reason = _candidate(len(candidates) + 1, chain, grid, sheet)
        if candidate is None:
            chain_rejections.append(
                _rejected_chain(chain, reason or "LOW_CANDIDATE_CONFIDENCE", grid)
            )
        else:
            candidates.append(candidate)
    rejected = list(duplicate_rejections) + primitive_rejections + chain_rejections
    warnings = list(geometry.warnings)
    if not candidates:
        warnings.append("NO_MEMBER_LINE_CANDIDATES")
    return MemberLineCandidateResult(
        source_file=source_file,
        pdf_page=geometry.pdf_page,
        sheet_number=sheet.sheet_number if sheet else grid.sheet_number,
        sheet_title=(sheet.actual_title or sheet.declared_title) if sheet else grid.sheet_title,
        sheet_subject=sheet.subject if sheet else grid.sheet_subject,
        level=sheet.level if sheet else grid.level,
        segment=sheet.segment if sheet else grid.segment,
        page_geometry=geometry,
        grid_system=grid,
        plan_region_bounds=plan_bounds,
        plan_region_margin=margin,
        raw_segment_count=len(raw),
        duplicate_segment_count=len(duplicate_rejections),
        deduplicated_segment_count=len(deduplicated),
        primitive_segments_rejected_count=len(primitive_rejections),
        primitive_segments_entering_merge_count=len(eligible),
        merged_chain_count=len(chains),
        accepted_candidate_count=len(candidates),
        rejected_chain_count=len(chain_rejections),
        rejected_candidate_count=len(rejected),
        candidates=candidates,
        rejected_candidates=rejected,
        warnings=list(dict.fromkeys(warnings)),
    )


def _screen_records(
    records: Sequence[_SegmentRecord], context: _DetectionContext
) -> Tuple[List[_SegmentRecord], List[RejectedMemberLine]]:
    eligible = []
    rejected = []
    for record in records:
        reason, evidence = _primitive_rejection(record, context)
        if reason:
            rejected.append(_rejected(record, reason, evidence, context.grid))
        else:
            eligible.append(record)
    return eligible, rejected


def filter_member_candidates(
    candidates: Iterable[MemberLineCandidate],
    orientation: Optional[LineOrientation] = None,
    inside_only: bool = False,
    min_confidence: float = 0.0,
    candidate_id: Optional[str] = None,
) -> List[MemberLineCandidate]:
    normalized_id = candidate_id.strip().upper() if candidate_id else None
    return [
        item
        for item in candidates
        if (orientation is None or item.orientation_class == orientation)
        and (not inside_only or item.inside_dominant_grid)
        and item.confidence >= min_confidence
        and (normalized_id is None or item.candidate_id == normalized_id)
    ]


def _deduplicate(
    segments: Sequence[LineSegment],
) -> Tuple[List[_SegmentRecord], List[RejectedMemberLine]]:
    records: List[_SegmentRecord] = []
    rejected: List[RejectedMemberLine] = []
    by_key: Dict[Tuple[Any, ...], _SegmentRecord] = {}
    for segment in segments:
        record = _record(segment)
        key = _duplicate_key(record)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = record
            records.append(record)
        else:
            existing.duplicates += 1
            rejected.append(_rejected(record, "DUPLICATE_GEOMETRY", ["REVERSED_ENDPOINT_SAFE_KEY"], None))
    return records, rejected


def _record(segment: LineSegment) -> _SegmentRecord:
    original = ((segment.x1, segment.y1), (segment.x2, segment.y2))
    start, end = sorted(original)
    dx, dy = end[0] - start[0], end[1] - start[1]
    angle = math.degrees(math.atan2(dy, dx)) if dx or dy else 0.0
    return _SegmentRecord(segment, start, end, angle, _orientation(angle, segment.length))


def _duplicate_key(record: _SegmentRecord) -> Tuple[Any, ...]:
    width = None if record.segment.width is None else round(record.segment.width / STYLE_WIDTH_TOLERANCE)
    return (
        round(record.start[0] / ENDPOINT_TOLERANCE),
        round(record.start[1] / ENDPOINT_TOLERANCE),
        round(record.end[0] / ENDPOINT_TOLERANCE),
        round(record.end[1] / ENDPOINT_TOLERANCE),
        width,
        tuple(round(value, 3) for value in record.segment.dash),
        record.segment.source,
    )


def _orientation(angle: float, length: float) -> LineOrientation:
    if length <= ENDPOINT_TOLERANCE:
        return LineOrientation.OTHER
    absolute = abs(angle)
    if absolute <= ANGLE_TOLERANCE or abs(absolute - 180.0) <= ANGLE_TOLERANCE:
        return LineOrientation.HORIZONTAL
    if abs(absolute - 90.0) <= ANGLE_TOLERANCE:
        return LineOrientation.VERTICAL
    if 10.0 <= absolute <= 170.0:
        return LineOrientation.DIAGONAL
    return LineOrientation.OTHER


def _primitive_rejection(
    record: _SegmentRecord,
    context: _DetectionContext,
) -> Tuple[Optional[str], List[str]]:
    if record.segment.length <= ENDPOINT_TOLERANCE:
        return "ZERO_LENGTH", ["LENGTH_BELOW_ENDPOINT_TOLERANCE"]
    if _page_border(record, context.geometry):
        return "PAGE_BORDER", ["COINCIDES_WITH_PAGE_BOUNDARY"]
    if _grid_geometry(record, context.grid):
        return "GRID_AXIS_GEOMETRY", ["COINCIDES_WITH_ACCEPTED_GRID_AXIS"]
    endpoints_inside = _inside(context.plan_bounds, record.start, context.plan_margin) and _inside(
        context.plan_bounds, record.end, context.plan_margin
    )
    if not endpoints_inside:
        return _outside_reason(record, context.density, context.geometry), ["OUTSIDE_DOMINANT_PLAN_REGION"]
    if _likely_dimension(record, context.spatial):
        return "LIKELY_DIMENSION_LINE", ["PERPENDICULAR_TICKS_AT_LINE_ENDS"]
    if _likely_leader(record, context.spatial):
        return "LIKELY_LEADER", ["SHORT_ANGLED_CHAIN_TERMINATES_NEAR_TEXT"]
    if record.segment.length < MIN_ABSOLUTE_LENGTH:
        return "TOO_SHORT", [f"MINIMUM_LENGTH:{MIN_ABSOLUTE_LENGTH:.1f}"]
    return None, []


def _outside_reason(record: _SegmentRecord, density: Counter, geometry: PageGeometry) -> str:
    x, y = _midpoint(record)
    x0, y0, x1, y1 = geometry.crop_box
    if x >= x0 + 0.72 * (x1 - x0) and y <= y0 + 0.28 * (y1 - y0):
        return "TITLE_BLOCK_GEOMETRY"
    if density[_density_cell(x, y)] >= 18:
        return "SCHEDULE_GEOMETRY"
    if record.segment.length >= min(geometry.width, geometry.height) * 0.12:
        return "DETAIL_BORDER"
    return "OUTSIDE_PLAN_REGION"


def _page_border(record: _SegmentRecord, geometry: PageGeometry) -> bool:
    x0, y0, x1, y1 = geometry.crop_box
    length_threshold = 0.75 * (geometry.width if record.orientation == LineOrientation.HORIZONTAL else geometry.height)
    if record.segment.length < length_threshold:
        return False
    if record.orientation == LineOrientation.HORIZONTAL:
        return min(abs(record.start[1] - y0), abs(record.start[1] - y1)) <= 2.0
    if record.orientation == LineOrientation.VERTICAL:
        return min(abs(record.start[0] - x0), abs(record.start[0] - x1)) <= 2.0
    return False


def _grid_geometry(record: _SegmentRecord, grid: GridSystem) -> bool:
    axes = grid.horizontal_axes if record.orientation == LineOrientation.HORIZONTAL else grid.vertical_axes
    if record.orientation not in (LineOrientation.HORIZONTAL, LineOrientation.VERTICAL):
        return False
    for axis in axes:
        coordinate = record.start[1] if record.orientation == LineOrientation.HORIZONTAL else record.start[0]
        if abs(coordinate - axis.coordinate) > GRID_COINCIDENCE_TOLERANCE:
            continue
        low, high = _projection(record)
        axis_low, axis_high = (
            sorted((axis.start_x, axis.end_x))
            if record.orientation == LineOrientation.HORIZONTAL
            else sorted((axis.start_y, axis.end_y))
        )
        overlap = max(0.0, min(high, axis_high) - max(low, axis_low))
        if overlap >= min(record.segment.length, axis_high - axis_low) * 0.65:
            return True
    return False


def _likely_dimension(record: _SegmentRecord, spatial: _SpatialEvidence) -> bool:
    if record.orientation not in (LineOrientation.HORIZONTAL, LineOrientation.VERTICAL):
        return False
    endpoints = (record.start, record.end)
    return all(
        any(
            other.segment.length <= 18.0
            and _perpendicular(record.orientation, other.orientation)
            and _point_segment_distance(endpoint, other) <= 3.0
            for other in _nearby_segments(spatial, endpoint)
            if other is not record
        )
        for endpoint in endpoints
    )


def _likely_leader(
    record: _SegmentRecord, spatial: _SpatialEvidence
) -> bool:
    if record.orientation != LineOrientation.DIAGONAL or record.segment.length > 80.0:
        return False
    connected = any(
        other is not record
        and other.segment.length <= 80.0
        and min(_distance(record.start, other.start), _distance(record.start, other.end), _distance(record.end, other.start), _distance(record.end, other.end)) <= 3.0
        for other in _nearby_segments(spatial, record.start) + _nearby_segments(spatial, record.end)
    )
    near_text = any(
        min(
            _distance_to_box(record.start, item),
            _distance_to_box(record.end, item),
        ) <= 12.0
        for item in _nearby_text(spatial, record.start) + _nearby_text(spatial, record.end)
    )
    return connected and near_text


def _merge_collinear(records: Sequence[_SegmentRecord], grid: GridSystem) -> List[List[_SegmentRecord]]:
    groups: DefaultDict[Tuple[Any, ...], List[_SegmentRecord]] = defaultdict(list)
    for record in records:
        groups[_chain_key(record)].append(record)
    chains: List[List[_SegmentRecord]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: _projection(item)[0])
        current: List[_SegmentRecord] = []
        current_high = 0.0
        for record in ordered:
            low, high = _projection(record)
            gap = low - current_high if current else 0.0
            if current and (gap > CHAIN_GAP_TOLERANCE or _split_at_grid(current[-1], record, gap, grid)):
                chains.append(current)
                current = []
            current.append(record)
            current_high = max(current_high, high) if len(current) > 1 else high
        if current:
            chains.append(current)
    return chains


def _chain_key(record: _SegmentRecord) -> Tuple[Any, ...]:
    width = None if record.segment.width is None else round(record.segment.width / STYLE_WIDTH_TOLERANCE)
    angle = round(record.angle / ANGLE_TOLERANCE)
    if record.orientation == LineOrientation.HORIZONTAL:
        offset = record.start[1]
    elif record.orientation == LineOrientation.VERTICAL:
        offset = record.start[0]
    else:
        radians = math.radians(record.angle)
        offset = -math.sin(radians) * record.start[0] + math.cos(radians) * record.start[1]
    return (
        record.orientation,
        angle,
        round(offset / CHAIN_LINE_TOLERANCE),
        width,
        tuple(round(value, 3) for value in record.segment.dash),
        record.segment.source,
    )


def _split_at_grid(left: _SegmentRecord, right: _SegmentRecord, gap: float, grid: GridSystem) -> bool:
    if gap <= ENDPOINT_TOLERANCE:
        return False
    join = ((left.end[0] + right.start[0]) / 2.0, (left.end[1] + right.start[1]) / 2.0)
    if left.orientation == LineOrientation.HORIZONTAL:
        return any(abs(join[0] - axis.coordinate) <= GRID_ENDPOINT_TOLERANCE for axis in grid.vertical_axes)
    if left.orientation == LineOrientation.VERTICAL:
        return any(abs(join[1] - axis.coordinate) <= GRID_ENDPOINT_TOLERANCE for axis in grid.horizontal_axes)
    return any(abs(join[0] - axis.coordinate) <= GRID_ENDPOINT_TOLERANCE for axis in grid.vertical_axes) or any(
        abs(join[1] - axis.coordinate) <= GRID_ENDPOINT_TOLERANCE for axis in grid.horizontal_axes
    )


def _candidate(
    index: int,
    chain: Sequence[_SegmentRecord],
    grid: GridSystem,
    sheet: Optional[ClassifiedSheet],
) -> Tuple[Optional[MemberLineCandidate], Optional[str]]:
    first, last = chain[0], chain[-1]
    start, end = first.start, last.end
    length = _distance(start, end)
    orientation = first.orientation
    start_location = locate_point_to_grid(grid, *start)
    end_location = locate_point_to_grid(grid, *end)
    crossed_h = _crossed_axes(start, end, grid.horizontal_axes, GridOrientation.HORIZONTAL)
    crossed_v = _crossed_axes(start, end, grid.vertical_axes, GridOrientation.VERTICAL)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    inside = locate_point_to_grid(grid, *midpoint).inside_grid_bounds
    score, evidence = _candidate_score(
        length, orientation, inside, start_location, end_location,
        crossed_h, crossed_v, len(chain), grid,
    )
    if score < MIN_CANDIDATE_CONFIDENCE:
        return None, "LOW_CANDIDATE_CONFIDENCE"
    return _build_candidate(
        index, chain, grid, sheet, start, end, length, orientation,
        start_location, end_location, crossed_h, crossed_v, inside, score, evidence,
    ), None


def _candidate_score(
    length: float,
    orientation: LineOrientation,
    inside: bool,
    start_location: Any,
    end_location: Any,
    crossed_h: Sequence[str],
    crossed_v: Sequence[str],
    source_count: int,
    grid: GridSystem,
) -> Tuple[float, List[str]]:
    evidence = ["INSIDE_DOMINANT_PLAN_REGION", f"NORMALIZED_ORIENTATION:{orientation.value}"]
    score = 0.30
    near_grid = _near_grid(start_location) or _near_grid(end_location)
    if inside:
        evidence.append("MIDPOINT_INSIDE_DOMINANT_GRID")
        score += 0.20
    page_span = min(grid.page_geometry.width, grid.page_geometry.height)
    if length >= page_span * 0.04:
        evidence.append("SUBSTANTIAL_PAGE_RELATIVE_LENGTH")
        score += 0.18
    elif length >= MIN_ABSOLUTE_LENGTH * 2:
        score += 0.08
    if near_grid:
        evidence.append("ENDPOINT_NEAR_ACCEPTED_GRID")
        score += 0.10
    crossings = len(crossed_h) + len(crossed_v)
    if crossings:
        evidence.append(f"CROSSES_ACCEPTED_GRIDS:{crossings}")
        score += min(0.16, crossings * 0.04)
    if source_count > 1:
        evidence.append(f"COLLINEAR_CHAIN:{source_count}")
        score += 0.08
    if orientation == LineOrientation.DIAGONAL:
        evidence.append("PLAUSIBLE_DIAGONAL_ORIENTATION")
        score += 0.06
    return round(min(0.98, score), 3), evidence


def _build_candidate(
    index: int,
    chain: Sequence[_SegmentRecord],
    grid: GridSystem,
    sheet: Optional[ClassifiedSheet],
    start: Tuple[float, float],
    end: Tuple[float, float],
    length: float,
    orientation: LineOrientation,
    start_location: Any,
    end_location: Any,
    crossed_h: List[str],
    crossed_v: List[str],
    inside: bool,
    score: float,
    evidence: List[str],
) -> MemberLineCandidate:
    first, last = chain[0], chain[-1]
    start_near_grid = _near_grid(start_location)
    end_near_grid = _near_grid(end_location)
    near_grid = start_near_grid or end_near_grid
    crossings = len(crossed_h) + len(crossed_v)
    source_segments = [item.segment for item in chain]
    candidate_type = _candidate_type(orientation, length)
    return MemberLineCandidate(
        candidate_id=f"MLC-{index:04d}",
        pdf_page=grid.pdf_page,
        sheet_number=sheet.sheet_number if sheet else grid.sheet_number,
        sheet_title=(sheet.actual_title or sheet.declared_title) if sheet else grid.sheet_title,
        sheet_subject=sheet.subject if sheet else grid.sheet_subject,
        level=sheet.level if sheet else grid.level,
        segment=sheet.segment if sheet else grid.segment,
        grid_system_id=f"PAGE_{grid.pdf_page}_DOMINANT_GRID",
        original_start_x=first.segment.x1,
        original_start_y=first.segment.y1,
        original_end_x=last.segment.x2,
        original_end_y=last.segment.y2,
        start_x=start[0], start_y=start[1], end_x=end[0], end_y=end[1],
        length=length, orientation_angle=first.angle, orientation_class=orientation,
        source_segments=source_segments, source_segment_count=len(source_segments),
        duplicate_count=sum(item.duplicates for item in chain),
        line_width=first.segment.width, dash_pattern=first.segment.dash,
        geometry_source=first.segment.source,
        nearest_start_horizontal_grid=start_location.nearest_horizontal_axis,
        nearest_start_vertical_grid=start_location.nearest_vertical_axis,
        nearest_end_horizontal_grid=end_location.nearest_horizontal_axis,
        nearest_end_vertical_grid=end_location.nearest_vertical_axis,
        start_grid_location=start_location.display, end_grid_location=end_location.display,
        crossed_horizontal_grids=crossed_h, crossed_vertical_grids=crossed_v,
        intersection_count=crossings, inside_dominant_grid=inside,
        start_near_grid=start_near_grid, end_near_grid=end_near_grid,
        near_grid_aligned=near_grid, candidate_type=candidate_type,
        confidence=score, evidence=evidence,
    )


def _candidate_type(orientation: LineOrientation, length: float) -> MemberCandidateType:
    if length < 60.0:
        return MemberCandidateType.SHORT_MEMBER_CANDIDATE
    if orientation == LineOrientation.DIAGONAL:
        return MemberCandidateType.DIAGONAL_MEMBER_CANDIDATE
    if orientation in (LineOrientation.HORIZONTAL, LineOrientation.VERTICAL):
        return MemberCandidateType.LINEAR_MEMBER_CANDIDATE
    return MemberCandidateType.UNKNOWN_LINEAR_CANDIDATE


def _crossed_axes(
    start: Tuple[float, float], end: Tuple[float, float], axes: Sequence[GridAxis], orientation: GridOrientation
) -> List[str]:
    crossed = []
    for axis in axes:
        if orientation == GridOrientation.VERTICAL:
            if not min(start[0], end[0]) <= axis.coordinate <= max(start[0], end[0]) or start[0] == end[0]:
                continue
            ratio = (axis.coordinate - start[0]) / (end[0] - start[0])
            other = start[1] + ratio * (end[1] - start[1])
            within = min(axis.start_y, axis.end_y) <= other <= max(axis.start_y, axis.end_y)
        else:
            if not min(start[1], end[1]) <= axis.coordinate <= max(start[1], end[1]) or start[1] == end[1]:
                continue
            ratio = (axis.coordinate - start[1]) / (end[1] - start[1])
            other = start[0] + ratio * (end[0] - start[0])
            within = min(axis.start_x, axis.end_x) <= other <= max(axis.start_x, axis.end_x)
        if within:
            crossed.append(axis.normalized_label)
    return crossed


def _plan_bounds(grid: GridSystem) -> Tuple[float, float, float, float]:
    if not grid.horizontal_axes or not grid.vertical_axes:
        return grid.page_geometry.crop_box
    return (
        min(axis.coordinate for axis in grid.vertical_axes),
        min(axis.coordinate for axis in grid.horizontal_axes),
        max(axis.coordinate for axis in grid.vertical_axes),
        max(axis.coordinate for axis in grid.horizontal_axes),
    )


def _inside(bounds: Tuple[float, float, float, float], point: Tuple[float, float], margin: float = 0.0) -> bool:
    return bounds[0] - margin <= point[0] <= bounds[2] + margin and bounds[1] - margin <= point[1] <= bounds[3] + margin


def _density_index(records: Sequence[_SegmentRecord]) -> Counter:
    return Counter(_density_cell(*_midpoint(record)) for record in records)


def _spatial_evidence(
    records: Sequence[_SegmentRecord], text_items: Iterable[Any]
) -> _SpatialEvidence:
    segment_cells: DefaultDict[Tuple[int, int], List[_SegmentRecord]] = defaultdict(list)
    text_cells: DefaultDict[Tuple[int, int], List[Any]] = defaultdict(list)
    for record in records:
        for point in (record.start, record.end):
            segment_cells[_neighbor_cell(point)].append(record)
    for item in text_items:
        center = (
            float(item.x) + float(item.width) / 2.0,
            float(item.y) + float(item.height) / 2.0,
        )
        text_cells[_neighbor_cell(center)].append(item)
    return _SpatialEvidence(segment_cells, text_cells)


def _nearby_segments(spatial: _SpatialEvidence, point: Tuple[float, float]) -> List[_SegmentRecord]:
    return [item for cell in _neighbor_cells(point) for item in spatial.segment_cells.get(cell, [])]


def _nearby_text(spatial: _SpatialEvidence, point: Tuple[float, float]) -> List[Any]:
    return [item for cell in _neighbor_cells(point) for item in spatial.text_cells.get(cell, [])]


def _neighbor_cell(point: Tuple[float, float]) -> Tuple[int, int]:
    return math.floor(point[0] / 24.0), math.floor(point[1] / 24.0)


def _neighbor_cells(point: Tuple[float, float]) -> List[Tuple[int, int]]:
    x, y = _neighbor_cell(point)
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def _density_cell(x: float, y: float) -> Tuple[int, int]:
    return round(x / 80.0), round(y / 80.0)


def _projection(record: _SegmentRecord) -> Tuple[float, float]:
    if record.orientation == LineOrientation.VERTICAL:
        return tuple(sorted((record.start[1], record.end[1])))
    if record.orientation == LineOrientation.HORIZONTAL:
        return tuple(sorted((record.start[0], record.end[0])))
    radians = math.radians(record.angle)
    values = [point[0] * math.cos(radians) + point[1] * math.sin(radians) for point in (record.start, record.end)]
    return min(values), max(values)


def _midpoint(record: _SegmentRecord) -> Tuple[float, float]:
    return (record.start[0] + record.end[0]) / 2.0, (record.start[1] + record.end[1]) / 2.0


def _near_grid(location: Any) -> bool:
    distances = [location.nearest_horizontal_distance, location.nearest_vertical_distance]
    return any(value is not None and abs(value) <= GRID_ENDPOINT_TOLERANCE for value in distances)


def _perpendicular(first: LineOrientation, second: LineOrientation) -> bool:
    return {first, second} == {LineOrientation.HORIZONTAL, LineOrientation.VERTICAL}


def _point_segment_distance(point: Tuple[float, float], record: _SegmentRecord) -> float:
    x, y = point
    x1, y1 = record.start
    x2, y2 = record.end
    dx, dy = x2 - x1, y2 - y1
    if not dx and not dy:
        return _distance(point, record.start)
    ratio = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return _distance(point, (x1 + ratio * dx, y1 + ratio * dy))


def _distance_to_box(point: Tuple[float, float], item: Any) -> float:
    x0, y0 = float(item.x), float(item.y)
    x1, y1 = x0 + float(item.width), y0 + float(item.height)
    dx = max(x0 - point[0], 0.0, point[0] - x1)
    dy = max(y0 - point[1], 0.0, point[1] - y1)
    return math.hypot(dx, dy)


def _distance(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _rejected(
    record: _SegmentRecord,
    reason: str,
    evidence: List[str],
    grid: Optional[GridSystem],
) -> RejectedMemberLine:
    nearest = []
    if grid:
        location = locate_point_to_grid(grid, *_midpoint(record))
        nearest = [value for value in (location.nearest_horizontal_axis, location.nearest_vertical_axis) if value]
    return RejectedMemberLine(
        record.segment.page, record.segment.x1, record.segment.y1,
        record.segment.x2, record.segment.y2, record.segment.length,
        record.orientation, record.segment.width, record.segment.dash,
        record.segment.source, reason, evidence, nearest,
    )


def _rejected_chain(chain: Sequence[_SegmentRecord], reason: str, grid: GridSystem) -> RejectedMemberLine:
    first, last = chain[0], chain[-1]
    combined = LineSegment(
        first.segment.page, first.start[0], first.start[1], last.end[0], last.end[1],
        first.segment.width, first.segment.dash, first.segment.source,
    )
    return _rejected(_record(combined), reason, [f"SOURCE_SEGMENTS:{len(chain)}"], grid)
