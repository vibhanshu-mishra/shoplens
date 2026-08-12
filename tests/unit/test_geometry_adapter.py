"""Focused coverage for pypdf fallback dependency guidance."""

import unittest
from unittest.mock import patch

from shoplens.geometry.adapter import _extract_with_pypdf


class PypdfFallbackGuidanceTests(unittest.TestCase):
    def test_missing_pypdf_reports_current_supported_dependency_range(self):
        with patch("builtins.__import__", side_effect=ImportError("missing pypdf")):
            with self.assertRaisesRegex(RuntimeError, r"pypdf>=6\.15,<7"):
                _extract_with_pypdf("drawing.pdf", [1], None)
