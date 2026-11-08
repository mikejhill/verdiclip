"""Small UIA ScrollPattern adapter, used only in the disposable capture helper.

No wheel messages, input synthesis, application injection, or foreground forcing.
ABI reference: Microsoft WinSDK UIAutomationClient.h. IUIAutomation slots:
ElementFromPoint=7, RawViewWalker=16; element: GetCurrentPatternAs=14,
IsOffscreen=38, BoundingRectangle=43; walker: GetParentElement=3;
ScrollPattern: SetScrollPercent=4, current getters=5..10.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import logging
import math
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Self

from PySide6.QtGui import QImage

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.scrolling import (
    BASE_REGISTRATION_DPI,
    END_PERCENT,
    MAX_REGISTRATION_DPI,
    ScrollBarState,
    ScrollPosition,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import Win32WindowApi, WindowInfo, WindowLocator

logger = logging.getLogger(__name__)

_SCROLL_IID = "88f4d42a-e881-459d-a77c-73bbbb7e02dc"
_MAX_ANCESTORS = 32
_DISCOVERY_OBSERVATIONS = 3
_MIN_SIZE = 64
_WAIT_TIMEOUT = 258
_MAX_WINDOWS = 512
_MAX_HEADER_CHILDREN = 256
_LIST_CONTROL = 50008
_LISTITEM_CONTROL = 50007
_MAX_ITEM_CHILDREN = 64
_MAX_ITEM_ANCESTORS = 8
_ITEM_SECONDS = 1.0
_ITEM_PAIR = 2
_ELEMENT_IID = "d22108aa-8ac5-49a5-837b-37bbb3d7591e"
_SCROLLBAR_CONTROL = 50014
_HEADER_CONTROL = 50034
_PER_MONITOR_AWARE = 2


class _NoScrollTargetError(CaptureError):
    """Ordinary unsupported discovery, distinct from a broken provider."""


class _Guid(c.Structure):
    _fields_ = [("bytes", c.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _LastInput(c.Structure):
    _fields_ = [("size", w.UINT), ("tick", w.DWORD)]


class _ScrollInfo(c.Structure):
    _fields_ = [
        ("size", w.UINT),
        ("mask", w.UINT),
        ("minimum", c.c_int),
        ("maximum", c.c_int),
        ("page", w.UINT),
        ("position", c.c_int),
        ("track", c.c_int),
    ]


class _MonitorInfo(c.Structure):
    _fields_ = [
        ("size", w.DWORD),
        ("monitor", w.RECT),
        ("work", w.RECT),
        ("flags", w.DWORD),
    ]


@dataclass(frozen=True, slots=True)
class _ScrollDisplay:
    """Read-only geometry evidence retained for one exact viewport and root."""

    monitor: int
    bounds: Rect
    window_dpi: int
    window_awareness: int
    capture_awareness: int


class _Interface:
    """One retained COM reference with explicit SDK call signatures."""

    def __init__(self, pointer: c.c_void_p, owners: ExitStack) -> None:
        if pointer.value is None:
            raise CaptureError("UI Automation returned no target")
        self._pointer = pointer
        owners.callback(self._release)

    def call(
        self, slot: int, arguments: tuple[type[c._CData], ...], *values: object
    ) -> None:
        table = c.cast(self._pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        function = c.WINFUNCTYPE(c.HRESULT, c.c_void_p, *arguments)(table[slot])
        function(self._pointer, *values)

    def _release(self) -> None:
        table = c.cast(self._pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        c.WINFUNCTYPE(w.ULONG, c.c_void_p)(table[2])(self._pointer)

    def pointer_result(
        self, slot: int, owners: ExitStack, *prefix: tuple[type[c._CData], object]
    ) -> _Interface:
        result = c.c_void_p()
        self.call(
            slot,
            (*[kind for kind, _ in prefix], c.POINTER(c.c_void_p)),
            *[value for _, value in prefix],
            c.byref(result),
        )
        return _Interface(result, owners)

    def same_element(self, first: _Interface, second: _Interface) -> bool:
        """Use UIA identity, not pointer addresses or coincident rectangles."""
        same = w.BOOL()
        self.call(
            3,
            (c.c_void_p, c.c_void_p, c.POINTER(w.BOOL)),
            first._pointer,
            second._pointer,
            c.byref(same),
        )
        return bool(same.value)

    def scroll_pattern(self, owners: ExitStack) -> _Interface | None:
        """A successful null pattern is unsupported, not a discovery failure."""
        result = c.c_void_p()
        self.call(
            14,
            (c.c_int, c.POINTER(_Guid), c.POINTER(c.c_void_p)),
            10004,
            c.byref(_Guid.parse(_SCROLL_IID)),
            c.byref(result),
        )
        return None if result.value is None else _Interface(result, owners)

    def native_handle(self) -> int | None:
        """Read the HWND associated with this exact UIA element, if any."""
        result = w.HWND()
        self.call(36, (c.POINTER(w.HWND),), c.byref(result))
        return result.value

    def control_type(self) -> int:
        """Read standard metadata, activating lazy application accessibility."""
        result = c.c_int()
        self.call(21, (c.POINTER(c.c_int),), c.byref(result))
        return result.value

    def parent(self, child: _Interface, owners: ExitStack) -> _Interface | None:
        """Return the raw parent; a null parent means the root was reached."""
        return self.relative(child, owners, 3)

    def relative(
        self, child: _Interface, owners: ExitStack, slot: int
    ) -> _Interface | None:
        """Read nullable parent/first-child/next-sibling results (slots 3/4/6)."""
        result = c.c_void_p()
        self.call(
            slot,
            (c.c_void_p, c.POINTER(c.c_void_p)),
            child._pointer,
            c.byref(result),
        )
        return None if result.value is None else _Interface(result, owners)

    def offscreen(self) -> bool:
        """Read the provider's current visibility without reading any content."""
        result = w.BOOL()
        self.call(38, (c.POINTER(w.BOOL),), c.byref(result))
        return bool(result.value)

    def number(self, slot: int) -> float:
        result = c.c_double()
        self.call(slot, (c.POINTER(c.c_double),), c.byref(result))
        if not math.isfinite(result.value):
            raise CaptureError("UI Automation returned an invalid scroll position")
        return result.value

    def bounds(self) -> Rect:
        native = w.RECT()
        self.call(43, (c.POINTER(w.RECT),), c.byref(native))
        return Rect.from_points(
            Point(native.left, native.top), Point(native.right, native.bottom)
        )


