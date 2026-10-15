"""Property-based tests: invariants that must hold for every input, not examples.

Hypothesis generates many inputs per test and shrinks any failure to the
smallest case, so these catch the edge cases hand-picked examples miss.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    HighlightShape,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.codec import AnnotationCodec, StyleCodec
from verdiclip.document.style import Color, Style
from verdiclip.geometry import Affine, Point, Rect
from verdiclip.settings import (
    AppearanceSettings,
    CaptureSettings,
    EditorSettings,
    HotkeySettings,
    ImageFormat,
    IntegrationSettings,
    OutputSettings,
    Settings,
    SettingsCodec,
    StartupSettings,
    Theme,
)

# Screen and image coordinates: whole pixels, as captures produce them
coords = st.integers(-10_000, 10_000)
sizes = st.integers(0, 10_000)
points = st.builds(Point, coords, coords)
rects = st.builds(Rect, coords, coords, sizes, sizes)
# Fractional values for code that must handle sub-pixel input
reals = st.floats(-10_000, 10_000, allow_nan=False)
real_points = st.builds(Point, reals, reals)
real_rects = st.builds(Rect, reals, reals, st.floats(0, 10_000), st.floats(0, 10_000))
channels = st.integers(0, 255)
colors = st.builds(Color, channels, channels, channels, channels)
positive = st.floats(0.01, 1_000, allow_nan=False)
text = st.text(max_size=40)


class TestRectProperties:
    """Rectangle operations agree with their set-of-points meaning."""

    @given(points, points)
    def test_from_points_spans_both_corners(self, a: Point, b: Point) -> None:
        """The normalized rectangle contains both points and has them as corners."""
        rect = Rect.from_points(a, b)

        assert rect.contains(a)
        assert rect.contains(b)
        assert rect == Rect.from_points(b, a)
        assert {rect.left, rect.right} == {a.x, b.x}
        assert {rect.top, rect.bottom} == {a.y, b.y}

    @given(rects, rects)
    def test_intersection_lies_in_both(self, a: Rect, b: Rect) -> None:
        """A non-empty intersection is inside both rectangles, in either order."""
        overlap = a.intersected(b)

        assert overlap == b.intersected(a)
        if not overlap.is_empty:
            assert a.intersects(b)
            for corner in (overlap.top_left, overlap.bottom_right):
                assert a.contains(corner)
                assert b.contains(corner)

    @given(rects, rects)
    def test_union_contains_both(self, a: Rect, b: Rect) -> None:
        """The union contains every corner of both rectangles and is minimal."""
        union = a.united(b)

        assert union == b.united(a)
        for rect in (a, b):
            assert union.contains(rect.top_left)
            assert union.contains(rect.bottom_right)
        assert union.left == min(a.left, b.left)
        assert union.bottom == max(a.bottom, b.bottom)

    @given(real_rects)
    def test_rounded_covers_original_with_whole_pixels(self, rect: Rect) -> None:
        """Rounding snaps outward to integers, so no selected pixel is lost."""
        snapped = rect.rounded()

        assert snapped.left <= rect.left
        assert snapped.top <= rect.top
        assert snapped.right >= rect.right
        assert snapped.bottom >= rect.bottom
        for value in (snapped.x, snapped.y, snapped.width, snapped.height):
            assert value == int(value)
        assert snapped.rounded() == snapped

    @given(rects, points)
    def test_translation_moves_without_resizing(self, rect: Rect, delta: Point) -> None:
        """Translating there and back is the identity, and the size never changes."""
        moved = rect.translated(delta)

        assert (moved.width, moved.height) == (rect.width, rect.height)
        assert moved.top_left == rect.top_left + delta
        assert moved.translated(Point(-delta.x, -delta.y)) == rect

    @given(rects, st.integers(0, 1_000))
    def test_grow_then_shrink_is_identity(self, rect: Rect, margin: int) -> None:
        """Growing by a margin then shrinking by it restores the rectangle."""
        grown = rect.adjusted(margin)

        assert grown.width == rect.width + 2 * margin
        assert grown.adjusted(-margin) == rect

    @given(rects, st.integers(-1_000, 0))
    def test_shrink_never_goes_negative(self, rect: Rect, margin: int) -> None:
        """Shrinking past nothing clamps to an empty rectangle, never a negative one."""
        shrunk = rect.adjusted(margin)

        assert shrunk.width >= 0
        assert shrunk.height >= 0

    @given(st.lists(points, min_size=1, max_size=20))
    def test_bounding_is_tight(self, pts: list[Point]) -> None:
        """Every point is inside the bounds, and each edge touches some point."""
        bounds = Rect.bounding(pts)

        assert all(bounds.contains(p) for p in pts)
        assert bounds.left == min(p.x for p in pts)
        assert bounds.right == max(p.x for p in pts)
        assert bounds.top == min(p.y for p in pts)
        assert bounds.bottom == max(p.y for p in pts)


class TestPointProperties:
    """Point arithmetic and the 45° snap."""

    @given(points, points)
    def test_add_and_subtract_are_inverse(self, a: Point, b: Point) -> None:
        """Adding then subtracting a point is the identity."""
        assert (a + b) - b == a
        assert a.distance_to(b) == b.distance_to(a)

    @given(real_points, real_points)
    def test_snap_keeps_length_and_lands_on_45_degrees(
        self, point: Point, origin: Point
    ) -> None:
        """Snapping keeps the distance from the origin and aligns to 45°."""
        snapped = point.snapped_to_45(origin)

        assert math.isclose(
            snapped.distance_to(origin), point.distance_to(origin), abs_tol=1e-6
        )
        delta = snapped - origin
        if math.hypot(delta.x, delta.y) > 1:
            steps = math.atan2(delta.y, delta.x) / (math.pi / 4)
            assert math.isclose(steps, round(steps), abs_tol=1e-6)
            # The snap picks the nearest 45° direction: never more than 22.5° off
            assert snapped.distance_to(point) <= point.distance_to(origin) * 0.4


class TestAffineProperties:
    """Scale-and-translate maps, as zoom and crop use them."""

    @given(rects, st.integers(1, 8), points)
    def test_scale_translate_maps_rect_exactly(
        self, rect: Rect, scale: int, offset: Point
    ) -> None:
        """An axis-aligned map moves corners and scales the size."""
        affine = Affine(scale, 0, offset.x, 0, scale, offset.y)

        mapped = affine.map_rect(rect)

        assert mapped.top_left == Point(
            rect.x * scale + offset.x, rect.y * scale + offset.y
        )
        assert (mapped.width, mapped.height) == (
            rect.width * scale,
            rect.height * scale,
        )
        assert affine.scale == scale


class TestColorProperties:
    """Hex color text round-trips exactly."""

    @given(colors)
    def test_hex_round_trip(self, color: Color) -> None:
        """Every color survives conversion to hex and back, in either case."""
        assert Color.from_hex(color.hex) == color
        assert Color.from_hex(color.hex.upper()) == color
        assert len(color.hex) == (7 if color.alpha == 255 else 9)


styles = st.builds(
    Style,
    stroke=colors,
    fill=colors,
    width=positive,
    font_family=text,
    font_size=st.integers(1, 200),
    bold=st.booleans(),
    italic=st.booleans(),
)
ids = st.text("0123456789abcdef", min_size=1, max_size=32)
annotations = st.one_of(
    st.builds(RectangleShape, id=ids, style=styles, rect=real_rects, text=text),
    st.builds(EllipseShape, id=ids, style=styles, rect=real_rects, text=text),
    st.builds(HighlightShape, id=ids, style=styles, rect=real_rects),
    st.builds(ObfuscateShape, id=ids, style=styles, rect=real_rects),
    st.builds(LineShape, id=ids, style=styles, start=real_points, end=real_points),
    st.builds(ArrowShape, id=ids, style=styles, start=real_points, end=real_points),
    st.builds(
        FreehandShape,
        id=ids,
        style=styles,
        points=st.lists(real_points, min_size=1, max_size=10).map(tuple),
    ),
    st.builds(TextNote, id=ids, style=styles, rect=real_rects, text=text),
    st.builds(
        CounterMarker,
        id=ids,
        style=styles,
        center=real_points,
        radius=positive,
        label=text,
    ),
)


class TestCodecProperties:
    """Whatever the editor can hold, the clipboard and files can carry back."""

    @given(styles)
    def test_style_round_trip(self, style: Style) -> None:
        """A style survives JSON text unchanged."""
        encoded = json.loads(json.dumps(StyleCodec.encode(style)))

        assert StyleCodec.decode(encoded) == style

    @given(st.lists(annotations, max_size=8))
    def test_annotations_round_trip(self, items: list[Annotation]) -> None:
        """Any list of annotations survives JSON text unchanged, in order."""
        codec = AnnotationCodec()

        encoded = json.loads(json.dumps(codec.encode_many(items)))

        assert codec.decode_many(encoded) == items


hotkeys = st.sampled_from(["", "print_screen", "ctrl+shift+a", "alt+f12"])
settings_values = st.builds(
    Settings,
    capture=st.builds(
        CaptureSettings,
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.booleans(),
    ),
    hotkeys=st.builds(HotkeySettings, hotkeys, hotkeys, hotkeys, hotkeys),
    output=st.builds(
        OutputSettings,
        directory=st.sampled_from([Path("shots"), Path("a") / "b c"]),
        filename_pattern=text,
        image_format=st.sampled_from(ImageFormat),
        jpeg_quality=st.integers(1, 100),
    ),
    editor=st.builds(
        EditorSettings,
        stroke_color=colors,
        stroke_width=positive,
        font_family=text,
        font_size=st.integers(1, 200),
        confirm_unsaved_close=st.booleans(),
    ),
    appearance=st.builds(AppearanceSettings, st.sampled_from(Theme)),
    startup=st.builds(StartupSettings, st.booleans()),
    integration=st.builds(IntegrationSettings, st.booleans(), st.booleans()),
)


class TestSettingsProperties:
    """Saved settings load back exactly as they were."""

    @given(settings_values)
    def test_settings_round_trip(self, settings: Settings) -> None:
        """Every combination of valid settings survives the settings file."""
        raw = json.loads(json.dumps(SettingsCodec.encode(settings)))

        assert SettingsCodec.decode(raw) == settings
