"""Portable UIA boundary contracts; actual ABI execution belongs to Windows CI."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import subprocess
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts.simple_scroll_native import (
    _position_start,
    _verify_edge_rows,
    _verify_selected_target,
)

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scrolling import ScrollBarState, ScrollPosition, ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform import scrolling
from verdiclip.platform.scrolling import (
    ScrollSelectionState,
    UiaScrollTarget,
    _Interface,
    _MonitorInfo,
    _ScrollHeader,
)
from verdiclip.platform.windows import WindowInfo


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
    result._display_supported = False
    result._display = None
    result._header = None
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


def header_element(kind: int, bounds: Rect) -> Mock:
    """One visible retained standard control without application-specific data."""
    return Mock(
        spec=_Interface,
        control_type=Mock(return_value=kind),
        bounds=Mock(return_value=bounds),
        offscreen=Mock(return_value=False),
    )


@pytest.mark.parametrize("kind", [50030, 50033, 50028])
def test_header_discovery_leaves_other_root_roles_unchanged(kind: int) -> None:
    """A proven List layout must not add broad tree scanning to other controls."""
    root, walker = header_element(kind, Rect(0, 0, 824, 532)), Mock()
    with ExitStack() as owners:
        assert _ScrollHeader.discover(root, walker, owners, root.bounds()) is None
    walker.relative.assert_not_called()


@pytest.mark.parametrize("scrollbar", [False, True])
def test_header_discovery_uses_exact_standard_roles(*, scrollbar: bool) -> None:
    """The observed25px Header is separate from the optional17px native rail."""
    area = Rect(178, 146, 824, 532)
    root = header_element(50008, area)
    header = header_element(50034, Rect(178, 146, 807 if scrollbar else 824, 25))
    bar = header_element(50014, Rect(985, 146, 17, 532))
    children = [bar, header, None] if scrollbar else [header, None]
    walker = Mock(relative=Mock(side_effect=children))
    with ExitStack() as owners:
        layout = _ScrollHeader.discover(root, walker, owners, area)
    assert layout is not None
    assert layout.bounds == header.bounds()
    calls = walker.relative.call_args_list
    assert calls[0].args[0] is root
    assert calls[0].args[2] == 4
    assert all(call.args[2] == 6 for call in calls[1:])


@pytest.mark.parametrize("change", ["hidden", "heading", "nested"])
def test_header_discovery_never_infers_a_header(change: str) -> None:
    """Hidden controls, text headings and grandchildren are not fixed-band proof."""
    area = Rect(178, 146, 824, 532)
    root = header_element(50008, area)
    kinds = {"hidden": 50034, "heading": 50020, "nested": 50033}
    child = header_element(kinds[change], Rect(178, 146, 824, 25))
    child.offscreen.return_value = change == "hidden"
    walker = Mock(relative=Mock(side_effect=[child, None]))
    with ExitStack() as owners:
        assert _ScrollHeader.discover(root, walker, owners, area) is None
    assert walker.relative.call_count == 2


@pytest.mark.parametrize("eligible", [False, True])
def test_header_discovery_records_raw_bars_without_changing_eligibility(
    caplog: pytest.LogCaptureFixture, *, eligible: bool
) -> None:
    """Log each existing raw rail read, including excluded sizes and hidden bars."""
    area = Rect(178, 146, 824, 532)
    root = header_element(50008, area)
    header = header_element(50034, Rect(178, 146, 807, 25))
    short = header_element(50014, Rect(967, 146, 34, 498))
    horizontal = header_element(50014, Rect(179, 644, 789, 34))
    hidden = header_element(50014, Rect(985, 146, 17, 532))
    hidden.offscreen.return_value = True
    bar = header_element(50014, Rect(985, 146, 17, 532))
    raw = [short, horizontal, hidden]
    if eligible:
        raw.append(bar)
    walker = Mock(relative=Mock(side_effect=[*raw, header, None]))
    with ExitStack() as owners, caplog.at_level("INFO", logger=scrolling.__name__):
        if eligible:
            layout = _ScrollHeader.discover(root, walker, owners, area)
            assert layout is not None
            assert layout.scrollbar is bar
        else:
            with pytest.raises(CaptureError, match="full width"):
                _ScrollHeader.discover(root, walker, owners, area)
    for excluded in (short, horizontal, hidden):
        excluded.bounds.assert_called_once_with()
        assert f"bounds={excluded.bounds.return_value}" in caplog.text
    short.offscreen.assert_not_called()
    horizontal.offscreen.assert_not_called()
    hidden.offscreen.assert_called_once_with()
    assert caplog.text.count("Scrolling direct ScrollBar: role=50014") == len(raw)
    assert caplog.text.count("height_matches=False") == 2
    assert caplog.text.count("height_matches=True") == 1 + eligible
    assert walker.relative.call_count == len(raw) + 2


def footer_parts() -> tuple[Rect, Mock, Mock, Mock]:
    """Use the exact native37 rectangles, not dimensions inferred from scaling."""
    return (
        Rect(343, 282, 654, 425),
        header_element(50034, Rect(343, 282, 1406, 52)),
        header_element(50014, Rect(963, 282, 34, 391)),
        header_element(50014, Rect(343, 673, 620, 34)),
    )


@pytest.mark.parametrize("order", [(0, 1, 2), (0, 2, 1), (1, 0, 2), (2, 1, 0)])
def test_header_footer_discovery_proves_exact_native_partition(
    order: tuple[int, int, int],
) -> None:
    """Direct roles prove the clipped Header, short V rail and exact H footer."""
    area, header, vertical, horizontal = footer_parts()
    root, parts = header_element(50008, area), [header, vertical, horizontal]
    walker = Mock(relative=Mock(side_effect=[*(parts[i] for i in order), None]))
    native = target()
    native._area = area
    with ExitStack() as owners:
        native._header = _ScrollHeader.discover(root, walker, owners, area)
    assert native.header == Rect(343, 282, 620, 52)
    assert native.footer == Rect(343, 673, 620, 34)
    assert native.vertical_scrollbar == Rect(963, 282, 34, 391)
    assert native._header is not None
    assert native._header.scrollbar is vertical
    assert native._header._parts[0][2] == Rect(343, 282, 1406, 52)
    assert walker.relative.call_count == 4
    assert native.footer.top - native.header.bottom == 339
    # The footer corner is outside V, so an exact V mask cannot omit it.
    corner = Rect(963, 673, 34, 34)
    assert native.vertical_scrollbar.intersected(corner).is_empty
    assert native.header.width + native.vertical_scrollbar.width == area.width
    assert native.footer.right == corner.left
    assert native.footer.bottom == corner.bottom == area.bottom


def test_clipped_header_must_cover_the_whole_content_top_strip() -> None:
    """Clip only the measured right overflow from the exact visible left origin."""
    area, header, vertical, horizontal = footer_parts()
    layout = _ScrollHeader(header, vertical, area, footer=horizontal)
    assert layout.bounds == Rect(343, 282, 620, 52)
    assert layout._parts[0][2] == header.bounds.return_value


@pytest.mark.parametrize(
    ("part", "bounds"),
    [
        ("vertical", Rect(962, 282, 35, 391)),
        ("vertical", Rect(964, 282, 33, 391)),
        ("vertical", Rect(963, 283, 34, 390)),
        ("vertical", Rect(963, 282, 34, 390)),
        ("vertical", Rect(963, 282, 34, 392)),
        ("vertical", Rect(963, 282, 34, 425)),
        ("vertical", Rect(963, 282, 35, 391)),
        ("horizontal", Rect(344, 673, 619, 34)),
        ("horizontal", Rect(343, 673, 619, 34)),
        ("horizontal", Rect(343, 673, 621, 34)),
        ("horizontal", Rect(343, 674, 620, 33)),
        ("horizontal", Rect(343, 673, 620, 33)),
        ("horizontal", Rect(343, 707, 620, 0)),
        ("horizontal", Rect(343, 282, 620, 425)),
    ],
)
def test_footer_partition_rejects_any_gap_overlap_or_wrong_edge(
    part: str, bounds: Rect
) -> None:
    """No tolerance or guessed strip accounts for missing viewport pixels."""
    area, header, vertical, horizontal = footer_parts()
    changed = vertical if part == "vertical" else horizontal
    changed.bounds.return_value = bounds
    with pytest.raises(CaptureError, match="partition"):
        _ScrollHeader(header, vertical, area, footer=horizontal)


@pytest.mark.parametrize(
    "bounds",
    [
        Rect(293, 282, 1406, 52),
        Rect(344, 282, 1406, 52),
        Rect(343, 283, 1406, 51),
        Rect(343, 281, 1406, 53),
        Rect(343, 282, 619, 52),
        Rect(343, 282, 1406, 0),
        Rect(343, 282, 1406, 328),
    ],
)
def test_footer_header_still_requires_coverage_and_minimum_body(bounds: Rect) -> None:
    """The fixed bands cannot conceal content gaps or consume the minimum body."""
    area, header, vertical, horizontal = footer_parts()
    header.bounds.return_value = bounds
    with pytest.raises(CaptureError, match="unsupported geometry"):
        _ScrollHeader(header, vertical, area, footer=horizontal)


@pytest.mark.parametrize("change", ["missing", "hidden", "role", "duplicate"])
@pytest.mark.parametrize("part", ["vertical", "horizontal"])
def test_footer_discovery_requires_unique_visible_direct_roles(
    part: str, change: str
) -> None:
    """A shortened rail needs its own exact companion, never an inferred footer."""
    area, header, vertical, horizontal = footer_parts()
    parts = [header, vertical, horizontal]
    changed = vertical if part == "vertical" else horizontal
    if change == "missing":
        parts.remove(changed)
    elif change == "hidden":
        changed.offscreen.return_value = True
    elif change == "role":
        changed.control_type.return_value = 50033
    else:
        parts.append(changed)
    walker = Mock(relative=Mock(side_effect=[*parts, None]))
    with ExitStack() as owners, pytest.raises(CaptureError):
        _ScrollHeader.discover(header_element(50008, area), walker, owners, area)
    assert walker.relative.call_count == len(parts) + 1


@pytest.mark.parametrize("part", ["header", "vertical", "horizontal"])
@pytest.mark.parametrize(
    "change", ["none", "bounds", "role", "hidden", "parent", "identity"]
)
def test_footer_retains_raw_control_bounds_and_identity_guards(
    part: str, change: str
) -> None:
    """Even changes outside the clipped Header reject through retained raw bounds."""
    area, header, vertical, horizontal = footer_parts()
    root = header_element(50008, area)
    layout = _ScrollHeader(header, vertical, area, footer=horizontal)
    changed = {"header": header, "vertical": vertical, "horizontal": horizontal}[part]
    walker = Mock()
    walker.parent.return_value = root
    automation = Mock(same_element=Mock(return_value=True))
    if change == "bounds":
        changed.bounds.return_value = replace(changed.bounds.return_value, width=1407)
    elif change == "role":
        changed.control_type.return_value = 50033
    elif change == "hidden":
        changed.offscreen.return_value = True
    elif change == "parent":
        walker.parent.side_effect = lambda element, _: (
            None if element is changed else root
        )
    elif change == "identity":
        automation.same_element.return_value = False
    if change == "none":
        layout.check(root, automation, walker)
        assert walker.parent.call_count == 3
    else:
        with pytest.raises(CaptureError, match="header or scrollbar changed"):
            layout.check(root, automation, walker)
    walker.relative.assert_not_called()


def test_no_header_exposes_no_footer_or_vertical_mask() -> None:
    """Ordinary targets never acquire inferred fixed bands or restoration masks."""
    native = target()
    assert native.header is native.footer is native.vertical_scrollbar is None


def test_header_discovery_rejects_ambiguity_and_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded discovery cannot assume an unseen second Header does not exist."""
    area = Rect(178, 146, 824, 532)
    root = header_element(50008, area)
    header = header_element(50034, Rect(178, 146, 824, 25))
    walker = Mock(relative=Mock(side_effect=[header, header, None]))
    with ExitStack() as owners, pytest.raises(CaptureError, match="ambiguous"):
        _ScrollHeader.discover(root, walker, owners, area)
    monkeypatch.setattr(scrolling, "_MAX_HEADER_CHILDREN", 1)
    walker.relative.side_effect = None
    walker.relative.return_value = header
    with ExitStack() as owners, pytest.raises(CaptureError, match="limit"):
        _ScrollHeader.discover(root, walker, owners, area)


