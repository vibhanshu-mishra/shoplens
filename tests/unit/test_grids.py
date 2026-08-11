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
from shoplens.grids import GridOrientation, detect_grid_system, export_grid_svg
from shoplens.grids.detect import GRID_LABEL_RE, _line_extent_components
from shoplens.grids.detect import _matching_line_component
from shoplens.grids.detect import _recover_candidate_axes_to_fixed_point
from shoplens.grids.detect import _recover_candidate_systems_to_fixed_point
from shoplens.grids.detect import _recover_perpendicular_supported_axes
from shoplens.grids.detect import _rank_system_candidates
from shoplens.grids.detect import _set_intersections
from shoplens.grids.models import GridAxis, GridLabel
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


def separated_grids_geometry_and_text(
    repeated_labels=False, shared_rows=False, shared_columns=False,
):
    """Two complete, spatially separated grids with independent bubble groups."""

    lines = []
    shapes = []
    items = []
    left_x, left_y = (120.0, 220.0, 320.0), (120.0, 220.0, 320.0)
    right_x = left_x if shared_columns else (620.0, 720.0, 820.0)
    right_y = left_y if shared_rows else (520.0, 620.0, 720.0)
    for prefix, x_values, y_values in (
        ("A", left_x, left_y),
        ("B", right_x, right_y),
    ):
        for index, x in enumerate(x_values, start=1):
            label = f"{('A' if repeated_labels else prefix)}{index}"
            bubble_y = (
                y_values[0] - 20.0
                if prefix == "A" or (shared_rows and y_values[-1] < 400.0)
                else y_values[-1] + 20.0
            )
            shapes.append(ellipse(x, bubble_y))
            items.append(text(label, x - 5.0, bubble_y - 5.0))
            lines.append(LineSegment(1, x, y_values[0] - 20.0, x, y_values[-1] + 20.0))
        for index, y in enumerate(y_values, start=1):
            label = str(index if repeated_labels or prefix == "A" else index + 10)
            bubble_x = (
                x_values[0]
                if prefix == "A" or (shared_columns and x_values[-1] < 500.0)
                else x_values[-1] + 20.0
            )
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


def recovery_axis(axis_id, orientation, label, coordinate, start, end):
    if orientation == GridOrientation.HORIZONTAL:
        start_x, start_y, end_x, end_y = start, coordinate, end, coordinate
    else:
        start_x, start_y, end_x, end_y = coordinate, start, coordinate, end
    return GridAxis(
        axis_id, orientation, label, [], coordinate,
        start_x, start_y, end_x, end_y,
        [], [], 0, 0.8, ["SEGMENT_COVERAGE:0.100"],
    )


def fragmented_recovery_geometry_and_labels():
    """One candidate whose two missing axes need fixed-point recovery."""

    horizontal = [
        recovery_axis("H:1", GridOrientation.HORIZONTAL, "1", 80.0, 40.0, 332.0),
        recovery_axis("H:3", GridOrientation.HORIZONTAL, "3", 240.0, 40.0, 332.0),
    ]
    vertical = [
        recovery_axis("V:1", GridOrientation.VERTICAL, "A", 80.0, 40.0, 332.0),
    ]
    lines = [
        *[
            LineSegment(1, start, 100.0, start + 12.0, 100.0)
            for start in (40.0, 120.0, 200.0, 280.0, 320.0)
        ],
        *[
            LineSegment(1, 300.0, start, 300.0, start + 12.0)
            for start in (40.0, 120.0, 200.0, 280.0, 320.0)
        ],
    ]
    labels = [
        GridLabel("2", "2", 1, 20.0, 95.0, 10.0, 10.0, "ELLIPSE", 0.8),
        GridLabel("B", "B", 1, 295.0, 20.0, 10.0, 10.0, "ELLIPSE", 0.8),
    ]
    geometry = PageGeometry(
        1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
        "raw", "synthetic", lines=lines,
    )
    return horizontal, vertical, labels, lines, geometry


