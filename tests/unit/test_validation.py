"""Local validation discovery, isolation, reporting, comparison, and CLI tests."""

import csv
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.validation.compare import compare_reports
from shoplens.validation.config import load_config
from shoplens.validation.models import (
    ReviewStatus,
    ValidationPackageResult,
    ValidationStageResult,
    ValidationStatus,
    ValidationSuiteResult,
)
from shoplens.validation.reporting import write_csv, write_json, write_markdown
from shoplens.validation.runner import (
    _classification,
    _reconciliation,
    _run_stage,
    _title_blocks,
    discover_pdfs,
    run_validation_package,
    run_validation_suite,
)
from shoplens.title_blocks.models import ReconciliationStatus
from shoplens.title_blocks.models import DeclaredIndexStatus, SheetRecordSource


def stage(name, status=ValidationStatus.PASS, metrics=None, warnings=None, errors=None):
    return ValidationStageResult(
        name, status, 0.01, metrics or {}, warnings or [], errors or [],
        ReviewStatus.NOT_REQUIRED if name == "PDF_HEALTH" else ReviewStatus.NOT_REVIEWED,
    )


def package(name="a.pdf", status=ValidationStatus.PASS, stages=None):
    values = stages or [stage("PDF_HEALTH")]
    return ValidationPackageResult(
        name, name, f"/confidential/{name}", 100, 0.2, status, values,
        [warning for value in values for warning in value.warnings],
        [error for value in values for error in value.errors],
    )


def suite(packages=None):
    values = packages or [package()]
    return ValidationSuiteResult(
        "1.0", "start", "end", 1.0, "evaluation", "/confidential/evaluation",
        len(values), sum(item.overall_status == ValidationStatus.PASS for item in values),
        sum(item.overall_status == ValidationStatus.FAIL for item in values),
        sum(item.overall_status == ValidationStatus.PASS_WITH_WARNINGS for item in values),
        {"PDF_HEALTH": {"PASS": len(values)}}, values, [],
        {"git_commit": None, "working_tree_dirty": False},
    )


