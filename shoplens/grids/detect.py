"""Deterministic grid-label and orthogonal-axis extraction."""

import re
from collections import defaultdict
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from shoplens.classification.models import ClassifiedSheet
from shoplens.geometry.models import LineSegment, PageGeometry, ShapeGeometry

from .models import (
    GridAxis,
    GridLabel,
    GridOrientation,
    GridSystem,
    RejectedGridCandidate,
)


GRID_LABEL_RE = re.compile(
    r"^(?:[A-Z]{1,3}(?:\d+(?:\.\d+)?)?|[A-Z]{1,3}\.\d+|\d+(?:\.\d+)?)$"
)
ALIGNMENT_TOLERANCE = 12.0
ORIENTATION_TOLERANCE = 2.0


def detect_grid_system(
    source_file: str,
    geometry: PageGeometry,
    text_items: Iterable[Any],
    sheet: Optional[ClassifiedSheet] = None,
) -> GridSystem:
    items = [item for item in text_items if int(item.page) == geometry.pdf_page]
    labels, rejected, bubble_diagnostics = _bubble_labels(geometry, items)
    x_groups = _aligned_groups(labels, "x")
    y_groups = _aligned_groups(labels, "y")
    horizontal_groups = _axis_groups(
        GridOrientation.HORIZONTAL, x_groups, geometry.lines, geometry
    )
    vertical_groups = _axis_groups(
        GridOrientation.VERTICAL, y_groups, geometry.lines, geometry
    )
    candidates = _coherent_system_candidates(horizontal_groups, vertical_groups)
    multiple_horizontal = len(horizontal_groups) > 1
    multiple_vertical = len(vertical_groups) > 1
    coherent_system_count = len(candidates)
    if candidates:
        horizontal, vertical = candidates[0][0], candidates[0][1]
    else:
        horizontal, _, multiple_horizontal = _select_axis_group(
            GridOrientation.HORIZONTAL, x_groups, geometry.lines, geometry
        )
        vertical, _, multiple_vertical = _select_axis_group(
            GridOrientation.VERTICAL, y_groups, geometry.lines, geometry
        )
        if horizontal or vertical:
            _set_intersections(horizontal, vertical)
            candidates = [(horizontal, vertical, _intersection_total(horizontal, vertical))]
    selected_axes = [axis for axes, _, _ in candidates for axis in axes] + [
        axis for _, axes, _ in candidates for axis in axes
    ]
    initially_assigned_ids = {
        id(label)
        for axis in selected_axes
        for label in axis.label_candidates
    }
    remaining_labels = [label for label in labels if id(label) not in initially_assigned_ids]
    recovered_horizontal = _recover_perpendicular_supported_axes(
        GridOrientation.HORIZONTAL, remaining_labels, geometry.lines, geometry, vertical,
    )
    recovered_vertical = _recover_perpendicular_supported_axes(
        GridOrientation.VERTICAL,
        remaining_labels,
        geometry.lines,
        geometry,
        [*horizontal, *recovered_horizontal],
    )
    if recovered_horizontal or recovered_vertical:
        horizontal.extend(recovered_horizontal)
        vertical.extend(recovered_vertical)
        _set_recovery_intersections(recovered_horizontal, recovered_vertical, horizontal, vertical)
        selected_axes.extend([*recovered_horizontal, *recovered_vertical])
    _attach_matching_labels(selected_axes, labels)
    assigned_ids = {
        id(label)
        for axis in selected_axes
        for label in axis.label_candidates
    }
    unassigned = [label for label in labels if id(label) not in assigned_ids]

    warnings = list(geometry.warnings)
    if not horizontal and not vertical:
        warnings.append("NO_DOMINANT_GRID_SYSTEM")
    if coherent_system_count > 1:
        warnings.append("MULTIPLE_GRID_SYSTEMS_DETECTED")
    if coherent_system_count > 1:
        warnings.append("MULTIPLE_SIMILAR_GRID_SYSTEMS")
    elif not coherent_system_count and (multiple_horizontal or multiple_vertical):
        warnings.append("MULTIPLE_SIMILAR_GRID_SYSTEMS")
    confidence = _system_confidence(horizontal, vertical)
    primary = GridSystem(
        source_file=source_file,
        pdf_page=geometry.pdf_page,
        sheet_number=sheet.sheet_number if sheet else None,
        sheet_title=(sheet.actual_title or sheet.declared_title) if sheet else None,
        sheet_subject=sheet.subject if sheet else None,
        level=sheet.level if sheet else None,
        segment=sheet.segment if sheet else None,
        page_geometry=geometry,
        horizontal_axes=horizontal,
        vertical_axes=vertical,
        unassigned_labels=unassigned,
        rejected_candidates=rejected,
        confidence=confidence,
        warnings=list(dict.fromkeys(warnings)),
        bubble_diagnostics=bubble_diagnostics,
        grid_system_id=f"PAGE_{geometry.pdf_page}_DOMINANT_GRID",
        system_evidence=_system_evidence(horizontal, vertical),
    )
    primary.secondary_grid_systems = [
        GridSystem(
            source_file=source_file,
            pdf_page=geometry.pdf_page,
            sheet_number=primary.sheet_number,
            sheet_title=primary.sheet_title,
            sheet_subject=primary.sheet_subject,
            level=primary.level,
            segment=primary.segment,
            page_geometry=geometry,
            horizontal_axes=secondary_horizontal,
            vertical_axes=secondary_vertical,
            unassigned_labels=[],
            rejected_candidates=[],
            confidence=_system_confidence(secondary_horizontal, secondary_vertical),
            warnings=[],
            grid_system_id=f"PAGE_{geometry.pdf_page}_SECONDARY_GRID_{index}",
            system_evidence=_system_evidence(secondary_horizontal, secondary_vertical),
        )
        for index, (secondary_horizontal, secondary_vertical, _) in enumerate(candidates[1:], start=1)
    ]
    return primary


