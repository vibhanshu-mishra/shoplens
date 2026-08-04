"""Deterministic clustering of accepted member-line candidates.

The implementation uses orientation buckets, spatial bins, and sorted projections;
it avoids a sheet-wide all-pairs comparison. Confidence is explainable rule strength,
not probability.
"""

import hashlib
import math
import statistics
import time
from collections import Counter, defaultdict
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from shoplens.members.models import LineOrientation, MemberLineCandidate, MemberLineCandidateResult

from .models import LinearPattern, LinearPatternMember, LinearPatternType, PatternResult

ANGLE_TOLERANCE = 4.0
MIN_GROUP_SIZE = 3
SPATIAL_BIN_SIZE = 180.0


def _angle_distance(a: float, b: float) -> float:
    return abs((a - b + 90.0) % 180.0 - 90.0)


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _variation(values: Sequence[float], center: Optional[float] = None) -> float:
    if not values:
        return 0.0
    center = _median(values) if center is None else center
    return _median([abs(value - center) for value in values]) / max(abs(center), 1e-9)


def _bbox(candidate: MemberLineCandidate) -> Tuple[float, float, float, float]:
    return (
        min(candidate.start_x, candidate.end_x), min(candidate.start_y, candidate.end_y),
        max(candidate.start_x, candidate.end_x), max(candidate.start_y, candidate.end_y),
    )


def _center(candidate: MemberLineCandidate) -> Tuple[float, float]:
    return ((candidate.start_x + candidate.end_x) / 2, (candidate.start_y + candidate.end_y) / 2)


def _compatible(a: MemberLineCandidate, b: MemberLineCandidate) -> bool:
    if _angle_distance(a.orientation_angle, b.orientation_angle) > ANGLE_TOLERANCE:
        return False
    if a.dash_pattern != b.dash_pattern or a.geometry_source != b.geometry_source:
        return False
    if a.line_width is not None and b.line_width is not None:
        if abs(a.line_width - b.line_width) > 0.05:
            return False
    ax, ay = _center(a)
    bx, by = _center(b)
    distance = math.hypot(ax - bx, ay - by)
    if distance > max(SPATIAL_BIN_SIZE * 1.8, 1.5 * max(a.length, b.length)):
        return False
    angle = math.radians((a.orientation_angle + b.orientation_angle) / 2)
    along = abs((ax - bx) * math.cos(angle) + (ay - by) * math.sin(angle))
    across = abs(-(ax - bx) * math.sin(angle) + (ay - by) * math.cos(angle))
    return across <= max(80.0, 0.55 * max(a.length, b.length)) and along <= max(
        180.0, 1.25 * (a.length + b.length)
    )


