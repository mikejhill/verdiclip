"""Read-only same-target refresh retains all original authority and COM owners."""

from collections.abc import Callable
from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from tests.platform.test_scrolling import mock, target

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform import scrolling
from verdiclip.platform.scrolling import UiaScrollTarget


def refresh_target() -> tuple[UiaScrollTarget, Mock, Mock]:
    """Provide native observations without any native writes or platform dependency."""
    native = target()
    native._point = Point(80, 90)
    native._control_type = 50030
    native._automation, native._walker = Mock(), Mock()
    native.check = Mock(side_effect=native._check_window)
    fresh = Mock()
    fresh.control_type.return_value = 50030
    fresh.bounds.return_value = native._provider_bounds
    fresh.native_handle.return_value = native._native_handle
    fresh.scroll_pattern.return_value.number.return_value = 35.0
    mock(native._automation.same_element).return_value = True
    released = Mock()

    def acquire(_slot: int, owners: ExitStack, *_args: object) -> Mock:
        if _slot == 16:
            return mock(native._walker)
        owners.callback(released)
        return fresh

    client = Mock()
    client.pointer_result.side_effect = acquire
    native._fresh_automation = Mock(return_value=client)
    return native, fresh, released


def test_refresh_keeps_original_interfaces_and_releases_fresh_ones(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two identity-confirmed observations never become a replacement provider."""
    caplog.set_level("DEBUG")
    native, fresh, released = refresh_target()
    original = native._element, native._scroll
    pause, guard = Mock(), Mock()
    native.refresh_position(guard, pause)
    assert (native._element, native._scroll) == original
    assert released.call_count == 2
    pause.assert_called_once_with(0.5)
    assert mock(native._automation.same_element).call_count == 2
    mock(native._automation.pointer_result).assert_not_called()
    mock(native._fresh_automation).return_value.same_element.assert_not_called()
    mock(native._scroll.call).assert_not_called()
    fresh.scroll_pattern.return_value.call.assert_not_called()
    assert guard.call_count > 20


@pytest.mark.parametrize("change", ["nested", "role", "bounds", "handle", "window"])
def test_refresh_rejects_different_nearest_target(change: str) -> None:
    """Do not walk past a newly nested scrollable control to find the old one."""
    native, fresh, released = refresh_target()
    if change == "nested":
        mock(native._automation.same_element).return_value = False
    elif change == "role":
        fresh.control_type.return_value = 50008
    elif change == "bounds":
        fresh.bounds.return_value = Rect(1, 2, 3, 4)
    elif change == "handle":
        fresh.native_handle.return_value = 91
    else:
        mock(native._user.GetAncestor).return_value = 2
    with pytest.raises(CaptureError, match="refresh"):
        native.refresh_position(lambda: None, Mock())
    mock(native._walker.parent).assert_not_called()
    mock(native._scroll.call).assert_not_called()
    assert released.call_count == (0 if change == "window" else 1)


@pytest.mark.parametrize("change", ["input", "focus", "deadline", "geometry"])
def test_refresh_checks_after_pause_and_releases_on_failure(change: str) -> None:
    """Input and lifecycle changes during observation cannot authorize a write."""
    native, _fresh, released = refresh_target()
    expired = False

    def check() -> None:
        if expired:
            raise CaptureError("deadline")

    def pause(_seconds: float) -> None:
        nonlocal expired
        if change == "input":
            mock(native._input).return_value = 99
        elif change == "focus":
            mock(native._user.GetForegroundWindow).return_value = 2
        elif change == "geometry":
            mock(native.check).side_effect = CaptureError("geometry")
        else:
            expired = True

    with pytest.raises(CaptureError):
        native.refresh_position(check, pause)
    assert released.call_count == 1
    mock(native._scroll.call).assert_not_called()


def test_refresh_checks_between_hit_test_and_pattern_call() -> None:
    """A deadline reached by hit-testing stops before any subsequent provider call."""
    native, fresh, released = refresh_target()
    acquire: Callable[..., Mock] = mock(
        native._fresh_automation
    ).return_value.pointer_result.side_effect
    expired = False

    def hit(*args: object) -> Mock:
        nonlocal expired
        result = acquire(*args)
        if args[0] == 7:
            expired = True
        return result

    def check() -> None:
        if expired:
            raise CaptureError("deadline")

    mock(native._fresh_automation).return_value.pointer_result.side_effect = hit
    with pytest.raises(CaptureError, match="deadline"):
        native.refresh_position(check, Mock())
    fresh.scroll_pattern.assert_not_called()
    assert released.call_count == 1


def test_fresh_client_uses_existing_com_apartment_and_scoped_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a temporary client without capture or COM reinitialization."""
    native = target()
    native._ole = Mock()
    interface = Mock()
    constructor = Mock(return_value=interface)
    monkeypatch.setattr(scrolling, "_Interface", constructor)
    with ExitStack() as owners:
        assert native._fresh_automation(owners) is interface
        assert constructor.call_args.args[1] is owners
    mock(native._ole.CoCreateInstance).assert_called_once()
    mock(native._ole.CoInitializeEx).assert_not_called()
