"""Synthetic tests for classified structural section-label inventories."""

import csv
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from shoplens import cli
from shoplens.classification import SheetKind, StructuralSubject, build_package_index
from shoplens.inventory import (
    InventoryFilters,
    build_section_inventory,
    export_inventory_csv,
    filter_inventory_sheets,
    matching_detections,
)
from shoplens.models import SectionFamily, SteelLabel
from shoplens.title_blocks.models import (
    ReconciliationEntry,
    ReconciliationResult,
    ReconciliationStatus,
)


def label(page, value="W18X35", family=SectionFamily.W, x=10.0, y=20.0):
    return SteelLabel(page, value, value, family, x, y, 50.0, 9.0, 1.0)


def reconciled(number, title, page):
    return ReconciliationEntry(
        declared_sheet_number=number,
        declared_sheet_title=title,
        actual_pdf_pages=[page],
        actual_sheet_number=number,
        actual_sheet_title=title,
        revision=None,
        status=ReconciliationStatus.MATCH,
        title_similarity=1.0,
        confidence=1.0,
        warnings=[],
    )


def package_index():
    entries = [
        reconciled("S1-20A", "SECOND FLOOR FRAMING PLAN - SEGMENT A", 2),
        reconciled("S1-30E", "MECHANICAL PLATFORM FRAMING PLAN - SEGMENT E", 3),
        reconciled("S5-30", "STAIR FRAMING DETAILS", 4),
    ]
    result = ReconciliationResult(
        source_file="drawing.pdf",
        declared_sheet_count=3,
        total_pdf_pages_processed=3,
        identified_page_count=3,
        unidentified_pages=[],
        missing_declared_sheets=[],
        undeclared_actual_sheets=[],
        duplicate_actual_sheet_numbers={},
        title_mismatches=[],
        entries=entries,
        warnings=[],
    )
    return build_package_index(result)


class InventoryBuildTests(unittest.TestCase):
    def test_page_join_zero_sheet_and_pdf_order(self):
        inventory = build_section_inventory(package_index(), [label(2), label(3, "HSS6X6X1/2", SectionFamily.HSS)])
        self.assertEqual([sheet.pdf_page for sheet in inventory.sheets], [2, 3, 4])
        self.assertEqual(inventory.total_indexed_sheets, 3)
        self.assertEqual(inventory.sheets_with_detections, 2)
        self.assertEqual(inventory.sheets_without_detections, 1)
        self.assertEqual(inventory.sheets[0].sheet_number, "S1-20A")
        self.assertEqual(inventory.sheets[0].detections[0].sheet_number, "S1-20A")
        self.assertEqual(inventory.sheets[2].detections, [])

    def test_raw_deduplicated_counts_and_distinct_positions(self):
        records = [label(2), label(2), label(2, x=100.0)]
        deduplicated = build_section_inventory(package_index(), records)
        self.assertEqual((deduplicated.raw_detection_count, deduplicated.deduplicated_detection_count), (3, 2))
        self.assertEqual(len(deduplicated.sheets[0].detections), 2)
        self.assertEqual(deduplicated.sheets[0].detections[0].duplicate_count, 2)
        raw = build_section_inventory(package_index(), records, raw=True)
        self.assertEqual(len(raw.sheets[0].detections), 3)
        self.assertTrue(all(item.record_mode == "raw" for item in raw.sheets[0].detections))

    def test_section_family_and_dimension_counts_include_sheet_counts(self):
        records = [label(2), label(2, x=100), label(3), label(3, "HSS6X6X1/2", SectionFamily.HSS)]
        inventory = build_section_inventory(package_index(), records)
        self.assertEqual(inventory.counts_by_section["W18X35"].detection_count, 3)
        self.assertEqual(inventory.counts_by_section["W18X35"].sheet_count, 2)
        self.assertEqual(inventory.counts_by_family["W"].detection_count, 3)
        self.assertEqual(inventory.counts_by_subject["FLOOR_FRAMING"].detection_count, 2)
        self.assertEqual(inventory.counts_by_level["SECOND FLOOR"].sheet_count, 1)
        self.assertEqual(inventory.counts_by_segment["A"].detection_count, 2)
        self.assertEqual(inventory.counts_by_area["MECHANICAL PLATFORM"].detection_count, 2)

    def test_unmatched_and_duplicate_indexed_pages_are_preserved(self):
        base = package_index()
        duplicate_sheet = replace(base.sheets[1], pdf_page=2, actual_pdf_pages=[2])
        duplicated = replace(base, sheets=[base.sheets[0], duplicate_sheet, base.sheets[2]])
        inventory = build_section_inventory(duplicated, [label(2), label(99)])
        self.assertIn("DUPLICATE_INDEXED_PDF_PAGE: 2", inventory.warnings)
        self.assertIn("UNMATCHED_DETECTION_PAGE: 99", inventory.warnings)
        self.assertEqual(len(inventory.unmatched_detections), 2)
        self.assertEqual({item.pdf_page for item in inventory.unmatched_detections}, {2, 99})

    def test_json_preserves_coordinates_and_classification(self):
        inventory = build_section_inventory(package_index(), [label(2, x=-12.5, y=-8.0)])
        payload = json.loads(json.dumps(inventory.to_dict()))
        detection = payload["sheets"][0]["detections"][0]
        self.assertEqual((detection["raw_x"], detection["raw_y"]), (-12.5, -8.0))
        self.assertEqual(detection["sheet_subject"], "FLOOR_FRAMING")
        self.assertEqual(payload["record_mode"], "deduplicated")


