"""Conservative, coordinate-aware comparison for compact geometry summaries."""

from collections import Counter, defaultdict
from typing import Any, Dict, List, Sequence


def compare_geometry_reports(current: Dict[str, Any], baseline: Dict[str, Any], tolerance: float) -> Dict[str, Any]:
    current_cases = {item["case_id"]: item for item in current.get("case_results", [])}
    baseline_cases = {item["case_id"]: item for item in baseline.get("case_results", [])}
    changes = []
    for case_id in sorted(set(current_cases) | set(baseline_cases)):
        now, before = current_cases.get(case_id), baseline_cases.get(case_id)
        if now is not None and now.get("execution_status") != "PASS":
            changes.append({"case_id": case_id, "change": "ERROR", "details": [{"kind": "EXECUTION_ERROR", "error": now.get("error")} ]})
        elif before is None:
            changes.append({"case_id": case_id, "change": "NEW_CASE", "details": []})
        elif now is None:
            changes.append({"case_id": case_id, "change": "REMOVED_CASE", "details": []})
        elif before.get("execution_status") != "PASS":
            changes.append({"case_id": case_id, "change": "IMPROVEMENT", "details": [{"kind": "EXECUTION_RECOVERED"}]})
        else:
            details = _case_details(now, before, tolerance)
            change = "REGRESSION" if any(item["kind"] == "LOST_AXIS" for item in details) else "REVIEW_REQUIRED" if details else "UNCHANGED"
            changes.append({"case_id": case_id, "change": change, "details": details})
    return {"summary": dict(sorted(Counter(item["change"] for item in changes).items())), "case_changes": changes}


def _case_details(now: Dict[str, Any], before: Dict[str, Any], tolerance: float) -> List[Dict[str, Any]]:
    details = []
    now_grid, old_grid = now.get("grid"), before.get("grid")
    if now_grid is not None and old_grid is not None:
        details.extend(_axis_details(now_grid, old_grid, tolerance))
        if now_grid.get("grid_system_count") != old_grid.get("grid_system_count"):
            details.append({"kind": "GRID_SYSTEM_COUNT_CHANGE", "before": old_grid.get("grid_system_count"), "current": now_grid.get("grid_system_count")})
    elif now_grid != old_grid:
        details.append({"kind": "GRID_RESULT_CHANGE", "before": old_grid is not None, "current": now_grid is not None})
    now_localization, old_localization = now.get("localization"), before.get("localization")
    if now_localization is not None and old_localization is not None:
        for key in ("total_section_detections", "complete_bay", "on_axis", "outside_grid", "ambiguous", "unlocalized"):
            if now_localization.get(key) != old_localization.get(key):
                details.append({"kind": "LOCALIZATION_CHANGE", "field": key, "before": old_localization.get(key), "current": now_localization.get(key)})
    elif now_localization != old_localization:
        details.append({"kind": "LOCALIZATION_RESULT_CHANGE", "before": old_localization is not None, "current": now_localization is not None})
    return details


def _axis_details(now: Dict[str, Any], before: Dict[str, Any], tolerance: float) -> List[Dict[str, Any]]:
    details = []
    for orientation in ("horizontal_axes", "vertical_axes"):
        details.extend(_match_axes(before.get(orientation, []), now.get(orientation, []), tolerance))
    return details


def _match_axes(before: Sequence[Dict[str, Any]], now: Sequence[Dict[str, Any]], tolerance: float) -> List[Dict[str, Any]]:
    old_groups, new_groups = defaultdict(list), defaultdict(list)
    for axis in before:
        old_groups[(axis.get("orientation"), axis.get("label"))].append(axis)
    for axis in now:
        new_groups[(axis.get("orientation"), axis.get("label"))].append(axis)
    details = []
    for identity in sorted(set(old_groups) | set(new_groups)):
        old_axes = sorted(old_groups[identity], key=lambda axis: axis.get("coordinate", 0))
        new_axes = sorted(new_groups[identity], key=lambda axis: axis.get("coordinate", 0))
        pairs = _nearest_axis_pairs(old_axes, new_axes)
        paired_old = {old_index for old_index, _ in pairs}
        paired_new = {new_index for _, new_index in pairs}
        for old_index, new_index in pairs:
            old_axis, new_axis = old_axes[old_index], new_axes[new_index]
            if abs(float(old_axis["coordinate"]) - float(new_axis["coordinate"])) > tolerance:
                details.append({"kind": "MOVED_AXIS", "orientation": identity[0], "label": identity[1], "before": old_axis["coordinate"], "current": new_axis["coordinate"]})
            if old_axis.get("intersection_count") != new_axis.get("intersection_count"):
                details.append({"kind": "INTERSECTION_CHANGE", "orientation": identity[0], "label": identity[1], "before": old_axis.get("intersection_count"), "current": new_axis.get("intersection_count")})
        details.extend({"kind": "LOST_AXIS", "orientation": identity[0], "label": axis["label"], "coordinate": axis["coordinate"]} for index, axis in enumerate(old_axes) if index not in paired_old)
        details.extend({"kind": "NEW_AXIS", "orientation": identity[0], "label": axis["label"], "coordinate": axis["coordinate"]} for index, axis in enumerate(new_axes) if index not in paired_new)
    return details


def _nearest_axis_pairs(before: Sequence[Dict[str, Any]], now: Sequence[Dict[str, Any]]) -> List[tuple[int, int]]:
    """Pair repeated labels by minimum coordinate distance, not list position."""

    available = [
        (abs(float(old_axis["coordinate"]) - float(new_axis["coordinate"])), old_index, new_index)
        for old_index, old_axis in enumerate(before)
        for new_index, new_axis in enumerate(now)
    ]
    pairs, used_before, used_now = [], set(), set()
    for _, old_index, new_index in sorted(available):
        if old_index not in used_before and new_index not in used_now:
            pairs.append((old_index, new_index))
            used_before.add(old_index)
            used_now.add(new_index)
    return pairs
