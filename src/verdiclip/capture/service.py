"""Run captures: freeze the screen, let the user choose, and emit the result."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, override

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture.gestures import ClickGestures, ClickOutput
from verdiclip.capture.grabber import FrozenScreen, ScreenSource
from verdiclip.capture.modality import ModalityGuard
from verdiclip.capture.models import Capture, CaptureMode, ScreenGeometry
from verdiclip.capture.overlay import (
    SelectionOverlay,
    SharedSelection,
    WindowTarget,
)
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)


class WindowSource(Protocol):
    """Lists capturable top-level windows."""

    def visible_windows(
        self, *, exclude: frozenset[int] = ...
    ) -> Sequence[WindowTarget]:
        """Return visible windows, topmost first."""

    def foreground(self) -> WindowTarget | None:
        """Return the active window, if any."""


@dataclass(frozen=True, slots=True)
class _FrozenTarget:
    """Copy discovery values so dismissal cannot change a selection target."""

    title: str
    bounds: Rect


@dataclass(frozen=True, slots=True)
class _PreparedCapture:
    """One desktop frame and its matching targets, before input handoff."""

    frozen: FrozenScreen
    mode: CaptureMode
    area: Rect | None = None
    title: str = ""
    windows: tuple[_FrozenTarget, ...] = ()
    repeat_area: bool = True


class CaptureService(QObject):
    """Produce ``Capture`` objects for every capture mode, including repeat."""

    captured = Signal(object)  # Capture
    cancelled = Signal()
    failed = Signal(str)
    # Point (physical), window title, whether to start from the top, ClickOutput
    scroll_requested = Signal(object, str, bool, object)

    def __init__(
        self,
        screens: ScreenSource,
        windows: WindowSource,
        *,
        show_magnifier: bool = True,
        gestures: ClickGestures | None = None,
    ) -> None:
        super().__init__()
        self._gestures = gestures if gestures is not None else ClickGestures()
        self._screens = screens
        self._windows = windows
        self._show_magnifier = show_magnifier
        self._overlays: list[SelectionOverlay] = []
        self._frozen: FrozenScreen | None = None
        self._last_mode: CaptureMode | None = None
        self._last_area: Rect | None = None
        self._last_title = ""
        self._pending: Callable[[], _PreparedCapture] | None = None
        self._prepared: _PreparedCapture | None = None
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

    def set_gestures(self, gestures: ClickGestures) -> None:
        """Use ``gestures`` for the modifiers of future universal captures."""
        self._gestures = gestures

    # Capture modes

    def capture_region(self, *, freeze_first: bool = False) -> None:
        """Start universal capture: drag a region or click a window.

        Hotkeys freeze before dismissing visible popups.
        """
        self._request(self._prepare_region, freeze_first=freeze_first)

    def _prepare_region(self) -> _PreparedCapture:
        """Snapshot pixels and copy window targets without changing input owners."""
        frozen = self._screens.freeze()
        windows = tuple(
            _FrozenTarget(window.title, window.bounds)
            for window in self._windows.visible_windows(exclude=frozenset())
        )
        return _PreparedCapture(frozen, CaptureMode.REGION, windows=windows)

    def _select_region(self, prepared: _PreparedCapture) -> None:
        """Show the one frozen frame only after the previous input owner exits."""
        self._frozen = prepared.frozen
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
                prepared.frozen,
                prepared.windows,
                show_magnifier=self._show_magnifier,
                shared=shared,
                gestures=self._gestures,
            )
            overlay.region_chosen.connect(self._on_region)
            overlay.window_chosen.connect(self._on_window)
            overlay.scroll_chosen.connect(self._on_scroll)
            overlay.cancelled.connect(self._on_cancel)
            self._overlays.append(overlay)
            overlay.show()
        self._activate_under_cursor()

    def capture_fullscreen(self, *, freeze_first: bool = False) -> None:
        """Capture the cursor monitor, optionally before popup dismissal."""
        self._request(self._prepare_fullscreen, freeze_first=freeze_first)

    def _prepare_fullscreen(self) -> _PreparedCapture:
        """Snapshot the cursor's monitor and its pixels before any focus changes."""
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        area = ScreenGeometry.from_screen(screen).physical
        return _PreparedCapture(
            self._screens.freeze(),
            CaptureMode.FULLSCREEN,
            area,
            "Screen",
            repeat_area=False,
        )

    def capture_active_window(self, *, freeze_first: bool = False) -> None:
        """Capture the foreground window, optionally before popup dismissal."""
        self._request(self._prepare_active_window, freeze_first=freeze_first)

    def _prepare_active_window(self) -> _PreparedCapture:
        """Snapshot foreground geometry and pixels before any focus changes."""
        frozen = self._screens.freeze()
        window = self._windows.foreground()
        if window is None:
            msg = "There is no active window to capture"
            raise CaptureError(msg)
        return _PreparedCapture(
            frozen, CaptureMode.WINDOW, window.bounds, window.title, repeat_area=False
        )

    def repeat_last(self, *, freeze_first: bool = False) -> None:
        """Repeat the last capture without asking (UX-CAP-09)."""
        self._request(self._prepare_repeat, freeze_first=freeze_first)

    def _prepare_repeat(self) -> _PreparedCapture:
        """Resolve repeat against the same desktop moment as a new capture."""
        mode, area = self._last_mode, self._last_area
        if mode is None:
            return self._prepare_region()
        if mode is CaptureMode.FULLSCREEN:
            return self._prepare_fullscreen()
        if area is None:
            return self._prepare_active_window()
        return _PreparedCapture(self._screens.freeze(), mode, area, self._last_title)

    def _start_prepared(self, prepared: _PreparedCapture | None) -> None:
        """Use only the saved pixels and targets after input ownership settles."""
        if prepared is None or self._cancelled_start:
            return
        if prepared.area is None:
            self._select_region(prepared)
            return
        image = prepared.frozen.crop(prepared.area)
        area = prepared.area.rounded().intersected(prepared.frozen.bounds)
        if (
            self._emit(image, prepared.mode, area, prepared.title)
            and not prepared.repeat_area
        ):
            self._last_area = None  # Repeat resolves the current target again

    def cancel(self) -> None:
        """Close any open overlay without capturing."""
        if self.is_selecting:
            self._on_cancel()

    # Overlay results

    def _on_region(self, area: Rect, output: ClickOutput) -> None:
        """Capture the chosen region from the frozen screen."""
        self._finish_selection(area, CaptureMode.REGION, "Screenshot", output)

    def _on_window(self, area: Rect, title: str, output: ClickOutput) -> None:
        """Capture the clicked window from the frozen screen."""
        self._finish_selection(area, CaptureMode.WINDOW, title, output)

    def _on_scroll(
        self,
        point: Point,
        title: str,
        from_top: bool,  # noqa: FBT001 - Qt slot
        output: ClickOutput,
    ) -> None:
        """Hand a panel clicked with a scrolling modifier to scrolling capture."""
        self._close_overlays()
        self.scroll_requested.emit(point, title, from_top, output)

    def _on_cancel(self) -> None:
        """Close overlays and report cancellation."""
        self._close_overlays()
        self.cancelled.emit()

    def _finish_selection(
        self,
        area: Rect,
        mode: CaptureMode,
        title: str,
        output: ClickOutput = ClickOutput.USUAL,
    ) -> None:
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
        self._emit(image, mode, area, title, output)

    # Internals

    def _request(
        self, prepare: Callable[[], _PreparedCapture], *, freeze_first: bool
    ) -> None:
        """Reserve a session, snapshot hotkey content, then hand off Qt input.

        Hotkeys preserve the pixels the user requested before closing popups.
        Tray commands keep the usual hide-before-freeze behavior. Both defer
        selection and delivery until the current native/menu dispatch unwinds.
        """
        if self.is_selecting:
            self._activate_under_cursor()
            return
        self._pending = prepare
        if freeze_first and not self._prepare_before_handoff(prepare):
            return
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

    def _prepare_before_handoff(self, prepare: Callable[[], _PreparedCapture]) -> bool:
        """Keep reservation and cancellation valid through synchronous capture."""
        self._starting = True
        self._cancelled_start = False
        try:
            self._prepared = self._prepare(prepare)
            return self._prepared is not None
        finally:
            self._starting = False
            if self._cancelled_start:
                self._close_overlays()

    def _prepare(
        self, prepare: Callable[[], _PreparedCapture]
    ) -> _PreparedCapture | None:
        """Drop failed or reentrantly cancelled preparation before using its frame."""
        try:
            prepared = prepare()
        except AppError as err:
            self._fail(err)
            return None
        return None if self._cancelled_start else prepared

    def _start_pending(self) -> None:
        """Start one request only when Qt's previous input owner has released it."""
        prepare, self._pending = self._pending, None
        prepared, self._prepared = self._prepared, None
        if prepare is None:
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
            self._start_prepared(prepared or self._prepare(prepare))
        except AppError as err:
            self._fail(err)
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

    def _emit(
        self,
        image: QImage,
        mode: CaptureMode,
        area: Rect,
        title: str,
        output: ClickOutput = ClickOutput.USUAL,
    ) -> bool:
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
                output=output,
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
        self._prepared = None
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
