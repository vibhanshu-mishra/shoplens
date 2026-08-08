"""Synthetic title-block extraction and reconciliation tests."""

import unittest
from dataclasses import dataclass, replace

from shoplens.sheets.models import SheetEntry, SheetListResult
from shoplens.title_blocks import extract_title_blocks, reconcile_sheets
from shoplens.title_blocks.models import (
    DeclaredIndexStatus,
    ReconciliationStatus,
    SheetRecordSource,
    TitleBlockPage,
    TitleBlockResult,
)
from shoplens.title_blocks.reconcile import compare_titles


@dataclass
class Item:
    text: str
    page: int
    x: float
    y: float
    width: float = 80.0
    height: float = 10.0
    font: str = "F1"
    font_size: float = 10.0


def declared(number, title):
    return SheetEntry(
        sheet_number=number,
        sheet_name=title,
        source_page=1,
        number_original_text=number,
        name_original_text=title,
        number_x=10,
        number_y=10,
        number_width=40,
        number_height=10,
        name_x=100,
        name_y=10,
        name_width=200,
        name_height=10,
        confidence=1.0,
        name_comparison_text=title,
    )


def standard_block(page, number, title_lines, x=900, y=20):
    items = [
        Item("SHEET", page, x, y + 60, 50, 19, font_size=19),
        Item(number, page, x + 60, y, 100, 30, font_size=30),
    ]
    for index, title in enumerate(title_lines):
        items.append(Item(title, page, x, y + 200 - index * 30, 180, 20, font_size=20))
    return items


def rotated_block(page, number, title_lines):
    items = [
        Item("SHEET", page, 160, 900, 0, 19, font_size=19),
        Item(number, page, 220, 960, 0, 30, font_size=30),
    ]
    for index, title in enumerate(title_lines):
        items.append(Item(title, page, 20 + index * 30, 900, 0, 20, font_size=20))
    return items


