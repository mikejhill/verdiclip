"""Bound an owned modal test loop; no Qt imports or application-window cleanup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

type Stop = Callable[[], None]
type Arm = Callable[[int, Callable[[], None]], Stop]


@dataclass(slots=True)
class _ModalState:
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

    def reject_owned(self) -> None:
        """Reject this exact fixture once; retain failures for pytest outside Qt."""
        if self.closed:
            return
        try:
            self.close()
            self.closed = True
        except BaseException as error:  # noqa: BLE001 - re-raised outside Qt after cleanup
            self.errors.append(error)

    def invoke(self) -> None:
        """Close even when the scheduled assertion/action raises."""
        if not self.active or self.started:
            return
        self.started = True
        try:
            if self.clock() >= self.deadline:
                self.expire()
                return
            self.callback()
        except BaseException as error:  # noqa: BLE001 - re-raised outside Qt after cleanup
            self.errors.append(error)
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
            try:
                stop()
            except BaseException as error:  # noqa: BLE001 - re-raised outside Qt after cleanup
                self.errors.append(error)
        self.reject_owned()


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
    stops: list[Stop] = []
    try:
        stops.append(arm(0, state.invoke))
        stops.append(arm(timeout_ms, state.expire))
        execute()
    except BaseException as error:  # noqa: BLE001 - re-raised outside Qt after cleanup
        state.errors.append(error)
    finally:
        state.cleanup(stops)
    if state.errors:
        original = state.errors[0]
        for secondary in state.errors[1:]:
            kind = type(secondary).__name__
            original.add_note(f"Additional owned modal cleanup error: {kind}")
        raise original
    if state.expired or clock() >= deadline:
        raise TimeoutError(
            "Owned modal test exceeded its deadline; cleanup is not success"
        )
    if not state.finished:
        raise RuntimeError("Owned modal test exited before its callback completed")
