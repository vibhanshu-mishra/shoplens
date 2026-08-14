"""Conservative, coordinate-aware comparison for compact geometry summaries."""

from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any, Dict, List, Sequence, Tuple


def compare_geometry_reports(
    current: Dict[str, Any], baseline: Dict[str, Any], tolerance: float
) -> Dict[str, Any]:
    current_cases = _index_cases(current.get("case_results", []), "current")
    baseline_cases = _index_cases(baseline.get("case_results", []), "baseline")
    changes = []
    for case_id in sorted(set(current_cases) | set(baseline_cases)):
        now, before = current_cases.get(case_id), baseline_cases.get(case_id)
        if now is not None and now.get("execution_status") != "PASS":
            changes.append({
                "case_id": case_id,
                "change": "ERROR",
                "details": [{"kind": "EXECUTION_ERROR", "error": now.get("error")}],
            })
        elif before is None:
            changes.append({"case_id": case_id, "change": "NEW_CASE", "details": []})
        elif now is None:
            changes.append({"case_id": case_id, "change": "REMOVED_CASE", "details": []})
        elif before.get("execution_status") != "PASS":
            changes.append({
                "case_id": case_id,
                "change": "IMPROVEMENT",
                "details": [{"kind": "EXECUTION_RECOVERED"}],
            })
        else:
            details = _case_details(now, before, tolerance)
            change = (
                "REGRESSION"
                if any(item["kind"].startswith("LOST_") for item in details)
                else "REVIEW_REQUIRED" if details else "UNCHANGED"
            )
            changes.append({"case_id": case_id, "change": change, "details": details})
    return {
        "summary": dict(sorted(Counter(item["change"] for item in changes).items())),
        "case_changes": changes,
    }


def _index_cases(results: Sequence[Dict[str, Any]], report_name: str) -> Dict[str, Dict[str, Any]]:
    indexed = {}
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            raise ValueError(f"{report_name} geometry case result {index} must be an object")
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{report_name} geometry case result {index} requires a non-empty case_id")
        if case_id in indexed:
            raise ValueError(f"duplicate {report_name} geometry case_id: {case_id}")
        indexed[case_id] = result
    return indexed


def _case_details(now: Dict[str, Any], before: Dict[str, Any], tolerance: float) -> List[Dict[str, Any]]:
    details = []
    now_grid, old_grid = now.get("grid"), before.get("grid")
    if now_grid is not None and old_grid is not None:
        details.extend(_grid_details(now_grid, old_grid, tolerance))
    elif now_grid != old_grid:
        details.append({"kind": "GRID_RESULT_CHANGE", "before": old_grid is not None, "current": now_grid is not None})

    now_localization, old_localization = now.get("localization"), before.get("localization")
    if now_localization is not None and old_localization is not None:
        for key in ("total_section_detections", "complete_bay", "on_axis", "outside_grid", "ambiguous", "unlocalized"):
            if now_localization.get(key) != old_localization.get(key):
                details.append({"kind": "LOCALIZATION_CHANGE", "field": key, "before": old_localization.get(key), "current": now_localization.get(key)})
        if now_localization.get("grid_system_distribution", {}) != old_localization.get("grid_system_distribution", {}):
            details.append({
                "kind": "LOCALIZATION_DISTRIBUTION_CHANGE",
                "before": old_localization.get("grid_system_distribution", {}),
                "current": now_localization.get("grid_system_distribution", {}),
            })
    elif now_localization != old_localization:
        details.append({"kind": "LOCALIZATION_RESULT_CHANGE", "before": old_localization is not None, "current": now_localization is not None})
    return details


def _grid_details(now: Dict[str, Any], before: Dict[str, Any], tolerance: float) -> List[Dict[str, Any]]:
    details = _axis_details(now, before, tolerance)
    if now.get("grid_system_count") != before.get("grid_system_count"):
        details.append({"kind": "GRID_SYSTEM_COUNT_CHANGE", "before": before.get("grid_system_count"), "current": now.get("grid_system_count")})

    old_systems = before.get("secondary_systems", [])
    new_systems = now.get("secondary_systems", [])
    pairs = _pair_secondary_systems(old_systems, new_systems)
    paired_old = {old_index for old_index, _ in pairs}
    paired_new = {new_index for _, new_index in pairs}
    for old_index, new_index in pairs:
        old_system, new_system = old_systems[old_index], new_systems[new_index]
        details.extend(_axis_details(new_system, old_system, tolerance, scope="SECONDARY", system_id=_system_id(old_system, new_system)))
    for old_index, old_system in enumerate(old_systems):
        if old_index not in paired_old:
            details.append({"kind": "LOST_SECONDARY_SYSTEM", "system_id": _system_id(old_system)})
            details.extend(_axis_details({}, old_system, tolerance, scope="SECONDARY", system_id=_system_id(old_system)))
    for new_index, new_system in enumerate(new_systems):
        if new_index not in paired_new:
            details.append({"kind": "NEW_SECONDARY_SYSTEM", "system_id": _system_id(new_system)})
            details.extend(_axis_details(new_system, {}, tolerance, scope="SECONDARY", system_id=_system_id(new_system)))
    return details


def _axis_details(
    now: Dict[str, Any], before: Dict[str, Any], tolerance: float,
    scope: str = "PRIMARY", system_id: str = "PRIMARY",
) -> List[Dict[str, Any]]:
    details = []
    for orientation in ("horizontal_axes", "vertical_axes"):
        details.extend(_match_axes(before.get(orientation, []), now.get(orientation, []), tolerance, scope, system_id))
    return details


