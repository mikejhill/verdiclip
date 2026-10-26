"""Own scrolling confirmation, acquisition and review under one preserved lease."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.interaction import CapturePanel
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.reviewed import ReviewedCapture
from verdiclip.capture.scroll.backend import ScrollBackend
from verdiclip.capture.scroll.binding import ScrollCaptureBinding
from verdiclip.capture.scroll.entry import ConfirmedScroll, ScrollEntry, ScrollOffer
from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.session import ScrollSession
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo

type BackendFactory = Callable[[UiaReference, UiaAdapter], ScrollBackend]
type ReviewFactory = Callable[[ScrollSession], ScrollReviewPanel]


@dataclass(frozen=True, slots=True)
class ScrollFactories:
    """Construct a backend and review owner that retains accepted sessions."""

    backend: BackendFactory
    review: ReviewFactory


class WorkflowState(StrEnum):
    """Keep pending review distinct from input ownership and delivery success."""

    IDLE = "idle"
    DISCOVERING = "discovering"
    CONFIRMING = "confirming"
    CAPTURING = "capturing"
    REVIEW = "review"
    PAUSED = "review_input_paused"
    DELIVERING = "waiting_for_input_cleanup"
    CLOSING = "closing"
    CLEANUP_FAILED = "cleanup_failed"


class ScrollProgressPanel(CapturePanel):
    """A small, nonactivating control surface positioned outside captured pixels."""

    stop_requested = Signal()

    def __init__(self) -> None:
        """Stop retains verified rows; Escape/close explicitly cancel this capture."""
        super().__init__(external_input=True)
        self.setWindowTitle("Scrolling capture")
        self._status = QLabel("Preparing scrolling capture.")
        self.setFixedSize(320, 96)
        self._status.setAccessibleName("Scrolling capture progress")
        stop = QPushButton("&Stop and review")
        stop.clicked.connect(self._stop)
        cancel = QPushButton("&Cancel capture")
        cancel.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        actions = QHBoxLayout()
        actions.addWidget(stop)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.set_capture_controls((stop, cancel))

    def _stop(self) -> None:
        self.stop_requested.emit()

    def set_status(self, message: str) -> None:
        """Expose backend progress without foreground or native input changes."""
        self._status.setAccessibleDescription(message)
        self._status.setText(
            self._status.fontMetrics().elidedText(
                message, Qt.TextElideMode.ElideRight, 280
            )
        )


def position_outside_capture(
    panel: CapturePanel, viewport: Rect, screens: Sequence[ScreenGeometry]
) -> bool:
    """Refuse occluding controls rather than sampling their pixels into output."""
    panel.adjustSize()
    for screen in screens:
        bounds = screen.physical
        overlap = viewport.intersected(bounds)
        regions = (
            (bounds,)
            if overlap.is_empty
            else (
                Rect(bounds.x, bounds.y, bounds.width, overlap.y - bounds.y),
                Rect(
                    bounds.x,
                    overlap.bottom,
                    bounds.width,
                    bounds.bottom - overlap.bottom,
                ),
                Rect(bounds.x, bounds.y, overlap.x - bounds.x, bounds.height),
                Rect(
                    overlap.right, bounds.y, bounds.right - overlap.right, bounds.height
                ),
            )
        )
        width, height = panel.width() * screen.scale, panel.height() * screen.scale
        for region in regions:
            if region.width >= width and region.height >= height:
                logical = screen.logical.top_left + (
                    region.top_left - bounds.top_left
                ).scaled(1 / screen.scale)
                panel.move(round(logical.x), round(logical.y))
                return True
    return False


class ScrollWorkflow(QObject):
    """Capture-owned UI; the injected review owner retains canonical delivery policy."""

    takeover = Signal()
    controls_mode = Signal(bool)
    release_requested = Signal()
    fallback = Signal(object, str)  # original frozen window bounds and title
    failed = Signal(str)
    work_changed = Signal()

    def __init__(
        self,
        adapter: UiaAdapter,
        *,
        factories: ScrollFactories,
        spool_parent: Path,
        input_active: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        """Inject all OS/output boundaries; construct no native capability here."""
        super().__init__(parent)
        self._adapter, self._backend_factory = adapter, factories.backend
        self._review_factory, self._spool_parent = factories.review, spool_parent
        self._input_active = input_active
        self._entry = ScrollEntry(adapter, self)
        self._state = WorkflowState.IDLE
        self._window: WindowInfo | None = None
        self._offer: ScrollOffer | None = None
        self._roi: Rect | None = None
        self._screens: tuple[ScreenGeometry, ...] = ()
        self._panel: CapturePanel | None = None
        self._session: ScrollSession | None = None
        self._review_owned = False
        self._binding: ScrollCaptureBinding | None = None
        self._accepted: ReviewedCapture | None = None
        self._error = ""
        self._epoch = 0
        self._entry.offered.connect(self._offered)
        self._entry.confirmed.connect(self._confirmed)
        self._entry.fallback.connect(self._fallback)
        self._entry.failed.connect(self._fail)
        self._entry.cancelled.connect(self._cleanup_complete)
        adapter.scope_closed.connect(self._scope_closed)

    @property
    def state(self) -> WorkflowState:
        """Expose progress/review/cleanup without exposing native helper internals."""
        return self._state

    @property
    def panel(self) -> CapturePanel | None:
        """Return only this workflow's currently owned presentation surface."""
        return self._panel

    @property
    def has_work(self) -> bool:
        """Unaccepted review and pending cleanup prevent idle exit or replacement."""
        return self._state is not WorkflowState.IDLE

    @property
    def paused_review(self) -> bool:
        """A fresh explicit capture request may resume this retained review."""
        return self._state is WorkflowState.PAUSED

    def begin(
        self,
        window: WindowInfo,
        point: Point,
        screens: Sequence[ScreenGeometry],
        *,
        explicit: bool,
        viewport: Rect | None = None,
    ) -> bool:
        """Inspect a frozen selection generation; do not click or scroll its app."""
        if self.has_work or self._adapter.busy or not self._input_active():
            return False
        if window.window_identity is None:
            if explicit:
                self.failed.emit(
                    "The selected window generation could not be verified."
                )
            else:
                self.fallback.emit(window.bounds, window.title)
            return False
        self._window, self._roi, self._screens = window, viewport, tuple(screens)
        self._epoch += 1
        self._review_owned = False
        epoch = self._epoch
        self._set_state(WorkflowState.DISCOVERING)
        if not self._owns_phase(WorkflowState.DISCOVERING, epoch=epoch):
            return False
        try:
            started = self._entry.inspect(
                window.handle,
                point,
                window.title,
                explicit=explicit,
                expected_window_identity=window.window_identity,
            )
            if not started and self._owns_phase(WorkflowState.DISCOVERING, epoch=epoch):
                self._fail("Scrolling discovery is unavailable after cleanup failure.")
        except ValueError as error:
            if explicit:
                self._fail(str(error))
            else:
                self._fallback()
        return self.has_work

    def _set_state(self, state: WorkflowState) -> None:
        self._state = state
        self.work_changed.emit()

    def _owns_phase(
        self,
        state: WorkflowState,
        *,
        panel: CapturePanel | None = None,
        epoch: int | None = None,
    ) -> bool:
        """Re-read ownership after callbacks, which may synchronously retire work."""
        return (
            self._state is state
            and (panel is None or panel is self._panel)
            and (epoch is None or epoch == self._epoch)
        )

    def _offered(self, offer: ScrollOffer) -> None:
        if self._state is not WorkflowState.DISCOVERING:
            return
        self._offer = offer
        candidates, viewports = offer.candidates, offer.viewports
        if self._roi is not None:
            candidates = tuple(
                choice
                for choice in candidates
                if self._roi.intersected(choice.bounds) == self._roi
            )
            viewports = tuple(self._roi for _choice in candidates)
        if not candidates:
            self._fail("Choose a content area inside one supported scroll container.")
            return
        panel = ScrollTargetPanel(
            offer.title, candidates, external_input=True, suggested_viewports=viewports
        )
        panel.confirmed.connect(self._start)
        panel.cancelled.connect(self.cancel)
        self._replace_panel(panel)
        self._set_state(WorkflowState.CONFIRMING)
        if not self._owns_phase(WorkflowState.CONFIRMING, panel=panel):
            return
        self.takeover.emit()
        if not self._owns_phase(WorkflowState.CONFIRMING, panel=panel):
            return
        self.controls_mode.emit(self.has_work)
        if self._owns_phase(WorkflowState.CONFIRMING, panel=panel):
            panel.show()

    def _start(self, target: ScrollTarget) -> None:
        offer = self._offer
        panel = self._panel
        if (
            self._state is not WorkflowState.CONFIRMING
            or offer is None
            or not isinstance(panel, ScrollTargetPanel)
        ):
            return
        progress = ScrollProgressPanel()
        if not position_outside_capture(progress, target.viewport, self._screens):
            progress.dismiss()
            panel.confirmation_failed(
                "Leave space for capture controls or choose a smaller content area."
            )
            return
        index = next(
            (
                index
                for index, choice in enumerate(offer.candidates)
                if choice.identity == target.identity
            ),
            -1,
        )
        self._replace_panel(progress)
        progress.stop_requested.connect(self.stop)
        progress.capture_closed.connect(self.cancel)
        if not self._entry.start(index, target.viewport, consent=True):
            self._fail("The selected content area is no longer available.")

    def _confirmed(self, selection: ConfirmedScroll) -> None:
        epoch = self._epoch
        try:
            session = ScrollSession(self._spool_parent)
            self._session = session
            backend = self._backend_factory(selection.reference, self._adapter)
            if (
                not self._owns_phase(WorkflowState.CONFIRMING, epoch=epoch)
                or session is not self._session
            ):
                backend.deleteLater()
                return
            binding = ScrollCaptureBinding(session, backend, self)
            backend.setParent(binding)
        except (CaptureError, OSError, ValueError) as error:
            self._fail(str(error))
            return
        self._binding = binding
        binding.status_changed.connect(self._status)
        binding.review_ready.connect(self._review)
        binding.failed.connect(self._fail)
        binding.cancelled.connect(self.cancel)
        self._set_state(WorkflowState.CAPTURING)
        if not self._owns_phase(WorkflowState.CAPTURING, epoch=epoch):
            return
        if self._panel is not None:
            self._panel.show()
        if not self._owns_phase(WorkflowState.CAPTURING, epoch=epoch):
            return
        binding.start_confirmed(selection.target)

    def _status(self, message: str) -> None:
        if isinstance(self._panel, ScrollProgressPanel):
            self._panel.set_status(message)

    def stop(self) -> None:
        """Explicit Stop keeps verified rows; Escape still cancels the capture."""
        if self._state is WorkflowState.CAPTURING and self._binding is not None:
            self._binding.stop()

    def _review(self, session: ScrollSession, message: str) -> None:
        if self._state is not WorkflowState.CAPTURING:
            return
        try:
            panel = self._review_factory(session)
            if (
                not self._owns_phase(WorkflowState.CAPTURING)
                or session is not self._session
            ):
                panel.discard_capture()
                panel.dismiss()
                panel.deleteLater()
                return
            self._review_owned = True
            self._replace_panel(panel)
            panel.use_capture_input()
        except (CaptureError, RuntimeError, ValueError) as error:
            self._fail(str(error))
            return
        panel.capture_accept_requested.connect(self._review_accepted)
        panel.capture_closed.connect(self._review_closed)
        self._set_state(WorkflowState.REVIEW)
        if not self._owns_phase(WorkflowState.REVIEW, panel=panel):
            return
        if message:
            panel.capture_input_paused(message)
        panel.show()
        if not self._input_active():
            self._pause_review(
                "Input paused. Press the capture shortcut to resume review."
            )

    def _accept(self, capture: ReviewedCapture) -> None:
        if self._state is not WorkflowState.REVIEW:
            return
        self._accepted = capture
        self._set_state(WorkflowState.DELIVERING)
        self.release_requested.emit()

    @Slot(object)
    def _review_accepted(self, capture: ReviewedCapture) -> None:
        if self.sender() is self._panel:
            self._accept(capture)

    @Slot()
    def _review_closed(self) -> None:
        if self.sender() is self._panel:
            self.cancel()

    def input_closed(self, *, cleaned: bool, message: str) -> bool:
        """Wait for cleanup before publication; retain review on a finite lease end."""
        if self._state is WorkflowState.DELIVERING:
            panel, capture = self._panel, self._accepted
            if (
                cleaned
                and not message
                and isinstance(panel, ScrollReviewPanel)
                and capture is not None
            ):
                panel.release_capture_delivery(capture.capture_id)
                self._accepted = None
                if panel.delivery_retry_needed:
                    self._pause_review(
                        "Delivery failed. Press the capture shortcut to resume retry."
                    )
                else:
                    panel.complete_capture_input()
                    self._panel = self._session = None  # review owner retains both
                    self._retire_binding()
                    self._set_state(WorkflowState.IDLE)
            else:
                self._pause_review(
                    message or "Input cleanup was not confirmed; capture is retained."
                )
            return True
        if self._state is WorkflowState.REVIEW:
            self._pause_review(
                message or "Input paused. Press the capture shortcut to resume review."
            )
            return True
        if self._state is WorkflowState.CAPTURING:
            self.stop()
            return True
        if self.has_work:
            self.cancel()
            return True
        return False

    def _pause_review(self, message: str) -> None:
        if isinstance(self._panel, ScrollReviewPanel):
            self._panel.capture_input_paused(message)
            self._set_state(WorkflowState.PAUSED)

    def resume_review(self) -> None:
        """Resume the same pixels only after a fresh explicit input lease is ready."""
        if self.paused_review and self._input_active():
            if isinstance(self._panel, ScrollReviewPanel):
                self._panel.resume_capture_input()
            self.controls_mode.emit(self.has_work)
            self._set_state(WorkflowState.REVIEW)

    def receive_input(self, event: SelectionInput) -> None:
        """Route to the currently owned surface; discovery consumes no extra actions."""
        if self._panel is not None and self._state in {
            WorkflowState.CONFIRMING,
            WorkflowState.CAPTURING,
            WorkflowState.REVIEW,
        }:
            self._panel.receive_input(event)
        elif event.kind is InputKind.CANCEL:
            self.cancel()

    def cancel(self) -> None:
        """Cancel owned resources once, keeping input reserved until helper cleanup."""
        if self._state in {WorkflowState.IDLE, WorkflowState.CLOSING}:
            return
        self._set_state(WorkflowState.CLOSING)
        epoch = self._epoch
        if isinstance(self._panel, ScrollReviewPanel):
            self._panel.discard_capture()
        self._replace_panel(None)
        if self._binding is not None:
            self._binding.cancel()
        self._entry.cancel()
        if not self._owns_phase(WorkflowState.CLOSING, epoch=epoch):
            return
        self._adapter.cancel()
        if not self._adapter.busy:
            self._cleanup_complete()

    def _scope_closed(self) -> None:
        if self._state is WorkflowState.CLOSING:
            self._cleanup_complete()

    def _cleanup_complete(self) -> None:
        if self._state is not WorkflowState.CLOSING:
            return
        if self._session is not None and not self._review_owned:
            try:
                self._session.close()
            except CaptureError as error:
                self._set_state(WorkflowState.CLEANUP_FAILED)
                self.failed.emit(str(error))
                return
        self._session = None
        self._accepted = None
        error, self._error = self._error, ""
        self._retire_binding()
        epoch = self._epoch
        self._set_state(WorkflowState.IDLE)
        if not self._owns_phase(WorkflowState.IDLE, epoch=epoch):
            return
        if error:
            self.failed.emit(error)
        else:
            self.release_requested.emit()

    def retry_cleanup(self) -> bool:
        """Retry only retained private storage cleanup, never a target action."""
        if self._state is not WorkflowState.CLEANUP_FAILED:
            return False
        self._set_state(WorkflowState.CLOSING)
        self._cleanup_complete()
        return self._state is WorkflowState.IDLE

    def _fallback(self) -> None:
        window = self._window
        epoch = self._epoch
        self._set_state(WorkflowState.IDLE)
        if (
            window is not None
            and epoch == self._epoch
            and self._state is WorkflowState.IDLE
        ):
            self.fallback.emit(window.bounds, window.title)

    def _fail(self, message: str) -> None:
        if self._state is WorkflowState.IDLE:
            self.failed.emit(message)
            return
        self._error = message
        self.cancel()

    def _replace_panel(self, panel: CapturePanel | None) -> None:
        previous, self._panel = self._panel, panel
        if previous is not None:
            previous.dismiss()
            previous.deleteLater()

    def _retire_binding(self) -> None:
        binding, self._binding = self._binding, None
        if binding is not None:
            binding.deleteLater()