def primary_and_recoverable_secondary_geometry_and_text():
    """A complete dominant grid plus a disconnected recoverable secondary."""

    lines = []
    shapes = []
    items = []
    for index, x in enumerate((100.0, 200.0, 300.0), start=1):
        shapes.append(ellipse(x, 80.0))
        items.append(text(f"P{index}", x - 5.0, 75.0))
        lines.append(LineSegment(1, x, 80.0, x, 320.0))
    for index, y in enumerate((100.0, 200.0, 300.0), start=1):
        shapes.append(ellipse(80.0, y))
        items.append(text(str(index), 75.0, y - 5.0))
        lines.append(LineSegment(1, 80.0, y, 320.0, y))

    for index, x in enumerate((600.0, 700.0), start=1):
        shapes.append(ellipse(x, 740.0))
        items.append(text(f"S{index}", x - 5.0, 735.0))
        lines.append(LineSegment(1, x, 480.0, x, 720.0))
    shapes.append(ellipse(800.0, 740.0))
    items.append(text("S3", 795.0, 735.0))
    lines.extend(
        LineSegment(1, 800.0, start, 800.0, start + 12.0)
        for start in (480.0, 560.0, 640.0, 720.0)
    )
    for index, y in enumerate((500.0, 600.0, 700.0), start=11):
        shapes.append(ellipse(840.0, y))
        items.append(text(str(index), 835.0, y - 5.0))
        lines.append(LineSegment(1, 580.0, y, 820.0, y))
    return PageGeometry(
        1, 1000, 800, 0, (0, 0, 1000, 800), (0, 0, 1000, 800),
        "raw", "synthetic", lines=lines, shapes=shapes,
    ), items


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

    def test_grid_serialization_does_not_recursively_copy_discarded_hierarchy(self):
        secondary_geometry, secondary_items = separated_grids_geometry_and_text()
        secondary = detect_grid_system("secondary.pdf", secondary_geometry, secondary_items)
        self.grid.secondary_grid_systems = [secondary]
        with patch(
            "shoplens.grids.models.asdict",
            side_effect=AssertionError("deep copy"),
            create=True,
        ):
            payload = self.grid.to_dict(include_hierarchy=False)
        self.assertEqual(payload["grid_system_id"], "PAGE_1_DOMINANT_GRID")
        self.assertEqual(payload["secondary_grid_systems"], [])
        self.assertNotIn("lines", payload["page_geometry"])
        self.assertIn("source_segments", payload["vertical_axes"][0])

    def test_no_grid_found(self):
        empty = PageGeometry(1, 100, 100, 0, (0, 0, 100, 100), (0, 0, 100, 100), "raw", "synthetic")
        result = detect_grid_system("empty.pdf", empty, [text("A", 10, 10)])
        self.assertEqual((result.horizontal_axes, result.vertical_axes), ([], []))
        self.assertIn("NO_DOMINANT_GRID_SYSTEM", result.warnings)

    def test_extra_axis_family_connected_to_dominant_grid_does_not_warn_as_multi_system(self):
        geometry, items = regular_geometry_and_text()
        for label, y in (("7", 100.0), ("8", 300.0), ("9", 500.0)):
            geometry.shapes.append(ellipse(400, y))
            items.append(text(label, 395, y - 5))
            geometry.lines.append(LineSegment(1, 100, y, 900, y))
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertNotIn("MULTIPLE_SIMILAR_GRID_SYSTEMS", result.warnings)

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
        self.assertIn("MULTIPLE_SIMILAR_GRID_SYSTEMS", result.warnings)

    def test_disconnected_grids_with_repeated_axis_labels_remain_separate(self):
        geometry, items = separated_grids_geometry_and_text(repeated_labels=True)
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)

    def test_side_by_side_grids_sharing_rows_do_not_bridge_across_gap(self):
        geometry, items = separated_grids_geometry_and_text(shared_rows=True)
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)
        self.assertTrue(all(
            axis.end_x - axis.start_x < 400.0
            for system in result.all_grid_systems
            for axis in system.horizontal_axes
        ))

    def test_stacked_grids_sharing_columns_do_not_bridge_across_gap(self):
        geometry, items = separated_grids_geometry_and_text(shared_columns=True)
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)
        self.assertTrue(all(
            axis.end_y - axis.start_y < 400.0
            for system in result.all_grid_systems
            for axis in system.vertical_axes
        ))

    def test_incidental_axis_group_does_not_claim_multiple_grid_systems(self):
        geometry, items = regular_geometry_and_text()
        for label, y in (("7", 680.0), ("8", 720.0), ("9", 760.0)):
            geometry.shapes.append(ellipse(400.0, y))
            items.append(text(label, 395.0, y - 5.0))
        geometry.lines.append(LineSegment(1, 100.0, 680.0, 900.0, 680.0))
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertNotIn("MULTIPLE_SIMILAR_GRID_SYSTEMS", result.warnings)
        self.assertNotIn("MULTIPLE_GRID_SYSTEMS_DETECTED", result.warnings)

    def test_fallback_intersections_are_recorded_on_incomplete_grid(self):
        geometry, items = regular_geometry_and_text()
        geometry = replace(
            geometry,
            lines=[
                line for line in geometry.lines
                if abs(line.y1 - line.y2) <= 2.0 or abs((line.x1 + line.x2) / 2.0 - 200.0) <= 2.0
            ],
        )
        result = detect_grid_system("drawing.pdf", geometry, items)
        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 1))
        self.assertEqual([axis.intersection_count for axis in result.horizontal_axes], [1, 1, 1])
        self.assertEqual(result.vertical_axes[0].intersection_count, 3)
        self.assertIn("PERPENDICULAR_INTERSECTIONS:3", result.vertical_axes[0].evidence)

    def test_duplicate_collinear_segment_extents_do_not_break_component_partitioning(self):
        line = LineSegment(1, 10.0, 20.0, 100.0, 20.0, source="first")
        duplicate = LineSegment(1, 10.0, 20.0, 100.0, 20.0, source="second")
        self.assertEqual(
            _line_extent_components([line, duplicate], GridOrientation.HORIZONTAL),
            [[line, duplicate]],
        )

    def test_tiny_collinear_detail_segments_do_not_split_dashed_grid_extent(self):
        grid_segments = [
            LineSegment(1, 10.0, 20.0, 210.0, 20.0),
            LineSegment(1, 220.0, 20.0, 420.0, 20.0),
        ]
        detail_segments = [
            LineSegment(1, 500.0 + index * 2.0, 20.0, 501.0 + index * 2.0, 20.0)
            for index in range(10)
        ]
        self.assertEqual(
            _line_extent_components(
                [*grid_segments, *detail_segments], GridOrientation.HORIZONTAL,
            )[0],
            grid_segments,
        )

    def test_tiny_collinear_strokes_cannot_bridge_separate_horizontal_extents(self):
        left = [
            LineSegment(1, 0.0, 20.0, 200.0, 20.0, source="left-1"),
            LineSegment(1, 210.0, 20.0, 410.0, 20.0, source="left-2"),
        ]
        right = [
            LineSegment(1, 700.0, 20.0, 900.0, 20.0, source="right-1"),
            LineSegment(1, 910.0, 20.0, 1110.0, 20.0, source="right-2"),
        ]
        bridge = [
            LineSegment(1, 430.0 + index * 20.0, 20.0, 435.0 + index * 20.0, 20.0)
            for index in range(13)
        ]

        components = _line_extent_components(
            [*left, *bridge, *right], GridOrientation.HORIZONTAL,
        )
        substantial_components = [
            component
            for component in components
            if any(line.length >= 20.0 for line in component)
        ]

        self.assertEqual(substantial_components, [left, right])
        self.assertNotIn(bridge, components)

    def test_tiny_collinear_strokes_cannot_bridge_separate_vertical_extents(self):
        lower = [
            LineSegment(1, 20.0, 0.0, 20.0, 200.0, source="lower-1"),
            LineSegment(1, 20.0, 210.0, 20.0, 410.0, source="lower-2"),
        ]
        upper = [
            LineSegment(1, 20.0, 700.0, 20.0, 900.0, source="upper-1"),
            LineSegment(1, 20.0, 910.0, 20.0, 1110.0, source="upper-2"),
        ]
        bridge = [
            LineSegment(1, 20.0, 430.0 + index * 20.0, 20.0, 435.0 + index * 20.0)
            for index in range(13)
        ]

        components = _line_extent_components(
            [*lower, *bridge, *upper], GridOrientation.VERTICAL,
        )
        substantial_components = [
            component
            for component in components
            if any(line.length >= 20.0 for line in component)
        ]

        self.assertEqual(substantial_components, [lower, upper])
        self.assertNotIn(bridge, components)

    def test_label_closest_to_ambiguous_minor_bridge_selects_structural_extent(self):
        left = [
            LineSegment(1, 0.0, 20.0, 200.0, 20.0),
            LineSegment(1, 210.0, 20.0, 410.0, 20.0),
        ]
        right = [
            LineSegment(1, 700.0, 20.0, 900.0, 20.0),
            LineSegment(1, 910.0, 20.0, 1110.0, 20.0),
        ]
        bridge = [
            LineSegment(1, 430.0 + index * 20.0, 20.0, 435.0 + index * 20.0, 20.0)
            for index in range(13)
        ]
        label = GridLabel("A", "A", 1, 545.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        components = _line_extent_components(
            [*left, *bridge, *right], GridOrientation.HORIZONTAL,
        )
        selected = _matching_line_component(
            [*left, *bridge, *right], GridOrientation.HORIZONTAL, [label],
        )

        self.assertNotIn(bridge, components)
        self.assertEqual(selected, left)

    def test_endpoint_label_does_not_make_ambiguous_minor_bridge_selectable(self):
        left = [
            LineSegment(1, 0.0, 20.0, 200.0, 20.0),
            LineSegment(1, 210.0, 20.0, 410.0, 20.0),
        ]
        right = [
            LineSegment(1, 700.0, 20.0, 900.0, 20.0),
            LineSegment(1, 910.0, 20.0, 1110.0, 20.0),
        ]
        bridge = [
            LineSegment(1, 430.0 + index * 20.0, 20.0, 435.0 + index * 20.0, 20.0)
            for index in range(13)
        ]
        label = GridLabel("A", "A", 1, 425.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        selected = _matching_line_component(
            [*left, *bridge, *right], GridOrientation.HORIZONTAL, [label],
        )

        self.assertEqual(selected, left)

    def test_endpoint_label_does_not_reinstate_multi_compatible_minor_run(self):
        left = [LineSegment(1, 0.0, 20.0, 200.0, 20.0)]
        right = [LineSegment(1, 700.0, 20.0, 900.0, 20.0)]
        segmented_axis = [
            LineSegment(1, 220.0 + index * 20.0, 20.0, 225.0 + index * 20.0, 20.0)
            for index in range(23)
        ]

        components = _line_extent_components(
            [*left, *segmented_axis, *right],
            GridOrientation.HORIZONTAL,
            label_positions=[215.0],
        )

        self.assertNotIn(segmented_axis, components)

    def test_label_rooted_segmented_run_traverses_short_primitives(self):
        segments = [
            LineSegment(1, 0.0, 20.0, 180.0, 20.0),
            *[
                LineSegment(1, 190.0 + index * 15.0, 20.0, 195.0 + index * 15.0, 20.0)
                for index in range(30)
            ],
            LineSegment(1, 650.0, 20.0, 830.0, 20.0),
        ]
        label = GridLabel("A", "A", 1, -15.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        selected = _matching_line_component(
            segments, GridOrientation.HORIZONTAL, [label],
        )

        self.assertEqual(selected, segments)

    def test_label_supported_dashed_run_crosses_small_recurring_gaps(self):
        segments = [
            LineSegment(1, index * 22.4, 20.0, index * 22.4 + 18.0, 20.0)
            for index in range(30)
        ]
        label = GridLabel("A", "A", 1, 667.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        selected = _matching_line_component(
            segments, GridOrientation.HORIZONTAL, [label],
        )

        self.assertEqual(selected, segments)

    def test_label_rooted_recurring_structural_fragments_cross_large_gaps(self):
        fragments = [
            LineSegment(1, index * 180.0, 20.0, index * 180.0 + 100.0, 20.0)
            for index in range(5)
        ]
        label = GridLabel("A", "A", 1, -15.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        selected = _matching_line_component(
            fragments, GridOrientation.HORIZONTAL, [label],
        )

        self.assertEqual(selected, fragments)

    def test_label_rooted_run_ignores_nested_detail_strokes_for_cadence(self):
        fragments = [
            LineSegment(1, index * 120.0, 20.0, index * 120.0 + 100.0, 20.0)
            for index in range(5)
        ]
        details = [
            LineSegment(1, index * 120.0 + 20.0, 20.0, index * 120.0 + 35.0, 20.0)
            for index in range(5)
            for _ in range(4)
        ]
        label = GridLabel("A", "A", 1, -15.0, 15.0, 10.0, 10.0, "ELLIPSE", 0.8)

        selected = _matching_line_component(
            [*fragments, *details], GridOrientation.HORIZONTAL, [label],
        )

        self.assertTrue(all(fragment in selected for fragment in fragments))

    def test_fragmented_axis_with_intermittent_intersections_stays_structural(self):
        lines = []
        shapes = []
        items = []
        for label, x in (("A", 100.0), ("B", 200.0), ("C", 300.0)):
            shapes.append(ellipse(x, 20.0))
            items.append(text(label, x - 5.0, 15.0))
            lines.append(LineSegment(1, x, 40.0, x, 360.0))
        for label, y in (("1", 100.0), ("2", 200.0), ("3", 300.0)):
            shapes.append(ellipse(20.0, y))
            items.append(text(label, 15.0, y - 5.0))
            fragments = [
                LineSegment(1, start, y, start + 100.0, y)
                for start in (40.0, 160.0, 280.0)
            ]
            details = [
                LineSegment(1, start + 20.0, y, start + 35.0, y)
                for start in (40.0, 160.0, 280.0)
                for _ in range(4)
            ]
            lines.extend([*fragments, *details])
        geometry = PageGeometry(
            1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
            "raw", "synthetic", lines=lines, shapes=shapes,
        )

        result = detect_grid_system("drawing.pdf", geometry, items)

        self.assertEqual([axis.normalized_label for axis in result.horizontal_axes], ["1", "2", "3"])
        self.assertTrue(all(axis.intersection_count == 3 for axis in result.horizontal_axes))

    def test_perpendicular_support_recovers_label_rooted_full_extent(self):
        label = GridLabel("EB2", "EB2", 1, 95.0, 390.0, 10.0, 10.0, "ELLIPSE", 0.8)
        fragments = [
            LineSegment(1, 100.0, start, 100.0, start + 12.0)
            for start in (40.0, 120.0, 200.0, 280.0, 360.0)
        ]
        horizontal = [
            GridAxis(
                f"H:{index}", GridOrientation.HORIZONTAL, str(index), [], y,
                40.0, y, 360.0, y, [], [], 0, 0.8,
            )
            for index, y in enumerate((60.0, 140.0, 220.0, 300.0, 365.0), start=1)
        ]
        geometry = PageGeometry(
            1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
            "raw", "synthetic", lines=fragments,
        )

        recovered = _recover_perpendicular_supported_axes(
            GridOrientation.VERTICAL, [label], fragments, geometry, horizontal,
        )

        self.assertEqual([axis.normalized_label for axis in recovered], ["EB2"])
        self.assertEqual(recovered[0].intersection_count, 5)

    def test_secondary_system_recovers_its_own_fragmented_axis(self):
        geometry, items = primary_and_recoverable_secondary_geometry_and_text()

        result = detect_grid_system("drawing.pdf", geometry, items)

        self.assertEqual((len(result.horizontal_axes), len(result.vertical_axes)), (3, 3))
        self.assertEqual(len(result.secondary_grid_systems), 1)
        self.assertEqual(
            [axis.normalized_label for axis in result.secondary_grid_systems[0].vertical_axes],
            ["S1", "S2", "S3"],
        )
        self.assertNotIn("S3", [label.normalized_label for label in result.unassigned_labels])

    def test_fixed_point_recovery_retries_horizontal_after_vertical_recovery(self):
        horizontal, vertical, labels, lines, geometry = fragmented_recovery_geometry_and_labels()

        recovered_horizontal, recovered_vertical = _recover_candidate_axes_to_fixed_point(
            horizontal, vertical, labels, lines, geometry,
        )

        self.assertEqual([axis.normalized_label for axis in recovered_horizontal], ["2"])
        self.assertEqual([axis.normalized_label for axis in recovered_vertical], ["B"])
        self.assertEqual(recovered_horizontal[0].intersection_count, 2)
        self.assertEqual(recovered_vertical[0].intersection_count, 3)

    def test_fixed_point_recovery_retries_vertical_after_horizontal_recovery(self):
        horizontal = [
            recovery_axis("H:1", GridOrientation.HORIZONTAL, "1", 80.0, 40.0, 332.0),
        ]
        vertical = [
            recovery_axis("V:A", GridOrientation.VERTICAL, "A", 80.0, 40.0, 332.0),
            recovery_axis("V:C", GridOrientation.VERTICAL, "C", 240.0, 40.0, 332.0),
        ]
        lines = [
            *[
                LineSegment(1, start, 300.0, start + 12.0, 300.0)
                for start in (40.0, 120.0, 200.0, 280.0, 320.0)
            ],
            *[
                LineSegment(1, 100.0, start, 100.0, start + 12.0)
                for start in (40.0, 120.0, 200.0, 280.0, 320.0)
            ],
        ]
        labels = [
            GridLabel("2", "2", 1, 20.0, 295.0, 10.0, 10.0, "ELLIPSE", 0.8),
            GridLabel("B", "B", 1, 95.0, 20.0, 10.0, 10.0, "ELLIPSE", 0.8),
        ]
        geometry = PageGeometry(
            1, 400, 400, 0, (0, 0, 400, 400), (0, 0, 400, 400),
            "raw", "synthetic", lines=lines,
        )

        recovered_horizontal, recovered_vertical = _recover_candidate_axes_to_fixed_point(
            horizontal, vertical, labels, lines, geometry,
        )

        self.assertEqual([axis.normalized_label for axis in recovered_horizontal], ["2"])
        self.assertEqual([axis.normalized_label for axis in recovered_vertical], ["B"])
        self.assertEqual(recovered_horizontal[0].intersection_count, 3)
        self.assertEqual(recovered_vertical[0].intersection_count, 2)

    def test_ambiguous_recovery_label_is_not_claimed_by_two_systems(self):
        first_vertical = [
            recovery_axis("V:A", GridOrientation.VERTICAL, "A", 80.0, 40.0, 160.0),
            recovery_axis("V:B", GridOrientation.VERTICAL, "B", 200.0, 40.0, 160.0),
        ]
        second_vertical = [
            recovery_axis("V:C", GridOrientation.VERTICAL, "C", 600.0, 40.0, 160.0),
            recovery_axis("V:D", GridOrientation.VERTICAL, "D", 720.0, 40.0, 160.0),
        ]
        lines = [
            LineSegment(1, start, 100.0, start + 12.0, 100.0)
            for start in (40.0, 180.0, 320.0, 460.0, 600.0, 740.0)
        ]
        label = GridLabel("1", "1", 1, 20.0, 95.0, 10.0, 10.0, "ELLIPSE", 0.8)
        geometry = PageGeometry(
            1, 800, 400, 0, (0, 0, 800, 400), (0, 0, 800, 400),
            "raw", "synthetic", lines=lines,
        )

        recovered = _recover_candidate_systems_to_fixed_point(
            [([], first_vertical, 0), ([], second_vertical, 0)],
            [label], lines, geometry,
        )

        self.assertEqual([len(horizontal) for horizontal, _, _ in recovered], [0, 0])

    def test_ranking_uses_axis_counts_after_recovery(self):
        first = (
            [recovery_axis("H:1", GridOrientation.HORIZONTAL, "1", 100.0, 40.0, 360.0)],
            [
                recovery_axis("V:A", GridOrientation.VERTICAL, "A", 100.0, 40.0, 360.0),
                recovery_axis("V:B", GridOrientation.VERTICAL, "B", 200.0, 40.0, 360.0),
            ],
            2,
        )
        recovered_secondary = (
            [
                recovery_axis("H:2", GridOrientation.HORIZONTAL, "2", 100.0, 440.0, 760.0),
                recovery_axis("H:3", GridOrientation.HORIZONTAL, "3", 200.0, 440.0, 760.0),
            ],
            [
                recovery_axis("V:C", GridOrientation.VERTICAL, "C", 500.0, 40.0, 360.0),
                recovery_axis("V:D", GridOrientation.VERTICAL, "D", 600.0, 40.0, 360.0),
                recovery_axis("V:E", GridOrientation.VERTICAL, "E", 700.0, 40.0, 360.0),
            ],
            6,
        )

        ranked = _rank_system_candidates([first, recovered_secondary])

        self.assertIs(ranked[0], recovered_secondary)

    def test_final_intersections_are_symmetric_and_idempotent(self):
        horizontal = [
            recovery_axis("H:1", GridOrientation.HORIZONTAL, "1", 100.0, 40.0, 360.0),
            recovery_axis("H:2", GridOrientation.HORIZONTAL, "2", 200.0, 40.0, 360.0),
        ]
        vertical = [
            recovery_axis("V:A", GridOrientation.VERTICAL, "A", 100.0, 40.0, 360.0),
            recovery_axis("V:B", GridOrientation.VERTICAL, "B", 300.0, 40.0, 360.0),
        ]
        horizontal[0].evidence.append("PERPENDICULAR_INTERSECTIONS:99")

        _set_intersections(horizontal, vertical)
        first = [(axis.intersection_count, axis.confidence, list(axis.evidence)) for axis in [*horizontal, *vertical]]
        _set_intersections(horizontal, vertical)
        second = [(axis.intersection_count, axis.confidence, list(axis.evidence)) for axis in [*horizontal, *vertical]]

        self.assertEqual([axis.intersection_count for axis in horizontal], [2, 2])
        self.assertEqual([axis.intersection_count for axis in vertical], [2, 2])
        self.assertEqual(first, second)
        self.assertTrue(all(
            sum(item.startswith("PERPENDICULAR_INTERSECTIONS:") for item in axis.evidence) == 1
            for axis in [*horizontal, *vertical]
        ))

    def test_isolated_minor_component_remains_selectable_geometry(self):
        left = [LineSegment(1, 0.0, 20.0, 200.0, 20.0)]
        right = [LineSegment(1, 700.0, 20.0, 900.0, 20.0)]
        isolated = [LineSegment(1, 1200.0, 20.0, 1205.0, 20.0)]

        self.assertEqual(
            _line_extent_components(
                [*left, *right, *isolated], GridOrientation.HORIZONTAL,
            ),
            [left, right, isolated],
        )

    def test_minor_segments_attach_locally_without_breaking_continuous_axis(self):
        axis = [
            LineSegment(1, 0.0, 20.0, 200.0, 20.0),
            LineSegment(1, 230.0, 20.0, 430.0, 20.0),
        ]
        local_detail = LineSegment(1, 445.0, 20.0, 450.0, 20.0)

        self.assertEqual(
            _line_extent_components(
                [*axis, local_detail], GridOrientation.HORIZONTAL,
            ),
            [[*axis, local_detail]],
        )

    def test_degenerate_minor_only_input_has_conservative_empty_fallback(self):
        self.assertEqual(
            _line_extent_components(
                [
                    LineSegment(1, 10.0, 20.0, 10.0, 20.0),
                    LineSegment(1, 20.0, 20.0, 20.0, 20.0),
                ],
                GridOrientation.HORIZONTAL,
            ),
            [],
        )

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
