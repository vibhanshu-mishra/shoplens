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
    labels, rejected = _bubble_labels(geometry, items)
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
        if len(axis.label_candidates) > 1 and "LABEL_REPEATED_AT_OPPOSITE_ENDS" not in axis.evidence:
            axis.evidence.append("LABEL_REPEATED_AT_OPPOSITE_ENDS")


def _bubble_labels(
    geometry: PageGeometry, items: Sequence[Any]
) -> Tuple[List[GridLabel], List[RejectedGridCandidate]]:
    ellipses = [shape for shape in geometry.shapes if shape.kind == "ELLIPSE"]
    sizes = [min(_shape_size(shape)) for shape in ellipses if min(_shape_size(shape)) > 5]
    typical = median(sizes) if sizes else 0.0
    labels: List[GridLabel] = []
    rejected: List[RejectedGridCandidate] = []
    for shape in ellipses:
        shape_width, shape_height = _shape_size(shape)
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
        normalized = _normalize(candidate.text)
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
        if typical and (min(shape_width, shape_height) > typical * 1.22 or min(shape_width, shape_height) < typical * 0.72):
            rejected.append(
                RejectedGridCandidate(
                    str(candidate.text).strip(),
                    geometry.pdf_page,
                    float(candidate.x),
                    float(candidate.y),
                    "INCONSISTENT_BUBBLE_SIZE",
                    [f"TYPICAL_BUBBLE_SIZE:{typical:.2f}"],
                )
            )
            continue
        labels.append(
            GridLabel(
                original_text=str(candidate.text),
                normalized_label=normalized,
                page=geometry.pdf_page,
                x=float(candidate.x),
                y=float(candidate.y),
                width=float(candidate.width),
                height=float(candidate.height),
                associated_shape="ELLIPSE",
                confidence=0.78,
                evidence=["ELLIPSE_CONTAINMENT", "CONSISTENT_BUBBLE_SIZE"],
            )
        )
    return labels, rejected


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
        if len(candidates) > 1:
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
                confidence=min(0.94, 0.68 + min(0.16, covered / span * 0.12) + (0.08 if len(candidates) > 1 else 0.0)),
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
