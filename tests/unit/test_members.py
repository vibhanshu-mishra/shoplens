"""Synthetic member-line candidate detection, filtering, CLI, JSON, and SVG tests."""

import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.classification.models import StructuralSubject
from shoplens.geometry import LineSegment, PageGeometry
from shoplens.grids.models import GridAxis, GridOrientation, GridSystem
from shoplens.members import (
    LineOrientation,
    detect_member_line_candidates,
    export_member_candidates_svg,
    filter_member_candidates,
)


def axis(label, orientation, coordinate):
    horizontal = orientation == GridOrientation.HORIZONTAL
    return GridAxis(
        f"{orientation.value}:{label}", orientation, label, [], coordinate,
        100 if horizontal else coordinate, coordinate if horizontal else 100,
        900 if horizontal else coordinate, coordinate if horizontal else 900,
        [], [], 3, 0.9,
    )


def grid(lines=None):
    geometry = PageGeometry(
        1, 1000, 1000, 0, (0, 0, 1000, 1000), (0, 0, 1000, 1000),
        "PDF_USER_SPACE_BOTTOM_LEFT", "synthetic", list(lines or []), [], [], "identity",
    )
    return GridSystem(
        "drawing.pdf", 1, "S1-20A", "PLAN", StructuralSubject.FLOOR_FRAMING,
        "SECOND FLOOR", "A", geometry,
        [axis(label, GridOrientation.HORIZONTAL, coordinate) for label, coordinate in (("1", 200), ("2", 500), ("3", 800))],
        [axis(label, GridOrientation.VERTICAL, coordinate) for label, coordinate in (("A", 200), ("B", 500), ("C", 800))],
        [], [], 0.9, [],
    )


def detect(lines, text=()):
    system = grid(lines)
    return detect_member_line_candidates("drawing.pdf", system.page_geometry, system, text)


def assert_stage_invariants(testcase, result):
    testcase.assertEqual(
        result.raw_segment_count,
        result.duplicate_segment_count + result.deduplicated_segment_count,
    )
    testcase.assertEqual(
        result.deduplicated_segment_count,
        result.primitive_segments_rejected_count
        + result.primitive_segments_entering_merge_count,
    )
    testcase.assertEqual(
        result.merged_chain_count,
        result.accepted_candidate_count + result.rejected_chain_count,
    )


