"""Synthetic privacy-safe coverage for geometry regression reporting."""

import json
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from shoplens import cli
from shoplens.geometry_validation import (
    GeometryCaseConfig,
    GeometryValidationConfig,
    compare_geometry_reports,
    load_geometry_config,
    run_geometry_validation,
    write_geometry_baseline,
    write_geometry_csv,
    write_geometry_json,
    write_geometry_markdown,
    validate_geometry_baseline,
)
from shoplens.geometry_validation.models import GeometryValidationResult
from shoplens.geometry_validation.compare import _minimum_cost_pairs
from shoplens.geometry_validation.reporting import _csv_safe_cell
from shoplens.geometry_validation.runner import _git_revision


def axis(orientation, label, coordinate, intersections=2):
    return {
        "orientation": orientation,
        "label": label,
        "coordinate": coordinate,
        "intersection_count": intersections,
    }


def system(system_id, horizontal=None, vertical=None):
    return {
        "grid_system_id": system_id,
        "horizontal_axes": horizontal or [],
        "vertical_axes": vertical or [],
    }


def case(case_id="case-001", *, horizontal=None, vertical=None, systems=1, secondary=None, localization=None, warnings=None):
    return {
        "case_id": case_id,
        "execution_status": "PASS",
        "selected_page": 1,
        "grid": {
            "grid_system_count": systems,
            "primary_grid_system_id": "PRIMARY",
            "horizontal_axes": horizontal if horizontal is not None else [axis("HORIZONTAL", "1", 100.0)],
            "vertical_axes": vertical if vertical is not None else [axis("VERTICAL", "A", 200.0)],
            "secondary_systems": secondary if secondary is not None else [],
            "unassigned_label_count": 0,
            "rejected_candidate_count": 0,
            "warnings": warnings or [],
        },
        "localization": localization if localization is not None else {
            "total_section_detections": 4,
            "complete_bay": 2,
            "on_axis": 1,
            "outside_grid": 1,
            "ambiguous": 0,
            "unlocalized": 0,
            "grid_system_distribution": {"PRIMARY": 4},
        },
    }


def report(*cases):
    return {"schema_version": 1, "case_results": list(cases)}


