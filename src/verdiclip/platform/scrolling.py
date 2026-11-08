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
import uuid
from contextlib import ExitStack
from typing import Self

from PySide6.QtGui import QImage

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)

_SCROLL_IID = "88f4d42a-e881-459d-a77c-73bbbb7e02dc"
_MAX_ANCESTORS = 32
_MIN_SIZE = 64
_WAIT_TIMEOUT = 258


class _Guid(c.Structure):
    _fields_ = [("bytes", c.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _LastInput(c.Structure):
    _fields_ = [("size", w.UINT), ("tick", w.DWORD)]


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


class UiaScrollTarget:
    """Retain the nearest scrollable ancestor of the user's physical point.

    Any real input stops the transaction, including mouse movement. This is
    conservative by design. We observe the current desktop and never seize it.
    The full provider rectangle is captured; UIA bounds that include fixed
    chrome may therefore fail the full-width stitch rather than be cropped.
    """

    def __init__(self, point: Point) -> None:
        if sys.platform != "win32":
            raise CaptureError("Scrolling capture requires Windows")
        self._owners = ExitStack()
        self._screens = MssScreenSource()
        self._owners.callback(self._screens.close)
        self._user = c.WinDLL("user32", use_last_error=True)
        self._kernel = c.WinDLL("kernel32", use_last_error=True)
        self._ole = c.WinDLL("ole32")
        self._bind()
        try:
            self._open(point)
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
            "GetWindowThreadProcessId": ((w.HWND, c.POINTER(w.DWORD)), w.DWORD),
            "IsWindowVisible": ((w.HWND,), w.BOOL),
            "IsIconic": ((w.HWND,), w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._user, name)
            function.argtypes, function.restype = arguments, result
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

    def _open(self, point: Point) -> None:
        self._input_tick = self._input()
        window = self._user.WindowFromPoint(w.POINT(round(point.x), round(point.y)))
        self._window = self._user.GetAncestor(window, 2)  # GA_ROOT
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
        element = automation.pointer_result(
            7, self._owners, (w.POINT, w.POINT(round(point.x), round(point.y)))
        )
        self._element, self._scroll = self._find(element, walker)
        self._area = self._element.bounds()
        if (
            self._area.width < _MIN_SIZE
            or self._area.height < _MIN_SIZE
            or not self._area.contains(point)
        ):
            raise CaptureError("UI Automation did not identify a visible scroll panel")
        self.check()

    def _find(
        self, element: _Interface, walker: _Interface
    ) -> tuple[_Interface, _Interface]:
        for _ in range(_MAX_ANCESTORS):
            pattern = element.scroll_pattern(self._owners)
            logger.debug(
                "UIA ancestor bounds=%s scroll=%s",
                element.bounds(),
                pattern is not None,
            )
            if pattern is not None and 0.0 <= pattern.number(6) <= END_PERCENT:
                return element, pattern
            element = walker.pointer_result(
                3, self._owners, (c.c_void_p, element._pointer)
            )
        raise CaptureError("No supported vertical scroll panel at this point")

    @property
    def area(self) -> Rect:
        """Return the provider's complete viewport, not a guessed content crop."""
        return self._area

    def check(self) -> None:
        """Reject changed user state before/after every provider/capture operation."""
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
        if self._element.bounds() != self._area:
            raise CaptureError("The scroll panel changed position or size")
        offscreen = w.BOOL()
        self._element.call(38, (c.POINTER(w.BOOL),), c.byref(offscreen))
        if offscreen.value:
            raise CaptureError("The scroll panel is no longer visible")
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

    def position(self) -> ScrollPosition:
        """Read percentages without asking the provider to change anything."""
        horizontal, vertical, size = (
            self._scroll.number(5),
            self._scroll.number(6),
            self._scroll.number(8),
        )
        if not -1.0 <= horizontal <= END_PERCENT or not 0.0 <= vertical <= END_PERCENT:
            raise CaptureError("The selected panel has unsupported scroll state")
        return ScrollPosition(horizontal, vertical, size)

    def move(self, vertical: float) -> None:
        """Keep horizontal position using UIA's NoScroll percentage (-1)."""
        self.check()
        self._scroll.call(4, (c.c_double, c.c_double), -1.0, vertical)
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
