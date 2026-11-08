"""Portable UIA boundary contracts; actual ABI execution belongs to Windows CI."""

from __future__ import annotations

import ctypes as c
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from scripts.simple_scroll_native import _position_start, _verify_selected_target

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
    result._provider_bounds = result._area
    result._viewport = Mock(return_value=result._area)
    result._input_tick = 12
    result._element, result._scroll = Mock(), Mock()
    result._element.bounds.return_value = result._area
    result._native_handle = None
    result._element.native_handle.return_value = None
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
    walker.parent.return_value = parent
    assert native._find(leaf, walker) == (parent, pattern)
    assert walker.parent.call_count == 1


def test_discovery_does_not_rebind_after_provider_error() -> None:
    """A dead provider is not permission to choose another ancestor."""
    native = target()
    leaf, walker = Mock(), Mock()
    leaf.scroll_pattern.side_effect = OSError("Element unavailable")
    with pytest.raises(OSError, match="unavailable"):
        native._find(leaf, walker)
    walker.parent.assert_not_called()


def test_discovery_ancestor_limit() -> None:
    """Broken/cyclic providers cannot cause an unbounded walk."""
    native = target()
    leaf, walker = Mock(), Mock()
    leaf.scroll_pattern.return_value = None
    walker.parent.return_value = leaf
    with pytest.raises(CaptureError, match="No supported"):
        native._find(leaf, walker)
    assert walker.parent.call_count == 32


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


@pytest.mark.parametrize("horizontal", [-1.0, 0.0, 34.883720930232556])
def test_write_keeps_horizontal_percent_and_checks_after_call(
    horizontal: float,
) -> None:
    """Do not ask a horizontally scrollable provider to interpret NoScroll."""
    native = target()
    mock(native._scroll.number).return_value = horizontal
    native.move(50.0)
    mock(native._scroll.call).assert_called_once_with(
        4, (c.c_double, c.c_double), horizontal, 50.0
    )
    assert mock(native._input).call_count == 6


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
    native._provider_bounds = native._area
    mock(native._viewport).return_value = native._area
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


def test_missing_pattern_requires_two_matching_same_point_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standard metadata reads may activate a lazily exposed application tree."""
    native = target()
    automation, walker, first, second, pattern = (Mock() for _ in range(5))
    automation.pointer_result.side_effect = [first, second, second]
    automation.same_element.return_value = True
    first.scroll_pattern.return_value = None
    walker.parent.return_value = None
    second.scroll_pattern.return_value = pattern
    pattern.number.return_value = 0.0
    pause = Mock()
    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", pause)
    assert native._discover(Point(30, 40), automation, walker) == (second, pattern)
    assert pause.call_count == 2
    assert first.control_type.call_count == 1
    assert second.control_type.call_count == 2
    automation.same_element.assert_called_once_with(second, second)
    assert [call.args[0] for call in automation.pointer_result.call_args_list] == [
        7,
        7,
        7,
    ]
    points = [call.args[2][1] for call in automation.pointer_result.call_args_list]
    assert [(point.x, point.y) for point in points] == [(30, 40)] * 3


def test_refresh_keeps_original_input_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting for accessibility never recaptures permission after user input."""
    native = target()
    automation, walker, leaf = Mock(), Mock(), Mock()
    automation.pointer_result.return_value = leaf
    leaf.scroll_pattern.return_value = None
    walker.parent.return_value = None

    def user_input(_seconds: float) -> None:
        mock(native._input).return_value = 13

    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", user_input)
    with pytest.raises(CaptureError, match="mouse or keyboard"):
        native._discover(Point(30, 40), automation, walker)
    assert automation.pointer_result.call_count == 1


