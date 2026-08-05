"""Standard-library JSON configuration for local validation."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ValidationConfig:
    evaluation_root: Optional[str] = None
    output_directory: str = "/tmp/shoplens-validation"
    include_patterns: List[str] = field(default_factory=lambda: ["*.pdf"])
    exclude_patterns: List[str] = field(default_factory=list)
    max_files: Optional[int] = None
    selected_files: List[str] = field(default_factory=list)
    timeout_per_stage: Optional[float] = None
    deep_validation_enabled: bool = False


def load_config(path: Optional[Path]) -> ValidationConfig:
    if path is None:
        return ValidationConfig()
    values = json.loads(path.read_text(encoding="utf-8"))
    known = {name for name in ValidationConfig.__dataclass_fields__}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ValueError(f"unknown validation configuration fields: {', '.join(unknown)}")
    config = ValidationConfig(**values)
    if config.max_files is not None and config.max_files < 0:
        raise ValueError("max_files must be nonnegative")
    if config.timeout_per_stage is not None and config.timeout_per_stage <= 0:
        raise ValueError("timeout_per_stage must be greater than zero")
    if not isinstance(config.include_patterns, list) or not isinstance(config.exclude_patterns, list):
        raise ValueError("include_patterns and exclude_patterns must be lists")
    return config