def _attach_matching_labels(axes: Sequence[GridAxis], labels: Sequence[GridLabel]) -> None:
    attached = {id(label) for axis in axes for label in axis.label_candidates}
    for axis in axes:
        for label in labels:
            if id(label) in attached or label.normalized_label != axis.normalized_label:
                continue
            coordinate = (
                label.center_y
                if axis.orientation == GridOrientation.HORIZONTAL
                else label.center_x
            )
            if abs(coordinate - axis.coordinate) <= 8.0:
                axis.label_candidates.append(label)
                attached.add(id(label))
        if (
            _distinct_observation_count(axis.label_candidates) > 1
            and "LABEL_REPEATED_AT_OPPOSITE_ENDS" not in axis.evidence
        ):
            axis.evidence.append("LABEL_REPEATED_AT_OPPOSITE_ENDS")


def _bubble_labels(
    geometry: PageGeometry, items: Sequence[Any]
) -> Tuple[List[GridLabel], List[RejectedGridCandidate], Dict[str, int]]:
    raw_ellipses = [shape for shape in geometry.shapes if shape.kind == "ELLIPSE"]
    ellipses = _deduplicate_bubbles(raw_ellipses)
    size_clusters = _bubble_size_clusters(ellipses)
    observations: Dict[str, Tuple[int, Any, List[ShapeGeometry]]] = {}
    rejected: List[RejectedGridCandidate] = []
    for shape in ellipses:
        hits = [item for item in items if _inside(shape, item, 4.0)]
        texts = [str(item.text).strip() for item in hits if str(item.text).strip()]
        candidates = [item for item in hits if GRID_LABEL_RE.fullmatch(_normalize(item.text))]
        if not candidates:
            rejected.append(
                RejectedGridCandidate(
                    " | ".join(texts),
                    geometry.pdf_page,
                    shape.bounds[0],
                    shape.bounds[1],
                    "EMPTY_OR_NON_GRID_BUBBLE",
                    ["ELLIPSE_CONTAINMENT"],
                )
            )
            continue
        candidate = min(candidates, key=lambda item: len(_normalize(item.text)))
        if any(_looks_like_reference(text) for text in texts if text != str(candidate.text).strip()):
            rejected.append(
                RejectedGridCandidate(
                    str(candidate.text).strip(),
                    geometry.pdf_page,
                    float(candidate.x),
                    float(candidate.y),
                    "DETAIL_OR_SECTION_REFERENCE",
                    ["ELLIPSE_CONTAINMENT", "REFERENCE_TEXT_IN_SAME_BUBBLE"],
                )
            )
            continue
        source_index = next(index for index, item in enumerate(items) if item is candidate)
        observation_id = _text_observation_id(geometry.pdf_page, source_index, candidate)
        if observation_id in observations:
            observations[observation_id][2].append(shape)
        else:
            observations[observation_id] = (source_index, candidate, [shape])

    labels: List[GridLabel] = []
    for observation_id, (source_index, candidate, shapes) in observations.items():
        shape = min(shapes, key=lambda value: _bubble_match_rank(value, candidate))
        size_cluster = size_clusters[id(shape)]
        labels.append(
            GridLabel(
                original_text=str(candidate.text),
                normalized_label=_normalize(candidate.text),
                page=geometry.pdf_page,
                x=float(candidate.x),
                y=float(candidate.y),
                width=float(candidate.width),
                height=float(candidate.height),
                associated_shape="ELLIPSE",
                confidence=0.78,
                evidence=[
                    "ELLIPSE_CONTAINMENT",
                    "ONE_PHYSICAL_LABEL_OBSERVATION",
                    f"BUBBLE_SIZE_CLUSTER:{size_cluster}",
                ],
                observation_id=observation_id,
                bubble_alternative_count=len(shapes) - 1,
            )
        )
    return labels, rejected, {
        "raw_bubble_candidate_count": len(raw_ellipses),
        "deduplicated_bubble_candidate_count": len(ellipses),
        "suppressed_duplicate_bubble_count": len(raw_ellipses) - len(ellipses),
        "bubble_size_cluster_count": len(set(size_clusters.values())),
        "physical_label_observation_count": len(labels),
        "alternative_bubble_association_count": sum(
            label.bubble_alternative_count for label in labels
        ),
    }


