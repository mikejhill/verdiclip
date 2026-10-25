"""Replay owned native trace coordinates without hooks, devices or desktop input."""

import pytest

from verdiclip.capture.input_routing import MoveMode, PointerTracker
from verdiclip.geometry import Point, Rect


def test_recorded_injected_drag_ignores_frozen_button_coordinates() -> None:
    """Replay the successful native-menu probe's exact move/down/move/up trace."""
    frozen = Point(262, 168)
    tracker = PointerTracker(frozen, Rect(0, 0, 1920, 1080))
    assert tracker.move(Point(259, 194), mode=MoveMode.ABSOLUTE, anchor=frozen)
    assert tracker.press()  # Native down packet also said (262,168).
    assert tracker.move(Point(369, 244), mode=MoveMode.ABSOLUTE, anchor=frozen)
    assert tracker.release() == Rect(259, 194, 110, 50)
    assert tracker.release() is None  # Native up packet also said (262,168).
    assert not tracker.press()
    assert not tracker.move(Point(1, 1), mode=MoveMode.ABSOLUTE, anchor=frozen)


def test_relative_moves_accumulate_against_frozen_os_anchor() -> None:
    """Model accelerated deltas without asserting physical-device qualification."""
    anchor = Point(200, 100)
    tracker = PointerTracker(anchor, Rect(-1920, -1080, 3840, 2160))
    assert tracker.press()
    assert tracker.move(Point(150, 80), mode=MoveMode.OS_RELATIVE, anchor=anchor)
    assert tracker.move(Point(150, 80), mode=MoveMode.OS_RELATIVE, anchor=anchor)
    assert tracker.release() == Rect(100, 60, 100, 40)


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 2])
def test_physical_selection_is_independent_of_display_scale(scale: float) -> None:
    """Synthetic DPI topologies retain physical pixels across a negative origin."""
    tracker = PointerTracker(
        Point(-100 * scale, 10 * scale), Rect(-3840, 0, 7680, 2160)
    )
    assert tracker.press()
    assert tracker.move(
        Point(100 * scale, 50 * scale), mode=MoveMode.ABSOLUTE, anchor=Point(0, 0)
    )
    assert tracker.release() == Rect(-100 * scale, 10 * scale, 200 * scale, 40 * scale)


def test_cancel_unmatched_release_and_repeated_press_are_safe() -> None:
    """Escape and unmatched packets cannot complete or resurrect a selection."""
    tracker = PointerTracker(Point(0, 0), Rect(0, 0, 100, 100))
    assert tracker.release() is None
    assert tracker.press()
    assert not tracker.press()
    tracker.cancel()
    assert tracker.finished
    assert tracker.release() is None
    assert not tracker.press()


def test_invalid_move_fails_closed_and_bounds_limit_pointer() -> None:
    """Nonfinite data cancels; a valid out-of-bounds move stays on the desktop."""
    tracker = PointerTracker(Point(10, 10), Rect(0, 0, 100, 100))
    assert tracker.press()
    assert tracker.move(Point(200, -20), mode=MoveMode.ABSOLUTE, anchor=Point(0, 0))
    assert tracker.point == Point(100, 0)
    assert not tracker.move(
        Point(float("nan"), 0), mode=MoveMode.ABSOLUTE, anchor=Point(0, 0)
    )
    assert tracker.release() is None


@pytest.mark.parametrize("bounds", [Rect(0, 0, 0, 1), Rect(0, 0, 1, 0)])
def test_invalid_topology_rejected(bounds: Rect) -> None:
    """A missing display is a failure rather than a fabricated selection."""
    with pytest.raises(ValueError, match="nonempty"):
        PointerTracker(Point(0, 0), bounds)
