"""Asynchronous acquisition policy tested without windows or target side effects."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from verdiclip.capture.reviewed import CaptureCompleteness
from verdiclip.capture.scroll.acquisition import (
    AcquisitionState,
    ScrollAcquisition,
    WorkKind,
)
from verdiclip.capture.scroll.models import ScrollLimits, ScrollTarget, StopReason
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class Run:
    """Simulate provider callbacks with known document pixels and a fake clock."""

    TARGET = ScrollTarget("fixture", "Example", Rect(0, 0, 96, 48))

    def __init__(
        self,
        path: Path,
        *,
        max_seconds: float = 120,
        limits: ScrollLimits | None = None,
    ) -> None:
        """Create one private session; no callback schedules work automatically."""
        data = bytearray(hashlib.shake_256(b"acquisition-golden").digest(96 * 240 * 4))
        data[3::4] = bytes([255]) * (96 * 240)
        self.image = QImage(data, 96, 240, QImage.Format.Format_ARGB32).copy()
        self.session = ScrollSession(path, limits=limits)
        self.driver = ScrollAcquisition(self.session, max_seconds=max_seconds)
        self.now = 0.0

    def start(self) -> None:
        """Represent explicit Start after target and ROI confirmation."""
        self.driver.start_confirmed(
            self.TARGET, now=self.now, taken_at=datetime(2026, 10, 25, tzinfo=UTC)
        )

    def settle(self, top: int = 0, *, end: bool = False) -> None:
        """Deliver three independent observations at scheduler-controlled times."""
        for _ in range(3):
            self.now += 0.1
            work = self.driver.sample(now=self.now)
            assert work.kind is WorkKind.SAMPLE
            assert self.driver.complete_sample(
                work,
                self.image.copy(0, top, 96, 48),
                target=self.TARGET,
                now=self.now,
                at_end=end,
            )

    def move(self) -> None:
        """Report a successful verified-container step, without invoking an app."""
        self.now += 0.1
        work = self.driver.step(self.TARGET, safe_to_scroll=True, now=self.now)
        assert work.kind is WorkKind.SCROLL
        assert self.driver.complete_move(work, now=self.now, succeeded=True)


def test_confirmed_pipeline_delivers_only_verified_pixels(tmp_path: Path) -> None:
    """Settling plus registration preserve every source row without seams."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    assert run.driver.state is AcquisitionState.READY
    run.move()
    run.settle(24)
    assert run.driver.state is AcquisitionState.READY
    run.driver.stop()
    accepted = run.session.accept()
    assert accepted.image == run.image.copy(0, 0, 96, 72)
    assert accepted.completeness is CaptureCompleteness.PARTIAL
    assert accepted.stop_reason == StopReason.USER_STOP.value
    run.session.close()


def test_reported_end_requires_two_settled_stationary_observations(
    tmp_path: Path,
) -> None:
    """A provider's first end flag never completes a moving viewport."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.move()
    run.settle(24, end=True)
    assert run.session.state is ScrollState.CAPTURING
    run.settle(24, end=True)
    assert run.session.state is ScrollState.PAUSED
    run.settle(24, end=True)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.driver.stop_reason is StopReason.REPORTED_END
    assert run.session.accept().completeness is CaptureCompleteness.COMPLETE
    run.session.close()


def test_uncertain_end_claim_pauses_instead_of_retrying_or_moving(
    tmp_path: Path,
) -> None:
    """Unrelated pixels cannot become a complete document through an end flag."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.move()
    run.settle(120, end=True)
    assert run.driver.state is AcquisitionState.PAUSED
    with pytest.raises(CaptureError, match="not ready"):
        run.driver.step(run.TARGET, safe_to_scroll=True, now=run.now)
    run.driver.stop()
    assert run.session.accept().stop_reason == StopReason.UNCERTAIN.value
    run.session.close()


def test_single_flight_and_late_completion_after_stop(tmp_path: Path) -> None:
    """No producer can queue ahead or commit after an explicit Stop."""
    run = Run(tmp_path)
    with pytest.raises(CaptureError, match="not waiting"):
        run.driver.sample(now=0)
    run.start()
    run.settle()
    run.move()
    run.now += 0.1
    work = run.driver.sample(now=run.now)
    with pytest.raises(CaptureError, match="in flight"):
        run.driver.sample(now=run.now)
    run.driver.stop()
    assert not run.driver.complete_sample(
        work, run.image, target=run.TARGET, now=run.now + 1
    )
    assert run.session.accept().image.height() == 48
    run.session.close()