class GeometryComparisonTests(unittest.TestCase):
    def compare(self, current, baseline, tolerance=2.0):
        return compare_geometry_reports(report(current), report(baseline), tolerance)

    def test_unchanged_axis_and_within_tolerance_move_are_unchanged(self):
        current = case(horizontal=[axis("HORIZONTAL", "1", 101.5)])
        result = self.compare(current, case())
        self.assertEqual(result["summary"], {"UNCHANGED": 1})

    def test_lost_baseline_axis_is_regression(self):
        result = self.compare(case(horizontal=[]), case())
        self.assertEqual(result["case_changes"][0]["change"], "REGRESSION")
        self.assertEqual(result["case_changes"][0]["details"][0]["kind"], "LOST_AXIS")

    def test_new_axis_is_review_required(self):
        current = case(horizontal=[axis("HORIZONTAL", "1", 100), axis("HORIZONTAL", "2", 200)])
        result = self.compare(current, case())
        self.assertEqual(result["case_changes"][0]["change"], "REVIEW_REQUIRED")
        self.assertEqual(result["case_changes"][0]["details"][0]["kind"], "NEW_AXIS")

    def test_axis_move_beyond_tolerance_is_review_required(self):
        result = self.compare(case(horizontal=[axis("HORIZONTAL", "1", 103.0)]), case())
        detail = result["case_changes"][0]["details"][0]
        self.assertEqual(result["case_changes"][0]["change"], "REVIEW_REQUIRED")
        self.assertEqual(detail["kind"], "MOVED_AXIS")

    def test_repeated_labels_match_by_coordinate(self):
        baseline = case(horizontal=[axis("HORIZONTAL", "A", 100), axis("HORIZONTAL", "A", 300)])
        current = case(horizontal=[axis("HORIZONTAL", "A", 301), axis("HORIZONTAL", "A", 101)])
        self.assertEqual(self.compare(current, baseline)["summary"], {"UNCHANGED": 1})

    def test_repeated_label_assignment_minimizes_total_coordinate_distance(self):
        baseline = case(horizontal=[axis("HORIZONTAL", "A", 0), axis("HORIZONTAL", "A", 5)])
        current = case(horizontal=[axis("HORIZONTAL", "A", 4), axis("HORIZONTAL", "A", 6)])
        self.assertEqual(self.compare(current, baseline, tolerance=5.0)["summary"], {"UNCHANGED": 1})

    def test_equal_cost_pairing_preserves_earlier_current_axis(self):
        old = [axis("HORIZONTAL", "A", 0)]
        current = [axis("HORIZONTAL", "A", -1), axis("HORIZONTAL", "A", 1)]
        self.assertEqual(_minimum_cost_pairs(old, current), [(0, 0)])

    def test_equal_cost_repeated_labels_are_deterministic(self):
        old = [axis("HORIZONTAL", "A", coordinate) for coordinate in (0, 10)]
        current = [axis("HORIZONTAL", "A", coordinate) for coordinate in (-1, 1, 9, 11)]
        expected = [(0, 0), (1, 2)]
        for _ in range(10):
            self.assertEqual(_minimum_cost_pairs(old, current), expected)

    def test_minimum_cost_pairs_preserve_old_new_indices_for_all_sizes(self):
        old_more = [axis("HORIZONTAL", "A", value) for value in (10, 20, 30)]
        new_fewer = [axis("HORIZONTAL", "A", value) for value in (11, 31)]
        self.assertEqual(_minimum_cost_pairs(old_more, new_fewer), [(0, 0), (2, 1)])

        old_fewer = [axis("HORIZONTAL", "A", value) for value in (10, 30)]
        new_more = [axis("HORIZONTAL", "A", value) for value in (11, 20, 31)]
        self.assertEqual(_minimum_cost_pairs(old_fewer, new_more), [(0, 0), (1, 2)])

        old_equal = [axis("HORIZONTAL", "A", value) for value in (20, 10)]
        new_equal = [axis("HORIZONTAL", "A", value) for value in (11, 21)]
        self.assertEqual(_minimum_cost_pairs(old_equal, new_equal), [(1, 0), (0, 1)])
        self.assertEqual(_minimum_cost_pairs([], new_equal), [])
        self.assertEqual(_minimum_cost_pairs(old_equal, []), [])

    def test_minimum_cost_pairs_handles_25_axis_groups_polynomially(self):
        for old_count, new_count in ((20, 20), (25, 20), (20, 25)):
            old = [axis("HORIZONTAL", "A", value) for value in range(old_count)]
            new = [axis("HORIZONTAL", "A", value + 0.25) for value in range(new_count)]
            pairs = _minimum_cost_pairs(old, new)
            self.assertEqual(len(pairs), min(old_count, new_count))
            self.assertEqual(pairs, sorted(pairs))

    def test_intersection_grid_system_and_localization_changes_are_reported(self):
        current = case(
            horizontal=[axis("HORIZONTAL", "1", 100, intersections=3)],
            systems=2,
            localization={
                "total_section_detections": 4, "complete_bay": 1, "on_axis": 1,
                "outside_grid": 2, "ambiguous": 0, "unlocalized": 0,
                "grid_system_distribution": {"PRIMARY": 4},
            },
        )
        details = self.compare(current, case())["case_changes"][0]["details"]
        self.assertEqual({detail["kind"] for detail in details}, {
            "INTERSECTION_CHANGE", "GRID_SYSTEM_COUNT_CHANGE", "LOCALIZATION_CHANGE",
        })

    def test_secondary_axes_are_compared_independently_of_primary(self):
        baseline = case(secondary=[system("S-A", [axis("HORIZONTAL", "1", 10), axis("HORIZONTAL", "2", 20)], [axis("VERTICAL", "A", 30)])])
        lost = case(secondary=[system("S-A", [axis("HORIZONTAL", "1", 10)], [axis("VERTICAL", "A", 30)])])
        gained = case(secondary=[system("S-A", [axis("HORIZONTAL", "1", 10), axis("HORIZONTAL", "2", 20), axis("HORIZONTAL", "3", 30)], [axis("VERTICAL", "A", 30)])])
        moved = case(secondary=[system("S-A", [axis("HORIZONTAL", "1", 13), axis("HORIZONTAL", "2", 20)], [axis("VERTICAL", "A", 30)])])
        changed = case(secondary=[system("S-A", [axis("HORIZONTAL", "1", 10, intersections=3), axis("HORIZONTAL", "2", 20)], [axis("VERTICAL", "A", 30)])])
        self.assertEqual(self.compare(lost, baseline)["summary"], {"REGRESSION": 1})
        self.assertEqual(self.compare(gained, baseline)["summary"], {"REVIEW_REQUIRED": 1})
        self.assertEqual(self.compare(moved, baseline)["summary"], {"REVIEW_REQUIRED": 1})
        self.assertEqual(self.compare(changed, baseline)["summary"], {"REVIEW_REQUIRED": 1})

    def test_secondary_ordering_is_not_semantic(self):
        first = system("PAGE_1_SECONDARY_GRID_1", [axis("HORIZONTAL", "1", 10)], [axis("VERTICAL", "A", 30)])
        second = system("PAGE_1_SECONDARY_GRID_2", [axis("HORIZONTAL", "2", 20)], [axis("VERTICAL", "B", 40)])
        self.assertEqual(self.compare(case(secondary=[second, first]), case(secondary=[first, second]))["summary"], {"UNCHANGED": 1})

    def test_secondary_system_addition_and_removal_are_reported(self):
        secondary = system("S-A", [axis("HORIZONTAL", "1", 10)], [axis("VERTICAL", "A", 30)])
        self.assertEqual(self.compare(case(), case(secondary=[secondary]))["summary"], {"REGRESSION": 1})
        self.assertEqual(self.compare(case(secondary=[secondary]), case())["summary"], {"REVIEW_REQUIRED": 1})

    def test_localization_distribution_changes_require_review(self):
        baseline = case(localization={
            "total_section_detections": 1, "complete_bay": 1, "on_axis": 0,
            "outside_grid": 0, "ambiguous": 0, "unlocalized": 0,
            "grid_system_distribution": {"PRIMARY": 1},
        })
        current = case(localization={
            "total_section_detections": 1, "complete_bay": 1, "on_axis": 0,
            "outside_grid": 0, "ambiguous": 0, "unlocalized": 0,
            "grid_system_distribution": {"SECONDARY": 1},
        })
        details = self.compare(current, baseline)["case_changes"][0]["details"]
        self.assertIn("LOCALIZATION_DISTRIBUTION_CHANGE", {item["kind"] for item in details})

    def test_new_removed_and_execution_error_are_isolated(self):
        current = report(case("case-001"), {"case_id": "case-002", "execution_status": "ERROR", "error": "failed"}, case("case-003"))
        baseline = report(case("case-001"), case("case-004"))
        changes = compare_geometry_reports(current, baseline, 2.0)["case_changes"]
        self.assertEqual({item["case_id"]: item["change"] for item in changes}, {
            "case-001": "UNCHANGED", "case-002": "ERROR", "case-003": "NEW_CASE", "case-004": "REMOVED_CASE",
        })


