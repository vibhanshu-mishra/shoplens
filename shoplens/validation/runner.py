"""Failure-isolated local validation suite runner."""

import fnmatch
import importlib.metadata
import platform
import signal
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from shoplens.classification import build_package_index
from shoplens.extraction import extract_positioned_text, get_pdf_page_count
from shoplens.sheets import extract_sheet_list
from shoplens.title_blocks import extract_title_blocks, reconcile_sheets
from shoplens.title_blocks.models import ReconciliationStatus

from .models import (
    ReviewStatus,
    ValidationPackageResult,
    ValidationStageResult,
    ValidationStatus,
    ValidationSuiteResult,
)

VALIDATION_VERSION = "1.0"


class StageTimeout(RuntimeError):
    pass


@contextmanager
def _timeout(seconds: Optional[float]):
    if not seconds or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)

    def handle_timeout(_signum, _frame):
        raise StageTimeout(f"stage exceeded {seconds:g} seconds")

    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def discover_pdfs(
    root: Path, include_patterns: Sequence[str] = ("*.pdf",),
    exclude_patterns: Sequence[str] = (), selected_files: Sequence[str] = (),
    max_files: Optional[int] = None,
) -> List[Path]:
    selected = {value.replace("\\", "/") for value in selected_files}
    values = []
    for path in root.rglob("*") if root.exists() else []:
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        relative = path.relative_to(root).as_posix()
        if selected and relative not in selected and path.name not in selected:
            continue
        if include_patterns and not any(
            fnmatch.fnmatch(relative.casefold(), pattern.casefold())
            or fnmatch.fnmatch(path.name.casefold(), pattern.casefold())
            for pattern in include_patterns
        ):
            continue
        if any(
            fnmatch.fnmatch(relative.casefold(), pattern.casefold())
            or fnmatch.fnmatch(path.name.casefold(), pattern.casefold())
            for pattern in exclude_patterns
        ):
            continue
        values.append(path)
    values.sort(key=lambda path: path.relative_to(root).as_posix().casefold())
    return values[:max_files] if max_files is not None else values


def run_validation_suite(
    evaluation_root: Path, files: Optional[Sequence[Path]] = None,
    include_patterns: Sequence[str] = ("*.pdf",), exclude_patterns: Sequence[str] = (),
    selected_files: Sequence[str] = (), max_files: Optional[int] = None,
    timeout_per_stage: Optional[float] = None, stop_on_error: bool = False,
) -> ValidationSuiteResult:
    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()
    root = evaluation_root.resolve()
    pdfs = [path.resolve() for path in files] if files is not None else discover_pdfs(
        root, include_patterns, exclude_patterns, selected_files, max_files
    )
    packages = []
    for path in pdfs:
        package = run_validation_package(path, root, timeout_per_stage)
        packages.append(package)
        if stop_on_error and package.overall_status in {ValidationStatus.FAIL, ValidationStatus.TIMEOUT}:
            break
    completed_wall = datetime.now(timezone.utc)
    warnings = []
    environment = _environment_metadata()
    if environment.get("working_tree_dirty"):
        warnings.append("WORKING_TREE_DIRTY")
    if not pdfs:
        warnings.append("NO_PDFS_FOUND")
    summary: Dict[str, Counter] = {}
    for package in packages:
        for stage in package.stages:
            summary.setdefault(stage.stage_name, Counter())[stage.status.value] += 1
    return ValidationSuiteResult(
        VALIDATION_VERSION, started_wall.isoformat(), completed_wall.isoformat(),
        time.monotonic() - started, root.name, str(root), len(packages),
        sum(item.overall_status == ValidationStatus.PASS for item in packages),
        sum(item.overall_status in {ValidationStatus.FAIL, ValidationStatus.TIMEOUT} for item in packages),
        sum(item.overall_status == ValidationStatus.PASS_WITH_WARNINGS for item in packages),
        {name: dict(sorted(counts.items())) for name, counts in sorted(summary.items())},
        packages, warnings, environment,
    )


