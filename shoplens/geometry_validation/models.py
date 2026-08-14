"""Privacy-safe, compact results for local geometry regression checks."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GeometryCaseConfig:
    case_id: str
    pdf: str
    page: Optional[int] = None
    sheet: Optional[str] = None
    checks: List[str] = field(default_factory=lambda: ["GRID", "LOCALIZATION"])


@dataclass(frozen=True)
class GeometryValidationConfig:
    schema_version: int
    cases: List[GeometryCaseConfig]
    coordinate_tolerance: float = 2.0


@dataclass
class GeometryCaseResult:
    case_id: str
    execution_status: str
    selected_page: Optional[int] = None
    grid: Optional[Dict[str, Any]] = None
    localization: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    source_path: Optional[str] = None

    def to_dict(self, debug: bool = False) -> Dict[str, Any]:
        value = asdict(self)
        if not debug:
            value.pop("source_path", None)
        return value


@dataclass
class GeometryValidationResult:
    schema_version: int
    started_at: str
    completed_at: str
    runtime_seconds: float
    coordinate_tolerance: float
    git_revision: Optional[str]
    case_results: List[GeometryCaseResult]
    comparison: Optional[Dict[str, Any]] = None

    def to_dict(self, debug: bool = False) -> Dict[str, Any]:
        value = asdict(self)
        value["case_results"] = [item.to_dict(debug=debug) for item in self.case_results]
        return value
