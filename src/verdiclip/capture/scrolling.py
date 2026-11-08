"""Bounded vertical capture, independent of UIA and the ordinary frozen selector.

This is deliberately an opt-in feasibility slice until real-app qualification
passes. Only a complete, unambiguous sequence produces an image. It is never a
recapturable desktop rectangle and must not enter ordinary Repeat history.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtGui import QImage, QPainter

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect

MAX_SECONDS = 25.0
MAX_FRAMES = 80
MAX_PIXELS = 40_000_000
MIN_OVERLAP = 32
END_PERCENT = 100.0


@dataclass(frozen=True, slots=True)
class ScrollPosition:
    """UIA percentages and the visible fraction of the full vertical content."""

    horizontal: float
    vertical: float
    view_size: float


class ScrollTarget(Protocol):
    """One retained target, used only inside a disposable helper."""

    @property
    def area(self) -> Rect:
        """Return the viewport in physical desktop pixels."""

    def check(self) -> None:
        """Reject input, focus, geometry, target, or visibility changes."""

    def position(self) -> ScrollPosition:
        """Read the current scroll state."""

    def move(self, vertical: float) -> None:
        """Set the vertical percentage without changing horizontal position."""

    def frame(self) -> QImage:
        """Capture the same entire viewport, with no guessed content crop."""


@dataclass(frozen=True, slots=True)
class ScrollResult:
    """One terminal result; failed and cancelled sequences have no image."""

    image: QImage | None
    message: str
    restoration: str
    frames: int


class VerticalStitch:
    """Join uniquely matching full-width rows, rejecting ambiguous evidence.

    No fixed sidebar or toolbar is silently thrown away. Repeating or animated
    content that cannot be matched is unsupported, rather than guessed.
    """

    def __init__(self, first: QImage) -> None:
        self._validate(first)
        self._frames = [first.copy()]
        self._previous = first.copy()
        self._height = first.height()

    @staticmethod
    def _validate(image: QImage) -> None:
        if image.isNull() or image.width() * image.height() > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")

    @staticmethod
    def _rows(image: QImage) -> list[bytes]:
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        raw = bytes(image.constBits())
        stride = image.bytesPerLine()
        return [raw[y * stride : (y + 1) * stride] for y in range(image.height())]

    def append(self, image: QImage) -> None:
        """Append only the new rows after one unique full-width overlap."""
        self._validate(image)
        if image.size() != self._previous.size():
            raise CaptureError("The scroll panel changed size")
        old, new = self._rows(self._previous), self._rows(image)
        matches = [
            overlap
            for overlap in range(MIN_OVERLAP, len(old))
            if old[-overlap:] == new[:overlap]
        ]
        if len(matches) != 1:
            raise CaptureError("The panel's scrolling pixels cannot be joined reliably")
        overlap = matches[0]
        added = image.height() - overlap
        if image.width() * (self._height + added) > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")
        self._frames.append(image.copy(0, overlap, image.width(), added))
        self._height += added
        self._previous = image.copy()

    def finish(self) -> QImage:
        """Return one detached image after the caller has reached the bottom."""
        result = QImage(
            self._previous.width(), self._height, QImage.Format.Format_RGB32
        )
        if result.isNull():
            raise CaptureError("Not enough memory for the scrolling image")
        painter = QPainter(result)
        y = 0
        for frame in self._frames:
            painter.drawImage(0, y, frame)
            y += frame.height()
        painter.end()
        return result


class ScrollTransaction:
    """Own one sequence and restore only while the original user state remains.

    A provider call can block: the process owner must impose its own deadline.
    Restoration is best effort, never a reason to fight new input or focus.
    """

    def __init__(
        self,
        target: ScrollTarget,
        cancelled: Callable[[], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target, self._cancelled = target, cancelled
        self._clock, self._pause = clock, pause
        self._deadline = clock() + MAX_SECONDS
        self._frames = 0
        self._moved = False
        self._expected: ScrollPosition | None = None

    def _check(self, *, restoring: bool = False) -> None:
        if not restoring and self._cancelled():
            raise CaptureError("Scrolling capture cancelled")
        if self._clock() >= self._deadline:
            raise CaptureError("Scrolling capture reached its time limit")
        self._target.check()

    def _frame(self, position: ScrollPosition) -> QImage:
        """Wait briefly for two equal frames, bounded by the transaction limit."""
        self._check()
        previous = self._target.frame()
        if self._target.position() != position:
            raise CaptureError("The scroll panel moved during capture")
        for _ in range(8):
            self._pause(0.08)
            self._check()
            current = self._target.frame()
            if self._target.position() != position:
                raise CaptureError("The scroll panel moved during capture")
            if current == previous:
                self._frames += 1
                return current
            previous = current
        raise CaptureError("The scroll panel is still changing")

    def _settle(
        self, before: ScrollPosition, percent: float, *, restoring: bool = False
    ) -> ScrollPosition:
        """Observe one write; unchanged reads never acknowledge pending movement."""
        previous = None
        for _ in range(8):
            self._pause(0.08)
            self._check(restoring=restoring)
            current = self._target.position()
            self._check(restoring=restoring)
            if (
                current.horizontal != before.horizontal
                or current.view_size != before.view_size
            ):
                raise CaptureError("The scroll panel moved unexpectedly")
            if (
                not restoring
                and percent not in (0.0, END_PERCENT)
                and abs(current.vertical - before.vertical)
                > abs(percent - before.vertical) * 1.5
            ):
                raise CaptureError("The scroll panel moved unexpectedly")
            reached = (
                current.vertical == percent
                if restoring or percent in (0.0, END_PERCENT)
                else (current.vertical - before.vertical) * (percent - before.vertical)
                > 0.0
            )
            if reached and current == previous:
                return current
            previous = current
        raise CaptureError("The scroll panel did not settle; its position is uncertain")

    def _move(self, percent: float) -> ScrollPosition:
        self._check()
        before = self._target.position()
        self._check()
        if before != self._expected:
            raise CaptureError("The scroll panel changed position unexpectedly")
        if before.vertical == percent:
            return before
        # Mark before crossing COM: a failed call may already have scrolled.
        self._moved = True
        self._expected = None
        self._target.move(percent)
        self._expected = self._settle(before, percent)
        self._check()
        return self._expected

    def _sequence(self, initial: ScrollPosition) -> QImage:
        position = self._move(0.0)
        if position.vertical != 0.0:
            raise CaptureError("The panel did not reach its top")
        stitch = VerticalStitch(self._frame(position))
        while position.vertical < END_PERCENT:
            if self._frames >= MAX_FRAMES:
                raise CaptureError("Scrolling capture reached its page limit")
            if position.view_size != initial.view_size:
                raise CaptureError("The panel's content changed size")
            # Half a viewport leaves ample overlap without wheel chaining.
            step = 50.0 * position.view_size / (100.0 - position.view_size)
            following = self._move(min(100.0, position.vertical + step))
            if (
                following.vertical <= position.vertical
                or following.vertical - position.vertical > step * 1.5
                or following.horizontal != initial.horizontal
                or following.view_size != initial.view_size
            ):
                raise CaptureError("The scroll panel moved unexpectedly")
            stitch.append(self._frame(following))
            position = following
        self._check()
        return stitch.finish()

    def _restore(self, initial: ScrollPosition) -> str:
        if not self._moved:
            return "unchanged"
        try:
            # Deliberately ignore a cooperative cancellation here, but never
            # ignore user input, lost focus, changed geometry, or the deadline.
            if self._clock() >= self._deadline:
                return "not restored: time limit"
            if self._expected is None:
                return "not restored: the provider write is unresolved"
            self._target.check()
            current = self._target.position()
            self._check(restoring=True)
            if (
                current != self._expected
                or self._expected.horizontal != initial.horizontal
                or self._expected.view_size != initial.view_size
            ):
                return "not restored: scroll position changed unexpectedly"
            if self._expected != initial:
                before = self._expected
                self._expected = None
                self._target.move(initial.vertical)
                self._expected = self._settle(before, initial.vertical, restoring=True)
        except (CaptureError, OSError):
            return "not restored: the target or user state changed"
        return "restored"

    @staticmethod
    def _validate_position(initial: ScrollPosition) -> None:
        if not 0.0 < initial.view_size < END_PERCENT:
            raise CaptureError("The selected panel is not vertically scrollable")

    def run(self) -> ScrollResult:
        """Return a single terminal outcome, discarding incomplete pixels."""
        initial: ScrollPosition | None = None
        image = None
        message = ""
        try:
            self._check()
            initial = self._target.position()
            self._validate_position(initial)
            self._expected = initial
            image = self._sequence(initial)
        except (CaptureError, OSError) as error:
            message = str(error)
        restoration = self._restore(initial) if initial is not None else "unchanged"
        if image is not None and restoration != "restored":
            image, message = None, restoration
        # User activity while restoring also invalidates a pending success.
        if image is not None:
            try:
                self._check()
            except (CaptureError, OSError) as error:
                image, message = None, str(error)
        return ScrollResult(image, message, restoration, self._frames)
