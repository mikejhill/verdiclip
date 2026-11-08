"""Portable UIA boundary contracts; actual ABI execution belongs to Windows CI."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts.simple_scroll_native import (
    _position_start,
    _verify_edge_rows,
    _verify_selected_target,
)

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scrolling import ScrollPosition, ScrollResult
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
    result._user, result._kernel, result._dwm = Mock(), Mock(), Mock()
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
    result._user.GetTopWindow.return_value = 1
    result._user.IsWindow.return_value = True
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


@pytest.mark.parametrize("fail", [False, True])
def test_native_construction_binds_apis_and_closes_owned_resources(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool
) -> None:
    """Mock only OS loading while exercising real binding and ownership setup."""
    dlls = {name: Mock() for name in ("user32", "kernel32", "ole32", "dwmapi")}
    screens = Mock()
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(c, "HRESULT", c.c_long, raising=False)
    monkeypatch.setattr(c, "WinDLL", lambda name, **_kwargs: dlls[name], raising=False)
    monkeypatch.setattr("verdiclip.platform.scrolling.MssScreenSource", lambda: screens)
    opening = Mock(side_effect=CaptureError("Discovery failed") if fail else None)
    monkeypatch.setattr(UiaScrollTarget, "_open", opening)
    if fail:
        with pytest.raises(CaptureError, match="Discovery failed"):
            UiaScrollTarget(Point(30, 40))
    else:
        with UiaScrollTarget(Point(30, 40)) as native:
            assert native._user is dlls["user32"]
    screens.close.assert_called_once()
    opening.assert_called_once_with(Point(30, 40))
    assert dlls["user32"].GetForegroundWindow.restype is w.HWND
    assert dlls["user32"].ClientToScreen.argtypes == (w.HWND, c.POINTER(w.POINT))
    assert dlls["kernel32"].WaitForSingleObject.argtypes == (w.HANDLE, w.DWORD)
    assert dlls["ole32"].CoInitializeEx.argtypes == (c.c_void_p, w.DWORD)


@pytest.mark.parametrize("failure", ["none", "foreground", "process", "area"])
def test_native_open_keeps_original_process_and_releases_com(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Exercise the real retained-target setup around mocked native calls."""
    native = target()
    native._ole = Mock()
    automation = Mock()
    monkeypatch.setattr(
        "verdiclip.platform.scrolling._Interface", Mock(return_value=automation)
    )
    native._discover = Mock(return_value=(native._element, native._scroll))

    def process_id(_window: object, out: c._CArgObject) -> int:
        c.cast(out, c.POINTER(w.DWORD)).contents.value = 42
        return 7

    mock(native._user.GetWindowThreadProcessId).side_effect = process_id
    mock(native._kernel.OpenProcess).return_value = 0 if failure == "process" else 2
    if failure == "foreground":
        mock(native._user.GetForegroundWindow).return_value = 99
    if failure == "area":
        mock(native._viewport).return_value = Rect(20, 20, 10, 10)
    try:
        if failure == "none":
            native._open(Point(30, 40))
            assert native._input_tick == 12
            assert native._window_rect == (0, 0, 500, 500)
            assert native._process == 2
            mock(native._kernel.OpenProcess).assert_called_once_with(0x00100000, 0, 42)
            automation.pointer_result.assert_called_once_with(16, native._owners)
        else:
            with pytest.raises(CaptureError):
                native._open(Point(30, 40))
    finally:
        native._owners.close()
    if failure in ("none", "area"):
        mock(native._kernel.CloseHandle).assert_called_once_with(2)
        mock(native._ole.CoUninitialize).assert_called_once()
    else:
        mock(native._kernel.CloseHandle).assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["none", "missing_span", "duplicate", "boundary", "recolor", "interior_chrome"],
)
def test_native_row_oracle_counts_content_and_original_chrome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """Visible source rows are checked separately from retained endpoint chrome."""
    monkeypatch.setattr("scripts.simple_scroll_native._ROWS", 4)
    source = QImage(80, 104, QImage.Format.Format_RGB32)
    source.fill(QColor("gray"))
    painter = QPainter(source)
    for row in range(4):
        painter.fillRect(0, row * 24, 80, 24, QColor(row + 30, 80, 160))
    painter.fillRect(0, 0, 80, 1, QColor("black"))
    painter.end()
    first = QImage(80, 64, QImage.Format.Format_RGB32)
    first.fill(QColor("gray"))
    painter = QPainter(first)
    painter.drawImage(0, 0, source.copy(0, 0, 80, 56))
    painter.end()
    assert first.save(str(tmp_path / "frame-0.png"))
    if failure == "missing_span":
        damaged = QImage(80, 103, QImage.Format.Format_RGB32)
        painter = QPainter(damaged)
        painter.drawImage(0, 0, source.copy(0, 0, 80, 47))
        painter.drawImage(0, 47, source.copy(0, 48, 80, 56))
        painter.end()
        source = damaged
    else:
        painter = QPainter(source)
        if failure == "duplicate":
            painter.fillRect(0, 48, 80, 24, QColor(31, 80, 160))
        elif failure == "boundary":
            painter.fillRect(0, 1, 80, 1, QColor("black"))
        elif failure == "recolor":
            painter.fillRect(0, 0, 80, 1, QColor("magenta"))
        elif failure == "interior_chrome":
            painter.fillRect(0, 50, 80, 1, QColor("black"))
        painter.end()
    report: dict[str, object] = {}
    result = ScrollResult(source, "", "restored", 3)
    if failure == "none":
        _verify_edge_rows(result, tmp_path, report)
        assert report["boundary_rows"] == {"top": 1, "bottom": 8}
        assert report["content_row_heights"] == [23, 24, 24, 24]
    else:
        with pytest.raises(RuntimeError):
            _verify_edge_rows(result, tmp_path, report)


