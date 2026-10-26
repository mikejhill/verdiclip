"""Shared readiness/cleanup driver for portable and native-presentation tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from verdiclip.capture.models import Capture
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class PreservationSnapshot(QObject):
    """Observe explicit pre-existing windows without changing focus or ownership."""

    def __init__(self, app: QApplication, windows: tuple[QWidget, ...]) -> None:
        super().__init__()
        self.app, self.windows = app, windows
        self.changes: list[str] = []
        self.handles = tuple(window.windowHandle() for window in windows)
        self.modalities = tuple(window.windowModality() for window in windows)
        self.popup = QApplication.activePopupWidget()
        self.mouse, self.keyboard = QWidget.mouseGrabber(), QWidget.keyboardGrabber()
        app.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Record only destructive presentation events for the declared owners."""
        if any(watched is window for window in self.windows) and event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.Close,
        }:
            self.changes.append(event.type().name)
        return False

    def errors(self) -> list[str]:
        """Compare preservation before fixture teardown, without focus assumptions."""
        errors = list(self.changes)
        for index, window in enumerate(self.windows):
            if not isValid(window):
                errors.append("A pre-existing window was destroyed")
                continue
            if not window.isVisible():
                errors.append("A pre-existing window became invisible")
            if window.windowHandle() is not self.handles[index]:
                errors.append("A pre-existing native handle changed")
            if window.windowModality() != self.modalities[index]:
                errors.append("Pre-existing modality changed")
        if QApplication.activePopupWidget() is not self.popup:
            errors.append("The active popup changed")
        if QWidget.mouseGrabber() is not self.mouse:
            errors.append("The mouse grab changed")
        if QWidget.keyboardGrabber() is not self.keyboard:
            errors.append("The keyboard grab changed")
        return errors

    def close(self) -> None:
        """Remove only this observer; never close or reject observed windows."""
        self.app.removeEventFilter(self)


@dataclass(frozen=True, slots=True)
class HarnessBudgets:
    """Bound both initial completion and cleanup failure independently."""

    deadline_ms: int = 5000
    cleanup_ms: int = 2000

    def __post_init__(self) -> None:
        """No fixture setting can disable either failure deadline."""
        if not all(
            0 < value <= 10_000 for value in (self.deadline_ms, self.cleanup_ms)
        ):
            raise ValueError("Harness deadlines must be bounded positive milliseconds")


class CaptureHarness(QObject):
    """Drive only after broker readiness and wait for terminal service cleanup."""

    completed = Signal()

    def __init__(
        self,
        service: CaptureService,
        snapshot: PreservationSnapshot,
        send: Callable[[str, Rect], None],
        *,
        outcome: str,
        budgets: HarnessBudgets | None = None,
    ) -> None:
        super().__init__()
        budgets = budgets if budgets is not None else HarnessBudgets()
        if outcome not in {"escape", "drag"}:
            raise ValueError("Choose a bounded drag or Escape harness")
        self.service, self.snapshot, self.send = service, snapshot, send
        self.outcome = outcome
        self.errors: list[str] = []
        self.captures: list[Capture] = []
        self.done = self.issued = self.expired = False
        self.cleanup_confirmed = self._closed = False
        self.expected: Rect | None = None
        self._terminal_seen = False
        self._poll = QTimer(self)
        self._poll.setInterval(5)
        self._poll.timeout.connect(self.advance)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(budgets.deadline_ms)
        self._deadline.timeout.connect(self.expire)
        self._cleanup_deadline = QTimer(self)
        self._cleanup_deadline.setSingleShot(True)
        self._cleanup_deadline.setInterval(budgets.cleanup_ms)
        self._cleanup_deadline.timeout.connect(self.cleanup_expired)
        service.captured.connect(self._captured)
        service.cancelled.connect(self._terminal)
        service.failed.connect(self._failed)

    def start(self) -> None:
        """Arm observers before the caller dispatches a menu or hotkey request."""
        self._poll.start()
        self._deadline.start()

    def advance(self) -> None:
        """Readiness never depends on an overlay becoming the focused window."""
        if self.done or self._closed:
            return
        if self._terminal_seen and not self.service.is_selecting:
            self._finish()
            return
        if self.expired:
            return
        overlays = self.service.overlays
        if self.issued or not self.service.input_active or not overlays:
            return
        if not all(overlay.isVisible() for overlay in overlays):
            return
        for overlay in overlays:
            flags = overlay.windowFlags()
            if not (
                flags & Qt.WindowType.WindowDoesNotAcceptFocus
                and flags & Qt.WindowType.WindowTransparentForInput
                and overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            ):
                self.errors.append("Selection is not nonactivating/input-transparent")
        self.errors.extend(self.snapshot.errors())
        if self.errors:
            self.expire()
            return
        bounds = overlays[0].screen_geometry.physical
        width, height = min(70, bounds.width / 3), min(50, bounds.height / 3)
        self.expected = Rect(
            bounds.x + width, bounds.y + height, width, height
        ).rounded()
        self.issued = True
        try:
            self.send(self.outcome, self.expected)
        except (CaptureError, OSError, RuntimeError, ValueError) as error:
            self.errors.append(str(error))
            self.expire()

    def _captured(self, capture: Capture) -> None:
        """Keep the real terminal result for exact rectangle/cardinality checks."""
        self.captures.append(capture)
        self._terminal()

    def _failed(self, message: str) -> None:
        """Preserve service failure evidence instead of converting it to a skip."""
        self.errors.append(message)
        self._terminal()

    def _terminal(self) -> None:
        """A signal alone is insufficient until service input ownership is idle."""
        if not self._closed:
            self._terminal_seen = True

    def expire(self) -> None:
        """Cancel only test-owned capture and await its actual cleanup receipt."""
        if self.done or self.expired or self._closed:
            return
        self.expired = True
        self.errors.append("Capture readiness/completion deadline expired")
        self.service.cancel()
        if not self.service.is_selecting:
            self._terminal()
        else:
            self._cleanup_deadline.start()

    def cleanup_expired(self) -> None:
        """Unwind a failed test's nested loop without claiming input was released."""
        if self.done or self._closed:
            return
        if self.service.is_selecting:
            self.errors.append("Capture cleanup was not confirmed before its deadline")
        self._finish()

    def _finish(self) -> None:
        """Assert preservation before any caller-owned host teardown can run."""
        if self.done or self._closed:
            return
        self.cleanup_confirmed = not self.service.is_selecting
        self.errors.extend(self.snapshot.errors())
        if not self.expired:
            if self.outcome == "escape" and self.captures:
                self.errors.append("Escape delivered an image")
            elif self.outcome == "drag" and (
                len(self.captures) != 1 or self.captures[0].area != self.expected
            ):
                self.errors.append(
                    "Drag did not deliver exactly the selected rectangle"
                )
        self.done = True
        self._poll.stop()
        self._deadline.stop()
        self._cleanup_deadline.stop()
        self.snapshot.close()
        self.completed.emit()

    def close(self) -> None:
        """Stop this observer; resource cleanup is still owned by the service."""
        self._closed = True
        self._poll.stop()
        self._deadline.stop()
        self._cleanup_deadline.stop()
        self.service.cancel()
        self.snapshot.close()