def test_header_absence_requires_complete_bounded_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unseen Header after the traversal limit cannot become proven absence."""
    area = Rect(178, 146, 824, 532)
    root = header_element(50008, area)
    item = header_element(50007, Rect(178, 177, 600, 24))
    walker = Mock(relative=Mock(return_value=item))
    monkeypatch.setattr(scrolling, "_MAX_HEADER_CHILDREN", 2)
    with ExitStack() as owners, pytest.raises(CaptureError, match="limit"):
        _ScrollHeader.discover(root, walker, owners, area)
    assert walker.relative.call_count == 3


@pytest.mark.parametrize(
    ("bounds", "failed"),
    [
        (Rect(179, 146, 806, 25), "left_mismatch"),
        (Rect(178, 147, 807, 25), "top_mismatch"),
        (Rect(178, 146, 825, 25), "right_overflow"),
        (Rect(178, 146, 807, 0), "nonpositive_height"),
        (Rect(178, 146, 0, 25), "nonpositive_width"),
        (Rect(178, 146, 807, 500), "short_body"),
    ],
)
def test_header_geometry_rejects_unproved_top_band(
    bounds: Rect, failed: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Only exact top-aligned geometry leaving a viable body is supported."""
    with pytest.raises(CaptureError, match="geometry"):
        _ScrollHeader(header_element(50034, bounds), None, Rect(178, 146, 824, 532))
    assert f"header={bounds}" in caplog.text
    assert "captured=Rect(x=178, y=146, width=824, height=532)" in caplog.text
    assert f"failed=['{failed}']" in caplog.text
    assert "header_role=50034" in caplog.text


@pytest.mark.parametrize(
    "failure", [None, CaptureError("Changed"), OSError("Unavailable")]
)
def test_header_rejection_retains_raw_rail_or_marks_failed_read(
    failure: Exception | None, caplog: pytest.LogCaptureFixture
) -> None:
    """The optional diagnostic read cannot change the original rejection."""
    area = Rect(178, 146, 824, 532)
    header = header_element(50034, Rect(178, 146, 1200, 50))
    rail = header_element(50014, Rect(968, 146, 34, 532))
    rail.bounds.side_effect = failure
    with pytest.raises(
        CaptureError, match=r"^The list header has unsupported geometry$"
    ):
        _ScrollHeader(header, rail, area)
    header.bounds.assert_called_once_with()
    rail.bounds.assert_called_once_with()
    assert "scrollbar_role=50014" in caplog.text
    assert f"scrollbar_read_failed={failure is not None}" in caplog.text
    if failure is None:
        assert "scrollbar=Rect(x=968, y=146, width=34, height=532)" in caplog.text
    else:
        assert "scrollbar=None" in caplog.text


@pytest.mark.parametrize("gap", [0, 1, 2])
def test_header_requires_exact_scrollbar_for_uncovered_width(gap: int) -> None:
    """A narrower Header never licenses discarding arbitrary right-side pixels."""
    header = header_element(50034, Rect(178, 146, 807, 25))
    bar = (
        None if gap == 0 else header_element(50014, Rect(985 + gap, 146, 17 - gap, 532))
    )
    with pytest.raises(CaptureError, match="full width"):
        _ScrollHeader(header, bar, Rect(178, 146, 824, 532))


@pytest.mark.parametrize("part", ["header", "scrollbar"])
@pytest.mark.parametrize(
    "change", ["none", "role", "bounds", "hidden", "parent", "identity"]
)
def test_retained_header_guards_role_geometry_visibility_and_parent(
    part: str, change: str
) -> None:
    """Each subsequent guard checks retained controls without rewalking list rows."""
    native = target()
    native._area = Rect(178, 146, 824, 532)
    header = header_element(50034, Rect(178, 146, 807, 25))
    bar = header_element(50014, Rect(985, 146, 17, 532))
    native._header = _ScrollHeader(header, bar, native.area)
    modified = header if part == "header" else bar
    walker = Mock()
    walker.parent.return_value = native._element
    automation = Mock(same_element=Mock(return_value=change != "identity"))
    if change == "role":
        modified.control_type.return_value = 50033
    elif change == "bounds":
        modified.bounds.return_value = Rect(178, 146, 806, 25)
    elif change == "hidden":
        modified.offscreen.return_value = True
    elif change == "parent":
        walker.parent.side_effect = lambda element, _: (
            None if element is modified else native._element
        )
    if change == "none":
        native._header.check(native._element, automation, walker)
        assert native.header == Rect(178, 146, 807, 25)
        assert walker.parent.call_count == 2
    else:
        with pytest.raises(CaptureError, match="header or scrollbar changed"):
            native._header.check(native._element, automation, walker)
    walker.relative.assert_not_called()


def display_target(dpi: int = 192) -> UiaScrollTarget:
    """Observe real adapter logic with mocked read-only Windows display calls."""
    native = target()
    native._display_supported = True
    mock(native._user.MonitorFromWindow).return_value = 10
    mock(native._user.MonitorFromRect).return_value = 10
    mock(native._user.GetDpiForWindow).return_value = dpi
    mock(native._user.GetWindowDpiAwarenessContext).return_value = 8
    mock(native._user.GetThreadDpiAwarenessContext).return_value = 9
    mock(native._user.GetAwarenessFromDpiAwarenessContext).return_value = 2

    def monitor_info(_handle: int, out: c._CArgObject) -> bool:
        value = c.cast(out, c.POINTER(_MonitorInfo)).contents
        assert value.size == c.sizeof(_MonitorInfo)
        value.monitor = w.RECT(0, 0, 1920, 1080)
        return True

    mock(native._user.GetMonitorInfoW).side_effect = monitor_info
    native._display = native._observe_display()
    return native


@pytest.mark.parametrize("dpi", [96, 120, 144, 192])
def test_per_monitor_aware_same_monitor_dpi_is_retained(dpi: int) -> None:
    """Only the selected root's DPI is used for the entire contained viewport."""
    native = display_target(dpi)
    native.check()
    assert native.registration_dpi == dpi
    assert native._display is not None
    assert native._display.monitor == 10
    assert native._display.bounds == Rect(0, 0, 1920, 1080)
    mock(native._user.MonitorFromWindow).assert_called_with(1, 0)
    mock(native._user.GetDpiForWindow).assert_called_with(1)
    assert not any("Set" in call[0] for call in mock(native._user).mock_calls)


@pytest.mark.parametrize("dpi", [0, 95, 193, 240])
def test_unknown_or_out_of_range_dpi_never_widens_registration(dpi: int) -> None:
    """Unknown metadata and unqualified scaling retain the original allowance."""
    native = display_target(dpi)
    native.check()
    assert native.registration_dpi is None


@pytest.mark.parametrize("context", [8, 9])
@pytest.mark.parametrize("awareness", [-1, 0, 1])
def test_unverified_window_or_capture_awareness_never_widens(
    context: int, awareness: int
) -> None:
    """Window DPI alone cannot establish physical registration scale."""
    native = display_target()
    mock(native._user.GetAwarenessFromDpiAwarenessContext).side_effect = lambda value: (
        awareness if value == context else 2
    )
    native._display = native._observe_display()
    native.check()
    assert native.registration_dpi is None


@pytest.mark.parametrize(
    "api",
    [
        "GetDpiForWindow",
        "GetWindowDpiAwarenessContext",
        "GetThreadDpiAwarenessContext",
        "GetAwarenessFromDpiAwarenessContext",
        "MonitorFromWindow",
        "MonitorFromRect",
        "GetMonitorInfoW",
    ],
)
def test_missing_display_api_keeps_conservative_allowance(api: str) -> None:
    """An older/missing API is never replaced by a guessed scale."""
    native = target()
    delattr(native._user, api)
    native._bind_display()
    native._display = native._observe_display()
    native.check()
    assert native.registration_dpi is None
    assert native._display is None


@pytest.mark.parametrize("failure", ["missing_monitor", "other_monitor", "bounds"])
def test_unverified_monitor_geometry_stops_before_a_scroll_write(failure: str) -> None:
    """Real unavailable or inconsistent geometry must not fall back to a guess."""
    native = display_target()
    if failure == "missing_monitor":
        mock(native._user.MonitorFromWindow).return_value = 0
    elif failure == "other_monitor":
        mock(native._user.MonitorFromRect).return_value = 11
    else:
        mock(native._user.GetMonitorInfoW).side_effect = None
        mock(native._user.GetMonitorInfoW).return_value = False
    with pytest.raises(CaptureError, match="monitor"):
        native.move(50.0)
    mock(native._scroll.call).assert_not_called()


