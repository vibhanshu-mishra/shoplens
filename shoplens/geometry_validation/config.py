"""Local-only JSON configuration for geometry regression cases."""

import json
from pathlib import Path

from .models import GeometryCaseConfig, GeometryValidationConfig


SCHEMA_VERSION = 1
VALID_CHECKS = {"GRID", "LOCALIZATION"}


def load_geometry_config(path: Path) -> GeometryValidationConfig:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or not _valid_schema_version(values.get("schema_version")):
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    unknown = sorted(set(values) - {"schema_version", "cases", "coordinate_tolerance"})
    if unknown:
        raise ValueError(f"unknown geometry configuration fields: {', '.join(unknown)}")
    raw_cases = values.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("cases must be a list")
    cases = []
    seen = set()
    for index, value in enumerate(raw_cases, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"case {index} must be an object")
        unknown_case = sorted(set(value) - {"case_id", "pdf", "page", "sheet", "checks"})
        if unknown_case:
            raise ValueError(f"case {index} has unknown fields: {', '.join(unknown_case)}")
        case_id, pdf = value.get("case_id"), value.get("pdf")
        if not isinstance(case_id, str) or not case_id.strip() or not isinstance(pdf, str) or not pdf.strip():
            raise ValueError(f"case {index} requires non-empty case_id and pdf")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        page, sheet = value.get("page"), value.get("sheet")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise ValueError(f"case {case_id} page must be a positive integer")
        if page is None and (not isinstance(sheet, str) or not sheet.strip()):
            raise ValueError(f"case {case_id} requires page or sheet")
        checks = value.get("checks", ["GRID", "LOCALIZATION"])
        if (
            not isinstance(checks, list)
            or not checks
            or any(not isinstance(item, str) or item not in VALID_CHECKS for item in checks)
        ):
            raise ValueError(f"case {case_id} checks must contain GRID and/or LOCALIZATION")
        cases.append(GeometryCaseConfig(case_id, pdf, page, sheet, tuple(checks)))
    tolerance = values.get("coordinate_tolerance", 2.0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance <= 0:
        raise ValueError("coordinate_tolerance must be greater than zero")
    return GeometryValidationConfig(SCHEMA_VERSION, tuple(cases), float(tolerance))


def validate_geometry_baseline(value):
    """Validate the public shape of a baseline before comparison."""

    if not isinstance(value, dict):
        raise ValueError("geometry baseline must be a JSON object")
    if not _valid_schema_version(value.get("schema_version")):
        raise ValueError(f"geometry baseline schema_version must be {SCHEMA_VERSION}")
    case_results = value.get("case_results")
    if not isinstance(case_results, list):
        raise ValueError("geometry baseline case_results must be a list")
    seen = set()
    for index, result in enumerate(case_results, start=1):
        if not isinstance(result, dict):
            raise ValueError(f"geometry baseline case result {index} must be an object")
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"geometry baseline case result {index} requires a non-empty case_id")
        if case_id in seen:
            raise ValueError(f"duplicate geometry baseline case_id: {case_id}")
        seen.add(case_id)
        _validate_case_result_fields(result, index)
    return value


def _valid_schema_version(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == SCHEMA_VERSION


def _validate_case_result_fields(result, index: int) -> None:
    grid = result.get("grid")
    if grid is not None:
        if not isinstance(grid, dict):
            raise ValueError(f"geometry baseline case result {index} grid must be an object or null")
        _validate_axes(grid, "horizontal_axes", index)
        _validate_axes(grid, "vertical_axes", index)
        secondary = grid.get("secondary_systems", [])
        if not isinstance(secondary, list):
            raise ValueError(f"geometry baseline case result {index} secondary_systems must be a list")
        for system_index, system in enumerate(secondary, start=1):
            if not isinstance(system, dict):
                raise ValueError(f"geometry baseline case result {index} secondary system {system_index} must be an object")
            _validate_axes(system, "horizontal_axes", index)
            _validate_axes(system, "vertical_axes", index)
    localization = result.get("localization")
    if localization is not None:
        if not isinstance(localization, dict):
            raise ValueError(f"geometry baseline case result {index} localization must be an object or null")
        distribution = localization.get("grid_system_distribution", {})
        if not isinstance(distribution, dict):
            raise ValueError(f"geometry baseline case result {index} grid_system_distribution must be an object")


def _validate_axes(grid, field_name: str, index: int) -> None:
    axes = grid.get(field_name, [])
    if not isinstance(axes, list):
        raise ValueError(f"geometry baseline case result {index} {field_name} must be a list")
    for axis_index, axis in enumerate(axes, start=1):
        if not isinstance(axis, dict):
            raise ValueError(f"geometry baseline case result {index} {field_name}[{axis_index}] must be an object")
        if not isinstance(axis.get("label"), str) or not isinstance(axis.get("orientation"), str):
            raise ValueError(f"geometry baseline case result {index} {field_name}[{axis_index}] requires string label and orientation")
        coordinate = axis.get("coordinate")
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ValueError(f"geometry baseline case result {index} {field_name}[{axis_index}] requires numeric coordinate")
