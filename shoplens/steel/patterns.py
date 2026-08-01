"""Conservative patterns for common structural-steel designations."""

import re

# An individual designation component: whole number, decimal, or fraction.
NUMBER = r"(?:\d+\/\d+|\d+(?:\.\d+)?)"
X = r"\s*[x\u00d7]\s*"
PREFIX = r"(?<![A-Za-z0-9])"
SUFFIX = r"(?![A-Za-z0-9/.])"

STEEL_LABEL_PATTERN = re.compile(
    PREFIX
    + r"(?:"
    + rf"2\s*L\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"HSS\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"(?:W|C)\s*{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"L\s*{NUMBER}{X}{NUMBER}{X}{NUMBER}"
    + r"|"
    + rf"PL\s*{NUMBER}{X}{NUMBER}"
    + r")"
    + SUFFIX,
    re.IGNORECASE,
)
