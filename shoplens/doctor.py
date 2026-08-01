"""Environment checks used by the ShopLens doctor command."""

import importlib
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, List, Optional


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self):
        return asdict(self)


def run_doctor(
    pdf_path: Optional[Path] = None,
    module_loader: Callable[[str], ModuleType] = importlib.import_module,
) -> List[DoctorCheck]:
    """Check imports and, optionally, real positioned extraction from one PDF."""

    checks = [DoctorCheck("Python version", True, sys.version.split()[0])]
    try:
        shoplens_module = module_loader("shoplens")
        checks.append(
            DoctorCheck("ShopLens import", True, str(getattr(shoplens_module, "__file__", "unknown")))
        )
    except ImportError as exc:
        checks.append(DoctorCheck("ShopLens import", False, str(exc)))

    if pdf_path is not None:
        exists = pdf_path.is_file()
        checks.append(
            DoctorCheck(
                "PDF path",
                exists,
                str(pdf_path) if exists else f"File does not exist: {pdf_path}",
            )
        )

    try:
        pdf_module = module_loader("pdf_inspector")
    except ImportError as exc:
        checks.append(
            DoctorCheck(
                "pdf_inspector import",
                False,
                f"{exc}. Run `python -m maturin develop --release` in the active environment.",
            )
        )
        return checks

    checks.append(
        DoctorCheck(
            "pdf_inspector import",
            True,
            str(getattr(pdf_module, "__file__", "location unavailable")),
        )
    )
    extractor = getattr(pdf_module, "extract_text_with_positions", None)
    has_extractor = callable(extractor)
    checks.append(
        DoctorCheck(
            "Positioned-text API",
            has_extractor,
            "extract_text_with_positions is available"
            if has_extractor
            else "Rebuild this repository with `python -m maturin develop --release`.",
        )
    )
    if pdf_path is None:
        return checks

    exists = pdf_path.is_file()
    if not exists or not has_extractor:
        return checks
    try:
        items = list(extractor(str(pdf_path)))
        checks.append(DoctorCheck("PDF open", True, "pdf-inspector opened the PDF"))
        checks.append(
            DoctorCheck(
                "Positioned text extraction",
                bool(items),
                f"Extracted {len(items)} positioned text items"
                if items
                else "No positioned text was extracted; this may be a scanned PDF.",
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("PDF open", False, f"pdf-inspector error: {exc}"))
    return checks
