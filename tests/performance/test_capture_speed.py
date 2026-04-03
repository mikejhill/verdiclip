"""Performance benchmarks for screen capture."""

from __future__ import annotations

from PySide6.QtCore import QRect

from verdiclip.capture.screen import ScreenCapture


class TestFullscreenCaptureSpeed:
    def test_fullscreen_capture_speed(self, qapp, benchmark) -> None:
        result = benchmark(ScreenCapture.capture_primary_monitor)
        assert not result.isNull(), "Fullscreen capture returned a null pixmap"
        mean = benchmark.stats.stats.mean
        assert mean < 0.200, f"Fullscreen capture mean {mean:.3f}s exceeds 200ms target"


class TestRegionCaptureSpeed:
    def test_region_capture_speed(self, qapp, benchmark) -> None:
        rect = QRect(0, 0, 100, 100)
        result = benchmark(ScreenCapture.capture_region, rect)
        assert not result.isNull(), "Region capture returned a null pixmap"
        mean = benchmark.stats.stats.mean
        assert mean < 0.200, f"Region capture mean {mean:.3f}s exceeds 200ms target"