def run_validation_package(
    path: Path, root: Path, timeout_per_stage: Optional[float] = None,
) -> ValidationPackageResult:
    path = path.resolve()
    root = root.resolve()
    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("validation package must be inside the evaluation root") from exc
    started = time.monotonic()
    stages: List[ValidationStageResult] = []
    context: Dict[str, Any] = {}

    health, output = _run_stage(
        "PDF_HEALTH", lambda: _pdf_health(path), timeout_per_stage,
        ReviewStatus.NOT_REQUIRED, path, root,
    )
    stages.append(health)
    if output:
        context.update(output)

    sheet, declared = _run_stage(
        "SHEET_LIST", lambda: _sheet_list(path, context), timeout_per_stage,
        ReviewStatus.NOT_REVIEWED, path, root,
        skip_reason=None if context.get("items") is not None else "PDF_HEALTH_DID_NOT_PRODUCE_TEXT",
    )
    stages.append(sheet)
    if declared is not None:
        context["declared"] = declared

    title, actual = _run_stage(
        "TITLE_BLOCKS", lambda: _title_blocks(path, context), timeout_per_stage,
        ReviewStatus.NOT_REVIEWED, path, root,
        skip_reason=None if context.get("items") is not None else "PDF_HEALTH_DID_NOT_PRODUCE_TEXT",
    )
    stages.append(title)
    if actual is not None:
        context["actual"] = actual

    can_reconcile = context.get("declared") is not None and context.get("actual") is not None
    reconciliation_stage, reconciliation = _run_stage(
        "SHEET_RECONCILIATION", lambda: _reconciliation(context), timeout_per_stage,
        ReviewStatus.NOT_REVIEWED, path, root,
        skip_reason=None if can_reconcile else "SHEET_LIST_OR_TITLE_BLOCKS_UNAVAILABLE",
    )
    stages.append(reconciliation_stage)
    if reconciliation is not None:
        context["reconciliation"] = reconciliation

    classification_stage, _ = _run_stage(
        "PACKAGE_CLASSIFICATION", lambda: _classification(context), timeout_per_stage,
        ReviewStatus.NOT_REVIEWED, path, root,
        skip_reason=None if reconciliation is not None else "RECONCILIATION_UNAVAILABLE",
    )
    stages.append(classification_stage)
    warnings = [warning for stage in stages for warning in stage.warnings]
    errors = [error for stage in stages for error in stage.errors]
    if any(stage.status == ValidationStatus.TIMEOUT for stage in stages):
        overall = ValidationStatus.TIMEOUT
    elif any(stage.status == ValidationStatus.FAIL for stage in stages):
        overall = ValidationStatus.FAIL
    elif any(stage.status in {ValidationStatus.PASS_WITH_WARNINGS, ValidationStatus.SKIPPED} for stage in stages):
        overall = ValidationStatus.PASS_WITH_WARNINGS
    else:
        overall = ValidationStatus.PASS
    return ValidationPackageResult(
        path.name, relative_path, str(path), path.stat().st_size,
        time.monotonic() - started, overall, stages,
        list(dict.fromkeys(warnings)), list(dict.fromkeys(errors)),
    )


def _run_stage(
    name: str, operation: Callable[[], Tuple[Dict[str, Any], Any]],
    timeout_seconds: Optional[float], review: ReviewStatus, path: Path, root: Path,
    skip_reason: Optional[str] = None,
) -> Tuple[ValidationStageResult, Any]:
    if skip_reason:
        return ValidationStageResult(name, ValidationStatus.SKIPPED, 0.0, {},
                                     [skip_reason], [], review), None
    started = time.monotonic()
    try:
        with _timeout(timeout_seconds):
            metrics, output = operation()
        warnings = list(metrics.pop("_warnings", []))
        status_override = metrics.pop("_status", None)
        status = (
            ValidationStatus(status_override)
            if status_override is not None
            else ValidationStatus.PASS_WITH_WARNINGS if warnings else ValidationStatus.PASS
        )
        return ValidationStageResult(name, status, time.monotonic() - started,
                                     metrics, warnings, [], review), output
    except StageTimeout as exc:
        return ValidationStageResult(name, ValidationStatus.TIMEOUT, time.monotonic() - started,
                                     {}, [], [_safe_error(exc, path, root)], review), None
    except Exception as exc:
        return ValidationStageResult(name, ValidationStatus.FAIL, time.monotonic() - started,
                                     {}, [], [_safe_error(exc, path, root)], review), None


