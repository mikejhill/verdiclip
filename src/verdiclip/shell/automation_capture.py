"""Run ``verdiclip ctl capture`` requests in the app and answer when they finish."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QImage

from verdiclip.automation.capturing import CaptureKind, CaptureOutcome, CaptureSpec
from verdiclip.automation.protocol import (
    AutomationError,
    Deferred,
    ErrorCode,
    JsonObject,
    JsonValue,
)
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.models import ScreenGeometry
from verdiclip.editor.page import DocumentRecord
from verdiclip.exceptions import AppError
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.windows import bring_to_front

logger = logging.getLogger(__name__)

# Let a window that was just raised repaint before its pixels are taken
SETTLE_MS: Final = 350


class CapturableWindow(Protocol):
    """A top-level window: its handle, caption and physical frame."""

    @property
    def handle(self) -> int:
        """Native window handle."""

    @property
    def title(self) -> str:
        """Window caption."""

    @property
    def bounds(self) -> Rect:
        """Frame in physical desktop pixels."""


class WindowList(Protocol):
    """Finds windows to capture."""

    def visible_windows(
        self, *, exclude: frozenset[int] = ...
    ) -> Sequence[CapturableWindow]:
        """Return visible windows, topmost first."""

    def foreground(self) -> CapturableWindow | None:
        """Return the active window, if any."""


class Choosers(Protocol):
    """The app's on-screen choosers and the scrolling engine."""

    def choose_region(self) -> None:
        """Show the region overlay (its Ctrl+click starts a scrolling capture)."""

    def choose_scrolling(self) -> None:
        """Show the scrolling chooser."""

    def scroll_at(self, point: Point, title: str, *, from_top: bool) -> None:
        """Start a scrolling capture of the panel at ``point``."""


type Admit = Callable[[QImage, str, Path | None, datetime, bool], DocumentRecord]


@dataclass(slots=True)
class _Pending:
    """The one capture request in flight."""

    spec: CaptureSpec
    answer: Deferred[CaptureOutcome]
    warning: str = ""
    scrolling: bool = False  # A scrolling session belongs to this request