class GeometryConfigAndReportingTests(unittest.TestCase):
    def test_schema_paths_are_redacted_and_baseline_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"schema_version": 1, "cases": [{
                "case_id": "case-001", "pdf": "/private/example.pdf", "page": 1,
                "checks": ["GRID", "LOCALIZATION"],
            }]}), encoding="utf-8")
            config = load_geometry_config(config_path)
            result = run_geometry_validation(config, case_runner=lambda value: case(value.case_id))
            json_path, markdown_path, csv_path, baseline_path = (root / name for name in ("result.json", "result.md", "result.csv", "baseline.json"))
            write_geometry_json(json_path, result)
            write_geometry_markdown(markdown_path, result)
            write_geometry_csv(csv_path, result)
            write_geometry_baseline(baseline_path, result)
            with self.assertRaises(FileExistsError):
                write_geometry_baseline(baseline_path, result)
            self.assertNotIn("/private/example.pdf", json_path.read_text(encoding="utf-8"))
            self.assertNotIn("/private/example.pdf", markdown_path.read_text(encoding="utf-8"))
            self.assertIn("# ShopLens Geometry Validation", markdown_path.read_text(encoding="utf-8"))
            self.assertIn("case_id", csv_path.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(baseline_path.read_text(encoding="utf-8"))["baseline_kind"],
                "GEOMETRY_REGRESSION_BASELINE",
            )

    def test_debug_json_explicitly_includes_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"schema_version": 1, "cases": [{
                "case_id": "case-001", "pdf": "/private/example.pdf", "page": 1,
            }]}), encoding="utf-8")
            config = load_geometry_config(config_path)
            result = run_geometry_validation(config, case_runner=lambda value: case(value.case_id))
            json_path = root / "debug.json"
            write_geometry_json(json_path, result, debug=True)
            self.assertIn("/private/example.pdf", json_path.read_text(encoding="utf-8"))

    def test_unknown_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version": 2, "cases": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_geometry_config(path)

    def test_config_rejects_bool_types_and_uses_immutable_fields(self):
        def load(value):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                return load_geometry_config(path)

        base = {"schema_version": 1, "cases": [{"case_id": "case-001", "pdf": "drawing.pdf", "page": 1}]}
        for field, value in (("page", True), ("checks", [1])):
            invalid = json.loads(json.dumps(base))
            invalid["cases"][0][field] = value
            with self.assertRaises(ValueError):
                load(invalid)
        invalid = json.loads(json.dumps(base))
        invalid["coordinate_tolerance"] = True
        with self.assertRaises(ValueError):
            load(invalid)
        config = load(base)
        self.assertIsInstance(config.cases, tuple)
        self.assertIsInstance(config.cases[0].checks, tuple)
        self.assertIsInstance(GeometryCaseConfig("case", "drawing.pdf", checks=["GRID"] ).checks, tuple)
        with self.assertRaises(TypeError):
            config.cases[0].checks[0] = "GRID"
        with self.assertRaises((AttributeError, TypeError)):
            config.cases[0].checks += ("GRID",)

    def test_malformed_baselines_fail_predictably(self):
        malformed = [[], None, {}, {"schema_version": 2, "case_results": []}, {"schema_version": True, "case_results": []}, {"schema_version": False, "case_results": []}, {"schema_version": 1.0, "case_results": []}, {"schema_version": "1", "case_results": []}, {"schema_version": None, "case_results": []}, {"schema_version": 1, "case_results": "bad"}, {"schema_version": 1, "case_results": [1]}, {"schema_version": 1, "case_results": [{}]}, {"schema_version": 1, "case_results": [{"case_id": ""}]}, {"schema_version": 1, "case_results": [{"case_id": "x"}, {"case_id": "x"}]}]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_geometry_baseline(value)

    def test_baseline_grid_and_localization_must_be_objects_or_null(self):
        for field in ("grid", "localization"):
            for invalid in ([], "bad", 1):
                value = {"schema_version": 1, "case_results": [{"case_id": "case-001", field: invalid}]}
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    validate_geometry_baseline(value)
        invalid_axes = {"schema_version": 1, "case_results": [{"case_id": "case-001", "grid": {"horizontal_axes": [1]}}]}
        with self.assertRaises(ValueError):
            validate_geometry_baseline(invalid_axes)

    def test_baseline_grid_and_localization_supported_object_states(self):
        supported = [
            {"schema_version": 1, "case_results": [{"case_id": "missing"}]},
            {"schema_version": 1, "case_results": [{"case_id": "null", "grid": None, "localization": None}]},
            {"schema_version": 1, "case_results": [{"case_id": "empty", "grid": {}, "localization": {}}]},
            report(case(
                "structured",
                secondary=[system("S-A", [axis("HORIZONTAL", "1", 10)], [axis("VERTICAL", "A", 20)])],
                localization={
                    "total_section_detections": 2,
                    "complete_bay": 1,
                    "on_axis": 1,
                    "outside_grid": 0,
                    "ambiguous": 0,
                    "unlocalized": 0,
                    "grid_system_distribution": {"PRIMARY": 1, "S-A": 1},
                },
            )),
        ]
        for value in supported:
            with self.subTest(case_id=value["case_results"][0]["case_id"]):
                self.assertIs(validate_geometry_baseline(value), value)

    def test_baseline_axis_coordinates_must_be_finite(self):
        for coordinate in (float("nan"), float("inf"), float("-inf")):
            value = report(case(horizontal=[axis("HORIZONTAL", "1", coordinate)]))
            with self.subTest(coordinate=coordinate), self.assertRaisesRegex(ValueError, "finite"):
                validate_geometry_baseline(value)

    def test_json_nan_coordinate_is_rejected_as_invalid_baseline(self):
        value = json.loads(
            '{"schema_version": 1, "case_results": [{"case_id": "nan", '
            '"grid": {"horizontal_axes": [{"orientation": "HORIZONTAL", '
            '"label": "1", "coordinate": NaN}]}}]}'
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_geometry_baseline(value)

    def test_markdown_and_csv_escape_user_controlled_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"schema_version": 1, "cases": [{"case_id": "=formula|row\nnext", "pdf": "drawing.pdf", "page": 1}]}), encoding="utf-8")
            config = load_geometry_config(config_path)
            result = run_geometry_validation(config, case_runner=lambda value: case(value.case_id, warnings=["@warning"]))
            markdown_path, csv_path = root / "result.md", root / "result.csv"
            write_geometry_markdown(markdown_path, result)
            write_geometry_csv(csv_path, result)
            markdown = markdown_path.read_text(encoding="utf-8")
            csv = csv_path.read_text(encoding="utf-8")
            self.assertIn("=formula\\|row<br>next", markdown)
            self.assertIn("'=formula|row\nnext", csv)
            self.assertIn("'@warning", csv)

    def test_csv_formula_guard_ignores_leading_whitespace_for_detection(self):
        for value in ("=formula", " =formula", "   +cmd", "\t=cmd", "\r@cmd", "\n-formula"):
            self.assertEqual(_csv_safe_cell(value), "'" + value)
        self.assertEqual(_csv_safe_cell(" ordinary"), " ordinary")
        self.assertEqual(_csv_safe_cell("ordinary"), "ordinary")

    def test_git_revision_is_resolved_outside_caller_working_directory(self):
        original = Path.cwd()
        try:
            os.chdir(tempfile.gettempdir())
            self.assertTrue(_git_revision())
        finally:
            os.chdir(original)

    def test_cli_review_required_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            baseline_path = root / "baseline.json"
            config_path.write_text(json.dumps({"schema_version": 1, "cases": [{"case_id": "case-001", "pdf": "drawing.pdf", "page": 1}]}), encoding="utf-8")
            baseline_path.write_text(json.dumps(report(case())), encoding="utf-8")
            current = run_geometry_validation(load_geometry_config(config_path), case_runner=lambda value: case(value.case_id, horizontal=[axis("HORIZONTAL", "1", 110)]))
            with mock.patch.object(cli, "run_geometry_validation", return_value=current):
                self.assertNotEqual(cli.main(["validate-geometry", str(config_path), "--compare", str(baseline_path)]), 0)

    def test_every_non_unchanged_comparison_status_fails(self):
        for status in ("UNCHANGED", "REGRESSION", "REVIEW_REQUIRED", "ERROR", "NEW_CASE", "REMOVED_CASE", "IMPROVEMENT"):
            with self.subTest(status=status):
                self.assertEqual(cli._geometry_comparison_failed({"summary": {status: 1}}), status != "UNCHANGED")
        self.assertTrue(cli._geometry_comparison_failed({"summary": {"UNCHANGED": 1, "IMPROVEMENT": 1}}))

    def test_cli_rejects_malformed_baseline_with_friendly_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            baseline_path = root / "baseline.json"
            config_path.write_text(json.dumps({"schema_version": 1, "cases": [{"case_id": "case-001", "pdf": "drawing.pdf", "page": 1}]}), encoding="utf-8")
            baseline_path.write_text("null", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = cli.main(["validate-geometry", str(config_path), "--compare", str(baseline_path)])
            self.assertEqual(status, 2)
            self.assertIn("invalid geometry comparison baseline", stderr.getvalue())
