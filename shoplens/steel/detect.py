"""Detect steel labels within individual positioned text items."""

from typing import Iterable, List, Protocol

from shoplens.models import SteelLabel

from .normalize import normalize_steel_label
from .patterns import STEEL_LABEL_PATTERN


class PositionedText(Protocol):
    """The subset of pdf_inspector.TextItem consumed by ShopLens."""

    text: str
    page: int
    x: float
    y: float
    width: float
    height: float


def detect_steel_labels(items: Iterable[PositionedText]) -> List[SteelLabel]:
    """Find complete labels inside each item without spatial item joining."""

    detections: List[SteelLabel] = []
    for item in items:
        text = item.text
        text_length = len(text)
        for match in STEEL_LABEL_PATTERN.finditer(text):
            normalized, family = normalize_steel_label(match.group(0))
            # An item may contain several labels. Approximate each match's box
            # proportionally within the source item until glyph boxes are exposed.
            fraction_start = match.start() / text_length if text_length else 0.0
            fraction_width = (match.end() - match.start()) / text_length if text_length else 1.0
            detections.append(
                SteelLabel(
                    page_number=int(item.page),
                    original_text=match.group(0),
                    normalized_text=normalized,
                    section_family=family,
                    x=float(item.x) + float(item.width) * fraction_start,
                    y=float(item.y),
                    width=float(item.width) * fraction_width,
                    height=float(item.height),
                    confidence=1.0,
                )
            )
    return detections