class _ScrollHeader:
    """Retain the proven standard List Header and optional adjacent scrollbar.

    Only direct standard roles establish this layout; no application profile,
    pixel inference, or discarded width. Public Header bounds identify the fixed
    prefix, while a separate ScrollBar must account for any remaining width.
    A bottom ScrollBar and its exact adjacent V rail can also prove a footer.
    UIA bounding rectangles enclose the whole control, not just visible pixels:
    https://learn.microsoft.com/windows/win32/winauto/uiauto-automation-element-propids
    """

    def __init__(
        self,
        header: _Interface,
        scrollbar: _Interface | None,
        area: Rect,
        *,
        footer: _Interface | None = None,
    ) -> None:
        self.scrollbar: _Interface | None = None
        self.vertical_scrollbar: Rect | None = None
        self.footer: Rect | None = None
        self.bounds = header.bounds()
        self._parts = [(header, _HEADER_CONTROL, self.bounds)]
        if footer is not None:
            self._footer_layout(scrollbar, footer, area)
            return
        if (
            self.bounds.left != area.left
            or self.bounds.top != area.top
            or self.bounds.width <= 0
            or self.bounds.right > area.right
            or self.bounds.height <= 0
            or area.bottom - self.bounds.bottom < _MIN_SIZE
        ):
            self._log_rejected_geometry(area, scrollbar)
            raise CaptureError("The list header has unsupported geometry")
        if self.bounds.right != area.right:
            expected = Rect(
                self.bounds.right, area.top, area.right - self.bounds.right, area.height
            )
            if scrollbar is None or scrollbar.bounds() != expected:
                raise CaptureError(
                    "The list header does not account for the full width"
                )
            self._parts.append((scrollbar, _SCROLLBAR_CONTROL, expected))
            self.scrollbar = scrollbar
            self.vertical_scrollbar = expected

    def _footer_layout(
        self, scrollbar: _Interface | None, footer: _Interface, area: Rect
    ) -> None:
        """Prove the two edge rails before clipping a horizontally extended Header."""
        bottom = footer.bounds()
        rail = None if scrollbar is None else scrollbar.bounds()
        if (
            scrollbar is None
            or rail is None
            or not 0 < rail.width < area.width
            or not 0 < bottom.height < area.height
            or bottom.left != area.left
            or bottom.bottom != area.bottom
            or rail
            != Rect(
                bottom.right,
                area.top,
                area.right - bottom.right,
                bottom.top - area.top,
            )
        ):
            raise CaptureError("The list scrollbars do not partition the viewport")
        content = Rect(area.left, area.top, bottom.width, rail.height)
        visible = Rect(area.left, area.top, content.width, self.bounds.height)
        if (
            self.bounds.left != area.left
            or self.bounds.top != area.top
            or self.bounds.height <= 0
            or content.width < _MIN_SIZE
            or bottom.top - self.bounds.bottom < _MIN_SIZE
            or self.bounds.intersected(content) != visible
        ):
            logger.warning(
                "Scrolling footer Header geometry rejected: captured=%s header=%s "
                "vertical_scrollbar=%s footer=%s",
                area,
                self.bounds,
                rail,
                bottom,
            )
            raise CaptureError("The list header has unsupported geometry")
        # Keep the raw Header in _parts: clipping never conceals later changes.
        self.bounds, self.footer, self.vertical_scrollbar = visible, bottom, rail
        self.scrollbar = scrollbar
        self._parts.extend(
            [
                (scrollbar, _SCROLLBAR_CONTROL, rail),
                (footer, _SCROLLBAR_CONTROL, bottom),
            ]
        )

    @staticmethod
    def _part(kind: int, rail: Rect | None, area: Rect) -> str | None:
        """Identify only standard roles on exact viewport edges, not inferred chrome."""
        if kind == _HEADER_CONTROL:
            return "header"
        if rail is None:
            return None
        if rail.height == area.height or (
            rail.top == area.top and rail.right == area.right
        ):
            return "vertical"
        if rail.left == area.left and rail.bottom == area.bottom:
            return "footer"
        return None

    def _log_rejected_geometry(self, area: Rect, scrollbar: _Interface | None) -> None:
        """Record bounded numeric evidence without replacing the original rejection."""
        rail, rail_read_failed = None, False
        try:
            if scrollbar is not None:
                rail = scrollbar.bounds()
        except (CaptureError, OSError):
            rail_read_failed = True
        failed = [
            name
            for name, invalid in (
                ("left_mismatch", self.bounds.left != area.left),
                ("top_mismatch", self.bounds.top != area.top),
                ("nonpositive_width", self.bounds.width <= 0),
                ("right_overflow", self.bounds.right > area.right),
                ("nonpositive_height", self.bounds.height <= 0),
                ("short_body", area.bottom - self.bounds.bottom < _MIN_SIZE),
            )
            if invalid
        ]
        logger.warning(
            "Scrolling Header geometry rejected: captured=%s header_role=%s "
            "header=%s scrollbar_role=%s scrollbar=%s scrollbar_read_failed=%s "
            "failed=%s",
            area,
            _HEADER_CONTROL,
            self.bounds,
            _SCROLLBAR_CONTROL if scrollbar is not None else None,
            rail,
            rail_read_failed,
            failed,
        )

    @classmethod
    def discover(
        cls, element: _Interface, walker: _Interface, owners: ExitStack, area: Rect
    ) -> _ScrollHeader | None:
        """Inspect only a bounded shallow List view; other targets are unchanged."""
        if element.control_type() != _LIST_CONTROL:
            return None
        parts: dict[str, _Interface] = {}
        ambiguous = False
        child = walker.relative(element, owners, 4)
        for _ in range(_MAX_HEADER_CHILDREN):
            if child is None:
                break
            kind = child.control_type()
            rail = None
            if kind == _SCROLLBAR_CONTROL:
                rail = child.bounds()
                logger.info(
                    "Scrolling direct ScrollBar: role=%s bounds=%s captured=%s "
                    "height_matches=%s",
                    kind,
                    rail,
                    area,
                    rail.height == area.height,
                )
            part = cls._part(kind, rail, area)
            if part is not None and not child.offscreen():
                if part in parts:
                    ambiguous = True
                parts[part] = child
            child = walker.relative(child, owners, 6)
        header = parts.get("header")
        if child is not None:
            raise CaptureError("The list header discovery exceeded its limit")
        if header is None:
            return None
        if ambiguous:
            raise CaptureError("The list has ambiguous header geometry")
        return cls(header, parts.get("vertical"), area, footer=parts.get("footer"))

    def check(
        self, root: _Interface, automation: _Interface, walker: _Interface
    ) -> None:
        """Recheck retained role geometry and parent identity, never rescan rows."""
        with ExitStack() as owners:
            for element, kind, bounds in self._parts:
                parent = walker.parent(element, owners)
                if (
                    parent is None
                    or not automation.same_element(parent, root)
                    or element.control_type() != kind
                    or element.bounds() != bounds
                    or element.offscreen()
                ):
                    raise CaptureError("The list header or scrollbar changed")


