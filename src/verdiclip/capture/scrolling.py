"""Scroll one panel from top to bottom and join what it showed.

Runs in the disposable helper process (``scroll_helper``). The target is a
small protocol so the sequence can be tested without Windows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtGui import QImage

from verdiclip.capture.stitch import Frame, stitch
from verdiclip.exceptions import CaptureError

MAX_SECONDS = 45.0
MAX_FRAMES = 100
MAX_PIXELS = 60_000_000
END_PERCENT = 99.9
# Advance this share of the viewport per step, keeping an overlap to match.
STEP = 0.75
_POLL_SECONDS = 0.05
# Wait this long for the first visible change after a scroll request.
_CHANGE_SECONDS = 0.6
# Give up waiting for animation to stop after this long.
_SETTLE_SECONDS = 1.5
_UNSURE_END = "The panel may not have reached the end; check the bottom of the image."


@dataclass(frozen=True, slots=True)
class ScrollPosition:
    """Vertical scroll percent (0-100) and visible share of the content (%)."""

    percent: float
    view_size: float

    def pixels_per_percent(self, viewport: int) -> float | None:
        """Pixels the content moves for one percent, when the size is known."""
        if not 0.0 < self.view_size < 100.0:  # noqa: PLR2004 - percent bounds
            return None
        content = viewport * 100.0 / self.view_size
        return (content - viewport) / 100.0


class ScrollTarget(Protocol):
    """One vertically scrollable panel."""

    @property
    def height(self) -> int:
        """Viewport height in physical pixels."""

    def position(self) -> ScrollPosition:
        """Read the current scroll position."""

    def scroll_to(self, percent: float) -> None:
        """Ask the provider to scroll vertically, keeping horizontal position."""

    def grab(self) -> QImage:
        """Capture the viewport's current pixels."""

    def check(self) -> None:
        """Raise ``CaptureError`` if the panel or its window went away."""


@dataclass(frozen=True, slots=True)
class ScrollResult:
    """One terminal outcome: an image, or a message saying why there is none."""

    image: QImage | None
    message: str
    frames: int
    warning: str = ""


class CancelledError(CaptureError):
    """The user pressed Esc."""


class ScrollCapture:
    """Top-to-bottom sequence with best-effort restoration of the start position."""

    def __init__(
        self,
        target: ScrollTarget,
        cancelled: Callable[[], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target, self._cancelled = target, cancelled
        self._clock, self._sleep = clock, sleep
        self._deadline = clock() + MAX_SECONDS

    def run(self) -> ScrollResult:
        """Capture, restore the starting position, then stitch."""
        target = self._target
        start = target.position()
        frames: list[Frame] = []
        notes: list[str] = []
        try:
            self._capture(start, frames, notes)
        except CancelledError:
            self._restore(start)
            return ScrollResult(None, "Scrolling capture cancelled", len(frames))
        except CaptureError as error:
            self._restore(start)
            return ScrollResult(None, str(error), len(frames))
        if not self._restore(start):
            notes.append("The panel could not be scrolled back to where it was.")
        result = stitch(frames)
        if result.estimated_seams:
            notes.append(
                f"{result.estimated_seams} seam(s) were estimated; check the image."
            )
        return ScrollResult(result.image, "", len(frames), " ".join(notes))

    def _check(self) -> None:
        if self._cancelled():
            raise CancelledError("Scrolling capture cancelled")
        self._target.check()

    def _capture(
        self, start: ScrollPosition, frames: list[Frame], notes: list[str]
    ) -> None:
        target = self._target
        self._check()
        if start.view_size >= 100.0:  # noqa: PLR2004 - everything is visible
            frames.append(Frame(self._settle(None)))
            return
        before = target.grab() if start.percent > 0.0 else None
        target.scroll_to(0.0)
        image = self._settle(before)
        frames.append(Frame(image))
        position = target.position()
        pixels = 0
        reading = position.percent  # The provider's last answer
        assumed = False  # ``position`` is where we asked to go, not a reading
        while position.percent < END_PERCENT:
            self._check()
            if len(frames) >= MAX_FRAMES or self._clock() >= self._deadline:
                notes.append("Stopped early at the capture limit.")
                return
            scale = position.pixels_per_percent(target.height)
            step = 10.0 if scale is None else STEP * target.height / scale
            requested = min(100.0, position.percent + max(step, 0.01))
            target.scroll_to(requested)
            following = self._settle(image)
            if following == image:
                return  # The panel did not move: it is at the end.
            moved = self._position_after(reading)
            assumed, reading = moved.percent == reading, moved.percent
            if assumed:
                # Pixels moved but the reported position did not (yet). Trust
                # the pixels; the stitcher aligns them without a distance.
                moved, distance = ScrollPosition(requested, moved.view_size), None
            else:
                distance = (
                    None
                    if scale is None
                    else (moved.percent - position.percent) * scale
                )
            frames.append(Frame(following, distance))
            pixels += following.width() * following.height()
            if pixels >= MAX_PIXELS:
                notes.append("Stopped early at the image size limit.")
                return
            if moved.percent < position.percent:
                notes.append(_UNSURE_END)
                return
            image, position = following, moved
        if assumed and self._position_after(-1.0).percent < END_PERCENT:
            notes.append(_UNSURE_END)

    def _position_after(self, previous: float) -> ScrollPosition:
        """Read the position, giving a lagging provider time to report a change."""
        started = self._clock()
        position = self._target.position()
        while (
            position.percent == previous and self._clock() - started < _CHANGE_SECONDS
        ):
            self._sleep(_POLL_SECONDS)
            position = self._target.position()
        return position

    def _settle(self, previous: QImage | None) -> QImage:
        """Wait for a visible change (if any), then for two identical grabs."""
        started = self._clock()
        current = self._target.grab()
        while (
            previous is not None
            and current == previous
            and self._clock() - started < _CHANGE_SECONDS
        ):
            self._sleep(_POLL_SECONDS)
            current = self._target.grab()
        while self._clock() - started < _SETTLE_SECONDS:
            self._sleep(_POLL_SECONDS)
            following = self._target.grab()
            if following == current:
                break
            current = following
        return current

    def _restore(self, start: ScrollPosition) -> bool:
        try:
            self._target.scroll_to(start.percent)
        except (CaptureError, OSError):
            return False
        return True
