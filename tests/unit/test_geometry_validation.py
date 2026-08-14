"""Synthetic privacy-safe coverage for geometry regression reporting."""

import json
import tempfile
import unittest
from pathlib import Path

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
)


def axis(orientation, label, coordinate, intersections=2):
    return {
        "orientation": orientation,
        "label": label,
        "coordinate": coordinate,
        "intersection_count": intersections,
    }


def case(case_id="case-001", *, horizontal=None, vertical=None, systems=1, localization=None):
    return {
        "case_id": case_id,
        "execution_status": "PASS",
        "selected_page": 1,
        "grid": {
            "grid_system_count": systems,
            "primary_grid_system_id": "PRIMARY",
            "horizontal_axes": horizontal if horizontal is not None else [axis("HORIZONTAL", "1", 100.0)],
            "vertical_axes": vertical if vertical is not None else [axis("VERTICAL", "A", 200.0)],
            "secondary_systems": [],
            "unassigned_label_count": 0,
            "rejected_candidate_count": 0,
            "warnings": [],
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
