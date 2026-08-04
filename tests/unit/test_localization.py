"""Synthetic grid-relative section localization tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.classification.models import SheetKind, StructuralSubject
from shoplens.geometry import PageGeometry
from shoplens.grids.models import GridAxis, GridOrientation, GridSystem
from shoplens.inventory.models import ClassifiedSectionDetection
from shoplens.localization import export_localization_svg, filter_localizations, localize_section_detections
from shoplens.models import SectionFamily


def axis(label, orientation, coordinate, start=100.0, end=900.0):
    horizontal = orientation == GridOrientation.HORIZONTAL
    return GridAxis(
        f"{orientation.value}:{label}", orientation, label, [], coordinate,
        start if horizontal else coordinate, coordinate if horizontal else start,
        end if horizontal else coordinate, coordinate if horizontal else end,
        [], [], 3, 0.9,
    )


def grid(horizontal=("1", "2", "3"), vertical=("A", "B", "C"), short_extents=False, warning=None):
    h_coordinates = (200.0, 500.0, 800.0)
    v_coordinates = (200.0, 500.0, 800.0)
    extent = (300.0, 700.0) if short_extents else (100.0, 900.0)
    geometry = PageGeometry(1, 1000, 1000, 0, (0, 0, 1000, 1000), (0, 0, 1000, 1000), "PDF_USER_SPACE_BOTTOM_LEFT", "synthetic")
    return GridSystem(
        "drawing.pdf", 1, "S1-20A", "PLAN", StructuralSubject.FLOOR_FRAMING,
        "SECOND FLOOR", "A", geometry,
        [axis(label, GridOrientation.HORIZONTAL, value, *extent) for label, value in zip(horizontal, h_coordinates)],
        [axis(label, GridOrientation.VERTICAL, value, *extent) for label, value in zip(vertical, v_coordinates)],
        [], [], 0.9, [warning] if warning else [],
    )


def detection(x=340.0, y=340.0, section="W24X55", family=SectionFamily.W, width=20.0, height=20.0):
    return ClassifiedSectionDetection(
        1, "S1-20A", "PLAN", SheetKind.PLAN, StructuralSubject.FLOOR_FRAMING,
        "SECOND FLOOR", "A", [], section, section, family, x, y, width, height,
        0.95, 1, "deduplicated", [],
    )


class LocalizationTests(unittest.TestCase):
    def locate(self, item=None, system=None):
        return localize_section_detections("drawing.pdf", [item or detection()], system or grid()).detections[0]

    def test_regular_bay_anchor_distances_and_json(self):
        item = self.locate()
        self.assertEqual((item.detection_anchor_x, item.detection_anchor_y), (350.0, 350.0))
        self.assertEqual((item.left_vertical_axis, item.right_vertical_axis), ("A", "B"))
        self.assertEqual((item.lower_horizontal_axis, item.upper_horizontal_axis), ("1", "2"))
        self.assertEqual(item.bay_id, "A–B / 1–2")
        self.assertEqual(item.nearest_vertical_distance, 150.0)
        self.assertEqual(item.coordinate_system, "PDF_USER_SPACE_BOTTOM_LEFT")
        payload = json.loads(json.dumps(item.to_dict()))
        self.assertEqual(payload["section_family"], "W")
        self.assertEqual(payload["detection_x"], 340.0)

    def test_on_horizontal_vertical_and_near_intersection(self):
        horizontal = self.locate(detection(x=340, y=490))
        self.assertEqual(horizontal.horizontal_interval, "ON 2")
        self.assertEqual(horizontal.bay_id, "A–B / ON 2")
        self.assertFalse(horizontal.inside_valid_bay)
        self.assertIn("ON_HORIZONTAL_AXIS", horizontal.warnings)
        vertical = self.locate(detection(x=490, y=340))
        self.assertEqual(vertical.vertical_interval, "ON B")
        intersection = self.locate(detection(x=490, y=490))
        self.assertIn("NEAR_GRID_INTERSECTION", intersection.warnings)

    def test_outside_and_inside_box_but_outside_axis_extents(self):
        outside = self.locate(detection(x=920, y=340))
        self.assertFalse(outside.inside_grid_bounds)
        self.assertIn("OUTSIDE_GRID_BOUNDS", outside.warnings)
        extent_gap = self.locate(detection(x=240, y=240), grid(short_extents=True))
        self.assertFalse(extent_gap.inside_grid_bounds)

    def test_missing_axis_families_and_no_grid(self):
        no_horizontal = grid()
        no_horizontal.horizontal_axes = []
        item = self.locate(system=no_horizontal)
        self.assertIsNone(item.nearest_horizontal_axis)
        self.assertIn("NO_SURROUNDING_HORIZONTAL_AXES", item.warnings)
        no_vertical = grid()
        no_vertical.vertical_axes = []
        self.assertIn("NO_SURROUNDING_VERTICAL_AXES", self.locate(system=no_vertical).warnings)
        result = localize_section_detections("drawing.pdf", [detection()], None)
        self.assertIn("NO_GRID_SYSTEM", result.detections[0].warnings)

    def test_spatial_order_ignores_labels_and_supports_label_forms(self):
        system = grid(horizontal=("C", "A", "AA"), vertical=("10", "2.5", "A.1"))
        item = self.locate(system=system)
        self.assertEqual(item.vertical_interval, "10–2.5")
        self.assertEqual(item.horizontal_interval, "C–A")

    def test_ambiguous_grid_label_and_multiple_systems(self):
        ambiguous = self.locate(detection(x=490, y=340), grid(vertical=("A", "04", "C")))
        self.assertTrue(ambiguous.ambiguous)
        self.assertIn("GRID_LABEL_AMBIGUITY", ambiguous.warnings)
        second = grid()
        second.confidence = 0.88
        result = localize_section_detections("drawing.pdf", [detection()], [grid(), second])
        self.assertIn("AMBIGUOUS_GRID_SYSTEM", result.detections[0].warnings)

    def test_summary_raw_mode_counts(self):
        records = [detection(), detection(x=490, y=490)]
        result = localize_section_detections("drawing.pdf", records, grid(), record_mode="raw")
        self.assertEqual(result.total_section_detections, 2)
        self.assertEqual(result.record_mode, "raw")
        self.assertEqual(result.detections_on_axes, 1)

    def test_all_filters_and_combination(self):
        records = [
            self.locate(),
            self.locate(detection(x=920, section="HSS6X6X1/2", family=SectionFamily.HSS)),
            self.locate(detection(x=490), grid(vertical=("A", "04", "C"))),
        ]
        self.assertEqual(len(filter_localizations(records, family=SectionFamily.W)), 2)
        self.assertEqual(len(filter_localizations(records, section="W24X55")), 2)
        self.assertEqual(len(filter_localizations(records, inside_only=True)), 2)
        self.assertEqual(len(filter_localizations(records, outside_only=True)), 1)
        self.assertEqual(len(filter_localizations(records, ambiguous_only=True)), 1)
        self.assertEqual(len(filter_localizations(records, family=SectionFamily.W, inside_only=True, ambiguous_only=True)), 1)

    def test_svg_export_is_geometry_and_text_only(self):
        result = localize_section_detections("drawing.pdf", [detection()], grid())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locations.svg"
            export_localization_svg(path, result)
            value = path.read_text(encoding="utf-8")
        self.assertIn("W24X55", value)
        self.assertIn("<circle", value)
        self.assertNotIn("data:image", value)


class LocalizationCliTests(unittest.TestCase):
    def run_cli(self, selector, extra=None):
        system = grid()
        sheet = SimpleNamespace(sheet_number="S1-20A", pdf_page=1, actual_pdf_pages=[1])
        inventory_sheet = SimpleNamespace(sheet_number="S1-20A", pdf_pages=[1], detections=[detection()])
        inventory = SimpleNamespace(sheets=[inventory_sheet], record_mode="deduplicated")
        output = io.StringIO()
        with patch.object(cli, "_extract_package_title_blocks_with_items", return_value=(object(), object(), [], 0)), \
             patch.object(cli, "reconcile_sheets", return_value=object()), \
             patch.object(cli, "build_package_index", return_value=SimpleNamespace(sheets=[sheet])), \
             patch.object(cli, "analyze_positioned_text", return_value=([], [], [])), \
             patch.object(cli, "build_section_inventory", return_value=inventory), \
             patch.object(cli, "extract_page_geometry", return_value=[system.page_geometry]), \
             patch.object(cli, "detect_grid_system", return_value=system):
            with redirect_stdout(output):
                status = cli.main(["grid-locate-sections", "drawing.pdf", *selector, *(extra or [])])
        return status, output.getvalue()

    def test_sheet_lookup_list_and_filters(self):
        status, output = self.run_cli(["--sheet", "s1-20a"], ["--family", "W", "--inside-only", "--list"])
        self.assertEqual(status, 0)
        self.assertIn("Sheet: S1-20A", output)
        self.assertIn("A–B / 1–2", output)
        self.assertIn("Active filters:", output)

    def test_page_lookup_json(self):
        status, output = self.run_cli(["--page", "1"], ["--json"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["pdf_page"], 1)
        self.assertEqual(payload["active_filters"], {"page": 1})
        self.assertFalse(payload["raw_mode"])

    def test_selector_required(self):
        with self.assertRaises(SystemExit):
            cli.main(["grid-locate-sections", "drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
