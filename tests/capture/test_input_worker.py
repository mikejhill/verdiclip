"""Dedicated input-worker lifecycle with a replay platform; no native hooks."""

from __future__ import annotations

import threading
from typing import override

import pytest
from tests.capture.input_fixture import ReplayInputPlatform

from verdiclip.capture.input_policy import InputKind
from verdiclip.capture.input_raw import RawPointer
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.input_worker import CaptureInputWorker, InputWorkerOptions
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


class ReplayRun:
    """Always own and join only a fake platform's bounded worker."""

    @staticmethod
    def make(
        platform: ReplayInputPlatform, options: InputWorkerOptions | None = None
    ) -> CaptureInputWorker:
        """Explicit injection prevents any Windows API construction."""
        return CaptureInputWorker(
            platform.anchor,
            Rect(-1920, -1080, 5760, 3240),
            options=options
            if options is not None
            else InputWorkerOptions(injected_move_mode=MoveMode.ABSOLUTE),
            platform_factory=lambda: platform,
            clock=platform.clock,
        )

    @staticmethod
    def complete(worker: CaptureInputWorker) -> None:
        """The terminal event must follow cleanup, even on an assertion failure."""
        worker.start()
        try:
            assert worker.stopped.wait(2)
            assert worker.ready.is_set() or worker.failure
        finally:
            worker.release()
            assert worker.stopped.wait(2)


def test_recorded_drag_routes_on_worker_and_cleanup_precedes_receipt() -> None:
    """The same menu trace needs no input device and does not forward a click."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    for message, point in [
        (0x200, Point(259, 194)),
        (0x201, Point(262, 168)),
        (0x200, Point(369, 244)),
        (0x202, Point(262, 168)),
    ]:
        platform.mouse(message, point)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    events = worker.drain()
    assert [event.kind for event in events] == [
        InputKind.MOVE,
        InputKind.PRESS,
        InputKind.MOVE,
        InputKind.RELEASE,
    ]
    assert events[-1].point == Point(369, 244)
    assert platform.results == [1, 1, 1, 1]
    assert not platform.forwarded
    assert worker.cleaned
    assert platform.closed
    assert platform.install_thread == platform.close_thread
    assert platform.install_thread != threading.get_ident()
    with pytest.raises(CaptureError, match="reserved"):
        worker.start()


@pytest.mark.parametrize(
    "unavailable", ["desktop", "buttons", "foreground", "install", "recipient"]
)
def test_startup_failure_cleans_partial_resources(unavailable: str) -> None:
    """A refused lease never creates a stuck selection or retries installation."""
    platform = ReplayInputPlatform()
    if unavailable == "desktop":
        platform.safe = False
    elif unavailable == "buttons":
        platform.held = True
    elif unavailable == "foreground":
        platform.owner = 0
    elif unavailable == "recipient":
        platform.recipient_supported = False
    else:
        platform.install_error = True
    worker = ReplayRun.make(platform)
    ReplayRun.complete(worker)
    assert worker.failure
    assert worker.cleaned
    assert platform.closed
    assert platform.installed is (unavailable == "install")


def test_recipient_change_during_owned_press_drains_pair_before_cleanup() -> None:
    """A UIPI refusal cancels selection without forwarding a dangling release."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    platform.mouse(0x201, platform.anchor)
    platform.actions.put(lambda: setattr(platform, "recipient_supported", False))
    platform.mouse(0x202, platform.anchor)
    ReplayRun.complete(worker)
    assert "recipient" in worker.failure
    assert platform.results == [1, 1]
    assert not platform.forwarded
    assert any(event.kind is InputKind.CANCEL for event in worker.drain())
    assert worker.cleaned
    assert platform.closed


