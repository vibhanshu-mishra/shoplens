"""Detect steel labels within individual positioned text items."""

from typing import Iterable, List, Optional, Protocol, Tuple

from shoplens.models import RejectedCandidate, SteelLabel, TextDiagnostic

from .normalize import normalize_steel_label
from .patterns import STEEL_CANDIDATE_PATTERN, WELDED_WIRE_PATTERN

WELDED_WIRE_REINFORCEMENT = "WELDED_WIRE_REINFORCEMENT"
IMPLAUSIBLE_W_SECTION = "IMPLAUSIBLE_W_SECTION"


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

    detections, _, _ = analyze_positioned_text(items)
    return detections


def analyze_positioned_text(
    items: Iterable[PositionedText],
) -> Tuple[List[SteelLabel], List[RejectedCandidate], List[TextDiagnostic]]:
    """Analyze source items while preserving accepted and rejected candidates."""

    detections: List[SteelLabel] = []
    rejections: List[RejectedCandidate] = []
    diagnostics: List[TextDiagnostic] = []
    for item in items:
        text = item.text
        text_length = len(text)
        item_detections: List[SteelLabel] = []
        item_reasons: List[str] = []
        welded_wire_candidates = list(WELDED_WIRE_PATTERN.finditer(text))
        candidates = [
            match
            for match in STEEL_CANDIDATE_PATTERN.finditer(text)
            if not any(
                match.start() < wire.end() and wire.start() < match.end()
                for wire in welded_wire_candidates
            )
        ]
        for match in welded_wire_candidates:
            fraction_start = match.start() / text_length if text_length else 0.0
            fraction_width = (match.end() - match.start()) / text_length if text_length else 1.0
            reason = WELDED_WIRE_REINFORCEMENT
            rejections.append(
                RejectedCandidate(
                    page_number=int(item.page),
                    original_text=match.group(0),
                    reason=reason,
                    x=float(item.x) + float(item.width) * fraction_start,
                    y=float(item.y),
                    width=float(item.width) * fraction_width,
                    height=float(item.height),
                )
            )
            item_reasons.append(reason)
        for match in candidates:
            normalized, family = normalize_steel_label(match.group(0))
            # An item may contain several labels. Approximate each match's box
            # proportionally within the source item until glyph boxes are exposed.
            fraction_start = match.start() / text_length if text_length else 0.0
            fraction_width = (match.end() - match.start()) / text_length if text_length else 1.0
            x = float(item.x) + float(item.width) * fraction_start
            width = float(item.width) * fraction_width
            if family.value == "UNKNOWN":
                reason = IMPLAUSIBLE_W_SECTION
                rejections.append(
                    RejectedCandidate(
                        page_number=int(item.page),
                        original_text=match.group(0),
                        reason=reason,
                        x=x,
                        y=float(item.y),
                        width=width,
                        height=float(item.height),
                    )
                )
                item_reasons.append(reason)
                continue
            detection = SteelLabel(
                page_number=int(item.page),
                original_text=match.group(0),
                normalized_text=normalized,
                section_family=family,
                x=x,
                y=float(item.y),
                width=width,
                height=float(item.height),
                confidence=1.0,
            )
            detections.append(detection)
            item_detections.append(detection)
        diagnostics.append(
            TextDiagnostic(
                source_page=int(item.page),
                page_number=int(item.page),
                text=text,
                x=float(item.x),
                y=float(item.y),
                width=float(item.width),
                height=float(item.height),
                font=_optional_str(item, "font"),
                font_size=_optional_float(item, "font_size"),
                is_candidate=bool(candidates or welded_wire_candidates),
                section_detected=bool(item_detections),
                detections=item_detections,
                rejection_reasons=item_reasons,
            )
        )
    return detections, rejections, diagnostics


def _optional_str(item: PositionedText, name: str) -> Optional[str]:
    value = getattr(item, name, None)
    return str(value) if value is not None else None


def _optional_float(item: PositionedText, name: str) -> Optional[float]:
    value = getattr(item, name, None)
    return float(value) if value is not None else None