@dataclass(frozen=True, slots=True)
class _ListItem:
    element: _Interface
    bounds: Rect


class _ListSteps:
    """Read two retained raw ListItems, never converting native units to pixels.

    This capability is selected before traversal. Only an exact retained List
    layout and native rail can use it; losing evidence never selects a fallback.
    Temporary scans and hits are bounded, and only two QI references survive a
    begin call. The transaction owns pixels, uniqueness and restoration rights.
    """

    def __init__(
        self,
        target: UiaScrollTarget,
        initial: ScrollPosition,
        native: ScrollBarState,
        check: Callable[[], None],
        poll: Callable[[], None],
    ) -> None:
        self._target, self._check, self._poll = target, check, poll
        self._header = target._header
        self._horizontal, self._native = initial.horizontal, native
        header = target.header
        if header is None:
            raise CaptureError("The list step Header is unavailable")
        footer = target.footer
        bottom = target.area.bottom if footer is None else footer.top
        self.body = Rect(
            header.left, header.bottom, header.width, bottom - header.bottom
        )
        self._right_clip = footer is not None and initial.horizontal == 0.0
        self._retained = ExitStack()
        self._items: list[_ListItem] = []
        self._before: tuple[ScrollPosition, ScrollBarState] | None = None
        self._terminal: tuple[ScrollPosition, ScrollBarState] | None = None
        self._deadline = 0.0
        target._owners.callback(self.close)

    def close(self) -> None:
        """Release an abandoned pair before the target's COM apartment closes."""
        try:
            self._retained.close()
        finally:
            self._items.clear()
            self._before = None

    def _guard(self) -> None:
        self._poll()
        self._target._check_window()
        self._poll()
        if time.monotonic() >= self._deadline:
            raise CaptureError("The list item observation exceeded its time limit")
        if self._target._header is not self._header:
            raise CaptureError("The retained list layout changed")

    def _boundary(self) -> None:
        """Validate the whole target before/after provisional read-only evidence."""
        self._check()
        self._guard()

    def _state(
        self, position: ScrollPosition, *, terminal: bool = False
    ) -> ScrollBarState:
        self._guard()
        target = self._target
        if (
            target._element.control_type() != _LIST_CONTROL
            or position.horizontal != self._horizontal
            or not math.isfinite(position.view_size)
            or not 0.0 < position.view_size < END_PERCENT
            or target.position() != position
        ):
            raise CaptureError("The list step position or identity changed")
        native = target.scrollbar_state()
        self._guard()
        if (
            native is None
            or not native.same_range(self._native)
            or not native.matches(position)
            or target.position() != position
            or (
                terminal
                and (
                    position.vertical != END_PERCENT
                    or native.position != native.maximum - max(native.page - 1, 0)
                )
            )
        ):
            raise CaptureError("The list step native state changed")
        self._guard()
        return native

    def _visible(self, raw: Rect) -> Rect | None:
        """Allow only right overflow; raw coordinates remain displacement authority."""
        visible = raw.intersected(self.body)
        if raw.is_empty or visible.is_empty:
            return None
        if visible != raw and not (
            self._right_clip
            and self.body.left <= raw.left < self.body.right < raw.right
            and self.body.top <= raw.top < raw.bottom <= self.body.bottom
        ):
            return None
        x, y = round(visible.center.x), round(visible.center.y)
        if not visible.left < x < visible.right or not visible.top < y < visible.bottom:
            return None
        return visible

    def _raw(self, element: _Interface) -> Rect:
        self._guard()
        raw = element.bounds()
        with ExitStack() as owners:
            parent = self._target._walker.parent(element, owners)
            if (
                element.control_type() != _LISTITEM_CONTROL
                or element.offscreen()
                or self._visible(raw) is None
                or parent is None
                or not self._target._automation.same_element(
                    parent, self._target._element
                )
            ):
                raise CaptureError(
                    "The retained list item is no longer visible or direct"
                )
        self._guard()
        return raw

    def _confirm(self, element: _Interface, raw: Rect) -> None:
        self._guard()
        visible = self._visible(raw)
        if visible is None:
            raise CaptureError("The list item has no supported hit area")
        target = self._target
        with ExitStack() as owners:
            current = target._automation.pointer_result(
                7,
                owners,
                (w.POINT, w.POINT(round(visible.center.x), round(visible.center.y))),
            )
            for _ in range(_MAX_ITEM_ANCESTORS):
                self._guard()
                if current.control_type() == _LISTITEM_CONTROL:
                    if (
                        self._raw(current) != raw
                        or not target._automation.same_element(element, current)
                        or self._raw(element) != raw
                    ):
                        raise CaptureError(
                            "The list item hit identity or raw shape changed"
                        )
                    self._guard()
                    return
                parent = target._walker.parent(current, owners)
                if parent is None:
                    break
                current = parent
        raise CaptureError("The retained list item cannot be hit-tested")

    def _seed(self) -> None:
        target = self._target
        with ExitStack() as temporary, ExitStack() as retained:
            candidates: list[_ListItem] = []
            child = target._walker.relative(target._element, temporary, 4)
            for _ in range(_MAX_ITEM_CHILDREN):
                self._guard()
                if child is None:
                    break
                if child.control_type() == _LISTITEM_CONTROL and not child.offscreen():
                    raw = child.bounds()
                    if self._visible(raw) is not None:
                        candidates.append(_ListItem(child, raw))
                child = target._walker.relative(child, temporary, 6)
            self._guard()
            if child is not None or len(candidates) < _ITEM_PAIR:
                raise CaptureError("The list step cannot retain a bounded item pair")
            first, last = sorted(candidates, key=lambda item: item.bounds.top)[
                -_ITEM_PAIR:
            ]
            if first.bounds.bottom > last.bounds.top or target._automation.same_element(
                first.element, last.element
            ):
                raise CaptureError("The list step item pair is ambiguous")
            items = []
            for item in (first, last):
                element = item.element.pointer_result(
                    0, retained, (c.POINTER(_Guid), c.byref(_Guid.parse(_ELEMENT_IID)))
                )
                if (
                    not target._automation.same_element(item.element, element)
                    or self._raw(element) != item.bounds
                ):
                    raise CaptureError("The list item changed while retaining it")
                self._confirm(element, item.bounds)
                items.append(_ListItem(element, item.bounds))
            self._guard()
            self._retained, self._items = retained.pop_all(), items

    def begin(self, position: ScrollPosition) -> None:
        """Retain two currently visible identities before one downward write."""
        if self._items or self._before is not None:
            raise CaptureError("The previous list step is still outstanding")
        self._deadline = time.monotonic() + _ITEM_SECONDS
        try:
            self._boundary()
            before = self._state(position)
            self._seed()
            if self._state(position) != before:
                raise CaptureError("The native list moved while retaining items")
            self._boundary()
            self._before = position, before
        finally:
            if self._before is None:
                self.close()

    def finish(self, position: ScrollPosition, *, terminal: bool = False) -> int:
        """Prove agreeing integral raw displacement, always releasing this pair."""
        self._deadline = time.monotonic() + _ITEM_SECONDS
        try:
            self._boundary()
            if (
                self._before is None
                or len(self._items) != _ITEM_PAIR
                or position.vertical <= self._before[0].vertical
            ):
                raise CaptureError("The list step has no forward retained pair")
            native = self._state(position, terminal=terminal)
            if native.position <= self._before[1].position:
                raise CaptureError("The native list did not advance")
            deltas = []
            for item in self._items:
                raw = self._raw(item.element)
                self._confirm(item.element, raw)
                if (raw.x, raw.width, raw.height) != (
                    item.bounds.x,
                    item.bounds.width,
                    item.bounds.height,
                ):
                    raise CaptureError("The list item's raw shape changed")
                deltas.append(item.bounds.y - raw.y)
            delta = deltas[0]
            if (
                not math.isfinite(delta)
                or delta <= 0
                or delta != round(delta)
                or deltas != [delta, delta]
            ):
                raise CaptureError("The list items disagree about pixel displacement")
            if self._state(position, terminal=terminal) != native:
                raise CaptureError("The native list moved during item evidence")
            if self._target._automation.same_element(
                self._items[0].element, self._items[1].element
            ):
                raise CaptureError("The retained list item identities became ambiguous")
        finally:
            self.close()
        self._boundary()
        if terminal:
            self._terminal = position, native
        return int(delta)

    def confirm_terminal(self, position: ScrollPosition) -> None:
        """Recheck the proven native maximum after all independent pixel proofs."""
        self._deadline = time.monotonic() + _ITEM_SECONDS
        self._boundary()
        native = self._state(position, terminal=True)
        if self._terminal != (position, native):
            raise CaptureError("The terminal list state is unconfirmed")
        self._boundary()


