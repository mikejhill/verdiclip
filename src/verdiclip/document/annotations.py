"""Immutable annotation values: geometry, handles, and hit testing.

Each annotation type owns everything that depends on its shape, so adding a
type means adding one class here plus its drawing in the renderer.
"""

from __future__ import annotations

import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import ClassVar, Final, Self, override

from verdiclip.document.style import Style
from verdiclip.geometry import Point, Rect

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]

MIN_COUNTER_RADIUS: Final = 8.0


class AnnotationKind(StrEnum):
    """Every annotation type the editor can create."""

    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    TEXT = "text"
    COUNTER = "counter"
    HIGHLIGHT = "highlight"
    OBFUSCATE = "obfuscate"
    FREEHAND = "freehand"


class HandleRole(StrEnum):
    """A draggable handle on a selected annotation."""

    TOP_LEFT = "top_left"
    TOP = "top"
    TOP_RIGHT = "top_right"
    RIGHT = "right"
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM = "bottom"
    BOTTOM_LEFT = "bottom_left"
    LEFT = "left"
    START = "start"
    END = "end"


@dataclass(frozen=True, slots=True, kw_only=True)
class Annotation(ABC):
    """Base class for all annotations; coordinates are original-image pixels."""

    kind: ClassVar[AnnotationKind]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    style: Style = field(default_factory=Style)

    @property
    @abstractmethod
    def bounds(self) -> Rect:
        """Return the visual bounds, including stroke width."""

    @abstractmethod
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True if ``point`` is on the annotation (within ``tolerance``)."""

    @abstractmethod
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""

    @abstractmethod
    def handles(self) -> dict[HandleRole, Point]:
        """Return the handle positions shown when selected."""

    @abstractmethod
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return a copy with handle ``role`` dragged to ``point``."""

    @abstractmethod
    def geometry_json(self) -> JsonObject:
        """Return the type-specific fields for encoding."""

    def with_style(self, style: Style) -> Self:
        """Return a copy drawn with ``style``."""
        return replace(self, style=style)

    def with_new_id(self) -> Self:
        """Return a copy with a fresh identity (for paste/duplicate)."""
        return replace(self, id=uuid.uuid4().hex)


# Shared geometry helpers


class Geometry:
    """Stateless geometry helpers shared by annotation types."""

    @staticmethod
    def distance_to_segment(point: Point, start: Point, end: Point) -> float:
        """Return the distance from ``point`` to the segment ``start``-``end``."""
        dx = end.x - start.x
        dy = end.y - start.y
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return point.distance_to(start)
        t = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        return point.distance_to(Point(start.x + t * dx, start.y + t * dy))

    @staticmethod
    def box_handles(rect: Rect) -> dict[HandleRole, Point]:
        """Return the eight edge and corner handles of ``rect``."""
        c = rect.center
        return {
            HandleRole.TOP_LEFT: rect.top_left,
            HandleRole.TOP: Point(c.x, rect.top),
            HandleRole.TOP_RIGHT: Point(rect.right, rect.top),
            HandleRole.RIGHT: Point(rect.right, c.y),
            HandleRole.BOTTOM_RIGHT: rect.bottom_right,
            HandleRole.BOTTOM: Point(c.x, rect.bottom),
            HandleRole.BOTTOM_LEFT: Point(rect.left, rect.bottom),
            HandleRole.LEFT: Point(rect.left, c.y),
        }

    @staticmethod
    def resize_box(
        rect: Rect, role: HandleRole, point: Point, *, constrain: bool
    ) -> Rect:
        """Return ``rect`` with the edge or corner ``role`` dragged to ``point``."""
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if role in (HandleRole.TOP_LEFT, HandleRole.LEFT, HandleRole.BOTTOM_LEFT):
            left = point.x
        if role in (HandleRole.TOP_RIGHT, HandleRole.RIGHT, HandleRole.BOTTOM_RIGHT):
            right = point.x
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP, HandleRole.TOP_RIGHT):
            top = point.y
        if role in (HandleRole.BOTTOM_LEFT, HandleRole.BOTTOM, HandleRole.BOTTOM_RIGHT):
            bottom = point.y
        resized = Rect.from_points(Point(left, top), Point(right, bottom))
        if constrain and role in Geometry.CORNERS:
            anchor = Geometry.opposite_corner(rect, role)
            return Geometry.square_from(anchor, point)
        return resized

    CORNERS: ClassVar[frozenset[HandleRole]] = frozenset(
        {
            HandleRole.TOP_LEFT,
            HandleRole.TOP_RIGHT,
            HandleRole.BOTTOM_LEFT,
            HandleRole.BOTTOM_RIGHT,
        }
    )

    @staticmethod
    def opposite_corner(rect: Rect, role: HandleRole) -> Point:
        """Return the corner diagonally opposite ``role``."""
        opposite = {
            HandleRole.TOP_LEFT: rect.bottom_right,
            HandleRole.TOP_RIGHT: Point(rect.left, rect.bottom),
            HandleRole.BOTTOM_LEFT: Point(rect.right, rect.top),
            HandleRole.BOTTOM_RIGHT: rect.top_left,
        }
        return opposite[role]

    @staticmethod
    def square_from(anchor: Point, point: Point) -> Rect:
        """Return the square cornered at ``anchor`` extending toward ``point``."""
        size = max(abs(point.x - anchor.x), abs(point.y - anchor.y))
        x = anchor.x - size if point.x < anchor.x else anchor.x
        y = anchor.y - size if point.y < anchor.y else anchor.y
        return Rect(x, y, size, size)


