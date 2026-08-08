"""Public ShopLens page-number contract tests for native extraction adapters."""

import argparse
import sys
import unittest
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shoplens import cli
from shoplens.extraction.pdf_text import extract_positioned_text
from shoplens.extraction.page_numbers import to_pdf_inspector_page_indexes
from shoplens.geometry.adapter import extract_page_geometry


def native_item(page):
    return SimpleNamespace(
        text="text", page=page, x=1.0, y=2.0, width=3.0, height=4.0,
        font="F1", font_size=4.0,
    )


class PublicPageNumberContractTests(unittest.TestCase):
    def test_public_pages_convert_to_zero_based_native_indexes(self):
        self.assertEqual(to_pdf_inspector_page_indexes([1]), [0])
        self.assertEqual(to_pdf_inspector_page_indexes([14]), [13])
        self.assertEqual(to_pdf_inspector_page_indexes([59]), [58])
        self.assertEqual(to_pdf_inspector_page_indexes([1, 14, 59]), [0, 13, 58])

    def test_invalid_public_pages_are_rejected(self):
        for page in (0, -1):
            with self.assertRaisesRegex(ValueError, "one-based"):
                to_pdf_inspector_page_indexes([page])
            with self.assertRaisesRegex(argparse.ArgumentTypeError, "1 or greater"):
                cli._one_based_page(str(page))

    def test_positioned_text_adapter_filters_zero_based_and_returns_one_based_pages(self):
        recorded = []

        def extract(path, pages=None):
            recorded.append((path, pages))
            return [native_item(0), native_item(13), native_item(58)]

        native = SimpleNamespace(extract_text_with_positions=extract)
        with patch.dict(sys.modules, {"pdf_inspector": native}):
            items = extract_positioned_text("drawing.pdf", pages=[1, 14, 59])

        self.assertEqual(recorded, [("drawing.pdf", [0, 13, 58])])
        self.assertEqual([item.page for item in items], [1, 14, 59])
        self.assertEqual(items[1].text, "text")

    def test_positioned_text_adapter_extracts_all_pages_without_a_filter(self):
        recorded = []

        def extract(path, pages=None):
            recorded.append((path, pages))
            return [native_item(0)]

        native = SimpleNamespace(extract_text_with_positions=extract)
        with patch.dict(sys.modules, {"pdf_inspector": native}):
            items = extract_positioned_text("drawing.pdf")

        self.assertEqual(recorded, [("drawing.pdf", None)])
        self.assertEqual(items[0].page, 1)

    def test_native_geometry_uses_the_same_page_conversion(self):
        recorded = []
        geometry = SimpleNamespace(
            page=13, width=100.0, height=200.0, rotation=0,
            media_box=(0.0, 0.0, 100.0, 200.0), crop_box=(0.0, 0.0, 100.0, 200.0),
            coordinate_system="native", lines=[], rectangles=[], warnings=[],
        )

        def extract(path, pages=None):
            recorded.append((path, pages))
            return [geometry]

        native = SimpleNamespace(extract_page_geometry=extract)
        with patch.dict(sys.modules, {"pdf_inspector": native}):
            result = extract_page_geometry("drawing.pdf", [14])

        self.assertEqual(recorded, [("drawing.pdf", [13])])
        self.assertEqual(result[0].pdf_page, 14)

    def test_debug_text_page_filter_uses_the_public_page_value(self):
        args = SimpleNamespace(
            pdf=Path("drawing.pdf"), page=14, contains=None, family=None,
            candidates_only=False, matches_only=False, json=False,
        )
        with patch.object(cli, "_load_items", return_value=([], 0)) as load_items, \
             redirect_stdout(StringIO()):
            status = cli._run_debug_text(args)

        self.assertEqual(status, 0)
        load_items.assert_called_once_with(args.pdf, pages=[14])
