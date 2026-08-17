"""Privacy-safe JSON, Markdown, CSV, and explicit baseline writing."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .models import GeometryValidationResult


def write_geometry_json(path: Path, result: GeometryValidationResult, debug: bool = False) -> None:
    _prepare(path)
    path.write_text(json.dumps(result.to_dict(debug=debug), indent=2) + "\n", encoding="utf-8")


def write_geometry_baseline(path: Path, result: GeometryValidationResult, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError("baseline already exists; use --overwrite-baseline to replace it")
    _prepare(path)
    baseline = result.to_dict(debug=False)
    baseline["baseline_kind"] = "GEOMETRY_REGRESSION_BASELINE"
    baseline["baseline_created_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")


def write_geometry_markdown(path: Path, result: GeometryValidationResult) -> None:
    _prepare(path)
    lines = ["# ShopLens Geometry Validation", "", f"- Cases: {len(result.case_results)}", f"- Runtime: {result.runtime_seconds:.3f} seconds", f"- Coordinate tolerance: {result.coordinate_tolerance:.3f} points", "", "## Case summary", "", "| Case | Execution | Grid axes | Localization |", "|---|---|---|---|"]
    for case in result.case_results:
        grid = case.grid or {}
        localization = case.localization or {}
        axes = f"H={len(grid.get('horizontal_axes', []))} V={len(grid.get('vertical_axes', []))}" if grid else "-"
        localized = f"complete={localization.get('complete_bay', 0)} outside={localization.get('outside_grid', 0)}" if localization else "-"
        lines.append(f"| {_markdown_cell(case.case_id)} | {_markdown_cell(case.execution_status)} | {_markdown_cell(axes)} | {_markdown_cell(localized)} |")
    if result.comparison:
        lines.extend(["", "## Baseline comparison", ""])
        for item in result.comparison.get("case_changes", []):
            lines.append(f"- {_markdown_cell(item['change'])}: {_markdown_cell(item['case_id'])}")
            lines.extend(
                f"  - {_markdown_cell(detail['kind'])}: {_markdown_cell(json.dumps(detail, sort_keys=True))}"
                for detail in item["details"]
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_geometry_csv(path: Path, result: GeometryValidationResult) -> None:
    _prepare(path)
    with path.open("w", encoding="utf-8", newline="") as stream:
        comparison = {
            item["case_id"]: item["change"]
            for item in (result.comparison or {}).get("case_changes", [])
        }
        writer = csv.DictWriter(stream, fieldnames=("case_id", "execution_status", "comparison_status", "selected_page", "horizontal_axis_count", "vertical_axis_count", "grid_system_count", "unassigned_label_count", "rejected_candidate_count", "grid_warnings", "complete_bay", "on_axis", "outside_grid", "ambiguous", "unlocalized"))
        writer.writeheader()
        for case in result.case_results:
            grid, localization = case.grid or {}, case.localization or {}
            writer.writerow({
                "case_id": _csv_safe_cell(case.case_id),
                "execution_status": _csv_safe_cell(case.execution_status),
                "comparison_status": _csv_safe_cell(comparison.get(case.case_id, "")),
                "selected_page": case.selected_page or "",
                "horizontal_axis_count": len(grid.get("horizontal_axes", [])),
                "vertical_axis_count": len(grid.get("vertical_axes", [])),
                "grid_system_count": grid.get("grid_system_count", ""),
                "unassigned_label_count": grid.get("unassigned_label_count", ""),
                "rejected_candidate_count": grid.get("rejected_candidate_count", ""),
                "grid_warnings": _csv_safe_cell(";".join(grid.get("warnings", []))),
                "complete_bay": localization.get("complete_bay", ""),
                "on_axis": localization.get("on_axis", ""),
                "outside_grid": localization.get("outside_grid", ""),
                "ambiguous": localization.get("ambiguous", ""),
                "unlocalized": localization.get("unlocalized", ""),
            })


def _prepare(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _csv_safe_cell(value: Any) -> Any:
    """Prevent spreadsheet formula evaluation for user-controlled string cells."""

    if isinstance(value, str) and value and value.lstrip() and value.lstrip()[0] in "=+-@\t\r\n":
        return "'" + value
    return value
