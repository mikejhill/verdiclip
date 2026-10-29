"""Controller-owned review lifetime, independent of native acquisition adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal

from verdiclip.capture.scroll.panels import ScrollReviewPanel
from verdiclip.exceptions import CaptureError

if TYPE_CHECKING:
    from verdiclip.capture.reviewed import ReviewedCapture
    from verdiclip.capture.scroll.session import ScrollSession


@dataclass(slots=True)
class ReviewLease:
    """Keep spool ownership after accepted review UI has been closed."""

    session: ScrollSession
    panel: ScrollReviewPanel | None
    capture_id: str | None = None
    complete: bool = False
    cleanup_pending: bool = False


class ScrollReviews(QObject):
    """Retain reviews until explicit discard or acknowledged delivery."""

    reviewed_capture = Signal(object)
    cleanup_failed = Signal(str)
    work_changed = Signal()

    def __init__(self, parent: QObject) -> None:
        """Use application ownership rather than a review window's lifetime."""
        super().__init__(parent)
        self._leases: list[ReviewLease] = []

    @property
    def has_work(self) -> bool:
        """Prevent idle exit/update while any original image is unresolved."""
        return any(
            not lease.complete or lease.cleanup_pending for lease in self._leases
        )

    @property
    def has_unaccepted(self) -> bool:
        """Unreviewed rows require explicit acceptance or discard in their UI."""
        return any(
            not lease.complete and lease.capture_id is None for lease in self._leases
        )

    def open(self, session: ScrollSession) -> ScrollReviewPanel:
        """Construct review only; callers decide when it is safe to present it."""
        if any(lease.session is session for lease in self._leases):
            raise ValueError("This scrolling session already has a review owner")
        panel = ScrollReviewPanel(session)
        panel.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lease = ReviewLease(session, panel)
        self._leases.append(lease)
        panel.reviewed_capture.connect(lambda result: self._accept(lease, result))
        panel.cancelled.connect(lambda: self._cancel(lease))
        panel.destroyed.connect(lambda: self._destroyed(lease))
        return panel

    def _accept(self, lease: ReviewLease, result: ReviewedCapture) -> None:
        if lease.complete:
            return
        if lease.capture_id is not None and lease.capture_id != result.capture_id:
            raise ValueError("Review retry changed capture identity")
        lease.capture_id = result.capture_id
        self.reviewed_capture.emit(result)

    def acknowledge_delivery(self, capture_id: str) -> None:
        """Release spool only after actual policy success, even without a tab."""
        for lease in tuple(self._leases):
            if lease.capture_id != capture_id or lease.complete:
                continue
            if lease.panel is not None and not lease.panel.acknowledge_delivery(
                capture_id
            ):
                continue
            self._release(lease)
            # Leave an existing review visible with its delivered status.
            if lease.panel is None and not lease.cleanup_pending:
                self._leases.remove(lease)
        self.work_changed.emit()

    def delivery_failed(self, capture_id: str, message: str) -> None:
        """Retain failed originals and expose same-object retry in live review."""
        for lease in self._leases:
            if lease.capture_id == capture_id and lease.panel is not None:
                lease.panel.delivery_failed(capture_id, message)

    def _cancel(self, lease: ReviewLease) -> None:
        # Only an unaccepted panel emits cancelled; accepted pending pixels remain.
        if lease.capture_id is None and not lease.complete:
            self._release(lease)
            self.work_changed.emit()

    def _destroyed(self, lease: ReviewLease) -> None:
        lease.panel = None
        if lease.complete and not lease.cleanup_pending:
            self._leases.remove(lease)

    def _release(self, lease: ReviewLease) -> bool:
        lease.complete = True
        try:
            lease.session.close()
        except CaptureError as error:
            lease.cleanup_pending = True
            self.cleanup_failed.emit(str(error))
            return False
        lease.cleanup_pending = False
        return True

    def close_resolved(self) -> bool:
        """Close review windows only after explicit application close succeeds."""
        if self.has_unaccepted:
            raise RuntimeError("An unreviewed capture still needs a decision")
        for lease in tuple(self._leases):
            if (not lease.complete or lease.cleanup_pending) and not self._release(
                lease
            ):
                return False
            if lease.panel is not None:
                lease.panel.close()
            elif lease in self._leases:
                self._leases.remove(lease)
        return True
