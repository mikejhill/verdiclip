"""Discover capturable top-level windows in physical screen coordinates."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point, Rect

_WS_EX_TOOLWINDOW: Final = 0x00000080
_WS_EX_APPWINDOW: Final = 0x00040000
_MINIMUM_SIZE: Final = 20


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A titled native window and its physical frame bounds."""

    handle: int
    title: str
    bounds: Rect


class WindowApi(Protocol):
    """Minimal discovery primitives for top-level windows."""

    def enumerate_handles(self) -> list[int]:
        """Return handles in top-to-bottom z-order."""

    def is_visible(self, handle: int) -> bool:
        """Return whether the visibility flag is set."""

    def is_iconic(self, handle: int) -> bool:
        """Return whether the window is minimized."""

    def is_cloaked(self, handle: int) -> bool:
        """Return whether DWM hides the window."""

    def ex_style(self, handle: int) -> int:
        """Return extended style flags."""

    def title(self, handle: int) -> str:
        """Return the window caption."""

    def frame_bounds(self, handle: int) -> Rect:
        """Return physical bounds excluding invisible DWM borders."""

    def foreground_handle(self) -> int:
        """Return the foreground handle, or zero."""


class _WindowEnumeration:
    """Collect one synchronous EnumWindows traversal."""

    def __init__(self) -> None:
        """Initialize the ordered callback result."""
        self._handles: list[int] = []

    def collect(self, handle: int, _parameter: int) -> bool:
        """Append each native handle and continue enumeration."""
        self._handles.append(handle)
        return True

    @property
    def handles(self) -> list[int]:
        """Return the collected handles in native order."""
        return self._handles.copy()


class Win32WindowApi:
    """Win32 and DWM discovery with explicit native signatures."""

    def __init__(self) -> None:
        """Load the native libraries and bind their signatures."""
        if sys.platform != "win32":
            msg = "Window discovery requires Windows"
            raise PlatformError(msg)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )
        self._user32.EnumWindows.argtypes = [
            self._callback_type,
            ctypes.wintypes.LPARAM,
        ]
        self._user32.EnumWindows.restype = ctypes.wintypes.BOOL
        for name in ("IsWindowVisible", "IsIconic"):
            function = getattr(self._user32, name)
            function.argtypes = [ctypes.wintypes.HWND]
            function.restype = ctypes.wintypes.BOOL
        self._user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        self._user32.GetWindowLongW.restype = ctypes.wintypes.LONG
        self._user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.GetWindowRect.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.wintypes.RECT),
        ]
        self._user32.GetWindowRect.restype = ctypes.wintypes.BOOL
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        self._dwmapi.DwmGetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        self._dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    def enumerate_handles(self) -> list[int]:
        """Enumerate top-level windows in native z-order."""
        enumeration = _WindowEnumeration()
        callback = self._callback_type(enumeration.collect)
        if not self._user32.EnumWindows(callback, 0):
            msg = "EnumWindows failed"
            raise PlatformError(msg)
        return enumeration.handles

    def is_visible(self, handle: int) -> bool:
        """Return the native visibility flag."""
        return bool(self._user32.IsWindowVisible(handle))

    def is_iconic(self, handle: int) -> bool:
        """Return the native minimized flag."""
        return bool(self._user32.IsIconic(handle))

    def is_cloaked(self, handle: int) -> bool:
        """Exclude cloaked windows and windows whose DWM state is unavailable."""
        cloaked = ctypes.wintypes.DWORD()
        result = self._dwmapi.DwmGetWindowAttribute(
            handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return bool(result != 0 or cloaked.value)

    def ex_style(self, handle: int) -> int:
        """Return extended styles using their 32-bit Win32 representation."""
        return int(self._user32.GetWindowLongW(handle, -20))

    def title(self, handle: int) -> str:
        """Read the Unicode caption into a correctly sized buffer."""
        length = int(self._user32.GetWindowTextLengthW(handle))
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(handle, buffer, len(buffer))
        return str(buffer.value)

    def frame_bounds(self, handle: int) -> Rect:
        """Prefer DWM frame bounds and fall back to GetWindowRect."""
        bounds = ctypes.wintypes.RECT()
        result = self._dwmapi.DwmGetWindowAttribute(
            handle, 9, ctypes.byref(bounds), ctypes.sizeof(bounds)
        )
        if result != 0 and not self._user32.GetWindowRect(handle, ctypes.byref(bounds)):
            msg = f"Could not read frame bounds for window {handle}"
            raise PlatformError(msg)
        return Rect.from_points(
            Point(bounds.left, bounds.top), Point(bounds.right, bounds.bottom)
        )

    def foreground_handle(self) -> int:
        """Return zero when there is no foreground window."""
        return int(self._user32.GetForegroundWindow() or 0)


class WindowLocator:
    """Select visible capture targets without changing native z-order."""

    def __init__(self, api: WindowApi | None = None) -> None:
        """Store the injected or native window discovery adapter."""
        self._api = api if api is not None else Win32WindowApi()

    def foreground(self) -> WindowInfo | None:
        """Return the foreground capture target if it passes visibility checks."""
        handle = self._api.foreground_handle()
        return self._window_info(handle) if handle else None

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> list[WindowInfo]:
        """Return eligible windows in topmost-first order."""
        windows: list[WindowInfo] = []
        for handle in self._api.enumerate_handles():
            if handle in exclude:
                continue
            info = self._window_info(handle)
            if info is not None:
                windows.append(info)
        return windows

    def _window_info(self, handle: int) -> WindowInfo | None:
        """Reject hidden, untitled, auxiliary, and tiny windows."""
        if (
            not self._api.is_visible(handle)
            or self._api.is_iconic(handle)
            or self._api.is_cloaked(handle)
        ):
            return None
        style = self._api.ex_style(handle)
        if style & _WS_EX_TOOLWINDOW and not style & _WS_EX_APPWINDOW:
            return None
        title = self._api.title(handle)
        if not title:
            return None
        bounds = self._api.frame_bounds(handle)
        if bounds.width < _MINIMUM_SIZE or bounds.height < _MINIMUM_SIZE:
            return None
        return WindowInfo(handle, title, bounds)

    @staticmethod
    def window_at(point: Point, windows: Sequence[WindowInfo]) -> WindowInfo | None:
        """Return the first containing window from a topmost-first sequence."""
        return next(
            (window for window in windows if window.bounds.contains(point)), None
        )
