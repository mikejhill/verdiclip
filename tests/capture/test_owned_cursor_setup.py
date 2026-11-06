"""Bounded setup controls without native input or cursor normalization."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture import owned_cursor_setup as setup
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_owned_cursor_observer import BASE

from verdiclip.geometry import Point


def prepared(
    monkeypatch: pytest.MonkeyPatch, inserted: int = 1
) -> tuple[setup.OneMove, Mock, Mock]:
    """Construct an injected protocol, never load user32 in portable controls."""
    peer = Mock()
    peer.snapshot_request = None
    peer.identity = (BASE.process, 1, BASE.foreground)
    peer.poll_snapshot.return_value = True
    api = Mock()
    api.GetSystemMetrics.side_effect = lambda index: {76: 0, 77: 0, 78: 1920, 79: 1080}[
        index
    ]
    api.SendInput.return_value = inserted
    value = object.__new__(setup.OneMove)
    value.peer = cast(ScrollPeer, peer)
    value.api = api
    value.attempted = False
    monkeypatch.setattr(value, "guard", Mock(return_value=Point(*BASE.position)))
    return value, peer, api


@pytest.mark.parametrize("inserted", [0, 2])
def test_refused_insertion_never_observes_or_retries(
    monkeypatch: pytest.MonkeyPatch, inserted: int
) -> None:
    """Anything other than one inserted event fails before acknowledgment/baseline."""
    move, peer, api = prepared(monkeypatch, inserted)
    reader = Mock(return_value=BASE)
    monkeypatch.setattr(setup, "observe", reader)
    with pytest.raises(RuntimeError, match="insertion-refused"):
        move.prepare(Mock())
    with pytest.raises(RuntimeError, match="already-attempted"):
        move.prepare(Mock())
    api.SendInput.assert_called_once()
    peer.request_snapshot.assert_not_called()
    reader.assert_not_called()


@pytest.mark.parametrize("flags", [0, 1, 2, 3])
def test_first_baseline_is_returned_without_polling_until_visible(
    monkeypatch: pytest.MonkeyPatch, flags: int
) -> None:
    """An ineligible result is retained, never corrected or replaced."""
    move, peer, api = prepared(monkeypatch)
    first = replace(BASE, flags=flags)
    reader = Mock(return_value=first)
    monkeypatch.setattr(setup, "observe", reader)
    result = move.prepare(Mock())
    assert result is first
    assert setup.eligible_baseline(result) is (flags == 1)
    reader.assert_called_once()
    api.SendInput.assert_called_once()
    peer.request_snapshot.assert_called_once()
    with pytest.raises(RuntimeError, match="already-attempted"):
        move.prepare(Mock())


def test_ack_timeout_never_samples_or_reinserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture acknowledgment budget is finite and does not become a retry."""
    move, peer, api = prepared(monkeypatch)
    peer.poll_snapshot.return_value = False
    moments = iter((0.0, 2.0))
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(moments))
    reader = Mock()
    monkeypatch.setattr(setup, "observe", reader)
    with pytest.raises(TimeoutError, match="ack-timeout"):
        move.prepare(Mock())
    api.SendInput.assert_called_once()
    reader.assert_not_called()


def test_guard_failure_prevents_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown foreign state never authorizes preparation."""
    move, peer, api = prepared(monkeypatch)
    monkeypatch.setattr(move, "guard", Mock(side_effect=RuntimeError("refused")))
    with pytest.raises(RuntimeError, match="refused"):
        move.prepare(Mock())
    api.SendInput.assert_not_called()
    peer.request_snapshot.assert_not_called()


def test_empty_desktop_prevents_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid native geometry is not normalized into a usable destination."""
    move, _, api = prepared(monkeypatch)
    api.GetSystemMetrics.side_effect = lambda _index: 0
    with pytest.raises(ValueError, match="outside"):
        move.prepare(Mock())
    api.SendInput.assert_not_called()


def test_fixed_summary_has_no_private_values() -> None:
    """Suppressed/hidden/unknown remain distinguishable without raw handles."""
    summary = setup.baseline_summary(replace(BASE, flags=2, shape=0))
    assert summary == {
        "injected_setup": True,
        "cursor_state": "suppressed",
        "handle_present": False,
        "physical_input_verified": False,
        "rendered_pointer_verified": False,
    }
    assert not setup.eligible_baseline(replace(BASE, flags=1, shape=0))


