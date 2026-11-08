"""Portable UIA boundary contracts; actual ABI execution belongs to Windows CI."""

from __future__ import annotations

import ctypes as c
from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scrolling import ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Interface


def mock(value: object) -> Mock:
    """Require the test-installed boundary double before inspecting it."""
    assert isinstance(value, Mock)
    return value


def target() -> UiaScrollTarget:
    """Retained target with a stable foreground process and exact viewport."""
    result = object.__new__(UiaScrollTarget)
    result._owners = ExitStack()
    result._user, result._kernel = Mock(), Mock()
    result._window, result._process = 1, 2
    result._window_rect = (0, 0, 500, 500)
    result._area = Rect(20, 20, 300, 300)
    result._input_tick = 12
    result._element, result._scroll = Mock(), Mock()
    result._element.bounds.return_value = result._area
    result._user.GetForegroundWindow.return_value = 1
    result._user.IsWindowVisible.return_value = True
    result._user.IsIconic.return_value = False
    result._user.GetAncestor.return_value = 1
    result._kernel.WaitForSingleObject.return_value = 258
    result._input = Mock(return_value=12)
    result._window_bounds = Mock(return_value=(0, 0, 500, 500))
    result._screens = Mock()
    result._screens.freeze.return_value = FrozenScreen(
        QImage(500, 500, QImage.Format.Format_RGB32), Rect(0, 0, 500, 500)
    )
    return result


def test_null_pattern_is_optional_without_masking_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COM success with a null pattern is not an error or a scroll candidate."""
    element = object.__new__(_Interface)
    call = Mock()
    monkeypatch.setattr(element, "call", call)
    with ExitStack() as owners:
        assert element.scroll_pattern(owners) is None
    assert call.call_args.args[0] == 14
    assert call.call_args.args[2] == 10004
    call.side_effect = OSError("Element unavailable")
    with ExitStack() as owners, pytest.raises(OSError, match="unavailable"):
        element.scroll_pattern(owners)


def test_null_pattern_leaf_ascends_to_scrollable_parent() -> None:
    """Ordinary document text leaves do not themselves expose ScrollPattern."""
    native = target()
    leaf, parent, walker, pattern = Mock(), Mock(), Mock(), Mock()
    leaf.scroll_pattern.return_value = None
    parent.scroll_pattern.return_value = pattern
    pattern.number.return_value = 20.0
    walker.pointer_result.return_value = parent
    assert native._find(leaf, walker) == (parent, pattern)
    assert walker.pointer_result.call_count == 1


def test_discovery_does_not_rebind_after_provider_error() -> None:
    """A dead provider is not permission to choose another ancestor."""
    native = target()
    leaf, walker = Mock(), Mock()
    leaf.scroll_pattern.side_effect = OSError("Element unavailable")
    with pytest.raises(OSError, match="unavailable"):
        native._find(leaf, walker)
    walker.pointer_result.assert_not_called()


def test_discovery_ancestor_limit() -> None:
    """Broken/cyclic providers cannot cause an unbounded walk."""
    native = target()
    leaf, walker = Mock(), Mock()
    leaf.scroll_pattern.return_value = None
    walker.pointer_result.return_value = leaf
    with pytest.raises(CaptureError, match="No supported"):
        native._find(leaf, walker)
    assert walker.pointer_result.call_count == 32


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "focus",
        "hidden",
        "minimized",
        "exited",
        "window_moved",
        "panel_moved",
        "covered",
    ],
)
def test_changed_user_or_target_state_rejects_write(change: str) -> None:
    """Any checked state loss stops before crossing the mutation boundary."""
    native = target()
    if change == "input":
        mock(native._input).return_value = 13
    elif change == "focus":
        mock(native._user.GetForegroundWindow).return_value = 9
    elif change == "hidden":
        mock(native._user.IsWindowVisible).return_value = False
    elif change == "minimized":
        mock(native._user.IsIconic).return_value = True
    elif change == "exited":
        mock(native._kernel.WaitForSingleObject).return_value = 0
    elif change == "window_moved":
        mock(native._window_bounds).return_value = (0, 0, 400, 400)
    elif change == "panel_moved":
        mock(native._element.bounds).return_value = Rect(30, 30, 300, 300)
    else:
        mock(native._user.GetAncestor).return_value = 9
    with pytest.raises(CaptureError):
        native.move(50.0)
    mock(native._scroll.call).assert_not_called()


def test_write_keeps_horizontal_percent_and_checks_after_call() -> None:
    """NoScroll (-1) preserves horizontal position; all actual input is observed."""
    native = target()
    native.move(50.0)
    mock(native._scroll.call).assert_called_once_with(
        4, (c.c_double, c.c_double), -1.0, 50.0
    )
    assert mock(native._input).call_count == 4


def test_frame_keeps_full_provider_rectangle() -> None:
    """No runtime guessed crop of sidebars, headers, or scrollbars."""
    native = target()
    assert native.area == Rect(20, 20, 300, 300)
    assert native.frame().size().width() == 300
    assert native.frame().size().height() == 300


def test_off_desktop_frame_is_not_silently_clipped() -> None:
    """Clipping could change both the selected content and stitch geometry."""
    native = target()
    native._area = Rect(300, 300, 300, 300)
    mock(native._element.bounds).return_value = native._area
    with pytest.raises(CaptureError, match="outside"):
        native.frame()


def test_position_read_is_typed_and_bounded() -> None:
    """Read the correct SDK getters and reject invalid values."""
    native = target()
    mock(native._scroll.number).side_effect = [20.0, 50.0, 10.0]
    assert native.position() == ScrollPosition(20.0, 50.0, 10.0)
    assert [call.args[0] for call in mock(native._scroll.number).call_args_list] == [
        5,
        6,
        8,
    ]
    mock(native._scroll.number).side_effect = [-2.0, 50.0, 10.0]
    with pytest.raises(CaptureError, match="unsupported"):
        native.position()


def test_non_windows_fails_without_loading_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing is portable; constructing unsupported native state is not."""
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(CaptureError, match="Windows"):
        UiaScrollTarget(Point(10, 10))
