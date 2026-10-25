"""Run captures: freeze the screen, let the user choose, and emit the result."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, override

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture.grabber import FrozenScreen, ScreenSource
from verdiclip.capture.modality import ModalityGuard
from verdiclip.capture.models import Capture, CaptureMode, ScreenGeometry
from verdiclip.capture.overlay import (
    SelectionOverlay,
    SharedSelection,
    WindowTarget,
)
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.geometry import Rect

logger = logging.getLogger(__name__)


class WindowSource(Protocol):
    """Lists capturable top-level windows."""

    def visible_windows(
        self, *, exclude: frozenset[int] = ...
    ) -> Sequence[WindowTarget]:
        """Return visible windows, topmost first."""

    def foreground(self) -> WindowTarget | None:
        """Return the active window, if any."""


class CaptureService(QObject):
    """Produce ``Capture`` objects for every capture mode, including repeat."""

    captured = Signal(object)  # Capture
    cancelled = Signal()
    failed = Signal(str)

    def __init__(
        self,
        screens: ScreenSource,
        windows: WindowSource,
        *,
        show_magnifier: bool = True,
    ) -> None:
        super().__init__()
        self._screens = screens
        self._windows = windows
        self._show_magnifier = show_magnifier
        self._overlays: list[SelectionOverlay] = []
        self._frozen: FrozenScreen | None = None
        self._last_mode: CaptureMode | None = None
        self._last_area: Rect | None = None
        self._last_title = ""
        self._pending: Callable[[], None] | None = None
        self._starting = False
        self._cancelled_start = False
        self._handoff = QTimer(self)
        self._handoff.setSingleShot(True)
        self._handoff.timeout.connect(self._start_pending)
        self._input_check = QTimer(self)
        self._input_check.setSingleShot(True)
        self._input_check.timeout.connect(self._check_input_owner)
        self._watching: QApplication | None = None
        self._deactivated = False
        # No dialog the app opens may block the overlays' input
        ModalityGuard.ensure()

    @property
    def is_selecting(self) -> bool:
        """True while capture is handing off input or showing selection overlays."""
        return self._starting or self._pending is not None or bool(self._overlays)

    @property
    def overlays(self) -> tuple[SelectionOverlay, ...]:
        """Return the open overlays (one per monitor)."""
        return tuple(self._overlays)

    def set_show_magnifier(self, *, show: bool) -> None:
        """Enable or disable the magnifier for future selections."""
        self._show_magnifier = show

    # Capture modes

    def capture_region(self) -> None:
        """Freeze every monitor and let the user drag a region or click a window."""
        self._request(self._select_region)

    def _select_region(self) -> None:
        """Freeze and show selection only after the previous input owner exits."""
        try:
            frozen = self._screens.freeze()
            windows = list(self._windows.visible_windows(exclude=frozenset()))
        except AppError as err:
            self._fail(err)
            return
        if self._cancelled_start:
            return
        self._frozen = frozen
        self._deactivated = False
        app = QApplication.instance()
        if isinstance(app, QApplication):
            self._watching = app
            app.installEventFilter(self)
        screens = QGuiApplication.screens()
        # One selection across every monitor, so a drag can span them
        shared = SharedSelection([ScreenGeometry.from_screen(s) for s in screens])
        for screen in screens:
            overlay = SelectionOverlay(
                ScreenGeometry.from_screen(screen),
                screen,
                frozen,
                windows,
                show_magnifier=self._show_magnifier,
                shared=shared,
            )
            overlay.region_chosen.connect(self._on_region)
            overlay.window_chosen.connect(self._on_window)
            overlay.cancelled.connect(self._on_cancel)
            self._overlays.append(overlay)
            overlay.show()
        self._activate_under_cursor()

    def capture_fullscreen(self) -> None:
        """Capture the monitor under the cursor after handing off input."""
        self._request(self._capture_fullscreen)

    def _capture_fullscreen(self) -> None:
        """Capture the cursor monitor once no transient UI owns input."""
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        area = ScreenGeometry.from_screen(screen).physical
        try:
            frozen = self._screens.freeze()
            image = frozen.crop(area)
        except AppError as err:
            self._fail(err)
            return
        area = area.rounded().intersected(frozen.bounds)
        if self._emit(image, CaptureMode.FULLSCREEN, area, "Screen"):
            self._last_area = None  # Repeat resolves the cursor's monitor again

    def capture_active_window(self) -> None:
        """Capture the foreground window after handing off input."""
        self._request(self._capture_active_window)

    def _capture_active_window(self) -> None:
        """Capture the foreground window once the request owns input."""
        try:
            frozen = self._screens.freeze()
            window = self._windows.foreground()
            if window is None:
                msg = "There is no active window to capture"
                raise CaptureError(msg)
            image = frozen.crop(window.bounds)
        except AppError as err:
            self._fail(err)
            return
        area = window.bounds.rounded().intersected(frozen.bounds)
        if self._emit(image, CaptureMode.WINDOW, area, window.title):
            self._last_area = None  # Repeat resolves the foreground window again

    def repeat_last(self) -> None:
        """Repeat the last capture without asking (UX-CAP-09)."""
        self._request(self._repeat_last)

    def _repeat_last(self) -> None:
        """Resolve repeat after completing the same input handoff as new captures."""
        mode, area = self._last_mode, self._last_area
        if mode is None:
            self._select_region()
        elif mode is CaptureMode.FULLSCREEN:
            self._capture_fullscreen()
        elif area is None:
            self._capture_active_window()
        else:
            try:
                frozen = self._screens.freeze()
                image = frozen.crop(area)
            except AppError as err:
                self._fail(err)
                return
            self._emit(image, mode, area, self._last_title)

    def cancel(self) -> None:
        """Close any open overlay without capturing."""
        if self.is_selecting:
            self._on_cancel()

    # Overlay results

    def _on_region(self, area: Rect) -> None:
        """Capture the chosen region from the frozen screen."""
        self._finish_selection(area, CaptureMode.REGION, "Screenshot")

    def _on_window(self, area: Rect, title: str) -> None:
        """Capture the clicked window from the frozen screen."""
        self._finish_selection(area, CaptureMode.WINDOW, title)

    def _on_cancel(self) -> None:
        """Close overlays and report cancellation."""
        self._close_overlays()
        self.cancelled.emit()

    def _finish_selection(self, area: Rect, mode: CaptureMode, title: str) -> None:
        """Crop the frozen screen and emit the capture."""
        frozen = self._frozen
        self._close_overlays()
        if frozen is None:
            return
        try:
            image = frozen.crop(area)
        except AppError as err:
            self._fail(err)
            return
        self._emit(image, mode, area, title)

    # Internals

    def _request(self, start: Callable[[], None]) -> None:
        """Acquire capture input before freezing or showing any overlay.

        Qt routes native input to active popups regardless of which window
        is raised. A popup must close and its dispatch unwind before capture
        begins; changing activation alone cannot transfer that ownership.
        """
        if self.is_selecting:
            self._activate_under_cursor()
            return
        self._pending = start
        popup = QApplication.activePopupWidget()
        mouse, keyboard = QWidget.mouseGrabber(), QWidget.keyboardGrabber()
        # Close this application's popup stack through Qt, which releases its
        # implicit/native grabs and unwinds QMenu.exec without affecting dialogs.
        dismissed: list[QWidget] = []
        while popup is not None:
            if popup in dismissed or not popup.close():
                self._fail(CaptureError("Close the open popup before capturing"))
                return
            dismissed.append(popup)
            popup = QApplication.activePopupWidget()
        if mouse is not None:
            mouse.releaseMouse()
        if keyboard is not None:
            keyboard.releaseKeyboard()
        # A zero-delay queued turn is an event-loop boundary, not a timing guess.
        # Qt close handlers may have cancelled the request reentrantly.
        if self.is_selecting:
            self._handoff.start(0)

    def _start_pending(self) -> None:
        """Start one request only when Qt's previous input owner has released it."""
        start, self._pending = self._pending, None
        if start is None:
            return
        if (
            QApplication.activePopupWidget() is not None
            or QWidget.mouseGrabber() is not None
            or QWidget.keyboardGrabber() is not None
        ):
            self._fail(
                CaptureError("Another window is holding input; try capturing again")
            )
            return
        self._starting = True
        self._cancelled_start = False
        try:
            start()
        finally:
            self._starting = False
            if self._cancelled_start:
                self._close_overlays()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Recheck the selection's input lease after Qt settles an owner change."""
        del watched
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self._deactivated = True
        elif event.type() == QEvent.Type.ApplicationActivate:
            self._deactivated = False
        if self._overlays and event.type() in {
            QEvent.Type.WindowActivate,
            QEvent.Type.ApplicationActivate,
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.GrabMouse,
            QEvent.Type.GrabKeyboard,
            QEvent.Type.Show,
        }:
            self._input_check.start(0)
        return False

    def _check_input_owner(self) -> None:
        """Cancel instead of stranding a selection that no longer owns input."""
        if not self._overlays:
            return
        focus, active = QGuiApplication.focusWindow(), QApplication.activeWindow()
        owns_focus = any(
            focus is overlay.windowHandle() or active is overlay
            for overlay in self._overlays
        )
        lost_focus = self._deactivated or (
            not owns_focus and (focus is not None or active is not None)
        )
        owners = (QWidget.mouseGrabber(), QWidget.keyboardGrabber())
        owns_grabs = all(
            owner is None
            or any(
                owner is overlay or overlay.isAncestorOf(owner)
                for overlay in self._overlays
            )
            for owner in owners
        )
        if lost_focus or not owns_grabs or QApplication.activePopupWidget() is not None:
            self._on_cancel()

    def _emit(self, image: QImage, mode: CaptureMode, area: Rect, title: str) -> bool:
        """Commit repeat history and emit only a still-valid capture result."""
        if self._cancelled_start:
            return False
        self._last_mode = mode
        self._last_area = area
        self._last_title = title
        logger.info("Captured %s %s", mode.value, area)
        self.captured.emit(
            Capture(
                image=image,
                mode=mode,
                area=area,
                title=title,
                taken_at=datetime.now().astimezone(),
            )
        )
        return True

    def _fail(self, err: AppError) -> None:
        """Report a capture failure."""
        logger.warning("Capture failed: %s", err)
        self._close_overlays()
        self.failed.emit(str(err))

    def _close_overlays(self) -> None:
        """End the input lease, including pending work and every sibling overlay."""
        if self._starting:
            self._cancelled_start = True
        self._handoff.stop()
        self._input_check.stop()
        self._pending = None
        if self._watching is not None:
            self._watching.removeEventFilter(self)
            self._watching = None
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            overlay.dismiss()
        self._frozen = None

    def _activate_under_cursor(self) -> None:
        """Give keyboard focus to the overlay under the mouse."""
        pos = QCursor.pos()
        for overlay in self._overlays:
            if overlay.geometry().contains(pos):
                overlay.raise_()
                overlay.activateWindow()
                overlay.setFocus()
                return
        if self._overlays:
            self._overlays[0].activateWindow()
