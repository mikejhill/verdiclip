"""Bounded vertical capture, independent of UIA and the ordinary frozen selector.

This is deliberately an opt-in feasibility slice until real-app qualification
passes. Only a complete, unambiguous sequence produces an image. It is never a
recapturable desktop rectangle and must not enter ordinary Repeat history.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from PySide6.QtGui import QColor, QImage, QPainter

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


def _extent_agrees(measured: float, expected: float) -> bool:
    return abs(measured - expected) <= max(2.0, expected * _ROUNDING_FRACTION)


@dataclass(frozen=True, slots=True)
class ScrollPosition:
    """UIA percentages and the visible fraction of the full vertical content."""

    horizontal: float
    vertical: float
    view_size: float


@dataclass(frozen=True, slots=True)
class ScrollBarState:
    """Verified native rail units; these are never image pixel coordinates."""

    rail: int
    parent: int
    root: int
    pid: int
    minimum: int
    maximum: int
    page: int
    position: int

    def percent(self, position: int) -> float:
        """Invert the current native range without rounding its absolute position."""
        last = self.maximum - max(self.page - 1, 0)
        if not (
            self.minimum < last <= self.maximum
            and self.page > 0
            and self.minimum <= position <= last
        ):
            raise CaptureError("The native scrollbar range is unsupported")
        return END_PERCENT * (position - self.minimum) / (last - self.minimum)

    def matches(self, position: ScrollPosition) -> bool:
        """Allow only one adjacent float from arithmetic operation ordering."""
        pairs = (
            (position.vertical, self.percent(self.position)),
            (
                position.view_size,
                END_PERCENT * self.page / (self.maximum - self.minimum),
            ),
        )
        return all(
            math.isfinite(observed)
            and observed
            in (
                computed,
                math.nextafter(computed, -math.inf),
                math.nextafter(computed, math.inf),
            )
            for observed, computed in pairs
        )

    def same_range(self, other: ScrollBarState) -> bool:
        """Keep the same retained native identity and content range."""
        return (
            self.rail,
            self.parent,
            self.root,
            self.pid,
            self.minimum,
            self.maximum,
        ) == (
            other.rail,
            other.parent,
            other.root,
            other.pid,
            other.minimum,
            other.maximum,
        )


class ListSteps(Protocol):
    """Optional standard-List evidence; raw elements stay inside the platform."""

    @property
    def body(self) -> Rect:
        """Return the verified entire nonrail moving body in desktop pixels."""

    def begin(self, position: ScrollPosition) -> None:
        """Retain two raw ListItems before a downward traversal write."""

    def finish(self, position: ScrollPosition, *, terminal: bool = False) -> int:
        """Require both retained items to report one positive integer movement."""

    def confirm_terminal(self, position: ScrollPosition) -> None:
        """Recheck the same native range at its actual maximum and eligibility."""

    def close(self) -> None:
        """Release retained raw items without moving the target."""


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

    def __init__(
        self,
        first: QImage,
        *,
        dpi: int | None = None,
        nonrail_width: int | None = None,
    ) -> None:
        self._validate(first)
        if dpi is not None and not BASE_REGISTRATION_DPI <= dpi <= MAX_REGISTRATION_DPI:
            raise CaptureError("The scrolling registration DPI is unsupported")
        if nonrail_width is not None and not (
            type(nonrail_width) is int and 0 < nonrail_width < first.width()
        ):
            raise CaptureError("The List body geometry is unsupported")
        self._nonrail_width = nonrail_width
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
    def _alignment(
        old: list[bytes], new: list[bytes], *, nonrail_width: int | None = None
    ) -> tuple[int, int, int] | None:
        """Find one displacement globally, using exact textured row bands."""
        ids: dict[bytes, int] = {}
        before = [ids.setdefault(row, len(ids)) for row in old]
        after = [ids.setdefault(row, len(ids)) for row in new]
        match = None
        for advance in range(1, len(before) - MIN_OVERLAP + 1):
            if nonrail_width is not None and any(
                left[: nonrail_width * 4] != right[: nonrail_width * 4]
                for left, right in zip(old[advance:], new, strict=False)
            ):
                continue
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

    def _edge_onset(
        self,
        old: list[bytes],
        new: list[bytes],
        *,
        right: bool,
        allow_unchanged: bool = False,
    ) -> int:
        """Skip one proven unchanged exterior column, never search inward."""
        corners = self._limit(_EDGE_CORNER_ROWS)
        if len(old) <= 2 * corners or len(old[0]) // 4 <= 1:
            return 0
        outer = len(old[0]) - 4 if right else 0
        adjacent = outer - 4 if right else 4
        color = old[0][outer : outer + 4]
        before, after = old[corners:-corners], new[corners:-corners]
        first = before[0][adjacent : adjacent + 4]
        second = after[0][adjacent : adjacent + 4]
        uniform = (
            all(row[outer : outer + 4] == color for row in (*old, *new))
            and color not in (first, second)
            and all(row[adjacent : adjacent + 4] == first for row in before)
            and all(row[adjacent : adjacent + 4] == second for row in after)
        )
        return int(uniform and (first != second or allow_unchanged))

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
        onset = self._edge_onset(old, new, right=right)
        for width in range(
            min(self._limit(_MAX_EDGE_WIDTH) + 1, len(old[0]) // 4 - onset)
        ):
            offset = onset + width
            column = len(old[0]) - 4 * (offset + 1) if right else 4 * offset
            first = before[0][column : column + 4]
            second = after[0][column : column + 4]
            if (
                first == second
                or any(row[column : column + 4] != first for row in before)
                or any(row[column : column + 4] != second for row in after)
            ):
                return width
        return 0

    def _registration(
        self, old: list[bytes], new: list[bytes]
    ) -> tuple[int, int, int, bool]:
        """Try all columns first; only no match permits a measured-edge retry."""
        match = self._alignment(old, new, nonrail_width=self._nonrail_width)
        if match is not None:
            return *match, True
        if self._nonrail_width is not None:
            raise CaptureError("The List's complete overlapping pixels do not match")
        left = self._edge_width(old, new, right=False)
        right = self._edge_width(old, new, right=True)
        if left or right:
            left_start = self._edge_onset(old, new, right=False) * 4
            right_end = len(old[0]) - self._edge_onset(old, new, right=True) * 4

            def retained(rows: list[bytes]) -> list[bytes]:
                return [
                    row[:left_start]
                    + row[left_start + left * 4 : right_end - right * 4]
                    + row[right_end:]
                    for row in rows
                ]

            match = self._alignment(retained(old), retained(new))
            if match is not None:
                logger.info(
                    "Scrolling registration edges: left_columns=%s right_columns=%s "
                    "registration_dpi=%s",
                    left,
                    right,
                    self._dpi,
                )
                return *match, False
        raise CaptureError("The panel's scrolling pixels cannot be joined reliably")

    def _edge_colors(self, rows: list[bytes], *, right: bool, onset: int) -> set[bytes]:
        """Find uniform side colors, excluding a color continuing past the cap."""
        corners = self._limit(_EDGE_CORNER_ROWS)
        body = rows[corners:-corners]
        limit = self._limit(_MAX_PERIMETER_WIDTH)
        colors: set[bytes] = set()
        for width in range(limit + 1):
            offset = onset + width
            column = len(rows[0]) - 4 * (offset + 1) if right else 4 * offset
            color = body[0][column : column + 4]
            if any(row[column : column + 4] != color for row in body):
                break
            if width < limit:
                colors.add(color)
            else:
                colors.discard(color)
        return colors

    def _top_perimeter(self, rows: list[bytes], onsets: tuple[int, int]) -> set[int]:
        """Identify bounded uniform top rows connected to both side perimeters."""
        corners = self._limit(_TOP_CORNER_COLUMNS)
        limit = self._limit(_MAX_TOP_PERIMETER_ROWS)
        if (
            len(rows) <= 2 * self._limit(_EDGE_CORNER_ROWS)
            or len(rows[0]) <= 8 * corners
        ):
            return set()
        colors = self._edge_colors(
            rows, right=False, onset=onsets[0]
        ) & self._edge_colors(rows, right=True, onset=onsets[1])
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

    def _check_top(
        self,
        old: list[bytes],
        new: list[bytes],
        advance: int,
        *,
        full_width: bool,
    ) -> None:
        """Keep the 5% guard, allowing only independently measured perimeter rows."""
        before, after = old[advance : advance + MIN_OVERLAP], new[:MIN_OVERLAP]
        mismatches, compared = self._difference(before, after)
        if mismatches <= compared * _PREFIX_MISMATCH_FRACTION:
            return
        # A stable outline needs no exclusion for registration. Only a unique
        # exact full-width match can prove its unchanged exterior for this check.
        onsets = (
            self._edge_onset(old, new, right=False, allow_unchanged=full_width),
            self._edge_onset(old, new, right=True, allow_unchanged=full_width),
        )
        perimeter = self._top_perimeter(old, onsets)
        if perimeter and perimeter == self._top_perimeter(new, onsets):
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

    def append(
        self,
        image: QImage,
        expected_advance: float | None,
        *,
        verify: Callable[[int], None] | None = None,
    ) -> None:
        """Join exact bands; a provisional terminal join requires later extent proof."""
        self._validate(image)
        if image.size() != self._previous.size():
            raise CaptureError("The scroll panel changed size")
        old, new = self._rows(self._previous), self._rows(image)
        if self._nonrail_width is not None and all(
            before[: self._nonrail_width * 4] == after[: self._nonrail_width * 4]
            for before, after in zip(old, new, strict=True)
        ):
            raise CaptureError("The List's rendered body did not move")
        advance, first, last, full_width = self._registration(old, new)
        logger.info(
            "Scrolling alignment: measured_advance=%s expected_advance=%s "
            "matching_band=(%s,%s)",
            advance,
            f"{expected_advance:.3f}"
            if expected_advance is not None
            else "list_item_geometry"
            if self._nonrail_width is not None
            else "terminal_extent",
            first,
            last,
        )
        if expected_advance is not None and not _extent_agrees(
            advance, expected_advance
        ):
            raise CaptureError("The panel's pixels disagree with its scroll position")
        # Reject broad fixed headers. This practical check cannot establish
        # pixels hidden by a tiny overlay at every position.
        self._check_top(old, new, advance, full_width=full_width)
        if image.width() * (self._height + advance) > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")
        # Keep earlier pixels above a verified seam and later pixels below it.
        # Later content replaces any earlier bottom chrome; final chrome stays.
        seam = max((first + last) // 2, self._tail_start - advance)
        if seam >= last:
            raise CaptureError("The panel has no consistent interior join")
        retained = advance + seam - self._tail_start
        if verify is not None:
            verify(advance)
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


def _footer_height(area: Rect, header: Rect | None, footer: Rect | None) -> int:
    """Accept only the horizontal rail paired with the visible Header/right rail."""
    if footer is None:
        return 0
    if (
        header is None
        or footer.left != area.left
        or footer.right != header.right
        or not 0 < footer.width < area.width
        or footer.bottom != area.bottom
        or footer.top <= header.bottom
        or footer.height <= 0
        or footer.height != round(footer.height)
    ):
        raise CaptureError("The fixed footer geometry is inconsistent")
    return round(footer.height)


class _HeaderFrames:
    """Separate verified Header pixels from the moving body without losing width."""

    def __init__(
        self, first: QImage, area: Rect, header: Rect | None, footer: Rect | None = None
    ) -> None:
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
        self.footer_height = _footer_height(area, header, footer)
        self.body_height = first.height() - self.height - self.footer_height
        self._footer = (
            first.copy(
                0,
                first.height() - self.footer_height,
                first.width(),
                self.footer_height,
            )
            if self.footer_height
            else QImage()
        )
        self._header = (
            first.copy(0, 0, self._width, self.height) if self.height else QImage()
        )

    def body(self, frame: QImage) -> QImage:
        """Compare actual Header pixels exactly; the adjacent scrollbar may move."""
        if frame.size() != self._first.size():
            raise CaptureError("The scroll panel changed size")
        if self.height and frame.copy(0, 0, self._width, self.height) != self._header:
            raise CaptureError("The list header changed during scrolling capture")
        if (
            self.footer_height
            and frame.copy(
                0,
                frame.height() - self.footer_height,
                frame.width(),
                self.footer_height,
            )
            != self._footer
        ):
            raise CaptureError("The list footer changed during scrolling capture")
        return (
            frame.copy(0, self.height, frame.width(), self.body_height)
            if self.height
            else frame
        )

    def finish(self, body: QImage) -> QImage:
        """Keep the first full-width prefix once, including its scrollbar corner."""
        if not self.height:
            return body
        height = body.height() + self.height + self.footer_height
        if body.width() * height > MAX_PIXELS:
            raise CaptureError("The scrolling image exceeds the size limit")
        image = QImage(body.width(), height, QImage.Format.Format_RGB32)
        if image.isNull():
            raise CaptureError("Not enough memory for the scrolling image")
        painter = QPainter(image)
        painter.drawImage(0, 0, self._first.copy(0, 0, body.width(), self.height))
        painter.drawImage(0, self.height, body)
        if self.footer_height:
            painter.drawImage(0, self.height + body.height(), self._footer)
        painter.end()
        return image

    def extent(self, view_size: float) -> float:
        """Use the original fixed prefix and full-width body for provider extent."""
        return (
            self.height
            + self.footer_height
            + self.body_height * END_PERCENT / view_size
        )


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
        self._terminal: ScrollPosition | None = None
        self._native: ScrollBarState | None = None
        self._anchor: QImage | None = None
        self._list_steps: ListSteps | None = None
        self._list_body: Rect | None = None

    def _poll(self, *, restoring: bool = False) -> None:
        """Observe cancellation/deadline without repeating target validation."""
        if not restoring and self._cancelled():
            raise CaptureError("Scrolling capture cancelled")
        if self._clock() >= self._deadline:
            raise CaptureError("Scrolling capture reached its time limit")

    def _check(self, *, restoring: bool = False) -> None:
        self._poll(restoring=restoring)
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

    def _native_state(self) -> ScrollBarState | None:
        getter = getattr(self._target, "scrollbar_state", None)
        state = getter() if getter is not None else None
        if state is not None and not isinstance(state, ScrollBarState):
            raise CaptureError("The native scrollbar observation is unsupported")
        return state

    def _select_list_steps(self, initial: ScrollPosition) -> None:
        """Choose once, before traversal; missing support retains the old path."""
        factory = getattr(self._target, "list_steps", None)
        self._list_steps = (
            factory(initial, self._check, poll=self._poll)
            if factory is not None
            else None
        )
        if self._list_steps is not None:
            self._list_body = self._list_steps.body
            self._list_width()
            if self._native is None or initial.horizontal != 0.0:
                raise CaptureError("The List's original native state is unsupported")

    def _list_width(self) -> int | None:
        """Exclude only the proven actual rail, never arbitrary exterior content."""
        steps = self._list_steps
        if steps is None:
            return None
        area, header = self._target.area, self._target.header
        footer = _footer_height(area, header, getattr(self._target, "footer", None))
        if (
            header is None
            or steps.body != self._list_body
            or steps.body
            != Rect(
                area.left,
                header.bottom,
                header.width,
                area.height - header.height - footer,
            )
            or getattr(self._target, "vertical_scrollbar", None)
            != Rect(
                header.right, area.top, area.width - header.width, area.height - footer
            )
            or not 0 < header.width < area.width
            or header.width != round(header.width)
        ):
            raise CaptureError("The List's nonrail geometry changed or is unsupported")
        return round(header.width)

    def _confirm_list_start(self, position: ScrollPosition) -> None:
        """Keep the top seek outside item lineage but prove its native state first."""
        native, original = self._native_state(), self._native
        if (
            native is None
            or original is None
            or not native.same_range(original)
            or native.page != original.page
            or native.position != native.minimum
            or position.vertical != 0.0
            or not native.matches(position)
            or position.horizontal != 0.0
            or self._target.position() != position
        ):
            raise CaptureError("The List changed native state while seeking the top")
        self._check()
        self._expected = position

    def _begin_list_step(self, position: ScrollPosition) -> None:
        """A selected capability failure also revokes previous restore ownership."""
        if self._list_steps is not None:
            self._check()
            try:
                self._list_steps.begin(position)
            except (CaptureError, OSError):
                self._expected = None
                raise

    def _verify_list_step(self, position: ScrollPosition, advance: int) -> None:
        """Confirm item evidence only after global pixel uniqueness, before append."""
        steps = self._list_steps
        if steps is None:
            raise CaptureError("The selected List capability is unavailable")
        delta = steps.finish(position, terminal=position.vertical == END_PERCENT)
        if type(delta) is not int or delta != advance or delta <= 0:
            raise CaptureError("The List items disagree with its scrolling pixels")
        self._list_width()
        self._check()
        if self._target.position() != position:
            raise CaptureError("The List moved during its pixel verification")
        self._check()

    def _anchor_sample(
        self, position: ScrollPosition, native: ScrollBarState, *, restoring: bool
    ) -> QImage:
        self._check(restoring=restoring)
        if self._target.position() != position or self._native_state() != native:
            raise CaptureError("The native scrollbar moved during observation")
        image = self._target.frame()
        header, area = self._target.header, self._target.area
        if (
            header is None
            or image.isNull()
            or (image.width(), image.height()) != (area.width, area.height)
            or not 0 < header.width < area.width
            or header.width != round(header.width)
            or image.width() * image.height() > MAX_PIXELS
        ):
            raise CaptureError("The native scrollbar has unsupported viewport pixels")
        if self._target.position() != position or self._native_state() != native:
            raise CaptureError("The native scrollbar moved during capture")
        self._check(restoring=restoring)
        footer_height = _footer_height(
            area, header, getattr(self._target, "footer", None)
        )
        rail = getattr(self._target, "vertical_scrollbar", None)
        if rail != Rect(
            header.right,
            area.top,
            area.right - header.right,
            area.height - footer_height,
        ):
            raise CaptureError("The native scrollbar rail geometry is inconsistent")
        # Exclude only the independently retained vertical rail. The horizontal
        # scrollbar and its bottom-right corner remain exact original pixels.
        image = image.copy()
        painter = QPainter(image)
        painter.fillRect(
            round(header.width),
            0,
            round(area.width - header.width),
            round(area.height - footer_height),
            QColor(0, 0, 0),
        )
        painter.end()
        return image

    def _save_anchor(self, initial: ScrollPosition) -> None:
        native = self._native_state()
        if native is None:
            return
        if not native.matches(initial):
            raise CaptureError("The native scrollbar mapping changed")
        previous = None
        for _ in range(8):
            current = self._anchor_sample(initial, native, restoring=False)
            if current == previous:
                self._native, self._anchor = native, current
                return
            previous = current
            self._pause(0.08)
        raise CaptureError("The original viewport did not settle")

    def _restore_native(self, initial: ScrollPosition, current: ScrollPosition) -> None:
        original, anchor = self._native, self._anchor
        native = self._native_state()
        if (
            original is None
            or anchor is None
            or native is None
            or not native.same_range(original)
            or not native.matches(current)
            or native.page == original.page
            or current != self._terminal
        ):
            raise CaptureError("The normalized scrollbar restoration is unsupported")
        percent = native.percent(original.position)
        self._check(restoring=True)
        if self._target.position() != current or self._native_state() != native:
            raise CaptureError("The terminal scrollbar changed before restoration")
        self._check(restoring=True)
        self._expected = None
        self._target.move(percent)
        previous = None
        for _ in range(8):
            self._pause(0.08)
            self._check(restoring=True)
            observed = self._target.position()
            state = self._native_state()
            if (
                state is None
                or not state.same_range(original)
                or state.page != native.page
                or observed.horizontal != initial.horizontal
                or observed.view_size != current.view_size
                or not state.matches(observed)
            ):
                raise CaptureError(
                    "The normalized scrollbar changed during restoration"
                )
            pixels = self._anchor_sample(observed, state, restoring=True)
            if state.position == original.position and pixels == anchor:
                if previous == (observed, state, pixels):
                    self._check(restoring=True)
                    self._expected = observed
                    return
                previous = (observed, state, pixels)
            else:
                previous = None
        raise CaptureError("The native scrollbar did not restore exact original pixels")

    def _terminal_change(
        self, before: ScrollPosition, current: ScrollPosition, percent: float
    ) -> bool:
        """Admit only a bounded Header endpoint candidate, not confirmed ownership."""
        header = self._target.header
        if (
            header is None
            or percent != END_PERCENT
            or current.vertical != END_PERCENT
            or before.vertical >= END_PERCENT
            or not math.isfinite(current.view_size)
            or not 0.0 < current.view_size < END_PERCENT
        ):
            return False
        if self._list_steps is not None:
            # Admission is provisional. Continuous pixels/items, native maximum,
            # and BOTH original/terminal actual-image extent checks remain required.
            return True
        fixed = header.height + _footer_height(
            self._target.area, header, getattr(self._target, "footer", None)
        )
        body = self._target.area.height - fixed
        original = fixed + body * END_PERCENT / before.view_size
        terminal = fixed + body * END_PERCENT / current.view_size
        return _extent_agrees(terminal, original)

    def _settle_state(
        self,
        before: ScrollPosition,
        current: ScrollPosition,
        percent: float,
        restoring: ScrollPosition | None,
    ) -> bool:
        if restoring is not None and before.view_size != restoring.view_size:
            return current in (before, restoring)
        if current.horizontal != before.horizontal:
            return False
        if current.view_size == before.view_size:
            return True
        return restoring is None and self._terminal_change(before, current, percent)

    def _settle(
        self,
        before: ScrollPosition,
        percent: float,
        *,
        restoring: ScrollPosition | None = None,
    ) -> ScrollPosition:
        """Observe one write; unchanged reads never acknowledge pending movement."""
        previous = None
        for _ in range(8):
            self._pause(0.08)
            self._check(restoring=restoring is not None)
            current = self._target.position()
            self._check(restoring=restoring is not None)
            if not self._settle_state(before, current, percent, restoring):
                logger.warning(
                    "Scrolling settle rejected: stage=state-change restoring=%s "
                    "requested_percent=%r before=%s observed=%s",
                    restoring is not None,
                    percent,
                    before,
                    current,
                )
                raise CaptureError("The scroll panel moved unexpectedly")
            if (
                restoring is None
                and percent not in (0.0, END_PERCENT)
                and abs(current.vertical - before.vertical)
                > abs(percent - before.vertical) * 1.5
            ):
                logger.warning(
                    "Scrolling settle rejected: stage=overshoot restoring=%s "
                    "requested_percent=%r before=%s observed=%s",
                    restoring is not None,
                    percent,
                    before,
                    current,
                )
                raise CaptureError("The scroll panel moved unexpectedly")
            reached = (
                current == restoring
                if restoring is not None
                else (
                    current.vertical == percent
                    if percent in (0.0, END_PERCENT)
                    else (current.vertical - before.vertical)
                    * (percent - before.vertical)
                    > 0.0
                )
            )
            if reached and current == previous:
                return current
            previous = current
        logger.warning(
            "Scrolling settle rejected: stage=unsettled restoring=%s "
            "requested_percent=%r before=%s observed=%s",
            restoring is not None,
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
        observed = self._settle(before, percent)
        if self._list_steps is None and observed.view_size == before.view_size:
            self._expected = observed
        self._check()
        return observed

    def _sequence(self, initial: ScrollPosition) -> QImage:
        position = self._move(0.0)
        first = self._frame(position)
        frames = _HeaderFrames(
            first,
            self._target.area,
            self._target.header,
            getattr(self._target, "footer", None),
        )
        stitch = VerticalStitch(
            frames.body(first),
            dpi=self._target.registration_dpi,
            nonrail_width=self._list_width(),
        )
        self._check()
        if self._list_steps is not None:
            self._confirm_list_start(position)
        while position.vertical < END_PERCENT:
            if self._frames >= MAX_FRAMES:
                raise CaptureError("Scrolling capture reached its page limit")
            # Half a viewport leaves ample overlap without wheel chaining.
            step = 50.0 * position.view_size / (100.0 - position.view_size)
            self._begin_list_step(position)
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
            stitch.append(
                frames.body(self._frame(following)),
                expected
                if self._list_steps is None
                and following.view_size == position.view_size
                else None,
                verify=partial(self._verify_list_step, following)
                if self._list_steps is not None
                else None,
            )
            if self._list_steps is not None and following.vertical != END_PERCENT:
                self._expected = following
            position = following
        self._check()
        image = frames.finish(stitch.finish())
        self._validate_extent(image, frames, initial.view_size)
        if position.view_size != initial.view_size or self._list_steps is not None:
            self._validate_extent(image, frames, position.view_size)
            if self._list_steps is not None:
                self._list_steps.confirm_terminal(position)
                self._list_width()
            self._check()
            if self._target.position() != position:
                raise CaptureError(
                    "The scroll panel changed before terminal confirmation"
                )
            self._check()
            self._expected = position
            if position.view_size != initial.view_size:
                self._terminal = position
        return image

    @staticmethod
    def _validate_extent(
        image: QImage, frames: _HeaderFrames, view_size: float
    ) -> None:
        expected_height = frames.extent(view_size)
        if not _extent_agrees(image.height(), expected_height):
            logger.warning(
                "Scrolling extent mismatch: measured_height=%s expected_height=%.3f "
                "viewport_height=%s view_size=%.6f",
                image.height(),
                expected_height,
                frames.body_height,
                view_size,
            )
            raise CaptureError("The completed pixels disagree with the panel's extent")

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
                or (
                    self._expected.view_size != initial.view_size
                    and self._expected != self._terminal
                )
            ):
                return "not restored: scroll position changed unexpectedly"
            if self._terminal is not None and self._native is not None:
                self._restore_native(initial, current)
            elif self._expected != initial:
                before = self._expected
                self._expected = None
                self._target.move(initial.vertical)
                self._expected = self._settle(
                    before, initial.vertical, restoring=initial
                )
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
            self._save_anchor(initial)
            self._select_list_steps(initial)
            image = self._sequence(initial)
        except (CaptureError, OSError) as error:
            message = str(error)
        finally:
            if self._list_steps is not None:
                try:
                    self._list_steps.close()
                except (CaptureError, OSError) as error:
                    image, message = None, str(error)
                    self._expected = self._terminal = None
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
