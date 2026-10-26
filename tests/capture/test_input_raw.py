"""Decode authoritative movement and button flags with synthetic display topology."""

import ctypes

import pytest

from verdiclip.capture.input_raw import (
    RawHeader,
    RawMouse,
    RawMouseMessage,
    RawPointerDecoder,
)
from verdiclip.capture.input_routing import MoveMode
from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point, Rect


def test_raw_mouse_layout_matches_64_bit_win32() -> None:
    """An aligned union prevents shifted packet fields on the supported ABI."""
    assert ctypes.sizeof(RawHeader) == 24
    assert ctypes.sizeof(RawMouse) == 24
    assert ctypes.sizeof(RawMouseMessage) == 48
    assert RawMouse.buttons.offset == 4


def test_relative_delta_does_not_infer_accelerated_or_absolute_motion() -> None:
    """The source supplies raw precision deltas; no Windows acceleration is invented."""
    mouse = RawMouse()
    mouse.x, mouse.y = -3, 7
    event = RawPointerDecoder.decode(
        mouse, primary=Rect(0, 0, 100, 100), virtual=Rect(-100, 0, 200, 100)
    )
    assert event.mode is MoveMode.RELATIVE_DELTA
    assert event.point == Point(-3, 7)


@pytest.mark.parametrize("virtual", [False, True])
def test_absolute_pointer_maps_its_explicit_display_coordinate_space(
    *, virtual: bool
) -> None:
    """Negative monitor origins do not turn absolute tablets into relative mice."""
    mouse = RawMouse()
    mouse.flags = 3 if virtual else 1
    mouse.x, mouse.y = 65535, 0
    event = RawPointerDecoder.decode(
        mouse, primary=Rect(0, 0, 1920, 1080), virtual=Rect(-1920, -1080, 3840, 2160)
    )
    assert event.mode is MoveMode.ABSOLUTE
    assert event.point == (Point(1919, -1080) if virtual else Point(1919, 0))


def test_extra_buttons_and_wheel_are_separate_semantic_fields() -> None:
    """Raw X1/X2 bits remain distinct even when both are pressed in one report."""
    mouse = RawMouse()
    mouse.buttons.parts.flags = 64 | 256 | 0x400
    event = RawPointerDecoder.decode(
        mouse, primary=Rect(0, 0, 1, 1), virtual=Rect(0, 0, 1, 1)
    )
    assert event.buttons == ((0x20B, 1), (0x20B, 2))
    assert event.wheel


def test_invalid_absolute_coordinates_refused() -> None:
    """Malformed driver coordinates cannot become an arbitrary capture click."""
    mouse = RawMouse()
    mouse.flags = 1
    mouse.x = -1
    with pytest.raises(PlatformError, match="coordinates"):
        RawPointerDecoder.decode(
            mouse, primary=Rect(0, 0, 100, 100), virtual=Rect(0, 0, 100, 100)
        )