def test_recipient_becoming_unsupported_during_install_never_publishes_ready() -> None:
    """Recheck eligibility after setup, before the GUI creates selection windows."""

    class ChangingRecipient(ReplayInputPlatform):
        @override
        def recipient_safe(self, _foreground: int) -> bool:
            return not self.installed

    platform = ChangingRecipient()
    worker = ReplayRun.make(platform)
    ReplayRun.complete(worker)
    assert not worker.ready.is_set()
    assert worker.failure
    assert worker.cleaned
    assert platform.closed


@pytest.mark.parametrize("watchdog", ["heartbeat", "hard"])
def test_independent_watchdogs_release_when_gui_is_silent(watchdog: str) -> None:
    """No GUI processing is needed to end the native lease's time budget."""
    platform = ReplayInputPlatform()
    settings = InputWorkerOptions(
        max_seconds=0.1 if watchdog == "hard" else 1,
        heartbeat_seconds=0.1 if watchdog == "heartbeat" else 1,
    )
    worker = ReplayRun.make(platform, settings)
    ReplayRun.complete(worker)
    assert worker.cleaned
    assert "time limit" in worker.failure or "UI stopped responding" in worker.failure
    assert worker.drain()[0].kind is InputKind.CANCEL


def test_release_drains_owned_pair_without_forwarding_or_late_selection() -> None:
    """Cancelling after down swallows its matching up but creates no screenshot."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    platform.mouse(0x201, platform.anchor)
    platform.actions.put(worker.release)
    platform.mouse(0x202, platform.anchor)
    ReplayRun.complete(worker)
    assert platform.results == [1, 1]
    assert [event.kind for event in worker.drain()] == [InputKind.PRESS]
    assert not platform.forwarded
    assert worker.cleaned


def test_unknown_injection_and_callback_failure_are_bounded() -> None:
    """Unknown move semantics and platform bugs cannot forward a target click."""
    for broken in (False, True):
        platform = ReplayInputPlatform()
        platform.cursor_error = broken
        worker = ReplayRun.make(platform, InputWorkerOptions())
        platform.mouse(0x200, Point(300, 200))
        platform.actions.put(worker.release)
        ReplayRun.complete(worker)
        assert platform.results == [1]
        assert not platform.forwarded
        assert worker.cleaned
        assert worker.drain()[0].kind is InputKind.CANCEL


def test_cleanup_failure_is_not_success() -> None:
    """A caller must withhold output/retry until native cleanup is confirmed."""
    platform = ReplayInputPlatform()
    platform.close_ok = False
    worker = ReplayRun.make(platform)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    assert not worker.cleaned
    assert "cleanup" in worker.failure


def test_hardware_moves_wait_for_explicit_raw_semantics() -> None:
    """A non-injected hook flag alone never supplies a guessed movement mode."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform, InputWorkerOptions())
    platform.mouse(0x200, Point(999, 999), injected=False)
    platform.raw(RawPointer(Point(3, 5), MoveMode.RELATIVE_DELTA, (), wheel=False))
    platform.mouse(0x201, platform.anchor, injected=False)
    platform.raw(
        RawPointer(Point(0, 0), MoveMode.RELATIVE_DELTA, ((0x201, 0),), wheel=False)
    )
    platform.mouse(0x202, platform.anchor, injected=False)
    platform.raw(
        RawPointer(Point(0, 0), MoveMode.RELATIVE_DELTA, ((0x202, 0),), wheel=False)
    )
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    events = worker.drain()
    assert events[-1].kind is InputKind.RELEASE
    assert events[-1].point == Point(265, 173)
    assert not platform.forwarded
    assert worker.cleaned


