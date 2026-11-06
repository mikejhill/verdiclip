"""Widget-free scrolling lifecycle with stale-work rejection and explicit review."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from PySide6.QtGui import QImage

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture
from verdiclip.capture.scroll.matching import MatchStatus, OverlapMatcher
from verdiclip.capture.scroll.models import (
    ScrollFrame,
    ScrollLimitError,
    ScrollLimits,
    ScrollResult,
    ScrollTarget,
    StopReason,
)
from verdiclip.capture.scroll.storage import (
    AppendRequest,
    StagedAppend,
    StripStore,
    StripWriter,
)
from verdiclip.exceptions import CaptureError
from verdiclip.startup_lifetime import StartupLifetime


class ScrollState(StrEnum):
    """Acquisition and review are distinct from delivery and cancellation."""

    IDLE = "idle"
    CAPTURING = "capturing"
    PAUSED = "paused"
    REVIEW = "review"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class PendingPixels:
    """Owner-thread metadata applied only after the matching storage admission."""

    request: AppendRequest
    image: QImage
    target: ScrollTarget | None = None
    now: float = 0.0
    taken_at: datetime | None = None


class ScrollSession:
    """Own committed pixels and one generation of ordered acquisition results."""

    END_OBSERVATIONS = 2

    def __init__(
        self,
        parent: Path,
        *,
        limits: ScrollLimits | None = None,
        writer: StripWriter | None = None,
    ) -> None:
        """Create dependencies without touching a target application or desktop."""
        self._limits = limits if limits is not None else ScrollLimits()
        self._store = StripStore(parent, self._limits, writer=writer)
        self._matcher = OverlapMatcher()
        self._state = ScrollState.IDLE
        self._token = 0
        self._target: ScrollTarget | None = None
        self._previous = QImage()
        self._sequence = 0
        self._frames = 0
        self._started = 0.0
        self._observed = 0.0
        self._end_count = 0
        self._taken_at: datetime | None = None
        self._result: ScrollResult | None = None
        self._pause_reason: StopReason | None = None
        self._accepted: ReviewedCapture | None = None
        self._accepted_range: tuple[int, int] | None = None
        self._pending_pixels: PendingPixels | None = None

    @property
    def storage_request(self) -> AppendRequest | None:
        """Return the single immutable transaction awaiting worker completion."""
        pending = self._pending_pixels
        return pending.request if pending is not None else None

    @property
    def limits(self) -> ScrollLimits:
        """Expose immutable budgets so acquisition can refuse work before copying."""
        return self._limits

    @property
    def token(self) -> int:
        """Identify the only generation allowed to supply more observations."""
        return self._token

    @property
    def state(self) -> ScrollState:
        """Return the explicit session phase for UI and driver coordination."""
        return self._state

    @property
    def result(self) -> ScrollResult | None:
        """Return committed extent metadata; storage remains session-owned."""
        return self._result

    def start(
        self,
        target: ScrollTarget,
        image: QImage,
        *,
        now: float,
        taken_at: datetime,
        defer_storage: bool = False,
    ) -> None:
        """Commit a stable first viewport before permitting any scrolling."""
        if self._state is not ScrollState.IDLE:
            raise CaptureError("This scrolling session has already started")
        if not math.isfinite(now) or taken_at.utcoffset() is None:
            raise CaptureError("Capture needs finite monotonic time and a timezone")
        self._validate_frame(image, target)
        request = self._store.prepare(image)
        self._pending_pixels = PendingPixels(
            request, QImage(image), target, now, taken_at
        )
        if not defer_storage:
            self._commit_synchronously(request)

    def complete_storage(self, receipt: StagedAppend) -> None:
        """Admit pixels and metadata together on the session's owning thread."""
        pending = self._pending_pixels
        if pending is None or receipt.token is not pending.request.token:
            raise CaptureError("Capture storage receipt is stale")
        self._store.admit(receipt)
        self._pending_pixels = None
        self._previous = pending.image
        if pending.target is not None:
            self._target = pending.target
            self._started = self._observed = pending.now
            self._taken_at = pending.taken_at
            self._frames = 1
            self._token += 1
            self._state = ScrollState.CAPTURING
        else:
            self._frames += 1

    def abandon_storage(self) -> None:
        """Invalidate a transaction without deleting worker-owned staging files."""
        self._store.abandon()
        self._pending_pixels = None

    def _commit_synchronously(self, request: AppendRequest) -> None:
        """Keep model callers on the same staging and admission algorithm."""
        receipt = StripStore.cleanup_receipt(request)
        try:
            receipt = StripStore.stage(request)
            self.complete_storage(receipt)
        except BaseException:
            self.abandon_storage()
            with StartupLifetime() as lifetime:
                lifetime.callback(self._store.discard, receipt)
                raise

    def _validate_frame(self, image: QImage, target: ScrollTarget) -> None:
        """Refuse geometry drift and over-budget input before copying pixels."""
        if (
            image.isNull()
            or image.width() != target.viewport.width
            or image.height() != target.viewport.height
        ):
            raise CaptureError("Capture viewport dimensions changed")
        if image.width() * image.height() > self._limits.max_frame_pixels:
            raise ScrollLimitError("Capture viewport exceeds the working-memory limit")

    def submit(self, frame: ScrollFrame, *, defer_storage: bool = False) -> MatchStatus:
        """Apply one ordered observation; stale or cancelled work cannot commit."""
        if (
            self._state not in {ScrollState.CAPTURING, ScrollState.PAUSED}
            or frame.token != self._token
            or frame.sequence <= self._sequence
            or frame.observed_at <= self._observed
        ):
            return MatchStatus.STALE
        self._sequence, self._observed = frame.sequence, frame.observed_at
        target = self._target
        if target is None:
            raise CaptureError("Scrolling target is unavailable")
        if (
            frame.target_identity != target.identity
            or frame.geometry_epoch != target.geometry_epoch
        ):
            self._finish(StopReason.TARGET_CHANGED)
            return MatchStatus.GEOMETRY_CHANGED
        if frame.observed_at - self._started >= self._limits.max_seconds:
            self._finish(StopReason.LIMIT)
            return MatchStatus.STALE
        try:
            self._validate_frame(frame.image, target)
            registration = self._matcher.compare(self._previous, frame.image)
        except ScrollLimitError:
            self._finish(StopReason.LIMIT)
            return MatchStatus.STALE
        except CaptureError:
            self._finish(StopReason.TARGET_CHANGED)
            return MatchStatus.GEOMETRY_CHANGED
        return self._apply(
            frame, registration.status, registration.shift, defer_storage=defer_storage
        )

    def _apply(
        self,
        frame: ScrollFrame,
        status: MatchStatus,
        shift: int | None,
        *,
        defer_storage: bool = False,
    ) -> MatchStatus:
        """Apply registration evidence separately from observation validation."""
        if status is MatchStatus.STATIONARY:
            self._end_count = self._end_count + 1 if frame.at_end else 0
            if self._end_count >= self.END_OBSERVATIONS:
                self._finish(StopReason.REPORTED_END)
            else:
                self._state = ScrollState.PAUSED
                self._pause_reason = StopReason.NO_PROGRESS
            return status
        self._end_count = 0
        if status is not MatchStatus.VERIFIED or self._state is ScrollState.PAUSED:
            self._state = ScrollState.PAUSED
            self._pause_reason = StopReason.UNCERTAIN
            return status
        if shift is None:
            raise CaptureError("A verified overlap must include a displacement")
        self._commit(frame.image, shift, defer_storage=defer_storage)
        return status

    def _commit(
        self, image: QImage, shift: int, *, defer_storage: bool = False
    ) -> None:
        """Append only verified novel rows, preserving the prefix on failure."""
        if (
            self._frames >= self._limits.max_frames
            or self._store.width * (self._store.height + shift)
            > self._limits.max_pixels
        ):
            self._finish(StopReason.LIMIT)
            return
        try:
            request = self._store.prepare(
                image.copy(0, image.height() - shift, image.width(), shift)
            )
            self._pending_pixels = PendingPixels(request, QImage(image))
            if not defer_storage:
                self._commit_synchronously(request)
        except ScrollLimitError:
            self._finish(StopReason.LIMIT)
            return
        except CaptureError:
            self._finish(StopReason.ERROR)
            return

    def resume(self, target: ScrollTarget) -> int:
        """Revalidate target identity and invalidate in-flight work before retry."""
        if self._state is not ScrollState.PAUSED or target != self._target:
            raise CaptureError("Cannot resume without the unchanged scrolling target")
        self._token += 1
        self._sequence = 0
        self._end_count = 0
        self._pause_reason = None
        self._state = ScrollState.CAPTURING
        return self._token

    def stop(self) -> None:
        """Keep the verified prefix for explicit review after a user stop."""
        if self._state not in {ScrollState.CAPTURING, ScrollState.PAUSED}:
            raise CaptureError("Scrolling acquisition is not active")
        self._finish(self._pause_reason or StopReason.USER_STOP)

    def interrupt(self, reason: StopReason) -> None:
        """Keep verified pixels with explicit backend or safety failure evidence."""
        if reason not in {
            StopReason.ERROR,
            StopReason.LIMIT,
            StopReason.TARGET_CHANGED,
            StopReason.UNCERTAIN,
        }:
            raise ValueError("Interruption cannot assert user intent or document end")
        if self._state not in {ScrollState.CAPTURING, ScrollState.PAUSED}:
            raise CaptureError("Scrolling acquisition is not active")
        self._finish(reason)

    def check_acquisition_budget(self, now: float, *, for_step: bool = False) -> bool:
        """Stop before more target input when known time/output capacity is gone.

        Observations do not count as committed frames: a provider end claim may
        still be verified without scrolling. A step requires room for at least
        one novel row. Actual displacement and disk writes retain independent
        transactional guards because a provider cannot promise its pixel delta.
        """
        if not math.isfinite(now) or now < self._observed:
            raise ValueError("Acquisition budget needs ordered finite time")
        if self._state not in {ScrollState.CAPTURING, ScrollState.PAUSED}:
            return False
        elapsed = now - self._started >= self._limits.max_seconds
        full = (
            self._frames >= self._limits.max_frames
            or self._store.width * (self._store.height + 1) > self._limits.max_pixels
        )
        if elapsed or (for_step and full):
            self._finish(StopReason.LIMIT)
            return False
        return True

    def _finish(self, reason: StopReason) -> None:
        """End acquisition without deleting the reviewable committed prefix."""
        self.abandon_storage()
        self._result = ScrollResult(
            self._store.width, self._store.height, self._frames, reason
        )
        self._token += 1
        self._state = ScrollState.REVIEW
        self._previous = QImage()

    def page(self, top: int, height: int) -> QImage:
        """Read one bounded page without changing acceptance or completeness."""
        if self._state is not ScrollState.REVIEW:
            raise CaptureError("Capture pages are available during review")
        return self._store.materialize(top, height)

    def page_rows(self, maximum: int) -> int:
        """Expose a safe preview extent without duplicating manifest arithmetic."""
        if self._state is not ScrollState.REVIEW:
            raise CaptureError("Capture pages are available during review")
        if maximum < 1:
            raise ValueError("A page needs a positive row limit")
        return min(maximum, self._store.page_capacity)

    def accept(self, *, top: int = 0, height: int | None = None) -> ReviewedCapture:
        """Accept the whole result or an explicit bounded partial row range."""
        result, target, taken_at = self._result, self._target, self._taken_at
        rows = height if height is not None else (result.height - top if result else 0)
        if self._state is ScrollState.DELIVERED and self._accepted is not None:
            if self._accepted_range != (top, rows):
                raise CaptureError("This capture has already delivered another range")
            return self._accepted
        if (
            self._state is not ScrollState.REVIEW
            or result is None
            or target is None
            or taken_at is None
        ):
            raise CaptureError("A capture must be in review before delivery")
        image = self._store.materialize(top, height)
        selected_range = top != 0 or (height is not None and height != result.height)
        completeness = (
            CaptureCompleteness.COMPLETE
            if result.stop_reason is StopReason.REPORTED_END and not selected_range
            else CaptureCompleteness.PARTIAL
        )
        accepted = ReviewedCapture(
            image,
            target.title,
            taken_at,
            CaptureKind.SCROLLING,
            completeness,
            "selected_range" if selected_range else result.stop_reason.value,
        )
        self._state = ScrollState.DELIVERED
        self._accepted = accepted
        self._accepted_range = (top, rows)
        return accepted

    def cancel(self, *, defer_cleanup: bool = False) -> tuple[StagedAppend, ...]:
        """Invalidate all pending work and discard only this session's storage."""
        self.abandon_storage()
        self._token += 1
        self._previous = QImage()
        self._result = None
        self._accepted = None
        self._accepted_range = None
        if defer_cleanup:
            transactions = self._store.release()
            self._state = ScrollState.CANCELLED
            return transactions
        try:
            self._store.close()
        finally:
            self._state = ScrollState.CANCELLED
        return ()

    def close(self) -> None:
        """Release temporary storage; an already delivered QImage remains valid."""
        try:
            self.cancel()
        finally:
            self._state = ScrollState.CLOSED
