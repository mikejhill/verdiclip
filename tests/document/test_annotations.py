"""Test annotation geometry through immutable public operations."""

from __future__ import annotations

import pytest

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    BoxAnnotation,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    Geometry,
    HandleRole,
    HighlightShape,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.style import RED, Style
from verdiclip.geometry import Point, Rect


@pytest.fixture
def annotations() -> list[Annotation]:
    """Return one of every serializable annotation kind."""
    rect = Rect(10, 20, 40, 30)
    return [
        RectangleShape(rect=rect),
        EllipseShape(rect=rect),
        HighlightShape(rect=rect),
        ObfuscateShape(rect=rect),
        LineShape(start=Point(10, 20), end=Point(50, 50)),
        ArrowShape(start=Point(10, 20), end=Point(50, 50)),
        FreehandShape(points=(Point(10, 20), Point(50, 50))),
        TextNote(rect=rect, text="Note"),
        CounterMarker(center=Point(30, 35), radius=15, label="1"),
    ]


class TestAnnotation:
    """Shared immutable annotation operations."""

    @pytest.mark.parametrize(
        "index",
        range(9),
        ids=[
            "rectangle",
            "ellipse",
            "highlight",
            "obfuscate",
            "line",
            "arrow",
            "freehand",
            "text",
            "counter",
        ],
    )
    def test_translation_and_identity(
        self, annotations: list[Annotation], index: int
    ) -> None:
        """Every annotation translates without mutating its original identity."""
        delta = Point(7, 9)
        original = annotations[index]

        translated = original.translated(delta)

        assert translated.bounds == original.bounds.translated(delta)
        assert translated.id == original.id
        assert original.with_new_id().id != original.id
        assert original.with_style(Style(width=8)).style.width == 8
        assert original.geometry_json()


class TestBoxAnnotation:
    """Box handles and resizing."""

    @pytest.mark.parametrize(
        "role", list(HandleRole)[:8], ids=[r.value for r in list(HandleRole)[:8]]
    )
    def test_resize_handles(self, role: HandleRole) -> None:
        """Each box handle moves its corresponding edges."""
        shape = HighlightShape(rect=Rect(10, 20, 40, 30))

        resized = shape.with_handle_moved(role, Point(5, 6), constrain=False)

        assert resized.rect == Geometry.resize_box(
            shape.rect, role, Point(5, 6), constrain=False
        )
        assert len(shape.handles()) == 8
        assert resized.id == shape.id

    @pytest.mark.parametrize(
        "role", list(Geometry.CORNERS), ids=[r.value for r in Geometry.CORNERS]
    )
    def test_constrained_corners(self, role: HandleRole) -> None:
        """Constrained corners keep the opposite corner and make a square."""
        shape = BoxAnnotation(rect=Rect(10, 20, 40, 30))

        resized = shape.with_handle_moved(role, Point(-10, -20), constrain=True)

        assert resized.rect.width == resized.rect.height
        assert resized.rect.contains(Geometry.opposite_corner(shape.rect, role))
        assert shape.with_rect(Rect(0, 0, 5, 6)).rect == Rect(0, 0, 5, 6)
        assert shape.hit_test(Point(20, 30), 0)
        assert shape.bounds == Rect(8.5, 18.5, 43, 33)


class TestRectangleShape:
    """Rectangle outline and filled hit targets."""

    @pytest.mark.parametrize(
        ("point", "filled", "expected"),
        [
            (Point(10, 25), False, True),
            (Point(30, 30), False, False),
            (Point(30, 30), True, True),
            (Point(100, 100), True, False),
        ],
        ids=["edge", "empty-center", "filled-center", "outside"],
    )
    def test_hit(self, point: Point, *, filled: bool, expected: bool) -> None:
        """Only filled rectangles accept interior hits."""
        style = Style(fill=RED) if filled else Style()
        shape = RectangleShape(rect=Rect(10, 20, 40, 30), style=style)

        hit = shape.hit_test(point, 0)

        assert hit is expected


class TestEllipseShape:
    """Ellipse hit geometry."""

    @pytest.mark.parametrize(
        ("rect", "point", "style", "expected"),
        [
            (Rect(10, 20, 40, 30), Point(10, 35), Style(), True),
            (Rect(10, 20, 40, 30), Point(30, 35), Style(), False),
            (Rect(10, 20, 40, 30), Point(30, 35), Style(fill=RED), True),
            (Rect(10, 20, 40, 30), Point(0, 0), Style(), False),
            (Rect(10, 20, 1, 1), Point(10.5, 20.5), Style(width=5), True),
        ],
        ids=["edge", "empty-center", "filled-center", "outside", "tiny"],
    )
    def test_hit(
        self, rect: Rect, point: Point, style: Style, *, expected: bool
    ) -> None:
        """Ellipse hits account for fill, outline thickness, and tiny radii."""
        shape = EllipseShape(rect=rect, style=style)

        hit = shape.hit_test(point, 0)

        assert hit is expected


