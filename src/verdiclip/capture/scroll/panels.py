"""Nonmodal target confirmation and bounded capture review widgets."""

from __future__ import annotations

import math
from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from verdiclip.capture.interaction import CapturePanel
from verdiclip.capture.reviewed import ReviewedCapture
from verdiclip.capture.scroll.models import ScrollTarget, StopReason
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.capture.scroll.targets import TargetCandidate
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect

STOP_LABELS = {
    StopReason.USER_STOP: "Stopped by you; this is a partial capture",
    StopReason.NO_PROGRESS: "No further movement detected; completeness is unknown",
    StopReason.UNCERTAIN: (
        "Content could not be joined reliably; the earlier rows are kept"
    ),
    StopReason.REPORTED_END: "Reached the reported end of the selected area",
    StopReason.TARGET_CHANGED: (
        "The selected content changed; the earlier rows are kept"
    ),
    StopReason.LIMIT: "Capture reached its resource limit; the earlier rows are kept",
    StopReason.ERROR: "Capture encountered a problem; the earlier rows are kept",
}


class ScrollTargetPanel(CapturePanel):
    """A proposal cannot move a target; only explicit Start emits confirmation."""

    confirmed = Signal(object)  # ScrollTarget
    cancelled = Signal()

    def __init__(
        self,
        title: str,
        candidates: tuple[TargetCandidate, ...],
        parent: QWidget | None = None,
        *,
        external_input: bool = False,
        suggested_viewports: tuple[Rect, ...] = (),
    ) -> None:
        """Offer nearest-first semantic targets and an explicitly editable ROI."""
        super().__init__(parent, external_input=external_input)
        self.setWindowTitle("Scrolling capture — choose content")
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.close)
        self._title = title
        self._candidates = candidates
        self._suggested = suggested_viewports
        self._confirmed = False
        self._choice = QComboBox()
        self._choice.setAccessibleName("Scroll container")
        for index, candidate in enumerate(candidates):
            self._choice.addItem(
                f"Area {index + 1} — {int(candidate.bounds.width)} x "
                f"{int(candidate.bounds.height)}"
            )
        self._fields: dict[str, QSpinBox] = {}
        form = QFormLayout()
        form.addRow("Scroll &container", self._choice)
        for key, label in (
            ("x", "Left"),
            ("y", "Top"),
            ("width", "Width"),
            ("height", "Height"),
        ):
            field = QSpinBox()
            field.setRange(-100_000 if key in {"x", "y"} else 1, 100_000)
            field.setAccessibleName(f"Content {label.lower()} in physical pixels")
            self._fields[key] = field
            form.addRow(label, field)
        self._status = QLabel(
            "Choose only the moving content. Exclude scrollbars and fixed headers. "
            "Start will scroll this selected area."
        )
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Target confirmation status")
        self._start = QPushButton("&Start scrolling capture")
        self._start.setAccessibleName("Start scrolling capture")
        cancel = QPushButton("&Cancel")
        cancel.clicked.connect(self.close)
        self._start.clicked.connect(self._confirm)
        self._choice.currentIndexChanged.connect(self._select)
        actions = QHBoxLayout()
        actions.addWidget(cancel)
        actions.addWidget(self._start)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addLayout(form)
        layout.addLayout(actions)
        self.set_capture_controls(
            (self._choice, *self._fields.values(), cancel, self._start)
        )
        self._start.setEnabled(bool(candidates))
        if not candidates:
            self._status.setText(
                "No supported scroll container was found. Select a content area "
                "in explicit scrolling mode, or use ordinary screenshot capture."
            )
        self._select(0)

    def _select(self, index: int) -> None:
        """Changing a proposed container does not confirm or touch its application."""
        if not 0 <= index < len(self._candidates):
            return
        bounds = (
            self._suggested[index]
            if len(self._suggested) == len(self._candidates)
            else self._candidates[index].bounds
        )
        values = (bounds.x, bounds.y, bounds.width, bounds.height)
        fields = tuple(self._fields.values())
        if any(
            not math.isfinite(value) or not field.minimum() <= value <= field.maximum()
            for field, value in zip(fields, values, strict=True)
        ):
            self._start.setEnabled(False)
            self._status.setText(
                "The proposed content geometry exceeds the supported range."
            )
            return
        self._start.setEnabled(not self._confirmed)
        for key, value in (
            ("x", bounds.x),
            ("y", bounds.y),
            ("width", bounds.width),
            ("height", bounds.height),
        ):
            self._fields[key].setValue(round(value))

    def _confirm(self) -> None:
        """Consume explicit consent once, refusing ROIs outside the container."""
        if self._confirmed or not self._candidates:
            return
        candidate = self._candidates[self._choice.currentIndex()]
        area = Rect(
            *(self._fields[key].value() for key in ("x", "y", "width", "height"))
        )
        if area.intersected(candidate.bounds) != area:
            self._status.setText(
                "Keep the content rectangle inside the selected scroll container."
            )
            return
        self._confirmed = True
        self._start.setEnabled(False)
        self.confirmed.emit(ScrollTarget(candidate.identity, self._title, area))

    def confirmation_failed(self, message: str) -> None:
        """Keep target selection editable when Start is safely refused."""
        self._confirmed = False
        self._status.setText(message)
        self._start.setEnabled(bool(self._candidates))

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Cancel this panel only; no foreign or pre-existing window is touched."""
        if not self._confirmed and not self._dismissing:
            self.cancelled.emit()
        super().closeEvent(event)


class ScrollReviewPanel(CapturePanel):
    """Inspect bounded pages and explicitly deliver whole or selected pixels."""

    reviewed_capture = Signal(object)  # canonical ReviewedCapture
    capture_accept_requested = Signal(object)  # await input cleanup before publication
    cancelled = Signal()

    PAGE_ROWS = 768
    PREVIEW_WIDTH = 720
    PREVIEW_HEIGHT = 480

    def __init__(
        self,
        session: ScrollSession,
        parent: QWidget | None = None,
        *,
        external_input: bool = False,
    ) -> None:
        """Retain the session until its owner acknowledges delivery or cancellation."""
        if session.state is not ScrollState.REVIEW or session.result is None:
            raise CaptureError("Only a stopped capture can be reviewed")
        super().__init__(parent, external_input=external_input)
        self.setWindowTitle("Review scrolling capture")
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.close)
        self._session = session
        self._delivered = False
        self._capture: ReviewedCapture | None = None
        self._awaiting_delivery = False
        self._acknowledged = False
        self._capture_published = self._input_complete = self._cancel_notified = False
        result = session.result
        self._status = QLabel(
            f"{result.width} x {result.height} pixels. "
            f"{STOP_LABELS[result.stop_reason]}. "
            "Only successfully joined content is included."
        )
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Capture result and stop reason")
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setAccessibleName("Selected capture rows preview")
        self._preview.setMinimumSize(320, 200)
        self._top = QSpinBox()
        self._top.setRange(0, result.height - 1)
        self._top.setAccessibleName("First selected row, zero based")
        self._height = QSpinBox()
        self._height.setRange(1, result.height)
        self._height.setValue(max(1, session.page_rows(self.PAGE_ROWS)))
        self._height.setAccessibleName("Number of selected rows")
        form = QFormLayout()
        form.addRow("First &row", self._top)
        form.addRow("Row &count", self._height)
        self._top.valueChanged.connect(self._range_changed)
        self._height.valueChanged.connect(self._render)
        self._whole = QPushButton("Use &whole capture")
        self._range = QPushButton("Use &selected rows")
        self._whole.clicked.connect(self._accept_whole)
        self._range.clicked.connect(self._accept_range)
        self._close_button = QPushButton("&Discard capture")
        self._close_button.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.addWidget(self._close_button)
        actions.addWidget(self._range)
        actions.addWidget(self._whole)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._preview, 1)
        layout.addLayout(form)
        layout.addLayout(actions)
        self.set_capture_controls(
            (self._top, self._height, self._close_button, self._range, self._whole)
        )
        self._render()

    def _range_changed(self) -> None:
        """Keep every selected range within the committed prefix."""
        result = self._session.result
        if result is not None:
            self._height.setMaximum(result.height - self._top.value())
        self._render()

    def _render(self) -> None:
        """Decode one bounded preview page, regardless of selected output size."""
        if self._delivered:
            return
        result = self._session.result
        if result is None:
            return
        rows = self._session.page_rows(self.PAGE_ROWS)
        if rows < 1:
            self._status.setText(
                "This image is too wide for the available review memory."
            )
            self._preview.clear()
            return
        try:
            image = self._session.page(
                self._top.value(), min(self._height.value(), rows)
            )
        except CaptureError as error:
            self._status.setText(str(error))
            self._preview.clear()
            return
        thumbnail = image.scaled(
            self.PREVIEW_WIDTH,
            self.PREVIEW_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(QPixmap.fromImage(thumbnail))

    def _accept_whole(self) -> None:
        """Full materialization may refuse safely, leaving bounded review usable."""
        self._deliver(selected=False)

    def _accept_range(self) -> None:
        """Explicitly selected rows are a partial artifact with a fresh identity."""
        self._deliver(selected=True)

    def _deliver(self, *, selected: bool) -> None:
        """Emit once through the canonical bridge; no output policy runs here."""
        if self._delivered:
            if not self._awaiting_delivery and not self._acknowledged:
                self._awaiting_delivery = True
                self._whole.setEnabled(False)
                self._status.setText("Retrying capture delivery.")
                self._publish_or_wait()
            return
        try:
            capture = (
                self._session.accept(top=self._top.value(), height=self._height.value())
                if selected
                else self._session.accept()
            )
        except CaptureError as error:
            self._status.setText(
                f"{error}. Choose fewer rows to open a bounded partial capture."
            )
            return
        self._delivered = True
        self._capture = capture
        self._awaiting_delivery = True
        for control in (self._whole, self._range, self._top, self._height):
            control.setEnabled(False)
        self._status.setText("Capture accepted. Waiting for delivery.")
        self._publish_or_wait()

    def _publish_or_wait(self) -> None:
        if self.external_input and not self._input_complete:
            self.capture_accept_requested.emit(self._capture)
        else:
            self._capture_published = True
            self.reviewed_capture.emit(self._capture)

    def release_capture_delivery(self, capture_id: str) -> bool:
        """Publish accepted pixels once after confirmed capture input cleanup."""
        if (
            self._capture is None
            or self._capture.capture_id != capture_id
            or self._input_complete
        ):
            return False
        self._input_complete = True
        self._publish_or_wait()
        return True

    @property
    def delivery_retry_needed(self) -> bool:
        """Distinguish a retained failed attempt from pending or acknowledged output."""
        return self._capture_published and not (
            self._awaiting_delivery or self._acknowledged
        )

    def resume_capture_input(self) -> None:
        """Require a new cleanup receipt for a fresh explicit delivery attempt."""
        self._input_complete = False

    def capture_input_paused(self, message: str) -> None:
        """Retain review pixels when a finite input lease ends before acceptance."""
        self._status.setText(message)
        if self._delivered and not self._capture_published:
            self._awaiting_delivery = False
            self._whole.setEnabled(True)

    def discard_capture(self) -> None:
        """Notify the review owner once of explicit discard before publication."""
        if not self._capture_published and not self._cancel_notified:
            self._cancel_notified = True
            self.cancelled.emit()

    def acknowledge_delivery(self, capture_id: str) -> bool:
        """Acknowledge actual output success, including successful tray-only output.

        The controller calls this only after its existing output policy succeeds,
        then releases the session. Returning an editor window is not required.
        Unknown IDs cannot release another capture's resources.
        """
        if self._capture is None or self._capture.capture_id != capture_id:
            return False
        self._acknowledged = True
        self._close_button.setText("&Close review")
        self._awaiting_delivery = False
        self._whole.setEnabled(False)
        self._status.setText("Capture delivered.")
        return True

    def delivery_failed(self, capture_id: str, message: str) -> bool:
        """Retain accepted pixels and retry the same identity after output failure."""
        if (
            self._capture is None
            or self._capture.capture_id != capture_id
            or self._acknowledged
        ):
            return False
        self._awaiting_delivery = False
        self._whole.setText("&Retry delivery")
        self._whole.setEnabled(True)
        self._status.setText(
            f"Capture delivery failed: {message}. Retry keeps the same image."
        )
        if self._capture_published:
            self._close_button.setText("&Close review (capture retained)")
        return True

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Ask the owner to discard only an unaccepted capture's resources."""
        if (
            not self._delivered or (self.external_input and not self._capture_published)
        ) and not self._dismissing:
            self.discard_capture()
        super().closeEvent(event)
