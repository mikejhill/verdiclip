"""Window capture using Win32 API."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect

from verdiclip.capture.screen import ScreenCapture

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

# Win32 API constants
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_SHOWMAXIMIZED = 3

_MIN_WINDOW_DIMENSION = 50


class WindowCapture:
    """Captures screenshots of specific windows."""

    @staticmethod
    def get_foreground_window_handle() -> int:
        """Return the handle of the currently active foreground window."""
        return user32.GetForegroundWindow()

    @staticmethod
    def get_window_rect(hwnd: int, include_decorations: bool = True) -> QRect:
        """Get the bounding rectangle of a window.

        Args:
            hwnd: Window handle.
            include_decorations: If True, use the standard window rect.
                If False, use DWM extended frame bounds (tighter crop).
        """
        if not include_decorations:
            rect = ctypes.wintypes.RECT()
            result = dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if result == 0:
                return QRect(
                    rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top,
                )

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return QRect(
            rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top,
        )

    @staticmethod
    def get_window_title(hwnd: int) -> str:
        """Return the title of the given window."""
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        return buf.value

    @classmethod
    def capture_active_window(cls, include_decorations: bool = True) -> QPixmap:
        """Capture the currently active (foreground) window."""
        hwnd = cls.get_foreground_window_handle()
        if not hwnd:
            logger.warning("No foreground window found, capturing primary monitor.")
            return ScreenCapture.capture_primary_monitor()

        title = cls.get_window_title(hwnd)
        rect = cls.get_window_rect(hwnd, include_decorations)
        logger.info("Capturing window '%s' at (%d,%d %dx%d)",
                     title, rect.x(), rect.y(), rect.width(), rect.height())
        return ScreenCapture.capture_region(rect)

    @classmethod
    def capture_window_by_handle(
        cls, hwnd: int, include_decorations: bool = True
    ) -> QPixmap:
        """Capture a specific window by its handle."""
        rect = cls.get_window_rect(hwnd, include_decorations)
        return ScreenCapture.capture_region(rect)

    @staticmethod
    def enumerate_visible_windows(
        exclude_hwnd: int = 0,
    ) -> list[tuple[int, str, QRect]]:
        """Return a list of visible, non-minimized, non-tool windows.

        Args:
            exclude_hwnd: Optional window handle to exclude (e.g. our own overlay).
        """
        windows: list[tuple[int, str, QRect]] = []

        @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_callback(hwnd: int, _lparam: int) -> bool:
            if hwnd == exclude_hwnd:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.IsIconic(hwnd):
                return True

            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            # Use DWM extended frame bounds for accurate geometry
            rect = ctypes.wintypes.RECT()
            result = dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if result != 0:
                user32.GetWindowRect(hwnd, ctypes.byref(rect))

            qrect = QRect(
                rect.left, rect.top,
                rect.right - rect.left, rect.bottom - rect.top,
            )
            if qrect.width() >= _MIN_WINDOW_DIMENSION and qrect.height() >= _MIN_WINDOW_DIMENSION:
                windows.append((hwnd, title, qrect))
            return True

        user32.EnumWindows(enum_callback, 0)
        return windows
