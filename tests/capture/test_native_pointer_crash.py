"""Portable ownership, protocol and cleanup controls; no child/native execution."""

import json
from unittest.mock import Mock

import pytest
from tests.capture.native_pointer_crash import (
    CrashChild,
    eligible,
    receive,
    send,
    unique,
)

from verdiclip.capture.native_pointer import PointerPhase


def test_protocol_size_and_duplicates() -> None:
    """No oversized private payload or duplicate-key override is accepted."""
    pipe = Mock()
    with pytest.raises(RuntimeError, match="size"):
        send(pipe, {"x": "x" * 4096})
    pipe.send_bytes.assert_not_called()
    with pytest.raises(ValueError, match="duplicate"):
        unique([("phase", "ready"), ("phase", "crash-entry")])


def test_receive_rejects_late_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A late available message cannot reset the original absolute deadline."""
    pipe = Mock()
    pipe.poll.return_value = True
    pipe.recv_bytes.return_value = b"{}"
    monkeypatch.setattr(
        "tests.capture.native_pointer_crash.time.monotonic", Mock(side_effect=[1, 3])
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        receive(pipe, 2)
    pipe.recv_bytes.assert_called_once_with(4096)


@pytest.mark.parametrize(
    "failure",
    ["closed", "not-selecting", "no-hwnd", "dead-hwnd", "unsafe", "wrong-cross", None],
)
def test_crash_requires_fresh_owner_proof(failure: str | None) -> None:
    """Earlier admission is not enough when any live owner fact changes."""
    service = Mock()
    pointer = service._native_pointer
    pointer.route.phase = (
        PointerPhase.CLOSED if failure == "closed" else PointerPhase.OWNED
    )
    service.is_selecting = failure != "not-selecting"
    service.overlays = () if failure == "no-hwnd" else (Mock(),)
    if service.overlays:
        service.overlays[0].winId.return_value = 7
    pointer.safe.return_value = failure != "unsafe"
    result = eligible(
        service,
        lambda _handle: failure != "dead-hwnd",
        lambda: failure != "wrong-cross",
    )
    assert result is (failure is None)


def owned() -> CrashChild:
    """No multiprocessing object or native process is created by these controls."""
    value = object.__new__(CrashChild)
    value.nonce = "n"
    value.started = True
    value.pipe = Mock()
    value.child_pipe = Mock()
    value.process = Mock()
    value.process.is_alive.return_value = False
    value.process.exitcode = 97
    return value


@pytest.mark.parametrize("exitcode", [0, 97])
def test_only_causal_marker_and_exact_exit_qualify(
    monkeypatch: pytest.MonkeyPatch, exitcode: int
) -> None:
    """Normal child retirement must not count as forced-process proof."""
    value = owned()
    assert isinstance(value.process, Mock)
    assert isinstance(value.pipe, Mock)
    value.process.exitcode = exitcode
    monkeypatch.setattr(
        "tests.capture.native_pointer_crash.receive",
        Mock(
            side_effect=[
                {"phase": "admitted", "nonce": "n", "handles": [7]},
                {"phase": "crash-entry", "nonce": "n"},
            ]
        ),
    )
    if exitcode == 97:
        value.crash()
    else:
        with pytest.raises(RuntimeError, match="exit-unconfirmed"):
            value.crash()
    assert json.loads(value.pipe.send_bytes.call_args.args[0]) == {
        "phase": "crash",
        "nonce": "n",
    }


def test_cleanup_reaps_despite_termination_failure() -> None:
    """Independent bounded wait and pipe closure survive a failed terminate."""
    value = owned()
    assert isinstance(value.process, Mock)
    assert isinstance(value.pipe, Mock)
    assert isinstance(value.child_pipe, Mock)
    value.process.is_alive.return_value = True
    value.process.terminate.side_effect = OSError("test")
    with pytest.raises(OSError, match="test"):
        value.close()
    value.process.join.assert_called_once_with(2)
    value.pipe.close.assert_called_once()
    value.child_pipe.close.assert_called_once()


def test_cleanup_control_propagates_after_reap() -> None:
    """KeyboardInterrupt is not swallowed, and cleanup peers still run."""
    value = owned()
    assert isinstance(value.process, Mock)
    assert isinstance(value.pipe, Mock)
    assert isinstance(value.child_pipe, Mock)
    value.process.is_alive.return_value = True
    value.process.terminate.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        value.close()
    value.process.join.assert_called_once_with(2)
    value.pipe.close.assert_called_once()
    value.child_pipe.close.assert_called_once()


@pytest.mark.parametrize("control", [False, True])
def test_partial_start_retains_cleanup_ownership(*, control: bool) -> None:
    """A retained PID is retired even when start raises before its flag is set."""
    value = owned()
    assert isinstance(value.process, Mock)
    assert isinstance(value.pipe, Mock)
    value.started = False
    value.process.pid = 123
    value.process.start.side_effect = (
        KeyboardInterrupt() if control else OSError("start")
    )
    value.process.is_alive.side_effect = [True, False]
    with pytest.raises(KeyboardInterrupt if control else OSError):  # noqa: PT012 - exercise caller finally ownership
        try:
            value.start()
        finally:
            value.close()
    value.process.terminate.assert_called_once()
    value.process.join.assert_called_once_with(2)
    value.pipe.close.assert_called_once()