class DiscoveryAndRunnerTests(unittest.TestCase):
    def test_multiple_pdfs_deterministic_filters_and_maximum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            for name in ("z.pdf", "A.PDF", "ignore.pdf", "note.txt"):
                (root / name).write_bytes(b"x")
            (root / "nested" / "b.pdf").write_bytes(b"x")
            values = discover_pdfs(root, exclude_patterns=["ignore.pdf"])
            self.assertEqual([path.name for path in values], ["A.PDF", "b.pdf", "z.pdf"])
            self.assertEqual(discover_pdfs(root, selected_files=["b.pdf"])[0].name, "b.pdf")
            self.assertEqual(len(discover_pdfs(root, max_files=1)), 1)

    def test_pass_warning_failure_and_stop_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name in ("a.pdf", "b.pdf", "c.pdf"):
                path = root / name
                path.write_bytes(b"x")
                paths.append(path)
            results = [
                package("a.pdf"),
                package("b.pdf", ValidationStatus.PASS_WITH_WARNINGS),
                package("c.pdf", ValidationStatus.FAIL),
            ]
            with patch("shoplens.validation.runner.run_validation_package", side_effect=results):
                result = run_validation_suite(root, files=paths)
            self.assertEqual((result.packages_passed, result.packages_with_warnings, result.packages_failed), (1, 1, 1))
            with patch("shoplens.validation.runner.run_validation_package", side_effect=[results[2], results[0]]):
                stopped = run_validation_suite(root, files=paths, stop_on_error=True)
            self.assertEqual(stopped.pdf_count, 1)

    def test_stage_failure_and_skip_contract(self):
        failed, output = _run_stage(
            "SHEET_LIST", lambda: (_ for _ in ()).throw(ValueError("bad stage")),
            None, ReviewStatus.NOT_REVIEWED, Path("a.pdf"), Path("."),
        )
        self.assertEqual(failed.status, ValidationStatus.FAIL)
        self.assertIsNone(output)
        skipped, _ = _run_stage(
            "PACKAGE_CLASSIFICATION", lambda: ({}, None), None,
            ReviewStatus.NOT_REVIEWED, Path("a.pdf"), Path("."), "DEPENDENCY_FAILED",
        )
        self.assertEqual(skipped.status, ValidationStatus.SKIPPED)

    def test_sheet_list_failure_does_not_abort_title_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "drawing.pdf"
            path.write_bytes(b"pdf")
            title_result = object()
            with patch("shoplens.validation.runner._pdf_health", return_value=({}, {"items": [], "page_count": 1})), \
                 patch("shoplens.validation.runner._sheet_list", side_effect=ValueError("sheet failed")), \
                 patch("shoplens.validation.runner._title_blocks", return_value=({"page_count": 1}, title_result)) as title:
                result = run_validation_package(path, root)
        stages = {value.stage_name: value for value in result.stages}
        self.assertEqual(stages["SHEET_LIST"].status, ValidationStatus.FAIL)
        self.assertEqual(stages["TITLE_BLOCKS"].status, ValidationStatus.PASS)
        self.assertEqual(stages["SHEET_RECONCILIATION"].status, ValidationStatus.SKIPPED)
        title.assert_called_once()

    def test_timeout_is_recorded(self):
        timed, _ = _run_stage(
            "PDF_HEALTH", lambda: (time.sleep(0.05), None), 0.005,
            ReviewStatus.NOT_REQUIRED, Path("a.pdf"), Path("."),
        )
        self.assertEqual(timed.status, ValidationStatus.TIMEOUT)
        self.assertGreaterEqual(timed.runtime_seconds, 0)

    def test_no_pdfs_and_git_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("shoplens.validation.runner.subprocess.run", side_effect=OSError("no git")):
            result = run_validation_suite(Path(directory))
        self.assertEqual(result.pdf_count, 0)
        self.assertIn("NO_PDFS_FOUND", result.warnings)
        self.assertFalse(result.environment.get("git_available", True))

    def test_dirty_tree_warning(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("shoplens.validation.runner._environment_metadata", return_value={"working_tree_dirty": True}):
            result = run_validation_suite(Path(directory))
        self.assertIn("WORKING_TREE_DIRTY", result.warnings)

    def test_incomplete_structural_metrics_produce_warnings(self):
        title_result = SimpleNamespace(
            total_pdf_pages_processed=2, identified_page_count=0,
            unidentified_pages=[1, 2], low_confidence_pages=[1],
            layouts_discovered=[], duplicate_sheet_numbers=["S1"], warnings=[],
        )
        with patch("shoplens.validation.runner.extract_title_blocks", return_value=title_result):
            metrics, _ = _title_blocks(
                Path("drawing.pdf"), {"page_count": 2, "items": [], "declared": None}
            )
        self.assertIn("NO_TITLE_BLOCKS_IDENTIFIED", metrics["_warnings"])
        self.assertIn("UNIDENTIFIED_TITLE_BLOCK_PAGES: 2", metrics["_warnings"])
        self.assertIn("LOW_CONFIDENCE_TITLE_BLOCK_PAGES: 1", metrics["_warnings"])
        self.assertEqual(metrics["intentional_non_title_block_page_count"], 0)

        reconciliation = SimpleNamespace(
            entries=[SimpleNamespace(
                status=ReconciliationStatus.TITLE_MISMATCH,
                record_source=SheetRecordSource.DECLARED_RECONCILIATION,
            )],
            declared_sheet_count=1, identified_page_count=0,
            missing_declared_sheets=["S1"], undeclared_actual_sheets=[],
            duplicate_actual_sheet_numbers=[], unidentified_pages=[1], warnings=[],
            declared_index_status=DeclaredIndexStatus.AVAILABLE,
        )
        with patch("shoplens.validation.runner.reconcile_sheets", return_value=reconciliation):
            metrics, _ = _reconciliation({"declared": object(), "actual": object()})
        self.assertIn("TITLE_MISMATCHES: 1", metrics["_warnings"])
        self.assertIn("MISSING_DECLARED_SHEETS: 1", metrics["_warnings"])
        self.assertIn("UNIDENTIFIED_RECONCILIATION_PAGES: 1", metrics["_warnings"])

    def test_classification_quality_metrics_produce_warnings(self):
        result = SimpleNamespace(
            sheets=[SimpleNamespace(
                classification_confidence=0.5, warnings=["LEVEL_CONFLICT"]
            )],
            indexed_sheet_count=1, classified_sheet_count=0, unknown_sheet_count=1,
            counts_by_kind={}, counts_by_subject={}, warnings=[],
        )
        with patch("shoplens.validation.runner.build_package_index", return_value=result):
            metrics, _ = _classification({"reconciliation": object()})
        self.assertIn("UNKNOWN_SHEET_CLASSIFICATIONS: 1", metrics["_warnings"])
        self.assertIn("LOW_CONFIDENCE_CLASSIFICATIONS: 1", metrics["_warnings"])
        self.assertIn("CLASSIFICATION_CONFLICT_WARNINGS: 1", metrics["_warnings"])

    def test_no_declared_index_is_not_applicable_but_remains_indexable(self):
        reconciliation = SimpleNamespace(
            entries=[SimpleNamespace(
                status=ReconciliationStatus.TITLE_BLOCK_ONLY_INDEX,
                record_source=SheetRecordSource.TITLE_BLOCK_ONLY,
            )],
            declared_sheet_count=0, identified_page_count=1,
            missing_declared_sheets=[], undeclared_actual_sheets=[],
            duplicate_actual_sheet_numbers=[], unidentified_pages=[],
            warnings=["NO_DECLARED_SHEET_LIST", "TITLE_BLOCK_ONLY_INDEX"],
            declared_index_status=DeclaredIndexStatus.NO_DECLARED_SHEET_LIST,
        )
        with patch("shoplens.validation.runner.reconcile_sheets", return_value=reconciliation):
            metrics, result = _reconciliation({"declared": object(), "actual": object()})
        self.assertIs(result, reconciliation)
        self.assertEqual(metrics["_status"], "NOT_APPLICABLE")
        self.assertEqual(metrics["title_block_only_sheet_count"], 1)
        self.assertEqual(metrics["_warnings"], [])


class ReportingAndComparisonTests(unittest.TestCase):
    def test_json_redacts_absolute_paths_and_debug_reveals_them(self):
        result = suite()
        normal = result.to_dict()
        self.assertNotIn("evaluation_root_path", normal)
        self.assertNotIn("source_file_path", normal["package_results"][0])
        self.assertIn("source_file_path", result.to_dict(debug=True)["package_results"][0])

    def test_json_markdown_and_csv_outputs(self):
        result = suite()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "report.json", result)
            write_markdown(root / "report.md", result)
            write_csv(root / "report.csv", result)
            self.assertEqual(json.loads((root / "report.json").read_text())["pdf_count"], 1)
            self.assertIn("Unreviewed", (root / "report.md").read_text())
            rows = list(csv.DictReader((root / "report.csv").open()))
            self.assertEqual(rows[0]["file_name"], "a.pdf")

    def test_empty_csv_still_has_header(self):
        result = suite([])
        result.package_results = []
        result.pdf_count = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            write_csv(path, result)
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_baseline_regression_improvement_new_and_removed(self):
        baseline = suite([
            package("regress.pdf", stages=[stage("PACKAGE_CLASSIFICATION", metrics={"unknown_sheet_count": 0})]),
            package("recover.pdf", ValidationStatus.FAIL), package("removed.pdf"),
        ]).to_dict()
        current = suite([
            package("regress.pdf", stages=[stage("PACKAGE_CLASSIFICATION", metrics={"unknown_sheet_count": 2})]),
            package("recover.pdf"), package("new.pdf"),
        ]).to_dict()
        changes = {item["relative_path"]: item["change"] for item in compare_reports(current, baseline)["package_changes"]}
        self.assertEqual(changes, {
            "new.pdf": "NEW_PACKAGE", "recover.pdf": "IMPROVEMENT",
            "regress.pdf": "REGRESSION", "removed.pdf": "REMOVED_PACKAGE",
        })

    def test_objective_improvement_takes_precedence_over_warning_growth(self):
        baseline = suite([package(
            "drawing.pdf",
            stages=[stage("TITLE_BLOCKS", metrics={"identified_page_count": 1})],
        )]).to_dict()
        current = suite([package(
            "drawing.pdf",
            stages=[stage(
                "TITLE_BLOCKS",
                metrics={"identified_page_count": 2},
                warnings=["LOW_CONFIDENCE_TITLE_BLOCK_PAGES: 1"],
            )],
        )]).to_dict()

        change = compare_reports(current, baseline)["package_changes"][0]

        self.assertEqual(change["change"], "IMPROVEMENT")
        self.assertIn("warning_count", [detail["field"] for detail in change["details"]])

    def test_index_availability_warnings_are_informational(self):
        baseline = suite([package("drawing.pdf")]).to_dict()
        current = suite([package(
            "drawing.pdf",
            stages=[stage(
                "PDF_HEALTH",
                warnings=["NO_DECLARED_SHEET_LIST", "TITLE_BLOCK_ONLY_INDEX"],
            )],
        )]).to_dict()

        change = compare_reports(current, baseline)["package_changes"][0]

        self.assertEqual(change["change"], "UNCHANGED")
        self.assertNotIn("warning_count", [detail["field"] for detail in change["details"]])

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"max_files": -1}')
            with self.assertRaises(ValueError):
                load_config(path)


class ValidationCliTests(unittest.TestCase):
    def test_cli_writes_outputs_and_prints_summary(self):
        result = suite()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with patch.object(cli, "run_validation_suite", return_value=result), redirect_stdout(output):
                status = cli.main([
                    "validate-suite", str(root), "--json", str(root / "out.json"),
                    "--markdown", str(root / "out.md"), "--csv", str(root / "out.csv"),
                ])
            self.assertEqual(status, 0)
            self.assertIn("Evaluation PDFs: 1", output.getvalue())
            self.assertTrue((root / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