def _spatial_orientation_groups(
    candidates: Sequence[MemberLineCandidate],
) -> List[List[MemberLineCandidate]]:
    buckets: DefaultDict[Tuple[int, int, int], List[int]] = defaultdict(list)
    parents = list(range(len(candidates)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parents[b] = a

    for index, item in enumerate(candidates):
        x, y = _center(item)
        angle_bucket_count = round(180.0 / ANGLE_TOLERANCE)
        angle_bucket = int(round((item.orientation_angle % 180.0) / ANGLE_TOLERANCE)) % angle_bucket_count
        cell_x, cell_y = int(x // SPATIAL_BIN_SIZE), int(y // SPATIAL_BIN_SIZE)
        for da in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor_angle = (angle_bucket + da) % angle_bucket_count
                    for other in buckets.get((neighbor_angle, cell_x + dx, cell_y + dy), []):
                        if _compatible(item, candidates[other]):
                            union(index, other)
        buckets[(angle_bucket, cell_x, cell_y)].append(index)
    groups: DefaultDict[int, List[MemberLineCandidate]] = defaultdict(list)
    for index, item in enumerate(candidates):
        groups[find(index)].append(item)
    return sorted(groups.values(), key=lambda group: min(item.candidate_id for item in group))


def _ordered_grid_labels(labels: Iterable[str], axes) -> List[str]:
    wanted = set(labels)
    return [axis.normalized_label for axis in axes if axis.normalized_label in wanted]


def _build_pattern(
    pattern_id: str,
    candidates: List[MemberLineCandidate],
    result: MemberLineCandidateResult,
    timing_accumulator: Optional[Dict[str, float]] = None,
) -> LinearPattern:
    angles = [item.orientation_angle % 180.0 for item in candidates]
    doubled_x = sum(math.cos(math.radians(2 * angle)) for angle in angles)
    doubled_y = sum(math.sin(math.radians(2 * angle)) for angle in angles)
    angle = math.degrees(math.atan2(doubled_y, doubled_x)) / 2.0 % 180.0
    radians = math.radians(angle)
    centers = [_center(item) for item in candidates]
    ordered_candidates = sorted(
        candidates,
        key=lambda item: (
            -_center(item)[0] * math.sin(radians) + _center(item)[1] * math.cos(radians),
            item.candidate_id,
        ),
    )
    offsets = [
        -_center(item)[0] * math.sin(radians) + _center(item)[1] * math.cos(radians)
        for item in ordered_candidates
    ]
    spacing = [right - left for left, right in zip(offsets, offsets[1:]) if right - left > 0.25]
    median_spacing = _median(spacing) if spacing else None
    spacing_variation = _variation(spacing, median_spacing) if spacing else None
    regularity = (
        max(0.0, 1.0 - min(1.0, spacing_variation * 2.0))
        if spacing_variation is not None else 0.0
    )
    lengths = [item.length for item in candidates]
    longitudinal = [
        sorted((item.start_x * math.cos(radians) + item.start_y * math.sin(radians),
                item.end_x * math.cos(radians) + item.end_y * math.sin(radians)))
        for item in candidates
    ]
    starts = [value[0] for value in longitudinal]
    ends = [value[1] for value in longitudinal]
    length_variation = _variation(lengths)
    endpoint_score = max(0.0, 1.0 - min(1.0, (_variation(starts) + _variation(ends)) / 2.0))
    pair_separations: List[float] = []
    pair_ids: List[str] = []
    pair_members: List[Tuple[str, str, str]] = []
    paired: Set[str] = set()
    pair_started = time.monotonic()
    if median_spacing:
        close_limit = min(_median(lengths) * 0.08, max(spacing) * 0.45)
        ordered = list(zip(offsets, ordered_candidates))
        for pair_index, ((left_offset, left), (right_offset, right)) in enumerate(zip(ordered, ordered[1:]), 1):
            separation = right_offset - left_offset
            if separation <= max(2.0, close_limit) and left.candidate_id not in paired and right.candidate_id not in paired:
                pair_id = f"{pattern_id}-PAIR-{pair_index:03d}"
                pair_ids.append(pair_id)
                pair_separations.append(separation)
                paired.update((left.candidate_id, right.candidate_id))
                pair_members.append((pair_id, left.candidate_id, right.candidate_id))
    if timing_accumulator is not None:
        timing_accumulator["double_line_pair_analysis_seconds"] = (
            timing_accumulator.get("double_line_pair_analysis_seconds", 0.0)
            + time.monotonic() - pair_started
        )
    missing = 0
    outliers: List[float] = []
    if median_spacing and median_spacing > 0:
        for value in spacing:
            ratio = value / median_spacing
            if ratio >= 1.65:
                slots = max(1, round(ratio) - 1)
                missing += slots
                outliers.append(value)
    if len(candidates) >= 50 and regularity < 0.72:
        pattern_type = LinearPatternType.DENSE_LINEAR_FIELD
    elif len(pair_ids) >= 2:
        pattern_type = LinearPatternType.DOUBLE_LINE_PAIR_GROUP
    elif regularity >= 0.72 and len(spacing) >= 2:
        pattern_type = LinearPatternType.REGULAR_SPACING_FIELD
    elif max(offsets) - min(offsets) <= 3.0:
        pattern_type = LinearPatternType.COLLINEAR_CHAIN_GROUP
    else:
        pattern_type = LinearPatternType.PARALLEL_LINE_GROUP
    boxes = [_bbox(item) for item in candidates]
    bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    area = max((bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), 1.0)
    orientations = Counter(item.orientation_class.value for item in candidates)
    crossed_h = _ordered_grid_labels(
        (label for item in candidates for label in item.crossed_horizontal_grids),
        result.grid_system.horizontal_axes,
    )
    crossed_v = _ordered_grid_labels(
        (label for item in candidates for label in item.crossed_vertical_grids),
        result.grid_system.vertical_axes,
    )
    grid_bays = [f"{h}/{v}" for h in crossed_h for v in crossed_v]
    evidence = ["SPATIALLY_COHERENT", "ORIENTATION_CONSISTENT"]
    if regularity >= 0.72:
        evidence.append("REGULAR_PERPENDICULAR_SPACING")
    if length_variation <= 0.15:
        evidence.append("CONSISTENT_LENGTH")
    if endpoint_score >= 0.75:
        evidence.append("BOTH_ENDPOINT_BANDS_ALIGNED")
    if pair_ids:
        evidence.append("REPEATED_CLOSE_PARALLEL_PAIRS")
    warnings = []
    if len(candidates) < 4:
        warnings.append("SMALL_PATTERN_GROUP")
    if regularity < 0.4:
        warnings.append("IRREGULAR_SPACING")
    if length_variation > 0.35:
        warnings.append("HIGH_LENGTH_VARIATION")
    confidence = min(0.95, 0.35 + min(len(candidates), 12) * 0.025 + regularity * 0.2 + endpoint_score * 0.15)
    if confidence < 0.58:
        warnings.append("LOW_PATTERN_CONFIDENCE")
    pair_by_candidate = {
        candidate_id: pair_id
        for pair_id, left_id, right_id in pair_members
        for candidate_id in (left_id, right_id)
    }
    members = [
        LinearPatternMember(item.candidate_id, pattern_id, [], "PRIMARY", confidence,
                            abs(offsets[index] - _median(offsets)), index,
                            pair_by_candidate.get(item.candidate_id), warnings=[])
        for index, item in enumerate(ordered_candidates)
    ]
    return LinearPattern(
        pattern_id, result.pdf_page, result.sheet_number, result.sheet_title,
        result.sheet_subject, result.level, result.segment, pattern_type,
        Counter(item.orientation_class.value for item in candidates).most_common(1)[0][0], [],
        [item.candidate_id for item in candidates], len(candidates), candidates, members,
        bounds, sum(x for x, _ in centers) / len(centers), sum(y for _, y in centers) / len(centers),
        angle, max(_angle_distance(value, angle) for value in angles), min(lengths), max(lengths),
        statistics.mean(lengths), _median(lengths), length_variation, offsets, spacing,
        statistics.mean(spacing) if spacing else None, median_spacing, spacing_variation,
        regularity, missing, outliers, (min(starts), max(starts)), (min(ends), max(ends)),
        endpoint_score, len(pair_ids), pair_ids, pair_separations,
        statistics.mean(pair_separations) if pair_separations else None,
        _variation(pair_separations) if pair_separations else None,
        [item.candidate_id for item in candidates if item.candidate_id not in paired],
        orientations["HORIZONTAL"], orientations["VERTICAL"], orientations["DIAGONAL"],
        orientations["OTHER"], f"GRID:{result.pdf_page}", grid_bays, crossed_h, crossed_v,
        sum(item.inside_dominant_grid for item in candidates) / len(candidates),
        len(candidates) / area, [], 0, 0, 0, 0, 0.0, None, [], 0, 0.0,
        [], 0, [], None, "NON_BINDING_TEXT_CONTEXT_NOT_EVALUATED",
        confidence, evidence, warnings,
    )


def _bbox_overlap(left: LinearPattern, right: LinearPattern) -> bool:
    return (
        min(left.bounding_box[2], right.bounding_box[2])
        > max(left.bounding_box[0], right.bounding_box[0])
        and min(left.bounding_box[3], right.bounding_box[3])
        > max(left.bounding_box[1], right.bounding_box[1])
    )


def _orthogonal_intersection_count(left: LinearPattern, right: LinearPattern) -> int:
    horizontal = left if left.primary_orientation == "HORIZONTAL" else right
    vertical = right if horizontal is left else left
    count = 0
    for h_item in horizontal.source_candidates:
        hx0, hy0, hx1, hy1 = _bbox(h_item)
        for v_item in vertical.source_candidates:
            vx0, vy0, vx1, vy1 = _bbox(v_item)
            if hx0 <= (vx0 + vx1) / 2 <= hx1 and vy0 <= (hy0 + hy1) / 2 <= vy1:
                count += 1
    return count


def _network_signature(patterns: Sequence[LinearPattern], bounds) -> str:
    grid_labels = sorted({
        label for pattern in patterns
        for label in pattern.crossed_horizontal_grids + pattern.crossed_vertical_grids
    })
    material = "|".join([
        ",".join(sorted(pattern.pattern_id for pattern in patterns)),
        ",".join(f"{value:.2f}" for value in bounds),
        ",".join(grid_labels),
        ",".join(sorted({candidate_id for pattern in patterns for candidate_id in pattern.candidate_ids})),
    ])
    return "NET-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper()


def _hierarchical_patterns(
    primary: List[LinearPattern], result: MemberLineCandidateResult, start: int
) -> Tuple[List[LinearPattern], int]:
    """Return one maximal orthogonal network per connected spatial component."""
    eligible = [
        pattern for pattern in primary
        if pattern.primary_orientation in {"HORIZONTAL", "VERTICAL"}
    ]
    parents = list(range(len(eligible)))
    edge_intersections: Dict[Tuple[int, int], int] = {}
    prior_pairwise_network_count = 0

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parents[b] = a

    for left_index, left in enumerate(eligible):
        for right_index in range(left_index + 1, len(eligible)):
            right = eligible[right_index]
            if left.primary_orientation == right.primary_orientation or not _bbox_overlap(left, right):
                continue
            prior_pairwise_network_count += 1
            intersections = _orthogonal_intersection_count(left, right)
            if intersections <= 0:
                continue
            edge_intersections[(left_index, right_index)] = intersections
            union(left_index, right_index)

    components: DefaultDict[int, List[int]] = defaultdict(list)
    for index in range(len(eligible)):
        components[find(index)].append(index)
    network_components = [
        indexes for indexes in components.values()
        if {eligible[index].primary_orientation for index in indexes}
        == {"HORIZONTAL", "VERTICAL"}
    ]
    network_components.sort(key=lambda indexes: min(eligible[index].pattern_id for index in indexes))
    hierarchical: List[LinearPattern] = []
    for indexes in network_components:
        components_for_network = [eligible[index] for index in indexes]
        combined_by_id = {
            item.candidate_id: item
            for pattern in components_for_network for item in pattern.source_candidates
        }
        combined = list(combined_by_id.values())
        network = _build_pattern(f"LP-{start + len(hierarchical):04d}", combined, result)
        network.pattern_type = LinearPatternType.ORTHOGONAL_NETWORK
        network.primary_orientation = "MIXED"
        network.secondary_orientations = ["HORIZONTAL", "VERTICAL"]
        network.component_pattern_ids = sorted(pattern.pattern_id for pattern in components_for_network)
        network.unique_primary_candidate_count = len(combined_by_id)
        network.horizontal_component_count = sum(
            pattern.primary_orientation == "HORIZONTAL" for pattern in components_for_network
        )
        network.vertical_component_count = sum(
            pattern.primary_orientation == "VERTICAL" for pattern in components_for_network
        )
        component_set = set(indexes)
        network.component_intersection_count = sum(
            count for (left, right), count in edge_intersections.items()
            if left in component_set and right in component_set
        )
        network.intersection_count = network.component_intersection_count
        network.candidate_coverage_fraction = len(combined_by_id) / max(len(result.candidates), 1)
        network.network_signature = _network_signature(
            components_for_network, network.bounding_box
        )
        area = max(
            (network.bounding_box[2] - network.bounding_box[0])
            * (network.bounding_box[3] - network.bounding_box[1]),
            1.0,
        )
        network.intersection_density = network.intersection_count / area
        network.evidence.append("MAXIMAL_ORTHOGONAL_COMPONENT")
        network.evidence.append("REPEATED_ORTHOGONAL_INTERSECTIONS")
        for pattern in components_for_network:
            for member in pattern.members:
                member.secondary_pattern_ids.append(network.pattern_id)
        hierarchical.append(network)
    for left_index, left in enumerate(hierarchical):
        for right in hierarchical[left_index + 1:]:
            if _bbox_overlap(left, right):
                left.overlap_with_other_networks.append(right.pattern_id)
                right.overlap_with_other_networks.append(left.pattern_id)
                left.warnings.append("OVERLAPPING_PATTERN_GROUPS")
                right.warnings.append("OVERLAPPING_PATTERN_GROUPS")
    # This compares the old pairwise overlap policy with the retained maximal
    # networks, including old overlaps that lacked a meaningful intersection.
    suppressed = max(0, prior_pairwise_network_count - len(hierarchical))
    return hierarchical, suppressed


def detect_linear_patterns(candidate_result: MemberLineCandidateResult) -> PatternResult:
    """Analyze accepted candidates without changing their geometry or acceptance."""
    timings: Dict[str, float] = {}
    started = time.monotonic()
    groups = _spatial_orientation_groups(candidate_result.candidates)
    timings["spatial_partitioning_seconds"] = time.monotonic() - started
    clustered_groups = [group for group in groups if len(group) >= MIN_GROUP_SIZE]
    started = time.monotonic()
    patterns = [
        _build_pattern(f"LP-{index:04d}", group, candidate_result, timings)
        for index, group in enumerate(clustered_groups, 1)
    ]
    primary_elapsed = time.monotonic() - started
    pair_elapsed = timings.get("double_line_pair_analysis_seconds", 0.0)
    timings["primary_pattern_clustering_seconds"] = max(0.0, primary_elapsed - pair_elapsed)
    assigned = {candidate_id for pattern in patterns for candidate_id in pattern.candidate_ids}
    unclustered = [item for item in candidate_result.candidates if item.candidate_id not in assigned]
    started = time.monotonic()
    hierarchical, suppressed = _hierarchical_patterns(
        patterns, candidate_result, len(patterns) + 1
    )
    timings["orthogonal_network_construction_seconds"] = time.monotonic() - started
    secondary_count = sum(len(member.secondary_pattern_ids) for pattern in patterns for member in pattern.members)
    network_candidate_ids = {
        candidate_id for network in hierarchical for candidate_id in network.candidate_ids
    }
    counts = Counter(pattern.pattern_type.value for pattern in patterns)
    warnings = []
    if not patterns:
        warnings.append("NO_LINEAR_PATTERNS")
    if unclustered:
        warnings.append("UNCLUSTERED_CANDIDATES_PRESENT")
    return PatternResult(
        candidate_result.source_file, candidate_result.pdf_page, candidate_result.sheet_number,
        candidate_result.sheet_title, candidate_result.sheet_subject, candidate_result.level,
        candidate_result.segment, candidate_result.page_geometry,
        candidate_result.plan_region_bounds, candidate_result.grid_system,
        len(candidate_result.candidates), len(assigned), len(unclustered), secondary_count,
        len(network_candidate_ids), secondary_count, len(hierarchical), suppressed,
        len(patterns), dict(sorted(counts.items())), len(hierarchical), patterns, hierarchical,
        unclustered, warnings, timings,
    )


def filter_linear_patterns(
    patterns: Iterable[LinearPattern], pattern_id: Optional[str] = None,
    pattern_type: Optional[LinearPatternType] = None, orientation: Optional[str] = None,
    min_candidates: int = 0, min_confidence: float = 0.0, regular_only: bool = False,
    dense_only: bool = False,
) -> List[LinearPattern]:
    normalized_id = pattern_id.strip().upper() if pattern_id else None
    return [
        item for item in patterns
        if (normalized_id is None or item.pattern_id == normalized_id)
        and (pattern_type is None or item.pattern_type == pattern_type)
        and (orientation is None or item.primary_orientation == orientation)
        and item.candidate_count >= min_candidates and item.confidence >= min_confidence
        and (not regular_only or item.pattern_type == LinearPatternType.REGULAR_SPACING_FIELD)
        and (not dense_only or item.pattern_type == LinearPatternType.DENSE_LINEAR_FIELD)
    ]
