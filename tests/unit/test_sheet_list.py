"""Synthetic tests for declared Sheet List extraction."""

import unittest
from dataclasses import dataclass

from shoplens.cli import _page_range
from shoplens.sheets.extract import extract_sheet_list, is_sheet_number


@dataclass
class Item:
    text: str
    page: int
    x: float
    y: float
    width: float = 60.0
    height: float = 10.0


def headers(page=1, shift=0.0, heading="SHEET LIST"):
    return [
        Item(heading, page, 100 + shift, 900, 100),
        Item("SHEET NUMBER", page, 100 + shift, 850, 100),
        Item("SHEET NAME", page, 400 + shift, 850, 100),
    ]


def row(number, name, y, page=1, shift=0.0, name_y=None):
    return [
        Item(number, page, 100 + shift, y, 80),
        Item(name, page, 400 + shift, y if name_y is None else name_y, 240),
    ]


class SheetNumberTests(unittest.TestCase):
    def test_supported_formats(self):
        for value in (
            "S0-00",
            "S0-01",
            "S1-20",
            "S1-20A",
            "S1-20B",
            "S2-10",
            "S3-15",
            "S5-00",
            "S-101",
            "S101",
            "S101A",
            "SK-01",
            "SSK-001",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_sheet_number(value))

    def test_invalid_row_text(self):
        for value in ("S0", "GRAND TOTAL", "92", "SECOND FLOOR", "1/8=1-0"):
            with self.subTest(value=value):
                self.assertFalse(is_sheet_number(value))

    def test_human_readable_page_range(self):
        self.assertEqual(_page_range("1-5"), [1, 2, 3, 4, 5])
        self.assertEqual(_page_range("3"), [3])