@pytest.mark.parametrize("changed", [False, True])
def test_each_step_requires_fresh_target_safety(
    tmp_path: Path, *, changed: bool
) -> None:
    """Unknown safety or a resized target denies movement and keeps a prefix."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    target = (
        ScrollTarget("other", "Other", run.TARGET.viewport) if changed else run.TARGET
    )
    with pytest.raises(CaptureError, match="safety"):
        run.driver.step(target, safe_to_scroll=changed, now=run.now)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.session.accept().stop_reason == StopReason.TARGET_CHANGED.value
    run.session.close()


@pytest.mark.parametrize("succeeded", [False, True])
def test_move_failure_or_timeout_cannot_leave_acquisition_stuck(
    tmp_path: Path, *, succeeded: bool
) -> None:
    """Both a failed response and a silent provider preserve useful pixels."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    work = run.driver.step(run.TARGET, safe_to_scroll=True, now=run.now)
    if succeeded:
        run.driver.tick(now=run.now + 3)
        assert not run.driver.complete_move(work, now=run.now + 3, succeeded=True)
    else:
        assert run.driver.complete_move(work, now=run.now, succeeded=False)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.session.accept().stop_reason == StopReason.ERROR.value
    run.session.close()


def test_silent_initial_sample_fails_without_a_false_result(tmp_path: Path) -> None:
    """No baseline pixels means no reviewable or allegedly complete capture."""
    run = Run(tmp_path)
    run.start()
    work = run.driver.sample(now=0)
    run.driver.tick(now=2)
    assert run.driver.state is AcquisitionState.FAILED
    assert run.session.state is ScrollState.IDLE
    assert not run.driver.complete_sample(work, run.image, target=run.TARGET, now=3)
    run.driver.cancel()
    assert not list(tmp_path.iterdir())


def test_overall_deadline_includes_time_spent_between_steps(tmp_path: Path) -> None:
    """A ready-but-idle run cannot evade the wall-time budget."""
    run = Run(tmp_path, max_seconds=1)
    run.start()
    run.settle()
    run.driver.tick(now=1)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.session.accept().stop_reason == StopReason.LIMIT.value
    run.session.close()


def test_cancel_invalidates_work_and_does_not_turn_into_failure_on_stop(
    tmp_path: Path,
) -> None:
    """A cancelled run releases only its spool and remains terminal."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    work = run.driver.step(run.TARGET, safe_to_scroll=True, now=run.now)
    run.driver.cancel()
    run.driver.stop()
    assert run.driver.state is AcquisitionState.CANCELLED
    assert not run.driver.complete_move(work, now=run.now + 1, succeeded=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("reason", [StopReason.REPORTED_END, StopReason.USER_STOP])
def test_backend_interrupt_cannot_manufacture_end_or_user_intent(
    tmp_path: Path, reason: StopReason
) -> None:
    """Only session evidence establishes completion or explicit Stop semantics."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    with pytest.raises(ValueError, match="cannot assert"):
        run.session.interrupt(reason)
    run.session.close()


def test_baseline_storage_limit_is_reported_without_pixels(tmp_path: Path) -> None:
    """A bounded disk refusal before baseline cannot create a review result."""
    run = Run(tmp_path, limits=ScrollLimits(max_disk_bytes=100))
    run.start()
    run.settle()
    assert run.driver.state is AcquisitionState.FAILED
    assert run.driver.stop_reason is StopReason.LIMIT
    assert run.session.state is ScrollState.IDLE
    run.driver.cancel()


