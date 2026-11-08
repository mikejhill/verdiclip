"""Bounded vertical capture, independent of UIA and the ordinary frozen selector.

This is deliberately an opt-in feasibility slice until real-app qualification
passes. Only a complete, unambiguous sequence produces an image. It is never a
recapturable desktop rectangle and must not enter ordinary Repeat history.
"""

from __future__ import annotations

import logging
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
_MIN_DISTINCT_ROWS = 8
_ROUNDING_FRACTION = 0.02
_PREFIX_MISMATCH_FRACTION = 0.05
# Measured Edge focus outlines, in physical pixels; not a content crop budget.
_MAX_EDGE_WIDTH = 2
_EDGE_CORNER_ROWS = 8
_MAX_PERIMETER_WIDTH = 3
_MAX_TOP_PERIMETER_ROWS = 4
_TOP_CORNER_COLUMNS = 8
BASE_REGISTRATION_DPI = 96
MAX_REGISTRATION_DPI = 192
END_PERCENT = 100.0
logger = logging.getLogger(__name__)


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

    @property
    def registration_dpi(self) -> int | None:
        """Return verified same-monitor window DPI, or conservative unknown."""

    @property
    def header(self) -> Rect | None:
        """Return a verified fixed Header rectangle within the captured area."""

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
    """Join unique exact bands, retaining the entire width in the result.

    Only measured narrow, uniform edge changes may be ignored for registration.
    Repeating or animated content without a unique match remains unsupported.
    """

    def __init__(self, first: QImage, *, dpi: int | None = None) -> None:
        self._validate(first)
        if dpi is not None and not BASE_REGISTRATION_DPI <= dpi <= MAX_REGISTRATION_DPI:
            raise CaptureError("The scrolling registration DPI is unsupported")
        self._dpi = BASE_REGISTRATION_DPI if dpi is None else dpi
        self._frames = [first.copy()]
        self._previous = first.copy()
        self._height = first.height()
        self._tail_start = 0

    def _limit(self, original: int) -> int:
        """Ceil the original pixel bound proportionately, never the scale itself."""
        return (
            original * self._dpi + BASE_REGISTRATION_DPI - 1
        ) // BASE_REGISTRATION_DPI

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

    @staticmethod
    def _alignment(old: list[bytes], new: list[bytes]) -> tuple[int, int, int] | None:
        """Find one displacement globally, using exact textured row bands."""
        ids: dict[bytes, int] = {}
        before = [ids.setdefault(row, len(ids)) for row in old]
        after = [ids.setdefault(row, len(ids)) for row in new]
        match = None
        for advance in range(1, len(before) - MIN_OVERLAP + 1):
            start = 0
            longest = (0, 0)
            for row in range(len(before) - advance + 1):
                if row < len(before) - advance and before[row + advance] == after[row]:
                    continue
                if (
                    row - start >= MIN_OVERLAP
                    and row - start > longest[1] - longest[0]
                    and len(set(after[start:row])) >= _MIN_DISTINCT_ROWS
                ):
                    longest = (start, row)
                start = row + 1
            if longest[1] > longest[0]:
                if match is not None:
                    raise CaptureError("The panel's scrolling pixels are ambiguous")
                match = (advance, *longest)
        return match

    def _edge_width(self, old: list[bytes], new: list[bytes], *, right: bool) -> int:
        """Measure a changed uniform outer strip, rejecting widths over the cap.

        Ignore only bounded corner rows when testing vertical uniformity. Stop
        at the first unchanged or textured column, never search for a crop that
        makes the alignment pass. Narrow solid content can look like an outline.
        """
        corners = self._limit(_EDGE_CORNER_ROWS)
        if len(old) <= 2 * corners:
            return 0
        before = old[corners:-corners]
        after = new[corners:-corners]
        for width in range(min(self._limit(_MAX_EDGE_WIDTH) + 1, len(old[0]) // 4)):
            column = len(old[0]) - 4 * (width + 1) if right else 4 * width
            first = before[0][column : column + 4]
            second = after[0][column : column + 4]
            if (
                first == second
                or any(row[column : column + 4] != first for row in before)
                or any(row[column : column + 4] != second for row in after)
            ):
                return width
        return 0

    def _registration(self, old: list[bytes], new: list[bytes]) -> tuple[int, int, int]:
        """Try all columns first; only no match permits a measured-edge retry."""
        match = self._alignment(old, new)
        if match is not None:
            return match
        left = self._edge_width(old, new, right=False)
        right = self._edge_width(old, new, right=True)
        if left or right:
            end = len(old[0]) - right * 4
            match = self._alignment(
                [row[left * 4 : end] for row in old],
                [row[left * 4 : end] for row in new],
            )
            if match is not None:
                logger.info(
                    "Scrolling registration edges: left_columns=%s right_columns=%s "
                    "registration_dpi=%s",
                    left,
                    right,
                    self._dpi,
                )
                return match
        raise CaptureError("The panel's scrolling pixels cannot be joined reliably")

    def _edge_colors(self, rows: list[bytes], *, right: bool) -> set[bytes]:
        """Find uniform side colors, excluding a color continuing past the cap."""
        corners = self._limit(_EDGE_CORNER_ROWS)
        body = rows[corners:-corners]
        limit = self._limit(_MAX_PERIMETER_WIDTH)
        colors: set[bytes] = set()
        for width in range(limit + 1):
            column = len(rows[0]) - 4 * (width + 1) if right else 4 * width
            color = body[0][column : column + 4]
            if any(row[column : column + 4] != color for row in body):
                break
            if width < limit:
                colors.add(color)
            else:
                colors.discard(color)
        return colors

    def _top_perimeter(self, rows: list[bytes]) -> set[int]:
        """Identify bounded uniform top rows connected to both side perimeters."""
        corners = self._limit(_TOP_CORNER_COLUMNS)
        limit = self._limit(_MAX_TOP_PERIMETER_ROWS)
        if (
            len(rows) <= 2 * self._limit(_EDGE_CORNER_ROWS)
            or len(rows[0]) <= 8 * corners
        ):
            return set()
        colors = self._edge_colors(rows, right=False) & self._edge_colors(
            rows, right=True
        )
        perimeter = set()
        for y, row in enumerate(rows[: limit + 1]):
            interior = row[corners * 4 : -corners * 4]
            color = interior[:4]
            if color in colors and interior == color * (len(interior) // 4):
                if y == limit:
                    return set()
                perimeter.add(y)
        return perimeter

    @staticmethod
    def _difference(old: list[bytes], new: list[bytes]) -> tuple[int, int]:
        before = memoryview(b"".join(old)).cast("I")
        after = memoryview(b"".join(new)).cast("I")
        return sum(a != b for a, b in zip(before, after, strict=True)), len(before)

    def _check_top(self, old: list[bytes], new: list[bytes], advance: int) -> None:
        """Keep the 5% guard, allowing only independently measured perimeter rows."""
        before, after = old[advance : advance + MIN_OVERLAP], new[:MIN_OVERLAP]
        mismatches, compared = self._difference(before, after)
        if mismatches <= compared * _PREFIX_MISMATCH_FRACTION:
            return
        perimeter = self._top_perimeter(old)
        if perimeter and perimeter == self._top_perimeter(new):
            mismatches, compared = self._difference(
                [row for y, row in enumerate(before) if y not in perimeter],
                [row for y, row in enumerate(after) if y not in perimeter],
            )
            logger.info("Scrolling top perimeter: rows=%s", sorted(perimeter))
        if mismatches > compared * _PREFIX_MISMATCH_FRACTION:
            logger.warning(
                "Scrolling top mismatch: changed_pixels=%s compared_pixels=%s "
                "limit_fraction=%s",
                mismatches,
                compared,
                _PREFIX_MISMATCH_FRACTION,
            )
            raise CaptureError("The top of the scroll panel is fixed or changing")

    def append(self, image: QImage, expected_advance: float) -> None:
        """Join at an interior seam; never discard a column or infer a chrome crop."""
        self._validate(image)
        if image.size() != self._previous.size():
            raise CaptureError("The scroll panel changed size")
        old, new = self._rows(self._previous), self._rows(image)
        advance, first, last = self._registration(old, new)
        logger.info(
            "Scrolling alignment: measured_advance=%s expected_advance=%.3f "
            "matching_band=(%s,%s)",
            advance,
            expected_advance,
            first,
            last,
        )
        if abs(advance - expected_advance) > max(
            2.0, expected_advance * _ROUNDING_FRACTION
        ):
            raise CaptureError("The panel's pixels disagree with its scroll position")
        # Reject broad fixed headers. This practical check cannot establish
        # pixels hidden by a tiny overlay at every position.
        self._check_top(old, new, advance)
        if image.width() * (self._height + advance) > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")
        # Keep earlier pixels above a verified seam and later pixels below it.
        # Later content replaces any earlier bottom chrome; final chrome stays.
        seam = max((first + last) // 2, self._tail_start - advance)
        if seam >= last:
            raise CaptureError("The panel has no consistent interior join")
        retained = advance + seam - self._tail_start
        self._frames[-1] = self._previous.copy(
            0, self._tail_start, image.width(), retained
        )
        self._frames.append(image.copy(0, seam, image.width(), image.height() - seam))
        self._height += advance
        self._tail_start = seam
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


class _HeaderFrames:
    """Separate verified Header pixels from the moving body without losing width."""

    def __init__(self, first: QImage, area: Rect, header: Rect | None) -> None:
        VerticalStitch._validate(first)
        self._first = first
        self.height, self._width = 0, 0
        if header is not None:
            if (
                header.left != area.left
                or header.top != area.top
                or header.width <= 0
                or header.right > area.right
                or header.height <= 0
                or header.bottom >= area.bottom
                or first.width() != round(area.width)
                or first.height() != round(area.height)
                or header.width != round(header.width)
                or header.height != round(header.height)
            ):
                raise CaptureError("The fixed header geometry is inconsistent")
            self.height, self._width = round(header.height), round(header.width)
        self.body_height = first.height() - self.height
        self._header = (
            first.copy(0, 0, self._width, self.height) if self.height else QImage()
        )

    def body(self, frame: QImage) -> QImage:
        """Compare actual Header pixels exactly; the adjacent scrollbar may move."""
        if frame.size() != self._first.size():
            raise CaptureError("The scroll panel changed size")
        if self.height and frame.copy(0, 0, self._width, self.height) != self._header:
            raise CaptureError("The list header changed during scrolling capture")
        return (
            frame.copy(0, self.height, frame.width(), self.body_height)
            if self.height
            else frame
        )

    def finish(self, body: QImage) -> QImage:
        """Keep the first full-width prefix once, including its scrollbar corner."""
        if not self.height:
            return body
        height = body.height() + self.height
        if body.width() * height > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")
        image = QImage(body.width(), height, QImage.Format.Format_RGB32)
        if image.isNull():
            raise CaptureError("Not enough memory for the scrolling image")
        painter = QPainter(image)
        painter.drawImage(0, 0, self._first.copy(0, 0, body.width(), self.height))
        painter.drawImage(0, self.height, body)
        painter.end()
        return image


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
                logger.warning(
                    "Scrolling settle rejected: stage=state-change restoring=%s "
                    "requested_percent=%r before=%s observed=%s",
                    restoring,
                    percent,
                    before,
                    current,
                )
                raise CaptureError("The scroll panel moved unexpectedly")
            if (
                not restoring
                and percent not in (0.0, END_PERCENT)
                and abs(current.vertical - before.vertical)
                > abs(percent - before.vertical) * 1.5
            ):
                logger.warning(
                    "Scrolling settle rejected: stage=overshoot restoring=%s "
                    "requested_percent=%r before=%s observed=%s",
                    restoring,
                    percent,
                    before,
                    current,
                )
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
        logger.warning(
            "Scrolling settle rejected: stage=unsettled restoring=%s "
            "requested_percent=%r before=%s observed=%s",
            restoring,
            percent,
            before,
            previous,
        )
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
        first = self._frame(position)
        frames = _HeaderFrames(first, self._target.area, self._target.header)
        stitch = VerticalStitch(frames.body(first), dpi=self._target.registration_dpi)
        while position.vertical < END_PERCENT:
            if self._frames >= MAX_FRAMES:
                raise CaptureError("Scrolling capture reached its page limit")
            # Half a viewport leaves ample overlap without wheel chaining.
            step = 50.0 * position.view_size / (100.0 - position.view_size)
            following = self._move(min(100.0, position.vertical + step))
            expected = (
                frames.body_height
                * (100.0 - position.view_size)
                / position.view_size
                * (following.vertical - position.vertical)
                / 100.0
            )
            logger.info(
                "Scrolling step: viewport_height=%s view_size=%.6f "
                "from_vertical=%.6f to_vertical=%.6f expected_advance=%.3f",
                frames.body_height,
                position.view_size,
                position.vertical,
                following.vertical,
                expected,
            )
            stitch.append(frames.body(self._frame(following)), expected)
            position = following
        self._check()
        image = frames.finish(stitch.finish())
        expected_height = frames.height + frames.body_height * 100.0 / initial.view_size
        if abs(image.height() - expected_height) > max(
            2.0, expected_height * _ROUNDING_FRACTION
        ):
            logger.warning(
                "Scrolling extent mismatch: measured_height=%s expected_height=%.3f "
                "viewport_height=%s view_size=%.6f",
                image.height(),
                expected_height,
                frames.body_height,
                initial.view_size,
            )
            raise CaptureError("The completed pixels disagree with the panel's extent")
        return image

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