def _deduplicate_bubbles(shapes: Sequence[ShapeGeometry]) -> List[ShapeGeometry]:
    """Collapse repeat vector traces of the same circular bubble, conservatively."""

    retained: List[ShapeGeometry] = []
    for shape in sorted(shapes, key=lambda value: (value.bounds, value.source)):
        if any(_same_bubble(shape, existing) for existing in retained):
            continue
        retained.append(shape)
    return retained


def _same_bubble(left: ShapeGeometry, right: ShapeGeometry) -> bool:
    if left.page != right.page or left.source != right.source:
        return False
    left_width, left_height = _shape_size(left)
    right_width, right_height = _shape_size(right)
    if min(left_width, left_height, right_width, right_height) <= 0:
        return False
    left_x = (left.bounds[0] + left.bounds[2]) / 2.0
    left_y = (left.bounds[1] + left.bounds[3]) / 2.0
    right_x = (right.bounds[0] + right.bounds[2]) / 2.0
    right_y = (right.bounds[1] + right.bounds[3]) / 2.0
    center_tolerance = max(0.75, min(left_width, left_height, right_width, right_height) * 0.18)
    if abs(left_x - right_x) > center_tolerance or abs(left_y - right_y) > center_tolerance:
        return False
    if max(abs(left_width - right_width), abs(left_height - right_height)) > max(
        1.0, max(left_width, left_height, right_width, right_height) * 0.18
    ):
        return False
    intersection_width = max(0.0, min(left.bounds[2], right.bounds[2]) - max(left.bounds[0], right.bounds[0]))
    intersection_height = max(0.0, min(left.bounds[3], right.bounds[3]) - max(left.bounds[1], right.bounds[1]))
    overlap = intersection_width * intersection_height
    smaller_area = min(left_width * left_height, right_width * right_height)
    return overlap >= smaller_area * 0.78


def _bubble_size_clusters(shapes: Sequence[ShapeGeometry]) -> Dict[int, int]:
    """Cluster comparable bubble sizes without imposing one page-wide standard."""

    clusters: List[List[float]] = []
    assignments: Dict[int, int] = {}
    for shape in sorted(shapes, key=lambda value: min(_shape_size(value))):
        size = min(_shape_size(shape))
        cluster_index = next(
            (
                index for index, values in enumerate(clusters)
                if size <= median(values) * 1.25 and size >= median(values) * 0.80
            ),
            None,
        )
        if cluster_index is None:
            clusters.append([size])
            assignments[id(shape)] = len(clusters)
        else:
            clusters[cluster_index].append(size)
            assignments[id(shape)] = cluster_index + 1
    return assignments


def _bubble_match_rank(shape: ShapeGeometry, item: Any) -> Tuple[float, float, float]:
    width, height = _shape_size(shape)
    center_x = (shape.bounds[0] + shape.bounds[2]) / 2.0
    center_y = (shape.bounds[1] + shape.bounds[3]) / 2.0
    item_x = float(item.x) + float(item.width) / 2.0
    item_y = float(item.y) + float(item.height) / 2.0
    offset = ((item_x - center_x) / max(width, 1.0)) ** 2 + ((item_y - center_y) / max(height, 1.0)) ** 2
    return offset, abs(width - height), -min(width, height)


def _text_observation_id(page: int, source_index: int, item: Any) -> str:
    return ":".join(
        (
            str(page),
            str(source_index),
            _normalize(item.text),
            f"{float(item.x):.3f}",
            f"{float(item.y):.3f}",
            f"{float(item.width):.3f}",
            f"{float(item.height):.3f}",
        )
    )


def _aligned_groups(labels: Sequence[GridLabel], dimension: str) -> List[List[GridLabel]]:
    groups: List[List[GridLabel]] = []
    for label in labels:
        coordinate = label.center_x if dimension == "x" else label.center_y
        target = next(
            (
                group
                for group in groups
                if abs(
                    coordinate
                    - median(
                        [item.center_x if dimension == "x" else item.center_y for item in group]
                    )
                )
                <= ALIGNMENT_TOLERANCE
            ),
            None,
        )
        if target is None:
            groups.append([label])
        else:
            target.append(label)
    return sorted((group for group in groups if len(group) >= 3), key=len, reverse=True)


def _select_axis_group(
    orientation: GridOrientation,
    groups: Sequence[List[GridLabel]],
    lines: Sequence[LineSegment],
    geometry: PageGeometry,
) -> Tuple[List[GridAxis], List[GridLabel], bool]:
    candidates = _merge_opposite_label_groups(groups)
    ranked = []
    for labels in candidates:
        axes = _build_axes(orientation, labels, lines, geometry)
        ranked.append((len(axes), sum(axis.confidence for axis in axes), axes, labels))
    ranked.sort(key=lambda value: (-value[0], -value[1]))
    if not ranked or not ranked[0][2]:
        return [], [], False
    multiple = len(ranked) > 1 and ranked[1][0] >= max(3, int(ranked[0][0] * 0.75))
    return ranked[0][2], ranked[0][3], multiple


