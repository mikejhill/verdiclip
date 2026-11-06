"""Owned process timeout evidence cannot turn late completion into success."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import Mock

import pytest
from tests.portable import activation_lifecycle as lifecycle


def process_fixture(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Use only a fake owned process, no external executables or signals."""
    process = Mock(returncode=None)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", Mock(return_value=process))
    return process


@pytest.mark.parametrize("code", [0, 1])
def test_exit_status_and_original_streams_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    code: int,
) -> None:
    """A normal nonzero child exit must still fail the caller's exit-zero check."""
    process = process_fixture(monkeypatch)
    process.communicate.return_value = ("stdout", "stderr")
    process.returncode = code
    result = lifecycle.run_activation(["owned-command"])
    assert (result.returncode, result.stdout, result.stderr) == (
        code,
        "stdout",
        "stderr",
    )
    process.communicate.assert_called_once_with(timeout=10)
    process.kill.assert_not_called()
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


@pytest.mark.parametrize("already_exited", [False, True])
def test_timeout_never_becomes_success_after_assertions_or_exit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    already_exited: bool,
) -> None:
    """Original timeout survives whether child was running or pipes remained."""
    process = process_fixture(monkeypatch)
    original = subprocess.TimeoutExpired(
        "owned-command",
        10,
        stderr=(
            b"ACTIVATION_PHASE=assertions-complete script_ms=3 parent_wall_ms=9999\n"
        ),
    )
    process.communicate.side_effect = original
    process.poll.return_value = 0 if already_exited else None
    process.wait.side_effect = lambda **_kwargs: setattr(process, "returncode", 0)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is original
    process.communicate.assert_called_once_with(timeout=10)
    process.wait.assert_called_once_with(timeout=2)
    assert "retirement_confirmed': True" in original.__notes__[0]
    state = "exited" if already_exited else "running"
    assert f"process_state_at_timeout': '{state}'" in original.__notes__[0]
    if already_exited:
        process.kill.assert_not_called()
    else:
        process.kill.assert_called_once_with()


def test_failed_kill_still_reaps_and_preserves_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX cleanup uncertainty cannot replace the original failure."""
    monkeypatch.setattr(lifecycle, "sys", Mock(platform="linux"))
    process = process_fixture(monkeypatch)
    original = subprocess.TimeoutExpired("owned-command", 10)
    process.communicate.side_effect = original
    process.poll.side_effect = RuntimeError
    process.kill.side_effect = OSError
    process.wait.side_effect = subprocess.TimeoutExpired("owned-command", 2)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is original
    process.wait.assert_called_once_with(timeout=2)
    assert "retirement_confirmed': False" in original.__notes__[0]
    assert "kill-failed" in original.__notes__[0]
    assert "reap-unconfirmed" in original.__notes__[0]
    assert "process_state_at_timeout': 'unavailable'" in original.__notes__[0]
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


def test_diagnostic_failures_do_not_replace_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clock, parser and note failures are evidence loss, never a new verdict."""
    process = process_fixture(monkeypatch)
    original = subprocess.TimeoutExpired("owned-command", 10)
    process.communicate.side_effect = original
    process.poll.return_value = None
    monkeypatch.setattr(
        lifecycle, "time", Mock(monotonic=Mock(side_effect=RuntimeError))
    )
    monkeypatch.setattr(lifecycle, "phase_receipts", Mock(side_effect=RuntimeError))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is original
    process.wait.assert_called_once_with(timeout=2)