def test_viewport_crossing_monitor_boundary_is_rejected() -> None:
    """The largest-intersection monitor is not proof of full containment."""
    native = display_target()
    native._area = Rect(1800, 20, 300, 300)
    native._provider_bounds = native._area
    mock(native._element.bounds).return_value = native._area
    mock(native._viewport).return_value = native._area
    with pytest.raises(CaptureError, match="crosses a monitor boundary"):
        native.move(50.0)
    mock(native._scroll.call).assert_not_called()


@pytest.mark.parametrize(
    "change", ["dpi", "monitor", "bounds", "window_awareness", "capture_awareness"]
)
def test_changed_display_snapshot_stops_before_a_scroll_write(change: str) -> None:
    """A capture never silently changes its scale or monitor allowance mid-run."""
    native = display_target()
    if change == "dpi":
        mock(native._user.GetDpiForWindow).return_value = 144
    elif change == "monitor":
        mock(native._user.MonitorFromWindow).return_value = 11
        mock(native._user.MonitorFromRect).return_value = 11
    elif change == "bounds":
        assert native._display is not None
        native._display = scrolling._ScrollDisplay(
            10, Rect(0, 0, 2000, 1080), 192, 2, 2
        )
    else:
        context = 8 if change == "window_awareness" else 9
        mock(native._user.GetAwarenessFromDpiAwarenessContext).side_effect = (
            lambda value: 1 if value == context else 2
        )
    with pytest.raises(CaptureError, match="monitor or DPI changed"):
        native.move(50.0)
    mock(native._scroll.call).assert_not_called()


def test_dpi_change_after_provider_write_is_detected() -> None:
    """Post-write observations must not report success with an outdated scale."""
    native = display_target()
    mock(native._scroll.number).return_value = 0.0

    def change(*_args: object) -> None:
        mock(native._user.GetDpiForWindow).return_value = 144

    mock(native._scroll.call).side_effect = change
    with pytest.raises(CaptureError, match="monitor or DPI changed"):
        native.move(50.0)
    assert mock(native._scroll.call).call_count == 1


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
    opening.assert_called_once_with(Point(30, 40), window=None, input_tick=None)
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
            native._open(Point(30, 40), window=1, input_tick=12)
            assert native._input_tick == 12
            assert native._window_rect == (0, 0, 500, 500)
            assert native._process == 2
            mock(native._kernel.OpenProcess).assert_called_once_with(0x00100000, 0, 42)
            automation.pointer_result.assert_called_once_with(16, native._owners)
        else:
            with pytest.raises(CaptureError):
                native._open(Point(30, 40), window=1, input_tick=12)
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


@pytest.mark.parametrize(
    "failure", ["none", "hit", "paint", "update", "partial", "outside"]
)
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
    user.UpdateWindow.return_value = failure != "update"
    user.GetForegroundWindow.return_value = 1
    user.WindowFromPoint.return_value = 1 if failure == "hit" else 7
    user.DestroyWindow.return_value = True
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", Mock())
    desktop = QImage(500, 500, QImage.Format.Format_RGB32)
    desktop.fill(QColor("white"))
    assert desktop.copy(20, 20, 300, 300).save(str(tmp_path / "before.png"))
    if failure != "paint":
        painter = QPainter(desktop)
        painter.fillRect(95, 95, 20, 20, QColor(0x12, 0x34, 0x56))
        painter.end()
    if failure == "partial":
        desktop.setPixelColor(95, 95, QColor("white"))
    elif failure == "outside":
        desktop.setPixelColor(20, 20, QColor("red"))
    report: dict[str, object] = {}
    screens = Mock()
    screens.freeze.return_value = FrozenScreen(desktop, Rect(0, 0, 500, 500))
    monkeypatch.setattr("scripts.simple_scroll_native.MssScreenSource", lambda: screens)
    if failure == "none":
        handle = _cover_panel(Rect(20, 20, 300, 300), 1, tmp_path, report)
        assert handle == 7
        assert report["overlay_color"] == "#123456"
        assert (tmp_path / "covered.png").exists()
        user.DestroyWindow.assert_not_called()
        _close_overlay(handle)
    else:
        with pytest.raises(RuntimeError):
            _cover_panel(Rect(20, 20, 300, 300), 1, tmp_path, report)
    args = user.CreateWindowExW.call_args.args
    assert args[0] == 0x08000088  # Non-activating, tool, topmost.
    assert args[1:4] == ("STATIC", "", 0x90000104)  # Visible popup, black, SS_NOTIFY.
    assert args[4:8] == (95, 95, 20, 20)
    user.DestroyWindow.assert_called_once_with(7)
    user.UpdateWindow.assert_called_once_with(7)
    assert user.UpdateWindow.argtypes == (w.HWND,)
    assert user.UpdateWindow.restype is w.BOOL
    if failure not in ("hit", "update"):
        screens.close.assert_called_once()
        assert (tmp_path / "covered.png").exists()


@pytest.mark.parametrize(("left", "top"), [(-1, 10), (10, -1), (31, 10), (10, 31)])
def test_native_cover_oracle_requires_all_400_pixels_in_bounds(
    left: int, top: int
) -> None:
    """A clipped rectangle must never count as a fully observed tiny cover."""
    from scripts.simple_scroll_native import _verify_cover_pixels  # noqa: PLC0415

    before = QImage(50, 50, QImage.Format.Format_RGB32)
    before.fill(QColor("white"))
    with pytest.raises(RuntimeError, match="clipped"):
        _verify_cover_pixels(before, before.copy(), left, top)