class InventoryFilterAndCsvTests(unittest.TestCase):
    def setUp(self):
        records = [
            label(2),
            label(2, "W24X55", x=100),
            label(3, "HSS6X6X1/2", SectionFamily.HSS),
        ]
        self.inventory = build_section_inventory(package_index(), records)

    def test_all_filters_combined_and_zero_result(self):
        selected = filter_inventory_sheets(
            self.inventory.sheets,
            InventoryFilters(
                sheet_number="s1-20a",
                page=2,
                kind=SheetKind.PLAN,
                subject=StructuralSubject.FLOOR_FRAMING,
                level="second floor",
                segment="a",
                family=SectionFamily.W,
                section="w18x35",
                with_detections=True,
            ),
        )
        self.assertEqual([sheet.sheet_number for sheet in selected], ["S1-20A"])
        self.assertEqual(
            filter_inventory_sheets(self.inventory.sheets, InventoryFilters(section="W99X99")),
            [],
        )
        self.assertEqual(
            [
                sheet.sheet_number
                for sheet in filter_inventory_sheets(
                    self.inventory.sheets, InventoryFilters(without_detections=True)
                )
            ],
            ["S5-30"],
        )
        self.assertEqual([item.normalized_section for item in matching_detections(selected[0], section="W18X35")], ["W18X35"])

    def test_area_and_family_filters(self):
        selected = filter_inventory_sheets(
            self.inventory.sheets,
            InventoryFilters(area="mechanical platform", family=SectionFamily.HSS),
        )
        self.assertEqual([sheet.sheet_number for sheet in selected], ["S1-30E"])

    def test_csv_rows_and_header_only_export(self):
        with tempfile.TemporaryDirectory() as directory:
            populated = Path(directory) / "populated.csv"
            empty = Path(directory) / "empty.csv"
            detections = [item for sheet in self.inventory.sheets for item in sheet.detections]
            self.assertEqual(export_inventory_csv(populated, detections), 3)
            self.assertEqual(export_inventory_csv(empty, []), 0)
            with populated.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["x"], "10.0")
            with empty.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.reader(handle))), 1)


class InventoryCliTests(unittest.TestCase):
    def run_cli(self, options, output_path=None):
        index = package_index()
        records = [label(2), label(3, "HSS6X6X1/2", SectionFamily.HSS)]
        arguments = ["section-inventory", "drawing.pdf"] + options
        output = io.StringIO()
        with patch.object(
            cli,
            "_extract_package_title_blocks_with_items",
            return_value=(object(), object(), [object()], 0),
        ):
            with patch.object(cli, "reconcile_sheets", return_value=object()):
                with patch.object(cli, "build_package_index", return_value=index):
                    with patch.object(cli, "analyze_positioned_text", return_value=(records, [], [])):
                        with redirect_stdout(output):
                            status = cli.main(arguments)
        return status, output.getvalue()

    def test_filtered_json_contains_active_filters_and_coordinates(self):
        status, output = self.run_cli(["--family", "W", "--json"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["active_filters"], {"family": "W"})
        self.assertEqual(payload["filtered_sheet_count"], 1)
        self.assertEqual(payload["sheets"][0]["detections"][0]["raw_x"], 10.0)

    def test_cli_csv_zero_filter_writes_header_and_reports_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.csv"
            status, output = self.run_cli(
                ["--section", "W99X99", "--csv", str(path)]
            )
            self.assertEqual(status, 0)
            self.assertIn("0 detection rows written", output)
            with path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.reader(handle))), 1)

    def test_list_and_debug_explain_join_and_counts(self):
        status, output = self.run_cli(["--sheet", "S1-20A", "--list", "--debug"])
        self.assertEqual(status, 0)
        self.assertIn("PDF 2 | S1-20A | FLOOR_FRAMING", output)
        self.assertIn("Package-index records used: 1", output)
        self.assertIn("Detection extraction pages used: 2", output)
        self.assertIn("S1-20A | raw=1 | deduplicated=1", output)

    def test_filtered_text_labels_package_wide_counts(self):
        status, output = self.run_cli(["--family", "W", "--list"])
        self.assertEqual(status, 0)
        self.assertIn("Whole-package summary:", output)
        self.assertIn("Matching sheets: 1", output)

    def test_raw_mode_is_not_reported_as_a_filter(self):
        status, output = self.run_cli(["--raw", "--json"])
        payload = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(payload["record_mode"], "raw")
        self.assertEqual(payload["active_filters"], {})


if __name__ == "__main__":
    unittest.main()
