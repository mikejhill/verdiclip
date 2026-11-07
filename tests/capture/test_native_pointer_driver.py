"""Portable finite-input scope and packet controls; no native input execution."""

import ctypes
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.capture.native_pointer_driver import Driver, Input, State
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point, Rect


def driver(monkeypatch: pytest.MonkeyPatch) -> tuple[Driver, Mock]:
    """Inject only packet and guard boundaries."""
    value = object.__new__(Driver)
    value.sent = 0
    value.post_consumed = False
    value._post_packet = False
    api = Mock()
    value.api = api
    api.SendInput.return_value = 1
    api.GetSystemMetrics.side_effect = lambda i: {76: 0, 77: 0, 78: 1920, 79: 1080}[i]
    monkeypatch.setattr(value, "guard", Mock())
    return value, api


def test_move_packet_is_one_absolute_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """No button, wheel or arbitrary payload is included in the MOVE."""
    value, api = driver(monkeypatch)
    value.move(Point(100, 100))
    count, address, size = api.SendInput.call_args.args
    packet = address._obj
    assert count == 1
    assert size == ctypes.sizeof(Input)
    assert packet.kind == 0
    assert packet.payload.mouse.dwFlags == 0xC001
    assert packet.payload.mouse.mouseData == 0


def test_invalid_key_cannot_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native harness is not an arbitrary key injector."""
    value, api = driver(monkeypatch)
    with pytest.raises(ValueError, match="not-allowed"):
        value.key(0x41, down=True)
    api.SendInput.assert_not_called()


def test_budget_cannot_be_extended(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated attempts cannot exceed the predeclared finite packet count."""
    value, api = driver(monkeypatch)
    value.sent = 16
    with pytest.raises(RuntimeError, match="budget-exhausted"):
        value.move(Point(100, 100))
    api.SendInput.assert_not_called()


def test_guard_refusal_precedes_native_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown foreground/recipient state does not authorize a packet."""
    value, api = driver(monkeypatch)
    monkeypatch.setattr(value, "guard", Mock(side_effect=RuntimeError("refused")))
    with pytest.raises(RuntimeError, match="refused"):
        value.move(Point(100, 100))
    api.SendInput.assert_not_called()


@pytest.mark.parametrize("inserted", [0, 2])
def test_failed_insertion_has_no_retry(
    monkeypatch: pytest.MonkeyPatch, inserted: int
) -> None:
    """Failed insertion is not silently repeated or repaired."""
    value, api = driver(monkeypatch)
    api.SendInput.return_value = inserted
    with pytest.raises(RuntimeError, match="insertion-unconfirmed"):
        value.move(Point(100, 100))
    api.SendInput.assert_called_once()
    assert value.sent == 1


def test_static_fixture_switch_is_opt_in(tmp_path: Path) -> None:
    """Existing providers do not silently change their cursor or fixture behavior."""
    assert ScrollPeer(tmp_path).native_pointer_cursor is False
    assert (
        ScrollPeer(
            tmp_path, provider="winforms", native_pointer_cursor=True
        ).native_pointer_cursor
        is True
    )
    with pytest.raises(ValueError, match="WinForms"):
        ScrollPeer(tmp_path, native_pointer_cursor=True)


@pytest.mark.parametrize("refusal", [None, "outside", "root", "pid", "menu"])
def test_real_destination_guard(
    monkeypatch: pytest.MonkeyPatch, refusal: str | None
) -> None:
    """The actual guard checks client, root ownership and protected GUI state."""
    value = object.__new__(Driver)
    value.fixture, value.fixture_pid = 7, 8
    value.allowed = {7: 8}
    api = Mock()
    value.api = api
    monkeypatch.setattr(
        "tests.capture.native_pointer_driver.require_hosted", lambda _env: None
    )
    gui = (0x14 if refusal == "menu" else 0, 7, 7, 0, 0, 0)
    monkeypatch.setattr(value, "read", lambda: State(1, 1, Point(10, 10), 7, gui))
    monkeypatch.setattr(value, "client", lambda: Rect(0, 0, 20, 20))
    api.WindowFromPoint.return_value = 9
    api.GetAncestor.return_value = 99 if refusal == "root" else 7

    def identity(_handle: int, address: Mock) -> int:
        address._obj.value = 99 if refusal == "pid" else 8
        return 1

    api.GetWindowThreadProcessId.side_effect = identity
    point = Point(50, 50) if refusal == "outside" else Point(10, 10)
    if refusal is not None:
        with pytest.raises(RuntimeError):
            value.guard(point)
    else:
        value.guard(point)
    api.SendInput.assert_not_called()


def test_button_passes_actual_position_to_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Button boundaries cannot bypass destination checks."""
    value, api = driver(monkeypatch)
    monkeypatch.setattr(value, "read", lambda: State(1, 1, Point(10, 12), 7, (0,) * 6))
    value.button(down=True)
    assert isinstance(value.guard, Mock)
    value.guard.assert_called_once_with(Point(10, 12))
    assert api.SendInput.call_args.args[1]._obj.payload.mouse.dwFlags == 2


@pytest.mark.parametrize("handle", [0, 42])
def test_cross_reference_uses_qt_shared_resource_loading(
    monkeypatch: pytest.MonkeyPatch, handle: int
) -> None:
    """Match the verified Qt backend flags, without destroying a shared cursor."""
    value, api = driver(monkeypatch)
    api.LoadImageW.return_value = handle
    if handle:
        assert value.cross_cursor() == handle
    else:
        with pytest.raises(RuntimeError, match="resource-unavailable"):
            value.cross_cursor()
    api.LoadImageW.assert_called_once_with(None, 32515, 2, 0, 0, 0x8040)
    api.DestroyCursor.assert_not_called()


def test_reserved_post_move_cannot_extend_ordinary_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sixteen ordinary commands plus exactly one reserved MOVE, never eighteen."""
    value, api = driver(monkeypatch)
    value.sent = 16
    value.move_post_retirement(Point(100, 100))
    assert value.sent == 17
    assert api.SendInput.call_args.args[0] == 1
    assert api.SendInput.call_args.args[1]._obj.payload.mouse.dwFlags == 0xC001
    with pytest.raises(RuntimeError, match="post-input-budget"):
        value.move_post_retirement(Point(101, 100))
    with pytest.raises(RuntimeError, match="input-budget"):
        value.move(Point(101, 100))
    api.SendInput.assert_called_once()


def test_failed_reserved_move_cannot_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A denied/partial reserved insertion consumes its sole attempt."""
    value, api = driver(monkeypatch)
    api.SendInput.return_value = 0
    with pytest.raises(RuntimeError, match="insertion"):
        value.move_post_retirement(Point(100, 100))
    with pytest.raises(RuntimeError, match="post-input-budget"):
        value.move_post_retirement(Point(100, 100))
    assert not value._post_packet
    api.SendInput.assert_called_once()
