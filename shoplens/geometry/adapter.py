"""Geometry extraction adapter with a native-first, permissive fallback."""

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

from shoplens.extraction.page_numbers import (
    to_pdf_inspector_page_indexes,
    to_shoplens_page_number,
)

from .models import Box, LineSegment, PageGeometry, ShapeGeometry
from .transforms import IDENTITY, Matrix, bounds, multiply, to_positioned_coordinates, transform_box, transform_point


def extract_page_geometry(
    path: Union[str, Path], pages: Sequence[int], positioned_text: Optional[Sequence[Any]] = None
) -> List[PageGeometry]:
    """Extract selected one-based pages without changing existing text extraction."""

    try:
        import pdf_inspector
    except ImportError:
        pdf_inspector = None
    if pdf_inspector is not None and hasattr(pdf_inspector, "extract_page_geometry"):
        native = [
            _from_native(value)
            for value in pdf_inspector.extract_page_geometry(
                str(path), pages=to_pdf_inspector_page_indexes(pages)
            )
        ]
        if not any("CURVE_GEOMETRY_NOT_EXPOSED" in item.warnings for item in native):
            return native
    return _extract_with_pypdf(path, pages, positioned_text)


def _from_native(value: Any) -> PageGeometry:
    lines = [
        LineSegment(
            to_shoplens_page_number(item.page), item.x1, item.y1, item.x2, item.y2,
            source="pdf_inspector",
        )
        for item in value.lines
    ]
    shapes = [
        ShapeGeometry(
            to_shoplens_page_number(item.page),
            "RECTANGLE",
            _normalized_rectangle(item.x, item.y, item.width, item.height),
            "pdf_inspector_re",
        )
        for item in value.rectangles
    ]
    return PageGeometry(
        pdf_page=to_shoplens_page_number(value.page),
        width=value.width,
        height=value.height,
        rotation=value.rotation,
        media_box=tuple(value.media_box),
        crop_box=tuple(value.crop_box),
        coordinate_system=value.coordinate_system,
        provider="pdf_inspector",
        lines=lines,
        shapes=shapes,
        warnings=list(value.warnings),
        conversion="native shared coordinate system",
    )


def _extract_with_pypdf(
    path: Union[str, Path], pages: Sequence[int], positioned_text: Optional[Sequence[Any]]
) -> List[PageGeometry]:
    try:
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
    except ImportError as exc:
        raise RuntimeError(
            "Grid geometry requires a rebuilt pdf-inspector geometry binding or pypdf>=5,<6."
        ) from exc

    reader = PdfReader(str(path))
    results = []
    for page_number in pages:
        if page_number < 1 or page_number > len(reader.pages):
            raise ValueError(f"PDF page {page_number} is outside 1-{len(reader.pages)}")
        page = reader.pages[page_number - 1]
        media = _box(page.mediabox)
        crop = _box(page.cropbox)
        rotation = int(page.rotation or 0) % 360
        stream = ContentStream(page.get_contents(), reader)
        page_items = [
            item for item in (positioned_text or []) if int(item.page) == page_number
        ]
        coordinate_rotation = _coordinate_rotation_from_anchors(page_items, crop)
        if coordinate_rotation is None:
            coordinate_rotation = _coordinate_rotation(stream.operations)
        lines, shapes = _parse_operations(stream.operations, page_number, coordinate_rotation)
        transformed_media = transform_box(media, coordinate_rotation)
        transformed_crop = transform_box(crop, coordinate_rotation)
        results.append(
            PageGeometry(
                pdf_page=page_number,
                width=transformed_crop[2] - transformed_crop[0],
                height=transformed_crop[3] - transformed_crop[1],
                rotation=rotation,
                media_box=transformed_media,
                crop_box=transformed_crop,
                coordinate_system="PDF_INSPECTOR_POSITIONED_BOTTOM_LEFT",
                provider="pypdf",
                lines=lines,
                shapes=shapes,
                warnings=["GEOMETRY_FALLBACK_PYPDF", "FORM_XOBJECT_GEOMETRY_NOT_EXPANDED"],
                conversion=(
                    f"raw PDF user space -> CTM -> inferred coordinate rotation {coordinate_rotation} "
                    "using pdf-inspector whole-page convention"
                ),
            )
        )
    return results


def _coordinate_rotation_from_anchors(items: Sequence[Any], crop: Box) -> Optional[int]:
    """Choose the transform whose page box contains the positioned-text anchors."""

    if not items:
        return None
    candidates = (0, 90, 180, 270)
    scores = {}
    for rotation in candidates:
        candidate_box = transform_box(crop, rotation)
        scores[rotation] = sum(
            candidate_box[0] - 6 <= float(item.x) <= candidate_box[2] + 6
            and candidate_box[1] - 6 <= float(item.y) <= candidate_box[3] + 6
            for item in items
        )
    ranked = sorted(scores.items(), key=lambda value: (-value[1], value[0]))
    return ranked[0][0]


def _coordinate_rotation(operations) -> int:
    """Mirror pdf-inspector's dominant transformed-text direction decision."""

    ctm: Matrix = IDENTITY
    stack: List[Matrix] = []
    horizontal = 0
    rotated = 0
    for operands, operator_bytes in operations:
        operator = operator_bytes.decode("latin-1")
        if operator == "q":
            stack.append(ctm)
        elif operator == "Q" and stack:
            ctm = stack.pop()
        elif operator == "cm" and len(operands) >= 6:
            matrix = _matrix(operands)
            ctm = multiply(matrix, ctm)
        elif operator == "Tm" and len(operands) >= 6:
            matrix = _matrix(operands)
            combined = multiply(matrix, ctm)
            if abs(combined[0]) > abs(combined[1]):
                horizontal += 1
            else:
                rotated += 1
    total = horizontal + rotated
    return 90 if total and rotated * 3 >= total * 2 else 0


