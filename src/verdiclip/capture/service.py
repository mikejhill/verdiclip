"""Run captures: freeze the screen, let the user choose, and emit the result."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage

from verdiclip.capture.grabber import FrozenScreen, ScreenSource
from verdiclip.capture.models import Capture, CaptureMode, ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay, WindowTarget
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

    @property
    def is_selecting(self) -> bool:
        """True while the selection overlay is showing."""
        return bool(self._overlays)

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
        if self.is_selecting:
            self._activate_under_cursor()
            return
        try:
            frozen = self._screens.freeze()
            windows = list(self._windows.visible_windows(exclude=frozenset()))
        except AppError as err:
            self._fail(err)
            return
        self._frozen = frozen
        for screen in QGuiApplication.screens():
            overlay = SelectionOverlay(
                ScreenGeometry.from_screen(screen),
                screen,
                frozen,
                windows,
                show_magnifier=self._show_magnifier,
            )
            overlay.region_chosen.connect(self._on_region)
            overlay.window_chosen.connect(self._on_window)
            overlay.cancelled.connect(self._on_cancel)
            self._overlays.append(overlay)
            overlay.show()
        self._activate_under_cursor()

    def capture_fullscreen(self) -> None:
        """Capture every monitor immediately."""
        try:
            frozen = self._screens.freeze()
        except AppError as err:
            self._fail(err)
            return
        self._emit(frozen.image, CaptureMode.FULLSCREEN, frozen.bounds, "Screen")

    def capture_active_window(self) -> None:
        """Capture the foreground window immediately."""
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
        self._emit(image, CaptureMode.WINDOW, area, window.title)
        self._last_area = None  # Repeat re-captures whichever window is active

    def repeat_last(self) -> None:
        """Repeat the last capture without asking (UX-CAP-09)."""
        mode, area = self._last_mode, self._last_area
        if mode is None:
            self.capture_region()
        elif mode is CaptureMode.FULLSCREEN:
            self.capture_fullscreen()
        elif area is None:
            self.capture_active_window()
        else:
            try:
                frozen = self._screens.freeze()
                image = frozen.crop(area)
            except AppError as err:
                self._fail(err)
                return
            self._emit(image, mode, area, "Screenshot")

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

    def _emit(self, image: QImage, mode: CaptureMode, area: Rect, title: str) -> None:
        """Record for repeat and emit the capture."""
        self._last_mode = mode
        self._last_area = area
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

    def _fail(self, err: AppError) -> None:
        """Report a capture failure."""
        logger.warning("Capture failed: %s", err)
        self._close_overlays()
        self.failed.emit(str(err))

    def _close_overlays(self) -> None:
        """Hide and delete every overlay."""
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            overlay.close()
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
