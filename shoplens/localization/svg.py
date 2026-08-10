"""Geometry-only SVG overlay for grid-relative section locations."""

from pathlib import Path
from typing import Union
from xml.etree.ElementTree import Element, ElementTree, SubElement

from .models import SheetGridSectionLocalization


def export_localization_svg(path: Union[str, Path], result: SheetGridSectionLocalization) -> None:
    if result.grid_system is None:
        raise ValueError("cannot export localization SVG without a grid system")
    grid = result.grid_system
    geometry = grid.page_geometry
    x0, _, _, y1 = geometry.crop_box

    def point(x: float, y: float):
        return x - x0, y1 - y

    root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {geometry.width:.3f} {geometry.height:.3f}"})
    SubElement(root, "rect", {"x": "0", "y": "0", "width": str(geometry.width), "height": str(geometry.height), "fill": "white", "stroke": "#222"})
    for axis in grid.horizontal_axes + grid.vertical_axes:
        sx, sy = point(axis.start_x, axis.start_y)
        ex, ey = point(axis.end_x, axis.end_y)
        SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": "#64748b", "stroke-width": "1.5"})
        lx, ly = point(axis.start_x, axis.start_y)
        label = SubElement(root, "text", {"x": str(lx + 3), "y": str(ly - 3), "font-size": "9", "fill": "#334155"})
        label.text = axis.normalized_label
    for item in result.detections:
        x, y = point(item.detection_anchor_x, item.detection_anchor_y)
        color = {
            "COMPLETE_BAY": "#16a34a",
            "ON_AXIS": "#f59e0b",
            "OUTSIDE_GRID": "#dc2626",
            "AMBIGUOUS": "#7c3aed",
            "UNLOCALIZED": "#64748b",
        }[item.localization_status]
        SubElement(root, "circle", {"cx": str(x), "cy": str(y), "r": "4", "fill": color, "stroke": "white", "stroke-width": "1"})
        label = SubElement(root, "text", {"x": str(x + 6), "y": str(y - 4), "font-size": "7", "fill": color})
        label.text = item.normalized_section
    ElementTree(root).write(str(path), encoding="unicode", xml_declaration=True)