class TestLineShape:
    """Segment targets and endpoint handles."""

    @pytest.mark.parametrize(
        "role",
        [HandleRole.START, HandleRole.END, HandleRole.TOP],
        ids=["start", "end", "irrelevant"],
    )
    def test_handles(self, role: HandleRole) -> None:
        """Only endpoint handles change line geometry."""
        shape = LineShape(start=Point(0, 0), end=Point(10, 0))

        moved = shape.with_handle_moved(role, Point(20, 9), constrain=True)

        assert len(shape.handles()) == 2
        assert (moved == shape) is (role is HandleRole.TOP)
        assert shape.hit_test(Point(5, 1), 0)
        assert not shape.hit_test(Point(5, 20), 0)

    def test_unconstrained_handles(self) -> None:
        """Unconstrained endpoint moves preserve the exact requested point."""
        shape = LineShape(start=Point(0, 0), end=Point(10, 0))

        start = shape.with_handle_moved(HandleRole.START, Point(1, 2), constrain=False)
        end = shape.with_handle_moved(HandleRole.END, Point(3, 4), constrain=False)

        assert start.start == Point(1, 2)
        assert end.end == Point(3, 4)


class TestGeometry:
    """Distance and square helpers."""

    @pytest.mark.parametrize(
        ("point", "end", "distance"),
        [
            (Point(3, 4), Point(0, 0), 5),
            (Point(-3, 4), Point(10, 0), 5),
            (Point(13, 4), Point(10, 0), 5),
            (Point(5, 4), Point(10, 0), 4),
        ],
        ids=["zero-segment", "before", "after", "middle"],
    )
    def test_segment_distance(self, point: Point, end: Point, distance: float) -> None:
        """Segment distance clamps projection to both endpoints."""
        start = Point(0, 0)

        result = Geometry.distance_to_segment(point, start, end)

        assert result == distance


class TestArrowShape:
    """Arrow head and shaft geometry."""

    def test_head(self) -> None:
        """The head ends at the tip and the shaft ends at its base."""
        shape = ArrowShape(start=Point(0, 0), end=Point(100, 0))

        tip, left, right = shape.head_polygon()

        assert tip == shape.end
        assert left.x == right.x == 88
        assert shape.shaft_end() == Point(88, 0)
        assert shape.head_length == 12


class TestFreehandShape:
    """Freehand targets and geometry."""

    def test_empty(self) -> None:
        """Empty freehand strokes are rejected."""
        with pytest.raises(ValueError, match="at least one point"):
            FreehandShape(points=())

    @pytest.mark.parametrize(
        "points",
        [
            (Point(0, 0),),
            (Point(0, 0), Point(10, 0)),
            (Point(0, 0), Point(10, 0), Point(10, 10)),
        ],
        ids=["dot", "segment", "polyline"],
    )
    def test_targets(self, points: tuple[Point, ...]) -> None:
        """Freehand strokes hit their path and expose no resize handles."""
        shape = FreehandShape(points=points)

        hit = shape.hit_test(Point(0, 0), 0)

        assert hit
        assert not shape.hit_test(Point(100, 100), 0)
        assert shape.handles() == {}
        assert (
            shape.with_handle_moved(HandleRole.END, Point(5, 5), constrain=False)
            is shape
        )


class TestTextNote:
    """Text content and immutable geometry."""

    def test_edit(self) -> None:
        """Editing text installs the measured box without resize handles."""
        note = TextNote(rect=Rect(0, 0, 20, 20), text="old")

        edited = note.with_text("new", Rect(1, 2, 30, 40))

        assert edited.text == "new"
        assert edited.bounds == Rect(1, 2, 30, 40)
        assert note.hit_test(Point(10, 10), 0)
        assert not note.hit_test(Point(50, 50), 0)
        assert note.handles() == {}
        assert (
            note.with_handle_moved(HandleRole.END, Point(1, 1), constrain=False) is note
        )


class TestCounterMarker:
    """Counter circular geometry and labels."""

    def test_resize_and_relabel(self) -> None:
        """Counters resize around a fixed center and accept arbitrary labels."""
        marker = CounterMarker(center=Point(20, 20), radius=10, label="1")

        resized = marker.with_handle_moved(
            HandleRole.BOTTOM_RIGHT, Point(40, 50), constrain=False
        )

        assert resized.center == marker.center
        assert resized.radius == 30
        assert len(marker.handles()) == 4
        assert marker.with_label("A").label == "A"
        assert marker.hit_test(Point(20, 20), 0)
        assert not marker.hit_test(Point(40, 40), 0)


class TestObfuscateShape:
    """Obfuscation block sizes."""

    @pytest.mark.parametrize(
        ("width", "expected"),
        [(1, 2), (3.4, 3), (12, 12)],
        ids=["minimum", "rounded", "normal"],
    )
    def test_block_size(self, width: float, expected: int) -> None:
        """Pixel blocks are rounded and never smaller than two pixels."""
        shape = ObfuscateShape(rect=Rect(0, 0, 10, 10), style=Style(width=width))

        size = shape.block_size

        assert size == expected
