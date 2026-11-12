"""UI Automation scroll target, used only inside the scrolling helper process.

ABI reference: Microsoft WinSDK UIAutomationClient.h.
IUIAutomation: ElementFromPoint=7, RawViewWalker=16.
IUIAutomationElement: GetCurrentPatternAs=14, ControlType=21, NativeWindowHandle=36,
BoundingRectangle=43. IUIAutomationTreeWalker: GetParentElement=3.
IUIAutomationScrollPattern: SetScrollPercent=4, VerticalScrollPercent=6,
HorizontalScrollPercent=5, VerticalViewSize=8, VerticallyScrollable=10.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import logging
import math
import sys
import time
import uuid
from contextlib import ExitStack
from typing import Self

import mss
from PySide6.QtGui import QImage

from verdiclip.capture.scrolling import ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)

_CUIAUTOMATION = "ff48dba4-60ef-4201-aa87-54103eef594e"
_IUIAUTOMATION = "30cbe57d-d9d0-452a-ab13-7ac5ac4825ee"
_SCROLL_IID = "88f4d42a-e881-459d-a77c-73bbbb7e02dc"
_SCROLL_PATTERN = 10004
_SCROLLBAR_CONTROL = 50014
_NO_SCROLL = -1.0
_MAX_ANCESTORS = 32
_MIN_SIZE = 32
_GA_ROOT = 2
_LOOKUPS = 2
_RETRY_SECONDS = 0.5


class _Guid(c.Structure):
    _fields_ = [("bytes", c.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _Interface:
    """One retained COM reference called through its vtable."""

    def __init__(self, pointer: c.c_void_p, owners: ExitStack) -> None:
        if pointer.value is None:
            raise CaptureError("UI Automation returned no element")
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

    def nullable(
        self, slot: int, owners: ExitStack, *prefix: tuple[type[c._CData], object]
    ) -> _Interface | None:
        result = c.c_void_p()
        self.call(
            slot,
            (*[kind for kind, _ in prefix], c.POINTER(c.c_void_p)),
            *[value for _, value in prefix],
            c.byref(result),
        )
        return None if result.value is None else _Interface(result, owners)

    def number(self, slot: int) -> float:
        result = c.c_double()
        self.call(slot, (c.POINTER(c.c_double),), c.byref(result))
        return result.value

    def flag(self, slot: int) -> bool:
        result = w.BOOL()
        self.call(slot, (c.POINTER(w.BOOL),), c.byref(result))
        return bool(result.value)

    def integer(self, slot: int) -> int:
        result = c.c_int()
        self.call(slot, (c.POINTER(c.c_int),), c.byref(result))
        return result.value

    def handle(self) -> int:
        result = w.HWND()
        self.call(36, (c.POINTER(w.HWND),), c.byref(result))
        return int(result.value or 0)

    def bounds(self) -> Rect:
        native = w.RECT()
        self.call(43, (c.POINTER(w.RECT),), c.byref(native))
        return Rect.from_points(
            Point(native.left, native.top), Point(native.right, native.bottom)
        )


class UiaScrollTarget:
    """The nearest vertically scrollable UIA ancestor of a screen point."""

    def __init__(self, point: Point) -> None:
        if sys.platform != "win32":
            raise CaptureError("Scrolling capture requires Windows")
        self._owners = ExitStack()
        try:
            self._open(point)
        except BaseException:
            self._owners.close()
            raise

    def _open(self, point: Point) -> None:
        user = c.WinDLL("user32", use_last_error=True)
        for name, arguments, result in (
            ("WindowFromPoint", (w.POINT,), w.HWND),
            ("GetAncestor", (w.HWND, w.UINT), w.HWND),
            ("IsWindow", (w.HWND,), w.BOOL),
            ("IsIconic", (w.HWND,), w.BOOL),
            ("GetClientRect", (w.HWND, c.POINTER(w.RECT)), w.BOOL),
            ("ClientToScreen", (w.HWND, c.POINTER(w.POINT)), w.BOOL),
        ):
            function = getattr(user, name)
            function.argtypes, function.restype = arguments, result
        self._user = user
        hit = user.WindowFromPoint(w.POINT(round(point.x), round(point.y)))
        self._window = int(user.GetAncestor(hit, _GA_ROOT) or 0)
        if not self._window:
            raise CaptureError("There is no window at that point")
        ole = c.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = (c.c_void_p, w.DWORD)
        ole.CoInitializeEx.restype = c.HRESULT
        ole.CoUninitialize.argtypes = ()
        ole.CoUninitialize.restype = None
        ole.CoInitializeEx(None, 0)  # Multithreaded apartment
        self._owners.callback(ole.CoUninitialize)
        pointer = c.c_void_p()
        ole.CoCreateInstance.argtypes = (
            c.POINTER(_Guid),
            c.c_void_p,
            w.DWORD,
            c.POINTER(_Guid),
            c.POINTER(c.c_void_p),
        )
        ole.CoCreateInstance.restype = c.HRESULT
        ole.CoCreateInstance(
            c.byref(_Guid.parse(_CUIAUTOMATION)),
            None,
            1,  # CLSCTX_INPROC_SERVER
            c.byref(_Guid.parse(_IUIAUTOMATION)),
            c.byref(pointer),
        )
        automation = _Interface(pointer, self._owners)
        walker = automation.nullable(16, self._owners)  # RawViewWalker
        if walker is None:
            raise CaptureError("UI Automation is unavailable")
        for attempt in range(_LOOKUPS):
            # Chromium builds its accessibility tree only after the first
            # request, so a miss is retried once after a pause.
            element = automation.nullable(
                7, self._owners, (w.POINT, w.POINT(round(point.x), round(point.y)))
            )
            if element is None:
                raise CaptureError("UI Automation is unavailable")
            try:
                self._element, self._scroll = self._find(element, walker)
                break
            except CaptureError:
                if attempt == _LOOKUPS - 1:
                    raise
                time.sleep(_RETRY_SECONDS)
        self._area = self._viewport()
        if self._area.width < _MIN_SIZE or self._area.height < _MIN_SIZE:
            raise CaptureError("The scrolling area is too small")
        self._grabber = mss.MSS()
        self._owners.callback(self._grabber.close)
        logger.info("Scrolling viewport %s", self._area)

    def _find(
        self, element: _Interface, walker: _Interface
    ) -> tuple[_Interface, _Interface]:
        current: _Interface | None = element
        for _ in range(_MAX_ANCESTORS):
            if current is None:
                break
            pattern = current.nullable(
                14,
                self._owners,
                (c.c_int, _SCROLL_PATTERN),
                (c.POINTER(_Guid), c.byref(_Guid.parse(_SCROLL_IID))),
            )
            if pattern is not None and pattern.flag(10):
                return current, pattern
            current = walker.nullable(3, self._owners, (c.c_void_p, current._pointer))
        raise CaptureError("Nothing at that point scrolls vertically")

    def _viewport(self) -> Rect:
        """The element's bounds, without its own window's non-client scrollbars."""
        bounds = self._element.bounds()
        handle = self._element.handle()
        if not handle:
            return bounds
        client = w.RECT()
        if not self._user.GetClientRect(handle, c.byref(client)):
            return bounds
        first = w.POINT(client.left, client.top)
        last = w.POINT(client.right, client.bottom)
        if not (
            self._user.ClientToScreen(handle, c.byref(first))
            and self._user.ClientToScreen(handle, c.byref(last))
        ):
            return bounds
        inner = bounds.intersected(
            Rect.from_points(Point(first.x, first.y), Point(last.x, last.y))
        )
        return bounds if inner.is_empty else inner

    @property
    def area(self) -> Rect:
        """The captured viewport in physical screen pixels."""
        return self._area

    @property
    def height(self) -> int:
        """Viewport height in physical pixels."""
        return int(self._area.height)

    def position(self) -> ScrollPosition:
        """Read the vertical percent and view size."""
        percent = self._scroll.number(6)
        size = self._scroll.number(8)
        if not math.isfinite(percent) or not math.isfinite(size):
            raise CaptureError("The panel reported an invalid scroll position")
        return ScrollPosition(max(0.0, min(100.0, percent)), size)

    def scroll_to(self, percent: float) -> None:
        """Scroll vertically, keeping the horizontal position."""
        self._scroll.call(4, (c.c_double, c.c_double), _NO_SCROLL, percent)

    def grab(self) -> QImage:
        """Capture the viewport's current screen pixels."""
        area = self._area.rounded()
        region = {
            "left": int(area.x),
            "top": int(area.y),
            "width": int(area.width),
            "height": int(area.height),
        }
        try:
            shot = self._grabber.grab(region)
        except mss.ScreenShotError as error:
            raise CaptureError(f"Could not capture the screen: {error}") from error
        return QImage(
            shot.raw,
            shot.width,
            shot.height,
            shot.width * 4,
            QImage.Format.Format_RGB32,
        ).copy()

    def check(self) -> None:
        """Stop if the window closed or was minimized, or the panel moved."""
        if not self._user.IsWindow(self._window) or self._user.IsIconic(self._window):
            raise CaptureError("The window being captured was closed or minimized")
        try:
            viewport = self._viewport()
        except OSError as error:  # The provider went away with its window
            raise CaptureError(
                "The window being captured was closed or minimized"
            ) from error
        if viewport != self._area:
            # Grabs read the original rectangle; anything else is the wrong pixels.
            raise CaptureError("The window being captured moved or changed size")

    def __enter__(self) -> Self:
        """Own COM and capture resources for one capture."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Release COM references on the thread that created them."""
        self._owners.close()