@pytest.mark.parametrize("failure", ["none", "timeout", "exit", "missing", "json"])
def test_optional_native_version_diagnostic_preserves_failures(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Metadata failure is reported without retrying or widening its deadline."""
    import subprocess  # noqa: PLC0415

    from scripts.simple_scroll_native import _record_version  # noqa: PLC0415

    monkeypatch.setenv("WINDIR", "C:/Windows")
    errors = {
        "timeout": subprocess.TimeoutExpired("metadata", 10),
        "exit": subprocess.CalledProcessError(1, "metadata"),
        "missing": FileNotFoundError("metadata reader missing"),
    }
    run = Mock(
        return_value=Mock(
            stdout="bad JSON" if failure == "json" else '{"FileVersion":"1.2.3"}'
        ),
        side_effect=errors.get(failure),
    )
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", run)
    report: dict[str, object] = {}
    _record_version(Path("C:/Windows/System32/notepad.exe"), report)
    assert run.call_count == 1
    assert run.call_args.kwargs["timeout"] == 10
    if failure == "none":
        assert report == {"version": {"FileVersion": "1.2.3"}}
    else:
        assert report["version"] is None
        assert report["version_error"]


def test_metadata_timeout_still_attempts_actual_notepad_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed optional read cannot be mistaken for an actual-app result."""
    import subprocess  # noqa: PLC0415

    from scripts.simple_scroll_native import _launch  # noqa: PLC0415

    monkeypatch.setenv("WINDIR", "C:/Windows")
    run = Mock(side_effect=subprocess.TimeoutExpired("metadata", 10))
    process = Mock()
    launch = Mock(return_value=process)
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", run)
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.Popen", launch)
    report: dict[str, object] = {}
    assert _launch("notepad", tmp_path, report) is process
    assert report["version"] is None
    assert "TimeoutExpired" in str(report["version_error"])
    assert launch.call_count == 1
    assert launch.call_args.args[0] == [
        str(Path("C:/Windows/System32/notepad.exe")),
        str(tmp_path / "VerdiClip scrolling check.txt"),
    ]
    assert run.call_count == 1


def _mock_native_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Fake only the entrypoint's native DPI and application construction."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WINDIR", "C:/Windows")
    monkeypatch.setattr("sys.argv", ["native", kind])
    user = Mock()
    user.SetProcessDpiAwarenessContext.return_value = True
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr("scripts.simple_scroll_native.QCoreApplication", Mock())


@pytest.mark.parametrize("failure", ["launch", "case", "report", "cleanup", "scratch"])
def test_native_entry_preserves_real_failures_and_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """Launch/capture errors still fail; even a report error cannot skip cleanup."""
    from scripts.simple_scroll_native import main  # noqa: PLC0415

    _mock_native_entry(monkeypatch, tmp_path, "notepad")
    process = Mock(pid=77)
    process.poll.return_value = None
    launch = Mock(
        return_value=process,
        side_effect=OSError("launch failed") if failure == "launch" else None,
    )
    case = Mock(side_effect=RuntimeError("case failed") if failure == "case" else None)
    stop = Mock()
    if failure == "cleanup":
        stop.side_effect = subprocess.CalledProcessError(
            255, ["taskkill"], output=b"cleanup failed", stderr=b"tree still alive"
        )
        case.side_effect = lambda _kind, _output, report: report.update(success=True)
    monkeypatch.setattr("scripts.simple_scroll_native._launch", launch)
    monkeypatch.setattr("scripts.simple_scroll_native._case", case)
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", stop)
    directory = MagicMock()
    directory.name = str(tmp_path / "scratch")
    temporary = Mock(return_value=directory)
    monkeypatch.setattr("scripts.simple_scroll_native.TemporaryDirectory", temporary)
    if failure == "scratch":
        directory.cleanup.side_effect = OSError("scratch failed")
        case.side_effect = lambda _kind, _output, report: report.update(success=True)
    if failure == "report":
        monkeypatch.setattr(
            Path, "write_text", Mock(side_effect=OSError("report failed"))
        )
    with pytest.raises(
        (RuntimeError, OSError, subprocess.CalledProcessError),
        match="exit status 255" if failure == "cleanup" else f"{failure} failed",
    ):
        main()
    directory.__exit__.assert_not_called()
    if failure == "cleanup":
        directory.cleanup.assert_not_called()
    else:
        directory.cleanup.assert_called_once_with()
    if failure == "launch":
        case.assert_not_called()
        stop.assert_not_called()
    else:
        assert stop.call_count == 1
        assert stop.call_args.args[0][-4:] == ["/PID", "77", "/T", "/F"]
        if failure == "cleanup":
            process.wait.assert_not_called()
        else:
            process.wait.assert_called_once()
            assert 0 < process.wait.call_args.kwargs["timeout"] <= 20
    if failure != "report":
        report = json.loads(
            (tmp_path / "artifacts/simple-scroll/notepad/result.json").read_text()
        )
        assert report["success"] is False
        if failure == "cleanup":
            assert "exit status 255" in report["cleanup_error"]
            assert report["cleanup_stdout"] == "cleanup failed"
            assert report["cleanup_stderr"] == "tree still alive"
        else:
            assert report["error"] == f"{failure} failed"


@pytest.mark.parametrize("changed", ["window", "input"])
def test_expected_selection_is_rejected_before_com_discovery(changed: str) -> None:
    """A delayed helper may not rebase the user's selected root or release tick."""
    native = target()
    native._ole = Mock()
    native._discover = Mock()
    with pytest.raises(CaptureError, match="before scrolling"):
        native._open(
            Point(30, 40),
            window=99 if changed == "window" else 1,
            input_tick=99 if changed == "input" else 12,
        )
    native._ole.CoInitializeEx.assert_not_called()
    mock(native._kernel.OpenProcess).assert_not_called()
    mock(native._discover).assert_not_called()
    mock(native._scroll.call).assert_not_called()


def test_native_entry_accepts_gui_case_without_importing_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The GUI entrypoint supplies its case without a circular harness import."""
    from scripts.simple_scroll_native import main  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["native", "edge-select"])
    user = Mock()
    user.SetProcessDpiAwarenessContext.return_value = True
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    application = Mock()
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", application)
    process = Mock()
    monkeypatch.setattr(
        "scripts.simple_scroll_native._launch", Mock(return_value=process)
    )
    stop = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native._stop_owned_process", stop)
    default_case = Mock(side_effect=AssertionError("Wrong case"))
    monkeypatch.setattr("scripts.simple_scroll_native._case", default_case)
    case = Mock(side_effect=lambda _kind, _output, report: report.update(success=True))
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: 12.0)
    main(gui=True, case=case)
    assert application.return_value.setQuitOnLastWindowClosed.call_args.args == (False,)
    case.assert_called_once()
    default_case.assert_not_called()
    stop.assert_called_once_with(process, window=None, deadline=32.0)
    report = json.loads(
        (tmp_path / "artifacts/simple-scroll/edge-select/result.json").read_text()
    )
    assert report["success"] is True


@pytest.mark.parametrize(
    "state", ["owned", "foreign", "missing", "exited", "post_failed"]
)
def test_native_cleanup_posts_only_to_live_owned_window(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """No unknown/reused HWND can receive an app-close request."""
    from scripts.simple_scroll_native import _request_owned_close  # noqa: PLC0415

    user = Mock()
    process = Mock(pid=42)
    process.poll.return_value = 0 if state == "exited" else None

    def owner(_window: int, out: c._CArgObject) -> int:
        c.cast(out, c.POINTER(w.DWORD)).contents.value = (
            99 if state == "foreign" else 42
        )
        return 0 if state == "missing" else 1

    user.GetWindowThreadProcessId.side_effect = owner
    user.PostMessageW.return_value = state != "post_failed"
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    if state == "post_failed":
        with pytest.raises(OSError, match="Could not close"):
            _request_owned_close(process, 123)
    else:
        assert _request_owned_close(process, 123) is (state == "owned")
    if state in ("owned", "post_failed"):
        user.PostMessageW.assert_called_once_with(123, 0x0010, 0, 0)
    else:
        user.PostMessageW.assert_not_called()
    assert user.PostMessageW.argtypes == (w.HWND, w.UINT, w.WPARAM, w.LPARAM)


@pytest.mark.parametrize("graceful", [True, False])
def test_native_cleanup_grace_and_force_share_existing_budget(
    monkeypatch: pytest.MonkeyPatch, *, graceful: bool
) -> None:
    """One ordinary close precedes the exact owned-PID force fallback if needed."""
    from scripts.simple_scroll_native import _stop_owned_process  # noqa: PLC0415

    process = Mock(pid=42)
    process.poll.return_value = None
    if not graceful:
        process.wait.side_effect = [subprocess.TimeoutExpired("app", 3), 0]
    request = Mock(return_value=True)
    force = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native._request_owned_close", request)
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", force)
    monkeypatch.setattr(
        "scripts.simple_scroll_native.time.monotonic", Mock(side_effect=[0, 0, 3, 13])
    )
    monkeypatch.setenv("WINDIR", "C:/Windows")
    _stop_owned_process(process, window=123)
    request.assert_called_once_with(process, 123)
    assert process.wait.call_args_list[0].kwargs == {"timeout": 3.0}
    if graceful:
        force.assert_not_called()
        assert process.wait.call_count == 1
    else:
        force.assert_called_once()
        assert force.call_args.args[0][-4:] == ["/PID", "42", "/T", "/F"]
        assert force.call_args.kwargs["timeout"] == 10.0
        assert process.wait.call_args_list[1].kwargs == {"timeout": 7.0}


def test_native_cleanup_does_not_force_an_owned_process_that_just_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained process exit wins the race before the numeric PID fallback."""
    from scripts.simple_scroll_native import _stop_owned_process  # noqa: PLC0415

    process = Mock(pid=42)
    process.poll.side_effect = [None, 0]
    request = Mock(return_value=False)
    force = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native._request_owned_close", request)
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", force)
    _stop_owned_process(process, window=123)
    request.assert_called_once_with(process, 123)
    force.assert_not_called()
    process.wait.assert_not_called()


@pytest.mark.parametrize("remaining", [0.0, 0.25, 2.0])
def test_native_cleanup_does_not_reset_a_supplied_shutdown_deadline(
    monkeypatch: pytest.MonkeyPatch, remaining: float
) -> None:
    """An enclosing cleanup owner, not the stop helper, owns remaining time."""
    from scripts.simple_scroll_native import _stop_owned_process  # noqa: PLC0415

    process = Mock(pid=42)
    process.poll.return_value = None
    force = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native.subprocess.run", force)
    monkeypatch.setattr(
        "scripts.simple_scroll_native.time.monotonic", lambda: 20.0 - remaining
    )
    monkeypatch.setenv("WINDIR", "C:/Windows")
    _stop_owned_process(process, deadline=20.0)
    force.assert_called_once()
    assert force.call_args.args[0][-4:] == ["/PID", "42", "/T", "/F"]
    assert force.call_args.kwargs["timeout"] == remaining
    process.wait.assert_called_once_with(timeout=remaining)


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_native_directory_cleanup_retries_only_the_owned_directory(
    monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    """Released handles allow the same public cleanup, with no process action."""
    from scripts.simple_scroll_native import _cleanup_owned_directory  # noqa: PLC0415

    error = PermissionError("owned profile is still in use")
    error.winerror = 32
    directory = Mock()
    directory.cleanup.side_effect = [error] * failures + [None]
    pause = Mock()
    stop = Mock(side_effect=AssertionError("Shutdown must not be repeated"))
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: 18.0)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    monkeypatch.setattr("scripts.simple_scroll_native._stop_owned_process", stop)
    _cleanup_owned_directory(directory, deadline=20.0)
    assert directory.cleanup.call_count == failures + 1
    assert all(
        call.args == () and call.kwargs == {}
        for call in directory.cleanup.call_args_list
    )
    assert pause.call_count == failures
    assert all(call.args == (0.1,) for call in pause.call_args_list)
    stop.assert_not_called()


@pytest.mark.parametrize("remaining", [0.0, 0.25])
def test_native_directory_cleanup_stops_at_the_shared_deadline(
    monkeypatch: pytest.MonkeyPatch, remaining: float
) -> None:
    """A persistent sharing violation stays failed without another budget."""
    from scripts.simple_scroll_native import _cleanup_owned_directory  # noqa: PLC0415

    error = PermissionError("owned profile is still in use")
    error.winerror = 32
    directory = Mock()
    directory.cleanup.side_effect = error
    now = 20.0 - remaining

    def pause(seconds: float) -> None:
        nonlocal now
        now += seconds

    sleep = Mock(side_effect=pause)
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: now)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", sleep)
    with pytest.raises(TimeoutError, match="shutdown deadline") as caught:
        _cleanup_owned_directory(directory, deadline=20.0)
    assert caught.value.__context__ is (error if remaining else None)
    assert now == 20.0
    assert directory.cleanup.call_count == (3 if remaining else 0)
    assert [call.args[0] for call in sleep.call_args_list] == pytest.approx(
        [0.1, 0.1, 0.05] if remaining else []
    )


@pytest.mark.parametrize("winerror", [None, 5, 33])
def test_native_directory_cleanup_rejects_other_errors_immediately(
    monkeypatch: pytest.MonkeyPatch, winerror: int | None
) -> None:
    """Permission errors are not presumed to be transient sharing violations."""
    from scripts.simple_scroll_native import _cleanup_owned_directory  # noqa: PLC0415

    error = PermissionError("owned directory cannot be removed")
    if winerror is not None:
        error.winerror = winerror
    directory = Mock()
    directory.cleanup.side_effect = error
    pause, clock = Mock(), Mock(return_value=0.0)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", clock)
    with pytest.raises(PermissionError) as caught:
        _cleanup_owned_directory(directory, deadline=20.0)
    assert caught.value is error
    directory.cleanup.assert_called_once_with()
    pause.assert_not_called()
    clock.assert_called_once_with()


@pytest.mark.parametrize("elapsed", [20.0, 21.0])
def test_native_directory_cleanup_never_starts_after_expiry(
    monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    """A fully consumed shutdown budget cannot start even the first deletion."""
    from scripts.simple_scroll_native import _cleanup_owned_directory  # noqa: PLC0415

    directory = Mock()
    pause = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: elapsed)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    with pytest.raises(TimeoutError, match="shutdown deadline"):
        _cleanup_owned_directory(directory, deadline=20.0)
    directory.cleanup.assert_not_called()
    pause.assert_not_called()


@pytest.mark.parametrize("finished", [20.0, 21.0])
def test_native_directory_cleanup_rejects_late_success(
    monkeypatch: pytest.MonkeyPatch, finished: float
) -> None:
    """A non-preemptible deletion returning late cannot be accepted as success."""
    from scripts.simple_scroll_native import _cleanup_owned_directory  # noqa: PLC0415

    now = 19.75

    def cleanup() -> None:
        nonlocal now
        now = finished

    directory = Mock()
    directory.cleanup.side_effect = cleanup
    pause = Mock()
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: now)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    with pytest.raises(TimeoutError, match="shutdown deadline"):
        _cleanup_owned_directory(directory, deadline=20.0)
    directory.cleanup.assert_called_once_with()
    pause.assert_not_called()


