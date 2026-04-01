"""Tests for verdiclip.capture.window.WindowCapture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap

from verdiclip.capture.window import WindowCapture


class TestGetForegroundWindowHandle:
    def test_returns_int(self) -> None:
        hwnd = WindowCapture.get_foreground_window_handle()
        assert isinstance(hwnd, int)


class TestGetWindowRect:
    def test_returns_qrect_with_reasonable_dimensions(self) -> None:
        hwnd = WindowCapture.get_foreground_window_handle()
        if not hwnd:
            return
        rect = WindowCapture.get_window_rect(hwnd)
        assert isinstance(rect, QRect)
        assert rect.width() > 0
        assert rect.height() > 0

    def test_without_decorations(self) -> None:
        hwnd = WindowCapture.get_foreground_window_handle()
        if not hwnd:
            return
        rect = WindowCapture.get_window_rect(hwnd, include_decorations=False)
        assert isinstance(rect, QRect)
        assert rect.width() > 0
        assert rect.height() > 0


class TestGetWindowTitle:
    def test_returns_string(self) -> None:
        hwnd = WindowCapture.get_foreground_window_handle()
        title = WindowCapture.get_window_title(hwnd)
        assert isinstance(title, str)


class TestCaptureActiveWindow:
    def test_returns_non_null_pixmap(self, qapp) -> None:
        pixmap = WindowCapture.capture_active_window()
        assert isinstance(pixmap, QPixmap)
        assert not pixmap.isNull()


class TestEnumerateVisibleWindows:
    def test_returns_non_empty_list(self) -> None:
        windows = WindowCapture.enumerate_visible_windows()
        assert isinstance(windows, list)
        assert len(windows) > 0

    def test_tuple_structure(self) -> None:
        windows = WindowCapture.enumerate_visible_windows()
        hwnd, title, rect = windows[0]
        assert isinstance(hwnd, int)
        assert isinstance(title, str)
        assert isinstance(rect, QRect)


class TestCaptureWindowByHandle:
    def test_captures_foreground_window(self, qapp) -> None:
        hwnd = WindowCapture.get_foreground_window_handle()
        if not hwnd:
            return
        pixmap = WindowCapture.capture_window_by_handle(hwnd)
        assert isinstance(pixmap, QPixmap)
        assert not pixmap.isNull()


class TestGetWindowRectMocked:
    """Mocked ctypes tests for deterministic Win32 coverage."""

    @patch("verdiclip.capture.window.dwmapi")
    def test_dwm_success_returns_extended_frame(
        self, mock_dwmapi
    ) -> None:
        """DwmGetWindowAttribute success uses DWM bounds."""
        mock_dwmapi.DwmGetWindowAttribute.return_value = 0
        rect = WindowCapture.get_window_rect(
            12345, include_decorations=False
        )
        assert isinstance(rect, QRect)
        mock_dwmapi.DwmGetWindowAttribute.assert_called_once()

    @patch("verdiclip.capture.window.user32")
    @patch("verdiclip.capture.window.dwmapi")
    def test_dwm_failure_falls_back_to_getwindowrect(
        self, mock_dwmapi, mock_user32
    ) -> None:
        """When DWM fails, falls back to user32.GetWindowRect."""
        mock_dwmapi.DwmGetWindowAttribute.return_value = -1
        rect = WindowCapture.get_window_rect(
            12345, include_decorations=False
        )
        assert isinstance(rect, QRect)
        mock_user32.GetWindowRect.assert_called_once()


class TestCaptureActiveWindowMocked:
    """Mocked tests for capture_active_window branches."""

    @patch("verdiclip.capture.window.ScreenCapture")
    @patch.object(WindowCapture, "get_window_rect")
    @patch.object(WindowCapture, "get_window_title")
    @patch.object(WindowCapture, "get_foreground_window_handle")
    def test_valid_hwnd_captures_region(
        self, mock_hwnd, mock_title, mock_rect, mock_screen
    ) -> None:
        """Valid hwnd triggers title+rect lookup and capture."""
        mock_hwnd.return_value = 12345
        mock_title.return_value = "Test Window"
        mock_rect.return_value = QRect(0, 0, 800, 600)
        expected = MagicMock()
        mock_screen.capture_region.return_value = expected

        result = WindowCapture.capture_active_window()

        mock_title.assert_called_once_with(12345)
        mock_rect.assert_called_once_with(12345, True)
        mock_screen.capture_region.assert_called_once()
        assert result is expected

    @patch("verdiclip.capture.window.ScreenCapture")
    @patch.object(WindowCapture, "get_foreground_window_handle")
    def test_no_hwnd_falls_back_to_primary(
        self, mock_hwnd, mock_screen
    ) -> None:
        """Null hwnd falls back to primary monitor capture."""
        mock_hwnd.return_value = 0
        expected = MagicMock()
        mock_screen.capture_primary_monitor.return_value = expected

        result = WindowCapture.capture_active_window()

        mock_screen.capture_primary_monitor.assert_called_once()
        assert result is expected


class TestCaptureWindowByHandleMocked:
    """Mocked test for capture_window_by_handle."""

    @patch("verdiclip.capture.window.ScreenCapture")
    @patch.object(WindowCapture, "get_window_rect")
    def test_captures_specified_window(
        self, mock_rect, mock_screen
    ) -> None:
        """Captures window region by handle."""
        mock_rect.return_value = QRect(10, 20, 400, 300)
        expected = MagicMock()
        mock_screen.capture_region.return_value = expected

        result = WindowCapture.capture_window_by_handle(99999)

        mock_rect.assert_called_once_with(99999, True)
        mock_screen.capture_region.assert_called_once()
        assert result is expected

    @patch("verdiclip.capture.window.ScreenCapture")
    @patch.object(WindowCapture, "get_window_rect")
    def test_without_decorations(
        self, mock_rect, mock_screen
    ) -> None:
        """Passes include_decorations=False through."""
        mock_rect.return_value = QRect(10, 20, 400, 300)
        mock_screen.capture_region.return_value = MagicMock()

        WindowCapture.capture_window_by_handle(
            99999, include_decorations=False
        )

        mock_rect.assert_called_once_with(99999, False)