class SheetListExtractionTests(unittest.TestCase):
    def test_table_region_excludes_surrounding_text_and_next_page(self):
        items = [Item("RTU01", 1, 100, 950), Item("ROOF TOP UNIT", 1, 400, 950)]
        items += headers()
        items += row("S0-00", "GENERAL NOTES", 800)
        items += row("S1-20A", "FRAMING PLAN A", 786)
        items += [Item("Grand total: 2", 1, 100, 770, 100)]
        items += row("S5-00", "OUTSIDE TABLE", 750)
        items += [Item("RTU12", 2, 100, 800), Item("6,968", 2, 400, 800)]
        items += row("S1-20B", "ISOLATED SHEET REFERENCE", 786, page=2)
        result = extract_sheet_list(items, "drawing.pdf", [1, 2])
        self.assertEqual(result.sheet_list_pages, [1])
        self.assertEqual([entry.sheet_number for entry in result.entries], ["S0-00", "S1-20A"])
        self.assertFalse(any(entry.sheet_number.startswith("RTU") for entry in result.entries))
        self.assertFalse(any("ROWS_WITHOUT_VALID" in warning for warning in result.warnings))

    def test_standalone_s0_and_equipment_tag_rejected_inside_region(self):
        items = headers()
        items += row("S0-00", "GENERAL NOTES", 800)
        items += row("S0", "INCOMPLETE", 786)
        items += row("RTU01", "ROOF TOP UNIT 6,198", 772)
        items += [Item("Grand total: 1", 1, 100, 758, 100)]
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual([entry.sheet_number for entry in result.entries], ["S0-00"])
        self.assertEqual(result.declared_total, 1)
        self.assertIn("ROWS_WITHOUT_VALID_SHEET_NUMBER: 2", result.warnings)

    def test_grand_total_mismatch(self):
        items = headers() + row("S0-00", "GENERAL NOTES", 800)
        items += row("S1-20", "FRAMING PLAN", 786)
        items += [Item("Grand total: 92", 1, 100, 770, 100)]
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(result.declared_total, 92)
        self.assertTrue(any(warning.startswith("DECLARED_TOTAL_MISMATCH") for warning in result.warnings))

    def test_close_adjacent_rows_remain_separate(self):
        items = headers()
        items += [Item("S0-00", 1, 100, 800, 80, 6), Item("NOTES", 1, 400, 801, 100, 6)]
        items += [Item("S0-01", 1, 100, 792, 80, 6), Item("NOTES", 1, 400, 793, 100, 6)]
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual([entry.sheet_number for entry in result.entries], ["S0-00", "S0-01"])

    def test_split_word_fragments_join_without_invented_space(self):
        items = headers() + [
            Item("S5-00", 1, 100, 800, 80),
            Item("BASE PLATE SC", 1, 400, 800, 100),
            Item("HEDULE", 1, 520, 800, 60),
        ]
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(result.entries[0].sheet_name, "BASE PLATE SCHEDULE")

    def test_alternate_heading_and_column_headers(self):
        items = [
            Item("drawing list", 1, 100, 900, 100),
            Item("SHEET NO.", 1, 100, 850, 100),
            Item("DESCRIPTION", 1, 400, 850, 100),
        ]
        items += row("SK-01", "STRUCTURAL SKETCH", 800)
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual([entry.sheet_number for entry in result.entries], ["SK-01"])

    def test_sheet_index_named_declared_row_is_not_dropped_as_a_heading(self):
        items = headers(heading="SHEET INDEX")
        items += row("S005", "SHEET INDEX", 800)
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(
            [(entry.sheet_number, entry.sheet_name) for entry in result.entries],
            [("S005", "SHEET INDEX")],
        )

    def test_normal_shifted_table_headers_rows_and_y_tolerance(self):
        items = headers(shift=275) + row(
            "S1-20A", "SECOND FLOOR", 800, shift=275, name_y=798.5
        )
        items += [Item("FRAMING PLAN - SEGMENT A", 1, 700, 798.5, 220)]
        items += row("S2-00", "BRACED FRAME ELEVATIONS", 770, shift=275)
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(result.sheet_list_pages, [1])
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(
            result.entries[0].sheet_name,
            "SECOND FLOOR FRAMING PLAN - SEGMENT A",
        )
        self.assertEqual(result.entries[0].number_x, 375.0)

    def test_continuation_page_with_and_without_repeated_headers(self):
        first = headers(page=1)
        for index in range(5):
            first += row(f"S0-0{index}", "GENERAL NOTES", 800 - index * 13.5, page=1)
        continued = []
        for index, suffix in enumerate(("A", "B", "D", "H", "M")):
            continued += row(
                f"S1-20{suffix}",
                f"FRAMING PLAN {suffix}",
                800 - index * 13.5,
                page=2,
            )
        result = extract_sheet_list(first + continued, "drawing.pdf", [1, 2])
        self.assertEqual(result.sheet_list_pages, [1, 2])
        self.assertEqual([entry.source_page for entry in result.entries], [1] * 5 + [2] * 5)

        repeated = headers(page=2) + row("S1-20B", "FRAMING PLAN B", 800, page=2)
        repeated_result = extract_sheet_list(first + repeated, "drawing.pdf", [1, 2])
        self.assertEqual(len(repeated_result.entries), 6)

    def test_duplicate_text_objects_are_suppressed(self):
        base = headers() + row("S0-00", "GENERAL NOTES", 800)
        result = extract_sheet_list(base + list(base), "drawing.pdf", [1])
        self.assertEqual(len(result.entries), 1)
        self.assertTrue(
            any("EXACT_DUPLICATE_TEXT_ITEMS_SUPPRESSED" in warning for warning in result.warnings)
        )

    def test_duplicate_sheet_number_identical_and_conflicting_titles(self):
        identical = headers() + row("S1-20", "FRAMING PLAN", 800)
        identical += row("S1-20", "FRAMING PLAN", 770)
        result = extract_sheet_list(identical, "drawing.pdf", [1])
        self.assertEqual(result.duplicate_sheet_numbers, ["S1-20"])
        self.assertTrue(all("DUPLICATE_SHEET_NUMBER" in entry.warnings for entry in result.entries))

        conflicting = headers() + row("S1-20", "FRAMING PLAN A", 800)
        conflicting += row("S1-20", "FRAMING PLAN B", 770)
        conflict_result = extract_sheet_list(conflicting, "drawing.pdf", [1])
        self.assertTrue(
            all("CONFLICTING_SHEET_TITLES" in entry.warnings for entry in conflict_result.entries)
        )

    def test_missing_name_invalid_name_only_and_footer(self):
        items = headers() + [Item("S3-10", 1, 100, 800, 80)]
        items += [Item("NOT A SHEET ROW", 1, 400, 770, 200)]
        items += [Item("Grand total: 92", 1, 400, 740, 150)]
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(len(result.entries), 1)
        self.assertIn("MISSING_SHEET_NAME", result.entries[0].warnings)
        self.assertTrue(any("ROWS_WITHOUT_VALID_SHEET_NUMBER" in item for item in result.warnings))
        self.assertFalse(any(entry.sheet_number == "92" for entry in result.entries))

    def test_negative_coordinates_are_preserved(self):
        items = headers(shift=-200) + row("S-101", "FOUNDATION PLAN", 800, shift=-200)
        result = extract_sheet_list(items, "drawing.pdf", [1])
        self.assertEqual(result.entries[0].number_x, -100.0)

    def test_page_filtering_and_no_sheet_list(self):
        items = headers(page=3) + row("S0-00", "GENERAL NOTES", 800, page=3)
        excluded = extract_sheet_list(items, "drawing.pdf", [1, 2])
        self.assertEqual(excluded.entries, [])
        self.assertIn("NO_NATIVE_TEXT_SHEET_LIST_FOUND", excluded.warnings)
        included = extract_sheet_list(items, "drawing.pdf", [3])
        self.assertEqual(len(included.entries), 1)

    def test_json_serialization_and_debug_evidence(self):
        result = extract_sheet_list(
            headers() + row("SSK-001", "SKETCH", 800), "drawing.pdf", [1]
        )
        payload = result.to_dict(include_debug=True)
        self.assertEqual(payload["entries"][0]["sheet_number"], "SSK-001")
        self.assertEqual(payload["entries"][0]["number_x"], 100.0)
        self.assertIn("column_boundary_x", payload["debug"][0])

    def test_heading_without_headers_returns_warning(self):
        result = extract_sheet_list(
            [Item("INDEX OF DRAWINGS", 1, 100, 900)], "drawing.pdf", [1]
        )
        self.assertIn("COLUMN_HEADERS_NOT_FOUND_PAGE_1", result.warnings)


if __name__ == "__main__":
    unittest.main()