class ScrollSelectionState:
    """Observe selection and request normal activation, without entering COM."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CaptureError("Scrolling capture requires Windows")
        self._user = c.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetLastInputInfo": ((c.POINTER(_LastInput),), w.BOOL),
            "GetForegroundWindow": ((), w.HWND),
            "SetForegroundWindow": ((w.HWND,), w.BOOL),
            "WindowFromPoint": ((w.POINT,), w.HWND),
            "GetAncestor": ((w.HWND, w.UINT), w.HWND),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._user, name)
            function.argtypes, function.restype = arguments, result
        self._frames = Win32WindowApi()

    @staticmethod
    def windows() -> list[WindowInfo]:
        """Snapshot normal top-level selection targets before showing overlays."""
        return WindowLocator().visible_windows()

    def tick(self) -> int:
        """Read the latest Windows input tick, including the completed release."""
        info = _LastInput(c.sizeof(_LastInput), 0)
        if not self._user.GetLastInputInfo(c.byref(info)):
            raise CaptureError("Could not observe user input")
        return int(info.tick)

    def foreground(self) -> int:
        """Return zero while Windows temporarily has no foreground window."""
        return int(self._user.GetForegroundWindow() or 0)

    def observe(self, point: Point, window: WindowInfo, tick: int) -> int:
        """Preserve the frozen target geometry while its activation settles."""
        if self._frames.frame_bounds(window.handle) != window.bounds:
            raise CaptureError("The selected window moved or resized")
        # DWM can take time. Read input and foreground after it, immediately
        # before the caller decides whether the activation is still eligible.
        return self._selected(point, window.handle, tick)

    def activate(
        self, point: Point, window: WindowInfo, tick: int, foreground: int
    ) -> None:
        """Make one ordinary activation request for the user's exact choice."""
        observed = self.observe(point, window, tick)
        if observed == window.handle:
            return
        if observed != foreground:
            raise CaptureError("The foreground window changed before scrolling capture")
        if not self._user.SetForegroundWindow(window.handle):
            raise CaptureError(
                "Windows did not allow activation of the selected window. "
                "Activate it and try scrolling capture again"
            )

    def check(self, point: Point, window: int, tick: int) -> None:
        """Require the same selected root and input after our selectors close."""
        self._selected(point, window, tick, require_foreground=True)

    def _selected(
        self, point: Point, window: int, tick: int, *, require_foreground: bool = False
    ) -> int:
        hit = self._user.WindowFromPoint(w.POINT(round(point.x), round(point.y)))
        observed_tick = self.tick()
        foreground = self.foreground()
        root = self._user.GetAncestor(hit, 2)
        if (
            observed_tick != tick
            or (require_foreground and foreground != window)
            or root != window
        ):
            logger.warning(
                "Scrolling selection guard: input_changed=%s foreground_changed=%s "
                "hit_changed=%s expected_tick=%s observed_tick=%s "
                "expected_window=%s foreground=%s hit_root=%s",
                observed_tick != tick,
                foreground != window,
                root != window,
                tick,
                observed_tick,
                window,
                foreground,
                root,
            )
            raise CaptureError("The selected window or mouse/keyboard input changed")
        return foreground


