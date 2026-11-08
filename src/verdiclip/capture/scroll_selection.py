"""Point selection and ownership of one scrolling helper, separate from Repeat."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, override

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.modality import ModalityGuard
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay, SharedSelection
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.geometry import Point
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.scrolling import ScrollSelectionState
from verdiclip.platform.windows import WindowInfo

_CANCEL_ACTION = "scroll_cancel"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrollingCapture:
    """A completed tall image, never a recapturable desktop rectangle."""

    image: QImage
    title: str
    taken_at: datetime
    warning: str = ""


class SelectionState(Protocol):
    """Read-only native state; no UI Automation runs in the GUI process."""

    def windows(self) -> Sequence[WindowInfo]:
        """List the current top-level targets."""

    def tick(self) -> int:
        """Observe the latest native input tick."""

    def check(self, point: Point, window: int, tick: int) -> None:
        """Reject a changed selected root, foreground, or input tick."""


class PointOverlay(SelectionOverlay):
    """Select one physical point on completed mouse or Enter release."""

    chosen = Signal(object)

    @override
    def _finish(self, at: Point) -> None:
        self.shared.release(at)
        self.chosen.emit(at)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)

    @override
    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.sync_cursor()
                if self.shared.pointer is not None:
                    self._finish(self.shared.pointer)
        else:
            super().keyReleaseEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            strip = self.rect().adjusted(0, self.height() - 44, 0, 0)
            painter.fillRect(strip, QColor(0, 0, 0, 220))
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(
                strip,
                Qt.AlignmentFlag.AlignCenter,
                "Click the scrolling area. Keep this window still. Esc cancels.",
            )
        finally:
            painter.end()


class ScrollSelection(QObject):
    """Keep selection, cancellation and helper retirement under one owner."""

    captured = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        screens: ScreenSource,
        hotkeys: HotkeyService,
        *,
        state: SelectionState | None = None,
        helper_factory: Callable[[], ScrollHelper] = ScrollHelper,
    ) -> None:
        super().__init__()
        self._screens, self._hotkeys = screens, hotkeys
        self._state = state
        self._helper_factory = helper_factory
        self._helper: ScrollHelper | None = None
        self._overlays: list[PointOverlay] = []
        self._windows: Sequence[WindowInfo] = ()
        self._choice: tuple[Point, WindowInfo, int] | None = None
        self._result: ScrollResult | None = None
        self._active = self._cancelled = self._closing = False
        self._deactivated = False
        self._watching: QApplication | None = None
        self._start = self._timer(self._show)
        self._handoff = self._timer(self._launch)
        self._publish = self._timer(self._deliver)
        self._input_check = self._timer(self._check_selector)
        hotkeys.triggered.connect(self._on_hotkey)
        ModalityGuard.ensure()

    def _timer(self, callback: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        return timer

    @property
    def busy(self) -> bool:
        """Remain busy until the owned child and any queued result are retired."""
        return self._active

    @property
    def overlays(self) -> tuple[PointOverlay, ...]:
        """Return the currently visible selectors."""
        return tuple(self._overlays)

    def capture(self) -> None:
        """Dismiss the initiating popup, then show a fresh point chooser."""
        if self._active or self._closing:
            return
        self._active, self._cancelled = True, False
        dismissed: list[QWidget] = []
        popup = QApplication.activePopupWidget()
        while popup is not None:
            if popup in dismissed or not popup.close():
                self._fail("Close the open popup before capturing")
                return
            dismissed.append(popup)
            popup = QApplication.activePopupWidget()
        self._start.start(0)

    def _show(self) -> None:
        if not self._active or self._cancelled:
            return
        try:
            self._check_released()
            if self._state is None:
                self._state = ScrollSelectionState()
            self._windows = self._state.windows()
            frozen = self._screens.freeze()
            shared = SharedSelection()
            for screen in QGuiApplication.screens():
                overlay = PointOverlay(
                    ScreenGeometry.from_screen(screen),
                    screen,
                    frozen,
                    self._windows,
                    show_magnifier=False,
                    shared=shared,
                )
                overlay.chosen.connect(self._selected)
                overlay.cancelled.connect(self.cancel)
                self._overlays.append(overlay)
                overlay.show()
            active = next((o for o in self._overlays if o.hovered_window()), None)
            if active is None and self._overlays:
                active = self._overlays[0]
            if active is None:
                raise CaptureError("No screen is available for selection")
            active.raise_()
            active.activateWindow()
            active.setFocus()
            self._deactivated = False
            app = QApplication.instance()
            if isinstance(app, QApplication):
                self._watching = app
                app.installEventFilter(self)
        except (AppError, OSError) as error:
            self._fail(str(error))

    @staticmethod
    def _check_released() -> None:
        if (
            QApplication.activePopupWidget() is not None
            or QWidget.mouseGrabber() is not None
            or QWidget.keyboardGrabber() is not None
        ):
            raise CaptureError("Another window is holding input; try capturing again")

    def _selected(self, point: Point) -> None:
        if not self._active or self._cancelled or self._state is None:
            return
        try:
            tick = self._state.tick()  # Before closing anything or queueing work.
            window = next((w for w in self._windows if w.bounds.contains(point)), None)
            if window is None:
                raise CaptureError(
                    "Select a scrolling area inside an application window"
                )
            self._choice = point, window, tick
            self._close_selectors()
            self._handoff.start(0)
        except (AppError, OSError) as error:
            self._fail(str(error))

    def _launch(self) -> None:
        if not self._active or self._cancelled or self._choice is None:
            return
        try:
            self._check_released()
            point, window, tick = self._choice
            if self._state is None:
                raise CaptureError("The selected window is no longer available")
            self._state.check(point, window.handle, tick)
            if not self._hotkeys.register(_CANCEL_ACTION, Hotkey.parse("escape")):
                raise CaptureError(
                    "Escape is unavailable; scrolling capture did not start"
                )
            self._helper = self._helper_factory()
            self._helper.finished.connect(self._completed)
            self._helper.start(point, window=window.handle, input_tick=tick)
        except (AppError, OSError) as error:
            logger.warning("Scrolling failure stage=launch: %s", error)
            self._fail(str(error))

    def _completed(self, result: ScrollResult) -> None:
        if not self._active:
            return
        self._result = result
        self._publish.start(0)

    def _deliver(self) -> None:
        result, choice = self._result, self._choice
        image = None if result is None or self._cancelled else result.image
        message = (
            result.message if result is not None else "Scrolling capture cancelled"
        )
        if image is not None and choice is not None and self._state is not None:
            try:
                self._state.check(choice[0], choice[1].handle, choice[2])
            except (AppError, OSError) as error:
                logger.warning("Scrolling failure stage=publication: %s", error)
                image, message = None, str(error)
        title = "Scrolling capture" if choice is None else choice[1].title
        restoration = "" if result is None else result.restoration
        self._end(notify=False)
        if image is not None and not self._closing and not self._cancelled:
            self.captured.emit(
                ScrollingCapture(image, title, datetime.now().astimezone(), message)
            )
        elif not self._closing:
            self.failed.emit(f"{message or 'Scrolling capture stopped'}. {restoration}")
        self.finished.emit()

    def cancel(self) -> None:
        """Latch cancellation before a queued result can publish any pixels."""
        self._cancelled = True
        if not self._active:
            return
        self._start.stop()
        self._handoff.stop()
        self._close_selectors()
        if self._helper is not None:
            self._helper.cancel()
        else:
            self._end()

    def shutdown(self) -> None:
        """Retire the child before app teardown; never publish during shutdown."""
        self._closing = self._cancelled = True
        self._start.stop()
        self._handoff.stop()
        self._publish.stop()
        self._close_selectors()
        try:
            if self._helper is not None:
                self._helper.shutdown()
        except AppError:
            # Exit was refused. Keep the child owned and cancellation latched,
            # but let its eventual terminal result retire the live session.
            self._closing = False
            raise
        self._end()

    def _fail(self, message: str) -> None:
        self._close_selectors()
        self._cancelled = True
        try:
            if self._helper is not None:
                self._helper.shutdown()
        except AppError as error:
            # Failed startup may still own a live process. Its terminal signal,
            # not this error, is the boundary for releasing Escape and ownership.
            self.failed.emit(f"{message}. {error}")
            return
        self._end()
        self.failed.emit(message)

    def _end(self, *, notify: bool = True) -> None:
        was_active = self._active
        self._start.stop()
        self._handoff.stop()
        self._publish.stop()
        self._hotkeys.unregister(_CANCEL_ACTION)
        helper, self._helper = self._helper, None
        self._active = False
        self._choice = self._result = None
        if helper is not None:
            helper.deleteLater()
        if notify and was_active:
            self.finished.emit()

    def _on_hotkey(self, action: str) -> None:
        if action == _CANCEL_ACTION:
            self.cancel()

    def _close_selectors(self) -> None:
        self._input_check.stop()
        if self._watching is not None:
            self._watching.removeEventFilter(self)
            self._watching = None
        for overlay in self._overlays:
            overlay.dismiss()
        self._overlays.clear()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        del watched
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self._deactivated = True
        if self._overlays and event.type() in {
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowActivate,
            QEvent.Type.GrabMouse,
            QEvent.Type.GrabKeyboard,
            QEvent.Type.Show,
        }:
            self._input_check.start(0)
        return False

    def _check_selector(self) -> None:
        if not self._overlays:
            return
        active = QApplication.activeWindow()
        owners = (QWidget.mouseGrabber(), QWidget.keyboardGrabber())
        own_grabs = all(
            owner is None
            or any(
                owner is overlay or overlay.isAncestorOf(owner)
                for overlay in self._overlays
            )
            for owner in owners
        )
        if (
            self._deactivated
            or active not in self._overlays
            or not own_grabs
            or QApplication.activePopupWidget() is not None
        ):
            self.cancel()