class MemberCandidateTests(unittest.TestCase):
    def test_horizontal_vertical_diagonal_and_grid_locations(self):
        result = detect([
            LineSegment(1, 200, 350, 500, 350, 1.0),
            LineSegment(1, 350, 200, 350, 500, 1.0),
            LineSegment(1, 200, 200, 500, 500, 1.0),
        ])
        self.assertEqual({item.orientation_class for item in result.candidates}, {
            LineOrientation.HORIZONTAL, LineOrientation.VERTICAL, LineOrientation.DIAGONAL,
        })
        horizontal = next(item for item in result.candidates if item.orientation_class == LineOrientation.HORIZONTAL)
        self.assertEqual(horizontal.crossed_vertical_grids, ["A", "B"])
        self.assertEqual(horizontal.start_grid_location, "ON A / 1–2")
        self.assertTrue(horizontal.start_near_grid)

    def test_slight_rotation_classes(self):
        result = detect([
            LineSegment(1, 200, 350, 500, 352, 1.0),
            LineSegment(1, 350, 200, 352, 500, 1.0),
        ])
        self.assertEqual(
            {item.orientation_class for item in result.candidates},
            {LineOrientation.HORIZONTAL, LineOrientation.VERTICAL},
        )

    def test_reversed_and_exact_duplicates_but_parallel_distinct(self):
        result = detect([
            LineSegment(1, 200, 350, 500, 350, 1.0),
            LineSegment(1, 500, 350, 200, 350, 1.0),
            LineSegment(1, 200, 350, 500, 350, 1.0),
            LineSegment(1, 200, 360, 500, 360, 1.0),
        ])
        self.assertEqual(result.raw_segment_count, 4)
        self.assertEqual(result.duplicate_segment_count, 2)
        self.assertEqual(result.deduplicated_segment_count, 2)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(Counter(item.rejection_reason for item in result.rejected_candidates)["DUPLICATE_GEOMETRY"], 2)
        assert_stage_invariants(self, result)

    def test_touching_and_small_gap_merge_but_large_gap_does_not(self):
        touching = detect([
            LineSegment(1, 200, 350, 340, 350, 1.0),
            LineSegment(1, 340, 350, 500, 350, 1.0),
        ])
        self.assertEqual(touching.candidates[0].source_segment_count, 2)
        self.assertEqual(touching.primitive_segments_entering_merge_count, 2)
        self.assertEqual(touching.merged_chain_count, 1)
        small_gap = detect([
            LineSegment(1, 200, 350, 340, 350, 1.0),
            LineSegment(1, 344, 350, 500, 350, 1.0),
        ])
        self.assertEqual(small_gap.candidates[0].source_segment_count, 2)
        large_gap = detect([
            LineSegment(1, 200, 350, 320, 350, 1.0),
            LineSegment(1, 340, 350, 500, 350, 1.0),
        ])
        self.assertEqual(large_gap.merged_chain_count, 2)

    def test_different_width_and_dash_do_not_merge(self):
        result = detect([
            LineSegment(1, 200, 350, 340, 350, 1.0),
            LineSegment(1, 340, 350, 500, 350, 2.0),
            LineSegment(1, 200, 400, 340, 400, 1.0, (4, 2)),
            LineSegment(1, 340, 400, 500, 400, 1.0, (8, 2)),
        ])
        self.assertEqual(result.merged_chain_count, 4)

    def test_grid_line_rejected_but_parallel_offset_accepted(self):
        result = detect([
            LineSegment(1, 100, 500, 900, 500, 0.5, (8, 4)),
            LineSegment(1, 200, 520, 800, 520, 1.0),
        ])
        self.assertIn("GRID_AXIS_GEOMETRY", {item.rejection_reason for item in result.rejected_candidates})
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].start_y, 520)

    def test_dimension_line_and_extension_ticks_rejected(self):
        result = detect([
            LineSegment(1, 300, 300, 450, 300, 0.5),
            LineSegment(1, 300, 295, 300, 305, 0.5),
            LineSegment(1, 450, 295, 450, 305, 0.5),
        ])
        reasons = {item.rejection_reason for item in result.rejected_candidates}
        self.assertIn("LIKELY_DIMENSION_LINE", reasons)
        self.assertIn("TOO_SHORT", reasons)

    def test_bent_leader_and_short_annotation_rejected(self):
        text = [SimpleNamespace(x=390, y=390, width=40, height=12)]
        result = detect([
            LineSegment(1, 350, 350, 400, 400, 0.5),
            LineSegment(1, 400, 400, 420, 400, 0.5),
            LineSegment(1, 600, 600, 608, 600, 0.5),
        ], text)
        reasons = {item.rejection_reason for item in result.rejected_candidates}
        self.assertIn("LIKELY_LEADER", reasons)
        self.assertIn("TOO_SHORT", reasons)

    def test_page_title_detail_schedule_and_outside_rejections(self):
        schedule = [LineSegment(1, 850 + index % 6 * 3, 700 + index // 6 * 3, 870 + index % 6 * 3, 700 + index // 6 * 3) for index in range(18)]
        result = detect([
            LineSegment(1, 0, 0, 1000, 0),
            LineSegment(1, 800, 100, 950, 100),
            LineSegment(1, 50, 700, 400, 700),
            LineSegment(1, 50, 650, 70, 650),
            *schedule,
        ])
        reasons = {item.rejection_reason for item in result.rejected_candidates}
        self.assertIn("PAGE_BORDER", reasons)
        self.assertIn("TITLE_BLOCK_GEOMETRY", reasons)
        self.assertIn("DETAIL_BORDER", reasons)
        self.assertIn("OUTSIDE_PLAN_REGION", reasons)
        self.assertIn("SCHEDULE_GEOMETRY", reasons)

    def test_plan_margin_and_outside_plan(self):
        result = detect([
            LineSegment(1, 170, 350, 300, 350, 1.0),
            LineSegment(1, 100, 350, 150, 350, 1.0),
        ])
        self.assertTrue(any(item.start_x == 170 for item in result.candidates))
        self.assertIn("OUTSIDE_PLAN_REGION", {item.rejection_reason for item in result.rejected_candidates})

    def test_low_confidence_and_filters(self):
        result = detect([
            LineSegment(1, 310, 310, 330, 310, 1.0),
            LineSegment(1, 200, 350, 500, 350, 1.0),
            LineSegment(1, 350, 200, 350, 500, 1.0),
        ])
        self.assertIn("LOW_CANDIDATE_CONFIDENCE", {item.rejection_reason for item in result.rejected_candidates})
        self.assertEqual(result.merged_chain_count, 3)
        self.assertEqual(result.accepted_candidate_count, 2)
        self.assertEqual(result.rejected_chain_count, 1)
        assert_stage_invariants(self, result)
        self.assertEqual(len(filter_member_candidates(result.candidates, LineOrientation.HORIZONTAL)), 1)
        self.assertEqual(len(filter_member_candidates(result.candidates, min_confidence=0.8)), 2)
        candidate = result.candidates[0]
        self.assertEqual(filter_member_candidates(result.candidates, candidate_id=candidate.candidate_id), [candidate])

    def test_json_and_svg(self):
        result = detect([LineSegment(1, 200, 350, 500, 350, 1.0)])
        payload = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(payload["candidates"][0]["candidate_type"], "LINEAR_MEMBER_CANDIDATE")
        self.assertIn("source_segments", payload["candidates"][0])
        for field in (
            "duplicate_segment_count", "primitive_segments_rejected_count",
            "primitive_segments_entering_merge_count", "rejected_chain_count",
        ):
            self.assertIn(field, payload)
        self.assertEqual(payload["duplicate_segment_count"], 0)
        self.assertEqual(payload["primitive_segments_entering_merge_count"], 1)
        self.assertEqual(payload["rejected_candidate_count"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "members.svg"
            export_member_candidates_svg(path, result, result.candidates, include_rejected=True)
            value = path.read_text(encoding="utf-8")
        self.assertIn("MLC-0001", value)
        self.assertNotIn("data:image", value)

    def test_no_candidates(self):
        result = detect([LineSegment(1, 300, 300, 304, 300)])
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.duplicate_segment_count, 0)
        self.assertEqual(result.primitive_segments_rejected_count, 1)
        self.assertEqual(result.primitive_segments_entering_merge_count, 0)
        self.assertEqual(result.merged_chain_count, 0)
        self.assertEqual(result.rejected_chain_count, 0)
        assert_stage_invariants(self, result)
        self.assertIn("NO_MEMBER_LINE_CANDIDATES", result.warnings)


class MemberCandidateCliTests(unittest.TestCase):
    def run_cli(self, selector, extra=None):
        lines = [LineSegment(1, 200, 350, 500, 350, 1.0)]
        system = grid(lines)
        sheet = SimpleNamespace(
            sheet_number="S1-20A", pdf_page=1, actual_pdf_pages=[1], actual_title="PLAN",
            declared_title="PLAN", subject=StructuralSubject.FLOOR_FRAMING,
            level="SECOND FLOOR", segment="A",
        )
        output = io.StringIO()
        with patch.object(cli, "_extract_package_title_blocks_with_items", return_value=(object(), object(), [], 0)), \
             patch.object(cli, "reconcile_sheets", return_value=object()), \
             patch.object(cli, "build_package_index", return_value=SimpleNamespace(sheets=[sheet])), \
             patch.object(cli, "extract_page_geometry", return_value=[system.page_geometry]), \
             patch.object(cli, "detect_grid_system", return_value=system):
            with redirect_stdout(output):
                status = cli.main(["member-line-candidates", "drawing.pdf", *selector, *(extra or [])])
        return status, output.getvalue()

    def test_sheet_lookup_list_and_filter(self):
        status, output = self.run_cli(["--sheet", "s1-20a"], ["--orientation", "HORIZONTAL", "--list"])
        self.assertEqual(status, 0)
        self.assertIn("MLC-0001", output)
        self.assertIn("Accepted member-line candidates: 1", output)
        self.assertIn("Duplicate segments suppressed: 0", output)
        self.assertIn("Primitive segments rejected before merging: 0", output)
        self.assertIn("Rejected chains: 0", output)
        self.assertNotIn("Rejected records", output)
        self.assertNotIn("Rejected geometry records", output)

    def test_debug_output_reconciles_stages(self):
        status, output = self.run_cli(["--sheet", "s1-20a"], ["--debug"])
        self.assertEqual(status, 0)
        self.assertIn("Stage accounting:", output)
        self.assertIn("raw = duplicates + unique: 1 = 0 + 1", output)
        self.assertIn("unique = primitive rejected + entering merge: 1 = 0 + 1", output)
        self.assertIn("chains = accepted + rejected: 1 = 1 + 0", output)

    def test_page_lookup_json(self):
        status, output = self.run_cli(["--page", "1"], ["--json", "--include-rejected"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["active_filters"], {"page": 1})
        self.assertEqual(payload["filtered_candidate_count"], 1)

    def test_selector_required(self):
        with self.assertRaises(SystemExit):
            cli.main(["member-line-candidates", "drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
