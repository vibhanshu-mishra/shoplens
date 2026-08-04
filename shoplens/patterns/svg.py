"""Geometry-only SVG for neutral linear-pattern review."""

from pathlib import Path
from typing import Sequence, Union
from xml.etree.ElementTree import Element, ElementTree, SubElement

from .models import LinearPattern, LinearPatternType, PatternResult

STYLES = {
    LinearPatternType.REGULAR_SPACING_FIELD: ("#2563eb", ""),
    LinearPatternType.PARALLEL_LINE_GROUP: ("#16a34a", "8 3"),
    LinearPatternType.DOUBLE_LINE_PAIR_GROUP: ("#ea580c", "3 2"),
    LinearPatternType.COLLINEAR_CHAIN_GROUP: ("#7c3aed", "10 3"),
    LinearPatternType.ORTHOGONAL_NETWORK: ("#be123c", "12 4"),
    LinearPatternType.DENSE_LINEAR_FIELD: ("#0f766e", "2 2"),
}


def export_linear_patterns_svg(
    path: Union[str, Path], result: PatternResult, patterns: Sequence[LinearPattern],
    include_unclustered: bool = True,
) -> None:
    geometry = result.page_geometry
    x0, _, _, y1 = geometry.crop_box

    def point(x: float, y: float):
        return x - x0, y1 - y

    root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {geometry.width:.3f} {geometry.height:.3f}"})
    SubElement(root, "rect", {"x": "0", "y": "0", "width": str(geometry.width), "height": str(geometry.height), "fill": "white", "stroke": "#111827"})
    px0, py0 = point(result.plan_region[0], result.plan_region[3])
    px1, py1 = point(result.plan_region[2], result.plan_region[1])
    SubElement(root, "rect", {"x": str(px0), "y": str(py0), "width": str(px1-px0), "height": str(py1-py0), "fill": "none", "stroke": "#64748b", "stroke-width": "2", "stroke-dasharray": "10 6"})
    for axis in result.grid_system.horizontal_axes + result.grid_system.vertical_axes:
        sx, sy = point(axis.start_x, axis.start_y)
        ex, ey = point(axis.end_x, axis.end_y)
        SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": "#cbd5e1", "stroke-width": "1"})
    if include_unclustered:
        for item in result.unclustered_candidates:
            sx, sy = point(item.start_x, item.start_y)
            ex, ey = point(item.end_x, item.end_y)
            SubElement(root, "line", {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": "#9ca3af", "stroke-width": "1", "stroke-dasharray": "2 3", "opacity": "0.65"})
    for pattern in patterns:
        color, dash = STYLES.get(pattern.pattern_type, ("#334155", "6 3"))
        for item in pattern.source_candidates:
            sx, sy = point(item.start_x, item.start_y)
            ex, ey = point(item.end_x, item.end_y)
            attrs = {"x1": str(sx), "y1": str(sy), "x2": str(ex), "y2": str(ey), "stroke": color, "stroke-width": "1.8", "opacity": "0.75"}
            if dash:
                attrs["stroke-dasharray"] = dash
            SubElement(root, "line", attrs)
        bx0, by0 = point(pattern.bounding_box[0], pattern.bounding_box[3])
        bx1, by1 = point(pattern.bounding_box[2], pattern.bounding_box[1])
        attrs = {"x": str(bx0), "y": str(by0), "width": str(max(1, bx1-bx0)), "height": str(max(1, by1-by0)), "fill": "none", "stroke": color, "stroke-width": "2"}
        if dash:
            attrs["stroke-dasharray"] = dash
        SubElement(root, "rect", attrs)
        label = SubElement(root, "text", {"x": str(bx0 + 3), "y": str(by0 + 10), "font-size": "8", "font-weight": "bold", "fill": color})
        label.text = f"{pattern.pattern_id} {pattern.pattern_type.value}"
    ElementTree(root).write(str(path), encoding="unicode", xml_declaration=True)
