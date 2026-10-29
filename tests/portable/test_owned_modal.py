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
    assert isinstance(original.__cause__, BaseExceptionGroup)
    assert len(original.__cause__.exceptions) == 2
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


class CustomCancellation(BaseException):
    """Represent control signals outside the ordinary Exception hierarchy."""


@pytest.mark.parametrize(
    "kind",
    [
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        CustomCancellation,
        pytest.fail.Exception,
        pytest.skip.Exception,
    ],
)
def test_control_signals_are_reraised_outside_qt_after_cleanup(
    kind: type[BaseException],
) -> None:
    """Cancellation is delayed only long enough to reject this owned fixture."""
    timers = Timers()
    closed = []
    original = kind("cancel requested")
    callback_returned = []

    def fail() -> None:
        raise original

    def execute() -> None:
        timers.fire_action()
        callback_returned.append(True)
        assert closed == ["owned"]

    with pytest.raises(kind) as caught:
        run_owned_modal(
            execute=execute,
            close=lambda: closed.append("owned"),
            callback=fail,
            arm=timers.arm,
        )
    assert caught.value is original
    assert callback_returned == [True]
    assert timers.stopped == [0, 1]


@pytest.mark.parametrize("phase", ["close", "stop", "execute"])
@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, CustomCancellation])
def test_secondary_control_signal_wins_and_retains_original_failure(
    phase: str, kind: type[BaseException]
) -> None:
    """Cleanup/exec cancellation cannot be reduced to a type-name diagnostic note."""
    timers = Timers()
    ordinary = AssertionError("capture failed")
    control = kind("cancel requested")

    def fail() -> None:
        raise ordinary

    def close() -> None:
        if phase == "close":
            raise control

    def arm(delay: int, callback: Callable[[], None]) -> Callable[[], None]:
        stop = timers.arm(delay, callback)

        def disarm() -> None:
            stop()
            if phase == "stop":
                raise control

        return disarm

    def execute() -> None:
        timers.fire_action()
        if phase == "execute":
            raise control

    with pytest.raises(kind) as caught:
        run_owned_modal(execute=execute, close=close, callback=fail, arm=arm)
    assert caught.value is control
    assert isinstance(control.__cause__, BaseExceptionGroup)
    assert ordinary in control.__cause__.exceptions
    assert timers.stopped == [0, 1]


def test_multiple_control_objects_are_retained_not_reduced_to_notes() -> None:
    """First control identity propagates while the other control remains inspectable."""
    timers = Timers()
    first = KeyboardInterrupt("first")
    second = SystemExit("second")

    def fail() -> None:
        raise first

    def close() -> None:
        raise second

    with pytest.raises(KeyboardInterrupt) as caught:
        run_owned_modal(
            execute=timers.fire_action, close=close, callback=fail, arm=timers.arm
        )
    assert caught.value is first
    assert isinstance(first.__cause__, BaseExceptionGroup)
    assert second in first.__cause__.exceptions


@pytest.mark.parametrize("skip_kind", [pytest.skip.Exception, pytest.xfail.Exception])
@pytest.mark.parametrize("skip_first", [False, True])
def test_skip_cannot_hide_a_real_callback_or_cleanup_failure(
    skip_kind: type[BaseException], *, skip_first: bool
) -> None:
    """A retained assertion must fail even when another phase requests skip/xfail."""
    timers = Timers()
    failure = AssertionError("real failure")
    skipped = skip_kind("skip requested")
    callback_error = skipped if skip_first else failure
    cleanup_error = failure if skip_first else skipped

    def callback() -> None:
        raise callback_error

    def arm(delay: int, action: Callable[[], None]) -> Callable[[], None]:
        stop = timers.arm(delay, action)

        def disarm() -> None:
            stop()
            raise cleanup_error

        return disarm

    with pytest.raises(AssertionError, match="real failure") as caught:
        run_owned_modal(
            execute=timers.fire_action, close=lambda: None, callback=callback, arm=arm
        )
    assert caught.value is failure
    assert isinstance(failure.__cause__, BaseExceptionGroup)
    assert skipped in failure.__cause__.exceptions
    assert timers.stopped == [0, 1]


def test_pytest_session_exit_is_preserved_over_an_ordinary_failure() -> None:
    """pytest.exit is an explicit control request despite deriving from Exception."""
    timers = Timers()
    failure = AssertionError("real failure")
    exit_request = pytest.exit.Exception("session exit")

    def callback() -> None:
        raise failure

    def execute() -> None:
        timers.fire_action()
        raise exit_request

    with pytest.raises(pytest.exit.Exception) as caught:
        run_owned_modal(
            execute=execute, close=lambda: None, callback=callback, arm=timers.arm
        )
    assert caught.value is exit_request
    assert isinstance(exit_request.__cause__, BaseExceptionGroup)
    assert failure in exit_request.__cause__.exceptions