def _axis_groups(
    orientation: GridOrientation,
    groups: Sequence[List[GridLabel]],
    lines: Sequence[LineSegment],
    geometry: PageGeometry,
) -> List[List[GridAxis]]:
    """Build every bubble/line-supported orientation family before ranking one."""

    candidates = []
    for labels in _merge_opposite_label_groups(groups):
        axes = _build_axes(orientation, labels, lines, geometry)
        if axes:
            candidates.append(axes)
    return sorted(
        candidates,
        key=lambda axes: (-len(axes), -sum(axis.confidence for axis in axes)),
    )


def _coherent_system_candidates(
    horizontal_groups: Sequence[Sequence[GridAxis]],
    vertical_groups: Sequence[Sequence[GridAxis]],
) -> List[Tuple[List[GridAxis], List[GridAxis], int]]:
    """Partition bubble-supported axes by their actual intersection graph."""

    horizontal = list({id(axis): axis for group in horizontal_groups for axis in group}.values())
    vertical = list({id(axis): axis for group in vertical_groups for axis in group}.values())
    adjacency = {id(axis): set() for axis in horizontal + vertical}
    by_id = {id(axis): axis for axis in horizontal + vertical}
    for h_axis in horizontal:
        for v_axis in vertical:
            if _axes_intersect(h_axis, v_axis):
                adjacency[id(h_axis)].add(id(v_axis))
                adjacency[id(v_axis)].add(id(h_axis))

    candidates = []
    visited = set()
    for start in adjacency:
        if start in visited or not adjacency[start]:
            continue
        component = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(adjacency[current] - component)
        visited.update(component)
        component_horizontal = [axis for axis in horizontal if id(axis) in component]
        component_vertical = [axis for axis in vertical if id(axis) in component]
        intersections = _intersection_total(component_horizontal, component_vertical)
        if len(component_horizontal) < 2 or len(component_vertical) < 2 or intersections < 3:
            continue
        _set_intersections(component_horizontal, component_vertical)
        candidates.append((component_horizontal, component_vertical, intersections))
    return sorted(
        candidates,
        key=lambda value: (
            -(len(value[0]) + len(value[1])),
            -value[2],
            -_system_confidence(value[0], value[1]),
            min(axis.coordinate for axis in value[1]),
            min(axis.coordinate for axis in value[0]),
        ),
    )


def _intersection_total(
    horizontal: Sequence[GridAxis], vertical: Sequence[GridAxis]
) -> int:
    return sum(_axes_intersect(axis, other) for axis in horizontal for other in vertical)


def _axes_intersect(horizontal: GridAxis, vertical: GridAxis) -> bool:
    return (
        min(horizontal.start_x, horizontal.end_x) <= vertical.coordinate <= max(horizontal.start_x, horizontal.end_x)
        and min(vertical.start_y, vertical.end_y) <= horizontal.coordinate <= max(vertical.start_y, vertical.end_y)
    )


def _system_evidence(horizontal: Sequence[GridAxis], vertical: Sequence[GridAxis]) -> List[str]:
    if not horizontal or not vertical:
        return []
    return [
        "ORTHOGONAL_AXIS_FAMILIES",
        f"HORIZONTAL_AXIS_COUNT:{len(horizontal)}",
        f"VERTICAL_AXIS_COUNT:{len(vertical)}",
        f"PERPENDICULAR_INTERSECTIONS:{_intersection_total(horizontal, vertical)}",
    ]


def _merge_opposite_label_groups(
    groups: Sequence[List[GridLabel]],
) -> List[List[GridLabel]]:
    merged: List[List[GridLabel]] = []
    used = set()
    for index, group in enumerate(groups):
        if index in used:
            continue
        labels = list(group)
        names = {item.normalized_label for item in group}
        for other_index in range(index + 1, len(groups)):
            if other_index in used:
                continue
            other_names = {item.normalized_label for item in groups[other_index]}
            overlap = len(names & other_names) / max(1, len(names | other_names))
            if overlap >= 0.5 or (len(names & other_names) >= 3 and overlap >= 0.35):
                labels.extend(groups[other_index])
                names.update(other_names)
                used.add(other_index)
        merged.append(labels)
    return merged


