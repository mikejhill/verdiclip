"""Immutable 2-D point and rectangle value objects used across the model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class Point:
    """A point in floating-point image or screen coordinates."""

    x: float
    y: float

    def __add__(self, other: Point) -> Point:
        """Return the component-wise sum."""
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point) -> Point:
        """Return the component-wise difference."""
        return Point(self.x - other.x, self.y - other.y)

    def scaled(self, factor: float) -> Point:
        """Return this point multiplied by ``factor``."""
        return Point(self.x * factor, self.y * factor)

    def distance_to(self, other: Point) -> float:
        """Return the Euclidean distance to ``other``."""
        return math.hypot(self.x - other.x, self.y - other.y)

    def snapped_to_45(self, origin: Point) -> Point:
        """Return this point moved so the line from ``origin`` is a multiple of 45°."""
        delta = self - origin
        length = math.hypot(delta.x, delta.y)
        if length == 0:
            return self
        step = math.pi / 4
        angle = round(math.atan2(delta.y, delta.x) / step) * step
        return Point(
            origin.x + length * math.cos(angle), origin.y + length * math.sin(angle)
        )


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle with non-negative width and height."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Reject negative sizes; use ``from_points`` to normalize."""
        if self.width < 0 or self.height < 0:
            msg = f"Rect size must be non-negative, got {self.width}x{self.height}"
            raise ValueError(msg)

    # Factories

    @classmethod
    def from_points(cls, a: Point, b: Point) -> Self:
        """Return the normalized rectangle spanning two corner points."""
        return cls(min(a.x, b.x), min(a.y, b.y), abs(b.x - a.x), abs(b.y - a.y))

    @classmethod
    def bounding(cls, points: list[Point]) -> Self:
        """Return the smallest rectangle containing every point."""
        if not points:
            msg = "Cannot bound an empty point list"
            raise ValueError(msg)
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return cls(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # Derived values

    @property
    def left(self) -> float:
        """X of the left edge."""
        return self.x

    @property
    def top(self) -> float:
        """Y of the top edge."""
        return self.y

    @property
    def right(self) -> float:
        """X of the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """Y of the bottom edge."""
        return self.y + self.height

    @property
    def top_left(self) -> Point:
        """Top-left corner."""
        return Point(self.x, self.y)

    @property
    def bottom_right(self) -> Point:
        """Bottom-right corner."""
        return Point(self.right, self.bottom)

    @property
    def center(self) -> Point:
        """Center point."""
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    @property
    def is_empty(self) -> bool:
        """True when the rectangle has no area."""
        return self.width <= 0 or self.height <= 0

    # Operations

    def contains(self, point: Point) -> bool:
        """Return True if ``point`` lies inside or on the edge."""
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def intersects(self, other: Rect) -> bool:
        """Return True if the two rectangles overlap or touch."""
        return not (
            other.left > self.right
            or other.right < self.left
            or other.top > self.bottom
            or other.bottom < self.top
        )

    def intersected(self, other: Rect) -> Rect:
        """Return the overlapping area, or an empty rect at the origin if none."""
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return Rect(0, 0, 0, 0)
        return Rect(left, top, right - left, bottom - top)

    def united(self, other: Rect) -> Rect:
        """Return the smallest rectangle containing both."""
        return Rect.bounding(
            [self.top_left, self.bottom_right, other.top_left, other.bottom_right]
        )

    def translated(self, delta: Point) -> Rect:
        """Return this rectangle moved by ``delta``."""
        return Rect(self.x + delta.x, self.y + delta.y, self.width, self.height)

    def adjusted(self, margin: float) -> Rect:
        """Return this rectangle grown by ``margin`` per side (shrunk if negative)."""
        width = max(0.0, self.width + 2 * margin)
        height = max(0.0, self.height + 2 * margin)
        return Rect(self.x - margin, self.y - margin, width, height)

    def rounded(self) -> Rect:
        """Return the rectangle snapped outward to whole pixels."""
        left = math.floor(self.left)
        top = math.floor(self.top)
        return Rect(
            left, top, math.ceil(self.right) - left, math.ceil(self.bottom) - top
        )
