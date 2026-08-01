"""Thin adapter around pdf-inspector's positioned-text Python API."""

from pathlib import Path
from typing import Any, List, Union


class PdfInspectorUnavailableError(RuntimeError):
    """Raised when the native pdf-inspector Python extension cannot be loaded."""


def extract_positioned_text(path: Union[str, Path]) -> List[Any]:
    """Extract 1-based page numbers and PDF-point bounding boxes from a PDF."""

    try:
        import pdf_inspector
    except ImportError as exc:
        raise PdfInspectorUnavailableError(
            "The pdf-inspector Python extension is not installed. From the repository "
            "root, run `python -m pip install maturin` and then "
            "`python -m maturin develop --release`."
        ) from exc

    try:
        return list(pdf_inspector.extract_text_with_positions(str(path)))
    except (AttributeError, ImportError) as exc:
        raise PdfInspectorUnavailableError(
            "The installed pdf-inspector extension does not provide positioned text. "
            "Rebuild this repository with `python -m maturin develop --release`."
        ) from exc
