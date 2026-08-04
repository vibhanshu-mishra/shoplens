"""Synthetic neutral repetitive linear-pattern tests."""

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.classification.models import StructuralSubject
from shoplens.geometry import LineSegment
from shoplens.patterns import (
    LinearPatternType,
    detect_linear_patterns,
    export_linear_patterns_svg,
    filter_linear_patterns,
)
from tests.unit.test_members import detect, grid


class LinearPatternTests(unittest.TestCase):
    def pattern_result(self, lines):
        return detect_linear_patterns(detect(lines))

    def test_regular_horizontal_vertical_and_grid_metadata(self):
        horizontal = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0) for y in (300, 350, 400, 450)
        ])
        pattern = horizontal.patterns[0]
        self.assertEqual(pattern.pattern_type, LinearPatternType.REGULAR_SPACING_FIELD)
        self.assertEqual(pattern.primary_orientation, "HORIZONTAL")
        self.assertEqual(pattern.spacing_values, [50, 50, 50])
        self.assertEqual(pattern.regular_spacing_score, 1.0)
        self.assertEqual(pattern.crossed_vertical_grids, ["A", "B"])
        vertical = self.pattern_result([
            LineSegment(1, x, 200, x, 500, 1.0) for x in (300, 350, 400)
        ])
        self.assertEqual(vertical.patterns[0].primary_orientation, "VERTICAL")

    def test_diagonal_families_are_separate_and_tolerate_small_rotation(self):
        result = self.pattern_result([
            LineSegment(1, 250, 250 + offset, 450, 450 + offset, 1.0)
            for offset in (0, 30, 61)
        ] + [
            LineSegment(1, 550, 250 + offset, 350, 450 + offset, 1.0)
            for offset in (0, 30, 60)
        ])
        self.assertEqual(result.pattern_count, 2)
        angles = sorted(round(item.principal_angle) for item in result.patterns)
        self.assertEqual(angles, [45, 135])

    def test_distant_parallel_groups_remain_separate(self):
        result = self.pattern_result([
            LineSegment(1, 200, y, 400, y, 1.0) for y in (250, 280, 310)
        ] + [
            LineSegment(1, 600, y, 800, y, 1.0) for y in (650, 680, 710)
        ])
        self.assertEqual(result.pattern_count, 2)

    def test_missing_slot_outlier_and_irregular_spacing(self):
        missing = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0) for y in (250, 300, 350, 450, 500)
        ]).patterns[0]
        self.assertEqual(missing.missing_slot_count, 1)
        self.assertEqual(missing.spacing_outliers, [100])
        irregular = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0) for y in (250, 280, 370, 490)
        ]).patterns[0]
        self.assertIn("IRREGULAR_SPACING", irregular.warnings)

    def test_endpoint_bands_length_variation_and_collinear_chain(self):
        variable = self.pattern_result([
            LineSegment(1, 200, y, end, y, 1.0)
            for y, end in ((300, 500), (350, 550), (400, 650))
        ]).patterns[0]
        self.assertGreater(variable.length_variation, 0)
        aligned = self.pattern_result([
            LineSegment(1, 200, 350, 280, 350, 1.0),
            LineSegment(1, 330, 350, 410, 350, 1.0),
            LineSegment(1, 460, 350, 540, 350, 1.0),
        ]).patterns[0]
        self.assertEqual(aligned.pattern_type, LinearPatternType.COLLINEAR_CHAIN_GROUP)

    def test_repeated_double_line_pairs_and_unpaired_candidate(self):
        result = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0)
            for y in (250, 255, 320, 325, 390, 395, 470)
        ])
        pattern = result.patterns[0]
        self.assertEqual(pattern.pattern_type, LinearPatternType.DOUBLE_LINE_PAIR_GROUP)
        self.assertEqual(pattern.pair_count, 3)
        self.assertIn(pattern.unpaired_candidate_ids[0], pattern.candidate_ids)

    def test_orthogonal_network_uses_secondary_membership_without_double_counting(self):
        result = self.pattern_result([
            *[LineSegment(1, 200, y, 500, y, 1.0) for y in (300, 350, 400)],
            *[LineSegment(1, x, 200, x, 500, 1.0) for x in (300, 350, 400)],
        ])
        self.assertEqual(result.hierarchical_pattern_count, 1)
        self.assertEqual(result.hierarchical_patterns[0].pattern_type, LinearPatternType.ORTHOGONAL_NETWORK)
        primary_ids = [candidate_id for item in result.patterns for candidate_id in item.candidate_ids]
        self.assertEqual(len(primary_ids), len(set(primary_ids)))
        self.assertEqual(
            result.input_candidate_count,
            result.primary_clustered_candidate_count + result.unclustered_candidate_count,
        )
        self.assertEqual(result.secondary_membership_count, 6)

    def test_maximal_network_suppresses_pairwise_combinations(self):
        lines = []
        for width, offsets in ((1.0, (260, 290, 320)), (2.0, (380, 410, 440))):
            lines.extend(LineSegment(1, 220, y, 500, y, width) for y in offsets)
            lines.extend(LineSegment(1, x, 220, x, 500, width) for x in offsets)
        first = self.pattern_result(lines)
        second = self.pattern_result(lines)
        self.assertEqual(first.pattern_count, 4)
        self.assertEqual(first.orthogonal_network_count, 1)
        self.assertEqual(first.redundant_networks_suppressed_count, 3)
        network = first.hierarchical_patterns[0]
        self.assertEqual(network.horizontal_component_count, 2)
        self.assertEqual(network.vertical_component_count, 2)
        self.assertEqual(network.unique_primary_candidate_count, 12)
        self.assertEqual(network.candidate_coverage_fraction, 1.0)
        self.assertEqual(first.network_candidates_unique_count, 12)
        self.assertEqual(first.secondary_network_membership_count, 12)
        self.assertEqual(
            network.network_signature,
            second.hierarchical_patterns[0].network_signature,
        )

    def test_spatially_separate_networks_remain_separate(self):
        lines = []
        for offset in (0, 400):
            lines.extend(
                LineSegment(1, 220 + offset, y + offset, 400 + offset, y + offset, 1.0)
                for y in (250, 280, 310)
            )
            lines.extend(
                LineSegment(1, x + offset, 220 + offset, x + offset, 400 + offset, 1.0)
                for x in (250, 280, 310)
            )
        result = self.pattern_result(lines)
        self.assertEqual(result.orthogonal_network_count, 2)
        self.assertEqual(result.redundant_networks_suppressed_count, 0)
        self.assertTrue(all(len(item.component_pattern_ids) == 2 for item in result.hierarchical_patterns))
        self.assertTrue(all(not item.overlap_with_other_networks for item in result.hierarchical_patterns))
        self.assertEqual(result.input_candidate_count, result.primary_clustered_candidate_count)

    def test_dense_field_and_nearby_text_remains_non_binding(self):
        y = 220
        lines = []
        for index in range(55):
            y += 4 + (index * index % 9)
            lines.append(LineSegment(1, 220, y, 760, y, 1.0))
        pattern = self.pattern_result(lines).patterns[0]
        self.assertEqual(pattern.pattern_type, LinearPatternType.DENSE_LINEAR_FIELD)
        self.assertEqual(pattern.nearby_section_labels, [])
        self.assertEqual(pattern.nearby_section_count, 0)
        self.assertEqual(
            pattern.non_binding_text_context,
            "NON_BINDING_TEXT_CONTEXT_NOT_EVALUATED",
        )

    def test_isolated_no_patterns_filters_and_json(self):
        result = self.pattern_result([LineSegment(1, 200, 350, 500, 350, 1.0)])
        self.assertEqual(result.patterns, [])
        self.assertEqual(result.unclustered_candidate_count, 1)
        self.assertIn("NO_LINEAR_PATTERNS", result.warnings)
        regular = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0) for y in (300, 350, 400)
        ])
        self.assertEqual(len(filter_linear_patterns(regular.patterns, regular_only=True)), 1)
        self.assertEqual(len(filter_linear_patterns(regular.patterns, orientation="VERTICAL")), 0)
        self.assertEqual(len(filter_linear_patterns(regular.patterns, min_confidence=0.99)), 0)
        payload = json.loads(json.dumps(regular.to_dict(include_candidates=False)))
        self.assertEqual(payload["input_candidate_count"], 3)
        self.assertEqual(payload["patterns"][0]["source_candidates"], [])

    def test_svg_contains_pattern_labels_and_no_pdf_image(self):
        result = self.pattern_result([
            LineSegment(1, 200, y, 500, y, 1.0) for y in (300, 350, 400)
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patterns.svg"
            export_linear_patterns_svg(path, result, result.patterns)
            value = path.read_text(encoding="utf-8")
        self.assertIn("LP-0001", value)
        self.assertIn("REGULAR_SPACING_FIELD", value)
        self.assertNotIn("data:image", value)


class LinearPatternCliTests(unittest.TestCase):
    def run_cli(self, selector, extra=None):
        lines = [LineSegment(1, 200, y, 500, y, 1.0) for y in (300, 350, 400)]
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
                status = cli.main(["linear-patterns", "drawing.pdf", *selector, *(extra or [])])
        return status, output.getvalue()

    def test_sheet_lookup_list_filter_and_accounting(self):
        status, output = self.run_cli(["--sheet", "s1-20a"], ["--list", "--regular-only"])
        self.assertEqual(status, 0)
        self.assertIn("LP-0001 | REGULAR_SPACING_FIELD", output)
        self.assertIn("Input member-line candidates: 3", output)

    def test_page_lookup_json(self):
        status, output = self.run_cli(["--page", "1"], ["--json", "--include-candidates"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["active_filters"], {"page": 1})
        self.assertEqual(payload["filtered_pattern_count"], 1)
        self.assertEqual(len(payload["patterns"][0]["source_candidates"]), 3)
        self.assertEqual(payload["orthogonal_network_count"], 0)
        self.assertIn("stage_timings", payload)

    def test_debug_stage_timings_are_present_and_nonnegative(self):
        status, output = self.run_cli(["--page", "1"], ["--debug"])
        self.assertEqual(status, 0)
        names = (
            "package_sheet_lookup_seconds", "page_geometry_extraction_seconds",
            "grid_extraction_seconds", "member_line_candidate_extraction_seconds",
            "spatial_partitioning_seconds", "primary_pattern_clustering_seconds",
            "double_line_pair_analysis_seconds", "orthogonal_network_construction_seconds",
            "svg_generation_seconds", "total_runtime_seconds",
        )
        for name in names:
            match = re.search(rf"{name}: ([0-9.]+)", output)
            self.assertIsNotNone(match, name)
            self.assertGreaterEqual(float(match.group(1)), 0.0)

    def test_selector_required(self):
        with self.assertRaises(SystemExit):
            cli.main(["linear-patterns", "drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
