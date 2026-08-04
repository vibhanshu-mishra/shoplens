"""Explicit conversions into pdf-inspector positioned-text coordinates."""

from typing import Iterable, Tuple

from .models import Box


Matrix = Tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(first: Matrix, second: Matrix) -> Matrix:
    """Concatenate PDF affine matrices using pdf-inspector's convention."""

    a, b, c, d, e, f = first
    g, h, i, j, k, l = second
    return (
        a * g + b * i,
        a * h + b * j,
        c * g + d * i,
        c * h + d * j,
        e * g + f * i + k,
        e * h + f * j + l,
    )


def transform_point(x: float, y: float, matrix: Matrix) -> Tuple[float, float]:
    return (
        x * matrix[0] + y * matrix[2] + matrix[4],
        x * matrix[1] + y * matrix[3] + matrix[5],
    )


def to_positioned_coordinates(x: float, y: float, rotation: int) -> Tuple[float, float]:
    """Match pdf-inspector's whole-page rotation normalization."""

    normalized = rotation % 360
    if normalized == 90:
        return y, -x
    if normalized == 270:
        return -y, x
    if normalized == 180:
        return -x, -y
    return x, y


def transform_box(box: Box, rotation: int) -> Box:
    points = [
        to_positioned_coordinates(box[0], box[1], rotation),
        to_positioned_coordinates(box[0], box[3], rotation),
        to_positioned_coordinates(box[2], box[1], rotation),
        to_positioned_coordinates(box[2], box[3], rotation),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bounds(points: Iterable[Tuple[float, float]]) -> Box:
    values = list(points)
    xs = [point[0] for point in values]
    ys = [point[1] for point in values]
    return min(xs), min(ys), max(xs), max(ys)