class AutomationCaptures(QObject):
    """Take captures for ``ctl``; the controller routes results back here.

    One request runs at a time. Captures that need no choice finish at once;
    a chooser or a scrolling capture answers when the controller reports the
    result, a cancellation or a failure.
    """

    def __init__(  # noqa: PLR0913 - the app's capture collaborators
        self,
        screens: ScreenSource,
        windows: WindowList,
        choosers: Choosers,
        admit: Admit,
        delivery: Callable[[], ImageDelivery],
        *,
        activate: Callable[[int], bool] = bring_to_front,
        settle_ms: int = SETTLE_MS,
    ) -> None:
        super().__init__()
        self._screens, self._windows, self._choosers = screens, windows, choosers
        self._admit, self._delivery = admit, delivery
        self._activate, self._settle_ms = activate, settle_ms
        self._pending: _Pending | None = None

    @property
    def waiting(self) -> bool:
        """Return whether a request is waiting for a capture to finish."""
        return self._pending is not None

    def start(self, spec: CaptureSpec) -> Deferred[CaptureOutcome]:
        """Begin ``spec``; the answer settles when the capture is open in a tab."""
        answer: Deferred[CaptureOutcome] = Deferred()
        pending = _Pending(spec, answer)
        self._pending = pending
        try:
            self._begin(pending)
        except AutomationError as err:
            self._finish(pending, error=err)
        except AppError as err:
            self._finish(pending, error=AutomationError(ErrorCode.FAILED, str(err)))
        return answer

    def targets(self) -> JsonObject:
        """Describe every monitor and capturable window."""
        monitors: list[JsonValue] = []
        primary = QGuiApplication.primaryScreen()
        for number, screen in enumerate(QGuiApplication.screens(), start=1):
            geometry = ScreenGeometry.from_screen(screen)
            monitors.append(
                {
                    "monitor": number,
                    "name": geometry.name,
                    "bounds": _rect(geometry.physical),
                    "scale": geometry.scale,
                    "primary": screen is primary,
                }
            )
        front = self._windows.foreground()
        windows: list[JsonValue] = [
            {
                "handle": w.handle,
                "title": w.title,
                "bounds": _rect(w.bounds),
                "foreground": front is not None and w.handle == front.handle,
            }
            for w in self._windows.visible_windows()
        ]
        return {"monitors": monitors, "windows": windows}

    # Results routed by the controller

    def deliver(
        self, image: QImage, title: str, taken_at: datetime, warning: str = ""
    ) -> None:
        """Open a chooser's or the scrolling engine's result for the request."""
        pending = self._pending
        if pending is not None:
            self._open(pending, image, title, taken_at, warning)

    def cancelled(self) -> None:
        """The user dismissed the chooser or stopped before anything was taken."""
        pending = self._pending
        if pending is not None:
            msg = "The capture was cancelled"
            self._finish(pending, error=AutomationError(ErrorCode.CANCELLED, msg))

    def scrolling_started(self) -> None:
        """A scrolling session began for the waiting request (overlay Ctrl+click)."""
        if self._pending is not None:
            self._pending.scrolling = True

    def scrolling_finished(self) -> None:
        """A scrolling session ended; with no result yet, its request is cancelled.

        Only the request that started the session is answered, never a later one.
        """
        pending = self._pending
        if pending is not None and pending.scrolling:
            self.cancelled()

    def failed(self, message: str) -> None:
        """The capture failed; pass the reason on."""
        pending = self._pending
        if pending is not None:
            self._finish(pending, error=AutomationError(ErrorCode.FAILED, message))

    def shutdown(self) -> None:
        """Answer a waiting request before the app closes."""
        self.failed("VerdiClip is closing")

    # Internals

    def _begin(self, pending: _Pending) -> None:
        spec = pending.spec
        if spec.kind is CaptureKind.REGION:
            if spec.region is None:
                self._choosers.choose_region()
            else:
                self._take(pending, spec.region, "Screenshot")
        elif spec.kind is CaptureKind.FULLSCREEN:
            self._take(pending, self._screen_area(spec), "Screen")
        elif spec.kind is CaptureKind.WINDOW:
            window = self._window(spec)
            self._raised(
                pending,
                window,
                lambda: self._take(pending, window.bounds, window.title),
            )
        elif spec.interactive:
            pending.scrolling = True
            self._choosers.choose_scrolling()
        elif spec.at is not None:
            pending.scrolling = True
            self._choosers.scroll_at(spec.at, "", from_top=spec.from_top)
        else:
            window = self._window(spec)

            def scroll() -> None:
                pending.scrolling = True
                self._choosers.scroll_at(
                    window.bounds.center, window.title, from_top=spec.from_top
                )

            self._raised(pending, window, scroll)

    def _raised(
        self, pending: _Pending, window: CapturableWindow, then: Callable[[], None]
    ) -> None:
        """Run ``then`` with ``window`` in front, raising it first if needed."""
        front = self._windows.foreground()
        if front is not None and front.handle == window.handle:
            then()
            return
        if not self._activate(window.handle):
            logger.info("Could not bring window %s to the front", window.handle)
            pending.warning = (
                f"Windows didn't bring {window.title!r} to the front, so other "
                "windows may cover part of it."
            )
        QTimer.singleShot(self._settle_ms, lambda: self._after_raise(pending, then))

    def _after_raise(self, pending: _Pending, then: Callable[[], None]) -> None:
        if self._pending is not pending:
            return
        try:
            then()
        except AppError as err:
            self._finish(pending, error=AutomationError(ErrorCode.FAILED, str(err)))

    def _take(self, pending: _Pending, area: Rect, title: str) -> None:
        """Freeze the desktop now and open ``area`` of it."""
        frozen = self._screens.freeze()
        image = frozen.crop(area)
        self._open(pending, image, title, datetime.now().astimezone())

    def _screen_area(self, spec: CaptureSpec) -> Rect:
        """Return the monitor ``spec`` names, all of them, or the cursor's."""
        screens = QGuiApplication.screens()
        if spec.all_screens:
            return self._screens.freeze().bounds
        if spec.monitor is not None:
            if spec.monitor > len(screens):
                msg = (
                    f"There is no monitor {spec.monitor}; this PC has "
                    f"{len(screens)} (see `verdiclip ctl targets`)"
                )
                raise AutomationError(ErrorCode.NOT_FOUND, msg)
            screen = screens[spec.monitor - 1]
        else:
            screen = (
                QGuiApplication.screenAt(QCursor.pos())
                or QGuiApplication.primaryScreen()
            )
        return ScreenGeometry.from_screen(screen).physical

    def _window(self, spec: CaptureSpec) -> CapturableWindow:
        """Return the window ``spec`` names, or the active one.

        Raises:
            AutomationError: If no visible window matches.
        """
        windows = self._windows.visible_windows()
        found: CapturableWindow | None
        if spec.handle is not None:
            found = next((w for w in windows if w.handle == spec.handle), None)
            wanted = f"handle {spec.handle}"
        elif spec.title is not None:
            needle = spec.title.casefold()
            found = next(
                (w for w in windows if w.title.casefold() == needle),
                next((w for w in windows if needle in w.title.casefold()), None),
            )
            wanted = f"a title containing {spec.title!r}"
        elif spec.at is not None:
            at = spec.at
            found = next((w for w in windows if w.bounds.contains(at)), None)
            wanted = f"a window at {at.x:g},{at.y:g}"
        else:
            found = self._windows.foreground()
            wanted = "an active window"
        if found is None:
            msg = (
                f"No visible window has {wanted}; minimized windows can't be "
                "captured. Run `verdiclip ctl targets`."
            )
            raise AutomationError(ErrorCode.NOT_FOUND, msg)
        return found

    def _open(
        self,
        pending: _Pending,
        image: QImage,
        title: str,
        taken_at: datetime,
        warning: str = "",
    ) -> None:
        """Save or copy as asked, then open the capture in a tab."""
        spec = pending.spec
        notes = [n for n in (pending.warning, warning) if n]
        saved: Path | None = None
        copied = False
        delivery = self._delivery()
        try:
            if spec.output is not None:
                saved = delivery.save(image, spec.output)
            if spec.copy:
                delivery.copy(image)
                copied = True
        except AppError as err:
            notes.append(f"Not delivered: {err}")
        try:
            record = self._admit(
                image, title, saved, taken_at, saved is not None or copied
            )
        except (AppError, RuntimeError, ValueError, MemoryError) as err:
            msg = f"The capture could not be opened in the editor: {err}"
            self._finish(pending, error=AutomationError(ErrorCode.FAILED, msg))
            return
        outcome = CaptureOutcome(record, saved, copied, " ".join(notes))
        self._finish(pending, outcome=outcome)

    def _finish(
        self,
        pending: _Pending,
        *,
        outcome: CaptureOutcome | None = None,
        error: AutomationError | None = None,
    ) -> None:
        if self._pending is pending:
            self._pending = None
        if outcome is not None:
            pending.answer.resolve(outcome)
        elif error is not None:
            pending.answer.reject(error)


def _rect(rect: Rect) -> JsonValue:
    return [round(rect.x), round(rect.y), round(rect.width), round(rect.height)]