# Box-shaped annotations


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxAnnotation(Annotation):
    """An annotation defined by an axis-aligned rectangle."""

    rect: Rect

    @override
    @property
    def bounds(self) -> Rect:
        """Return the rectangle grown by half the stroke."""
        return self.rect.adjusted(self.style.width / 2)

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True inside the area (box annotations are solid targets)."""
        return self.rect.adjusted(tolerance).contains(point)

    @override
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""
        return replace(self, rect=self.rect.translated(delta))

    @override
    def handles(self) -> dict[HandleRole, Point]:
        """Return the eight box handles."""
        return Geometry.box_handles(self.rect)

    @override
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return a copy resized from ``role``."""
        return replace(
            self, rect=Geometry.resize_box(self.rect, role, point, constrain=constrain)
        )

    def with_rect(self, rect: Rect) -> Self:
        """Return a copy occupying ``rect``."""
        return replace(self, rect=rect)

    @override
    def geometry_json(self) -> JsonObject:
        """Return the rectangle as JSON."""
        r = self.rect
        return {"rect": [r.x, r.y, r.width, r.height]}


@dataclass(frozen=True, slots=True, kw_only=True)
class LabeledBox(BoxAnnotation):
    """A box that can hold centered text, so it works as a label."""

    text: str = ""

    @property
    def is_solid(self) -> bool:
        """True when clicking inside should select it (filled or labeled)."""
        return not self.style.fill.is_transparent or bool(self.text)

    def with_text(self, text: str) -> Self:
        """Return a copy showing ``text``."""
        return replace(self, text=text)

    @override
    def geometry_json(self) -> JsonObject:
        """Return the rectangle and text as JSON."""
        r = self.rect
        return {"rect": [r.x, r.y, r.width, r.height], "text": self.text}


@dataclass(frozen=True, slots=True, kw_only=True)
class RectangleShape(LabeledBox):
    """A rectangle outline with optional fill and label text."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.RECTANGLE

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True on the outline, or anywhere inside when solid."""
        grow = self.style.width / 2 + tolerance
        if not self.rect.adjusted(grow).contains(point):
            return False
        if self.is_solid:
            return True
        return not self.rect.adjusted(-grow).contains(point)