def _build_axes(
    orientation: GridOrientation,
    labels: Sequence[GridLabel],
    lines: Sequence[LineSegment],
    geometry: PageGeometry,
) -> List[GridAxis]:
    by_label: Dict[str, List[GridLabel]] = defaultdict(list)
    for label in labels:
        by_label[label.normalized_label].append(label)
    axes = []
    for normalized, candidates in by_label.items():
        for label_cluster in _spatial_label_clusters(candidates, orientation):
            label_coordinate = median(
                [
                    label.center_y if orientation == GridOrientation.HORIZONTAL else label.center_x
                    for label in label_cluster
                ]
            )
            coordinate = _snap_axis_coordinate(lines, orientation, label_coordinate)
            aligned = [line for line in lines if _line_matches(line, orientation, coordinate)]
            span = geometry.width if orientation == GridOrientation.HORIZONTAL else geometry.height
            if not aligned:
                continue
            component = _matching_line_component(aligned, orientation, label_cluster)
            if component is None:
                continue
            if orientation == GridOrientation.HORIZONTAL:
                starts = [value for line in component for value in (line.x1, line.x2)]
                start_x, end_x = min(starts), max(starts)
                start_x, end_x = _cap_extent_to_labels(
                    start_x,
                    end_x,
                    [label.center_x for label in label_cluster],
                    (geometry.crop_box[0] + geometry.crop_box[2]) / 2.0,
                )
                start_y = end_y = coordinate
            else:
                starts = [value for line in component for value in (line.y1, line.y2)]
                start_y, end_y = min(starts), max(starts)
                start_y, end_y = _cap_extent_to_labels(
                    start_y,
                    end_y,
                    [label.center_y for label in label_cluster],
                    (geometry.crop_box[1] + geometry.crop_box[3]) / 2.0,
                )
                start_x = end_x = coordinate
            covered = _covered_length(component, orientation, (start_x, end_x, start_y, end_y))
            if covered < span * 0.04:
                continue
            evidence = [
                "ALIGNED_GRID_LABEL_BUBBLES",
                f"COLLINEAR_SEGMENTS:{len(component)}",
                f"SEGMENT_COVERAGE:{covered / span:.3f}",
            ]
            repeated_observations = _distinct_observation_count(label_cluster)
            if repeated_observations > 1:
                evidence.append("LABEL_REPEATED_AT_OPPOSITE_ENDS")
            suffix = "" if len(candidates) == 1 else f":{coordinate:.1f}"
            axes.append(
                GridAxis(
                    axis_id=f"{orientation.value}:{normalized}{suffix}",
                    orientation=orientation,
                    normalized_label=normalized,
                    alternate_labels=[],
                    coordinate=coordinate,
                    start_x=start_x,
                    start_y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                    source_segments=component,
                    label_candidates=list(label_cluster),
                    intersection_count=0,
                    confidence=min(0.94, 0.68 + min(0.16, covered / span * 0.12) + (0.08 if repeated_observations > 1 else 0.0)),
                    evidence=evidence,
                )
            )
    return sorted(axes, key=lambda axis: axis.coordinate)


def _spatial_label_clusters(
    labels: Sequence[GridLabel], orientation: GridOrientation
) -> List[List[GridLabel]]:
    """Keep repeated labels on disconnected axes as separate observations."""

    coordinate = (
        (lambda label: label.center_y)
        if orientation == GridOrientation.HORIZONTAL
        else (lambda label: label.center_x)
    )
    clusters: List[List[GridLabel]] = []
    for label in sorted(labels, key=coordinate):
        if clusters and abs(coordinate(label) - median([coordinate(item) for item in clusters[-1]])) <= ALIGNMENT_TOLERANCE:
            clusters[-1].append(label)
        else:
            clusters.append([label])
    return clusters


def _matching_line_component(
    lines: Sequence[LineSegment],
    orientation: GridOrientation,
    labels: Sequence[GridLabel],
) -> Optional[List[LineSegment]]:
    """Select the continuous projected line extent supported by this label cluster."""

    positions = [
        label.center_x if orientation == GridOrientation.HORIZONTAL else label.center_y
        for label in labels
    ]
    components = _line_extent_components(lines, orientation, positions)
    if not components:
        return None
    return max(
        components,
        key=lambda component: (
            _labels_near_component(positions, component, orientation),
            -_component_distance(positions, component, orientation),
            _covered_length(component, orientation),
        ),
    )


