"""Pure ownership/propagation additions; no Qt or plugin collection."""

import traceback
from collections.abc import Callable

import pytest
from scripts.owned_modal import _DeferredCallbackScope, _ModalState, run_owned_modal


def test_early_return_still_propagates_retained_control_and_cleans() -> None:
    """Returning cannot bypass cleanup or deferred cancellation."""
    original = KeyboardInterrupt("cancel")
    closed = []
    stopped = []
    state = _ModalState(lambda: None, lambda: closed.append(True), lambda: 0.0, 1.0)
    state.stops.append(lambda: stopped.append(True))

    def return_early() -> None:
        with state:
            with _DeferredCallbackScope(state):
                raise original
            return

    with pytest.raises(KeyboardInterrupt) as caught:
        return_early()
    assert caught.value is original
    assert closed == [True]
    assert stopped == [True]
    assert not state.scope_open


def test_capture_without_mandatory_outer_propagation_is_rejected() -> None:
    """A callback capture cannot exist without an owner that must propagate it."""
    state = _ModalState(lambda: None, lambda: None, lambda: 0.0, 1.0)
    with (
        pytest.raises(RuntimeError, match="active propagation scope"),
        _DeferredCallbackScope(state),
    ):
        raise KeyboardInterrupt("must never be captured")
    assert state.errors == []


def test_outer_scope_cannot_be_reused_after_success() -> None:
    """Retired owners cannot acquire a second generation."""
    state = _ModalState(lambda: None, lambda: None, lambda: 0.0, 1.0)
    with state:
        state.invoke()
    with pytest.raises(RuntimeError, match="cannot be reused"), state:
        pass
    assert not state.scope_open


@pytest.mark.parametrize("phase", ["setup", "callback", "postcheck"])
def test_clock_failure_keeps_identity_and_owned_cleanup(phase: str) -> None:
    """Every admitted resource is retired before an exact clock failure escapes."""
    original = ValueError("clock failed")
    calls = 0
    timers: list[Callable[[], None]] = []
    stopped = []
    closed = []
    fail_at = {"setup": 1, "callback": 2, "postcheck": 3}[phase]

    def clock() -> float:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise original
        return 0.0

    def arm(delay: int, callback: Callable[[], None]) -> Callable[[], None]:
        del delay
        timers.append(callback)
        return lambda: stopped.append(True)

    def fire_action() -> None:
        timers[0]()

    with pytest.raises(ValueError, match="clock failed") as caught:
        run_owned_modal(
            execute=fire_action,
            close=lambda: closed.append(True),
            callback=lambda: None,
            arm=arm,
            clock=clock,
        )
    assert caught.value is original
    assert stopped == ([] if phase == "setup" else [True, True])
    assert closed == ([] if phase == "setup" else [True])


def test_early_return_without_callback_cannot_bypass_postconditions() -> None:
    """The outer exit owns validity checks as well as cleanup."""
    closed = []
    state = _ModalState(lambda: None, lambda: closed.append(True), lambda: 0.0, 1.0)

    def return_early() -> None:
        with state:
            return

    with pytest.raises(RuntimeError, match="before its callback"):
        return_early()
    assert closed == [True]
    assert not state.scope_open


def test_watchdog_during_callback_remains_failure_and_revokes_late_work() -> None:
    """Nested deadline delivery cannot turn cleanup into success."""
    closed = []
    state = _ModalState(lambda: None, lambda: closed.append(True), lambda: 0.0, 1.0)
    state.callback = state.expire
    with pytest.raises(TimeoutError, match="cleanup is not success"), state:
        state.invoke()
    state.invoke()
    state.expire()
    assert closed == [True]
    assert not state.active
    assert not state.scope_open


def test_inner_scope_reentry_and_retired_capture_are_rejected() -> None:
    """A capture token is single-use and cannot outlive its mandatory owner."""
    state = _ModalState(lambda: None, lambda: None, lambda: 0.0, 1.0)
    scope = _DeferredCallbackScope(state)
    with state:
        with scope:
            pass
        with pytest.raises(RuntimeError, match="cannot be reused"), scope:
            pass
        state.invoke()
    with (
        pytest.raises(RuntimeError, match="active propagation scope"),
        _DeferredCallbackScope(state),
    ):
        pass


def test_original_traceback_and_secondary_object_survive_scope_exit() -> None:
    """Deferred transport retains original frames and exact secondary failures."""
    original = KeyboardInterrupt("cancel")
    secondary = AssertionError("cleanup")

    def fail_callback() -> None:
        raise original

    def fail_close() -> None:
        raise secondary

    state = _ModalState(fail_callback, fail_close, lambda: 0.0, 1.0)
    with pytest.raises(KeyboardInterrupt) as caught, state:
        state.invoke()
    assert caught.value is original
    assert "fail_callback" in [
        frame.name for frame in traceback.extract_tb(original.__traceback__)
    ]
    assert isinstance(original.__cause__, BaseExceptionGroup)
    assert secondary in original.__cause__.exceptions
    assert "fail_close" in [
        frame.name for frame in traceback.extract_tb(secondary.__traceback__)
    ]