def _parse_operations(operations, page: int, rotation: int):
    ctm: Matrix = IDENTITY
    width = 1.0
    dash: Tuple[float, ...] = ()
    stack = []
    current = None
    start = None
    paths = []
    curves = []
    rectangles = []
    output_lines: List[LineSegment] = []
    output_shapes: List[ShapeGeometry] = []

    for operands, operator_bytes in operations:
        operator = operator_bytes.decode("latin-1")
        numbers = [_number(value) for value in operands]
        if operator == "q":
            stack.append((ctm, width, dash))
        elif operator == "Q" and stack:
            ctm, width, dash = stack.pop()
        elif operator == "cm" and len(numbers) >= 6:
            ctm = multiply(_matrix(numbers), ctm)
        elif operator == "w" and numbers:
            width = numbers[0]
        elif operator == "d" and operands:
            dash = tuple(_number(value) for value in operands[0])
        elif operator == "m" and len(numbers) >= 2:
            current = (numbers[0], numbers[1])
            start = current
        elif operator == "l" and len(numbers) >= 2 and current is not None:
            endpoint = (numbers[0], numbers[1])
            paths.append((current, endpoint, ctm, width, dash))
            current = endpoint
        elif operator == "h" and current is not None and start is not None:
            paths.append((current, start, ctm, width, dash))
            current = start
        elif operator == "re" and len(numbers) >= 4:
            x, y, rect_width, rect_height = numbers[:4]
            points = [
                transform_point(x, y, ctm),
                transform_point(x + rect_width, y, ctm),
                transform_point(x + rect_width, y + rect_height, ctm),
                transform_point(x, y + rect_height, ctm),
            ]
            rectangles.append(points)
        elif operator in {"c", "v", "y"} and current is not None:
            points = [current]
            values = list(zip(numbers[::2], numbers[1::2]))
            points.extend(values)
            endpoint = values[-1] if values else current
            curves.append(([transform_point(x, y, ctm) for x, y in points], width))
            current = endpoint
        elif operator in {"S", "s", "B", "B*", "b", "b*"}:
            if operator in {"s", "b", "b*"} and current is not None and start is not None:
                paths.append((current, start, ctm, width, dash))
            for first, second, matrix, line_width, line_dash in paths:
                p1 = transform_point(first[0], first[1], matrix)
                p2 = transform_point(second[0], second[1], matrix)
                x1, y1 = to_positioned_coordinates(*p1, rotation)
                x2, y2 = to_positioned_coordinates(*p2, rotation)
                output_lines.append(LineSegment(page, x1, y1, x2, y2, line_width, line_dash, "pypdf_path"))
            if curves:
                curve_points = [
                    to_positioned_coordinates(x, y, rotation)
                    for values, _ in curves
                    for x, y in values
                ]
                curve_bounds = bounds(curve_points)
                output_shapes.append(
                    ShapeGeometry(page, _curve_kind(curve_bounds), curve_bounds, "pypdf_bezier")
                )
            for points in rectangles:
                converted = [to_positioned_coordinates(x, y, rotation) for x, y in points]
                output_shapes.append(ShapeGeometry(page, "RECTANGLE", bounds(converted), "pypdf_re"))
            paths, curves, rectangles = [], [], []
            current = start = None
        elif operator in {"f", "F", "f*"}:
            if curves:
                curve_points = [
                    to_positioned_coordinates(x, y, rotation)
                    for values, _ in curves
                    for x, y in values
                ]
                curve_bounds = bounds(curve_points)
                output_shapes.append(ShapeGeometry(page, _curve_kind(curve_bounds), curve_bounds, "pypdf_bezier"))
            for points in rectangles:
                converted = [to_positioned_coordinates(x, y, rotation) for x, y in points]
                output_shapes.append(ShapeGeometry(page, "RECTANGLE", bounds(converted), "pypdf_re"))
            paths, curves, rectangles = [], [], []
            current = start = None
        elif operator == "n":
            paths, curves, rectangles = [], [], []
            current = start = None
    return _deduplicate_lines(output_lines), output_shapes


def _deduplicate_lines(lines: List[LineSegment]) -> List[LineSegment]:
    seen = set()
    result = []
    for line in lines:
        endpoints = sorted(((round(line.x1, 2), round(line.y1, 2)), (round(line.x2, 2), round(line.y2, 2))))
        key = tuple(endpoints)
        if key not in seen:
            seen.add(key)
            result.append(line)
    return result


def _curve_kind(value: Box) -> str:
    width = value[2] - value[0]
    height = value[3] - value[1]
    return "ELLIPSE" if height and 0.75 <= width / height <= 1.33 else "CURVE"


def _box(value) -> Box:
    return float(value.left), float(value.bottom), float(value.right), float(value.top)


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _matrix(values) -> Matrix:
    numbers = [_number(value) for value in values[:6]]
    return numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5]


def _normalized_rectangle(x: float, y: float, width: float, height: float) -> Box:
    return min(x, x + width), min(y, y + height), max(x, x + width), max(y, y + height)
