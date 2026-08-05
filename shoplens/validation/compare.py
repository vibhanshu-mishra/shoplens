"""Stable relative-filename comparison for validation reports."""

from typing import Any, Dict, List


STAGE_METRICS = {
    "SHEET_LIST": ("declared_sheet_count",),
    "TITLE_BLOCKS": ("identified_page_count",),
    "PACKAGE_CLASSIFICATION": ("unknown_sheet_count",),
}


def compare_reports(current: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    current_packages = {item["relative_path"]: item for item in current.get("package_results", [])}
    baseline_packages = {item["relative_path"]: item for item in baseline.get("package_results", [])}
    changes: List[Dict[str, Any]] = []
    for name in sorted(set(current_packages) | set(baseline_packages)):
        now, before = current_packages.get(name), baseline_packages.get(name)
        if before is None:
            changes.append({"relative_path": name, "change": "NEW_PACKAGE", "details": []})
            continue
        if now is None:
            changes.append({"relative_path": name, "change": "REMOVED_PACKAGE", "details": []})
            continue
        details = _package_details(now, before)
        old_failed = before.get("overall_status") in {"FAIL", "TIMEOUT"}
        new_failed = now.get("overall_status") in {"FAIL", "TIMEOUT"}
        if new_failed and not old_failed:
            change = "REGRESSION"
        elif old_failed and not new_failed:
            change = "IMPROVEMENT"
        elif any(item["direction"] == "REGRESSION" for item in details):
            change = "REGRESSION"
        elif any(item["direction"] == "IMPROVEMENT" for item in details):
            change = "IMPROVEMENT"
        else:
            change = "UNCHANGED"
        changes.append({"relative_path": name, "change": change, "details": details})
    counts: Dict[str, int] = {}
    for item in changes:
        counts[item["change"]] = counts.get(item["change"], 0) + 1
    return {"summary": dict(sorted(counts.items())), "package_changes": changes}


def _package_details(now: Dict[str, Any], before: Dict[str, Any]) -> List[Dict[str, Any]]:
    details = []
    current_stages = {stage["stage_name"]: stage for stage in now.get("stages", [])}
    baseline_stages = {stage["stage_name"]: stage for stage in before.get("stages", [])}
    rank = {"PASS": 0, "PASS_WITH_WARNINGS": 1, "UNREVIEWED": 1, "SKIPPED": 2, "FAIL": 3, "TIMEOUT": 4}
    for name in sorted(set(current_stages) & set(baseline_stages)):
        current_stage, baseline_stage = current_stages[name], baseline_stages[name]
        current_rank = rank.get(current_stage.get("status"), 3)
        baseline_rank = rank.get(baseline_stage.get("status"), 3)
        if current_rank != baseline_rank:
            details.append({
                "field": f"{name}.status", "before": baseline_stage.get("status"),
                "current": current_stage.get("status"),
                "direction": "REGRESSION" if current_rank > baseline_rank else "IMPROVEMENT",
            })
        for metric in STAGE_METRICS.get(name, ()):
            old = baseline_stage.get("metrics", {}).get(metric)
            new = current_stage.get("metrics", {}).get(metric)
            if old is None or new is None or old == new:
                continue
            regression = (
                (metric == "unknown_sheet_count" and new > old)
                or (metric in {"declared_sheet_count", "identified_page_count"} and new < old)
            )
            improvement = (
                (metric == "unknown_sheet_count" and new < old)
                or (metric in {"declared_sheet_count", "identified_page_count"} and new > old)
            )
            details.append({"field": f"{name}.{metric}", "before": old, "current": new,
                            "direction": "REGRESSION" if regression else "IMPROVEMENT" if improvement else "CHANGE"})
    old_warnings = sum(len(stage.get("warnings", [])) for stage in before.get("stages", []))
    new_warnings = sum(len(stage.get("warnings", [])) for stage in now.get("stages", []))
    if new_warnings != old_warnings:
        details.append({"field": "warning_count", "before": old_warnings, "current": new_warnings,
                        "direction": "REGRESSION" if new_warnings > old_warnings else "IMPROVEMENT"})
    old_runtime = float(before.get("runtime_seconds", 0))
    new_runtime = float(now.get("runtime_seconds", 0))
    if old_runtime > 0 and new_runtime > old_runtime * 1.25:
        details.append({"field": "runtime_seconds", "before": old_runtime, "current": new_runtime,
                        "direction": "REGRESSION"})
    return details
