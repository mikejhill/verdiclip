"""Decode authoritative movement and button flags with synthetic display topology."""

import ctypes
import subprocess
import sys

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


def test_wire_layout_does_not_inherit_lp64_host_long_width() -> None:
    """Linux mutation/replay must decode the same Win32 packet as Windows."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes,ctypes.wintypes as w; "
            "w.DWORD=w.ULONG=ctypes.c_uint64; w.LONG=ctypes.c_int64; "
            "from verdiclip.capture.input_raw import "
            "RawHeader,RawMouse,RawMouseMessage,RawInputDevice; "
            "types=(RawHeader,RawMouse,RawMouseMessage,RawInputDevice); "
            "assert [ctypes.sizeof(t) for t in types]==[24,24,48,16]; "
            "m=RawMouse();m.x=-3;assert m.x==-3;print('Win32 layout retained')",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == "Win32 layout retained"


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
