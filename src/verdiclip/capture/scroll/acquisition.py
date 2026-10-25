"""Offscreen acquisition orchestration; platform work stays asynchronous."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from PySide6.QtGui import QImage

from verdiclip.capture.scroll.matching import MatchStatus
from verdiclip.capture.scroll.models import (
    ScrollFrame,
    ScrollLimitError,
    ScrollTarget,
    StopReason,
)
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.capture.scroll.settling import FrameSettler, SettleStatus
from verdiclip.exceptions import CaptureError


class AcquisitionState(StrEnum):
    """Platform acquisition is distinct from storage and review ownership."""

    IDLE = "idle"
    SETTLING = "settling"
    READY = "ready_for_step"
    MOVING = "moving"
    PAUSED = "paused"
    REVIEW = "review"
    FAILED = "failed_without_pixels"
    CANCELLED = "cancelled"


class WorkKind(StrEnum):
    """Work descriptions do not themselves call UIA or inject input."""

    SAMPLE = "sample_viewport"
    SCROLL = "scroll_selected_container"


@dataclass(frozen=True, slots=True)
class AcquisitionWork:
    """One target-bound operation; only its original completion is accepted.

    Adapters must revalidate target identity and safety immediately before
    executing work. Queued work does not authorize reusing stale native handles
    or provider objects.
    """

    serial: int
    kind: WorkKind
    target: ScrollTarget
    issued_at: float


class ScrollAcquisition:
    """Coordinate confirmed capture with one outstanding platform operation.

    The UI calls start_confirmed only after explicit Start. A platform adapter
    executes returned work off the GUI thread with its own process deadline;
    this object never calls an external provider, activates windows or closes
    content. A timer must call tick even when a provider stops responding.
    """

    MOVE_TIMEOUT_SECONDS = 2.0

    def __init__(self, session: ScrollSession, *, max_seconds: float = 120) -> None:
        """Keep storage owned by the caller and set a finite overall deadline."""
        if not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("Acquisition duration must be finite and positive")
        self._session = session
        self._duration = min(max_seconds, session.limits.max_seconds)
        self._settler = FrameSettler(max_frame_pixels=session.limits.max_frame_pixels)
        self._settling_request = 0
        self._state = AcquisitionState.IDLE
        self._target: ScrollTarget | None = None
        self._taken_at: datetime | None = None
        self._pending: AcquisitionWork | None = None
        self._serial = 0
        self._sequence = 0
        self._started = self._observed = 0.0
        self._reason: StopReason | None = None

    @property
    def state(self) -> AcquisitionState:
        """Expose actionable progress without reading native focus or input."""
        return self._state

    @property
    def stop_reason(self) -> StopReason | None:
        """Expose why acquisition stopped, including failures before baseline."""
        return self._reason

    def _clock(self, now: float) -> None:
        """Use one nondecreasing monotonic clock for all callbacks and timers."""
        if not math.isfinite(now) or now < self._observed:
            raise ValueError("Acquisition needs ordered finite monotonic time")
        self._observed = now

    def start_confirmed(
        self, target: ScrollTarget, *, now: float, taken_at: datetime
    ) -> None:
        """Reserve the selected target after consent, before issuing any work."""
        if self._state is not AcquisitionState.IDLE:
            raise CaptureError("Acquisition has already started")
        if self._session.state is not ScrollState.IDLE:
            raise CaptureError("Acquisition needs an unused scrolling session")
        if taken_at.utcoffset() is None or not math.isfinite(now):
            raise ValueError("Capture needs aware wall time and finite monotonic time")
        self._target, self._taken_at = target, taken_at
        self._started = self._observed = now
        if target.viewport.width * target.viewport.height > min(
            self._session.limits.max_frame_pixels, self._session.limits.max_pixels
        ):
            self._state = AcquisitionState.FAILED
            self._reason = StopReason.LIMIT
            return
        self._begin_settling(now)

    def _begin_settling(self, now: float) -> None:
        """Start a fresh observation generation after a completed scroll step."""
        target = self._target
        if target is None:
            raise CaptureError("Acquisition target is unavailable")
        self._settling_request = self._settler.begin(
            target, token=max(1, self._session.token), now=now
        )
        self._state = AcquisitionState.SETTLING

    def _work(self, kind: WorkKind, now: float) -> AcquisitionWork:
        """Reserve one asynchronous request; caller must not queue ahead."""
        if self._pending is not None or self._target is None:
            raise CaptureError("Acquisition already has work in flight")
        self._serial += 1
        work = AcquisitionWork(self._serial, kind, self._target, now)
        self._pending = work
        return work

    def sample(self, *, now: float) -> AcquisitionWork:
        """Request a single ROI sample; cadence belongs to the scheduler."""
        self.tick(now=now)
        if self._state is not AcquisitionState.SETTLING:
            raise CaptureError("Acquisition is not waiting for a viewport")
        return self._work(WorkKind.SAMPLE, now)

    def step(
        self, target: ScrollTarget, *, safe_to_scroll: bool, now: float
    ) -> AcquisitionWork:
        """Require fresh adapter safety evidence before each selected-container step.

        The adapter must prove the original target is still present, visible,
        geometrically unchanged and permitted, with no user interference.
        Unknown evidence must be false. No fallback clicks or wheel injection
        are authorized by this work item.
        """
        self.tick(now=now)
        if self._state is not AcquisitionState.READY:
            raise CaptureError("Acquisition is not ready for another scroll")
        if target != self._target or not safe_to_scroll:
            self._interrupt(StopReason.TARGET_CHANGED)
            raise CaptureError("Scrolling target safety could not be verified")
        if not self._session.check_acquisition_budget(now, for_step=True):
            self._adopt_review()
            raise CaptureError("Scrolling acquisition has reached its resource limit")
        work = self._work(WorkKind.SCROLL, now)
        self._state = AcquisitionState.MOVING
        return work

    def complete_move(
        self, work: AcquisitionWork, *, now: float, succeeded: bool
    ) -> bool:
        """A completed provider step permits observation, never presumed pixels."""
        if work is not self._pending or work.kind is not WorkKind.SCROLL:
            return False
        self.tick(now=now)
        if self._state is not AcquisitionState.MOVING:
            return False
        self._pending = None
        if not succeeded:
            self._interrupt(StopReason.ERROR)
        else:
            self._begin_settling(now)
        return True

    def complete_sample(
        self,
        work: AcquisitionWork,
        image: QImage,
        *,
        target: ScrollTarget,
        now: float,
        at_end: bool = False,
    ) -> bool:
        """Admit only settled target pixels; stale callbacks have no side effects."""
        if work is not self._pending or work.kind is not WorkKind.SAMPLE:
            return False
        self.tick(now=now)
        if self._state is not AcquisitionState.SETTLING:
            return False
        self._pending = None
        if target != work.target:
            self._interrupt(StopReason.TARGET_CHANGED)
            return True
        self._sequence += 1
        frame = ScrollFrame(
            max(1, self._session.token),
            self._sequence,
            target.identity,
            target.geometry_epoch,
            now,
            image,
            at_end,
        )
        status = self._settler.submit(self._settling_request, frame)
        if status is SettleStatus.STABLE:
            self._commit_stable(now)
        elif status is SettleStatus.TARGET_CHANGED:
            self._interrupt(StopReason.TARGET_CHANGED)
        elif status is SettleStatus.EXHAUSTED:
            self._interrupt(StopReason.UNCERTAIN)
        return True

    def _commit_stable(self, now: float) -> None:
        """Keep provider end claims independent from actual overlap evidence."""
        frame = self._settler.take(self._settling_request)
        target, taken_at = self._target, self._taken_at
        if frame is None or target is None or taken_at is None:
            raise CaptureError("Stable acquisition metadata is unavailable")
        status: MatchStatus | None = None
        try:
            if self._session.state is ScrollState.IDLE:
                self._session.start(target, frame.image, now=now, taken_at=taken_at)
            else:
                status = self._session.submit(frame)
        except ScrollLimitError:
            self._interrupt(StopReason.LIMIT)
            return
        except CaptureError:
            self._interrupt(StopReason.ERROR)
            return
        if self._session.state is ScrollState.REVIEW:
            self._adopt_review()
        elif (
            self._session.state is ScrollState.PAUSED
            and status is not MatchStatus.STATIONARY
        ):
            self._state = AcquisitionState.PAUSED
        elif frame.at_end:
            # Observe again without another movement; session requires repeated
            # stationary evidence before accepting the provider's end claim.
            self._begin_settling(now)
        elif self._session.state is ScrollState.PAUSED:
            self._state = AcquisitionState.PAUSED
        else:
            self._state = AcquisitionState.READY

    def tick(self, *, now: float) -> None:
        """Enforce deadlines independently of provider callback delivery."""
        self._clock(now)
        if self._state not in {
            AcquisitionState.SETTLING,
            AcquisitionState.MOVING,
            AcquisitionState.READY,
        }:
            return
        if self._session.state in {
            ScrollState.CAPTURING,
            ScrollState.PAUSED,
        } and not self._session.check_acquisition_budget(now):
            self._adopt_review()
        elif now - self._started >= self._duration:
            self._interrupt(StopReason.LIMIT)
        elif self._state is AcquisitionState.SETTLING:
            if (
                self._settler.expire(self._settling_request, now=now)
                is SettleStatus.EXHAUSTED
            ):
                self._interrupt(StopReason.UNCERTAIN)
        elif (
            self._pending is not None
            and now - self._pending.issued_at >= self.MOVE_TIMEOUT_SECONDS
        ):
            self._interrupt(StopReason.ERROR)

    def _adopt_review(self) -> None:
        """Invalidate platform work before exposing the session's stop evidence."""
        self._pending = None
        self._settler.cancel()
        self._state = AcquisitionState.REVIEW
        result = self._session.result
        self._reason = result.stop_reason if result is not None else None

    def _interrupt(self, reason: StopReason) -> None:
        """Invalidate work and keep a prefix only when pixels were committed."""
        self._pending = None
        self._settler.cancel()
        self._reason = reason
        if self._session.state in {ScrollState.CAPTURING, ScrollState.PAUSED}:
            self._session.interrupt(reason)
            self._state = AcquisitionState.REVIEW
        else:
            self._state = AcquisitionState.FAILED

    def stop(self) -> None:
        """Keep the verified prefix on explicit Stop without executing pending work."""
        if self._state in {
            AcquisitionState.CANCELLED,
            AcquisitionState.FAILED,
            AcquisitionState.REVIEW,
        }:
            return
        self._pending = None
        self._settler.cancel()
        if self._session.state in {ScrollState.CAPTURING, ScrollState.PAUSED}:
            self._session.stop()
            self._state = AcquisitionState.REVIEW
            result = self._session.result
            self._reason = result.stop_reason if result is not None else None
        else:
            self._state = AcquisitionState.FAILED
            self._reason = StopReason.USER_STOP

    def cancel(self) -> None:
        """Discard this session and reject every outstanding platform completion."""
        self._pending = None
        self._settler.cancel()
        self._session.cancel()
        self._state = AcquisitionState.CANCELLED
