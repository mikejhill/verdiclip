"""Tests for verdiclip.capture.screen.ScreenCapture."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect

from verdiclip.capture.screen import ScreenCapture


class TestCapturePrimaryMonitor:
    def test_capture_primary_monitor(self, qapp) -> None:
        pixmap = ScreenCapture.capture_primary_monitor()
        assert not pixmap.isNull()
        assert pixmap.width() > 0
        assert pixmap.height() > 0


class TestCaptureAllMonitors:
    def test_capture_all_monitors(self, qapp) -> None:
        pixmap = ScreenCapture.capture_all_monitors()
        assert not pixmap.isNull()
        assert pixmap.width() > 0
        assert pixmap.height() > 0


class TestGetMonitorCount:
    def test_get_monitor_count(self) -> None:
        count = ScreenCapture.get_monitor_count()
        assert isinstance(count, int)
        assert count >= 1


class TestCaptureRegion:
    def test_capture_region(self, qapp) -> None:
        rect = QRect(0, 0, 100, 100)
        pixmap = ScreenCapture.capture_region(rect)
        assert not pixmap.isNull()
        assert pixmap.width() == 100
        assert pixmap.height() == 100


class TestCaptureInvalidMonitor:
    def test_capture_invalid_monitor(self, qapp) -> None:
        with pytest.raises(ValueError, match="out of range"):
            ScreenCapture.capture_monitor(999)
