"""Standard-library diagnostic SVG export."""

from pathlib import Path
from typing import Union
from xml.etree.ElementTree import Element, ElementTree, SubElement

from .models import GridOrientation, GridSystem


def export_grid_svg(path: Union[str, Path], grid: GridSystem, include_rejected: bool = False) -> None:
    geometry = grid.page_geometry
    x0, y0, _, y1 = geometry.crop_box

    def point(x: float, y: float):
        return x - x0, y1 - y

    root = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {geometry.width:.3f} {geometry.height:.3f}",
            "width": f"{geometry.width:.3f}",
            "height": f"{geometry.height:.3f}",
        },
    )
    SubElement(root, "rect", {"x": "0", "y": "0", "width": str(geometry.width), "height": str(geometry.height), "fill": "white", "stroke": "#222"})
    for axis in grid.horizontal_axes + grid.vertical_axes:
        sx, sy = point(axis.start_x, axis.start_y)
        ex, ey = point(axis.end_x, axis.end_y)
        color = "#2563eb" if axis.orientation == GridOrientation.VERTICAL else "#dc2626"
        SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": color, "stroke-width": "2", "opacity": "0.8"})
        for label in axis.label_candidates:
            lx, ly = point(label.center_x, label.center_y)
            SubElement(root, "circle", {"cx": str(lx), "cy": str(ly), "r": "14", "fill": "none", "stroke": color, "stroke-width": "2"})
            text = SubElement(root, "text", {"x": str(lx), "y": str(ly + 4), "text-anchor": "middle", "font-size": "10", "fill": color})
            text.text = label.normalized_label
    for label in grid.unassigned_labels:
        lx, ly = point(label.center_x, label.center_y)
        SubElement(root, "circle", {"cx": str(lx), "cy": str(ly), "r": "12", "fill": "none", "stroke": "#f59e0b", "stroke-width": "2"})
    if include_rejected:
        for item in grid.rejected_candidates:
            rx, ry = point(item.x, item.y)
            SubElement(root, "path", {"d": f"M {rx-5} {ry-5} L {rx+5} {ry+5} M {rx+5} {ry-5} L {rx-5} {ry+5}", "stroke": "#6b7280", "stroke-width": "1"})
    ElementTree(root).write(str(path), encoding="unicode", xml_declaration=True)
