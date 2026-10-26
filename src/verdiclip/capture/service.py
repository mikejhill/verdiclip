"""Capture lifecycle with preserved foreground and acknowledged input cleanup."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol
from weakref import WeakSet

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QScreen
from shiboken6 import getCppPointer

from verdiclip.capture.grabber import FrozenScreen, ScreenSource
from verdiclip.capture.input_broker import InputBroker, make_input_broker
from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import Capture, CaptureMode, ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay, SharedSelection, WindowTarget
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)

type DisplaySnapshot = tuple[tuple[int, ScreenGeometry], ...]


def read_display_snapshot() -> DisplaySnapshot:
    """Read identity, physical/logical geometry and scale without changing displays."""
    return tuple(
        (int(getCppPointer(screen)[0]), ScreenGeometry.from_screen(screen))
        for screen in QGuiApplication.screens()
    )


class WindowSource(Protocol):
    """Read-only top-level window discovery."""

    def visible_windows(
        self, *, exclude: frozenset[int] = ...
    ) -> Sequence[WindowTarget]:
        """Return capturable windows in z-order."""

    def foreground(self) -> WindowTarget | None:
        """Return the existing foreground window without changing it."""


class CaptureService(QObject):
    """Freeze pixels and route selection independently of popup/modal ownership."""

    captured = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(
        self,
        screens: ScreenSource,
        windows: WindowSource,
        *,
        show_magnifier: bool = True,
        input_broker: InputBroker | None = None,
        display_snapshot: Callable[[], DisplaySnapshot] = read_display_snapshot,
    ) -> None:
        super().__init__()
        self._screens, self._windows = screens, windows
        self._show_magnifier = show_magnifier
        self._broker = (
            input_broker if input_broker is not None else make_input_broker(self)
        )
        self._broker.ready.connect(self._input_ready)
        self._broker.input_event.connect(self._input_event)
        self._broker.closed.connect(self._input_closed)
        self._overlays: list[SelectionOverlay] = []
        self._frozen: FrozenScreen | None = None
        self._shared: SharedSelection | None = None
        self._last_mode: CaptureMode | None = None
        self._last_area: Rect | None = None
        self._last_title = ""
        self._pending: Callable[[], None] | None = None
        self._starting = self._aborted = self._preparing = self._ending = False
        self._result: Capture | None = None
        self._error = ""
        self._generation = 0
        self._display_snapshot = display_snapshot
        self._prepared_screens: tuple[QScreen, ...] = ()
        self._layout: DisplaySnapshot = ()
        self._connected_screens: WeakSet[QScreen] = WeakSet()
        self._connect_screens()
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.screenAdded.connect(self._layout_changed)
            app.screenRemoved.connect(self._layout_changed)
        self._handoff = QTimer(self)
        self._handoff.setSingleShot(True)
        self._handoff.timeout.connect(self._start_pending)

    @property
    def is_selecting(self) -> bool:
        """Queued start and unconfirmed cleanup are part of input ownership."""
        return (
            self._pending is not None
            or self._starting
            or self._preparing
            or self._ending
            or bool(self._overlays)
            or self._broker.busy
        )

    @property
    def overlays(self) -> tuple[SelectionOverlay, ...]:
        """Expose only this capture's newly created overlays."""
        return tuple(self._overlays)

    @property
    def preserve_foreground(self) -> bool:
        """Delivery and failure presentation must also avoid foreground activation."""
        return True

    def set_show_magnifier(self, *, show: bool) -> None:
        """Configure guides for future selection windows."""
        self._show_magnifier = show

    def capture_region(self) -> None:
        """Queue an interactive region/window selection without dismissing content."""
        self._request(self._select_region)

    def capture_fullscreen(self) -> None:
        """Queue a read-only cursor-monitor capture, with no input interception."""
        self._request(self._capture_fullscreen)

    def capture_active_window(self) -> None:
        """Queue a read-only capture of the original foreground window."""
        self._request(self._capture_active_window)

    def repeat_last(self) -> None:
        """Repeat safely; a missing previous capture starts a new selection."""
        self._request(self._repeat_last)

    def _request(self, start: Callable[[], None]) -> None:
        if self.is_selecting:
            return
        self._pending = start
        self._handoff.start(0)

    def _start_pending(self) -> None:
        start, self._pending = self._pending, None
        if start is None:
            return
        self._starting = True
        self._aborted = False
        try:
            start()
        except AppError as error:
            self._end(error=str(error))
        finally:
            self._starting = False

    def _select_region(self) -> None:
        self._prepared_screens = tuple(QGuiApplication.screens())
        self._layout = self._display_snapshot()
        if tuple(
            int(getCppPointer(screen)[0]) for screen in self._prepared_screens
        ) != tuple(identity for identity, _geometry in self._layout):
            raise CaptureError("The display layout changed. Start a new capture.")
        screens = [geometry for _identity, geometry in self._layout]
        if not screens:
            raise CaptureError("No display is available for selection")
        shared = SharedSelection(screens)
        cursor = QCursor.pos()
        initial = shared.to_physical(Point(cursor.x(), cursor.y()))
        shared.move_to(initial)
        self._shared = shared
        self._preparing = True
        bounds = Rect.bounding(
            [
                point
                for screen in screens
                for point in (screen.physical.top_left, screen.physical.bottom_right)
            ]
        )
        self._broker.start(initial, bounds)

    def _input_ready(self) -> None:
        if not self._preparing or self._ending:
            return
        if not self._layout_valid():
            return
        generation = self._generation
        try:
            frozen = self._screens.freeze()
            windows = list(self._windows.visible_windows(exclude=frozenset()))
        except AppError as error:
            self._end(error=str(error))
            return
        if (
            generation != self._generation
            or not self._broker.active
            or not self._layout_valid()
        ):
            return
        self._frozen = frozen
        shared = self._shared
        if shared is None:
            self._end(error="The display layout changed during capture")
            return
        self._show_overlays(frozen, windows, shared, generation)

    def _show_overlays(
        self,
        frozen: FrozenScreen,
        windows: Sequence[WindowTarget],
        shared: SharedSelection,
        generation: int,
    ) -> None:
        for screen, (_identity, geometry) in zip(
            self._prepared_screens, self._layout, strict=True
        ):
            if generation != self._generation:
                return
            try:
                overlay = SelectionOverlay(
                    geometry,
                    screen,
                    frozen,
                    windows,
                    show_magnifier=self._show_magnifier,
                    shared=shared,
                    external_input=True,
                )
            except (AppError, RuntimeError) as error:
                logger.warning("Capture overlay unavailable: %s", error)
                self._end(error="The display became unavailable. Start a new capture.")
                return
            overlay.region_chosen.connect(self._on_region)
            overlay.window_chosen.connect(self._on_window)
            overlay.cancelled.connect(self.cancel)
            self._overlays.append(overlay)
            overlay.show()
            if generation != self._generation:
                return
        self._preparing = False

    def _input_event(self, event: object) -> None:
        if not isinstance(event, SelectionInput) or self._ending:
            return
        if not self._layout_valid():
            return
        if event.kind is InputKind.CANCEL:
            if event.reason in {"escape", "right-button", "keyboard-interference"}:
                self.cancel()
            else:
                self._end(
                    error="Capture input changed or became unavailable. Try again."
                )
            return
        overlay = next(
            (
                item
                for item in self._overlays
                if item.screen_geometry.physical.contains(event.point)
            ),
            None,
        )
        if overlay is None and self._overlays:
            overlay = self._overlays[0]
        if overlay is not None:
            overlay.receive_input(event)

    def _capture_fullscreen(self) -> None:
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        if screen is None:
            raise CaptureError("No display is available for capture")
        area = ScreenGeometry.from_screen(screen).physical
        frozen = self._screens.freeze()
        self._deliver(
            frozen.crop(area),
            CaptureMode.FULLSCREEN,
            area.intersected(frozen.bounds),
            "Screen",
        )

    def _capture_active_window(self) -> None:
        window = self._windows.foreground()
        if window is None:
            raise CaptureError("There is no active window to capture")
        frozen = self._screens.freeze()
        area = window.bounds.rounded().intersected(frozen.bounds)
        self._deliver(
            frozen.crop(area), CaptureMode.WINDOW, area, window.title, repeat_area=False
        )

    def _repeat_last(self) -> None:
        mode, area = self._last_mode, self._last_area
        if mode is None:
            self._select_region()
        elif mode is CaptureMode.FULLSCREEN:
            self._capture_fullscreen()
        elif area is None:
            self._capture_active_window()
        else:
            frozen = self._screens.freeze()
            self._deliver(frozen.crop(area), mode, area, self._last_title)

    def _on_region(self, area: Rect) -> None:
        self._finish_selection(area, CaptureMode.REGION, "Screenshot")

    def _on_window(self, area: Rect, title: str) -> None:
        self._finish_selection(area, CaptureMode.WINDOW, title)

    def _finish_selection(self, area: Rect, mode: CaptureMode, title: str) -> None:
        if self._frozen is None or self._ending:
            return
        try:
            image = self._frozen.crop(area)
        except AppError as error:
            self._end(error=str(error))
            return
        self._end(
            result=self._capture(
                image, mode, area.rounded().intersected(self._frozen.bounds), title
            )
        )

    def cancel(self) -> None:
        """Cancel queued or active work; release only this capture's input lease."""
        if self.is_selecting and not self._ending:
            self._end()

    def _end(self, *, result: Capture | None = None, error: str = "") -> None:
        if not self._reserve_end(result=result, error=error):
            return
        if self._broker.busy:
            self._broker.release()
        else:
            self._input_closed(cleaned=True, message="")

    def _reserve_end(self, *, result: Capture | None, error: str) -> bool:
        if self._ending:
            return False
        self._ending = True
        self._aborted = True
        self._generation += 1
        self._result, self._error = result, error
        self._preparing = False
        self._pending = None
        self._handoff.stop()
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            overlay.dismiss()
        self._frozen = None
        self._shared = None
        self._layout = ()
        self._prepared_screens = ()
        return True

    def _connect_screens(self) -> None:
        for screen in QGuiApplication.screens():
            if screen not in self._connected_screens:
                self._connected_screens.add(screen)
                screen.geometryChanged.connect(self._layout_changed)
                screen.logicalDotsPerInchChanged.connect(self._layout_changed)
                screen.physicalDotsPerInchChanged.connect(self._layout_changed)

    @Slot()
    def _layout_changed(self) -> None:
        self._connect_screens()
        if self._preparing or self._overlays:
            self._layout_valid()

    def _layout_valid(self) -> bool:
        if self._layout and self._display_snapshot() != self._layout:
            self._end(error="The display layout changed. Start a new capture.")
            return False
        return True

    def _input_closed(self, cleaned: bool, message: str) -> None:  # noqa: FBT001 - Qt signal payload
        if not self._ending:
            if not (self._preparing or self._overlays):
                return
            self._reserve_end(
                result=None, error=message or "Capture input was interrupted"
            )
        result, self._result = self._result, None
        error, self._error = self._error or message, ""
        self._ending = False
        if not cleaned:
            error = (
                "Capture input cleanup could not be confirmed. "
                "Restart VerdiClip before retrying."
            )
        if error:
            logger.warning("Capture stopped: %s", error)
            self.failed.emit(error)
        elif result is not None:
            self._commit(result)
        else:
            self.cancelled.emit()

    @staticmethod
    def _capture(image: QImage, mode: CaptureMode, area: Rect, title: str) -> Capture:
        return Capture(
            image=image,
            mode=mode,
            area=area,
            title=title,
            taken_at=datetime.now().astimezone(),
            preserve_foreground=True,
        )

    def _deliver(
        self,
        image: QImage,
        mode: CaptureMode,
        area: Rect,
        title: str,
        *,
        repeat_area: bool = True,
    ) -> None:
        if not self._aborted:
            self._commit(
                self._capture(image, mode, area, title), repeat_area=repeat_area
            )

    def _commit(self, capture: Capture, *, repeat_area: bool = True) -> None:
        self._last_mode = capture.mode
        self._last_area = (
            capture.area
            if repeat_area and capture.mode is not CaptureMode.FULLSCREEN
            else None
        )
        self._last_title = capture.title
        self.captured.emit(capture)