def _pdf_health(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    page_count = get_pdf_page_count(path)
    items = extract_positioned_text(path)
    warnings = []
    if page_count == 0:
        warnings.append("ZERO_PAGE_PDF")
    if not items:
        warnings.append("NO_POSITIONED_TEXT_ITEMS")
    metrics = {
        "file_exists": path.exists(), "file_opens": True,
        "positioned_text_api_worked": True, "positioned_text_item_count": len(items),
        "page_count": page_count, "parser_warning_count": 0, "_warnings": warnings,
    }
    return metrics, {"items": items, "page_count": page_count}


def _sheet_list(path: Path, context: Dict[str, Any]):
    page_count = context["page_count"]
    pages = list(range(1, min(5, page_count) + 1))
    result = extract_sheet_list(
        [item for item in context["items"] if int(item.page) in pages], str(path), pages
    )
    warnings = list(result.warnings)
    if not result.sheet_list_pages:
        warnings.append("SHEET_LIST_NOT_FOUND")
    unique_declared_count = len({entry.sheet_number for entry in result.entries})
    mismatch = result.declared_total is not None and result.declared_total != unique_declared_count
    if mismatch:
        warnings.append("DECLARED_TOTAL_MISMATCH")
    return {
        "sheet_list_found": bool(result.sheet_list_pages),
        "sheet_list_pages": result.sheet_list_pages,
        "declared_sheet_count": len(result.entries), "declared_total": result.declared_total,
        "count_mismatch": mismatch, "_warnings": warnings,
    }, result


def _title_blocks(path: Path, context: Dict[str, Any]):
    pages = list(range(1, context["page_count"] + 1))
    declared_entries = context.get("declared").entries if context.get("declared") else []
    declared = context.get("declared")
    result = extract_title_blocks(
        context["items"],
        str(path),
        pages,
        declared_entries,
        declared_total=declared.declared_total if declared else None,
        sheet_list_pages=declared.sheet_list_pages if declared else (),
    )
    warnings = list(result.warnings)
    if result.identified_page_count == 0:
        warnings.append("NO_TITLE_BLOCKS_IDENTIFIED")
    if result.unidentified_pages:
        warnings.append(f"UNIDENTIFIED_TITLE_BLOCK_PAGES: {len(result.unidentified_pages)}")
    if result.low_confidence_pages:
        warnings.append(f"LOW_CONFIDENCE_TITLE_BLOCK_PAGES: {len(result.low_confidence_pages)}")
    if result.duplicate_sheet_numbers:
        warnings.append(f"DUPLICATE_TITLE_BLOCK_SHEET_NUMBERS: {len(result.duplicate_sheet_numbers)}")
    return {
        "page_count": result.total_pdf_pages_processed,
        "identified_page_count": result.identified_page_count,
        "intentional_non_title_block_page_count": len(
            getattr(result, "intentional_non_title_block_pages", [])
        ),
        "unidentified_page_count": len(result.unidentified_pages),
        "low_confidence_page_count": len(result.low_confidence_pages),
        "layout_count": len(result.layouts_discovered),
        "duplicate_sheet_number_count": len(result.duplicate_sheet_numbers),
        "_warnings": warnings,
    }, result


def _reconciliation(context: Dict[str, Any]):
    result = reconcile_sheets(context["declared"], context["actual"])
    counts = Counter(entry.status.value for entry in result.entries)
    no_declared_index = result.declared_index_status.value == "NO_DECLARED_SHEET_LIST"
    warnings = [
        warning for warning in result.warnings
        if warning not in {"NO_DECLARED_SHEET_LIST", "TITLE_BLOCK_ONLY_INDEX"}
    ]
    warning_counts = (
        ("TITLE_MISMATCHES", counts[ReconciliationStatus.TITLE_MISMATCH.value]),
        ("MISSING_DECLARED_SHEETS", len(result.missing_declared_sheets)),
        ("UNDECLARED_ACTUAL_SHEETS", len(result.undeclared_actual_sheets)),
        ("DUPLICATE_ACTUAL_SHEET_NUMBERS", len(result.duplicate_actual_sheet_numbers)),
        ("UNIDENTIFIED_RECONCILIATION_PAGES", len(result.unidentified_pages)),
    )
    warnings.extend(f"{name}: {count}" for name, count in warning_counts if count)
    return {
        "declared_sheet_count": result.declared_sheet_count,
        "actual_identified_sheet_count": result.identified_page_count,
        "match_count": counts[ReconciliationStatus.MATCH.value],
        "title_variation_count": counts[ReconciliationStatus.TITLE_VARIATION.value],
        "title_mismatch_count": counts[ReconciliationStatus.TITLE_MISMATCH.value],
        "missing_sheet_count": len(result.missing_declared_sheets),
        "undeclared_sheet_count": len(result.undeclared_actual_sheets),
        "duplicate_sheet_number_count": len(result.duplicate_actual_sheet_numbers),
        "unidentified_page_count": len(result.unidentified_pages),
        "declared_index_status": result.declared_index_status.value,
        "title_block_only_sheet_count": sum(
            entry.record_source.value == "TITLE_BLOCK_ONLY" for entry in result.entries
        ),
        "_status": "NOT_APPLICABLE" if no_declared_index else None,
        "_warnings": warnings,
    }, result


def _classification(context: Dict[str, Any]):
    result = build_package_index(context["reconciliation"])
    low_confidence = sum(sheet.classification_confidence < 0.70 for sheet in result.sheets)
    conflicts = sum(
        warning in {"MULTIPLE_PRIMARY_RULES", "LEVEL_CONFLICT", "SEGMENT_CONFLICT"}
        for sheet in result.sheets for warning in sheet.warnings
    )
    warnings = list(result.warnings)
    if result.unknown_sheet_count:
        warnings.append(f"UNKNOWN_SHEET_CLASSIFICATIONS: {result.unknown_sheet_count}")
    if low_confidence:
        warnings.append(f"LOW_CONFIDENCE_CLASSIFICATIONS: {low_confidence}")
    if conflicts:
        warnings.append(f"CLASSIFICATION_CONFLICT_WARNINGS: {conflicts}")
    return {
        "indexed_sheet_count": result.indexed_sheet_count,
        "classified_sheet_count": result.classified_sheet_count,
        "unknown_sheet_count": result.unknown_sheet_count,
        "low_confidence_classification_count": low_confidence,
        "conflict_warning_count": conflicts,
        "counts_by_kind": result.counts_by_kind,
        "counts_by_subject": result.counts_by_subject,
        "_warnings": warnings,
    }, result


def _safe_error(exc: Exception, path: Path, root: Path) -> str:
    return str(exc).replace(str(path), path.name).replace(str(root), root.name)


def _environment_metadata() -> Dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    metadata: Dict[str, Any] = {
        "python_version": platform.python_version(), "platform": platform.platform(),
        "shoplens_package_version": _package_version(),
        "git_commit": None, "git_branch": None, "working_tree_dirty": None,
    }
    try:
        metadata["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
            capture_output=True, check=True, timeout=5,
        ).stdout.strip()
        metadata["git_branch"] = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repository, text=True,
            capture_output=True, check=True, timeout=5,
        ).stdout.strip()
        metadata["working_tree_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repository, text=True,
            capture_output=True, check=True, timeout=5,
        ).stdout.strip())
        metadata["git_available"] = True
    except (OSError, subprocess.SubprocessError):
        metadata["git_available"] = False
    return metadata


def _package_version() -> Optional[str]:
    try:
        return importlib.metadata.version("pdf-inspector")
    except importlib.metadata.PackageNotFoundError:
        return None
