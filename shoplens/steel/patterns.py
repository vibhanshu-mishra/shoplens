"""Conservative patterns for common structural-steel designations."""

import re

# An individual designation component: whole number, decimal, or fraction.
NUMBER = r"(?:\d+\/\d+|\d+(?:\.\d+)?)"
X = r"\s*[x\u00d7]\s*"
PREFIX = r"(?<![A-Za-z0-9])"
SUFFIX = r"(?![A-Za-z0-9/.])"

_COMMON_ALTERNATIVES = (
    rf"2\s*L\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"HSS\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"C\s*{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"L\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"PL\s*{NUMBER}{X}{NUMBER}"
)

# Accepted W-shapes require a whole-number nominal depth. Decimal weights are valid.
STEEL_LABEL_PATTERN = re.compile(
    PREFIX
    + r"(?:"
    + _COMMON_ALTERNATIVES
    + r"|"
    + rf"W\s*\d+{X}{NUMBER}"
    + r")"
    + SUFFIX,
    re.IGNORECASE,
)

# Diagnostic candidate matching is intentionally broader so invalid W syntax can
# be reported instead of silently disappearing.
STEEL_CANDIDATE_PATTERN = re.compile(
    PREFIX
    + r"(?:"
    + _COMMON_ALTERNATIVES
    + r"|"
    + rf"W\s*{NUMBER}{X}{NUMBER}"
    + r")"
    + SUFFIX,
    re.IGNORECASE,
)

# Welded-wire reinforcement commonly appears as W-area X W-area, optionally
# preceded by a wire spacing such as 6X6-. Some PDF encodings omit the second W.
WELDED_WIRE_PATTERN = re.compile(
    PREFIX
    + rf"(?:\d+\s*[x\u00d7]\s*\d+\s*-\s*)?"
    + rf"W\s*\d+\.\d+{X}W?\s*\d+\.\d+"
    + SUFFIX,
    re.IGNORECASE,
)
