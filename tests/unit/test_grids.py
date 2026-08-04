"""Synthetic geometry, grid extraction, CLI, JSON, and SVG tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.classification.models import (
    ClassificationTitleSource,
    ClassifiedSheet,
    Discipline,
    SheetKind,
    StructuralSubject,
)
from shoplens.geometry import LineSegment, PageGeometry, ShapeGeometry
from shoplens.geometry.adapter import _deduplicate_lines
from shoplens.geometry.transforms import to_positioned_coordinates, transform_box
from shoplens.grids import detect_grid_system, export_grid_svg
from shoplens.grids.detect import GRID_LABEL_RE


def text(value, x, y, width=10.0, height=10.0, page=1):
    return SimpleNamespace(text=value, x=x, y=y, width=width, height=height, page=page)


def ellipse(center_x, center_y, size=20.0):
    half = size / 2.0
    return ShapeGeometry(1, "ELLIPSE", (center_x - half, center_y - half, center_x + half, center_y + half), "synthetic")


def regular_geometry_and_text():
    lines = []
    shapes = []
    items = []
    vertical = [("A", 200.0), ("B", 500.0), ("AA", 800.0)]
    for label, x in vertical:
        for y in ([780.0, 20.0] if label != "AA" else [780.0]):
            shapes.append(ellipse(x, y))
            items.append(text(label, x - 5, y - 5))
        # Dashed/collinear representation; B is slightly imperfect.
        offset = 1.5 if label == "B" else 0.0
        lines.extend(
            [
                LineSegment(1, x, 40, x + offset, 350, dash=(12, 6)),
                LineSegment(1, x + offset, 360, x, 760, dash=(12, 6)),
            ]
        )
    # A fourth label at the lower end makes that boundary contextual while
    # remaining too short to form an axis.
    shapes.append(ellipse(900, 20))
    items.append(text("AB", 895, 15))
    lines.append(LineSegment(1, 900, 10, 900, 35))

    horizontal = [("1", 200.0), ("2", 400.0), ("2.5", 600.0)]
    for label, y in horizontal:
        for x in ([20.0, 980.0] if label != "2.5" else [20.0]):
            shapes.append(ellipse(x, y))
            items.append(text(label, x - 5, y - 5))
        lines.extend(
            [
                LineSegment(1, 40, y, 450, y, dash=(10, 5)),
                LineSegment(1, 460, y, 960, y, dash=(10, 5)),
            ]
        )
    shapes.append(ellipse(980, 700))
    items.append(text("3", 975, 695))
    lines.append(LineSegment(1, 965, 700, 990, 700))

    # Larger reference bubble and ordinary schedule number are not grid labels.
    shapes.append(ellipse(700, 300, 40))
    items.extend([text("4", 695, 295), text("S2-03", 680, 290, 50, 10)])
    items.append(text("12", 300, 300))
    geometry = PageGeometry(
        pdf_page=1,
        width=1000,
        height=800,
        rotation=0,
        media_box=(0, 0, 1000, 800),
        crop_box=(0, 0, 1000, 800),
        coordinate_system="PDF_USER_SPACE_BOTTOM_LEFT",
        provider="synthetic",
        lines=lines,
        shapes=shapes,
    )
    return geometry, items


def classified_sheet(page=1, number="S1-20A"):
    return ClassifiedSheet(
        pdf_page=page,
        actual_pdf_pages=[page],
        sheet_number=number,
        declared_title="SECOND FLOOR FRAMING PLAN - SEGMENT A",
        actual_title="SECOND FLOOR FRAMING PLAN - SEGMENT A",
        classification_title="SECOND FLOOR FRAMING PLAN-SEGMENT A",
        classification_title_source=ClassificationTitleSource.ACTUAL_TITLE,
        discipline=Discipline.STRUCTURAL,
        sheet_kind=SheetKind.PLAN,
        secondary_kinds=[],
        subject=StructuralSubject.FLOOR_FRAMING,
        secondary_subjects=[],
        level="SECOND FLOOR",
        segment="A",
        area=[],
        modifiers=[],
        classification_confidence=0.98,
        matched_rule="SECOND_FLOOR_FRAMING_PLAN",
        classification_evidence=[],
        group_keys=[],
        warnings=[],
    )


class GeometryTransformTests(unittest.TestCase):
    def test_rotation_crop_offsets_and_negative_coordinates(self):
        self.assertEqual(to_positioned_coordinates(10, 20, 90), (20, -10))
        self.assertEqual(transform_box((10, 20, 210, 320), 90), (20, -210, 320, -10))

    def test_duplicate_vector_objects_are_safely_suppressed(self):
        first = LineSegment(1, 0, 0, 100, 0)
        reverse = LineSegment(1, 100, 0, 0, 0)
        distinct = LineSegment(1, 0, 10, 100, 10)
        self.assertEqual(_deduplicate_lines([first, reverse, distinct]), [first, distinct])


class GridDetectionTests(unittest.TestCase):
    def setUp(self):
        self.geometry, self.items = regular_geometry_and_text()
        self.grid = detect_grid_system("drawing.pdf", self.geometry, self.items, classified_sheet())

    def test_regular_grid_labels_orientation_repetition_and_single_end(self):
        self.assertEqual([axis.normalized_label for axis in self.grid.vertical_axes], ["A", "B", "AA"])
        self.assertEqual([axis.normalized_label for axis in self.grid.horizontal_axes], ["1", "2", "2.5"])
        by_label = {axis.normalized_label: axis for axis in self.grid.vertical_axes}
        self.assertEqual(len(by_label["A"].label_candidates), 2)
        self.assertEqual(len(by_label["AA"].label_candidates), 1)
        self.assertTrue(all(axis.intersection_count == 3 for axis in self.grid.vertical_axes))
        self.assertIsNotNone(GRID_LABEL_RE.fullmatch("A.1"))

    def test_collinear_dashed_and_slightly_rotated_segments_merge(self):
        axis = next(value for value in self.grid.vertical_axes if value.normalized_label == "B")
        self.assertEqual(len(axis.source_segments), 2)
        self.assertLessEqual(axis.start_y, 40)
        self.assertGreaterEqual(axis.end_y, 760)

    def test_short_structural_lines_and_reference_bubbles_are_rejected(self):
        accepted = {axis.normalized_label for axis in self.grid.horizontal_axes + self.grid.vertical_axes}
        self.assertNotIn("3", accepted)
        self.assertNotIn("4", accepted)
        self.assertTrue(any(item.reason == "DETAIL_OR_SECTION_REFERENCE" for item in self.grid.rejected_candidates))
        self.assertNotIn("12", accepted)  # ordinary schedule number has no bubble context

    def test_classification_metadata_json_and_negative_raw_coordinates(self):
        payload = json.loads(json.dumps(self.grid.to_dict()))
        self.assertEqual(payload["sheet_number"], "S1-20A")
        self.assertEqual(payload["sheet_subject"], "FLOOR_FRAMING")
        self.assertEqual(payload["vertical_axes"][0]["orientation"], "VERTICAL")
        self.assertIn("source_segments", payload["vertical_axes"][0])
        self.assertNotIn("lines", payload["page_geometry"])
        self.assertEqual(payload["page_geometry"]["line_count"], len(self.geometry.lines))

    def test_no_grid_found(self):
        empty = PageGeometry(1, 100, 100, 0, (0, 0, 100, 100), (0, 0, 100, 100), "raw", "synthetic")
        result = detect_grid_system("empty.pdf", empty, [text("A", 10, 10)])
        self.assertEqual((result.horizontal_axes, result.vertical_axes), ([], []))
        self.assertIn("NO_DOMINANT_GRID_SYSTEM", result.warnings)

    def test_multiple_separate_grid_systems_warn(self):
        geometry, items = regular_geometry_and_text()
        for label, y in (("7", 100.0), ("8", 300.0), ("9", 500.0)):
            geometry.shapes.append(ellipse(400, y))
            items.append(text(label, 395, y - 5))
            geometry.lines.append(LineSegment(1, 100, y, 900, y))
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertIn("MULTIPLE_SIMILAR_GRID_SYSTEMS", result.warnings)

    def test_svg_export_contains_geometry_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.svg"
            export_grid_svg(path, self.grid, include_rejected=True)
            value = path.read_text(encoding="utf-8")
        self.assertIn("<svg", value)
        self.assertIn("<line", value)
        self.assertNotIn("data:image", value)


class GridCliTests(unittest.TestCase):
    def run_cli(self, options):
        geometry, items = regular_geometry_and_text()
        sheet = classified_sheet()
        output = io.StringIO()
        with patch.object(cli, "_extract_package_title_blocks_with_items", return_value=(object(), object(), items, 0)), patch.object(cli, "reconcile_sheets", return_value=object()), patch.object(cli, "build_package_index", return_value=SimpleNamespace(sheets=[sheet])), patch.object(cli, "extract_page_geometry", return_value=[geometry]):
            with redirect_stdout(output):
                status = cli.main(["grid-system", "drawing.pdf"] + options)
        return status, output.getvalue()

    def test_sheet_lookup_and_readable_list(self):
        status, output = self.run_cli(["--sheet", "s1-20a", "--list"])
        self.assertEqual(status, 0)
        self.assertIn("Sheet: S1-20A", output)
        self.assertIn("VERTICAL | A", output)

    def test_page_lookup_and_json(self):
        status, output = self.run_cli(["--page", "1", "--json"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["pdf_page"], 1)
        self.assertEqual(payload["sheet_number"], "S1-20A")

    def test_selector_is_required(self):
        with self.assertRaises(SystemExit):
            cli.main(["grid-system", "drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
