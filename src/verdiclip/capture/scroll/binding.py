"""Bind target confirmation, asynchronous backend work and explicit review."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from verdiclip.capture.scroll.acquisition import (
    AcquisitionState,
    AcquisitionWork,
    ScrollAcquisition,
)
from verdiclip.capture.scroll.backend import (
    BackendFinish,
    BackendFrame,
    ScrollBackend,
    TargetVerification,
)
from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.capture.scroll.spooling import StorageJob
from verdiclip.capture.scroll.storage import StagedAppend
from verdiclip.exceptions import CaptureError


class BindingState(StrEnum):
    """Lifecycle visible to the capture owner without native/provider details."""

    IDLE = "idle"
    PREPARING = "preparing"
    CAPTURING = "capturing"
    VERIFYING = "verifying_before_step"
    FINISHING = "finishing"
    REVIEW = "review"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class BindingOptions:
    """Observation cadence and explicit best-effort restoration policy."""

    interval_ms: int = 100
    restore_position: bool = True

    def __post_init__(self) -> None:
        """No busy-loop timer can be configured."""
        if self.interval_ms < 1:
            raise ValueError("Capture observation interval must be positive")


class ScrollCaptureBinding(QObject):
    """Own one asynchronous acquisition and expose review through a stable boundary.

    Controller connects target confirmation to start_confirmed, displays status,
    then builds ScrollReviewPanel on review_ready. It retains the session until
    matching capture-ID delivery acknowledgement; this binding runs no output
    policy and closes no pre-existing windows, dialogs, menus or popups.
    """

    status_changed = Signal(str)
    review_ready = Signal(object, str)  # ScrollSession, restoration/cleanup note
    failed = Signal(str)
    cancelled = Signal()
    storage_changed = Signal()
    storage_failed = Signal(str)
    input_release_requested = Signal()
    PREPARE_MS = 12_000
    VERIFY_MS = 4000
    FINISH_MS = 8000

    def __init__(
        self,
        session: ScrollSession,
        backend: ScrollBackend,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        options: BindingOptions | None = None,
    ) -> None:
        """Inject backend/clock for deterministic offscreen full-flow verification."""
        super().__init__(parent)
        settings = options if options is not None else BindingOptions()
        self._session, self._backend, self._clock = session, backend, clock
        self._driver = ScrollAcquisition(session, defer_storage=True)
        self._restore = settings.restore_position
        self._state = BindingState.IDLE
        self._target: ScrollTarget | None = None
        self._requested_at = datetime.now(UTC)
        self._serial = 0
        self._probe = 0
        self._pending: AcquisitionWork | None = None
        self._storage_job: StorageJob | None = None
        self._storage_cleanup: tuple[StagedAppend, ...] = ()
        self._storage_error = ""
        self._finish_receipt: BackendFinish | None = None
        self._cancel_announced = False
        self._poll = QTimer(self)
        self._poll.setInterval(settings.interval_ms)
        self._poll.timeout.connect(self._advance)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._deadline_expired)
        backend.verified.connect(self._verified)
        backend.sampled.connect(self._sampled)
        backend.moved.connect(self._moved)
        backend.failed.connect(self._backend_failed)
        backend.finished.connect(self._finished)

    @property
    def state(self) -> BindingState:
        """Return stable progress/error/review states for the shell."""
        return self._state

    @property
    def storage_pending(self) -> bool:
        """Retain replacement/cleanup ownership until every file operation retires."""
        return bool(self._storage_job or self._storage_cleanup or self._storage_error)

    @property
    def storage_error(self) -> str:
        """Expose retained cleanup failure without pretending its files are gone."""
        return self._storage_error

    @property
    def native_finished(self) -> bool:
        """Input release still requires the workflow's independent helper receipt."""
        return self._finish_receipt is not None or self._state in {
            BindingState.CANCELLED,
            BindingState.FAILED,
        }

    def start_confirmed(self, target: ScrollTarget) -> bool:
        """Explicit Start reserves once; unsupported native builds fail visibly."""
        if self._state is not BindingState.IDLE:
            return False
        reason = self._backend.unavailable_reason
        if reason is not None:
            self._fail_without_pixels(reason)
            return False
        self._target = target
        self._requested_at = datetime.now(UTC)
        self._state = BindingState.PREPARING
        self.status_changed.emit("Preparing the selected scroll area…")
        self._verify(self.PREPARE_MS)
        return self._state in {
            BindingState.PREPARING,
            BindingState.CAPTURING,
            BindingState.VERIFYING,
        }

    def _verify(self, milliseconds: int) -> None:
        """Keep preparation/provider startup outside the pixel-settling deadline."""
        if self._state not in {BindingState.PREPARING, BindingState.VERIFYING}:
            return
        target = self._target
        if target is None:
            self._fail_without_pixels("The selected scroll area is unavailable.")
            return
        self._serial += 1
        self._probe = self._serial
        self._deadline.start(milliseconds)
        try:
            self._backend.verify(target, self._probe)
        except CaptureError as error:
            self._backend_failed(self._probe, str(error))

    def _verified(self, verification: TargetVerification) -> None:
        """Only a current, unchanged and safe target may begin or continue movement."""
        if verification.request_id != self._probe or self._state not in {
            BindingState.PREPARING,
            BindingState.VERIFYING,
        }:
            return
        self._deadline.stop()
        if verification.target != self._target or not verification.safe_to_scroll:
            self._driver.backend_failed(target_changed=True)
            self._backend_failed(
                self._probe, "The selected area changed or cannot be safely scrolled."
            )
            return
        now = self._clock()
        if self._state is BindingState.PREPARING:
            self._driver.start_confirmed(
                verification.target, now=now, taken_at=self._requested_at
            )
            self._state = BindingState.CAPTURING
            self._poll.start()
            self.status_changed.emit("Capturing scrolling content…")
        else:
            self._state = BindingState.CAPTURING
            try:
                work = self._driver.step(
                    verification.target, safe_to_scroll=True, now=now
                )
                self._dispatch(work)
            except CaptureError as error:
                self._driver.backend_failed()
                self._settle_state(str(error))

    def _advance(self) -> None:
        """Schedule bounded observations or a fresh safety check, never queue ahead."""
        self._advance_storage()
        if self._state not in {BindingState.CAPTURING, BindingState.VERIFYING}:
            return
        self._driver.tick(now=self._clock())
        if (
            self._settle_state()
            or self._pending is not None
            or self._state is BindingState.VERIFYING
        ):
            return
        try:
            if self._driver.state is AcquisitionState.SETTLING:
                self._dispatch(self._driver.sample(now=self._clock()))
            elif self._driver.state is AcquisitionState.READY:
                self._state = BindingState.VERIFYING
                self.status_changed.emit("Checking the selected area before scrolling…")
                self._verify(self.VERIFY_MS)
        except CaptureError as error:
            self._driver.backend_failed()
            self._settle_state(str(error))

    def _dispatch(self, work: AcquisitionWork) -> None:
        """Reserve callback identity before invoking even a synchronous test backend."""
        self._pending = work
        try:
            self._backend.execute(work)
        except CaptureError as error:
            self._backend_failed(work, str(error))

    def _sampled(self, work: AcquisitionWork, frame: BackendFrame) -> None:
        """Actual target metadata and pixels pass through all session guards."""
        if work is not self._pending or self._state is not BindingState.CAPTURING:
            return
        self._pending = None
        self._driver.complete_sample(
            work,
            frame.image,
            target=frame.target,
            now=self._clock(),
            at_end=frame.at_end,
        )
        request = self._driver.storage_request
        if request is not None:
            if self.storage_pending:
                raise CaptureError("Capture storage already has work in flight")
            job = StorageJob(request)
            self._storage_job = job
            try:
                job.start()
            except (OSError, RuntimeError) as error:
                self._storage_job = None
                self._driver.backend_failed()
                self._settle_state(f"Could not start capture storage: {error}")
                return
            except BaseException:
                self._driver.backend_failed()
                self._discard_staging()
                raise
            self._poll.start()
        self._settle_state()

    def _advance_storage(self) -> None:
        """Consume plain worker receipts; admission and lifecycle stay on the GUI."""
        job = self._storage_job
        if job is None or not job.done:
            return
        receipt = job.take()
        self._storage_job = None
        staged = receipt.staged
        if staged is not None:
            admitted = (
                self._state is BindingState.CAPTURING
                and self._driver.complete_storage(staged, now=self._clock())
            )
            if not admitted:
                self._storage_cleanup += (staged,)
        self._storage_cleanup += receipt.remaining
        if receipt.error:
            self._storage_error = receipt.error
            self.storage_failed.emit(receipt.error)
        self._start_cleanup()
        if not self.storage_pending:
            if self._state is BindingState.CANCELLED:
                self._announce_cancel()
                self.storage_changed.emit()
            elif (
                self._state is BindingState.FINISHING
                and self._finish_receipt is not None
            ):
                self._publish_review(self._finish_receipt)
            elif self._state is BindingState.FAILED:
                self._poll.stop()
                self.storage_changed.emit()

    def _start_cleanup(self) -> None:
        if (
            self._storage_job is None
            and self._storage_cleanup
            and not self._storage_error
        ):
            cleanup = self._storage_cleanup
            job = StorageJob(cleanup=cleanup)
            self._storage_job = job
            self._storage_cleanup = ()
            try:
                job.start()
            except (OSError, RuntimeError) as error:
                self._storage_job = None
                self._storage_cleanup = cleanup
                self._storage_error = f"Could not start capture cleanup: {error}"
                self.storage_failed.emit(self._storage_error)
                return
            except BaseException:
                job.discard()
                raise
            self._poll.start()

    def _discard_staging(self) -> None:
        """Let a blocked job clean itself even if its GUI is later destroyed."""
        job = self._storage_job
        if job is not None and job.discard(self._storage_cleanup):
            self._storage_cleanup = ()
        self._start_cleanup()

    def retry_storage_cleanup(self) -> bool:
        """Retry retained private files only; never restart capture or target input."""
        if not self._storage_error or not self._storage_cleanup:
            return False
        self._storage_error = ""
        self._start_cleanup()
        return True

    def _moved(self, work: AcquisitionWork) -> None:
        """A provider reply permits settling without implying pixel displacement."""
        if work is not self._pending or self._state is not BindingState.CAPTURING:
            return
        self._pending = None
        self._driver.complete_move(work, now=self._clock(), succeeded=True)
        self._settle_state()

    def _backend_failed(self, request: object, message: str) -> None:
        """Reject stale failures and preserve a verified prefix when available."""
        if self._state not in {
            BindingState.PREPARING,
            BindingState.CAPTURING,
            BindingState.VERIFYING,
        }:
            return
        active_probe = (
            self._state in {BindingState.PREPARING, BindingState.VERIFYING}
            and request == self._probe
        )
        if request is not self._pending and not active_probe:
            return
        self._pending = None
        self._driver.backend_failed()
        if not self._settle_state(message):
            self._fail_without_pixels(message)

    def _settle_state(self, message: str = "") -> bool:
        """Transition once into bounded backend cleanup or a truthful empty failure."""
        if self._driver.state is AcquisitionState.PAUSED:
            self._driver.stop()
        if self._driver.state is AcquisitionState.REVIEW:
            self._begin_finish(message)
            return True
        if self._driver.state is AcquisitionState.FAILED:
            self._fail_without_pixels(
                message or "The content did not become stable. No image was captured."
            )
            return True
        return False

    def _begin_finish(self, message: str = "") -> None:
        """Restoration is separate from image completeness and has its own deadline."""
        if self._state in {
            BindingState.FINISHING,
            BindingState.REVIEW,
            BindingState.CANCELLED,
        }:
            return
        self._pending = None
        self._discard_staging()
        if not self.storage_pending:
            self._poll.stop()
        self._serial += 1
        self._probe = self._serial
        self._state = BindingState.FINISHING
        self.status_changed.emit(
            message or "Finishing capture and checking the original position…"
        )
        if self.state is not BindingState.FINISHING:
            return
        self._deadline.start(self.FINISH_MS)
        try:
            self._backend.finish(self._probe, restore=self._restore)
        except CaptureError as error:
            self._backend.cancel()
            self._finished(
                BackendFinish(self._probe, restored=False, message=str(error))
            )

    def _finished(self, result: BackendFinish) -> None:
        """Only the current cleanup receipt exposes the session for explicit review."""
        if (
            result.request_id != self._probe
            or self._state is not BindingState.FINISHING
        ):
            return
        self._deadline.stop()
        self._finish_receipt = result
        if self.storage_pending:
            self.input_release_requested.emit()
            return
        self._publish_review(result)

    def _publish_review(self, result: BackendFinish) -> None:
        """No review owner can close pixels while staging or rollback still runs."""
        self._poll.stop()
        self._state = BindingState.REVIEW
        self.status_changed.emit("Capture stopped. Review the captured rows.")
        if (
            self.state is BindingState.REVIEW
            and self._session.state is ScrollState.REVIEW
        ):
            self.review_ready.emit(self._session, result.message)

    def _deadline_expired(self) -> None:
        """A silent prepare/cleanup stage cannot leave a stuck progress state."""
        if self._state is BindingState.FINISHING:
            self._backend.cancel()
            self._finished(
                BackendFinish(
                    self._probe,
                    restored=False,
                    message="The original position could not be restored.",
                )
            )
        elif self._state in {BindingState.PREPARING, BindingState.VERIFYING}:
            self._backend_failed(
                self._probe, "The selected area did not respond in time."
            )

    def _fail_without_pixels(self, message: str) -> None:
        """Report failure with no false image, and stop only owned backend work."""
        self._discard_staging()
        if not self.storage_pending:
            self._poll.stop()
        self._deadline.stop()
        self._state = BindingState.FAILED
        self._serial += 1
        self._probe = self._serial
        self._pending = None
        self._backend.cancel()
        self.status_changed.emit(message)
        if self.state is BindingState.FAILED:
            self.failed.emit(message)

    def stop(self) -> None:
        """Keep a verified prefix on Stop; no prefix means cancellation."""
        if self._state not in {
            BindingState.PREPARING,
            BindingState.CAPTURING,
            BindingState.VERIFYING,
        }:
            return
        if self._session.state is ScrollState.IDLE:
            self.cancel()
            return
        self._driver.stop()
        self._begin_finish()

    def cancel(self) -> None:
        """Invalidate all receipt identities before cancelling/discarding owned work."""
        if self._state is BindingState.CANCELLED or (
            self._state is BindingState.REVIEW
            and self._session.state is not ScrollState.REVIEW
        ):
            return
        self._serial += 1
        self._probe = self._serial
        self._pending = None
        self._deadline.stop()
        self._state = BindingState.CANCELLED
        self._storage_cleanup += self._driver.cancel(defer_cleanup=True)
        self._discard_staging()
        self._backend.cancel()
        self.status_changed.emit("Scrolling capture cancelled.")
        if not self.storage_pending:
            self._announce_cancel()

    def _announce_cancel(self) -> None:
        self._poll.stop()
        if not self._cancel_announced:
            self._cancel_announced = True
            self.cancelled.emit()

    def shutdown_storage(self, *, timeout_ms: int) -> None:
        """Bounded retirement; late worker cleanup needs no Qt callbacks."""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        self.cancel()
        while self._storage_job is not None:
            job = self._storage_job
            if not job.wait(deadline - time.monotonic()):
                raise CaptureError("Scrolling storage shutdown cleanup is unconfirmed")
            self._advance_storage()
        if self.storage_pending:
            raise CaptureError(
                self._storage_error or "Scrolling storage cleanup is unconfirmed"
            )
