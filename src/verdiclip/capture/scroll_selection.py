"""Choose a scrolling panel with a click, then run one helper for it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import override

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QApplication

from verdiclip.capture.gestures import ClickAction, ClickOutput
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay, SharedSelection
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import CANCELLED, ScrollResult
from verdiclip.capture.service import WindowSource
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.geometry import Point
from verdiclip.platform.hotkeys import Hotkey, HotkeyService

_CANCEL_ACTION = "scroll_cancel"
# Let the chooser disappear from the screen before the helper looks at it.
_HANDOFF_MS = 150
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrollingCapture:
    """A completed tall image, never a recapturable desktop rectangle."""

    image: QImage
    title: str
    taken_at: datetime
    warning: str = ""
    output: ClickOutput = ClickOutput.USUAL


class PointOverlay(SelectionOverlay):
    """Select one physical point with a click or Enter.

    A click captures from the top; Ctrl+click from the current position.
    """

    chosen = Signal(object, bool)  # Point (physical), from the top

    @override
    def _finish(
        self, at: Point, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
    ) -> None:
        # Every click here is a scrolling capture; Ctrl alone means "from here".
        self.shared.release(at)
        here = modifiers & Qt.KeyboardModifier.ControlModifier and not (
            modifiers & Qt.KeyboardModifier.AltModifier
        )
        self.chosen.emit(at, not here)

    @override
    def action(self) -> ClickAction:
        # The chooser has its own fixed meaning for modifiers; show no hint.
        return ClickAction()

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
                "Click: capture the area from the top. Ctrl+click: from here "
                "down. Esc cancels; during the capture it stops and keeps it.",
            )
        finally:
            painter.end()


class ScrollSelection(QObject):
    """Own the chooser, the Esc shortcut and the helper until one result."""

    state_changed = Signal(bool, bool)  # active, cancelling
    captured = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        screens: ScreenSource,
        windows: WindowSource,
        hotkeys: HotkeyService,
        *,
        helper_factory: Callable[[], ScrollHelper] = ScrollHelper,
    ) -> None:
        super().__init__()
        self._screens, self._windows, self._hotkeys = screens, windows, hotkeys
        self._helper_factory = helper_factory
        self._helper: ScrollHelper | None = None
        self._overlays: list[PointOverlay] = []
        self._point: Point | None = None
        self._title = ""
        self._from_top = True
        self._output = ClickOutput.USUAL
        self._active = self._cancelled = self._stopping = self._closing = False
        self._launch = QTimer(self)
        self._launch.setSingleShot(True)
        self._launch.timeout.connect(self._start_helper)
        hotkeys.triggered.connect(self._on_hotkey)

    @property
    def busy(self) -> bool:
        """True from the chooser opening until the result is delivered."""
        return self._active

    @property
    def overlays(self) -> tuple[PointOverlay, ...]:
        """Return the currently visible choosers."""
        return tuple(self._overlays)

    def capture(self) -> None:
        """Show the chooser over a frozen screen."""
        if self._active or self._closing:
            return
        self._active, self._cancelled = True, False
        self._output = ClickOutput.USUAL
        self.state_changed.emit(True, False)  # noqa: FBT003 - Qt signal
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
        try:
            windows = self._windows.visible_windows()
            frozen = self._screens.freeze()
            shared = SharedSelection()
            for screen in QGuiApplication.screens():
                overlay = PointOverlay(
                    ScreenGeometry.from_screen(screen),
                    screen,
                    frozen,
                    windows,
                    show_magnifier=False,
                    shared=shared,
                )
                overlay.chosen.connect(self._selected)
                overlay.cancelled.connect(self.cancel)
                self._overlays.append(overlay)
                overlay.show()
            if not self._overlays:
                raise CaptureError("No screen is available for selection")
            active = next(
                (o for o in self._overlays if o.hovered_window()), self._overlays[0]
            )
            active.raise_()
            active.activateWindow()
            active.setFocus()
        except (AppError, OSError) as error:
            self._fail(str(error))

    def _selected(self, point: Point, from_top: bool) -> None:  # noqa: FBT001 - Qt slot
        if not self._active or self._cancelled or self._point is not None:
            return
        window = next(
            (o.hovered_window() for o in self._overlays if o.hovered_window()), None
        )
        self._begin(point, window.title if window is not None else "", from_top)

    def start_at(
        self,
        point: Point,
        title: str,
        *,
        from_top: bool = True,
        output: ClickOutput = ClickOutput.USUAL,
    ) -> None:
        """Capture the panel at ``point`` without a chooser (from universal capture).

        ``output`` is where the image goes, as the click's modifiers chose.
        """
        if self._active or self._closing:
            return
        self._active, self._cancelled = True, False
        self._output = output
        self.state_changed.emit(True, False)  # noqa: FBT003 - Qt signal
        self._begin(point, title, from_top)

    def _begin(self, point: Point, title: str, from_top: bool) -> None:  # noqa: FBT001
        self._title = title or "Scrolling capture"
        self._from_top = from_top
        self._point = point
        self._close_overlays()
        if _CANCEL_ACTION not in self._hotkeys.registered and not (
            self._hotkeys.register(_CANCEL_ACTION, Hotkey.parse("escape"))
        ):
            self._fail(
                "Esc is in use by another program, so a scrolling capture could "
                "not be cancelled. Close that program and try again."
            )
            return
        self._launch.start(_HANDOFF_MS)

    def _start_helper(self) -> None:
        if not self._active or self._cancelled or self._point is None:
            return
        self._helper = self._helper_factory()
        self._helper.finished.connect(self._completed)
        try:
            self._helper.start(self._point, from_top=self._from_top)
        except (AppError, OSError) as error:
            self._fail(f"Could not start scrolling capture: {error}")

    def _completed(self, result: ScrollResult) -> None:
        cancelled, closing, title = self._cancelled, self._closing, self._title
        stopping, output = self._stopping, self._output
        self._end()
        if closing or cancelled or (stopping and result.message == CANCELLED):
            pass  # Stopped before anything was captured: like a cancel
        elif result.image is not None:
            self.captured.emit(
                ScrollingCapture(
                    result.image,
                    title,
                    datetime.now().astimezone(),
                    result.warning,
                    output,
                )
            )
        else:
            self.failed.emit(result.message or "Scrolling capture stopped")
        self.finished.emit()

    def cancel(self) -> None:
        """Stop choosing or capturing; nothing is delivered."""
        if not self._active:
            return
        self._cancelled = True
        self.state_changed.emit(True, True)  # noqa: FBT003 - Qt signal
        self._launch.stop()
        self._close_overlays()
        if self._helper is not None:
            self._helper.cancel()
        else:
            self._end()
            self.finished.emit()

    def stop(self) -> None:
        """Esc: end a running capture early and keep it; before that, cancel."""
        if not self._active:
            return
        if self._helper is None or self._cancelled:
            self.cancel()
            return
        if not self._stopping:
            self._stopping = True
            self.state_changed.emit(True, True)  # noqa: FBT003 - Qt signal
            self._helper.stop()

    def shutdown(self) -> None:
        """Kill any running helper before the application closes."""
        self._closing = self._cancelled = True
        self._launch.stop()
        self._close_overlays()
        if self._helper is not None:
            try:
                self._helper.shutdown()  # Emits finished -> _completed -> _end
            except AppError:
                # The app stays open; later captures must not be refused.
                self._closing = False
                raise
        elif self._active:
            self._end()

    def _fail(self, message: str) -> None:
        self._end()
        self.failed.emit(message)
        self.finished.emit()

    def _end(self) -> None:
        self._launch.stop()
        self._close_overlays()
        self._hotkeys.unregister(_CANCEL_ACTION)
        helper, self._helper = self._helper, None
        if helper is not None:
            helper.deleteLater()
        self._point = None
        self._stopping = False
        if self._active:
            self._active = False
            self.state_changed.emit(False, self._cancelled)  # noqa: FBT003 - Qt signal

    def _on_hotkey(self, action: str) -> None:
        if action == _CANCEL_ACTION:
            self.stop()

    def _close_overlays(self) -> None:
        for overlay in self._overlays:
            overlay.dismiss()
        self._overlays.clear()
