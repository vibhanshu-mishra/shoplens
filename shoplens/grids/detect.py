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
    horizontal, horizontal_labels, multiple_horizontal = _select_axis_group(
        GridOrientation.HORIZONTAL, x_groups, geometry.lines, geometry
    )
    vertical, vertical_labels, multiple_vertical = _select_axis_group(
        GridOrientation.VERTICAL, y_groups, geometry.lines, geometry
    )
    _attach_matching_labels(horizontal + vertical, labels)
    horizontal_labels = [label for axis in horizontal for label in axis.label_candidates]
    vertical_labels = [label for axis in vertical for label in axis.label_candidates]
    assigned_ids = {id(label) for label in horizontal_labels + vertical_labels}
    unassigned = [label for label in labels if id(label) not in assigned_ids]
    _set_intersections(horizontal, vertical)

    warnings = list(geometry.warnings)
    if not horizontal and not vertical:
        warnings.append("NO_DOMINANT_GRID_SYSTEM")
    if multiple_horizontal or multiple_vertical:
        warnings.append("MULTIPLE_SIMILAR_GRID_SYSTEMS")
    confidence = _system_confidence(horizontal, vertical)
    return GridSystem(
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
    )


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
        label_coordinate = median(
            [
                label.center_y if orientation == GridOrientation.HORIZONTAL else label.center_x
                for label in candidates
            ]
        )
        coordinate = _snap_axis_coordinate(lines, orientation, label_coordinate)
        aligned = [line for line in lines if _line_matches(line, orientation, coordinate)]
        span = geometry.width if orientation == GridOrientation.HORIZONTAL else geometry.height
        if not aligned:
            continue
        if orientation == GridOrientation.HORIZONTAL:
            starts = [value for line in aligned for value in (line.x1, line.x2)]
            start_x, end_x = min(starts), max(starts)
            start_x, end_x = _cap_extent_to_labels(
                start_x,
                end_x,
                [label.center_x for label in candidates],
                (geometry.crop_box[0] + geometry.crop_box[2]) / 2.0,
            )
            start_y = end_y = coordinate
        else:
            starts = [value for line in aligned for value in (line.y1, line.y2)]
            start_y, end_y = min(starts), max(starts)
            start_y, end_y = _cap_extent_to_labels(
                start_y,
                end_y,
                [label.center_y for label in candidates],
                (geometry.crop_box[1] + geometry.crop_box[3]) / 2.0,
            )
            start_x = end_x = coordinate
        covered = _covered_length(aligned, orientation, (start_x, end_x, start_y, end_y))
        if covered < span * 0.04:
            continue
        evidence = [
            "ALIGNED_GRID_LABEL_BUBBLES",
            f"COLLINEAR_SEGMENTS:{len(aligned)}",
            f"SEGMENT_COVERAGE:{covered / span:.3f}",
        ]
        repeated_observations = _distinct_observation_count(candidates)
        if repeated_observations > 1:
            evidence.append("LABEL_REPEATED_AT_OPPOSITE_ENDS")
        axes.append(
            GridAxis(
                axis_id=f"{orientation.value}:{normalized}",
                orientation=orientation,
                normalized_label=normalized,
                alternate_labels=[],
                coordinate=coordinate,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                source_segments=aligned,
                label_candidates=list(candidates),
                intersection_count=0,
                confidence=min(0.94, 0.68 + min(0.16, covered / span * 0.12) + (0.08 if repeated_observations > 1 else 0.0)),
                evidence=evidence,
            )
        )
    return sorted(axes, key=lambda axis: axis.coordinate)


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