@pytest.mark.parametrize(
    "outcome", ["released", "locked", "permission", "expired", "slow_success"]
)
def test_native_entry_shares_shutdown_budget_with_directory_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    """Cleanup retry never reruns shutdown or converts a failed removal to pass."""
    from scripts.simple_scroll_native import main  # noqa: PLC0415

    _mock_native_entry(monkeypatch, tmp_path, "edge")
    process = Mock()
    launch = Mock(return_value=process)
    case = Mock(side_effect=lambda _kind, _output, report: report.update(success=True))
    monkeypatch.setattr("scripts.simple_scroll_native._launch", launch)
    monkeypatch.setattr("scripts.simple_scroll_native._case", case)
    error = PermissionError("owned profile could not be removed")
    error.winerror = 5 if outcome == "permission" else 32
    directory = MagicMock()
    directory.name = str(tmp_path / "owned-scratch")
    directory.cleanup.side_effect = [error, None] if outcome == "released" else error
    temporary = Mock(return_value=directory)
    monkeypatch.setattr("scripts.simple_scroll_native.TemporaryDirectory", temporary)
    now = 0.0

    def shutdown(*_args: object, **_kwargs: object) -> None:
        nonlocal now
        now = 20.0 if outcome == "expired" else 19.75

    def pause(seconds: float) -> None:
        nonlocal now
        now += seconds

    if outcome == "slow_success":
        directory.cleanup.side_effect = lambda: pause(1.0)
    stop = Mock(side_effect=shutdown)
    monkeypatch.setattr("scripts.simple_scroll_native._stop_owned_process", stop)
    monkeypatch.setattr("scripts.simple_scroll_native.time.monotonic", lambda: now)
    monkeypatch.setattr("scripts.simple_scroll_native.time.sleep", pause)
    if outcome == "released":
        main()
    else:
        failure = PermissionError if outcome == "permission" else TimeoutError
        with pytest.raises(failure) as caught:
            main()
        if outcome == "permission":
            assert caught.value is error
    temporary.assert_called_once_with(prefix="verdiclip-scroll-test-", delete=False)
    assert launch.call_args.args[1] == Path(directory.name)
    stop.assert_called_once_with(process, window=None, deadline=20.0)
    directory.__exit__.assert_not_called()
    assert (
        directory.cleanup.call_count
        == {
            "released": 2,
            "locked": 3,
            "permission": 1,
            "expired": 0,
            "slow_success": 1,
        }[outcome]
    )
    assert now <= 20.0 or outcome == "slow_success"
    report = json.loads(
        (tmp_path / "artifacts/simple-scroll/edge/result.json").read_text()
    )
    assert report["success"] is (outcome == "released")
    if outcome != "released":
        assert report["cleanup_error"] == report["error"]
        if outcome == "permission":
            assert report["error"] == str(error)
        else:
            assert "shutdown deadline" in report["error"]


