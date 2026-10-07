"""Test immutable point and rectangle geometry."""

from __future__ import annotations

import math

import pytest

from verdiclip.geometry import Point, Rect


class TestPoint:
    """Point arithmetic and constrained directions."""

    def test_arithmetic(self) -> None:
        """Arithmetic returns independent component-wise values."""
        point = Point(3, 4)

        added, subtracted, scaled = (
            point + Point(2, 1),
            point - Point(2, 1),
            point.scaled(2),
        )

        assert (added, subtracted, scaled) == (Point(5, 5), Point(1, 3), Point(6, 8))
        assert point.distance_to(Point(0, 0)) == 5

    @pytest.mark.parametrize(
        "point",
        [Point(0, 0), Point(8, 1), Point(-4, -3), Point(1, 9)],
        ids=["zero", "horizontal", "diagonal", "vertical"],
    )
    def test_snap(self, point: Point) -> None:
        """Snapping preserves length and produces a multiple of 45 degrees."""
        origin = Point(0, 0)

        snapped = point.snapped_to_45(origin)

        assert snapped.distance_to(origin) == pytest.approx(point.distance_to(origin))
        angle = math.atan2(snapped.y, snapped.x) / (math.pi / 4)
        assert angle == pytest.approx(round(angle))


class TestRect:
    """Rectangle boundaries and transformations."""

    def test_properties(self) -> None:
        """Edges and derived points describe the normalized rectangle."""
        rect = Rect.from_points(Point(8, 10), Point(2, 4))

        edges = rect.left, rect.top, rect.right, rect.bottom

        assert edges == (2, 4, 8, 10)
        assert (rect.top_left, rect.bottom_right, rect.center) == (
            Point(2, 4),
            Point(8, 10),
            Point(5, 7),
        )
        assert not rect.is_empty

    @pytest.mark.parametrize(
        "size",
        [(-1, 2), (2, -1), (-1, -1)],
        ids=["negative-width", "negative-height", "both-negative"],
    )
    def test_negative_size(self, size: tuple[int, int]) -> None:
        """Negative dimensions are rejected."""
        with pytest.raises(ValueError, match="non-negative"):
            Rect(0, 0, *size)

    def test_empty_bounding(self) -> None:
        """Bounding an empty point sequence fails explicitly."""
        with pytest.raises(ValueError, match="empty point list"):
            Rect.bounding([])

    @pytest.mark.parametrize(
        ("other", "overlap"),
        [
            (Rect(5, 5, 10, 10), Rect(5, 5, 5, 5)),
            (Rect(10, 0, 2, 2), Rect(0, 0, 0, 0)),
            (Rect(20, 20, 2, 2), Rect(0, 0, 0, 0)),
        ],
        ids=["overlap", "touch", "outside"],
    )
    def test_intersection(self, other: Rect, overlap: Rect) -> None:
        """Intersection returns only positive shared area."""
        rect = Rect(0, 0, 10, 10)

        result = rect.intersected(other)

        assert result == overlap
        assert rect.intersects(other) == (other.left <= 10 and other.top <= 10)

    @pytest.mark.parametrize(
        "point",
        [Point(-1, 5), Point(5, -1), Point(11, 5), Point(5, 11)],
        ids=["left", "top", "right", "bottom"],
    )
    def test_outside(self, point: Point) -> None:
        """Points beyond any edge are excluded."""
        rect = Rect(0, 0, 10, 10)

        result = rect.contains(point)

        assert not result

    def test_transformations(self) -> None:
        """Translation, margins, union, and outward rounding preserve geometry."""
        rect = Rect(0.2, -0.2, 3.2, 4.2)

        moved = rect.translated(Point(2, 3))

        assert moved == Rect(2.2, 2.8, 3.2, 4.2)
        assert rect.rounded() == Rect(0, -1, 4, 5)
        assert Rect(0, 0, 10, 10).adjusted(2) == Rect(-2, -2, 14, 14)
        assert Rect(0, 0, 1, 1).adjusted(-2).is_empty
        assert Rect(0, 0, 1, 1).united(Rect(3, 4, 2, 2)) == Rect(0, 0, 5, 6)
        assert Rect(0, 0, 1, 1).contains(Point(1, 1))