class UiaScrollTarget:
    """Retain the nearest scrollable ancestor of the user's physical point.

    Any real input stops the transaction, including mouse movement. This is
    conservative by design. We observe the current desktop and never seize it.
    The provider rectangle is intersected only with its own native client
    rectangle when available. A proven standard List Header is retained once;
    other fixed chrome may fail the stitch rather than be guessed or cropped.
    """

    def __init__(
        self, point: Point, *, window: int | None = None, input_tick: int | None = None
    ) -> None:
        if sys.platform != "win32":
            raise CaptureError("Scrolling capture requires Windows")
        self._owners = ExitStack()
        self._screens = MssScreenSource()
        self._owners.callback(self._screens.close)
        self._user = c.WinDLL("user32", use_last_error=True)
        self._kernel = c.WinDLL("kernel32", use_last_error=True)
        self._ole = c.WinDLL("ole32")
        self._dwm = c.WinDLL("dwmapi")
        self._bind()
        try:
            self._open(point, window=window, input_tick=input_tick)
        except BaseException:
            self._owners.close()
            raise

    def _bind(self) -> None:
        signatures = {
            "GetLastInputInfo": ((c.POINTER(_LastInput),), w.BOOL),
            "GetForegroundWindow": ((), w.HWND),
            "WindowFromPoint": ((w.POINT,), w.HWND),
            "GetAncestor": ((w.HWND, w.UINT), w.HWND),
            "GetWindowRect": ((w.HWND, c.POINTER(w.RECT)), w.BOOL),
            "GetClientRect": ((w.HWND, c.POINTER(w.RECT)), w.BOOL),
            "ClientToScreen": ((w.HWND, c.POINTER(w.POINT)), w.BOOL),
            "GetWindowThreadProcessId": ((w.HWND, c.POINTER(w.DWORD)), w.DWORD),
            "IsWindowVisible": ((w.HWND,), w.BOOL),
            "IsIconic": ((w.HWND,), w.BOOL),
            "GetTopWindow": ((w.HWND,), w.HWND),
            "GetWindow": ((w.HWND, w.UINT), w.HWND),
            "IsWindow": ((w.HWND,), w.BOOL),
            "GetParent": ((w.HWND,), w.HWND),
            "GetScrollInfo": ((w.HWND, c.c_int, c.POINTER(_ScrollInfo)), w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._user, name)
            function.argtypes, function.restype = arguments, result
        self._bind_display()
        self._kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        self._kernel.OpenProcess.restype = w.HANDLE
        self._kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
        self._kernel.WaitForSingleObject.restype = w.DWORD
        self._kernel.CloseHandle.argtypes = (w.HANDLE,)
        self._kernel.CloseHandle.restype = w.BOOL
        self._ole.CoInitializeEx.argtypes = (c.c_void_p, w.DWORD)
        self._ole.CoInitializeEx.restype = c.HRESULT
        self._ole.CoUninitialize.argtypes = ()
        self._ole.CoUninitialize.restype = None
        self._ole.CoCreateInstance.argtypes = (
            c.POINTER(_Guid),
            c.c_void_p,
            w.DWORD,
            c.POINTER(_Guid),
            c.POINTER(c.c_void_p),
        )
        self._ole.CoCreateInstance.restype = c.HRESULT
        self._dwm.DwmGetWindowAttribute.argtypes = (
            w.HWND,
            w.DWORD,
            c.c_void_p,
            w.DWORD,
        )
        self._dwm.DwmGetWindowAttribute.restype = c.HRESULT

    def _bind_display(self) -> None:
        """Bind read-only DPI/monitor APIs, retaining conservative older-OS support."""
        signatures = {
            "GetDpiForWindow": ((w.HWND,), w.UINT),
            "GetWindowDpiAwarenessContext": ((w.HWND,), c.c_void_p),
            "GetThreadDpiAwarenessContext": ((), c.c_void_p),
            "GetAwarenessFromDpiAwarenessContext": ((c.c_void_p,), c.c_int),
            "MonitorFromWindow": ((w.HWND, w.DWORD), w.HMONITOR),
            "MonitorFromRect": ((c.POINTER(w.RECT), w.DWORD), w.HMONITOR),
            "GetMonitorInfoW": ((w.HMONITOR, c.POINTER(_MonitorInfo)), w.BOOL),
        }
        self._display_supported = True
        for name, (arguments, result) in signatures.items():
            function = getattr(self._user, name, None)
            if function is None:
                self._display_supported = False
            else:
                function.argtypes, function.restype = arguments, result

    def _observe_display(self) -> _ScrollDisplay | None:
        """Read the root DPI only when its monitor contains the entire viewport."""
        if not self._display_supported:
            return None
        monitor = self._user.MonitorFromWindow(self._window, 0)
        rectangle = w.RECT(
            round(self._area.left),
            round(self._area.top),
            round(self._area.right),
            round(self._area.bottom),
        )
        if not monitor or self._user.MonitorFromRect(c.byref(rectangle), 0) != monitor:
            raise CaptureError(
                "The scroll panel is not on the selected window's monitor"
            )
        info = _MonitorInfo()
        info.size = c.sizeof(info)
        if not self._user.GetMonitorInfoW(monitor, c.byref(info)):
            raise CaptureError("Could not verify the scroll panel's monitor bounds")
        bounds = Rect.from_points(
            Point(info.monitor.left, info.monitor.top),
            Point(info.monitor.right, info.monitor.bottom),
        )
        if bounds.intersected(self._area) != self._area:
            raise CaptureError("The scroll panel crosses a monitor boundary")
        window_context = self._user.GetWindowDpiAwarenessContext(self._window)
        capture_context = self._user.GetThreadDpiAwarenessContext()
        return _ScrollDisplay(
            monitor=int(monitor),
            bounds=bounds,
            window_dpi=int(self._user.GetDpiForWindow(self._window)),
            window_awareness=int(
                self._user.GetAwarenessFromDpiAwarenessContext(window_context)
            ),
            capture_awareness=int(
                self._user.GetAwarenessFromDpiAwarenessContext(capture_context)
            ),
        )

    def _input(self) -> int:
        info = _LastInput(c.sizeof(_LastInput), 0)
        if not self._user.GetLastInputInfo(c.byref(info)):
            raise CaptureError("Could not observe user input")
        return int(info.tick)

    def _window_bounds(self) -> tuple[int, int, int, int]:
        bounds = w.RECT()
        if not self._user.GetWindowRect(self._window, c.byref(bounds)):
            raise CaptureError("The target window is no longer available")
        return bounds.left, bounds.top, bounds.right, bounds.bottom

    def _open(
        self, point: Point, *, window: int | None = None, input_tick: int | None = None
    ) -> None:
        observed = self._input()
        if input_tick is not None and observed != input_tick:
            raise CaptureError(
                "Mouse or keyboard input changed before scrolling capture"
            )
        self._input_tick = observed if input_tick is None else input_tick
        expected_window = window
        hit = self._user.WindowFromPoint(w.POINT(round(point.x), round(point.y)))
        self._window = self._user.GetAncestor(hit, 2)  # GA_ROOT
        if expected_window is not None and self._window != expected_window:
            raise CaptureError("The selected window changed before scrolling capture")
        if not self._window or self._user.GetForegroundWindow() != self._window:
            raise CaptureError("Select a panel in the foreground window")
        self._window_rect = self._window_bounds()
        pid = w.DWORD()
        self._user.GetWindowThreadProcessId(self._window, c.byref(pid))
        self._process = self._kernel.OpenProcess(0x00100000, 0, pid.value)
        if not self._process:
            raise CaptureError("The target process cannot be observed safely")
        self._owners.callback(self._kernel.CloseHandle, self._process)
        self._ole.CoInitializeEx(None, 0)  # MTA; never the app's GUI thread
        self._owners.callback(self._ole.CoUninitialize)
        pointer = c.c_void_p()
        self._ole.CoCreateInstance(
            c.byref(_Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
            None,
            1,
            c.byref(_Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
            c.byref(pointer),
        )
        automation = _Interface(pointer, self._owners)
        walker = automation.pointer_result(16, self._owners)
        self._element, self._scroll = self._discover(point, automation, walker)
        self._provider_bounds = self._element.bounds()
        self._native_handle = self._element.native_handle()
        self._area = self._viewport()
        self._automation, self._walker = automation, walker
        try:
            self._header = _ScrollHeader.discover(
                self._element, walker, self._owners, self._area
            )
        except CaptureError:
            self._log_header_context()
            raise
        logger.info(
            "Scrolling viewport: provider=%s captured=%s native_handle=%s",
            self._provider_bounds,
            self._area,
            self._native_handle,
        )
        if (
            self._area.width < _MIN_SIZE
            or self._area.height < _MIN_SIZE
            or not self._area.contains(point)
        ):
            raise CaptureError("UI Automation did not identify a visible scroll panel")
        self._scrollbar_identity: ScrollBarState | None = None
        self._display = self._observe_display()
        logger.info(
            "Scrolling display: observed=%s registration_dpi=%s",
            self._display,
            self.registration_dpi,
        )
        self.check()

    def _header_context(self) -> tuple[Rect | None, int | None]:
        """Read client bounds and selected-window DPI with existing typed APIs."""
        self._check_window()
        client = None
        handle = self._native_handle
        if handle is not None:
            if (
                not self._user.IsWindow(handle)
                or self._user.GetAncestor(handle, 2) != self._window
            ):
                raise CaptureError("The Header diagnostic native owner changed")
            bounds = w.RECT()
            if not self._user.GetClientRect(handle, c.byref(bounds)):
                raise CaptureError("Could not read the Header diagnostic client bounds")
            first, last = (
                w.POINT(bounds.left, bounds.top),
                w.POINT(bounds.right, bounds.bottom),
            )
            if not self._user.ClientToScreen(
                handle, c.byref(first)
            ) or not self._user.ClientToScreen(handle, c.byref(last)):
                raise CaptureError(
                    "Could not locate the Header diagnostic client bounds"
                )
            client = Rect.from_points(Point(first.x, first.y), Point(last.x, last.y))
        dpi_api = getattr(self._user, "GetDpiForWindow", None)
        dpi = None if dpi_api is None else int(dpi_api(self._window))
        self._check_window()
        return client, dpi

    def _log_header_context(self) -> None:
        """Keep failed-discovery evidence numeric and subordinate to its exception."""
        client, dpi, observed = None, None, False
        try:
            client, dpi = self._header_context()
            observed = True
        except (CaptureError, OSError):
            pass
        logger.warning(
            "Scrolling Header discovery context: provider=%s captured=%s "
            "native_handle=%s native_client=%s selected_window=%s "
            "window_dpi=%s observation_valid=%s",
            self._provider_bounds,
            self._area,
            self._native_handle,
            client,
            self._window,
            dpi,
            observed,
        )

    def _discover(
        self, point: Point, automation: _Interface, walker: _Interface
    ) -> tuple[_Interface, _Interface]:
        previous = None
        for attempt in range(_DISCOVERY_OBSERVATIONS):
            self._check_window()
            element = automation.pointer_result(
                7, self._owners, (w.POINT, w.POINT(round(point.x), round(point.y)))
            )
            try:
                candidate = self._find(element, walker)
            except _NoScrollTargetError:
                previous = None
            else:
                same = previous is not None and automation.same_element(
                    previous[0], candidate[0]
                )
                logger.debug(
                    "UIA point=%s observation=%s same_candidate=%s",
                    point,
                    attempt + 1,
                    same,
                )
                if same:
                    return candidate
                previous = candidate
            if attempt + 1 < _DISCOVERY_OBSERVATIONS:
                # A lazy tree can expose an outer scroller before its inner
                # target. Confirm the nearest element without resetting state.
                time.sleep(0.5)
        raise CaptureError("No stable supported vertical scroll panel at this point")

    def _viewport(self) -> Rect:
        """Exclude only OS non-client chrome of the element's own native HWND."""
        handle = self._element.native_handle()
        if handle != self._native_handle:
            raise CaptureError("The scroll panel's native handle changed")
        if handle is None:
            return self._provider_bounds
        if self._user.GetAncestor(handle, 2) != self._window:
            raise CaptureError("The scroll panel's native owner changed")
        bounds = w.RECT()
        if not self._user.GetClientRect(handle, c.byref(bounds)):
            raise CaptureError("Could not read the scroll panel's native client area")
        first, last = (
            w.POINT(bounds.left, bounds.top),
            w.POINT(bounds.right, bounds.bottom),
        )
        if not self._user.ClientToScreen(
            handle, c.byref(first)
        ) or not self._user.ClientToScreen(handle, c.byref(last)):
            raise CaptureError("Could not locate the scroll panel's native client area")
        client = Rect.from_points(Point(first.x, first.y), Point(last.x, last.y))
        return self._provider_bounds.intersected(client)

    def _find(
        self, element: _Interface, walker: _Interface
    ) -> tuple[_Interface, _Interface]:
        for _ in range(_MAX_ANCESTORS):
            kind = element.control_type()
            pattern = element.scroll_pattern(self._owners)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "UIA ancestor type=%s bounds=%s scroll=%s",
                    kind,
                    element.bounds(),
                    pattern is not None,
                )
            if pattern is not None and 0.0 <= pattern.number(6) <= END_PERCENT:
                return element, pattern
            parent = walker.parent(element, self._owners)
            if parent is None:
                break
            element = parent
        raise _NoScrollTargetError("No supported vertical scroll panel at this point")

    @property
    def area(self) -> Rect:
        """Return the viewport, excluding only known native non-client chrome."""
        return self._area

    @property
    def header(self) -> Rect | None:
        """Return the verified standard Header rectangle, never an inferred crop."""
        return None if self._header is None else self._header.bounds

    @property
    def footer(self) -> Rect | None:
        """Return the exact horizontal rail; its remaining corner is retained too."""
        return None if self._header is None else self._header.footer

    @property
    def vertical_scrollbar(self) -> Rect | None:
        """Return only the verified vertical rail, excluding the footer corner."""
        return None if self._header is None else self._header.vertical_scrollbar

    @property
    def registration_dpi(self) -> int | None:
        """Use only per-monitor-aware, same-monitor DPI within the bounded range."""
        display = self._display
        if (
            display is not None
            and display.window_awareness == _PER_MONITOR_AWARE
            and display.capture_awareness == _PER_MONITOR_AWARE
            and BASE_REGISTRATION_DPI <= display.window_dpi <= MAX_REGISTRATION_DPI
        ):
            return display.window_dpi
        return None

    def _check_window(self) -> None:
        """Preserve the original eligibility snapshot, including during discovery."""
        if self._input() != self._input_tick:
            raise CaptureError("Stopped because you used the mouse or keyboard")
        if (
            self._user.GetForegroundWindow() != self._window
            or not self._user.IsWindowVisible(self._window)
            or self._user.IsIconic(self._window)
            or self._kernel.WaitForSingleObject(self._process, 0) != _WAIT_TIMEOUT
            or self._window_bounds() != self._window_rect
        ):
            raise CaptureError("The target window changed or lost focus")

    def check(self) -> None:
        """Reject changed user state before/after every provider/capture operation."""
        self._check_window()
        if (
            self._element.bounds() != self._provider_bounds
            or self._viewport() != self._area
        ):
            raise CaptureError("The scroll panel changed position or size")
        if self._observe_display() != self._display:
            raise CaptureError("The scroll panel's monitor or DPI changed")
        if self._header is not None:
            self._header.check(self._element, self._automation, self._walker)
        offscreen = w.BOOL()
        self._element.call(38, (c.POINTER(w.BOOL),), c.byref(offscreen))
        if offscreen.value:
            raise CaptureError("The scroll panel is no longer visible")
        self._check_occlusion()
        # Reject overlaid windows at the viewport edges as well as the center.
        for x in (self._area.left, self._area.center.x, self._area.right - 1):
            for y in (self._area.top, self._area.center.y, self._area.bottom - 1):
                window = self._user.WindowFromPoint(w.POINT(round(x), round(y)))
                if self._user.GetAncestor(window, 2) != self._window:
                    raise CaptureError(
                        f"Another window covers the scroll panel at ({x}, {y}); "
                        f"viewport={self._area}, window={self._window_rect}"
                    )
        if self._input() != self._input_tick:
            raise CaptureError("Stopped because you used the mouse or keyboard")

    def _check_occlusion(self) -> None:
        """Conservatively reject top-level overlap, including tiny tool windows.

        Do not filter titles, styles, opacity or minimum size. A bounding-box
        false positive is safer than capturing an overlay. Same-window content
        overlays remain outside this OS-level check.
        """
        window = self._user.GetTopWindow(None)
        seen: set[int] = set()
        for _ in range(_MAX_WINDOWS):
            if not window or window in seen or not self._user.IsWindow(window):
                break
            if window == self._window:
                return
            seen.add(window)
            if self._user.IsWindowVisible(window) and not self._user.IsIconic(window):
                cloaked = w.DWORD()
                self._dwm.DwmGetWindowAttribute(
                    window, 14, c.byref(cloaked), c.sizeof(cloaked)
                )  # DWMWA_CLOAKED; HRESULT errors propagate, never fail open.
                if not cloaked.value:
                    bounds = w.RECT()
                    if not self._user.GetWindowRect(window, c.byref(bounds)):
                        raise CaptureError("Could not inspect a window above the panel")
                    rectangle = Rect.from_points(
                        Point(bounds.left, bounds.top),
                        Point(bounds.right, bounds.bottom),
                    )
                    if not rectangle.intersected(self._area).is_empty:
                        raise CaptureError("Another window covers the scroll panel")
            window = self._user.GetWindow(window, 2)  # GW_HWNDNEXT, top to bottom
        raise CaptureError("Could not confirm the scroll panel's window order")

    def _rail_identity(self, element: _Interface) -> None:
        """Reidentify the retained rail, without discovering a new target."""
        with ExitStack() as owners:
            point = element.bounds().center
            current = self._automation.pointer_result(
                7, owners, (w.POINT, w.POINT(round(point.x), round(point.y)))
            )
            for _ in range(_MAX_ANCESTORS):
                if current.control_type() == _SCROLLBAR_CONTROL:
                    if not self._automation.same_element(current, element):
                        raise CaptureError("The native scrollbar identity changed")
                    return
                parent = self._walker.parent(current, owners)
                if parent is None:
                    break
                current = parent
        raise CaptureError("The retained native scrollbar cannot be verified")

    def scrollbar_state(self) -> ScrollBarState | None:
        """Read optional exact native units only for the already proven Header rail."""
        if self._header is None or self._header.scrollbar is None:
            return None
        element = self._header.scrollbar
        handle = element.native_handle()
        previous = getattr(self, "_scrollbar_identity", None)
        if (handle is None or handle <= 0) and previous is None:
            return None
        self.check()
        if handle is None or handle <= 0 or not self._user.IsWindow(handle):
            raise CaptureError("The native scrollbar is no longer available")
        self._rail_identity(element)
        parent = int(self._user.GetParent(handle) or 0)
        root = int(self._user.GetAncestor(handle, 2) or 0)
        pid, root_pid = w.DWORD(), w.DWORD()
        self._user.GetWindowThreadProcessId(handle, c.byref(pid))
        self._user.GetWindowThreadProcessId(self._window, c.byref(root_pid))
        if (
            not parent
            or root != self._window
            or not pid.value
            or pid.value != root_pid.value
        ):
            raise CaptureError("The native scrollbar owner changed")
        info = _ScrollInfo()
        info.size, info.mask = c.sizeof(info), 7  # SIF_RANGE | SIF_PAGE | SIF_POS
        if not self._user.GetScrollInfo(handle, 2, c.byref(info)):  # SB_CTL
            raise CaptureError("Could not read the native scrollbar")
        state = ScrollBarState(
            handle,
            parent,
            root,
            pid.value,
            info.minimum,
            info.maximum,
            info.page,
            info.position,
        )
        if (
            previous is not None and not state.same_range(previous)
        ) or not state.matches(self.position()):
            raise CaptureError("The native scrollbar mapping or identity changed")
        self.check()
        if element.native_handle() != handle:
            raise CaptureError("The native scrollbar handle changed during observation")
        self._scrollbar_identity = state
        return state

    def list_steps(
        self,
        initial: ScrollPosition,
        check: Callable[[], None],
        *,
        poll: Callable[[], None],
    ) -> _ListSteps | None:
        """Select optional standard-List evidence once, before any traversal."""
        check()
        if (
            self._header is None
            or self._header.scrollbar is None
            or self._element.control_type() != _LIST_CONTROL
            or initial.horizontal not in (-1.0, 0.0)
            or (initial.horizontal == -1.0 and self._header.footer is not None)
        ):
            return None
        native = self.scrollbar_state()
        check()
        if native is None:
            return None
        if self.position() != initial or not native.matches(initial):
            raise CaptureError("The list changed while selecting item evidence")
        check()
        return _ListSteps(self, initial, native, check, poll)

    def position(self) -> ScrollPosition:
        """Read percentages without asking the provider to change anything."""
        horizontal, vertical, size = (
            self._scroll.number(5),
            self._scroll.number(6),
            self._scroll.number(8),
        )
        if (
            horizontal != -1.0 and not 0.0 <= horizontal <= END_PERCENT
        ) or not 0.0 <= vertical <= END_PERCENT:
            raise CaptureError("The selected panel has unsupported scroll state")
        return ScrollPosition(horizontal, vertical, size)

    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Preserve H and admit the setter only after all blocking preflight.

        The callback may reject without issuing a write. After it returns no
        native/provider work precedes the setter; an exception then is uncertain.
        """
        self.check()
        horizontal = self._scroll.number(5)
        if horizontal != -1.0 and not 0.0 <= horizontal <= END_PERCENT:
            raise CaptureError("The selected panel has unsupported horizontal state")
        self.check()
        self._check_window()
        if before_write is not None:
            before_write()
        self._scroll.call(4, (c.c_double, c.c_double), horizontal, vertical)
        self.check()

    def frame(self) -> QImage:
        """Read visible desktop pixels, rejecting clipping at monitor boundaries."""
        self.check()
        frozen = self._screens.freeze()
        if frozen.bounds.intersected(self._area) != self._area:
            raise CaptureError("The scroll panel extends outside the desktop")
        image = frozen.crop(self._area)
        self.check()
        return image

    def __enter__(self) -> Self:
        """Own UIA and capture resources until process-local use finishes."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Release retained native handles on the same COM thread."""
        self._owners.close()
