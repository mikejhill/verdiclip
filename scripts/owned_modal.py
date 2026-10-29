"""Bound an owned modal test loop; no Qt imports or application-window cleanup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import pytest

type Stop = Callable[[], None]
type Arm = Callable[[int, Callable[[], None]], Stop]


def _failure_priority(error: BaseException) -> int:
    """Honor control flow, then real failures, then standalone pytest outcomes."""
    if isinstance(
        error, (KeyboardInterrupt, SystemExit, GeneratorExit, pytest.exit.Exception)
    ):
        return 0
    if isinstance(error, (pytest.skip.Exception, pytest.xfail.Exception)):
        return 2
    if isinstance(error, (Exception, pytest.fail.Exception)):
        return 1
    return 0  # Unknown BaseException subclasses retain their control-flow status.


@dataclass(slots=True)
class _DeferredCallbackScope:
    """Temporarily intercept all failure kinds across a Qt callback boundary.

    This deliberately suppresses only until the enclosing active owner exits.
    It is not a generic ignore-errors context: entry requires a single-use
    owner whose exit performs cleanup, propagation and validity checks.
    """

    owner: _ModalState
    used: bool = False
    entered: bool = False

    def __enter__(self) -> None:
        if not self.owner.scope_open:
            raise RuntimeError("Deferred callback requires an active propagation scope")
        if self.used:
            raise RuntimeError("Deferred callback scope cannot be reused")
        self.used = True
        self.entered = True

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del kind, traceback
        if not self.entered or not self.owner.scope_open:
            raise RuntimeError("Deferred callback scope has no active owner")
        self.entered = False
        if error is None:
            return False
        self.owner.errors.append(error)
        return True


@dataclass(slots=True)
class _ModalState:
    """Defer failures across Qt callbacks; never resolve or discard cancellation.

    PySide cannot propagate an arbitrary Python callback failure through the
    nested C++ event loop. We retain even KeyboardInterrupt/SystemExit until the
    exact owned dialog is rejected and timers are disarmed. The outer context
    unconditionally calls raise_deferred outside Qt on every exit. Control has
    priority over real failures; a skip/xfail can never hide a retained failure.
    Every secondary object remains inspectable in the explicit cause group.
    """

    callback: Callable[[], None]
    close: Callable[[], None]
    clock: Callable[[], float]
    deadline: float
    active: bool = True
    started: bool = False
    finished: bool = False
    expired: bool = False
    closed: bool = False
    errors: list[BaseException] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)
    scope_started: bool = False
    scope_open: bool = False

    def __enter__(self) -> Self:
        if self.scope_started:
            raise RuntimeError("Owned modal propagation scope cannot be reused")
        self.scope_started = True
        self.scope_open = True
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Every scope exit performs cleanup and propagates retained control."""
        del kind, traceback
        if error is not None:
            self.errors.append(error)
        try:
            self.cleanup(self.stops)
            self.raise_deferred()
            if self.expired or self.clock() >= self.deadline:
                raise TimeoutError(
                    "Owned modal test exceeded its deadline; cleanup is not success"
                )
            if not self.finished:
                raise RuntimeError(
                    "Owned modal test exited before its callback completed"
                )
        finally:
            self.scope_open = False

    def reject_owned(self) -> None:
        """Reject this exact fixture once; retain failures for pytest outside Qt."""
        if self.closed:
            return
        with _DeferredCallbackScope(self):
            self.close()
            self.closed = True

    def invoke(self) -> None:
        """Close even when the scheduled assertion/action raises."""
        if not self.active or self.started:
            return
        self.started = True
        try:
            with _DeferredCallbackScope(self):
                if self.clock() >= self.deadline:
                    self.expire()
                    return
                self.callback()
        finally:
            self.finished = True
            self.reject_owned()

    def expire(self) -> None:
        """Exit a responsive nested loop and mark its result as failure."""
        if self.active:
            self.active = False
            self.expired = True
            self.reject_owned()

    def cleanup(self, stops: list[Stop]) -> None:
        """Disarm independently; stale queued callbacks lose ownership first."""
        self.active = False
        for stop in stops:
            with _DeferredCallbackScope(self):
                stop()
        self.reject_owned()

    def raise_deferred(self) -> None:
        """Prioritize retained control flow and keep every other failure as cause."""
        if not self.errors:
            return
        primary = min(self.errors, key=_failure_priority)
        secondary = [error for error in self.errors if error is not primary]
        if secondary:
            # Re-raise the exact control object, rather than hiding cancellation
            # behind an earlier AssertionError. Retain full secondary objects.
            raise primary from BaseExceptionGroup(
                "Other owned modal failures", secondary
            )
        raise primary


def run_owned_modal(  # noqa: PLR0913 - explicit injected IO keeps this Qt-independent
    *,
    execute: Callable[[], object],
    close: Callable[[], None],
    callback: Callable[[], None],
    arm: Arm,
    timeout_ms: int = 5000,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Fail on deadline/missing callback; never call timeout cleanup a success.

    Timers must be test-owned single-shot timers. They cannot preempt a blocked
    GUI thread: the outer CI deadline is still required. The monotonic postcheck
    rejects late success even when the timer itself was delayed.
    """
    if timeout_ms <= 0:
        raise ValueError("The owned modal deadline must be positive")
    deadline = clock() + timeout_ms / 1000
    state = _ModalState(callback, close, clock, deadline)
    with state:
        state.stops.append(arm(0, state.invoke))
        state.stops.append(arm(timeout_ms, state.expire))
        execute()