@pytest.mark.parametrize(
    "state",
    [
        "clear",
        "overlap",
        "hidden",
        "minimized",
        "cloaked",
        "dwm_error",
        "rect_error",
        "cycle",
        "missing",
        "limit",
        "gone",
    ],
)
def test_occlusion_checks_every_higher_window_without_filtering(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """Even a one-pixel untitled overlay between the old samples is rejected."""
    native = target()
    mock(native._user.GetTopWindow).return_value = 9
    mock(native._user.GetWindow).return_value = {"cycle": 9, "missing": None}.get(
        state, 1
    )
    monkeypatch.setattr(
        "verdiclip.platform.scrolling._MAX_WINDOWS", 1 if state == "limit" else 512
    )
    mock(native._user.IsWindow).return_value = state != "gone"
    mock(native._user.IsWindowVisible).return_value = state != "hidden"
    mock(native._user.IsIconic).return_value = state == "minimized"

    def cloak(_window: int, _attribute: int, out: c._CArgObject, _size: int) -> None:
        if state == "dwm_error":
            raise OSError("DWM unavailable")
        c.cast(out, c.POINTER(w.DWORD)).contents.value = int(state == "cloaked")

    def bounds(_window: int, out: c._CArgObject) -> bool:
        rect = c.cast(out, c.POINTER(w.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = (
            (30, 30, 31, 31) if state == "overlap" else (600, 600, 610, 610)
        )
        return state != "rect_error"

    mock(native._dwm.DwmGetWindowAttribute).side_effect = cloak
    mock(native._user.GetWindowRect).side_effect = bounds
    if state in ("clear", "hidden", "minimized", "cloaked"):
        native._check_occlusion()
    else:
        with pytest.raises((CaptureError, OSError)):
            native._check_occlusion()
    mock(native._scroll.call).assert_not_called()
    if state in ("hidden", "minimized"):
        mock(native._dwm.DwmGetWindowAttribute).assert_not_called()
    if state == "cloaked":
        mock(native._user.GetWindowRect).assert_not_called()


def test_interface_dispatch_and_release_use_retained_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise ctypes vtable dispatch locally, without invoking native APIs."""
    monkeypatch.setattr(c, "WINFUNCTYPE", c.CFUNCTYPE, raising=False)
    monkeypatch.setattr(c, "HRESULT", c.c_long, raising=False)
    seen: list[tuple[int, int | None]] = []
    release_type = c.CFUNCTYPE(w.ULONG, c.c_void_p)
    getter_type = c.CFUNCTYPE(c.c_long, c.c_void_p, c.POINTER(c.c_double))

    @release_type
    def release(pointer: int) -> int:
        seen.append((2, pointer))
        return 0

    @getter_type
    def getter(pointer: int, out: c._Pointer[c.c_double]) -> int:
        seen.append((5, pointer))
        out.contents.value = 37.0
        return 0

    table = (c.c_void_p * 6)()
    table[2], table[5] = c.cast(release, c.c_void_p), c.cast(getter, c.c_void_p)
    object_pointer = c.pointer(c.cast(table, c.POINTER(c.c_void_p)))
    pointer = c.cast(object_pointer, c.c_void_p)
    with ExitStack() as owners:
        interface = _Interface(pointer, owners)
        assert interface.number(5) == 37.0
    assert seen == [(5, pointer.value), (2, pointer.value)]
    with ExitStack() as owners, pytest.raises(CaptureError, match="no target"):
        _Interface(c.c_void_p(), owners)


def test_interface_output_wrappers_and_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert exact native output types, values and retained-reference cleanup."""
    interface = object.__new__(_Interface)
    release = Mock()
    monkeypatch.setattr(_Interface, "_release", release)

    def call(slot: int, _arguments: object, *values: object) -> None:
        out = values[-1]
        # The declared wrappers supply ctypes byref output arguments.
        assert isinstance(out, type(c.byref(c.c_int())))
        if slot in (7, 14):
            c.cast(out, c.POINTER(c.c_void_p)).contents.value = 123
        elif slot == 36:
            c.cast(out, c.POINTER(w.HWND)).contents.value = 456
        elif slot == 21:
            c.cast(out, c.POINTER(c.c_int)).contents.value = 50030
        elif slot == 43:
            rect = c.cast(out, c.POINTER(w.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = 10, 20, 310, 420
        elif slot == 5:
            c.cast(out, c.POINTER(c.c_double)).contents.value = float("nan")

    monkeypatch.setattr(interface, "call", call)
    with ExitStack() as owners:
        assert interface.pointer_result(7, owners)._pointer.value == 123
        pattern = interface.scroll_pattern(owners)
        assert pattern is not None
        assert pattern._pointer.value == 123
        interface._pointer = c.c_void_p(99)
        assert interface.parent(interface, owners) is None
        assert interface.native_handle() == 456
        assert interface.control_type() == 50030
        assert interface.bounds() == Rect(10, 20, 300, 400)
        with pytest.raises(CaptureError, match="invalid scroll position"):
            interface.number(5)
    assert release.call_count == 2


@pytest.mark.parametrize("fail", [False, True])
def test_input_and_window_bounds_observations(*, fail: bool) -> None:
    """Unavailable native observations fail rather than inventing stable state."""
    native = target()
    del native._input
    del native._window_bounds

    def input_info(out: c._CArgObject) -> bool:
        from verdiclip.platform.scrolling import _LastInput  # noqa: PLC0415

        c.cast(out, c.POINTER(_LastInput)).contents.tick = 87
        return not fail

    def bounds(_window: int, out: c._CArgObject) -> bool:
        rect = c.cast(out, c.POINTER(w.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 10, 20, 500, 600
        return not fail

    mock(native._user.GetLastInputInfo).side_effect = input_info
    mock(native._user.GetWindowRect).side_effect = bounds
    if fail:
        with pytest.raises(CaptureError, match="input"):
            native._input()
        with pytest.raises(CaptureError, match="no longer available"):
            native._window_bounds()
    else:
        assert native._input() == 87
        assert native._window_bounds() == (10, 20, 500, 600)


@pytest.mark.parametrize(
    "state", ["offscreen", "late_input", "client_rect", "client_to_screen"]
)
def test_native_read_failures_prevent_mutation(state: str) -> None:
    """Late visibility/input changes and client lookup failures cannot write."""
    native = target()
    if state == "offscreen":

        def offscreen(_slot: int, _arguments: object, out: c._CArgObject) -> None:
            c.cast(out, c.POINTER(w.BOOL)).contents.value = 1

        mock(native._element.call).side_effect = offscreen
    elif state == "late_input":
        mock(native._input).side_effect = [12, 13]
    else:
        del native._viewport
        native._native_handle = 7
        mock(native._element.native_handle).return_value = 7
        mock(native._user.GetClientRect).return_value = state != "client_rect"
        mock(native._user.ClientToScreen).return_value = False
    with pytest.raises(CaptureError):
        native.move(50.0)
    mock(native._scroll.call).assert_not_called()


@pytest.mark.parametrize("kind", ["edge-cancel", "edge-input", "edge-covered"])
@pytest.mark.parametrize("fault", ["none", "extra_write", "image", "position"])
def test_native_interruption_oracle_rejects_unsafe_outcomes(
    tmp_path: Path, kind: str, fault: str
) -> None:
    """Require real restoration for cancel, and no later write after interference."""
    from scripts.simple_scroll_native import _verify_interruption  # noqa: PLC0415

    initial = ScrollPosition(-1.0, 37.0, 20.0)
    image = QImage(10, 10, QImage.Format.Format_RGB32) if fault == "image" else None
    if kind == "edge-cancel":
        result = ScrollResult(image, "Scrolling capture cancelled", "restored", 1)
        movement = {"writes": 2, "last_vertical": 37.0}
        after = initial
    elif kind == "edge-input":
        result = ScrollResult(
            image,
            "Stopped because you used the mouse or keyboard",
            "not restored: target changed",
            1,
        )
        movement = {"writes": 1, "last_vertical": 0.0}
        after = ScrollPosition(-1.0, 0.0, 20.0)
    else:
        result = ScrollResult(
            image,
            "Another window covers the scroll panel",
            "unchanged: discovery failed",
            0,
        )
        movement = {"writes": 0, "last_vertical": None}
        after = initial
    if fault == "extra_write":
        movement["writes"] = 3
    if fault == "position":
        after = ScrollPosition(-1.0, 50.0, 20.0)
    (tmp_path / "movement.json").write_text(json.dumps(movement))
    if fault == "none":
        assert _verify_interruption(kind, result, initial, after, tmp_path) == movement
    else:
        with pytest.raises(RuntimeError):
            _verify_interruption(kind, result, initial, after, tmp_path)


def test_native_input_is_hosted_only_and_has_no_button_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never move a personal desktop pointer or leave pressed input behind."""
    from scripts.simple_scroll_native import _real_mouse_input  # noqa: PLC0415

    user = Mock()
    loader = Mock(return_value=user)
    monkeypatch.setattr(c, "WinDLL", loader, raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="hosted runner"):
        _real_mouse_input()
    loader.assert_not_called()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    _real_mouse_input()
    user.mouse_event.assert_called_once_with(1, 1, 0, 0, 0)


@pytest.mark.parametrize("failure", ["none", "hit", "paint"])
def test_owned_native_overlay_is_opaque_without_activation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """STATIC needs SS_NOTIFY or Windows returns HTTRANSPARENT for hit testing."""
    from scripts.simple_scroll_native import (  # noqa: PLC0415
        _close_overlay,
        _cover_panel,
    )

    user = Mock()
    user.CreateWindowExW.return_value = 7
    user.GetForegroundWindow.return_value = 1
    user.WindowFromPoint.return_value = 1 if failure == "hit" else 7
    user.DestroyWindow.return_value = True
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", Mock())
    desktop = QImage(500, 500, QImage.Format.Format_RGB32)
    desktop.fill(QColor("white"))
    if failure != "paint":
        painter = QPainter(desktop)
        painter.fillRect(95, 95, 20, 20, QColor("black"))
        painter.end()
    screens = Mock()
    screens.freeze.return_value = FrozenScreen(desktop, Rect(0, 0, 500, 500))
    monkeypatch.setattr("scripts.simple_scroll_native.MssScreenSource", lambda: screens)
    if failure == "none":
        handle = _cover_panel(Rect(20, 20, 300, 300), 1, tmp_path)
        assert handle == 7
        assert (tmp_path / "covered.png").exists()
        user.DestroyWindow.assert_not_called()
        _close_overlay(handle)
    else:
        with pytest.raises(RuntimeError):
            _cover_panel(Rect(20, 20, 300, 300), 1, tmp_path)
    args = user.CreateWindowExW.call_args.args
    assert args[0] == 0x08000088  # Non-activating, tool, topmost.
    assert args[1:4] == ("STATIC", "", 0x90000104)  # Visible popup, black, SS_NOTIFY.
    assert args[4:8] == (95, 95, 20, 20)
    user.DestroyWindow.assert_called_once_with(7)
    if failure != "hit":
        screens.close.assert_called_once()