def test_other_communicate_exception_preserves_identity_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-timeout controls also retire only the owned process before propagating."""
    process = process_fixture(monkeypatch)
    original = KeyboardInterrupt()
    process.communicate.side_effect = original
    with pytest.raises(KeyboardInterrupt) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is original
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=2)


def test_phase_output_is_fixed_and_bounded() -> None:
    """Duplicate markers and arbitrary child output cannot expand the receipt."""
    phases = (
        "script-started",
        "before-decision",
        "after-decision",
        "assertions-complete",
    )
    text = "private arbitrary output\n" + "\n".join(
        f"ACTIVATION_PHASE={phase} script_ms=1 parent_wall_ms=20"
        for _ in range(100)
        for phase in phases
    )
    result = lifecycle.phase_receipts(text)
    assert len(result) == 4
    assert [row["phase"] for row in result] == list(phases)
    assert "private" not in str(result)
    assert lifecycle.phase_receipts(None) == []


@pytest.mark.parametrize("already_exited", [False, True])
def test_windows_interrupted_pipes_are_never_closed_or_claimed_retired(
    monkeypatch: pytest.MonkeyPatch,
    *,
    already_exited: bool,
) -> None:
    """Potential reader locks cannot extend timeout cleanup beyond the reap cap."""
    process = process_fixture(monkeypatch)
    monkeypatch.setattr(lifecycle, "sys", Mock(platform="win32"))
    original = subprocess.TimeoutExpired("owned-command", 10)
    process.communicate.side_effect = original
    process.poll.return_value = 0 if already_exited else None
    process.wait.side_effect = lambda **_kwargs: setattr(process, "returncode", 0)
    # A close could block forever. Prove it is never called, not merely caught.
    process.stdout.close.side_effect = AssertionError("blocking close reached")
    process.stderr.close.side_effect = AssertionError("blocking close reached")
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is original
    process.stdout.close.assert_not_called()
    process.stderr.close.assert_not_called()
    process.wait.assert_called_once_with(timeout=2)
    assert "retirement_confirmed': True" in original.__notes__[0]
    assert "pipe_cleanup_confirmed': False" in original.__notes__[0]
    assert "cleanup_confirmed': False" in original.__notes__[0]


@pytest.mark.parametrize(
    "kind", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
@pytest.mark.parametrize(
    "stage", ["clock", "poll", "receipt", "kill", "reap", "stdout", "stderr"]
)
def test_controls_propagate_while_preserving_timeout_and_independent_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[BaseException],
    stage: str,
) -> None:
    """Controls are not optional evidence; original timeout remains in context."""
    monkeypatch.setattr(lifecycle, "sys", Mock(platform="linux"))
    process = process_fixture(monkeypatch)
    original = subprocess.TimeoutExpired("owned-command", 10)
    control = kind("control")
    process.communicate.side_effect = original
    process.poll.return_value = None
    if stage == "clock":
        monkeypatch.setattr(
            lifecycle, "time", Mock(monotonic=Mock(side_effect=[1.0, control]))
        )
    elif stage == "poll":
        process.poll.side_effect = control
    elif stage == "receipt":
        monkeypatch.setattr(lifecycle, "phase_receipts", Mock(side_effect=control))
    elif stage == "kill":
        process.kill.side_effect = control
    elif stage == "reap":
        process.wait.side_effect = control
    else:
        getattr(process, stage).close.side_effect = control
    with pytest.raises(kind) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is control
    assert control.__context__ is original
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=2)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


def test_multiple_cleanup_controls_keep_complete_context_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every independent cleanup runs and all superseded controls remain visible."""
    monkeypatch.setattr(lifecycle, "sys", Mock(platform="linux"))
    process = process_fixture(monkeypatch)
    original = KeyboardInterrupt("communicate")
    kill = SystemExit("kill")
    reap = asyncio.CancelledError("reap")
    stdout = GeneratorExit("stdout")
    stderr = KeyboardInterrupt("stderr")
    process.communicate.side_effect = original
    process.kill.side_effect = kill
    process.wait.side_effect = reap
    process.stdout.close.side_effect = stdout
    process.stderr.close.side_effect = stderr
    with pytest.raises(KeyboardInterrupt) as caught:
        lifecycle.run_activation(["owned-command"])
    assert caught.value is stderr
    chain = []
    error: BaseException | None = caught.value
    while error is not None:
        chain.append(error)
        error = error.__context__
    assert chain == [stderr, stdout, reap, kill, original]
    process.wait.assert_called_once_with(timeout=2)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()
