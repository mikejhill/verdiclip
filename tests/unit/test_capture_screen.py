"""Tests for verdiclip.capture.screen.ScreenCapture."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect

from verdiclip.capture.screen import ScreenCapture


class TestCapturePrimaryMonitor:
    def test_capture_primary_monitor(self, qapp) -> None:
        pixmap = ScreenCapture.capture_primary_monitor()
        assert not pixmap.isNull(), "Expected capture_primary_monitor to return non-null pixmap"
        assert pixmap.width() > 0, f"Expected primary monitor width > 0, got {pixmap.width()}"
        assert pixmap.height() > 0, f"Expected primary monitor height > 0, got {pixmap.height()}"


class TestCaptureAllMonitors:
    def test_capture_all_monitors(self, qapp) -> None:
        pixmap = ScreenCapture.capture_all_monitors()
        assert not pixmap.isNull(), "Expected capture_all_monitors to return non-null pixmap"
        assert pixmap.width() > 0, f"Expected all monitors width > 0, got {pixmap.width()}"
        assert pixmap.height() > 0, f"Expected all monitors height > 0, got {pixmap.height()}"


class TestGetMonitorCount:
    def test_get_monitor_count(self) -> None:
        count = ScreenCapture.get_monitor_count()
        assert isinstance(count, int), f"Expected int, got {type(count).__name__}"
        assert count >= 1, f"Expected at least 1 monitor, got {count}"


class TestCaptureRegion:
    def test_capture_region(self, qapp) -> None:
        rect = QRect(0, 0, 100, 100)
        pixmap = ScreenCapture.capture_region(rect)
        assert not pixmap.isNull(), "Expected capture_region to return non-null pixmap"
        assert pixmap.width() == 100, f"Expected captured region width 100, got {pixmap.width()}"
        assert pixmap.height() == 100, f"Expected captured region height 100, got {pixmap.height()}"


class TestCaptureInvalidMonitor:
    def test_capture_invalid_monitor(self, qapp) -> None:
        with pytest.raises(ValueError, match="out of range"):
            ScreenCapture.capture_monitor(999)
