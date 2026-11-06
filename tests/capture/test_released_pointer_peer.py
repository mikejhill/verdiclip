"""Finite baseline protocol and owned cleanup controls, without native execution."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.capture.released_pointer_peer import BaselinePeer, reply


def peer(mode: str = "cancel") -> BaselinePeer:
    """Create no process or pipe while exercising real supervisor control flow."""
    value = object.__new__(BaselinePeer)
    value.mode = mode
    value.nonce = "n"
    value.process = Mock()
    value.process.pid = 23
    value.process.is_alive.return_value = True
    value.process.exitcode = 97 if mode == "crash" else 0
    value.pipe, value.child_pipe = Mock(), Mock()
    value.started = False
    value.handles = ()
    value.foreground_owned = False
    return value


def test_invalid_mode_starts_nothing(tmp_path: Path) -> None:
    """No arbitrary command mode can instantiate a child."""
    with pytest.raises(ValueError, match="mode-invalid"):
        BaselinePeer(tmp_path, "anything", (1, 2))


def test_unexpected_attestation_never_starts_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child must match exact PID/runtime/source before a start command."""
    value = peer()
    monkeypatch.setattr(
        "tests.capture.released_pointer_peer.receive", lambda *_args: {}
    )
    sender = Mock()
    monkeypatch.setattr("tests.capture.released_pointer_peer.send", sender)
    with pytest.raises(RuntimeError, match="attestation-failed"):
        value.start()
    sender.assert_not_called()


@pytest.mark.parametrize("mode", ["cancel", "crash"])
def test_exact_action_boundary(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Normal cancellation remains alive; crash waits for the exact forced exit."""
    value = peer(mode)
    assert isinstance(value.process, Mock)
    value.process.is_alive.return_value = mode == "cancel"
    receiver = Mock(
        side_effect=[
            {
                "phase": "admitted",
                "nonce": "n",
                "handles": [7],
                "foreground_owned": True,
            },
            {"phase": "retired" if mode == "cancel" else "crash-entry", "nonce": "n"},
        ]
    )
    monkeypatch.setattr("tests.capture.released_pointer_peer.receive", receiver)
    sender = Mock()
    monkeypatch.setattr("tests.capture.released_pointer_peer.send", sender)
    value.act()
    assert value.handles == (7,)
    assert value.foreground_owned is True
    sender.assert_called_once_with(value.pipe, {"phase": "action", "nonce": "n"})


def test_wrong_crash_exit_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary exit must not stand in for forced process death."""
    value = peer("crash")
    assert isinstance(value.process, Mock)
    value.process.exitcode = 0
    value.process.is_alive.return_value = False
    monkeypatch.setattr(
        "tests.capture.released_pointer_peer.receive",
        Mock(
            side_effect=[
                {
                    "phase": "admitted",
                    "nonce": "n",
                    "handles": [7],
                    "foreground_owned": True,
                },
                {"phase": "crash-entry", "nonce": "n"},
            ]
        ),
    )
    with pytest.raises(RuntimeError, match="crash-exit-unconfirmed"):
        value.act()


@pytest.mark.parametrize("failure", [OSError("cleanup"), KeyboardInterrupt()])
def test_partial_start_cleanup_is_independent(failure: BaseException) -> None:
    """Retained PID ownership survives start errors and preserves controls."""
    value = peer()
    assert isinstance(value.process, Mock)
    assert isinstance(value.pipe, Mock)
    assert isinstance(value.child_pipe, Mock)
    value.process.terminate.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        value.close()
    assert caught.value is failure
    value.process.join.assert_called_once_with(2)
    value.pipe.close.assert_called_once()
    value.child_pipe.close.assert_called_once()


@pytest.mark.parametrize("stage", ["admission", "private/untrusted"])
def test_failure_reply_preserves_only_fixed_categories(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Unexpected data never enters the public failure text."""
    monkeypatch.setattr(
        "tests.capture.released_pointer_peer.receive",
        lambda *_args: {
            "phase": "failed",
            "nonce": "n",
            "stage": stage,
            "reason": "timeout",
        },
    )
    expected = (
        "baseline-child-admission-timeout"
        if stage == "admission"
        else "baseline-failure-reply-invalid"
    )
    with pytest.raises(RuntimeError, match=expected):
        reply(Mock(), 5, "n")
