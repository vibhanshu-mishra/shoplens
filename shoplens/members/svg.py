"""Geometry-only SVG for member-line candidate review."""

from pathlib import Path
from typing import Sequence, Union
from xml.etree.ElementTree import Element, ElementTree, SubElement

from .models import LineOrientation, MemberLineCandidate, MemberLineCandidateResult


COLORS = {
    LineOrientation.HORIZONTAL: "#2563eb",
    LineOrientation.VERTICAL: "#16a34a",
    LineOrientation.DIAGONAL: "#ea580c",
    LineOrientation.OTHER: "#7c3aed",
}


def export_member_candidates_svg(
    path: Union[str, Path],
    result: MemberLineCandidateResult,
    candidates: Sequence[MemberLineCandidate],
    include_rejected: bool = False,
) -> None:
    geometry = result.page_geometry
    x0, _, _, y1 = geometry.crop_box

    def point(x: float, y: float):
        return x - x0, y1 - y

    root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {geometry.width:.3f} {geometry.height:.3f}"})
    SubElement(root, "rect", {"x": "0", "y": "0", "width": str(geometry.width), "height": str(geometry.height), "fill": "white", "stroke": "#111827"})
    bx0, by0 = point(result.plan_region_bounds[0], result.plan_region_bounds[3])
    bx1, by1 = point(result.plan_region_bounds[2], result.plan_region_bounds[1])
    SubElement(root, "rect", {"x": str(bx0), "y": str(by0), "width": str(bx1 - bx0), "height": str(by1 - by0), "fill": "none", "stroke": "#a855f7", "stroke-width": "2", "stroke-dasharray": "10 6"})
    for axis in result.grid_system.horizontal_axes + result.grid_system.vertical_axes:
        sx, sy = point(axis.start_x, axis.start_y)
        ex, ey = point(axis.end_x, axis.end_y)
        SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": "#94a3b8", "stroke-width": "1"})
    if include_rejected:
        for item in result.rejected_candidates[:4000]:
            sx, sy = point(item.start_x, item.start_y)
            ex, ey = point(item.end_x, item.end_y)
            SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": "#d1d5db", "stroke-width": "0.5", "opacity": "0.45"})
    for item in candidates:
        sx, sy = point(item.start_x, item.start_y)
        ex, ey = point(item.end_x, item.end_y)
        color = COLORS[item.orientation_class]
        SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": color, "stroke-width": "2.5", "opacity": "0.85"})
        for x, y in ((sx, sy), (ex, ey)):
            SubElement(root, "circle", {"cx": str(x), "cy": str(y), "r": "3", "fill": "white", "stroke": color, "stroke-width": "1.5"})
        label = SubElement(root, "text", {"x": str((sx + ex) / 2.0 + 3), "y": str((sy + ey) / 2.0 - 3), "font-size": "7", "fill": color})
        label.text = item.candidate_id
    ElementTree(root).write(str(path), encoding="unicode", xml_declaration=True)
