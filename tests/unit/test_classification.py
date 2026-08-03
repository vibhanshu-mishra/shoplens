"""Synthetic tests for deterministic sheet classification and package indexing."""

import re
import unittest
from unittest.mock import patch

from shoplens.classification import (
    ClassificationTitleSource,
    SheetKind,
    StructuralSubject,
    build_package_index,
    classify_entry,
    filter_sheets,
)
from shoplens.classification.rules import ClassificationRule
from shoplens.title_blocks.models import (
    ReconciliationEntry,
    ReconciliationResult,
    ReconciliationStatus,
)


def entry(number, title, page=1, declared_title=None):
    return ReconciliationEntry(
        declared_sheet_number=number,
        declared_sheet_title=declared_title if declared_title is not None else title,
        actual_pdf_pages=[] if page is None else [page],
        actual_sheet_number=number if page is not None else None,
        actual_sheet_title=title,
        revision=None,
        status=ReconciliationStatus.MATCH,
        title_similarity=1.0,
        confidence=1.0,
        warnings=[],
    )


def reconciliation(entries):
    return ReconciliationResult(
        source_file="drawing.pdf",
        declared_sheet_count=len(entries),
        total_pdf_pages_processed=len([value for value in entries if value.actual_pdf_pages]),
        identified_page_count=len([value for value in entries if value.actual_pdf_pages]),
        unidentified_pages=[],
        missing_declared_sheets=[],
        undeclared_actual_sheets=[],
        duplicate_actual_sheet_numbers={},
        title_mismatches=[],
        entries=entries,
        warnings=[],
    )