def test_missing_raw_reports_cancel_instead_of_freezing_selection() -> None:
    """Unsupported pointer delivery cannot strand the overlay or guess a click."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform, InputWorkerOptions())
    platform.mouse(0x200, Point(999, 999), injected=False)
    ReplayRun.complete(worker)
    assert "pointer source" in worker.failure
    assert worker.drain()[0].kind is InputKind.CANCEL
    assert worker.cleaned


def test_semantic_key_held_before_capture_refuses_ownership() -> None:
    """An existing Enter autorepeat must never become the capture's key pair."""
    platform = ReplayInputPlatform()
    platform.held_keys = frozenset({0x0D})
    worker = ReplayRun.make(platform)
    ReplayRun.complete(worker)
    assert not worker.ready.is_set()
    assert "Release selection keys" in worker.failure
    assert worker.cleaned


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_unbounded_input_budgets_rejected(duration: float) -> None:
    """There is no infinite interception setting."""
    with pytest.raises(ValueError, match="finite"):
        InputWorkerOptions(max_seconds=duration)


def test_keyboard_selection_routes_without_forwarding_selection_pairs() -> None:
    """Arrow/Enter and Escape ownership uses semantic commands, never OS replay."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    for message, code in ((0x100, 0x27), (0x101, 0x27), (0x100, 0x0D), (0x101, 0x0D)):
        platform.key(message, code)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    events = worker.drain()
    assert events[0].kind is InputKind.MOVE
    assert events[0].point.x > platform.anchor.x
    assert events[-1].kind is InputKind.ENTER
    assert platform.results == [1, 1, 1, 1]
    assert not platform.forwarded


def test_missing_release_has_finite_drain_and_no_false_cleanup_success() -> None:
    """An absent hardware up cannot retain interception indefinitely."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    platform.mouse(0x201, platform.anchor)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    assert "release reached its deadline" in worker.failure
    assert worker.cleaned
    assert platform.closed


def test_slow_consumer_exhausts_bounded_queue_and_retires_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck GUI cannot accumulate unbounded movement history."""
    monkeypatch.setattr(CaptureInputWorker, "QUEUE_LIMIT", 2)
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    for x in (270, 280, 290, 300):
        platform.mouse(0x200, Point(x, 200))
    ReplayRun.complete(worker)
    assert "event budget" in worker.failure
    assert len(worker.drain()) == 2
    assert worker.cleaned


def test_cleanup_exception_withholds_positive_receipt() -> None:
    """A raised cleanup error has the same retry barrier as a negative receipt."""

    class BrokenCleanup(ReplayInputPlatform):
        @override
        def close(self) -> bool:
            raise OSError("Fixture cleanup unavailable")

    platform = BrokenCleanup()
    worker = ReplayRun.make(platform)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    assert not worker.cleaned
    assert worker.failure == "Capture input cleanup failed"


@pytest.mark.parametrize(
    "settings", [(121.0, 1.0, 0.5), (120.0, 3.0, 0.5), (120.0, 1.0, 2.0)]
)
def test_finite_but_excessive_budgets_are_refused(
    settings: tuple[float, float, float],
) -> None:
    """Finite values cannot disable the independent upper safety limits."""
    with pytest.raises(ValueError, match="safety limits"):
        InputWorkerOptions(*settings)


def test_controls_navigation_and_release_latch_are_independent_of_gui_polling() -> None:
    """A draining owned pair cannot still authorize a provider scroll operation."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    worker.set_controls_mode(enabled=True)
    platform.shift = True
    platform.key(0x100, 0x09)
    platform.key(0x101, 0x09)
    platform.key(0x100, 0x27)
    platform.key(0x101, 0x27)
    platform.mouse(0x201, platform.anchor)
    active: list[bool] = []
    platform.actions.put(lambda: active.append(worker.active))
    platform.actions.put(worker.release)
    platform.actions.put(lambda: active.append(worker.active))
    platform.mouse(0x202, platform.anchor)
    ReplayRun.complete(worker)
    events = worker.drain()
    assert events[0].kind is InputKind.NEXT
    assert events[0].reason == "previous"
    assert events[1].point == platform.anchor
    assert active == [True, False]
    assert not worker.active
    worker.set_controls_mode(enabled=False)
    assert worker.cleaned
    assert not platform.forwarded
