"""Unit tests for steel-label normalization and detection."""

import unittest
from dataclasses import dataclass

from shoplens.models import SectionFamily
from shoplens.steel.detect import detect_steel_labels
from shoplens.steel.normalize import normalize_steel_label


@dataclass
class Item:
    text: str
    page: int = 3
    x: float = 10.0
    y: float = 20.0
    width: float = 100.0
    height: float = 12.0


class NormalizationTests(unittest.TestCase):
    def test_supported_variations(self):
        cases = {
            "W18X35": ("W18X35", SectionFamily.W),
            "W18 x 35": ("W18X35", SectionFamily.W),
            "W18×35": ("W18X35", SectionFamily.W),
            "w 18 X 35": ("W18X35", SectionFamily.W),
            "HSS 8 x 8 x 3/8": ("HSS8X8X3/8", SectionFamily.HSS),
            "C12X20.7": ("C12X20.7", SectionFamily.C),
            "L4X4X3/8": ("L4X4X3/8", SectionFamily.L),
            "2L 4 x 4 x 3/8": ("2L4X4X3/8", SectionFamily.DOUBLE_ANGLE),
            "PL 3/8 x 8": ("PL3/8X8", SectionFamily.PL),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_steel_label(source), expected)

    def test_unrelated_text_is_unchanged(self):
        for value in ("Scale: 1/8 = 1'-0", "Sheet S2.01", "March 18, 2026", "Grid W"):
            with self.subTest(value=value):
                self.assertEqual(normalize_steel_label(value), (value, SectionFamily.UNKNOWN))


class DetectionTests(unittest.TestCase):
    def test_surrounding_text_and_multiple_labels(self):
        results = detect_steel_labels([Item("BEAMS: W18X35 and C12 x 20.7 TYP")])
        self.assertEqual([result.normalized_text for result in results], ["W18X35", "C12X20.7"])

    def test_preserves_page_and_coordinates(self):
        result = detect_steel_labels([Item("W18X35", page=7, x=11, y=22, width=70, height=9)])[0]
        self.assertEqual(result.page_number, 7)
        self.assertEqual((result.x, result.y, result.width, result.height), (11.0, 22.0, 70.0, 9.0))
        self.assertEqual(result.confidence, 1.0)

    def test_rejects_false_positives(self):
        text = "Scale 1/8=1-0; sheet S2.01; 2026-08-01; grids W L C; dimension 18 x 35"
        self.assertEqual(detect_steel_labels([Item(text)]), [])

    def test_does_not_match_inside_identifier(self):
        self.assertEqual(detect_steel_labels([Item("AW18X35B")]), [])


if __name__ == "__main__":
    unittest.main()
