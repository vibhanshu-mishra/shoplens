"""The single conversion boundary between ShopLens and pdf-inspector pages."""

from typing import List, Sequence


def to_pdf_inspector_page_indexes(pages: Sequence[int]) -> List[int]:
    """Convert one-based public ShopLens pages to zero-based native indexes."""

    values = list(pages)
    if any(not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in values):
        raise ValueError("ShopLens page numbers must be one-based integers (1 or greater)")
    return [page - 1 for page in values]


def to_shoplens_page_number(pdf_inspector_page_index: int) -> int:
    """Convert a zero-based native page value to a one-based ShopLens page."""

    return int(pdf_inspector_page_index) + 1