def _match_axes(
    before: Sequence[Dict[str, Any]], now: Sequence[Dict[str, Any]], tolerance: float,
    scope: str, system_id: str,
) -> List[Dict[str, Any]]:
    old_groups, new_groups = defaultdict(list), defaultdict(list)
    for axis in before:
        old_groups[(axis.get("orientation"), axis.get("label"))].append(axis)
    for axis in now:
        new_groups[(axis.get("orientation"), axis.get("label"))].append(axis)
    details = []
    for identity in sorted(set(old_groups) | set(new_groups), key=lambda value: (str(value[0]), str(value[1]))):
        old_axes = sorted(old_groups[identity], key=lambda axis: float(axis.get("coordinate", 0)))
        new_axes = sorted(new_groups[identity], key=lambda axis: float(axis.get("coordinate", 0)))
        pairs = _minimum_cost_pairs(old_axes, new_axes)
        paired_old = {old_index for old_index, _ in pairs}
        paired_new = {new_index for _, new_index in pairs}
        for old_index, new_index in pairs:
            old_axis, new_axis = old_axes[old_index], new_axes[new_index]
            context = {"orientation": identity[0], "label": identity[1], "system_scope": scope, "system_id": system_id}
            if abs(float(old_axis["coordinate"]) - float(new_axis["coordinate"])) > tolerance:
                details.append({"kind": "MOVED_AXIS", **context, "before": old_axis["coordinate"], "current": new_axis["coordinate"]})
            if old_axis.get("intersection_count") != new_axis.get("intersection_count"):
                details.append({"kind": "INTERSECTION_CHANGE", **context, "before": old_axis.get("intersection_count"), "current": new_axis.get("intersection_count")})
        details.extend({"kind": "LOST_AXIS", "orientation": identity[0], "label": axis["label"], "coordinate": axis["coordinate"], "system_scope": scope, "system_id": system_id} for index, axis in enumerate(old_axes) if index not in paired_old)
        details.extend({"kind": "NEW_AXIS", "orientation": identity[0], "label": axis["label"], "coordinate": axis["coordinate"], "system_scope": scope, "system_id": system_id} for index, axis in enumerate(new_axes) if index not in paired_new)
    return details


def _minimum_cost_pairs(before: Sequence[Dict[str, Any]], now: Sequence[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Return a deterministic minimum-cost one-to-one coordinate assignment."""

    if not before or not now:
        return []
    if len(before) > len(now):
        return [(new_index, old_index) for new_index, old_index in _minimum_cost_pairs(now, before)]

    @lru_cache(maxsize=None)
    def solve(old_index: int, used_new: int):
        if old_index == len(before):
            return 0.0, ()
        best = None
        old_coordinate = float(before[old_index]["coordinate"])
        for new_index, new_axis in enumerate(now):
            if used_new & (1 << new_index):
                continue
            tail_cost, tail_pairs = solve(old_index + 1, used_new | (1 << new_index))
            candidate = (
                abs(old_coordinate - float(new_axis["coordinate"])) + tail_cost,
                ((old_index, new_index),) + tail_pairs,
            )
            if best is None or candidate < best:
                best = candidate
        return best

    return list(solve(0, 0)[1])


def _pair_secondary_systems(before: Sequence[Dict[str, Any]], now: Sequence[Dict[str, Any]]) -> List[Tuple[int, int]]:
    candidates = []
    for old_index, old_system in enumerate(before):
        old_id = old_system.get("grid_system_id")
        for new_index, new_system in enumerate(now):
            new_id = new_system.get("grid_system_id")
            old_labels = _system_labels(old_system)
            new_labels = _system_labels(new_system)
            overlap = len(old_labels & new_labels)
            exact_id = bool(old_id and old_id == new_id and _stable_system_id(old_id))
            if overlap or exact_id:
                distance = _system_distance(old_system, new_system)
                candidates.append((0 if exact_id else 1, -overlap, distance, old_index, new_index))
    pairs = []
    used_old, used_new = set(), set()
    for _, _, _, old_index, new_index in sorted(candidates):
        if old_index not in used_old and new_index not in used_new:
            pairs.append((old_index, new_index))
            used_old.add(old_index)
            used_new.add(new_index)
    return pairs


def _system_labels(system: Dict[str, Any]):
    return {(axis.get("orientation"), axis.get("label")) for orientation in ("horizontal_axes", "vertical_axes") for axis in system.get(orientation, [])}


def _system_distance(before: Dict[str, Any], now: Dict[str, Any]) -> float:
    old_axes = [axis for orientation in ("horizontal_axes", "vertical_axes") for axis in before.get(orientation, [])]
    new_axes = [axis for orientation in ("horizontal_axes", "vertical_axes") for axis in now.get(orientation, [])]
    return sum(abs(float(old["coordinate"]) - float(new["coordinate"])) for old, new in zip(sorted(old_axes, key=lambda item: float(item["coordinate"])), sorted(new_axes, key=lambda item: float(item["coordinate"]))))


def _system_id(*systems: Dict[str, Any]) -> str:
    for system in systems:
        if system.get("grid_system_id"):
            return str(system["grid_system_id"])
    return "SECONDARY"


def _stable_system_id(value: Any) -> bool:
    """Generated secondary indexes are ordering-dependent, so use geometry for them."""

    return bool(value) and "_SECONDARY_GRID_" not in str(value)
