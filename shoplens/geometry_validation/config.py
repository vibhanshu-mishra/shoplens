"""Local-only JSON configuration for geometry regression cases."""

import json
from pathlib import Path

from .models import GeometryCaseConfig, GeometryValidationConfig


SCHEMA_VERSION = 1
VALID_CHECKS = {"GRID", "LOCALIZATION"}


def load_geometry_config(path: Path) -> GeometryValidationConfig:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or values.get("schema_version") != SCHEMA_VERSION:
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
        if page is not None and (not isinstance(page, int) or page < 1):
            raise ValueError(f"case {case_id} page must be a positive integer")
        if page is None and (not isinstance(sheet, str) or not sheet.strip()):
            raise ValueError(f"case {case_id} requires page or sheet")
        checks = value.get("checks", ["GRID", "LOCALIZATION"])
        if not isinstance(checks, list) or not checks or any(item not in VALID_CHECKS for item in checks):
            raise ValueError(f"case {case_id} checks must contain GRID and/or LOCALIZATION")
        cases.append(GeometryCaseConfig(case_id, pdf, page, sheet, list(checks)))
    tolerance = values.get("coordinate_tolerance", 2.0)
    if not isinstance(tolerance, (int, float)) or tolerance <= 0:
        raise ValueError("coordinate_tolerance must be greater than zero")
    return GeometryValidationConfig(SCHEMA_VERSION, cases, float(tolerance))
