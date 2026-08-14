"""Run isolated local geometry cases using existing extraction APIs."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Callable, Dict, List, Optional

from shoplens.extraction import extract_positioned_text, get_pdf_page_count
from shoplens.geometry import extract_page_geometry
from shoplens.grids import detect_grid_system
from shoplens.inventory.models import ClassifiedSectionDetection
from shoplens.localization import localize_section_detections
from shoplens.reporting import deduplicate_detections
from shoplens.steel.detect import analyze_positioned_text

from .models import GeometryCaseConfig, GeometryCaseResult, GeometryValidationConfig, GeometryValidationResult


def run_geometry_validation(
    config: GeometryValidationConfig,
    case_runner: Optional[Callable[[GeometryCaseConfig], Dict]] = None,
) -> GeometryValidationResult:
    started = datetime.now(timezone.utc)
    started_clock = monotonic()
    runner = case_runner or _run_case
    results = []
    for case in config.cases:
        try:
            value = runner(case)
            results.append(_result_from_value(case, value))
        except Exception as exc:  # case-level isolation is the harness contract
            results.append(GeometryCaseResult(case.case_id, "ERROR", error=_safe_error(exc, case.pdf), source_path=case.pdf))
    completed = datetime.now(timezone.utc)
    return GeometryValidationResult(
        schema_version=1,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        runtime_seconds=monotonic() - started_clock,
        coordinate_tolerance=config.coordinate_tolerance,
        git_revision=_git_revision(),
        case_results=results,
    )


def _result_from_value(case: GeometryCaseConfig, value: Dict) -> GeometryCaseResult:
    return GeometryCaseResult(
        case_id=case.case_id,
        execution_status=value.get("execution_status", "PASS"),
        selected_page=value.get("selected_page", case.page),
        grid=value.get("grid"),
        localization=value.get("localization"),
        error=value.get("error"),
        source_path=case.pdf,
    )


def _run_case(case: GeometryCaseConfig) -> Dict:
    path = Path(case.pdf)
    if not path.is_file():
        raise ValueError("configured PDF is not an existing file")
    page = case.page or _resolve_sheet_page(path, case.sheet)
    page_items = extract_positioned_text(path, pages=[page])
    geometry = extract_page_geometry(path, [page], page_items)[0]
    grid = detect_grid_system(str(path), geometry, page_items)
    value: Dict = {"execution_status": "PASS", "selected_page": page}
    if "GRID" in case.checks:
        value["grid"] = _grid_summary(grid)
    if "LOCALIZATION" in case.checks:
        raw, _, _ = analyze_positioned_text(page_items)
        detections, _ = deduplicate_detections(raw)
        localized = localize_section_detections(str(path), [_detection(item) for item in detections], grid)
        value["localization"] = _localization_summary(localized)
    return value


def _resolve_sheet_page(path: Path, sheet: Optional[str]) -> int:
    """Resolve a local sheet identifier only when callers omit an explicit page."""

    if not sheet:
        raise ValueError("case requires page or sheet")
    from shoplens.classification import build_package_index
    from shoplens.sheets import extract_sheet_list
    from shoplens.title_blocks import extract_title_blocks, reconcile_sheets

    pages = list(range(1, get_pdf_page_count(path) + 1))
    items = extract_positioned_text(path, pages=pages)
    declared = extract_sheet_list(items, str(path), pages)
    actual = extract_title_blocks(
        items, str(path), pages, declared.entries,
        declared_total=declared.declared_total, sheet_list_pages=declared.sheet_list_pages,
    )
    matches = [item for item in build_package_index(reconcile_sheets(declared, actual)).sheets if item.sheet_number == sheet.strip().upper()]
    matched_pages = sorted({page for item in matches for page in (item.actual_pdf_pages or ([item.pdf_page] if item.pdf_page else []))})
    if len(matched_pages) != 1:
        raise ValueError("configured sheet did not resolve to one page")
    return matched_pages[0]


def _grid_summary(grid) -> Dict:
    systems = grid.all_grid_systems
    return {
        "grid_system_count": len(systems),
        "primary_grid_system_id": grid.grid_system_id,
        "horizontal_axis_count": len(grid.horizontal_axes),
        "vertical_axis_count": len(grid.vertical_axes),
        "horizontal_axes": [_axis(axis) for axis in grid.horizontal_axes],
        "vertical_axes": [_axis(axis) for axis in grid.vertical_axes],
        "secondary_grid_system_count": len(grid.secondary_grid_systems),
        "secondary_systems": [
            {
                "grid_system_id": system.grid_system_id,
                "horizontal_axes": [_axis(axis) for axis in system.horizontal_axes],
                "vertical_axes": [_axis(axis) for axis in system.vertical_axes],
            }
            for system in grid.secondary_grid_systems
        ],
        "unassigned_label_count": len(grid.unassigned_labels),
        "rejected_candidate_count": len(grid.rejected_candidates),
        "warnings": list(grid.warnings),
    }


def _axis(axis) -> Dict:
    return {"orientation": axis.orientation.value, "label": axis.normalized_label, "coordinate": axis.coordinate, "intersection_count": axis.intersection_count}


def _detection(item) -> ClassifiedSectionDetection:
    return ClassifiedSectionDetection(
        pdf_page=item.page_number, sheet_number=None, sheet_title=None, sheet_kind=None, sheet_subject=None,
        level=None, segment=None, area=[], original_text=item.original_text, normalized_section=item.normalized_text,
        section_family=item.section_family, raw_x=item.x, raw_y=item.y, raw_width=item.width, raw_height=item.height,
        confidence=item.confidence, duplicate_count=item.duplicate_count, record_mode="deduplicated", warnings=[],
    )


def _localization_summary(result) -> Dict:
    return {
        "total_section_detections": result.total_section_detections,
        "complete_bay": result.detections_with_complete_bay,
        "on_axis": result.detections_on_axes,
        "outside_grid": result.outside_grid_count,
        "ambiguous": result.ambiguous_detection_count,
        "unlocalized": result.unlocalized_detection_count,
        "grid_system_distribution": _grid_system_distribution(result),
    }


def _grid_system_distribution(result) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in result.detections:
        key = "PRIMARY" if item.grid_system_id == result.primary_grid_system_id else "SECONDARY" if item.grid_system_id else "NONE"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _git_revision() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _safe_error(exc: Exception, source_path: str) -> str:
    """Keep ordinary reports free of configured local source paths."""

    return str(exc).replace(source_path, "<redacted source>")