def _line_extent_components(
    lines: Sequence[LineSegment],
    orientation: GridOrientation,
    label_positions: Sequence[float] = (),
) -> List[List[LineSegment]]:
    """Split collinear drafting geometry without letting minor strokes bridge grids."""

    intervals = []
    for line in lines:
        low, high = sorted((line.x1, line.x2) if orientation == GridOrientation.HORIZONTAL else (line.y1, line.y2))
        if high > low:
            intervals.append((low, high, line))
    if not intervals:
        return []
    lengths = [high - low for low, high, _ in intervals]
    max_length = max(lengths)
    substantial_lengths = [
        length for length in lengths if length >= max_length * 0.10
    ]
    substantial_intervals = [
        interval for interval in intervals if interval[1] - interval[0] >= max_length * 0.10
    ]
    typical_length = median(substantial_lengths or lengths)
    # Native path extraction can split a dashed stroke at approximately 4.4
    # PDF points. Keep those recurring fragments continuous, while leaving
    # materially larger gaps for the guarded, label-rooted path below.
    continuity_gap = max(ORIENTATION_TOLERANCE * 2.5, typical_length * 0.20)

    # The relative threshold makes the longest interval substantial whenever
    # there is usable geometry. Keep this defensive fallback so malformed or
    # future interval selection cannot silently discard all short geometry.
    if not substantial_intervals:
        return _interval_components(intervals, continuity_gap)

    substantial_components = _interval_components(
        substantial_intervals, continuity_gap
    )
    component_bounds = [
        _component_extent(component, orientation)
        for component in substantial_components
    ]
    attachments: List[List[LineSegment]] = [
        [] for _ in substantial_components
    ]
    substantial_ids = {id(line) for _, _, line in substantial_intervals}
    minor_intervals = [
        interval for interval in intervals if id(interval[2]) not in substantial_ids
    ]
    isolated_minor_components = []
    for minor_component in _interval_components(minor_intervals, continuity_gap):
        low, high = _component_extent(minor_component, orientation)
        compatible = [
            (index, _interval_distance(low, high, start, end))
            for index, (start, end) in enumerate(component_bounds)
            if _interval_distance(low, high, start, end) <= continuity_gap
        ]
        if len(compatible) == 1:
            attachments[compatible[0][0]].extend(minor_component)
        elif not compatible:
            # Minor geometry that is independent of every structural component
            # can remain a selectable isolated extent.
            isolated_minor_components.append(minor_component)
        else:
            # A minor run compatible with two structural components could be a
            # detail-stroke bridge. It must not participate in axis matching:
            # making it selectable lets nearby labels prefer the bridge itself.
            continue

    components = [
        component + attached
        for component, attached in zip(substantial_components, attachments)
    ]
    components.extend(isolated_minor_components)
    # A physical grid bubble at the end of a locally continuous run is strong
    # evidence that the intervening short primitives are a segmented axis, not
    # an unlabeled bridge. Build this candidate before matching so the matcher
    # only ranks valid extents.
    components.extend(
        component
        for component in _interval_components(intervals, continuity_gap)
        if _endpoint_label_supported(
            component, orientation, label_positions, continuity_gap
        )
    )
    components.extend(
        _label_rooted_structural_runs(
            substantial_intervals,
            orientation,
            label_positions,
            continuity_gap,
        )
    )
    return sorted(
        components,
        key=lambda component: _component_extent(component, orientation)[0],
    )


def _label_rooted_structural_runs(
    intervals: Sequence[Tuple[float, float, LineSegment]],
    orientation: GridOrientation,
    label_positions: Sequence[float],
    continuity_gap: float,
) -> List[List[LineSegment]]:
    """Recover a label-supported run of recurring substantial fragments.

    This is deliberately narrower than general collinear continuity: it only
    spans repeated substantial fragments and requires a physical label near a
    resulting endpoint. Minor strokes never provide the links, so an unlabeled
    detail bridge cannot combine otherwise separated structural extents.
    """

    if len(intervals) < 3 or not label_positions:
        return []
    cadence_intervals = _outer_intervals(intervals)
    if len(cadence_intervals) < 3:
        return []
    typical_length = median(high - low for low, high, _ in cadence_intervals)
    structural_gap = max(continuity_gap, typical_length)
    return [
        component
        for component in _interval_components(cadence_intervals, structural_gap)
        if len(component) >= 3
        and _endpoint_label_supported(
            component, orientation, label_positions, structural_gap
        )
    ]


def _outer_intervals(
    intervals: Sequence[Tuple[float, float, LineSegment]],
) -> List[Tuple[float, float, LineSegment]]:
    """Keep interval fragments that advance the projected structural extent."""

    outer: List[Tuple[float, float, LineSegment]] = []
    greatest_high: Optional[float] = None
    for interval in sorted(intervals, key=lambda value: (value[0], -value[1])):
        if greatest_high is not None and interval[1] <= greatest_high:
            continue
        outer.append(interval)
        greatest_high = interval[1]
    return outer


