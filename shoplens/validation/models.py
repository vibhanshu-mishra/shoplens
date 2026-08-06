"""Typed package-level validation results without confidential drawing content."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ValidationStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNREVIEWED = "UNREVIEWED"
    TIMEOUT = "TIMEOUT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_REVIEWED = "NOT_REVIEWED"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    HUMAN_FAILED = "HUMAN_FAILED"


@dataclass
class ValidationStageResult:
    stage_name: str
    status: ValidationStatus
    runtime_seconds: float
    metrics: Dict[str, Any]
    warnings: List[str]
    errors: List[str]
    review_status: ReviewStatus

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["review_status"] = self.review_status.value
        return value


@dataclass
class ValidationPackageResult:
    source_file_name: str
    relative_path: str
    source_file_path: str
    file_size_bytes: int
    runtime_seconds: float
    overall_status: ValidationStatus
    stages: List[ValidationStageResult]
    warnings: List[str]
    errors: List[str]

    def to_dict(self, debug: bool = False) -> Dict[str, Any]:
        value = asdict(self)
        value["overall_status"] = self.overall_status.value
        value["stages"] = [stage.to_dict() for stage in self.stages]
        if not debug:
            value.pop("source_file_path", None)
        return value


@dataclass
class ValidationSuiteResult:
    validation_version: str
    started_at: str
    completed_at: str
    runtime_seconds: float
    evaluation_root: str
    evaluation_root_path: str
    pdf_count: int
    packages_passed: int
    packages_failed: int
    packages_with_warnings: int
    stage_summary: Dict[str, Dict[str, int]]
    package_results: List[ValidationPackageResult]
    warnings: List[str]
    environment: Dict[str, Any]
    comparison: Optional[Dict[str, Any]] = None

    def to_dict(self, debug: bool = False) -> Dict[str, Any]:
        value = asdict(self)
        value["package_results"] = [item.to_dict(debug=debug) for item in self.package_results]
        if not debug:
            value.pop("evaluation_root_path", None)
        return value