class ClassificationRuleTests(unittest.TestCase):
    def assert_classification(self, title, kind, subject, number="S1-01"):
        result = classify_entry(entry(number, title))
        self.assertEqual(result.sheet_kind, kind)
        self.assertEqual(result.subject, subject)
        return result

    def test_notes_loading_foundation_floor_and_roof_plans(self):
        self.assert_classification("GENERAL NOTES", SheetKind.NOTES, StructuralSubject.GENERAL_NOTES)
        self.assert_classification("LOADING DIAGRAMS", SheetKind.DIAGRAM, StructuralSubject.LOADING)
        foundation = self.assert_classification(
            "FOUNDATION PLAN - SEGMENT A", SheetKind.PLAN, StructuralSubject.FOUNDATION_PLAN, "S1-10A"
        )
        self.assertEqual((foundation.level, foundation.segment), ("FOUNDATION", "A"))
        floor = self.assert_classification(
            "SECOND FLOOR FRAMING PLAN - SEGMENT B", SheetKind.PLAN, StructuralSubject.FLOOR_FRAMING, "S1-20B"
        )
        self.assertEqual(floor.level, "SECOND FLOOR")
        roof = self.assert_classification(
            "ROOF FRAMING PLAN - SEGMENT N", SheetKind.PLAN, StructuralSubject.ROOF_FRAMING, "S1-20N"
        )
        self.assertEqual((roof.level, roof.segment), ("ROOF", "N"))

    def test_platform_areas_modifiers_and_group_keys(self):
        platform = self.assert_classification(
            "MECHANICAL PLATFORM FRAMING PLAN - SEGMENT E",
            SheetKind.PLAN,
            StructuralSubject.PLATFORM_FRAMING,
            "S1-30E",
        )
        self.assertEqual(platform.area, ["MECHANICAL PLATFORM"])
        self.assertEqual(platform.segment, "E")
        overall = self.assert_classification(
            "OVERALL PLATFORM AND OFFICE ROOF FRAMING PLAN",
            SheetKind.PLAN,
            StructuralSubject.ROOF_FRAMING,
        )
        self.assertIn("OFFICE ROOF", overall.area)
        self.assertIn("OVERALL", overall.modifiers)
        self.assertIn(StructuralSubject.PLATFORM_FRAMING, overall.secondary_subjects)
        office = self.assert_classification(
            "OFFICE ROOF & SCREEN WALL FRAMING PLAN - SEGMENT B",
            SheetKind.PLAN,
            StructuralSubject.ROOF_FRAMING,
            "S1-30B",
        )
        self.assertEqual(office.area, ["OFFICE ROOF"])

    def test_elevations_and_specific_details(self):
        self.assert_classification("BRACED FRAME ELEVATIONS", SheetKind.ELEVATION, StructuralSubject.BRACED_FRAME)
        wind = self.assert_classification("TYPICAL WIND BRACING DETAILS", SheetKind.DETAIL, StructuralSubject.WIND_BRACING)
        self.assertIn("TYPICAL", wind.modifiers)
        foundation = self.assert_classification("TYPICAL FOUNDATION DETAILS", SheetKind.DETAIL, StructuralSubject.FOUNDATION_DETAIL)
        self.assertEqual(foundation.level, "FOUNDATION")
        connection = self.assert_classification("DOUBLE ANGLE BOLTED-WELDED CONNECTION", SheetKind.DETAIL, StructuralSubject.CONNECTION)
        self.assertIn("CONNECTION:DOUBLE_ANGLE", connection.group_keys)
        steel = self.assert_classification("TYPICAL STEEL FRAMING SECTIONS AND DETAILS", SheetKind.DETAIL, StructuralSubject.STEEL_FRAMING)
        self.assertIn(SheetKind.SECTION, steel.secondary_kinds)
        self.assert_classification("STAIR FRAMING DETAILS", SheetKind.DETAIL, StructuralSubject.STAIR_FRAMING)
        self.assert_classification("STEEL PLATFORM DETAILS", SheetKind.DETAIL, StructuralSubject.PLATFORM)
        self.assert_classification("WEST STAIR TOWER FRAMING PLANS", SheetKind.PLAN, StructuralSubject.STAIR_FRAMING)

    def test_column_schedule_preserves_secondary_taxonomy(self):
        result = self.assert_classification(
            "TYPICAL STEEL COLUMN DETAILS AND BASE PLATE SCHEDULE",
            SheetKind.DETAIL,
            StructuralSubject.STEEL_COLUMN,
        )
        self.assertIn(SheetKind.SCHEDULE, result.secondary_kinds)
        self.assertIn(StructuralSubject.BASE_PLATE, result.secondary_subjects)

    def test_segment_conflict_and_suffix_support(self):
        conflict = classify_entry(entry("S1-20B", "ROOF FRAMING PLAN - SEGMENT A"))
        self.assertEqual(conflict.segment, "A")
        self.assertIn("SEGMENT_CONFLICT", conflict.warnings)
        supported = classify_entry(entry("S1-20A", "ROOF FRAMING PLAN - SEGMENT A"))
        self.assertIn("SHEET_SUFFIX_SUPPORTS_SEGMENT", supported.classification_evidence)

    def test_title_fallback_missing_and_unknown(self):
        fallback = classify_entry(entry("S0-00", None, declared_title="GENERAL NOTES"))
        self.assertEqual(fallback.classification_title_source, ClassificationTitleSource.DECLARED_TITLE)
        self.assertEqual(fallback.subject, StructuralSubject.GENERAL_NOTES)
        missing = classify_entry(entry(None, None, page=None, declared_title=None))
        self.assertEqual(missing.sheet_kind, SheetKind.UNKNOWN)
        self.assertIn("TITLE_SOURCE_MISSING", missing.warnings)
        unknown = classify_entry(entry("S0-10", "UNCLASSIFIED CONTENT"))
        self.assertIn("UNKNOWN_CLASSIFICATION", unknown.warnings)

    def test_explicit_drawing_views_and_similar_non_view_title(self):
        for title in (
            "OVERALL 3D VIEW",
            "STRUCTURAL ISOMETRIC VIEW",
            "AXONOMETRIC VIEW",
            "PERSPECTIVE VIEW",
        ):
            with self.subTest(title=title):
                result = classify_entry(entry("S0-10", title))
                self.assertEqual(result.sheet_kind, SheetKind.VIEW)
                self.assertEqual(result.subject, StructuralSubject.OTHER_STRUCTURAL)
                self.assertEqual(result.matched_rule, "DRAWING_VIEW")
                self.assertEqual(result.classification_confidence, 0.98)
        overall = classify_entry(entry("S0-10", "OVERALL 3D VIEW"))
        self.assertIn("OVERALL", overall.modifiers)
        viewing_platform = classify_entry(entry("S5-40", "OVERALL VIEWING PLATFORM"))
        self.assertNotEqual(viewing_platform.sheet_kind, SheetKind.VIEW)

    def test_equal_priority_conflict_is_not_silently_ordered(self):
        rules = (
            ClassificationRule("ONE", 100, re.compile("TEST"), SheetKind.PLAN, StructuralSubject.FOUNDATION_PLAN, 0.9),
            ClassificationRule("TWO", 100, re.compile("TEST"), SheetKind.DETAIL, StructuralSubject.FOUNDATION_DETAIL, 0.9),
        )
        with patch("shoplens.classification.rules.RULES", rules):
            result = classify_entry(entry("S1-01", "TEST"))
        self.assertEqual(result.sheet_kind, SheetKind.UNKNOWN)
        self.assertIn("MULTIPLE_PRIMARY_RULES", result.warnings)

    def test_low_confidence_and_level_conflict_warnings(self):
        rules = (
            ClassificationRule("WEAK", 100, re.compile("TEST"), SheetKind.GENERAL, StructuralSubject.OTHER_STRUCTURAL, 0.65),
        )
        with patch("shoplens.classification.rules.RULES", rules):
            weak = classify_entry(entry("S1-01", "TEST"))
        self.assertIn("LOW_CLASSIFICATION_CONFIDENCE", weak.warnings)
        conflict = classify_entry(entry("S1-20", "SECOND FLOOR AND ROOF FRAMING PLAN"))
        self.assertIsNone(conflict.level)
        self.assertIn("LEVEL_CONFLICT", conflict.warnings)


