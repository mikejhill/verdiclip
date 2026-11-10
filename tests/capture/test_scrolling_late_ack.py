"""A bounded native re-observation never reissues the pending setter."""

from collections.abc import Callable
from functools import partial
from typing import override

import pytest
from tests.capture.test_scrolling import DelayedTarget

from verdiclip.capture.scrolling import ScrollPosition, ScrollTransaction
from verdiclip.exceptions import CaptureError


class TimedDelayedTarget(DelayedTarget):
    """A provider whose write remains pending beyond the original eight samples."""

    def __init__(self, *, delay: int | None = 12) -> None:
        super().__init__()
        self.now = 0.0
        self.delay = delay
        self.pauses = 0
        self.refreshes = 0

    @override
    def pause(self, _seconds: float) -> None:
        """Advance the monotonic test clock without issuing any native action."""
        self.now += _seconds
        self.pauses += 1
        if self.pending is not None:
            self.polls += 1
            if self.delay is not None and self.polls == self.delay:
                self.offset = round(480 * self.pending / 100)
                self.pending = None

    def refresh_position(
        self, check: Callable[[], None], pause: Callable[[float], None]
    ) -> None:
        """Model the bounded read-only observation, not a second setter."""
        self.refreshes += 1
        check()
        pause(0.5)
        check()


def operation_for(target: TimedDelayedTarget) -> ScrollTransaction:
    """Retain a verified pre-write position under the existing deadline."""
    operation = ScrollTransaction(
        target, lambda: False, pause=target.pause, clock=lambda: target.now
    )
    operation._expected = target.position()
    return operation


def test_late_acknowledgment_keeps_one_setter_and_two_stable_reads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Observed native-style delay is acknowledged without repeating the request."""
    caplog.set_level("INFO")
    target = TimedDelayedTarget()
    operation = operation_for(target)
    observed = operation._move(0)
    assert observed.vertical == 0
    assert operation._expected == observed
    assert target.moves == [0]
    assert target.polls == 12
    assert target.pauses == 13
    assert 0.64 < target.now < 2
    assert "sample=9" in caplog.text
    assert "elapsed_seconds=" in caplog.text
    assert "grace=True" in caplog.text
    assert "requested_percent=0" in caplog.text


def test_never_acknowledged_write_stays_unresolved_without_restore() -> None:
    """No progress is not proof of failure and never permits another setter."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    initial = target.position()
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert operation._expected is None
    assert (
        operation._restore(initial) == "not restored: the provider write is unresolved"
    )
    assert target.moves == [0]
    assert target.pending == 0
    assert target.pauses <= 25
    assert target.now <= 2.01
    assert target.refreshes == 1


def test_fixed_sample_cap_terminates_even_if_clock_stalls() -> None:
    """An unchanging injected clock cannot turn observation into an endless loop."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    operation._clock = lambda: 0
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert target.pauses == 17
    assert target.moves == [0]


def inject_failure(
    target: TimedDelayedTarget,
    operation: ScrollTransaction,
    failure: str,
    position: Callable[[], ScrollPosition],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a native/user-state change at a chosen observation boundary."""
    if failure == "input":
        target.input_changed = True
    elif failure == "cancel":
        operation._cancelled = lambda: True
    elif failure == "deadline":
        target.now = operation._deadline
    elif failure == "extent":
        monkeypatch.setattr(
            target,
            "position",
            lambda: ScrollPosition(position().horizontal, position().vertical, 21),
        )
    elif failure == "horizontal":
        target.horizontal += 1
    elif failure == "opposite":
        target.offset += 10
    elif failure == "overshoot":
        target.offset += 300
    else:

        def changed_geometry() -> None:
            raise CaptureError("geometry changed")

        monkeypatch.setattr(target, "check", changed_geometry)


