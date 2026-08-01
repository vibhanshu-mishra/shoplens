"""Normalization of recognized steel labels only."""

import re
from typing import Tuple

from shoplens.models import SectionFamily

from .patterns import STEEL_LABEL_PATTERN


def normalize_steel_label(value: str) -> Tuple[str, SectionFamily]:
    """Normalize a complete recognized designation and return its family.

    Unrelated text is returned unchanged with the UNKNOWN family, ensuring this
    helper cannot accidentally rewrite ordinary drawing notes.
    """

    match = STEEL_LABEL_PATTERN.fullmatch(value.strip())
    if match is None:
        return value, SectionFamily.UNKNOWN

    normalized = re.sub(r"\s+", "", match.group(0).upper().replace("\u00d7", "X"))
    if normalized.startswith("2L"):
        family = SectionFamily.DOUBLE_ANGLE
    elif normalized.startswith("HSS"):
        family = SectionFamily.HSS
    elif normalized.startswith("PL"):
        family = SectionFamily.PL
    elif normalized.startswith("W"):
        family = SectionFamily.W
    elif normalized.startswith("C"):
        family = SectionFamily.C
    elif normalized.startswith("L"):
        family = SectionFamily.L
    else:
        family = SectionFamily.UNKNOWN
    return normalized, family