def test_refresh_never_masks_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken inner provider is never a reason to bind an outer target."""
    native = target()
    automation, walker, leaf = Mock(), Mock(), Mock()
    automation.pointer_result.return_value = leaf
    leaf.scroll_pattern.side_effect = OSError("Provider unavailable")
    pause = Mock()
    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", pause)
    with pytest.raises(OSError, match="unavailable"):
        native._discover(Point(30, 40), automation, walker)
    pause.assert_not_called()
    assert automation.pointer_result.call_count == 1


def test_native_viewport_without_handle_uses_provider_bounds() -> None:
    """Never substitute an ancestor HWND for an application-owned panel."""
    native = target()
    assert UiaScrollTarget._viewport(native) == native._provider_bounds


def test_native_viewport_excludes_only_own_non_client_chrome() -> None:
    """Use the actual native client rectangle, not guessed text/sidebars."""
    native = target()

    def client_rect(_handle: object, value: c._CArgObject) -> bool:
        # RECT uses Windows LONG; use exactly the type from the adapter.
        import ctypes.wintypes as w  # noqa: PLC0415

        rect = c.cast(value, c.POINTER(w.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 280, 280
        return True

    def locate(_handle: object, value: c._CArgObject) -> bool:
        import ctypes.wintypes as w  # noqa: PLC0415

        point = c.cast(value, c.POINTER(w.POINT)).contents
        point.x, point.y = point.x + 20, point.y + 20
        return True

    native._native_handle = 10
    mock(native._element.native_handle).return_value = 10
    mock(native._user.GetClientRect).side_effect = client_rect
    mock(native._user.ClientToScreen).side_effect = locate
    assert UiaScrollTarget._viewport(native) == Rect(20, 20, 280, 280)
    mock(native._user.GetAncestor).return_value = 9
    with pytest.raises(CaptureError, match="owner changed"):
        UiaScrollTarget._viewport(native)


def test_discovery_exhausts_after_three_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported targets stop at the fixed observation bound."""
    native = target()
    automation, walker, leaf = Mock(), Mock(), Mock()
    automation.pointer_result.return_value = leaf
    leaf.scroll_pattern.return_value = None
    walker.parent.return_value = None
    pause = Mock()
    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", pause)
    with pytest.raises(CaptureError, match="No stable supported"):
        native._discover(Point(30, 40), automation, walker)
    assert pause.call_count == 2
    assert automation.pointer_result.call_count == 3


def test_changed_native_handle_is_not_rebound() -> None:
    """Identical geometry is insufficient when the selected native owner changes."""
    native = target()
    native._native_handle = 10
    mock(native._element.native_handle).return_value = 11
    with pytest.raises(CaptureError, match="handle changed"):
        UiaScrollTarget._viewport(native)
    mock(native._user.GetClientRect).assert_not_called()


@pytest.mark.parametrize("nested", [False, True])
def test_native_harness_waits_for_its_setup_write(
    monkeypatch: pytest.MonkeyPatch, *, nested: bool
) -> None:
    """The actual helper starts only after one confirmed setup mutation."""
    native = target()
    start = ScrollPosition(0.0, 0.0, 20.0)
    settled = ScrollPosition(34.9 if nested else 0.0, 36.8, 20.0)
    native.position = Mock(side_effect=[start, start, settled, settled])
    pause = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    report: dict[str, object] = {}
    assert _position_start(native, report, nested=nested) == settled
    assert report["immediate_setup_position"] == {
        "horizontal": 0.0,
        "vertical": 0.0,
        "view_size": 20.0,
    }
    mock(native._scroll.call).assert_called_once()
    assert mock(native._scroll.call).call_args.args[2:] == (
        35.0 if nested else -1.0,
        37.0,
    )
    assert pause.call_count == 3


