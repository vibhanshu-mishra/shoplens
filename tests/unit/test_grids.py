"""Synthetic geometry, grid extraction, CLI, JSON, and SVG tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
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
from shoplens.title_blocks.models import SheetRecordSource


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


def separated_grids_geometry_and_text(repeated_labels=False):
    """Two complete, spatially separated grids with independent bubble groups."""

    lines = []
    shapes = []
    items = []
    for prefix, x_values, y_values in (
        ("A", (120.0, 220.0, 320.0), (120.0, 220.0, 320.0)),
        ("B", (620.0, 720.0, 820.0), (520.0, 620.0, 720.0)),
    ):
        for index, x in enumerate(x_values, start=1):
            label = f"{('A' if repeated_labels else prefix)}{index}"
            bubble_y = y_values[0] - 20.0 if prefix == "A" else y_values[-1] + 20.0
            shapes.append(ellipse(x, bubble_y))
            items.append(text(label, x - 5.0, bubble_y - 5.0))
            lines.append(LineSegment(1, x, y_values[0] - 20.0, x, y_values[-1] + 20.0))
        for index, y in enumerate(y_values, start=1):
            label = str(index if repeated_labels or prefix == "A" else index + 10)
            bubble_x = x_values[0] if prefix == "A" else x_values[-1] + 20.0
            shapes.append(ellipse(bubble_x, y))
            items.append(text(label, bubble_x - 5.0, y - 5.0))
            lines.append(LineSegment(1, x_values[0], y, x_values[-1] + 20.0, y))
    return PageGeometry(
        1, 1000, 800, 0, (0, 0, 1000, 800), (0, 0, 1000, 800),
        "PDF_USER_SPACE_BOTTOM_LEFT", "synthetic", lines=lines, shapes=shapes,
    ), items


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
        self.assertIn("LABEL_REPEATED_AT_OPPOSITE_ENDS", by_label["A"].evidence)
        self.assertTrue(all(axis.intersection_count == 3 for axis in self.grid.vertical_axes))
        self.assertIsNotNone(GRID_LABEL_RE.fullmatch("A.1"))

    def test_one_text_item_surrounded_by_overlapping_bubbles_is_one_observation(self):
        shapes = [ellipse(100, 100, 20.0) for _ in range(100)]
        geometry = PageGeometry(
            1, 200, 200, 0, (0, 0, 200, 200), (0, 0, 200, 200),
            "raw", "synthetic", shapes=shapes,
        )
        result = detect_grid_system("drawing.pdf", geometry, [text("A", 95, 95)])
        self.assertEqual(len(result.unassigned_labels), 1)
        self.assertEqual(result.unassigned_labels[0].bubble_alternative_count, 0)
        self.assertEqual(result.bubble_diagnostics["raw_bubble_candidate_count"], 100)
        self.assertEqual(result.bubble_diagnostics["deduplicated_bubble_candidate_count"], 1)
        self.assertEqual(result.bubble_diagnostics["physical_label_observation_count"], 1)

    def test_unrelated_dominant_bubble_size_does_not_reject_real_grid_family(self):
        geometry, items = regular_geometry_and_text()
        geometry.shapes.extend(
            ellipse(40 + index * 9, 730, 8.0) for index in range(100)
        )
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual(
            [axis.normalized_label for axis in result.vertical_axes], ["A", "B", "AA"]
        )
        self.assertEqual(
            [axis.normalized_label for axis in result.horizontal_axes], ["1", "2", "2.5"]
        )
        self.assertFalse(any(
            candidate.reason == "INCONSISTENT_BUBBLE_SIZE"
            for candidate in result.rejected_candidates
        ))

    def test_two_legitimate_bubble_size_families_survive(self):
        lines, shapes, items = [], [], []
        for label, x in (("A.1", 100.0), ("B.5", 200.0), ("C", 300.0)):
            shapes.extend([ellipse(x, 380, 20), ellipse(x, 20, 32)])
            items.extend([text(label, x - 5, 375), text(label, x - 5, 15)])
            lines.append(LineSegment(1, x, 40, x, 360))
        for label, y in (("1", 100.0), ("2", 200.0), ("3.5", 300.0)):
            shapes.extend([ellipse(20, y, 20), ellipse(380, y, 32)])
            items.extend([text(label, 15, y - 5), text(label, 375, y - 5)])
            lines.append(LineSegment(1, 40, y, 360, y))
        geometry = PageGeometry(
            1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
            "raw", "synthetic", lines=lines, shapes=shapes,
        )
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual([axis.normalized_label for axis in result.vertical_axes], ["A.1", "B.5", "C"])
        self.assertEqual([axis.normalized_label for axis in result.horizontal_axes], ["1", "2", "3.5"])
        self.assertGreaterEqual(result.bubble_diagnostics["bubble_size_cluster_count"], 2)

    def test_nearby_distinct_bubbles_are_not_deduplicated(self):
        geometry = PageGeometry(
            1, 300, 200, 0, (0, 0, 300, 200), (0, 0, 300, 200),
            "raw", "synthetic", shapes=[ellipse(100, 100, 20), ellipse(132, 100, 20)],
        )
        result = detect_grid_system(
            "drawing.pdf", geometry, [text("A", 95, 95), text("B", 127, 95)]
        )
        self.assertEqual(result.bubble_diagnostics["deduplicated_bubble_candidate_count"], 2)
        self.assertEqual({label.normalized_label for label in result.unassigned_labels}, {"A", "B"})

    def test_decorative_circles_without_grid_labels_produce_no_grid(self):
        geometry = PageGeometry(
            1, 300, 200, 0, (0, 0, 300, 200), (0, 0, 300, 200),
            "raw", "synthetic", shapes=[ellipse(50 + index * 20, 100, 12) for index in range(10)],
        )
        result = detect_grid_system("drawing.pdf", geometry, [text("LOGO", 20, 20)])
        self.assertEqual((result.horizontal_axes, result.vertical_axes), ([], []))
        self.assertIn("NO_DOMINANT_GRID_SYSTEM", result.warnings)

    def test_one_text_item_with_multiple_shape_alternatives_cannot_imply_repetition(self):
        shapes = [
            ellipse(100, 100, 20), ellipse(107, 100, 20),
            ellipse(100, 200, 20), ellipse(100, 300, 20),
        ]
        items = [text("1", 95, 95), text("2", 95, 195), text("3", 95, 295)]
        geometry = PageGeometry(
            1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
            "raw", "synthetic",
            lines=[
                LineSegment(1, 40, 100, 360, 100),
                LineSegment(1, 40, 200, 360, 200),
                LineSegment(1, 40, 300, 360, 300),
            ],
            shapes=shapes,
        )
        result = detect_grid_system("drawing.pdf", geometry, items)
        axis = next(value for value in result.horizontal_axes if value.normalized_label == "1")
        self.assertEqual(len(axis.label_candidates), 1)
        self.assertNotIn("LABEL_REPEATED_AT_OPPOSITE_ENDS", axis.evidence)
        self.assertEqual(axis.label_candidates[0].bubble_alternative_count, 1)

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

    def test_separated_complete_grids_are_exposed_as_primary_and_secondary_systems(self):
        geometry, items = separated_grids_geometry_and_text()
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual(result.grid_system_id, "PAGE_1_DOMINANT_GRID")
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)
        secondary = result.secondary_grid_systems[0]
        self.assertEqual(secondary.grid_system_id, "PAGE_1_SECONDARY_GRID_1")
        self.assertEqual((len(secondary.horizontal_axes), len(secondary.vertical_axes)), (3, 3))
        self.assertEqual(result.warnings.count("MULTIPLE_GRID_SYSTEMS_DETECTED"), 1)

    def test_disconnected_grids_with_repeated_axis_labels_remain_separate(self):
        geometry, items = separated_grids_geometry_and_text(repeated_labels=True)
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)

    def test_svg_export_contains_geometry_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.svg"
            export_grid_svg(path, self.grid, include_rejected=True)
            value = path.read_text(encoding="utf-8")
        self.assertIn("<svg", value)
        self.assertIn("<line", value)
        self.assertNotIn("data:image", value)

    def test_svg_marks_secondary_grid_axes_with_their_system_id(self):
        geometry, items = separated_grids_geometry_and_text()
        result = detect_grid_system("drawing.pdf", geometry, items)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grids.svg"
            export_grid_svg(path, result)
            value = path.read_text(encoding="utf-8")
        self.assertIn('data-grid-system="PAGE_1_SECONDARY_GRID_1"', value)


class GridCliTests(unittest.TestCase):
    def run_cli(self, options, sheets=None):
        geometry, items = regular_geometry_and_text()
        sheets = sheets if sheets is not None else [classified_sheet()]
        if len(sheets) == 1 and sheets[0].actual_pdf_pages:
            geometry = replace(geometry, pdf_page=sheets[0].actual_pdf_pages[0])
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(cli, "_extract_package_title_blocks_with_items", return_value=(object(), object(), items, 0)), patch.object(cli, "reconcile_sheets", return_value=object()), patch.object(cli, "build_package_index", return_value=SimpleNamespace(sheets=sheets)), patch.object(cli, "extract_page_geometry", return_value=[geometry]):
            with redirect_stdout(output), redirect_stderr(errors):
                status = cli.main(["grid-system", "drawing.pdf"] + options)
        return status, output.getvalue(), errors.getvalue()

    def test_sheet_lookup_and_readable_list(self):
        status, output, _ = self.run_cli(["--sheet", "s1-20a", "--list"])
        self.assertEqual(status, 0)
        self.assertIn("Sheet: S1-20A", output)
        self.assertIn("Grid systems: 1", output)
        self.assertIn("VERTICAL | A", output)

    def test_page_lookup_and_json(self):
        status, output, _ = self.run_cli(["--page", "1", "--json"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["pdf_page"], 1)
        self.assertEqual(payload["sheet_number"], "S1-20A")

    def test_title_block_only_sheet_resolves_by_sheet_number(self):
        sheet = replace(
            classified_sheet(page=7, number="S103A"),
            declared_title=None,
            actual_title="LEVEL 3 BURN TOWER FRAMING PLAN - PART A",
            record_source=SheetRecordSource.TITLE_BLOCK_ONLY,
        )
        status, output, errors = self.run_cli(["--sheet", "S103A"], [sheet])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Sheet: S103A", output)
        self.assertIn("PDF page: 7", output)

    def test_unknown_title_block_only_sheet_still_resolves_by_page(self):
        sheet = replace(
            classified_sheet(page=7, number="S103A"),
            sheet_kind=SheetKind.UNKNOWN,
            subject=StructuralSubject.UNKNOWN,
            record_source=SheetRecordSource.TITLE_BLOCK_ONLY,
        )
        status, output, errors = self.run_cli(["--sheet", "S103A"], [sheet])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Sheet: S103A", output)

    def test_duplicate_sheet_number_is_ambiguous(self):
        sheets = [classified_sheet(page=1, number="S103A"), classified_sheet(page=2, number="S103A")]
        status, output, errors = self.run_cli(["--sheet", "S103A"], sheets)
        self.assertEqual(status, 7)
        self.assertEqual(output, "")
        self.assertIn("sheet S103A is ambiguous", errors)

    def test_multiple_pages_for_one_sheet_number_is_ambiguous(self):
        sheet = replace(classified_sheet(page=1, number="S103A"), actual_pdf_pages=[1, 2])
        status, output, errors = self.run_cli(["--sheet", "S103A"], [sheet])
        self.assertEqual(status, 7)
        self.assertEqual(output, "")
        self.assertIn("does not have one unambiguous PDF page", errors)

    def test_missing_page_remains_unresolved(self):
        sheet = replace(classified_sheet(page=1, number="S103A"), pdf_page=None, actual_pdf_pages=[])
        status, output, errors = self.run_cli(["--sheet", "S103A"], [sheet])
        self.assertEqual(status, 7)
        self.assertEqual(output, "")
        self.assertIn("does not have one unambiguous PDF page", errors)

    def test_selector_is_required(self):
        with self.assertRaises(SystemExit):
            cli.main(["grid-system", "drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