class PackageIndexTests(unittest.TestCase):
    def test_counts_order_lookups_filters_and_json(self):
        entries = [
            entry("S5-30", "STAIR FRAMING DETAILS", 3),
            entry("S1-20A", "SECOND FLOOR FRAMING PLAN - SEGMENT A", 2),
            entry("S0-00", "GENERAL NOTES", 1),
        ]
        result = build_package_index(reconciliation(entries))
        self.assertEqual([sheet.pdf_page for sheet in result.sheets], [1, 2, 3])
        self.assertEqual(result.indexed_sheet_count, 3)
        self.assertEqual(result.classified_sheet_count, 3)
        self.assertEqual(result.by_sheet_number("s1-20a").pdf_page, 2)
        self.assertEqual(result.by_pdf_page(3).subject, StructuralSubject.STAIR_FRAMING)
        self.assertEqual(len(result.by_kind(SheetKind.PLAN)), 1)
        self.assertEqual(len(result.by_segment("a")), 1)
        selected = filter_sheets(result.sheets, subject=StructuralSubject.FLOOR_FRAMING, level="second floor")
        self.assertEqual([sheet.sheet_number for sheet in selected], ["S1-20A"])
        payload = result.to_dict(include_debug=True)
        self.assertEqual(payload["sheets"][1]["sheet_kind"], "PLAN")
        self.assertIn("candidate_rules", payload["sheets"][1])

    def test_exactly_one_record_per_reconciliation_entry_and_unknown_filter(self):
        entries = [entry("S0-10", "UNCLASSIFIED CONTENT", 1), entry("S0-00", "GENERAL NOTES", 2)]
        result = build_package_index(reconciliation(entries))
        self.assertEqual(result.indexed_sheet_count, len(entries))
        self.assertEqual(result.unknown_sheet_count, 1)
        self.assertIn("S0-10: UNKNOWN_CLASSIFICATION", result.warnings)
        self.assertEqual([sheet.sheet_number for sheet in filter_sheets(result.sheets, unknown_only=True)], ["S0-10"])


if __name__ == "__main__":
    unittest.main()