def test_native_harness_does_not_reissue_unsettled_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stable but unchanged or horizontally reset setup is a failure."""
    native = target()
    native.position = Mock(return_value=ScrollPosition(0.0, 37.0, 20.0))
    pause = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    with pytest.raises(RuntimeError, match="did not settle"):
        _position_start(native, {}, nested=True)
    assert pause.call_count == 8
    mock(native._scroll.call).assert_called_once()


@pytest.mark.parametrize("stable", [True, False])
def test_lazy_outer_target_cannot_win_before_inner_discovery(
    monkeypatch: pytest.MonkeyPatch, *, stable: bool
) -> None:
    """A provisional outer candidate must never start a scrolling transaction."""
    native = target()
    automation, walker, outer, inner, other = (Mock() for _ in range(5))
    automation.pointer_result.side_effect = [outer, inner, inner if stable else other]
    automation.same_element.side_effect = [False, stable]
    for element in (outer, inner, other):
        element.scroll_pattern.return_value.number.return_value = 0.0
    pause = Mock()
    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", pause)
    if stable:
        assert native._discover(Point(30, 40), automation, walker) == (
            inner,
            inner.scroll_pattern.return_value,
        )
    else:
        with pytest.raises(CaptureError, match="No stable supported"):
            native._discover(Point(30, 40), automation, walker)
    assert automation.pointer_result.call_count == 3
    assert pause.call_count == 2
    mock(native._scroll.call).assert_not_called()


def test_uia_element_identity_uses_compare_elements_slot() -> None:
    """Equal-looking rectangles and different interface addresses are not identity."""
    import ctypes.wintypes as w  # noqa: PLC0415

    automation, first, second = (object.__new__(_Interface) for _ in range(3))
    first._pointer, second._pointer = c.c_void_p(10), c.c_void_p(20)

    def compare(
        slot: int, _types: object, a: object, b: object, out: c._CArgObject
    ) -> None:
        assert slot == 3
        assert a is first._pointer
        assert b is second._pointer
        c.cast(out, c.POINTER(w.BOOL)).contents.value = 1

    automation.call = Mock(side_effect=compare)
    assert automation.same_element(first, second)


@pytest.mark.parametrize("wrong", ["none", "dimensions", "horizontal", "view"])
def test_native_harness_rejects_outer_panel_even_if_restored(
    tmp_path: Path, wrong: str
) -> None:
    """Final inner state cannot qualify a helper that captured another target."""
    image = QImage(
        976 if wrong == "dimensions" else 700, 420, QImage.Format.Format_RGB32
    )
    image.fill(0)
    assert image.save(str(tmp_path / "frame-0.png"))
    position = {
        "horizontal": -1.0 if wrong == "horizontal" else 35.0,
        "vertical": 0.0,
        "view_size": 30.0 if wrong == "view" else 14.5,
    }
    (tmp_path / "frames.json").write_text(json.dumps([position]), encoding="utf-8")
    arguments = (tmp_path, Rect(22, 90, 700, 420), ScrollPosition(35.0, 37.0, 14.5))
    if wrong == "none":
        _verify_selected_target(*arguments)
    else:
        with pytest.raises(RuntimeError, match="different panel"):
            _verify_selected_target(*arguments)


def test_discovery_identity_error_never_retries_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed identity read is not ordinary lazy-tree readiness."""
    native = target()
    automation, walker, leaf = Mock(), Mock(), Mock()
    automation.pointer_result.return_value = leaf
    leaf.scroll_pattern.return_value.number.return_value = 0.0
    automation.same_element.side_effect = OSError("Identity unavailable")
    pause = Mock()
    monkeypatch.setattr("verdiclip.platform.scrolling.time.sleep", pause)
    with pytest.raises(OSError, match="Identity unavailable"):
        native._discover(Point(30, 40), automation, walker)
    assert automation.pointer_result.call_count == 2
    pause.assert_called_once_with(0.5)
    mock(native._scroll.call).assert_not_called()


def test_horizontal_read_must_not_hide_new_user_input() -> None:
    """A standard H read still precedes the final pre-write ownership check."""
    native = target()

    def horizontal(_slot: int) -> float:
        mock(native._input).return_value = 13
        return 35.0

    mock(native._scroll.number).side_effect = horizontal
    with pytest.raises(CaptureError, match="mouse or keyboard"):
        native.move(0.0)
    mock(native._scroll.call).assert_not_called()


@pytest.mark.parametrize("horizontal", [-2.0, -0.5])
def test_invalid_horizontal_read_never_reaches_provider_write(
    horizontal: float,
) -> None:
    """Invalid horizontal metadata is not silently turned into NoScroll."""
    native = target()
    mock(native._scroll.number).return_value = horizontal
    with pytest.raises(CaptureError, match="horizontal state"):
        native.move(0.0)
    mock(native._scroll.call).assert_not_called()


def test_fractional_negative_horizontal_position_is_not_noscroll() -> None:
    """UIA defines -1 or a nonnegative percentage, not the entire -1..100 range."""
    native = target()
    mock(native._scroll.number).side_effect = [-0.5, 50.0, 20.0]
    with pytest.raises(CaptureError, match="unsupported scroll state"):
        native.position()