def test_gui_selection_checks_are_read_only_and_do_not_enter_com(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GUI observes HWND/input only and never initializes UI Automation."""
    user = Mock()
    monkeypatch.setattr(scrolling.sys, "platform", "win32")
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr(scrolling, "Win32WindowApi", Mock())
    native = scrolling.ScrollSelectionState()
    assert user.WindowFromPoint.argtypes == (w.POINT,)
    assert user.GetAncestor.restype is w.HWND
    assert user.SetForegroundWindow.argtypes == (w.HWND,)
    assert user.SetForegroundWindow.restype is w.BOOL

    def input_info(pointer: c.c_void_p) -> bool:
        c.cast(pointer, c.POINTER(scrolling._LastInput)).contents.tick = 123
        return True

    user.GetLastInputInfo.side_effect = input_info
    user.GetForegroundWindow.return_value = 456
    user.GetAncestor.return_value = 456
    native.check(Point(2.2, 3.8), 456, 123)
    user.SetForegroundWindow.assert_not_called()
    point = user.WindowFromPoint.call_args.args[0]
    assert (point.x, point.y) == (2, 4)
    locator = Mock()
    monkeypatch.setattr(scrolling, "WindowLocator", Mock(return_value=locator))
    assert native.windows() is locator.visible_windows.return_value
    user.GetLastInputInfo.side_effect = None
    user.GetLastInputInfo.return_value = False
    with pytest.raises(CaptureError, match="observe user input"):
        native.tick()


@pytest.mark.parametrize("changed", ["input", "foreground", "hit"])
def test_gui_selection_state_rejects_handoff_changes(
    changed: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The parent cannot rebase the completed selection to a newer input/window."""
    native = object.__new__(ScrollSelectionState)
    native._user = Mock()
    native.tick = Mock(return_value=1 if changed != "input" else 2)
    native._user.GetForegroundWindow.return_value = 3 if changed != "foreground" else 4
    native._user.GetAncestor.return_value = 3 if changed != "hit" else 4
    with pytest.raises(CaptureError, match="changed"):
        native.check(Point(2, 4), 3, 1)
    assert f"input_changed={changed == 'input'}" in caplog.text
    assert f"foreground_changed={changed == 'foreground'}" in caplog.text
    assert f"hit_changed={changed == 'hit'}" in caplog.text
    assert "expected_tick=1" in caplog.text
    assert "expected_window=3" in caplog.text


def test_gui_selection_rejects_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported hosts fail explicitly before loading any Windows DLL."""
    monkeypatch.setattr(scrolling.sys, "platform", "linux")
    with pytest.raises(CaptureError, match="requires Windows"):
        scrolling.ScrollSelectionState()


def selection_state() -> tuple[ScrollSelectionState, WindowInfo]:
    """Native GUI adapter with stable frozen geometry and selected point root."""
    native = object.__new__(ScrollSelectionState)
    native._user = Mock()
    native._frames = Mock()
    native.tick = Mock(return_value=12)
    native._user.GetForegroundWindow.return_value = 7
    native._user.GetAncestor.return_value = 9
    window = WindowInfo(9, "Selected document", Rect(0, 0, 500, 400))
    native._frames.frame_bounds.return_value = window.bounds
    return native, window


@pytest.mark.parametrize("foreground", [None, 0, 7, 9])
def test_selection_observation_allows_pending_focus_but_is_read_only(
    foreground: int | None,
) -> None:
    """NULL, previous focus and completion can be observed without a write."""
    native, window = selection_state()
    mock(native._user).GetForegroundWindow.return_value = foreground
    assert native.observe(Point(20, 30), window, 12) == (foreground or 0)
    mock(native._user).SetForegroundWindow.assert_not_called()
    mock(native._frames.frame_bounds).assert_called_once_with(9)


@pytest.mark.parametrize("change", ["input", "hit", "geometry"])
def test_selection_observation_preserves_original_input_hit_and_geometry(
    change: str,
) -> None:
    """No foreground settling can make changed targets or user input eligible."""
    native, window = selection_state()
    if change == "input":
        native.tick = Mock(return_value=13)
    elif change == "hit":
        mock(native._user).GetAncestor.return_value = 10
    else:
        mock(native._frames.frame_bounds).return_value = Rect(1, 0, 500, 400)
    with pytest.raises(CaptureError, match=r"changed|moved or resized"):
        native.observe(Point(20, 30), window, 12)
    mock(native._user).SetForegroundWindow.assert_not_called()


@pytest.mark.parametrize("foreground", [0, 7])
def test_selection_activation_uses_exact_chosen_window_once(foreground: int) -> None:
    """The ordinary request uses no synthetic input or thread-input attachment."""
    native, window = selection_state()
    mock(native._user).GetForegroundWindow.return_value = foreground
    native.activate(Point(20, 30), window, 12, foreground)
    mock(native._user).SetForegroundWindow.assert_called_once_with(9)
    assert {call[0] for call in mock(native._user).mock_calls} == {
        "WindowFromPoint",
        "GetForegroundWindow",
        "GetAncestor",
        "SetForegroundWindow",
    }


def test_selection_activation_skips_target_already_in_foreground() -> None:
    """Asynchronous normal focus restoration wins without a redundant request."""
    native, window = selection_state()
    mock(native._user).GetForegroundWindow.return_value = 9
    native.activate(Point(20, 30), window, 12, 7)
    mock(native._user).SetForegroundWindow.assert_not_called()


@pytest.mark.parametrize("change", ["input", "hit", "geometry", "foreground"])
def test_selection_activation_rechecks_every_guard_before_request(change: str) -> None:
    """An interleaved change after the prior observation prevents activation."""
    native, window = selection_state()
    assert native.observe(Point(20, 30), window, 12) == 7
    if change == "input":
        native.tick = Mock(return_value=13)
    elif change == "hit":
        mock(native._user).GetAncestor.return_value = 10
    elif change == "geometry":
        mock(native._frames.frame_bounds).return_value = Rect(0, 0, 501, 400)
    else:
        mock(native._user).GetForegroundWindow.return_value = 8
    with pytest.raises(CaptureError, match=r"changed|moved or resized"):
        native.activate(Point(20, 30), window, 12, 7)
    mock(native._user).SetForegroundWindow.assert_not_called()


def test_selection_activation_refusal_is_not_bypassed() -> None:
    """A rejected ordinary activation has no fallback or second native write."""
    native, window = selection_state()
    mock(native._user).SetForegroundWindow.return_value = False
    with pytest.raises(CaptureError, match="did not allow activation"):
        native.activate(Point(20, 30), window, 12, 7)
    mock(native._user).SetForegroundWindow.assert_called_once_with(9)


@pytest.mark.parametrize("change", ["input", "foreground", "hit"])
def test_activation_checks_state_after_the_potentially_slow_frame_read(
    change: str,
) -> None:
    """Changes during DWM observation must prevent the subsequent focus write."""
    native, window = selection_state()

    def changed_during_bounds(_handle: int) -> Rect:
        if change == "input":
            native.tick = Mock(return_value=13)
        elif change == "foreground":
            mock(native._user).GetForegroundWindow.return_value = 8
        else:
            mock(native._user).GetAncestor.return_value = 10
        return window.bounds

    mock(native._frames.frame_bounds).side_effect = changed_during_bounds
    with pytest.raises(CaptureError, match="changed"):
        native.activate(Point(20, 30), window, 12, 7)
    mock(native._user).SetForegroundWindow.assert_not_called()


def native_target() -> UiaScrollTarget:
    """A retained standard Header rail backed by read-only typed native metadata."""
    item = target()
    header = header_element(50034, Rect(20, 20, 283, 25))
    rail = header_element(50014, Rect(303, 20, 17, 300))
    rail.native_handle.return_value = 101
    item._header = _ScrollHeader(header, rail, item.area)
    item._scrollbar_identity = None
    item.check = Mock()
    item._automation = Mock()
    item._automation.pointer_result.return_value = rail
    item._automation.same_element.return_value = True
    item._walker = Mock()
    item._walker.parent.return_value = item._element
    mock(item._user.GetParent).return_value = 102

    def pid(_handle: int, result: c._CArgObject) -> int:
        c.cast(result, c.POINTER(w.DWORD)).contents.value = 104
        return 1

    def read(_handle: int, _bar: int, result: c._CArgObject) -> bool:
        info = c.cast(result, c.POINTER(scrolling._ScrollInfo)).contents
        assert info.size == c.sizeof(scrolling._ScrollInfo)
        assert info.mask == 7
        info.minimum, info.maximum, info.page, info.position = 0, 15999, 1787, 7106
        return True

    mock(item._user.GetWindowThreadProcessId).side_effect = pid
    mock(item._user.GetScrollInfo).side_effect = read
    item.position = Mock(
        return_value=ScrollPosition(-1, 100 * 7106 / 14213, 100 * 1787 / 15999)
    )
    return item


def test_native_snapshot_reuses_retained_rail_and_typed_sb_ctl() -> None:
    """No new whole-tree discovery or alternate target is needed for native proof."""
    item = native_target()
    state = item.scrollbar_state()
    assert state == ScrollBarState(101, 102, 1, 104, 0, 15999, 1787, 7106)
    assert item.scrollbar_state() == state
    assert mock(item._user.GetScrollInfo).call_args.args[:2] == (101, 2)
    mock(item._walker.relative).assert_not_called()
    assert mock(item.check).call_count == 4


def test_footer_native_snapshot_uses_only_the_retained_short_vertical_rail() -> None:
    """The horizontal bar cannot replace the V identity in the native-unit proof."""
    item = native_target()
    area, header, vertical, horizontal = footer_parts()
    vertical.native_handle.return_value = 101
    horizontal.native_handle.return_value = 201
    item._area = area
    item._header = _ScrollHeader(header, vertical, area, footer=horizontal)
    mock(item._automation.pointer_result).return_value = vertical
    assert item.scrollbar_state() == ScrollBarState(
        101, 102, 1, 104, 0, 15999, 1787, 7106
    )
    assert item.vertical_scrollbar == Rect(963, 282, 34, 391)
    assert mock(item._user.GetScrollInfo).call_args.args[:2] == (101, 2)
    horizontal.native_handle.assert_not_called()
    mock(item._walker.relative).assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "handle",
        "parent",
        "root",
        "pid",
        "range",
        "identity",
        "missing",
        "failure",
        "mapping",
        "window",
    ],
)
def test_native_snapshot_rejects_changed_evidence(change: str) -> None:
    """Native identity, UIA identity and arithmetic all remain mandatory."""
    item = native_target()
    original = item.scrollbar_state()
    assert original is not None
    assert item._header is not None
    assert item._header.scrollbar is not None
    rail = item._header.scrollbar
    if change in ("handle", "missing"):
        mock(rail.native_handle).return_value = 105 if change == "handle" else None
    elif change == "parent":
        mock(item._user.GetParent).return_value = 105
    elif change == "root":
        mock(item._user.GetAncestor).return_value = 105
    elif change == "pid":

        def changed_pid(handle: int, result: c._CArgObject) -> int:
            c.cast(result, c.POINTER(w.DWORD)).contents.value = handle
            return 1

        mock(item._user.GetWindowThreadProcessId).side_effect = changed_pid
    elif change == "range":
        item._scrollbar_identity = replace(original, maximum=16000)
    elif change == "identity":
        mock(item._automation.same_element).return_value = False
    elif change == "failure":
        mock(item._user.GetScrollInfo).side_effect = None
        mock(item._user.GetScrollInfo).return_value = False
    elif change == "mapping":
        mock(item.position).return_value = ScrollPosition(-1, 50, 11)
    else:
        mock(item._user.IsWindow).return_value = False
    with pytest.raises(CaptureError):
        item.scrollbar_state()


@pytest.mark.parametrize("kind", ["no-header", "no-rail", "no-hwnd"])
def test_native_snapshot_absent_for_unsupported_rail(kind: str) -> None:
    """Absent native support leaves ordinary providers on their strict tuple path."""
    item = native_target()
    assert item._header is not None
    if kind == "no-header":
        item._header = None
    elif kind == "no-rail":
        item._header.scrollbar = None
    else:
        assert item._header.scrollbar is not None
        mock(item._header.scrollbar.native_handle).return_value = None
    assert item.scrollbar_state() is None
    mock(item._user.GetScrollInfo).assert_not_called()


def test_native_rail_hit_requires_bounded_same_scrollbar_ancestor() -> None:
    """Unknown hit trees cannot prove ownership by coincident rail coordinates."""
    item = native_target()
    hit = header_element(50000, Rect(303, 20, 17, 300))
    mock(item._automation.pointer_result).return_value = hit
    mock(item._walker.parent).return_value = hit
    with pytest.raises(CaptureError, match="cannot be verified"):
        item.scrollbar_state()
    assert mock(item._walker.parent).call_count == scrolling._MAX_ANCESTORS
    mock(item._user.GetScrollInfo).assert_not_called()


def test_native_rail_missing_hit_parent_is_unsupported() -> None:
    """A hit with no ancestor rail cannot silently fall back after native discovery."""
    item = native_target()
    mock(item._automation.pointer_result).return_value = header_element(
        50000, item.area
    )
    mock(item._walker.parent).return_value = None
    with pytest.raises(CaptureError, match="cannot be verified"):
        item.scrollbar_state()
    mock(item._user.GetScrollInfo).assert_not_called()


def test_native_handle_must_survive_final_read() -> None:
    """The native identity is still retained after potentially blocking metadata IO."""
    item = native_target()
    assert item._header is not None
    assert item._header.scrollbar is not None
    mock(item._header.scrollbar.native_handle).side_effect = [101, 105]
    with pytest.raises(CaptureError, match="during observation"):
        item.scrollbar_state()


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "client",
        "mapping",
        "owner",
        "input-before",
        "input-after",
        "no-handle",
        "no-dpi",
    ],
)
def test_header_failure_context_is_bounded_checked_numeric_evidence(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Read the actual selected-window DPI and unclipped native client once."""
    native = target()
    native._native_handle = None if failure == "no-handle" else 11
    native._check_window = Mock()
    if failure.startswith("input-"):
        mock(native._check_window).side_effect = (
            [CaptureError("Changed input")]
            if failure == "input-before"
            else [None, CaptureError("Changed input")]
        )
    mock(native._user.GetAncestor).return_value = 99 if failure == "owner" else 1
    mock(native._user).GetDpiForWindow = (
        None if failure == "no-dpi" else Mock(return_value=192)
    )

    def client(_handle: int, output: c._CArgObject) -> bool:
        rectangle = c.cast(output, c.POINTER(w.RECT)).contents
        rectangle.left, rectangle.top, rectangle.right, rectangle.bottom = (
            0,
            0,
            950,
            600,
        )
        return failure != "client"

    def screen(_handle: int, output: c._CArgObject) -> bool:
        point = c.cast(output, c.POINTER(w.POINT)).contents
        point.x += 300
        point.y += 200
        return failure != "mapping"

    mock(native._user.GetClientRect).side_effect = client
    mock(native._user.ClientToScreen).side_effect = screen
    client_signature = (w.HWND, c.POINTER(w.RECT))
    screen_signature = (w.HWND, c.POINTER(w.POINT))
    native._user.GetClientRect.argtypes = client_signature
    native._user.ClientToScreen.argtypes = screen_signature
    native._log_header_context()
    assert native._user.GetClientRect.argtypes == client_signature
    assert native._user.ClientToScreen.argtypes == screen_signature
    assert "provider=Rect(x=20, y=20, width=300, height=300)" in caplog.text
    assert "captured=Rect(x=20, y=20, width=300, height=300)" in caplog.text
    assert "selected_window=1" in caplog.text
    success = failure in ("none", "no-handle", "no-dpi")
    assert f"observation_valid={success}" in caplog.text
    if success:
        assert mock(native._check_window).call_count == 2
        assert f"window_dpi={None if failure == 'no-dpi' else 192}" in caplog.text
        if failure == "no-handle":
            assert "native_client=None" in caplog.text
            mock(native._user.GetClientRect).assert_not_called()
        else:
            assert (
                "native_client=Rect(x=300, y=200, width=950, height=600)" in caplog.text
            )
            mock(native._user.GetClientRect).assert_called_once_with(
                11, mock(native._user.GetClientRect).call_args.args[1]
            )
            assert mock(native._user.ClientToScreen).call_count == 2
        if failure != "no-dpi":
            mock(native._user.GetDpiForWindow).assert_called_once_with(1)
    else:
        assert "native_client=None" in caplog.text
        assert "window_dpi=None" in caplog.text
    mock(native._scroll.call).assert_not_called()
    mock(native._screens.freeze).assert_not_called()


@pytest.mark.parametrize("context_failure", [False, True])
def test_failed_header_discovery_logs_context_without_replacing_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    context_failure: bool,
) -> None:
    """The original helper failure survives even if diagnostic reads also fail."""
    native = target()
    native._ole = Mock()
    native._discover = Mock(return_value=(native._element, native._scroll))
    automation = Mock()
    monkeypatch.setattr(scrolling, "_Interface", Mock(return_value=automation))
    monkeypatch.setattr(
        _ScrollHeader,
        "discover",
        Mock(side_effect=CaptureError("The list header has unsupported geometry")),
    )
    native._header_context = Mock(
        return_value=(Rect(1, 2, 300, 400), 192),
        side_effect=OSError("Read failed") if context_failure else None,
    )
    try:
        with pytest.raises(
            CaptureError, match=r"^The list header has unsupported geometry$"
        ):
            native._open(Point(30, 40), window=1, input_tick=12)
    finally:
        native._owners.close()
    mock(native._header_context).assert_called_once_with()
    assert f"observation_valid={not context_failure}" in caplog.text
    mock(native._scroll.call).assert_not_called()