class TitleBlockExtractionTests(unittest.TestCase):
    def test_cross_firm_labels_long_numbers_and_fragment_evidence(self):
        items = [
            Item("DWG NO.", 1, 900, 80, 70, 19, font_size=19),
            Item("S-101", 1, 960, 20, 100, 30, font_size=30),
            Item("FOUNDATION PLAN", 1, 900, 220, 180, 20, font_size=20),
            Item("DOCUMENT NO.", 2, 900, 80, 100, 19, font_size=19),
            Item("TX22-AGE-ZZ-ZZ-DR-S-0001", 2, 960, 20, 260, 30, font_size=30),
            Item("ROOF FRAMING PLAN", 2, 900, 220, 190, 20, font_size=20),
            Item("DRAWING NO.", 3, 900, 80, 90, 19, font_size=19),
            Item("SSK-", 3, 960, 20, 48, 30, font_size=30),
            Item("001", 3, 1010, 20, 42, 30, font_size=30),
            Item("STRUCTURAL SKETCH", 3, 900, 220, 190, 20, font_size=20),
        ]
        result = extract_title_blocks(items, "drawing.pdf", [1, 2, 3])
        self.assertEqual([page.sheet_number for page in result.pages], [
            "S-101", "TX22-AGE-ZZ-ZZ-DR-S-0001", "SSK-001",
        ])
        self.assertEqual(result.pages[2].number_source_fragments, ["SSK-", "001"])

    def test_recurring_layout_without_literal_sheet_label(self):
        items = []
        for page, number, title in (
            (1, "S001", "GENERAL NOTES"),
            (2, "S002", "FOUNDATION PLAN"),
            (3, "S003", "ROOF FRAMING PLAN"),
        ):
            items.append(Item(number, page, 900, 20, 100, 30, font_size=30))
            items.append(Item(title, page, 900, 220, 180, 20, font_size=20))
        result = extract_title_blocks(items, "drawing.pdf", [1, 2, 3])
        self.assertEqual(result.identified_page_count, 3)
        self.assertTrue(all(
            "RECURRING_LAYOUT_WITHOUT_LITERAL_LABEL" in page.evidence
            for page in result.pages
        ))

    def test_explicit_title_field_and_rotated_coded_sheet_number(self):
        explicit = [
            Item("Title", 1, 1000, 220, 30, 12, font_size=12),
            Item("MOMENT FRAME", 1, 820, 190, 180, 19, font_size=19),
            Item("Sheet", 1, 1000, 125, 40, 12, font_size=12),
            Item("BS21-02", 1, 850, 95, 150, 37, font_size=37),
        ]
        rotated = [
            Item("Sheet Number", 2, 2020, 2710, 0, 13, font_size=13),
            Item("S01-30F-P1", 2, 2043, 2895, 0, 16, font_size=16),
            Item("ROOF FRAMING PLAN", 2, 1968, 2735, 0, 13, font_size=13),
        ]
        result = extract_title_blocks(explicit + rotated, "drawing.pdf", [1, 2])
        self.assertEqual(result.pages[0].sheet_title, "MOMENT FRAME")
        self.assertEqual(result.pages[1].sheet_number, "S01-30F-P1")
        self.assertEqual(result.pages[1].sheet_title, "ROOF FRAMING PLAN")

    def test_compact_suffixes_underscores_and_complete_fragment_preference(self):
        cases = (
            "S11-F1",
            "S11-OPL1",
            "S11-T1",
            "BS11-00_FR",
            "BS11-01_RA",
            "S01-10-P1",
        )
        items = []
        for page, number in enumerate(cases, start=1):
            items += standard_block(page, number, ["FOUNDATION PLAN"])
        result = extract_title_blocks(items, "drawing.pdf", range(1, len(cases) + 1))
        self.assertEqual([page.sheet_number for page in result.pages], list(cases))

        fragments = [
            Item("Sheet", 7, 960, 100, 40, 12, font_size=12),
            Item("BS42", 7, 900, 120, 92, 37, font_size=37),
            Item("-", 7, 900, 50, 12, 37, font_size=37),
            Item("01", 7, 900, 10, 41, 37, font_size=37),
            Item("ELEVATOR PLANS AND SECTIONS", 7, 900, 220, 220, 20, font_size=20),
        ]
        fragment_result = extract_title_blocks(fragments, "drawing.pdf", [7])
        page = fragment_result.pages[0]
        self.assertEqual(page.sheet_number, "BS42-01")
        self.assertEqual(page.number_source_fragments, ["BS42", "-", "01"])
        self.assertIn("COMPLETE_SHEET_NUMBER_OVER_PREFIX", page.evidence)

    def test_complete_coded_number_and_multiline_title_outrank_placeholders(self):
        items = [
            Item("Sheet", 1, 900, 80, 50, 19, font_size=19),
            Item("S01-10-P1", 1, 960, 20, 120, 30, font_size=30),
            Item("Title", 1, 900, 220, 30, 12, font_size=12),
            Item("-", 1, 900, 195, 12, 20, font_size=20),
            Item("OVERALL FOUNDATION", 1, 820, 190, 180, 20, font_size=20),
            Item("PLAN", 1, 820, 160, 60, 20, font_size=20),
        ]
        result = extract_title_blocks(items, "drawing.pdf", [1])
        page = result.pages[0]
        self.assertEqual(page.sheet_number, "S01-10-P1")
        self.assertEqual(page.sheet_title, "OVERALL FOUNDATION PLAN")
        self.assertNotIn("-", page.title_source_fragments)

    def test_declared_residual_reconstruction_requires_one_textless_page(self):
        declarations = [
            declared("S01-10-P1", "OVERALL FOUNDATION PLAN"),
            declared("S01-10A-P1", "FOUNDATION PLAN - SEGMENT A"),
            declared("S01-10B-P1", "FOUNDATION PLAN - SEGMENT B"),
        ]
        items = standard_block(1, "S01-10A-P1", ["FOUNDATION PLAN - SEGMENT A"])
        items += standard_block(3, "S01-10B-P1", ["FOUNDATION PLAN - SEGMENT B"])
        result = extract_title_blocks(
            items,
            "drawing.pdf",
            [1, 2, 3],
            declarations,
            declared_total=3,
        )
        page = result.pages[1]
        self.assertEqual(page.sheet_number, "S01-10-P1")
        self.assertEqual(page.sheet_title, "OVERALL FOUNDATION PLAN")
        self.assertEqual(page.identity_source, "DECLARED_SHEET_LIST")
        self.assertEqual(page.title_block_status, "RECONSTRUCTED")
        self.assertIsNone(page.number_x)
        self.assertIn("SINGLE_RESIDUAL_DECLARED_IDENTITY", page.evidence)

    def test_declared_sheet_index_is_known_without_a_title_block(self):
        index = replace(declared("S005", "SHEET INDEX"), source_page=2)
        result = extract_title_blocks(
            standard_block(1, "S001", ["GENERAL NOTES"]),
            "drawing.pdf",
            [1, 2, 3],
            [index],
            declared_total=1,
            sheet_list_pages=[2],
        )
        page = result.pages[1]
        self.assertEqual(page.sheet_number, "S005")
        self.assertEqual(page.sheet_title, "SHEET INDEX")
        self.assertEqual(page.identity_source, "DECLARED_SHEET_LIST")
        self.assertEqual(page.title_block_status, "NOT_PRESENT")
        self.assertEqual(page.page_role, "SHEET_INDEX")
        self.assertIsNone(page.number_x)
        self.assertEqual(result.identified_page_count, 1)
        self.assertEqual(result.intentional_non_title_block_pages, [2])
        self.assertEqual(result.unidentified_pages, [3])
        self.assertEqual(
            result.total_pdf_pages_processed,
            result.identified_page_count
            + len(result.intentional_non_title_block_pages)
            + len(result.unidentified_pages),
        )

    def test_non_textless_or_non_exhaustive_residual_stays_unidentified(self):
        result = extract_title_blocks(
            [Item("unrelated", 2, 100, 100)],
            "drawing.pdf",
            [1, 2],
            [declared("S01-10-P1", "OVERALL FOUNDATION PLAN")],
            declared_total=2,
        )
        self.assertEqual(result.unidentified_pages, [1, 2])

    def test_repeated_layout_fragmented_titles_and_declared_matches(self):
        declarations = [declared("S0-00", "GENERAL NOTES"), declared("S1-20A", "FRAMING PLAN-A"), declared("S5-00", "STEEL DETAILS")]
        items = standard_block(1, "S0-00", ["GENERAL NOTES"])
        items += standard_block(2, "S1-20A", ["FRAMING", "PLAN-A"])
        items += standard_block(3, "S5-00", ["STEEL DETAILS"])
        result = extract_title_blocks(items, "drawing.pdf", [1, 2, 3], declarations)
        self.assertEqual(result.identified_page_count, 3)
        self.assertEqual(len(result.layouts_discovered), 1)
        self.assertEqual(result.pages[1].sheet_title, "FRAMING PLAN-A")
        self.assertTrue(all(page.confidence >= 0.9 for page in result.pages))

    def test_two_layouts_and_negative_coordinates(self):
        declarations = [declared("S0-00", "NOTES"), declared("S2-00", "BRACED FRAME ELEVATIONS")]
        items = standard_block(1, "S0-00", ["NOTES"], x=-300, y=-900)
        items += standard_block(2, "S0-00", ["NOTES"], x=-300, y=-900)
        items += rotated_block(3, "S2-00", ["BRACED FRAME", "ELEVATIONS"])
        items += rotated_block(4, "S2-00", ["BRACED FRAME", "ELEVATIONS"])
        result = extract_title_blocks(items, "drawing.pdf", [1, 2, 3, 4], declarations)
        self.assertEqual(len(result.layouts_discovered), 2)
        self.assertEqual(result.pages[0].number_x, -240.0)
        self.assertEqual(result.pages[2].number_width, 0.0)

    def test_references_and_sheet_list_rows_do_not_beat_title_block(self):
        declarations = [declared("S0-00", "GENERAL NOTES"), declared("S3-00", "DETAILS")]
        items = [
            Item("S0-00", 1, 100, 800, 30, 9, font_size=9),
            Item("S3-00", 1, 300, 500, 40, 10, font_size=10),
            Item("REFER TO SHEET S3-00", 1, 200, 500, 200, 10, font_size=10),
        ]
        items += standard_block(1, "S0-00", ["GENERAL NOTES"])
        items += standard_block(2, "S3-00", ["DETAILS"])
        result = extract_title_blocks(items, "drawing.pdf", [1, 2], declarations)
        self.assertEqual(result.pages[0].sheet_number, "S0-00")
        self.assertGreater(result.pages[0].candidate_count, 1)

    def test_layout_context_overrides_reference_frequency_and_excludes_numeric_metadata(self):
        declarations = [declared(f"S2-0{index}", "BRACED FRAME ELEVATIONS") for index in range(1, 5)]
        items = []
        for page, number in enumerate(("S2-01", "S2-02", "S2-04", "S2-01"), start=1):
            items += standard_block(page, number, ["BRACED FRAME ELEVATIONS"])
            items.append(Item("S2-03", page, 100, 500, 30, 9, font_size=9))
        items += standard_block(5, "S2-03", ["BRACED FRAME ELEVATIONS", "25136.0000"])
        result = extract_title_blocks(items, "drawing.pdf", range(1, 6), declarations)
        self.assertEqual(result.identified_page_count, 5)
        self.assertTrue(all(page.sheet_title == "BRACED FRAME ELEVATIONS" for page in result.pages))
        self.assertIn("TITLE_BLOCK_CONTEXT_OVERRIDES_PAGE_FREQUENCY", result.pages[4].evidence)

    def test_missing_title_missing_number_and_ambiguity(self):
        declarations = [declared("S0-00", "NOTES"), declared("S0-01", "NOTES")]
        missing_title = standard_block(1, "S0-00", [])
        no_number = [Item("SHEET", 2, 900, 80, 50, 19, font_size=19)]
        ambiguous = [Item("SHEET", 3, 900, 80, 50, 19, font_size=19)]
        ambiguous += [
            Item("S0-00", 3, 950, 20, 90, 30, font_size=30),
            Item("S0-01", 3, 960, 25, 90, 30, font_size=30),
        ]
        result = extract_title_blocks(missing_title + no_number + ambiguous, "drawing.pdf", [1, 2, 3], declarations)
        self.assertIn("MISSING_SHEET_TITLE", result.pages[0].warnings)
        self.assertIsNone(result.pages[1].sheet_number)
        self.assertIn("AMBIGUOUS_SHEET_NUMBER", result.pages[2].warnings)

    def test_duplicate_objects_page_filter_revision_and_json(self):
        declarations = [declared("S0-00", "NOTES")]
        block = standard_block(2, "S0-00", ["NOTES"])
        block += [Item("REV", 2, 900, 0, 30, 10), Item("A", 2, 930, 0, 10, 10)]
        result = extract_title_blocks(block + list(block), "drawing.pdf", [2], declarations)
        self.assertEqual([page.pdf_page for page in result.pages], [2])
        self.assertEqual(result.pages[0].revision, "A")
        self.assertIn("debug", result.to_dict(include_debug=True))
        self.assertNotIn("debug", result.to_dict())


