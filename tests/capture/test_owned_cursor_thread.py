"""Injected lifecycle tests never create a native surface or input hook."""

from collections.abc import Callable
from typing import override

import pytest
from tests.capture.owned_cursor_policy import OwnedCursorScope
from tests.capture.owned_cursor_thread import Controls, Outcome, run_owner


class FakeSurface:
    """Single natural callback and explicit cleanup outcome."""

    def __init__(self, calls: list[str], *, close_ok: bool = True) -> None:
        self.calls = calls
        self.close_ok = close_ok
        self.callback: Callable[[], None] | None = None

    def open(self, on_cursor: Callable[[], None]) -> None:
        """Register callback without native window creation."""
        self.calls.append("open")
        self.callback = on_cursor

    def check(self) -> None:
        """The ordinary fake has no callback fault."""

    def pump(self) -> None:
        """Deliver one natural-event equivalent for accounting only."""
        callback, self.callback = self.callback, None
        if callback is not None:
            callback()

    def close(self) -> bool:
        """Expose destruction outcome separately from counter balance."""
        self.calls.append("close")
        return self.close_ok


def exercise(
    *, close_ok: bool, observer: Callable[[Outcome], None]
) -> tuple[Outcome, list[str]]:
    """Run a finite injected clock with no wall-clock wait."""
    calls: list[str] = []
    ticks = iter(value / 10 for value in range(100))

    def show(value: int) -> int:
        calls.append("balance" if value else "hide")
        return 0 if value else -1

    scope = OwnedCursorScope(lambda: 1, show)
    result = run_owner(
        scope,
        FakeSurface(calls, close_ok=close_ok),
        Controls(lambda: True, lambda: False, lambda: next(ticks), observer),
    )
    return result, calls


def test_balance_precedes_native_destruction() -> None:
    """Normal retirement balances on owner path before closing surface."""
    result, calls = exercise(close_ok=True, observer=lambda _: None)
    assert calls == ["open", "hide", "balance", "close"]
    assert result.admitted
    assert result.balanced
    assert result.destroyed


def test_failed_destroy_does_not_claim_cleanup() -> None:
    """A retained native callback owner cannot become a successful result."""
    result, calls = exercise(close_ok=False, observer=lambda _: None)
    assert calls == ["open", "hide", "balance", "close"]
    assert result.failed
    assert not result.destroyed


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_observer_failure_still_retires(error: type[BaseException]) -> None:
    """An observation failure cannot skip scope or surface retirement."""

    def fail(_: Outcome) -> None:
        raise error()

    with pytest.raises(error):
        exercise(close_ok=True, observer=fail)


def test_creation_control_not_skipped_when_cancelled() -> None:
    """Synchronous callback controls survive cancellation before first pump."""
    calls: list[str] = []
    cancelled = [False]

    class FaultSurface(FakeSurface):
        @override
        def open(self, on_cursor: Callable[[], None]) -> None:
            super().open(on_cursor)
            cancelled[0] = True

        @override
        def check(self) -> None:
            raise KeyboardInterrupt

    scope = OwnedCursorScope(lambda: 1, lambda _: 0)
    with pytest.raises(KeyboardInterrupt):
        run_owner(
            scope,
            FaultSurface(calls),
            Controls(lambda: True, lambda: cancelled[0], lambda: 0.0, lambda _: None),
        )
    assert calls == ["open", "close"]


def test_admission_loop_preservation_loss_without_cursor_message() -> None:
    """Absence of a cursor message cannot hide a foreign-state change."""
    calls: list[str] = []
    checks = iter([True, False])
    scope = OwnedCursorScope(lambda: 1, lambda _: 0)
    result = run_owner(
        scope,
        FakeSurface(calls),
        Controls(lambda: next(checks), lambda: False, lambda: 0.0, lambda _: None),
    )
    assert result.failed
    assert not result.admitted
    assert calls == ["open", "close"]