def _recover_perpendicular_supported_axes(
    orientation: GridOrientation,
    labels: Sequence[GridLabel],
    lines: Sequence[LineSegment],
    geometry: PageGeometry,
    opposite_axes: Sequence[GridAxis],
) -> List[GridAxis]:
    """Recover a full label-rooted extent only when the grid supports it twice."""

    by_label: Dict[str, List[GridLabel]] = defaultdict(list)
    for label in labels:
        by_label[label.normalized_label].append(label)
    recovered = []
    for normalized, candidates in by_label.items():
        for label_cluster in _spatial_label_clusters(candidates, orientation):
            label_coordinate = median([
                label.center_y if orientation == GridOrientation.HORIZONTAL else label.center_x
                for label in label_cluster
            ])
            coordinate = _snap_axis_coordinate(lines, orientation, label_coordinate)
            aligned = [line for line in lines if _line_matches(line, orientation, coordinate)]
            if not aligned:
                continue
            span = geometry.width if orientation == GridOrientation.HORIZONTAL else geometry.height
            locally_matched = _matching_line_component(aligned, orientation, label_cluster)
            if locally_matched is None or _covered_length(locally_matched, orientation) >= span * 0.04:
                # This recovery is only for a label whose local component is
                # too short to become an axis. A separately viable component
                # belongs to normal system selection, not this fallback.
                continue
            positions = [
                label.center_x if orientation == GridOrientation.HORIZONTAL else label.center_y
                for label in label_cluster
            ]
            # The physical bubble must support an endpoint of the full extent,
            # not merely an interior detail stroke.
            if not _endpoint_label_supported(aligned, orientation, positions, 0.0):
                continue
            if orientation == GridOrientation.HORIZONTAL:
                values = [value for line in aligned for value in (line.x1, line.x2)]
                start_x, end_x = _cap_extent_to_labels(
                    min(values), max(values), positions,
                    (geometry.crop_box[0] + geometry.crop_box[2]) / 2.0,
                )
                start_y = end_y = coordinate
            else:
                values = [value for line in aligned for value in (line.y1, line.y2)]
                start_y, end_y = _cap_extent_to_labels(
                    min(values), max(values), positions,
                    (geometry.crop_box[1] + geometry.crop_box[3]) / 2.0,
                )
                start_x = end_x = coordinate
            covered = _covered_length(aligned, orientation, (start_x, end_x, start_y, end_y))
            if covered < span * 0.04:
                continue
            axis = GridAxis(
                axis_id=f"{orientation.value}:{normalized}:{coordinate:.1f}",
                orientation=orientation,
                normalized_label=normalized,
                alternate_labels=[],
                coordinate=coordinate,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                source_segments=aligned,
                label_candidates=list(label_cluster),
                intersection_count=0,
                confidence=min(0.94, 0.68 + min(0.16, covered / span * 0.12)),
                evidence=[
                    "ALIGNED_GRID_LABEL_BUBBLES",
                    "LABEL_ROOTED_PERPENDICULAR_RECOVERY",
                    f"COLLINEAR_SEGMENTS:{len(aligned)}",
                    f"SEGMENT_COVERAGE:{covered / span:.3f}",
                ],
            )
            intersections = sum(
                _axes_intersect(axis, other)
                if orientation == GridOrientation.HORIZONTAL
                else _axes_intersect(other, axis)
                for other in opposite_axes
            )
            if intersections < 2:
                continue
            axis.intersection_count = intersections
            axis.confidence = min(0.98, axis.confidence + 0.04)
            axis.evidence.append(f"PERPENDICULAR_INTERSECTIONS:{intersections}")
            recovered.append(axis)
    return sorted(recovered, key=lambda axis: axis.coordinate)


def _set_recovery_intersections(
    recovered_horizontal: Sequence[GridAxis],
    recovered_vertical: Sequence[GridAxis],
    horizontal: Sequence[GridAxis],
    vertical: Sequence[GridAxis],
) -> None:
    """Update existing intersection counters for axes added after graph selection."""

    for axis in horizontal:
        if axis in recovered_horizontal:
            continue
        axis.intersection_count += sum(
            _axes_intersect(axis, recovered) for recovered in recovered_vertical
        )
    for axis in vertical:
        if axis in recovered_vertical:
            continue
        axis.intersection_count += sum(
            _axes_intersect(recovered, axis) for recovered in recovered_horizontal
        )


def _interval_components(
    intervals: Sequence[Tuple[float, float, LineSegment]], continuity_gap: float
) -> List[List[LineSegment]]:
    """Partition sorted projected intervals using the established gap tolerance."""

    components: List[List[LineSegment]] = []
    current: List[LineSegment] = []
    current_high: Optional[float] = None
    for low, high, line in sorted(intervals, key=lambda value: (value[0], value[1])):
        if current and current_high is not None and low > current_high + continuity_gap:
            components.append(current)
            current = []
            current_high = None
        current.append(line)
        current_high = high if current_high is None else max(current_high, high)
    if current:
        components.append(current)
    return components


def _interval_distance(low: float, high: float, start: float, end: float) -> float:
    """Return the gap between two projected intervals, or zero when they overlap."""

    return max(start - high, low - end, 0.0)


def _endpoint_label_supported(
    component: Sequence[LineSegment],
    orientation: GridOrientation,
    label_positions: Sequence[float],
    continuity_gap: float,
) -> bool:
    """Return whether a physical grid label directly supports a minor-run endpoint."""

    start, end = _component_extent(component, orientation)
    tolerance = max(ALIGNMENT_TOLERANCE * 2.0, continuity_gap)
    return any(
        min(abs(position - start), abs(position - end)) <= tolerance
        for position in label_positions
    )


def _labels_near_component(
    positions: Sequence[float], component: Sequence[LineSegment], orientation: GridOrientation
) -> int:
    low, high = _component_extent(component, orientation)
    tolerance = max(ALIGNMENT_TOLERANCE * 2.0, (high - low) * 0.12)
    return sum(low - tolerance <= position <= high + tolerance for position in positions)


def _component_distance(
    positions: Sequence[float], component: Sequence[LineSegment], orientation: GridOrientation
) -> float:
    low, high = _component_extent(component, orientation)
    return sum(max(low - position, 0.0, position - high) for position in positions)


def _component_extent(component: Sequence[LineSegment], orientation: GridOrientation) -> Tuple[float, float]:
    values = [
        value
        for line in component
        for value in ((line.x1, line.x2) if orientation == GridOrientation.HORIZONTAL else (line.y1, line.y2))
    ]
    return min(values), max(values)


