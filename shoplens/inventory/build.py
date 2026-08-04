"""Join classified sheets to steel-label detections and aggregate them."""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from shoplens.classification.models import ClassifiedSheet, PackageIndexResult
from shoplens.models import SectionFamily, SteelLabel
from shoplens.reporting import deduplicate_detections

from .models import (
    ClassifiedSectionDetection,
    InventoryCount,
    InventoryFilters,
    PackageSectionInventory,
    SheetSectionInventory,
)

INVENTORY_VERSION = "1.0"
RAW_MODE = "raw"
DEDUPLICATED_MODE = "deduplicated"


def build_section_inventory(
    package_index: PackageIndexResult,
    raw_detections: Iterable[SteelLabel],
    raw: bool = False,
) -> PackageSectionInventory:
    raw_records = list(raw_detections)
    deduplicated, suppressed = deduplicate_detections(raw_records)
    selected = raw_records if raw else deduplicated
    mode = RAW_MODE if raw else DEDUPLICATED_MODE
    indexed_by_page: Dict[int, List[ClassifiedSheet]] = defaultdict(list)
    for sheet in package_index.sheets:
        pages = sheet.actual_pdf_pages or ([sheet.pdf_page] if sheet.pdf_page is not None else [])
        for page in pages:
            indexed_by_page[page].append(sheet)

    warnings: List[str] = []
    duplicate_pages = {page for page, sheets in indexed_by_page.items() if len(sheets) > 1}
    for page in sorted(duplicate_pages):
        warnings.append(f"DUPLICATE_INDEXED_PDF_PAGE: {page}")

    raw_by_page = _by_page(raw_records)
    deduplicated_by_page = _by_page(deduplicated)
    selected_by_page = _by_page(selected)
    sheet_inventories: List[SheetSectionInventory] = []
    for sheet in package_index.sheets:
        pages = sheet.actual_pdf_pages or ([sheet.pdf_page] if sheet.pdf_page is not None else [])
        page = sheet.pdf_page
        page_warnings = list(sheet.warnings)
        if any(value in duplicate_pages for value in pages):
            page_warnings.append("DUPLICATE_INDEXED_PDF_PAGE")
        joinable_pages = [value for value in pages if value not in duplicate_pages]
        page_selected = [item for value in joinable_pages for item in selected_by_page.get(value, [])]
        wrapped = [_wrap_detection(item, sheet, mode) for item in page_selected]
        sheet_inventories.append(
            _sheet_inventory(
                sheet,
                wrapped,
                sum(len(raw_by_page.get(value, [])) for value in joinable_pages),
                sum(len(deduplicated_by_page.get(value, [])) for value in joinable_pages),
                page_warnings,
            )
        )

    unmatched: List[ClassifiedSectionDetection] = []
    for detection in selected:
        indexed = indexed_by_page.get(detection.page_number, [])
        if len(indexed) == 1:
            continue
        warning = "DUPLICATE_INDEXED_PDF_PAGE" if indexed else "UNMATCHED_DETECTION_PAGE"
        unmatched.append(_wrap_detection(detection, None, mode, [warning]))
        if not indexed:
            message = f"UNMATCHED_DETECTION_PAGE: {detection.page_number}"
            if message not in warnings:
                warnings.append(message)

    summaries = _package_summaries(sheet_inventories)
    sheets_with = sum(bool(sheet.detections) for sheet in sheet_inventories)
    warnings.extend(package_index.warnings)
    return PackageSectionInventory(
        source_file=package_index.source_file,
        total_indexed_sheets=len(sheet_inventories),
        sheets_with_detections=sheets_with,
        sheets_without_detections=len(sheet_inventories) - sheets_with,
        raw_detection_count=len(raw_records),
        deduplicated_detection_count=len(deduplicated),
        unique_section_count=len(summaries[1]),
        counts_by_family=summaries[0],
        counts_by_section=summaries[1],
        counts_by_subject=summaries[2],
        counts_by_level=summaries[3],
        counts_by_segment=summaries[4],
        counts_by_area=summaries[5],
        sheets=sheet_inventories,
        unmatched_detections=unmatched,
        warnings=list(dict.fromkeys(warnings)),
        inventory_version=INVENTORY_VERSION,
        record_mode=mode,
        duplicate_suppression_count=suppressed,
        unmatched_detection_count=len(unmatched),
    )


def filter_inventory_sheets(
    sheets: Sequence[SheetSectionInventory],
    filters: Optional[InventoryFilters] = None,
) -> List[SheetSectionInventory]:
    active = filters or InventoryFilters()
    return [
        sheet
        for sheet in sheets
        if active.matches_identity(sheet)
        and active.matches_classification(sheet)
        and active.matches_detections(sheet)
    ]


