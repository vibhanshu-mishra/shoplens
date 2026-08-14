"""Local, privacy-safe regression checks for compact grid geometry summaries."""

from .compare import compare_geometry_reports
from .config import load_geometry_config
from .models import GeometryCaseConfig, GeometryValidationConfig
from .reporting import write_geometry_baseline, write_geometry_csv, write_geometry_json, write_geometry_markdown
from .runner import run_geometry_validation

__all__ = [
    "GeometryCaseConfig", "GeometryValidationConfig", "compare_geometry_reports", "load_geometry_config",
    "run_geometry_validation", "write_geometry_baseline", "write_geometry_csv", "write_geometry_json", "write_geometry_markdown",
]