@dataclass(frozen=True, slots=True, kw_only=True)
class EllipseShape(LabeledBox):
    """An ellipse inscribed in its rectangle, with optional label text."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.ELLIPSE

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True on the outline, or anywhere inside when solid."""
        grow = self.style.width / 2 + tolerance
        outer = self._normalized_distance(point, grow)
        if outer > 1:
            return False
        if self.is_solid:
            return True
        return self._normalized_distance(point, -grow) >= 1

    def _normalized_distance(self, point: Point, grow: float) -> float:
        """Return the ellipse equation value at ``point`` with radii grown."""
        c = self.rect.center
        rx = self.rect.width / 2 + grow
        ry = self.rect.height / 2 + grow
        if rx <= 0 or ry <= 0:
            return math.inf
        return ((point.x - c.x) / rx) ** 2 + ((point.y - c.y) / ry) ** 2


@dataclass(frozen=True, slots=True, kw_only=True)
class HighlightShape(BoxAnnotation):
    """A translucent marker rectangle multiplied onto the image."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.HIGHLIGHT

    @override
    @property
    def bounds(self) -> Rect:
        """Return the rectangle (highlights have no stroke)."""
        return self.rect


@dataclass(frozen=True, slots=True, kw_only=True)
class ObfuscateShape(BoxAnnotation):
    """A region pixelated from the image beneath it; ``style.width`` is block size."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.OBFUSCATE

    @override
    @property
    def bounds(self) -> Rect:
        """Return the rectangle (obfuscation has no stroke)."""
        return self.rect

    @property
    def block_size(self) -> int:
        """Return the pixelation block size in image pixels."""
        return max(2, round(self.style.width))


# Line-shaped annotations


@dataclass(frozen=True, slots=True, kw_only=True)
class LineShape(Annotation):
    """A straight line segment."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.LINE
    start: Point
    end: Point

    @override
    @property
    def bounds(self) -> Rect:
        """Return the segment bounds grown by the stroke."""
        return Rect.bounding([self.start, self.end]).adjusted(self.style.width)

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True within half the stroke plus ``tolerance`` of the segment."""
        reach = self.style.width / 2 + tolerance
        return Geometry.distance_to_segment(point, self.start, self.end) <= reach

    @override
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""
        return replace(self, start=self.start + delta, end=self.end + delta)

    @override
    def handles(self) -> dict[HandleRole, Point]:
        """Return the two endpoint handles."""
        return {HandleRole.START: self.start, HandleRole.END: self.end}

    @override
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return a copy with one endpoint moved, snapping to 45° if constrained."""
        if role is HandleRole.START:
            moved = point.snapped_to_45(self.end) if constrain else point
            return replace(self, start=moved)
        if role is HandleRole.END:
            moved = point.snapped_to_45(self.start) if constrain else point
            return replace(self, end=moved)
        return self

    @override
    def geometry_json(self) -> JsonObject:
        """Return the endpoints as JSON."""
        return {"start": [self.start.x, self.start.y], "end": [self.end.x, self.end.y]}


@dataclass(frozen=True, slots=True, kw_only=True)
class ArrowShape(LineShape):
    """A line with a pointed head at ``end``."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.ARROW

    @property
    def head_length(self) -> float:
        """Return the arrowhead length, proportional to the stroke width."""
        return max(10.0, self.style.width * 4)

    @override
    @property
    def bounds(self) -> Rect:
        """Return the segment bounds grown to include the head."""
        return Rect.bounding([self.start, self.end]).adjusted(self.head_length)

    def head_polygon(self) -> tuple[Point, Point, Point]:
        """Return the head triangle: tip, then the two base corners."""
        angle = math.atan2(self.end.y - self.start.y, self.end.x - self.start.x)
        length = self.head_length
        half_width = length * 0.45
        base = Point(
            self.end.x - length * math.cos(angle), self.end.y - length * math.sin(angle)
        )
        normal = Point(-math.sin(angle) * half_width, math.cos(angle) * half_width)
        return (self.end, base + normal, base - normal)

    def shaft_end(self) -> Point:
        """Return where the shaft stops so it never covers the tip."""
        _, left, right = self.head_polygon()
        return Point((left.x + right.x) / 2, (left.y + right.y) / 2)


