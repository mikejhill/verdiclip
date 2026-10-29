"""Owned modal-loop failures are surfaced without Qt or desktop interaction."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from scripts.owned_modal import run_owned_modal


class Timers:
    """Manually driven cancellable timers; never sleep or create threads."""

    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []
        self.stopped: list[int] = []

    def fire_action(self) -> None:
        """Fire the action only after the modal controller has armed it."""
        self.callbacks[0]()

    def fire_watchdog(self) -> None:
        """Fire the deadline without sleeping or processing native events."""
        self.callbacks[1]()

    def arm(self, delay_ms: int, callback: Callable[[], None]) -> Callable[[], None]:
        """Retain callbacks so tests can fire even a stale queued delivery."""
        del delay_ms
        index = len(self.callbacks)
        self.callbacks.append(callback)

        def stop() -> None:
            self.stopped.append(index)

        return stop


def test_success_closes_only_owned_dialog_and_cancels_timers() -> None:
    """Late queued callbacks cannot close another generation or rerun actions."""
    timers = Timers()
    closed = []
    called = []
    run_owned_modal(
        execute=timers.fire_action,
        close=lambda: closed.append("owned"),
        callback=lambda: called.append("capture"),
        arm=timers.arm,
    )
    assert closed == ["owned"]
    assert called == ["capture"]
    assert timers.stopped == [0, 1]
    for callback in timers.callbacks:
        callback()
    assert closed == ["owned"]
    assert called == ["capture"]


def test_callback_exception_is_reraised_after_owned_cleanup() -> None:
    """A Qt callback error must not strand the nested loop or become a pass."""
    timers = Timers()
    closed = []
    original = AssertionError("capture failed")

    def fail() -> None:
        raise original

    with pytest.raises(AssertionError, match="capture failed") as caught:
        run_owned_modal(
            execute=timers.fire_action,
            close=lambda: closed.append("owned"),
            callback=fail,
            arm=timers.arm,
        )
    assert caught.value is original
    assert closed == ["owned"]
    assert timers.stopped == [0, 1]


def test_watchdog_closes_owned_dialog_but_fails_test() -> None:
    """Deadline cleanup is explicitly failure, even if exec returns normally."""
    timers = Timers()
    closed = []
    called = []
    with pytest.raises(TimeoutError, match="cleanup is not success"):
        run_owned_modal(
            execute=timers.fire_watchdog,
            close=lambda: closed.append("owned"),
            callback=lambda: called.append("capture"),
            arm=timers.arm,
        )
    assert closed == ["owned"]
    assert called == []
    assert timers.stopped == [0, 1]


def test_late_completion_fails_even_if_watchdog_delivery_was_delayed() -> None:
    """A blocked GUI cannot evade its deadline merely by delaying the QTimer."""
    timers = Timers()
    clock = iter([0.0, 0.0, 6.0])
    with pytest.raises(TimeoutError, match="deadline"):
        run_owned_modal(
            execute=timers.fire_action,
            close=lambda: None,
            callback=lambda: None,
            arm=timers.arm,
            clock=lambda: next(clock),
        )


def test_exec_failure_keeps_identity_and_cancels_owned_timers() -> None:
    """Unexpected exec errors also release the exact test fixture."""
    timers = Timers()
    closed = []
    original = ValueError("exec failed")

    def fail() -> None:
        raise original

    with pytest.raises(ValueError, match="exec failed") as caught:
        run_owned_modal(
            execute=fail,
            close=lambda: closed.append("owned"),
            callback=lambda: None,
            arm=timers.arm,
        )
    assert caught.value is original
    assert closed == ["owned"]
    assert timers.stopped == [0, 1]


def test_early_exit_without_callback_cannot_pass() -> None:
    """Foreign or unintended loop exit must not count as capture validation."""
    timers = Timers()
    with pytest.raises(RuntimeError, match="before its callback"):
        run_owned_modal(
            execute=lambda: None,
            close=lambda: None,
            callback=lambda: None,
            arm=timers.arm,
        )


def test_partial_timer_setup_failure_cancels_first_timer() -> None:
    """Failure to arm the watchdog cannot leave a scheduled action behind."""
    timers = Timers()
    original = ValueError("timer arm failed")

    def arm(delay: int, callback: Callable[[], None]) -> Callable[[], None]:
        if delay:
            raise original
        return timers.arm(delay, callback)

    with pytest.raises(ValueError, match="timer arm failed") as caught:
        run_owned_modal(
            execute=lambda: None, close=lambda: None, callback=lambda: None, arm=arm
        )
    assert caught.value is original
    assert timers.stopped == [0]
    timers.callbacks[0]()


def test_cleanup_failure_does_not_hide_original_callback_error() -> None:
    """All owned cleanup paths get an attempt and original assertion survives."""
    timers = Timers()
    original = AssertionError("capture failed")
    closed = []

    def fail() -> None:
        raise original

    def arm(delay: int, callback: Callable[[], None]) -> Callable[[], None]:
        stop = timers.arm(delay, callback)

        def failing_stop() -> None:
            stop()
            raise ValueError("stop failed")

        return failing_stop

    with pytest.raises(AssertionError, match="capture failed") as caught:
        run_owned_modal(
            execute=timers.fire_action,
            close=lambda: closed.append("owned"),
            callback=fail,
            arm=arm,
        )
    assert caught.value is original
    assert len(original.__notes__) == 2
    assert timers.stopped == [0, 1]
    assert closed == ["owned"]


def test_watchdog_revokes_already_queued_action_before_exec_returns() -> None:
    """A queued zero timer has no authority after the deadline rejected its box."""
    timers = Timers()
    called = []

    def execute() -> None:
        timers.fire_watchdog()
        timers.fire_action()

    with pytest.raises(TimeoutError, match="deadline"):
        run_owned_modal(
            execute=execute,
            close=lambda: None,
            callback=lambda: called.append("capture"),
            arm=timers.arm,
        )
    assert called == []


def test_delayed_action_is_not_admitted_when_watchdog_also_delayed() -> None:
    """Timer delivery order cannot authorize capture past the monotonic deadline."""
    timers = Timers()
    called = []
    clock = iter([0.0, 6.0])
    with pytest.raises(TimeoutError, match="deadline"):
        run_owned_modal(
            execute=timers.fire_action,
            close=lambda: None,
            callback=lambda: called.append("capture"),
            arm=timers.arm,
            clock=lambda: next(clock),
        )
    assert called == []