class ListStepFixture:
    """Real retained layout and item references with read-only native doubles."""

    def __init__(self) -> None:
        self.target = native_target()
        area, header, rail, footer = footer_parts()
        self.target._area = area
        self.target._header = _ScrollHeader(header, rail, area, footer=footer)
        mock(self.target._element.control_type).return_value = 50008
        self.native = ScrollBarState(101, 102, 1, 104, 0, 15999, 597, 0)
        self.position = ScrollPosition(0, 0, 100 * 597 / 15999)
        self.target.position = Mock(side_effect=lambda: self.position)
        self.target.scrollbar_state = Mock(side_effect=lambda: self.native)
        self.items = [header_element(50007, Rect(371, y, 1350, 48)) for y in (514, 570)]
        self.releases: list[Mock] = []
        self.temporary_releases: list[Mock] = []
        self.children = [header, rail, footer, *self.items]
        self.target._walker = Mock()
        self.target._automation = Mock()
        mock(self.target._automation.same_element).side_effect = lambda a, b: a is b
        mock(self.target._walker.parent).return_value = self.target._element
        mock(self.target._walker.relative).side_effect = self.relative
        mock(self.target._automation.pointer_result).side_effect = self.hit
        for item in self.items:
            item.pointer_result.side_effect = self.retain(item)
        self.check = Mock(side_effect=self.layout_check)

    def layout_check(self) -> None:
        """Keep the actual retained Header/rail checker active during evidence."""
        assert self.target._header is not None
        self.target._header.check(
            self.target._element, self.target._automation, self.target._walker
        )

    def relative(self, child: _Interface, owners: ExitStack, slot: int) -> Mock | None:
        """Enumerate bounded direct children, retaining only temporary references."""
        index = 0 if slot == 4 else self.children.index(mock(child)) + 1
        if index == len(self.children):
            return None
        release = Mock()
        self.temporary_releases.append(release)
        owners.callback(release)
        return self.children[index]

    def hit(self, slot: int, _owners: ExitStack, *args: object) -> Mock:
        """Fresh point lookup returns the raw item, never a saved oracle verdict."""
        assert slot == 7
        assert len(args) == 1
        prefix = args[0]
        assert isinstance(prefix, tuple)
        point = prefix[1]
        assert isinstance(point, w.POINT)
        for item in self.items:
            raw = item.bounds.return_value
            assert isinstance(raw, Rect)
            if raw.contains(Point(point.x, point.y)):
                return item
        return header_element(50033, Rect(0, 0, 1, 1))

    def retain(self, item: Mock) -> object:
        """Install one QI-owned reference with observable cleanup."""

        def acquire(slot: int, owners: ExitStack, *_args: object) -> Mock:
            assert slot == 0
            release = Mock()
            self.releases.append(release)
            owners.callback(release)
            return item

        return acquire

    def capability(self) -> scrolling._ListSteps:
        """Select the production optional capability without issuing a write."""
        steps = self.target.list_steps(self.position, self.check)
        assert steps is not None
        return steps

    def advance(self, delta: float = 175, *, terminal: bool = False) -> None:
        """Change provider observations only, never invoke a production move."""
        self.native = replace(
            self.native,
            position=15388 if terminal else self.native.position + 298,
            page=612 if terminal else self.native.page,
        )
        self.position = ScrollPosition(
            0,
            self.native.percent(self.native.position),
            100 * self.native.page / (self.native.maximum - self.native.minimum),
        )
        for item in self.items:
            item.bounds.return_value = item.bounds.return_value.translated(
                Point(0, -delta)
            )


@pytest.mark.parametrize("delta", [175, 167, 176])
def test_list_steps_measure_raw_items_not_native_unit_predictions(delta: int) -> None:
    """Equal native increments may produce distinct, exactly proven pixel deltas."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    assert steps.body == Rect(343, 334, 620, 339)
    steps.begin(fixture.position)
    assert len(fixture.releases) == 2
    assert all(release.call_count == 1 for release in fixture.temporary_releases)
    assert all(release.call_count == 0 for release in fixture.releases)
    calls = mock(fixture.target._walker.relative).call_count
    fixture.advance(delta)
    assert steps.finish(fixture.position) == delta
    assert mock(fixture.target._walker.relative).call_count == calls
    assert all(release.call_count == 1 for release in fixture.releases)
    steps.close()
    fixture.target._owners.close()
    assert all(release.call_count == 1 for release in fixture.releases)
    mock(fixture.target._scroll.call).assert_not_called()


@pytest.mark.parametrize("reason", ["header", "rail", "role", "horizontal", "native"])
def test_list_steps_unsupported_selection_does_not_scan_items(reason: str) -> None:
    """Optional structural absence preserves the existing generic route."""
    fixture = ListStepFixture()
    fixture.check.side_effect = None
    assert fixture.target._header is not None
    if reason == "header":
        fixture.target._header = None
    elif reason == "rail":
        fixture.target._header.scrollbar = None
    elif reason == "role":
        mock(fixture.target._element.control_type).return_value = 50030
    elif reason == "horizontal":
        fixture.position = replace(fixture.position, horizontal=20)
    else:
        mock(fixture.target.scrollbar_state).side_effect = None
        mock(fixture.target.scrollbar_state).return_value = None
    assert fixture.target.list_steps(fixture.position, fixture.check) is None
    mock(fixture.target._walker.relative).assert_not_called()
    mock(fixture.target._scroll.call).assert_not_called()


@pytest.mark.parametrize("reason", ["position", "mapping", "native-error", "check"])
def test_list_steps_unsafe_selection_never_returns_unsupported(reason: str) -> None:
    """An eligible-looking route with failed evidence cannot silently fall back."""
    fixture = ListStepFixture()
    initial = fixture.position
    if reason == "position":
        fixture.position = replace(initial, vertical=1)
    elif reason == "mapping":
        fixture.native = replace(fixture.native, page=599)
    elif reason == "native-error":
        mock(fixture.target.scrollbar_state).side_effect = CaptureError("native")
    else:
        fixture.check.side_effect = CaptureError("input")
    with pytest.raises(CaptureError):
        fixture.target.list_steps(initial, fixture.check)
    mock(fixture.target._walker.relative).assert_not_called()


@pytest.mark.parametrize("phase", ["begin", "finish"])
@pytest.mark.parametrize(
    "reason", ["role", "hidden", "parent", "identity", "hit-shape"]
)
def test_list_steps_reject_lost_item_evidence(phase: str, reason: str) -> None:
    """Fresh hits and exact direct parent identity are required on both sides."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    if phase == "finish":
        steps.begin(fixture.position)
        fixture.advance()
    item = fixture.items[0]
    if reason == "role":
        item.control_type.return_value = 50020
    elif reason == "hidden":
        item.offscreen.return_value = True
    elif reason == "parent":
        old_parent = fixture.target._walker.parent
        fixture.target._walker.parent = Mock(
            side_effect=lambda element, owners: (
                header_element(50008, fixture.target.area)
                if element is item
                else old_parent(element, owners)
            )
        )
    else:
        other = header_element(50007, item.bounds.return_value)
        if reason == "hit-shape":
            other.bounds.return_value = other.bounds.return_value.translated(
                Point(0, 1)
            )
            mock(fixture.target._automation.same_element).side_effect = lambda a, b: (
                a is b or (a is item and b is other)
            )
        original_hit = fixture.hit
        mock(fixture.target._automation.pointer_result).side_effect = lambda *args: (
            other if original_hit(*args) is item else original_hit(*args)
        )
    with pytest.raises(CaptureError):
        steps.begin(fixture.position) if phase == "begin" else steps.finish(
            fixture.position
        )
    steps.close()
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize(
    "raw",
    [
        Rect(342, 514, 1350, 48),  # left clipping
        Rect(371, 333, 1350, 48),  # top clipping
        Rect(371, 626, 1350, 48),  # bottom clipping
        Rect(962.75, 514, 1350, 48),  # no strictly interior rounded hit
        Rect(371, 514, 0, 48),
        Rect(371, 514, 1350, 0),
    ],
)
def test_list_steps_reject_other_clipping_and_slivers(raw: Rect) -> None:
    """No guessed left inset, vertical clipping or unusable hit point is admitted."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    fixture.items[0].bounds.return_value = raw
    with pytest.raises(CaptureError, match="bounded item pair"):
        steps.begin(fixture.position)
    assert not fixture.releases


@pytest.mark.parametrize("reason", ["overlap", "duplicate", "short", "unbounded"])
def test_list_steps_require_bounded_distinct_disjoint_pair(reason: str) -> None:
    """Scanning never realizes more items or rescans until a pair can be accepted."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    if reason == "overlap":
        fixture.items[1].bounds.return_value = Rect(371, 540, 1350, 48)
    elif reason == "duplicate":
        mock(fixture.target._automation.same_element).side_effect = lambda a, b: (
            a is b or (a is fixture.items[0] and b is fixture.items[1])
        )
    elif reason == "short":
        fixture.children.remove(fixture.items[1])
    else:
        mock(fixture.target._walker.relative).side_effect = None
        mock(fixture.target._walker.relative).return_value = fixture.items[0]
    with pytest.raises(CaptureError):
        steps.begin(fixture.position)
    assert mock(fixture.target._walker.relative).call_count <= 65
    assert not fixture.releases
    assert all(release.call_count == 1 for release in fixture.temporary_releases)


@pytest.mark.parametrize("delta", [0, -1, 175.5])
def test_list_steps_require_positive_integral_delta(delta: float) -> None:
    """Item coordinates cannot authorize approximate pixel movement."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance(delta)
    with pytest.raises(CaptureError, match="pixel displacement"):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize("change", ["x", "width", "height", "delta"])
def test_list_steps_reject_raw_shape_or_displacement_disagreement(change: str) -> None:
    """The clipped visible width never conceals a changed raw rectangle."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    item = fixture.items[0]
    raw = item.bounds.return_value
    item.bounds.return_value = replace(
        raw,
        **{
            ("y" if change == "delta" else change): getattr(
                raw, "y" if change == "delta" else change
            )
            + 1
        },
    )
    with pytest.raises(CaptureError):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize(
    "change", ["horizontal", "range", "page", "native-lost", "role"]
)
def test_list_steps_reject_nonterminal_state_changes(change: str) -> None:
    """Native minimum/maximum, page, H and standard List role stay retained."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    if change == "horizontal":
        fixture.position = replace(fixture.position, horizontal=1)
    elif change == "range":
        fixture.native = replace(fixture.native, maximum=16000)
    elif change == "page":
        fixture.native = replace(fixture.native, page=612)
        fixture.position = replace(fixture.position, view_size=100 * 612 / 15999)
    elif change == "native-lost":
        mock(fixture.target.scrollbar_state).side_effect = None
        mock(fixture.target.scrollbar_state).return_value = None
    else:
        mock(fixture.target._element.control_type).return_value = 50030
    with pytest.raises(CaptureError):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize("part", [0, 1, 2])
def test_list_steps_reject_header_footer_and_rail_geometry_changes(part: int) -> None:
    """All layout parts remain exact; clipping cannot hide changed raw Header."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    fixture.children[part].bounds.return_value = fixture.children[
        part
    ].bounds.return_value.translated(Point(1, 0))
    with pytest.raises(CaptureError):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