@dataclass(frozen=True, slots=True, kw_only=True)
class FreehandShape(Annotation):
    """A smooth pen stroke through a sequence of points."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.FREEHAND
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        """Require at least one point."""
        if not self.points:
            msg = "A freehand stroke needs at least one point"
            raise ValueError(msg)

    @override
    @property
    def bounds(self) -> Rect:
        """Return the point bounds grown by the stroke."""
        return Rect.bounding(list(self.points)).adjusted(self.style.width)

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True within reach of any segment."""
        reach = self.style.width / 2 + tolerance
        if len(self.points) == 1:
            return point.distance_to(self.points[0]) <= reach
        return any(
            Geometry.distance_to_segment(point, a, b) <= reach
            for a, b in zip(self.points, self.points[1:], strict=False)
        )

    @override
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""
        return replace(self, points=tuple(p + delta for p in self.points))

    @override
    def handles(self) -> dict[HandleRole, Point]:
        """Return no handles; freehand strokes only move."""
        return {}

    @override
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return self; freehand strokes have no handles."""
        del role, point, constrain
        return self

    @override
    def geometry_json(self) -> JsonObject:
        """Return the points as JSON."""
        return {"points": [[p.x, p.y] for p in self.points]}


# Text-bearing annotations


@dataclass(frozen=True, slots=True, kw_only=True)
class TextNote(Annotation):
    """Text in a box; ``rect`` is measured by the renderer when text changes."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.TEXT
    rect: Rect
    text: str

    @override
    @property
    def bounds(self) -> Rect:
        """Return the text box."""
        return self.rect

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True anywhere in the text box."""
        return self.rect.adjusted(tolerance).contains(point)

    @override
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""
        return replace(self, rect=self.rect.translated(delta))

    @override
    def handles(self) -> dict[HandleRole, Point]:
        """Return no handles; text is sized by its font."""
        return {}

    @override
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return self; text has no handles."""
        del role, point, constrain
        return self

    def with_text(self, text: str, rect: Rect) -> Self:
        """Return a copy with new text and its measured box."""
        return replace(self, text=text, rect=rect)

    @override
    def geometry_json(self) -> JsonObject:
        """Return the box and text as JSON."""
        r = self.rect
        return {"rect": [r.x, r.y, r.width, r.height], "text": self.text}


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterMarker(Annotation):
    """A filled circle with a centered label; fill is the circle, stroke the label."""

    kind: ClassVar[AnnotationKind] = AnnotationKind.COUNTER
    center: Point
    radius: float
    label: str

    @property
    def rect(self) -> Rect:
        """Return the circle's bounding square."""
        r = self.radius
        return Rect(self.center.x - r, self.center.y - r, 2 * r, 2 * r)

    @override
    @property
    def bounds(self) -> Rect:
        """Return the circle's bounding square."""
        return self.rect

    @override
    def hit_test(self, point: Point, tolerance: float) -> bool:
        """Return True inside the circle."""
        return point.distance_to(self.center) <= self.radius + tolerance

    @override
    def translated(self, delta: Point) -> Self:
        """Return a copy moved by ``delta``."""
        return replace(self, center=self.center + delta)

    @override
    def handles(self) -> dict[HandleRole, Point]:
        """Return the four corner handles."""
        return {
            role: pos
            for role, pos in Geometry.box_handles(self.rect).items()
            if role in Geometry.CORNERS
        }

    @override
    def with_handle_moved(
        self, role: HandleRole, point: Point, *, constrain: bool
    ) -> Self:
        """Return a copy resized uniformly, keeping the center fixed."""
        del role, constrain
        radius = max(
            MIN_COUNTER_RADIUS,
            abs(point.x - self.center.x),
            abs(point.y - self.center.y),
        )
        return replace(self, radius=radius)

    def with_label(self, label: str) -> Self:
        """Return a copy showing ``label``."""
        return replace(self, label=label)

    @override
    def geometry_json(self) -> JsonObject:
        """Return center, radius, and label as JSON."""
        return {
            "center": [self.center.x, self.center.y],
            "radius": self.radius,
            "label": self.label,
        }