@pytest.mark.parametrize(
    "failure",
    [
        "input",
        "cancel",
        "deadline",
        "extent",
        "horizontal",
        "opposite",
        "overshoot",
        "geometry",
    ],
)
def test_grace_preserves_every_guard_without_another_setter(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsafe later observation cannot acknowledge or retry a pending action."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    pause = target.pause
    position = target.position

    def interrupted(seconds: float) -> None:
        pause(seconds)
        if target.pauses != 10:
            return
        inject_failure(target, operation, failure, position, monkeypatch)

    operation._pause = interrupted
    with pytest.raises(CaptureError):
        operation._move(0 if failure != "overshoot" else 30)
    assert operation._expected is None
    assert target.moves == [0 if failure != "overshoot" else 30]


def test_no_progress_cannot_spend_traversal_reserve() -> None:
    """The grace cannot start after the existing no-new-traversal boundary."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    target.now = operation._deadline - 2.5
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert target.pauses == 8
    assert target.moves == [0]


def test_motion_already_seen_does_not_get_no_progress_grace() -> None:
    """An unstable moving provider retains the original eight-sample bound."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    pause = target.pause

    def moving(seconds: float) -> None:
        pause(seconds)
        target.offset -= 1

    operation._pause = moving
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert target.pauses == 8
    assert target.moves == [0]


@pytest.mark.parametrize("route", ["header", "footer", "native", "list", "restore"])
def test_non_generic_and_restore_keep_original_sample_limit(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Late acknowledgment headroom does not broaden specialized native protocols."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    if route == "header":
        target.header = target.area
    elif route == "footer":
        monkeypatch.setattr(target, "footer", target.area, raising=False)
    elif route == "native":
        monkeypatch.setattr(operation, "_native", object())
    elif route == "list":
        monkeypatch.setattr(operation, "_list_steps", object())
    if route == "restore":
        before = target.position()
        target.move(0, before_write=operation._before_write)
        action = partial(
            operation._settle,
            before,
            0,
            restoring=ScrollPosition(before.horizontal, 0, before.view_size),
        )
    else:
        action = partial(operation._move, 0)
    with pytest.raises(CaptureError, match="position is uncertain"):
        action()
    assert target.pauses == 8
    assert target.moves == [0]


def test_real_transaction_handles_one_late_ack_and_restores() -> None:
    """One slow later setter does not require a failed capture or duplicate action."""
    target = TimedDelayedTarget(delay=3)
    operation = operation_for(target)
    original = target.position()
    pause = target.pause

    def selected_delay(seconds: float) -> None:
        target.delay = 12 if len(target.moves) == 7 else 3
        pause(seconds)

    operation._pause = selected_delay
    result = operation.run()
    assert result.image == target.document
    assert result.restoration == "restored"
    assert target.position() == original
    assert len(target.moves) == 10


def test_acknowledgment_at_total_two_second_limit_cannot_succeed() -> None:
    """The grace does not reset its duration when motion finally starts."""
    target = TimedDelayedTarget(delay=16)
    operation = operation_for(target)
    pause = target.pause

    def slow_confirmation(seconds: float) -> None:
        pause(seconds)
        if target.pauses == 17:
            target.now = 2.0

    operation._pause = slow_confirmation
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert target.offset == 0
    assert target.pauses == 17
    assert operation._expected is None
    assert target.moves == [0]


def test_global_deadline_dominates_second_stable_ack_sample() -> None:
    """Even valid late motion cannot acknowledge a write after the hard deadline."""
    target = TimedDelayedTarget(delay=9)
    operation = operation_for(target)
    pause = target.pause

    def expire(seconds: float) -> None:
        pause(seconds)
        if target.pauses == 10:
            target.now = operation._deadline

    operation._pause = expire
    with pytest.raises(CaptureError, match="time limit"):
        operation._move(0)
    assert target.offset == 0
    assert operation._expected is None
    assert target.moves == [0]


def test_fresh_only_state_never_acknowledges_retained_pending_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a fresh provider reporting the exact request cannot grant ownership."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    initial = target.position()
    calls = []

    def refresh(
        check: Callable[[], None], _pause: Callable[[float], None]
    ) -> ScrollPosition:
        check()
        calls.append("fresh")
        return ScrollPosition(initial.horizontal, 0, initial.view_size)

    monkeypatch.setattr(target, "refresh_position", refresh)
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert calls == ["fresh"]
    assert operation._expected is None
    assert target.moves == [0]
    assert "unresolved" in operation._restore(initial)


@pytest.mark.parametrize(
    "failure", ["cancel", "deadline", "reserve", "duration", "input"]
)
def test_refresh_boundary_failure_stops_without_new_setter(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Read-only refresh cannot restart its clock or ignore intervening user input."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    calls = []

    def refresh(check: Callable[[], None], _pause: Callable[[float], None]) -> None:
        calls.append("refresh")
        if failure == "cancel":
            operation._cancelled = lambda: True
        elif failure == "deadline":
            target.now = operation._deadline
        elif failure == "reserve":
            target.now = operation._deadline - 2
        elif failure == "duration":
            target.now = 2
        else:
            target.input_changed = True
        check()

    monkeypatch.setattr(target, "refresh_position", refresh)
    with pytest.raises(CaptureError):
        operation._move(0)
    assert calls == ["refresh"]
    assert operation._expected is None
    assert target.moves == [0]


def test_target_without_refresh_stops_after_original_eight_reads() -> None:
    """An unsupported provider gets no unexplained passive grace."""
    target = DelayedTarget()
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    operation._expected = target.position()
    with pytest.raises(CaptureError, match="position is uncertain"):
        operation._move(0)
    assert target.moves == [0]
    assert operation._expected is None


def test_refresh_updated_retained_state_requires_two_new_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only refresh is not itself an acknowledgment observation."""
    target = TimedDelayedTarget(delay=None)
    operation = operation_for(target)
    calls = []

    def refresh(check: Callable[[], None], _pause: Callable[[float], None]) -> None:
        check()
        target.offset = 0
        target.pending = None
        calls.append(target.pauses)

    monkeypatch.setattr(target, "refresh_position", refresh)
    result = operation._move(0)
    assert calls == [8]
    assert target.pauses == 10
    assert result.vertical == 0
    assert operation._expected == result
    assert target.moves == [0]