def test_list_steps_terminal_page_change_requires_maximum_and_late_identity() -> None:
    """A terminal context flag does not replace fresh native endpoint evidence."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance(175, terminal=True)
    assert steps.finish(fixture.position, terminal=True) == 175
    steps.confirm_terminal(fixture.position)
    fixture.native = replace(fixture.native, page=613, position=15387)
    fixture.position = replace(fixture.position, view_size=100 * 613 / 15999)
    with pytest.raises(CaptureError, match="unconfirmed"):
        steps.confirm_terminal(fixture.position)


@pytest.mark.parametrize("terminal", [False, True])
def test_list_steps_terminal_flag_never_authorizes_nonmaximum(
    *, terminal: bool
) -> None:
    """Normalization midsequence fails with or without the terminal context flag."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance(175, terminal=True)
    fixture.native = replace(fixture.native, position=1000)
    fixture.position = replace(fixture.position, vertical=fixture.native.percent(1000))
    with pytest.raises(CaptureError):
        steps.finish(fixture.position, terminal=terminal)


def test_list_steps_original_position_is_not_an_item_anchor() -> None:
    """An initial nonzero V may move to top before the first retained pair."""
    fixture = ListStepFixture()
    fixture.native = replace(fixture.native, position=500)
    fixture.position = replace(fixture.position, vertical=fixture.native.percent(500))
    steps = fixture.capability()
    assert not fixture.releases
    fixture.native = replace(fixture.native, position=0)
    fixture.position = replace(fixture.position, vertical=0)
    steps.begin(fixture.position)
    fixture.advance()
    assert steps.finish(fixture.position) == 175


def test_list_steps_cannot_overwrite_or_finish_without_retained_pair() -> None:
    """Outstanding evidence is never retargeted, including on duplicate calls."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    with pytest.raises(CaptureError, match="retained pair"):
        steps.finish(fixture.position)
    steps.begin(fixture.position)
    with pytest.raises(CaptureError, match="outstanding"):
        steps.begin(fixture.position)
    assert len(fixture.releases) == 2
    assert all(release.call_count == 0 for release in fixture.releases)
    fixture.target._owners.close()
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize("phase", ["begin", "finish", "terminal"])
@pytest.mark.parametrize("reason", ["input", "focus", "deadline"])
def test_list_steps_late_guard_failure_discards_evidence(
    phase: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligibility and deadlines are rechecked after potentially blocking reads."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    if phase != "begin":
        steps.begin(fixture.position)
        fixture.advance(175, terminal=phase == "terminal")
    if phase == "terminal":
        steps.finish(fixture.position, terminal=True)
    original_read = fixture.target.scrollbar_state

    def fail_after_read() -> ScrollBarState:
        state = original_read()
        assert state is not None
        if reason == "deadline":
            monkeypatch.setattr(scrolling.time, "monotonic", lambda: steps._deadline)
        else:
            fixture.check.side_effect = CaptureError(reason)
        return state

    fixture.target.scrollbar_state = Mock(side_effect=fail_after_read)
    action = {
        "begin": steps.begin,
        "finish": steps.finish,
        "terminal": steps.confirm_terminal,
    }[phase]
    with pytest.raises(CaptureError):
        action(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


@pytest.mark.parametrize("footer", [False, True])
@pytest.mark.parametrize("clipped", [False, True])
def test_list_steps_right_clip_requires_the_exact_two_rail_partition(
    *, footer: bool, clipped: bool
) -> None:
    """Without a footer, only wholly contained raw item rectangles qualify."""
    fixture = ListStepFixture()
    if not footer:
        header = header_element(50034, Rect(343, 282, 620, 52))
        rail = header_element(50014, Rect(963, 282, 34, 425))
        fixture.target._header = _ScrollHeader(header, rail, fixture.target.area)
        fixture.children[:3] = [header, rail]
    if not clipped:
        for item in fixture.items:
            item.bounds.return_value = replace(item.bounds.return_value, width=592)
    steps = fixture.capability()
    if clipped and not footer:
        with pytest.raises(CaptureError, match="bounded item pair"):
            steps.begin(fixture.position)
    else:
        steps.begin(fixture.position)
        fixture.advance()
        assert steps.finish(fixture.position) == 175


@pytest.mark.parametrize("failure", ["missing", "loop", "wrong-item"])
def test_list_steps_hit_ancestry_is_bounded_and_never_retargets(failure: str) -> None:
    """A fresh unrelated item cannot replace the pair selected before the write."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    unrelated = header_element(
        50007 if failure == "wrong-item" else 50020,
        fixture.items[0].bounds.return_value,
    )
    mock(fixture.target._automation.pointer_result).side_effect = None
    mock(fixture.target._automation.pointer_result).return_value = unrelated
    original_parent = fixture.target._walker.parent
    visits = []

    def parent(element: _Interface, owners: ExitStack) -> _Interface | None:
        if element is unrelated:
            visits.append(element)
            return unrelated if failure == "loop" else None
        return original_parent(element, owners)

    fixture.target._walker.parent = Mock(side_effect=parent)
    scans = mock(fixture.target._walker.relative).call_count
    with pytest.raises(CaptureError):
        steps.finish(fixture.position)
    assert len(visits) <= 8
    assert mock(fixture.target._walker.relative).call_count == scans
    assert all(release.call_count == 1 for release in fixture.releases)


def test_list_steps_fresh_text_descendant_resolves_to_the_same_retained_item() -> None:
    """Bounded ancestry supports hits inside a ListItem without pointer equality."""
    fixture = ListStepFixture()
    descendants = {
        item: header_element(50020, item.bounds.return_value) for item in fixture.items
    }
    original_parent = fixture.target._walker.parent
    fixture.target._walker.parent = Mock(
        side_effect=lambda element, owners: next(
            (item for item, descendant in descendants.items() if element is descendant),
            original_parent(element, owners),
        )
    )
    mock(fixture.target._automation.pointer_result).side_effect = lambda *args: (
        descendants[fixture.hit(*args)]
    )
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    assert steps.finish(fixture.position) == 175


@pytest.mark.parametrize("failure", ["qi", "raw", "native-after", "budget"])
def test_list_steps_releases_partial_retention_on_begin_failure(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first QI reference is released even if acquiring the second fails."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    second = fixture.items[1]
    if failure == "qi":
        second.pointer_result.side_effect = OSError("QI failed")
    elif failure == "raw":
        second.bounds.side_effect = [
            second.bounds.return_value,
            Rect(372, 570, 1350, 48),
        ]
    elif failure == "native-after":
        original = fixture.target.scrollbar_state
        reads = 0

        def changed_read() -> ScrollBarState:
            nonlocal reads
            reads += 1
            if reads == 2:
                fixture.native = replace(fixture.native, position=1)
            state = original()
            assert state is not None
            return state

        fixture.target.scrollbar_state = Mock(side_effect=changed_read)
    else:
        original_qi = second.pointer_result.side_effect
        assert callable(original_qi)

        def delayed_qi(*args: object) -> object:
            item = original_qi(*args)
            monkeypatch.setattr(scrolling.time, "monotonic", lambda: steps._deadline)
            return item

        second.pointer_result.side_effect = delayed_qi
    with pytest.raises((CaptureError, OSError)):
        steps.begin(fixture.position)
    assert fixture.releases
    assert all(release.call_count == 1 for release in fixture.releases)
    assert all(release.call_count == 1 for release in fixture.temporary_releases)
    assert not steps._items


@pytest.mark.parametrize("change", ["input", "foreground"])
def test_list_steps_recheck_real_window_guard_after_raw_read(change: str) -> None:
    """Mouse/keyboard or focus changes during metadata reads are not accepted."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    fixture.check.side_effect = lambda: UiaScrollTarget._check_window(fixture.target)
    raw = fixture.items[0].bounds.return_value
    assert isinstance(raw, Rect)

    def changed_bounds() -> Rect:
        if change == "input":
            mock(fixture.target._input).return_value = 13
        else:
            mock(fixture.target._user.GetForegroundWindow).return_value = 9
        return raw

    fixture.items[0].bounds.side_effect = changed_bounds
    with pytest.raises(CaptureError):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)


def test_list_steps_cannot_confirm_terminal_without_verified_items() -> None:
    """Native maximum alone never establishes the terminal item lineage."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    fixture.advance(175, terminal=True)
    with pytest.raises(CaptureError, match="unconfirmed"):
        steps.confirm_terminal(fixture.position)


def test_list_steps_selection_rechecks_guard_after_last_position_read() -> None:
    """Late focus/input failure during selection cannot create the capability."""
    fixture = ListStepFixture()
    position = fixture.position

    def changed_position() -> ScrollPosition:
        fixture.check.side_effect = CaptureError("input changed")
        return position

    fixture.target.position = Mock(side_effect=changed_position)
    with pytest.raises(CaptureError, match="input changed"):
        fixture.target.list_steps(position, fixture.check)
    assert not fixture.releases


def test_list_steps_never_reuse_prior_pair_for_a_later_step() -> None:
    """Two fresh item references belong to each step and previous ones are released."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    assert steps.finish(fixture.position) == 175
    for item, y in zip(fixture.items, (563, 619), strict=True):
        item.bounds.return_value = replace(item.bounds.return_value, y=y)
    steps.begin(fixture.position)
    fixture.advance(167)
    assert steps.finish(fixture.position) == 167
    assert len(fixture.releases) == 4
    assert all(release.call_count == 1 for release in fixture.releases)


def test_list_steps_pair_identities_must_remain_distinct_after_movement() -> None:
    """Two references becoming the same UIA identity cannot prove two items."""
    fixture = ListStepFixture()
    steps = fixture.capability()
    steps.begin(fixture.position)
    fixture.advance()
    mock(fixture.target._automation.same_element).side_effect = lambda a, b: (
        a is b or (a is fixture.items[0] and b is fixture.items[1])
    )
    with pytest.raises(CaptureError, match="identities became ambiguous"):
        steps.finish(fixture.position)
    assert all(release.call_count == 1 for release in fixture.releases)