def matching_detections(
    sheet: SheetSectionInventory,
    family: Optional[SectionFamily] = None,
    section: Optional[str] = None,
) -> List[ClassifiedSectionDetection]:
    normalized_section = section.strip().upper() if section else None
    return [
        item
        for item in sheet.detections
        if (family is None or item.section_family == family)
        and (normalized_section is None or item.normalized_section == normalized_section)
    ]


def _by_page(detections: Iterable[SteelLabel]) -> Dict[int, List[SteelLabel]]:
    grouped: Dict[int, List[SteelLabel]] = defaultdict(list)
    for detection in detections:
        grouped[detection.page_number].append(detection)
    return grouped


def _wrap_detection(
    detection: SteelLabel,
    sheet: Optional[ClassifiedSheet],
    mode: str,
    warnings: Optional[List[str]] = None,
) -> ClassifiedSectionDetection:
    return ClassifiedSectionDetection(
        pdf_page=detection.page_number,
        sheet_number=sheet.sheet_number if sheet else None,
        sheet_title=(sheet.actual_title or sheet.declared_title) if sheet else None,
        sheet_kind=sheet.sheet_kind if sheet else None,
        sheet_subject=sheet.subject if sheet else None,
        level=sheet.level if sheet else None,
        segment=sheet.segment if sheet else None,
        area=list(sheet.area) if sheet else [],
        original_text=detection.original_text,
        normalized_section=detection.normalized_text,
        section_family=detection.section_family,
        raw_x=detection.x,
        raw_y=detection.y,
        raw_width=detection.width,
        raw_height=detection.height,
        confidence=detection.confidence,
        duplicate_count=detection.duplicate_count,
        record_mode=mode,
        warnings=warnings or [],
    )


def _sheet_inventory(
    sheet: ClassifiedSheet,
    detections: List[ClassifiedSectionDetection],
    raw_count: int,
    deduplicated_count: int,
    warnings: List[str],
) -> SheetSectionInventory:
    family_counts = Counter(item.section_family.value for item in detections)
    section_counts = Counter(item.normalized_section for item in detections)
    return SheetSectionInventory(
        pdf_page=sheet.pdf_page,
        pdf_pages=list(sheet.actual_pdf_pages or ([sheet.pdf_page] if sheet.pdf_page is not None else [])),
        sheet_number=sheet.sheet_number,
        sheet_title=sheet.actual_title or sheet.declared_title,
        sheet_kind=sheet.sheet_kind,
        sheet_subject=sheet.subject,
        level=sheet.level,
        segment=sheet.segment,
        area=list(sheet.area),
        raw_detection_count=raw_count,
        deduplicated_detection_count=deduplicated_count,
        unique_section_count=len(section_counts),
        counts_by_family=dict(sorted(family_counts.items())),
        counts_by_section=dict(sorted(section_counts.items())),
        detections=detections,
        warnings=list(dict.fromkeys(warnings)),
    )


def _package_summaries(
    sheets: Sequence[SheetSectionInventory],
) -> Tuple[
    Dict[str, InventoryCount],
    Dict[str, InventoryCount],
    Dict[str, InventoryCount],
    Dict[str, InventoryCount],
    Dict[str, InventoryCount],
    Dict[str, InventoryCount],
]:
    dimensions: List[Dict[str, List[Tuple[str, int]]]] = [defaultdict(list) for _ in range(6)]
    for sheet in sheets:
        identity = sheet.sheet_number or f"PDF_{sheet.pdf_page}"
        for detection in sheet.detections:
            dimensions[0][detection.section_family.value].append((identity, 1))
            dimensions[1][detection.normalized_section].append((identity, 1))
            dimensions[2][sheet.sheet_subject.value].append((identity, 1))
            if sheet.level:
                dimensions[3][sheet.level].append((identity, 1))
            if sheet.segment:
                dimensions[4][sheet.segment].append((identity, 1))
            for area in sheet.area:
                dimensions[5][area].append((identity, 1))
    summaries = [_summarize(values) for values in dimensions]
    return (
        summaries[0],
        summaries[1],
        summaries[2],
        summaries[3],
        summaries[4],
        summaries[5],
    )


def _summarize(values: Dict[str, List[Tuple[str, int]]]) -> Dict[str, InventoryCount]:
    return {
        key: InventoryCount(
            detection_count=sum(count for _, count in occurrences),
            sheet_count=len({sheet for sheet, _ in occurrences}),
        )
        for key, occurrences in sorted(values.items())
    }