@pytest.mark.parametrize(
    "state",
    [
        replace(BASE, foreground=99),
        replace(BASE, process=99),
        replace(BASE, gui=(4, 9, 9, 0, 0, 0)),
        replace(BASE, gui=(0, 9, 9, 99, 0, 0)),
        replace(BASE, gui=(0, 9, 9, 0, 99, 0)),
        replace(BASE, gui=(0, 9, 9, 0, 0, 99)),
    ],
)
def test_native_guard_rejects_foreign_activity(
    monkeypatch: pytest.MonkeyPatch, state: setup.Snapshot
) -> None:
    """The real guard rejects foreign identity, capture, menus and move-size state."""
    move = object.__new__(setup.OneMove)
    peer = Mock()
    peer.poll.return_value = True
    peer.process.pid = BASE.process
    peer.identity = (BASE.process, 1, BASE.foreground)
    peer.handle = BASE.foreground
    peer.nonce = "owned"
    move.peer = cast(ScrollPeer, peer)
    move.api = Mock()

    def title(_root: int, buffer: object, _size: int) -> int:
        cast(SimpleNamespace, buffer).value = "VerdiClip synthetic scroll fixture owned"
        return 48

    move.api.GetWindowTextW.side_effect = title
    monkeypatch.setattr(setup, "require_hosted", lambda _env: None)
    monkeypatch.setattr(setup, "observe", lambda: state)
    with pytest.raises(RuntimeError, match="foreign-gui-state"):
        move.guard()
    move.api.SendInput.assert_not_called()


def native_guard_fixture(monkeypatch: pytest.MonkeyPatch) -> setup.OneMove:
    """Exercise real guard logic with private fixed native-call substitutes."""
    move = object.__new__(setup.OneMove)
    peer = Mock()
    peer.poll.return_value = True
    peer.process.pid = BASE.process
    peer.identity = (BASE.process, 1, BASE.foreground)
    peer.handle = BASE.foreground
    peer.nonce = "owned"
    move.peer = cast(ScrollPeer, peer)
    move.api = Mock()
    move.api.GetWindowTextW.side_effect = lambda _r, buffer, _n: setattr(
        buffer, "value", "VerdiClip synthetic scroll fixture owned"
    )

    def rectangle(_root: int, pointer: object) -> bool:
        value = cast(SimpleNamespace, pointer)._obj
        value.left, value.top, value.right, value.bottom = 0, 0, 20, 40
        return True

    move.api.GetClientRect.side_effect = rectangle
    move.api.ClientToScreen.return_value = True
    move.api.WindowFromPoint.return_value = 19

    def process(_hit: int, pointer: object) -> int:
        cast(SimpleNamespace, pointer)._obj.value = BASE.process
        return 11

    move.api.GetWindowThreadProcessId.side_effect = process
    move.api.GetAncestor.return_value = BASE.foreground
    move.api.IsWindowVisible.return_value = True
    monkeypatch.setattr(setup, "require_hosted", lambda _env: None)
    monkeypatch.setattr(setup, "observe", lambda: BASE)
    return move


def test_actual_destination_guard_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client center must resolve to an owned hit with the exact foreground root."""
    move = native_guard_fixture(monkeypatch)
    api = cast(Mock, move.api)
    assert move.guard() == Point(10, 20)
    api.GetAncestor.assert_called_once_with(19, 2)
    api.SendInput.assert_not_called()


@pytest.mark.parametrize("fault", ["title", "hwnd", "root", "pid", "moved"])
def test_actual_destination_guard_refusals(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Late ownership/geometry failures remain refusals rather than coerced targets."""
    move = native_guard_fixture(monkeypatch)
    api = cast(Mock, move.api)
    if fault == "title":
        api.GetWindowTextW.side_effect = lambda _r, buffer, _n: setattr(
            buffer, "value", "wrong"
        )
    elif fault == "hwnd":
        api.WindowFromPoint.return_value = 0
    elif fault == "root":
        api.GetAncestor.return_value = 99
    elif fault == "pid":
        api.GetWindowThreadProcessId.side_effect = lambda _h, p: (
            setattr(p._obj, "value", 99) or 11
        )
    with pytest.raises(
        RuntimeError, match=r"setup-(fixture-title|destination-not-owned|client-moved)"
    ):
        move.guard(Point(99, 99) if fault == "moved" else None)
    api.SendInput.assert_not_called()


def test_exact_single_move_packet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only one absolute virtual-desktop MOVE is submitted, with no buttons/wheel."""
    move, _, api = prepared(monkeypatch)
    monkeypatch.setattr(setup, "observe", lambda: BASE)
    move.prepare(Mock())
    count, packets, size = api.SendInput.call_args.args
    assert count == 1
    assert len(packets) == 1
    assert size == setup.ctypes.sizeof(setup.Input)
    assert packets[0].type == 0
    mouse = packets[0].payload.mouse
    assert mouse.dwFlags == 0xC001
    assert mouse.mouseData == 0
    assert mouse.dwExtraInfo == 0
    assert mouse.time == 0
    assert 0 <= mouse.dx <= 65535
    assert 0 <= mouse.dy <= 65535


def test_late_successful_ack_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful poll cannot erase an already expired acknowledgment budget."""
    move, peer, api = prepared(monkeypatch)
    peer.poll_snapshot.return_value = True
    moments = iter((0.0, 2.001))
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(moments))
    reader = Mock()
    monkeypatch.setattr(setup, "observe", reader)
    with pytest.raises(TimeoutError, match="ack-timeout"):
        move.prepare(Mock())
    reader.assert_not_called()
    api.SendInput.assert_called_once()
