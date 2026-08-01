"""Tests for extraction diagnostics, filtering, summaries, and doctor checks."""

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from shoplens.doctor import run_doctor
from shoplens.models import SectionFamily, SteelLabel
from shoplens.reporting import (
    build_summary,
    deduplicate_detections,
    filter_detections,
    filter_diagnostics,
)
from shoplens.steel.detect import WELDED_WIRE_REINFORCEMENT, analyze_positioned_text


@dataclass
class Item:
    text: str
    page: int = 1
    x: float = 10.0
    y: float = 20.0
    width: float = 70.0
    height: float = 9.0
    font: str = "Arial"
    font_size: float = 9.0


def label(x=10.0, y=20.0, width=70.0, height=9.0, page=1, value="W18X35"):
    return SteelLabel(
        page_number=page,
        original_text=value,
        normalized_text=value,
        section_family=SectionFamily.W,
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=1.0,
    )


class DeduplicationTests(unittest.TestCase):
    def test_exact_duplicate_suppression_and_raw_preservation(self):
        raw = [label(), label()]
        displayed, duplicate_count = deduplicate_detections(raw)
        self.assertEqual(len(raw), 2)
        self.assertEqual(len(displayed), 1)
        self.assertEqual(displayed[0].duplicate_count, 2)
        self.assertEqual(duplicate_count, 1)

    def test_near_duplicate_within_tolerance(self):
        displayed, duplicate_count = deduplicate_detections(
            [label(), label(x=10.2, y=19.8, width=70.1, height=9.1)]
        )
        self.assertEqual((len(displayed), duplicate_count), (1, 1))

    def test_same_section_at_different_coordinates_remains(self):
        displayed, duplicate_count = deduplicate_detections([label(x=1193), label(x=1553)])
        self.assertEqual((len(displayed), duplicate_count), (2, 0))


class DiagnosticsTests(unittest.TestCase):
    def test_welded_wire_rejection_reason_and_valid_w_shapes(self):
        raw, rejected, diagnostics = analyze_positioned_text(
            [
                Item("W2.9XW2.9"),
                Item("W2.9 X W2.9"),
                Item("W2.9X2.9"),
                Item("6X6-W2.9XW2.9"),
                Item("6 X 6 - W2.9 X W2.9"),
                Item("W6X8.5", page=2),
                Item("W18X35", page=3),
                Item("W44X335", page=4),
            ]
        )
        self.assertEqual(
            [item.normalized_text for item in raw],
            ["W6X8.5", "W18X35", "W44X335"],
        )
        self.assertEqual(len(rejected), 5)
        self.assertTrue(all(item.reason == WELDED_WIRE_REINFORCEMENT for item in rejected))
        self.assertEqual(
            diagnostics[0].rejection_reasons,
            [WELDED_WIRE_REINFORCEMENT],
        )
        self.assertTrue(diagnostics[0].is_candidate)
        self.assertFalse(diagnostics[0].section_detected)

    def test_ordinary_decimal_dimensions_are_not_wwr(self):
        raw, rejected, diagnostics = analyze_positioned_text([Item("PLATE IS 2.9 X 2.9")])
        self.assertEqual(raw, [])
        self.assertEqual(rejected, [])
        self.assertFalse(diagnostics[0].is_candidate)

    def test_negative_coordinates_and_serialization(self):
        _, _, diagnostics = analyze_positioned_text([Item("W18X35", x=-12.5, y=-2.0)])
        payload = diagnostics[0].to_dict()
        self.assertEqual((payload["raw_x"], payload["raw_y"]), (-12.5, -2.0))
        self.assertEqual(payload["font"], "Arial")
        self.assertEqual(payload["detections"][0]["normalized_text"], "W18X35")

    def test_filters(self):
        _, _, diagnostics = analyze_positioned_text(
            [Item("W18X35", page=39), Item("HSS8X8X3/8", page=40), Item("GENERAL NOTE")]
        )
        self.assertEqual(len(filter_diagnostics(diagnostics, page=39)), 1)
        self.assertEqual(len(filter_diagnostics(diagnostics, contains="hss")), 1)
        self.assertEqual(
            len(filter_diagnostics(diagnostics, families=[SectionFamily.HSS])), 1
        )
        self.assertEqual(len(filter_diagnostics(diagnostics, candidates_only=True)), 2)
        self.assertEqual(len(filter_diagnostics(diagnostics, matches_only=True)), 2)

    def test_detection_family_page_and_text_filters(self):
        items = [label(page=39), label(page=40, value="W24X62")]
        self.assertEqual(len(filter_detections(items, page=39)), 1)
        self.assertEqual(len(filter_detections(items, contains="24x")), 1)
        self.assertEqual(len(filter_detections(items, families=[SectionFamily.W])), 2)

    def test_summary_counts(self):
        raw = [label(), label(), label(page=2, value="W24X62"), label(x=-1, page=3)]
        displayed, duplicate_count = deduplicate_detections(raw)
        _, rejected, _ = analyze_positioned_text([Item("W2.9X2.9")])
        summary = build_summary(raw, displayed, rejected, duplicate_count, "deduplicated")
        self.assertEqual(summary["total_raw_detections"], 4)
        self.assertEqual(summary["total_displayed_detections"], 3)
        self.assertEqual(summary["total_unique_section_values"], 2)
        self.assertEqual(summary["duplicate_count"], 1)
        self.assertEqual(summary["negative_x_detections"], 1)
        self.assertEqual(summary["rejected_likely_false_positives"], 1)


class DoctorTests(unittest.TestCase):
    def test_success_with_pdf(self):
        shoplens_module = ModuleType("shoplens")
        shoplens_module.__file__ = "/tmp/shoplens/__init__.py"
        pdf_module = ModuleType("pdf_inspector")
        pdf_module.__file__ = "/tmp/pdf_inspector.so"
        pdf_module.extract_text_with_positions = lambda path: [Item("W18X35")]

        def loader(name):
            return {"shoplens": shoplens_module, "pdf_inspector": pdf_module}[name]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drawing.pdf"
            path.touch()
            checks = run_doctor(path, loader)
        self.assertTrue(all(check.passed for check in checks))
        self.assertTrue(any(check.name == "Positioned text extraction" for check in checks))

    def test_missing_pdf_inspector(self):
        shoplens_module = ModuleType("shoplens")

        def loader(name):
            if name == "pdf_inspector":
                raise ImportError("missing native extension")
            return shoplens_module

        checks = run_doctor(module_loader=loader)
        self.assertFalse(checks[-1].passed)
        self.assertIn("maturin develop", checks[-1].detail)


if __name__ == "__main__":
    unittest.main()
