"""Injected owner-thread lifecycle; native surface adapter follows separately."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tests.capture.owned_cursor_policy import OwnedCursorScope


class Surface(Protocol):
    """Native lifetime stays owned until close reports confirmed destruction."""

    def open(self, on_cursor: Callable[[], None]) -> None:
        """Create nonactivating hittable HWND and retain its callback."""

    def check(self) -> None:
        """Reraise any callback fault outside native callback dispatch."""

    def pump(self) -> None:
        """Pump bounded work without injecting input or setting cursor shape."""

    def close(self) -> bool:
        """Destroy only owned HWND and unregister owned class on this thread."""


@dataclass
class Outcome:
    """Logical result only; independent restoration and rendering are external."""

    admitted: bool = False
    balanced: bool = False
    destroyed: bool = False
    failed: bool = False


# Strong retention prevents native callbacks from outliving their Python owner.
_UNCONFIRMED: list[Surface] = []


@dataclass(frozen=True)
class Controls:
    """Injected controls keep the production input broker outside this skeleton."""

    safe: Callable[[], bool]
    cancelled: Callable[[], bool]
    clock: Callable[[], float]
    observe: Callable[[Outcome], None]
    active: Callable[[], None] = lambda: None


class CheckedClock:
    """Reject unavailable/regressing time rather than extending observation."""

    def __init__(self, source: Callable[[], float]) -> None:
        self.source = source
        self.previous = float("-inf")

    def __call__(self) -> float:
        """Read a finite monotonic value."""
        value = self.source()
        if not math.isfinite(value) or value < self.previous:
            raise RuntimeError("invalid-owner-clock")
        self.previous = value
        return value


def run_owner(
    scope: OwnedCursorScope,
    surface: Surface,
    controls: Controls,
) -> Outcome:
    """Run entirely on dedicated thread; retire scope before closing native HWND.

    Admission has one second, accepted observation one second. These clocks never
    extend after a refusal. Caller owns process lifetime if native APIs block.
    """
    result = Outcome()
    safe, cancelled, observe = controls.safe, controls.cancelled, controls.observe
    clock = CheckedClock(controls.clock)

    def permitted() -> bool:
        return not cancelled() and safe() and not cancelled()

    opened = False
    admission_attempted = False
    started = clock()
    deadline = started + 1.0

    def on_cursor() -> None:
        nonlocal admission_attempted
        if admission_attempted or cancelled() or clock() >= deadline:
            return
        admission_attempted = True
        result.admitted = scope.admit(permitted, natural=True)

    try:
        if not permitted():
            return result
        # Retain before entering native creation, including partial-create faults.
        _UNCONFIRMED.append(surface)
        opened = True
        surface.open(on_cursor)
        surface.check()
        while not cancelled() and clock() < deadline and not admission_attempted:
            if not permitted():
                result.failed = True
                break
            surface.pump()
        if not result.admitted:
            return result
        observe(result)
        deadline = clock() + 1.0
        while not cancelled() and clock() < deadline:
            if not scope.observe(permitted):
                result.failed = True
                break
            controls.active()
            surface.pump()
        return result
    finally:
        retire(scope, surface, result, opened=opened)


def retire(
    scope: OwnedCursorScope, surface: Surface, result: Outcome, *, opened: bool
) -> None:
    """Always attempt owned surface destruction after balancing scope."""
    try:
        scope.retire()
        result.balanced = scope.balance_confirmed
        result.failed |= scope.failed
    finally:
        if opened:
            confirmed = False
            try:
                confirmed = surface.close()
                result.destroyed = confirmed
            finally:
                if confirmed:
                    _UNCONFIRMED.remove(surface)
                else:
                    result.failed = True