def title_page(page, number, title, confidence=0.95):
    return TitleBlockPage(
        pdf_page=page,
        sheet_number=number,
        sheet_title=title,
        revision=None,
        confidence=confidence,
        layout_id="layout-1" if number else None,
        number_original_text=number,
        title_original_text=title,
        number_x=1.0 if number else None,
        number_y=2.0 if number else None,
        number_width=3.0 if number else None,
        number_height=4.0 if number else None,
        title_x=5.0 if title else None,
        title_y=6.0 if title else None,
        title_width=7.0 if title else None,
        title_height=8.0 if title else None,
        evidence=[],
        candidate_count=1 if number else 0,
        warnings=[] if number else ["UNIDENTIFIED_PAGE"],
    )


class ReconciliationTests(unittest.TestCase):
    def test_title_comparison(self):
        self.assertEqual(compare_titles("PLAN - SEGMENT A", "PLAN-SEGMENT A")[1], ReconciliationStatus.MATCH)
        self.assertEqual(compare_titles("TYP DETAILS", "TYPICAL DETAILS")[1], ReconciliationStatus.TITLE_VARIATION)
        self.assertEqual(compare_titles("FOUNDATION PLAN", "ROOF PLAN")[1], ReconciliationStatus.TITLE_MISMATCH)

    def test_all_reconciliation_status_paths(self):
        declared_result = SheetListResult(
            source_file="drawing.pdf",
            pages_scanned=[1],
            sheet_list_pages=[1],
            entries=[
                declared("S0-00", "GENERAL NOTES"),
                declared("S1-00", "TYP DETAILS"),
                declared("S2-00", "FOUNDATION PLAN"),
                declared("S3-00", "MISSING SHEET"),
            ],
            duplicate_sheet_numbers=[],
            warnings=[],
        )
        pages = [
            title_page(1, "S0-00", "GENERAL NOTES"),
            title_page(2, "S1-00", "TYPICAL DETAILS"),
            title_page(3, "S2-00", "ROOF PLAN"),
            title_page(4, "S0-00", "GENERAL NOTES"),
            title_page(5, "SSK-001", "SKETCH"),
            title_page(6, None, None),
        ]
        actual_result = TitleBlockResult(
            source_file="drawing.pdf",
            total_pdf_pages_processed=6,
            identified_page_count=5,
            unidentified_pages=[6],
            low_confidence_pages=[],
            layouts_discovered=[],
            duplicate_sheet_numbers={"S0-00": [1, 4]},
            pages=pages,
            warnings=[],
        )
        result = reconcile_sheets(declared_result, actual_result)
        statuses = {entry.status for entry in result.entries}
        self.assertIn(ReconciliationStatus.DUPLICATE_SHEET_NUMBER, statuses)
        self.assertIn(ReconciliationStatus.TITLE_VARIATION, statuses)
        self.assertIn(ReconciliationStatus.TITLE_MISMATCH, statuses)
        self.assertIn(ReconciliationStatus.DECLARED_BUT_MISSING, statuses)
        self.assertIn(ReconciliationStatus.PRESENT_BUT_UNDECLARED, statuses)
        self.assertIn(ReconciliationStatus.UNIDENTIFIED_PAGE, statuses)
        self.assertEqual(result.missing_declared_sheets, ["S3-00"])
        self.assertEqual(result.undeclared_actual_sheets, ["SSK-001"])
        self.assertEqual(result.title_mismatches, ["S2-00"])
        self.assertEqual(result.to_dict()["entries"][0]["status"], "DUPLICATE_SHEET_NUMBER")

    def test_title_block_only_index_when_declared_list_is_absent(self):
        declared_result = SheetListResult(
            source_file="drawing.pdf", pages_scanned=[1], sheet_list_pages=[],
            entries=[], duplicate_sheet_numbers=[], warnings=["NO_NATIVE_TEXT_SHEET_LIST_FOUND"],
        )
        actual_result = TitleBlockResult(
            source_file="drawing.pdf", total_pdf_pages_processed=2,
            identified_page_count=2, unidentified_pages=[], low_confidence_pages=[],
            layouts_discovered=[], duplicate_sheet_numbers={},
            pages=[
                title_page(1, "S-101", "FOUNDATION PLAN"),
                title_page(2, "S-102", "ROOF FRAMING PLAN"),
            ], warnings=[],
        )
        result = reconcile_sheets(declared_result, actual_result)
        self.assertEqual(result.declared_index_status, DeclaredIndexStatus.NO_DECLARED_SHEET_LIST)
        self.assertEqual(result.undeclared_actual_sheets, [])
        self.assertTrue(all(
            entry.status == ReconciliationStatus.TITLE_BLOCK_ONLY_INDEX
            and entry.record_source == SheetRecordSource.TITLE_BLOCK_ONLY
            for entry in result.entries
        ))
        self.assertIn("TITLE_BLOCK_ONLY_INDEX", result.warnings)

    def test_declared_total_mismatch_marks_partial_index(self):
        declared_result = SheetListResult(
            source_file="drawing.pdf", pages_scanned=[1], sheet_list_pages=[1],
            entries=[declared("S-101", "FOUNDATION PLAN")],
            duplicate_sheet_numbers=[], warnings=[], declared_total=2,
        )
        actual_result = TitleBlockResult(
            source_file="drawing.pdf", total_pdf_pages_processed=2,
            identified_page_count=2, unidentified_pages=[], low_confidence_pages=[],
            layouts_discovered=[], duplicate_sheet_numbers={},
            pages=[
                title_page(1, "S-101", "FOUNDATION PLAN"),
                title_page(2, "S-102", "ROOF FRAMING PLAN"),
            ], warnings=[],
        )
        result = reconcile_sheets(declared_result, actual_result)
        self.assertEqual(
            result.declared_index_status,
            DeclaredIndexStatus.PARTIAL_DECLARED_SHEET_LIST,
        )
        added = next(entry for entry in result.entries if entry.actual_sheet_number == "S-102")
        self.assertEqual(added.record_source, SheetRecordSource.TITLE_BLOCK_ONLY)


if __name__ == "__main__":
    unittest.main()
