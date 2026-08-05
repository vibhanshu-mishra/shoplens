"""JSON, Markdown, and CSV validation reports from one result contract."""

import csv
import json
from pathlib import Path
from typing import Any, Dict

from .models import ValidationSuiteResult

CSV_FIELDS = (
    "file_name", "overall_status", "pdf_health_status", "positioned_text_count",
    "sheet_list_status", "declared_sheet_count", "title_block_status", "page_count",
    "identified_page_count", "reconciliation_status", "match_count",
    "classification_status", "classified_count", "unknown_count", "warning_count",
    "error_count", "runtime_seconds",
)


def write_json(path: Path, result: ValidationSuiteResult, debug: bool = False) -> None:
    _prepare(path)
    path.write_text(json.dumps(result.to_dict(debug=debug), indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, result: ValidationSuiteResult) -> None:
    _prepare(path)
    lines = [
        "# ShopLens Validation Suite", "",
        f"- Evaluation PDFs: {result.pdf_count}",
        f"- Packages passed: {result.packages_passed}",
        f"- Packages with warnings: {result.packages_with_warnings}",
        f"- Packages failed: {result.packages_failed}",
        f"- Runtime: {result.runtime_seconds:.3f} seconds", "",
        "Unreviewed extraction results indicate execution and structural completeness only; "
        "they are not human verification of drawing geometry.", "",
        "## Package summary", "",
        "| Package | Overall | PDF health | Sheet List | Title blocks | Reconciliation | Classification | Runtime |",
        "|---|---|---|---|---|---|---|---:|",
    ]
    for package in result.package_results:
        stages = {stage.stage_name: stage for stage in package.stages}
        lines.append(
            f"| {package.relative_path} | {package.overall_status.value} | "
            f"{_status(stages, 'PDF_HEALTH')} | {_status(stages, 'SHEET_LIST')} | "
            f"{_status(stages, 'TITLE_BLOCKS')} | {_status(stages, 'SHEET_RECONCILIATION')} | "
            f"{_status(stages, 'PACKAGE_CLASSIFICATION')} | {package.runtime_seconds:.3f}s |"
        )
    lines.extend(["", "## Stage pass rates", ""])
    for name, counts in result.stage_summary.items():
        successful = counts.get("PASS", 0) + counts.get("PASS_WITH_WARNINGS", 0)
        lines.append(f"- {name}: {successful}/{result.pdf_count} completed without failure")
    lines.extend(["", "## Failures and warnings", ""])
    issues = False
    for package in result.package_results:
        if not package.errors and not package.warnings:
            continue
        issues = True
        lines.append(f"### {package.relative_path}")
        lines.extend(f"- Error: {value}" for value in package.errors)
        lines.extend(f"- Warning: {value}" for value in package.warnings)
        lines.append("")
    if not issues:
        lines.append("None.")
    lines.extend(["", "## Environment", ""])
    for key, value in sorted(result.environment.items()):
        lines.append(f"- {key}: {value}")
    if result.comparison:
        lines.extend(["", "## Baseline comparison", ""])
        for item in result.comparison.get("package_changes", []):
            lines.append(f"- {item['change']}: {item['relative_path']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(path: Path, result: ValidationSuiteResult) -> None:
    _prepare(path)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for package in result.package_results:
            writer.writerow(_csv_row(package))


def _csv_row(package) -> Dict[str, Any]:
    stages = {stage.stage_name: stage for stage in package.stages}
    health = stages.get("PDF_HEALTH")
    sheet = stages.get("SHEET_LIST")
    title = stages.get("TITLE_BLOCKS")
    reconciliation = stages.get("SHEET_RECONCILIATION")
    classification = stages.get("PACKAGE_CLASSIFICATION")
    return {
        "file_name": package.relative_path, "overall_status": package.overall_status.value,
        "pdf_health_status": health.status.value if health else "",
        "positioned_text_count": _metric(health, "positioned_text_item_count"),
        "sheet_list_status": sheet.status.value if sheet else "",
        "declared_sheet_count": _metric(sheet, "declared_sheet_count"),
        "title_block_status": title.status.value if title else "",
        "page_count": _metric(title, "page_count"),
        "identified_page_count": _metric(title, "identified_page_count"),
        "reconciliation_status": reconciliation.status.value if reconciliation else "",
        "match_count": _metric(reconciliation, "match_count"),
        "classification_status": classification.status.value if classification else "",
        "classified_count": _metric(classification, "classified_sheet_count"),
        "unknown_count": _metric(classification, "unknown_sheet_count"),
        "warning_count": len(package.warnings), "error_count": len(package.errors),
        "runtime_seconds": f"{package.runtime_seconds:.6f}",
    }


def _metric(stage, key):
    return stage.metrics.get(key, "") if stage else ""


def _status(stages, name):
    return stages[name].status.value if name in stages else "-"


def _prepare(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