def test_invalid_sample_size_preserves_prefix_and_reports_target_change(
    tmp_path: Path,
) -> None:
    """A resized or unavailable capture is not fed into overlap registration."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.move()
    run.now += 0.1
    work = run.driver.sample(now=run.now)
    assert run.driver.complete_sample(work, QImage(), target=run.TARGET, now=run.now)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.driver.stop_reason is StopReason.TARGET_CHANGED
    assert run.session.accept().image.height() == 48
    run.session.close()


def test_acquisition_cannot_restart_or_use_a_cancelled_session(tmp_path: Path) -> None:
    """Repeated Start never resets deadlines, consent, or pending work."""
    run = Run(tmp_path)
    run.start()
    with pytest.raises(CaptureError, match="already started"):
        run.start()
    run.driver.cancel()
    with pytest.raises(CaptureError, match="unused"):
        ScrollAcquisition(run.session).start_confirmed(
            run.TARGET, now=0, taken_at=datetime.now(UTC)
        )


def test_invalid_clock_cannot_reset_a_live_deadline(tmp_path: Path) -> None:
    """Backward or nonfinite timestamps cannot prolong platform work."""
    run = Run(tmp_path)
    run.start()
    run.driver.tick(now=1)
    for now in (0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="ordered finite"):
            run.driver.tick(now=now)
    run.driver.tick(now=2)
    assert run.driver.state is AcquisitionState.FAILED
    assert run.driver.stop_reason is StopReason.UNCERTAIN
    run.driver.cancel()


def test_stop_before_baseline_has_no_spool_or_false_result(tmp_path: Path) -> None:
    """An early Stop retains its intent and does not emit an empty image."""
    run = Run(tmp_path)
    run.start()
    run.driver.stop()
    assert run.driver.stop_reason is StopReason.USER_STOP
    assert run.session.state is ScrollState.IDLE
    assert not list(tmp_path.iterdir())
    run.driver.cancel()


@pytest.mark.parametrize("capacity", ["frames", "pixels"])
def test_known_session_capacity_prevents_another_scroll(
    tmp_path: Path, capacity: str
) -> None:
    """Do not move a target when even one more committed row is disallowed."""
    limits = (
        ScrollLimits(max_frames=1)
        if capacity == "frames"
        else ScrollLimits(max_pixels=96 * 48)
    )
    run = Run(tmp_path, limits=limits)
    run.start()
    run.settle()
    with pytest.raises(CaptureError, match="resource limit"):
        run.driver.step(run.TARGET, safe_to_scroll=True, now=run.now)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.driver.stop_reason is StopReason.LIMIT
    assert run.session.accept().image == run.image.copy(0, 0, 96, 48)
    run.session.close()


def test_custom_session_time_limit_expires_without_more_input(tmp_path: Path) -> None:
    """A looser coordinator default cannot extend the session's own budget."""
    run = Run(tmp_path, limits=ScrollLimits(max_seconds=1))
    run.start()
    run.settle()
    run.driver.tick(now=1.4)
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.driver.stop_reason is StopReason.LIMIT
    with pytest.raises(CaptureError, match="not ready"):
        run.driver.step(run.TARGET, safe_to_scroll=True, now=1.4)
    run.session.close()


def test_callback_reports_actual_target_instead_of_inheriting_request_identity(
    tmp_path: Path,
) -> None:
    """Same-sized pixels from another target cannot acquire the old identity."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.move()
    run.now += 0.1
    work = run.driver.sample(now=run.now)
    changed = ScrollTarget("other-process", "Other", run.TARGET.viewport)
    assert run.driver.complete_sample(
        work, run.image.copy(0, 24, 96, 48), target=changed, now=run.now
    )
    assert run.driver.state is AcquisitionState.REVIEW
    assert run.driver.stop_reason is StopReason.TARGET_CHANGED
    assert run.session.accept().image.height() == 48
    run.session.close()


def test_known_viewport_memory_limit_refuses_sampling_before_allocation(
    tmp_path: Path,
) -> None:
    """An oversized target cannot request three retained baseline images."""
    run = Run(tmp_path, limits=ScrollLimits(max_frame_pixels=100))
    run.start()
    assert run.driver.state is AcquisitionState.FAILED
    assert run.driver.stop_reason is StopReason.LIMIT
    with pytest.raises(CaptureError, match="not waiting"):
        run.driver.sample(now=0)
    assert not list(tmp_path.iterdir())
    run.driver.cancel()


def test_custom_session_deadline_includes_initial_settling(tmp_path: Path) -> None:
    """The acquisition deadline starts at consent, before baseline is available."""
    run = Run(tmp_path, limits=ScrollLimits(max_seconds=0.2))
    run.start()
    work = run.driver.sample(now=0.1)
    assert run.driver.complete_sample(
        work, run.image.copy(0, 0, 96, 48), target=run.TARGET, now=0.1
    )
    run.driver.tick(now=0.2)
    assert run.driver.state is AcquisitionState.FAILED
    assert run.driver.stop_reason is StopReason.LIMIT
    run.driver.cancel()
