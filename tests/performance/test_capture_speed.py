"""Performance benchmarks for screen capture."""

from __future__ import annotations

from PySide6.QtCore import QRect

from verdiclip.capture.screen import ScreenCapture


class TestFullscreenCaptureSpeed:
    def test_fullscreen_capture_speed(self, qapp, benchmark) -> None:
        result = benchmark(ScreenCapture.capture_primary_monitor)
        assert not result.isNull()
        # pytest-benchmark default: verify mean < 200ms
        assert benchmark.stats.stats.mean < 0.200


class TestRegionCaptureSpeed:
    def test_region_capture_speed(self, qapp, benchmark) -> None:
        rect = QRect(0, 0, 100, 100)
        result = benchmark(ScreenCapture.capture_region, rect)
        assert not result.isNull()
        assert benchmark.stats.stats.mean < 0.200