def _line_matches(line: LineSegment, orientation: GridOrientation, coordinate: float) -> bool:
    if orientation == GridOrientation.HORIZONTAL:
        return abs(line.y2 - line.y1) <= ORIENTATION_TOLERANCE and abs((line.y1 + line.y2) / 2.0 - coordinate) <= ORIENTATION_TOLERANCE
    return abs(line.x2 - line.x1) <= ORIENTATION_TOLERANCE and abs((line.x1 + line.x2) / 2.0 - coordinate) <= ORIENTATION_TOLERANCE


def _snap_axis_coordinate(
    lines: Sequence[LineSegment], orientation: GridOrientation, label_coordinate: float
) -> float:
    grouped: Dict[float, List[LineSegment]] = defaultdict(list)
    for line in lines:
        if orientation == GridOrientation.HORIZONTAL:
            if abs(line.y2 - line.y1) > ORIENTATION_TOLERANCE:
                continue
            coordinate = (line.y1 + line.y2) / 2.0
        else:
            if abs(line.x2 - line.x1) > ORIENTATION_TOLERANCE:
                continue
            coordinate = (line.x1 + line.x2) / 2.0
        if abs(coordinate - label_coordinate) <= 8.0:
            grouped[round(coordinate * 2.0) / 2.0].append(line)
    if not grouped:
        return label_coordinate
    best = max(
        grouped,
        key=lambda coordinate: (
            _covered_length(grouped[coordinate], orientation),
            -abs(coordinate - label_coordinate),
        ),
    )
    return best


def _cap_extent_to_labels(
    start: float, end: float, label_coordinates: Sequence[float], midpoint: float
) -> Tuple[float, float]:
    low = min(label_coordinates)
    high = max(label_coordinates)
    if low < midpoint < high:
        return max(start, low), min(end, high)
    if high <= midpoint:
        return max(start, low), end
    return start, min(end, high)


def _covered_length(
    lines: Sequence[LineSegment],
    orientation: GridOrientation,
    extent: Optional[Tuple[float, float, float, float]] = None,
) -> float:
    intervals = []
    for line in lines:
        if orientation == GridOrientation.HORIZONTAL:
            low, high = sorted((line.x1, line.x2))
            if extent:
                low, high = max(low, extent[0]), min(high, extent[1])
        else:
            low, high = sorted((line.y1, line.y2))
            if extent:
                low, high = max(low, extent[2]), min(high, extent[3])
        if high > low:
            intervals.append((low, high))
    if not intervals:
        return 0.0
    intervals.sort()
    total = 0.0
    current_low, current_high = intervals[0]
    for low, high in intervals[1:]:
        if low <= current_high:
            current_high = max(current_high, high)
        else:
            total += current_high - current_low
            current_low, current_high = low, high
    return total + current_high - current_low


def _set_intersections(horizontal: Sequence[GridAxis], vertical: Sequence[GridAxis]) -> None:
    for axis in horizontal:
        axis.intersection_count = sum(
            axis.start_x <= other.coordinate <= axis.end_x
            and other.start_y <= axis.coordinate <= other.end_y
            for other in vertical
        )
        if axis.intersection_count:
            axis.evidence.append(f"PERPENDICULAR_INTERSECTIONS:{axis.intersection_count}")
            axis.confidence = min(0.98, axis.confidence + 0.04)
    for axis in vertical:
        axis.intersection_count = sum(
            other.start_x <= axis.coordinate <= other.end_x
            and axis.start_y <= other.coordinate <= axis.end_y
            for other in horizontal
        )
        if axis.intersection_count:
            axis.evidence.append(f"PERPENDICULAR_INTERSECTIONS:{axis.intersection_count}")
            axis.confidence = min(0.98, axis.confidence + 0.04)


def _system_confidence(horizontal: Sequence[GridAxis], vertical: Sequence[GridAxis]) -> float:
    axes = list(horizontal) + list(vertical)
    if not axes:
        return 0.0
    base = sum(axis.confidence for axis in axes) / len(axes)
    return round(min(0.98, base + (0.04 if horizontal and vertical else 0.0)), 3)


def _distinct_observation_count(labels: Sequence[GridLabel]) -> int:
    """Count positioned-text observations, never alternative bubble interpretations."""

    return len({
        label.observation_id or (
            f"{label.page}:{label.normalized_label}:{label.x:.3f}:{label.y:.3f}:"
            f"{label.width:.3f}:{label.height:.3f}"
        )
        for label in labels
    })


def _inside(shape: ShapeGeometry, item: Any, tolerance: float) -> bool:
    center_x = float(item.x) + float(item.width) / 2.0
    center_y = float(item.y) + float(item.height) / 2.0
    return (
        shape.bounds[0] - tolerance <= center_x <= shape.bounds[2] + tolerance
        and shape.bounds[1] - tolerance <= center_y <= shape.bounds[3] + tolerance
    )


def _shape_size(shape: ShapeGeometry) -> Tuple[float, float]:
    return shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1]


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value).upper())


def _looks_like_reference(value: str) -> bool:
    compact = value.upper().replace(" ", "")
    return bool(re.search(r"S\d+-?\d", compact) or "/" in compact)
