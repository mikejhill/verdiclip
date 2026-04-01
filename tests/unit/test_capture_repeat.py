"""Tests for verdiclip.capture.repeat — CaptureType, LastCapture, RepeatCapture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap

from verdiclip.capture.repeat import CaptureType, LastCapture, RepeatCapture


class TestCaptureType:
    def test_region_exists(self) -> None:
        assert CaptureType.REGION is not None

    def test_fullscreen_exists(self) -> None:
        assert CaptureType.FULLSCREEN is not None

    def test_active_window_exists(self) -> None:
        assert CaptureType.ACTIVE_WINDOW is not None

    def test_window_pick_exists(self) -> None:
        assert CaptureType.WINDOW_PICK is not None

    def test_has_exactly_four_members(self) -> None:
        assert len(CaptureType) == 4


class TestLastCapture:
    def test_creation_with_type(self) -> None:
        lc = LastCapture(capture_type=CaptureType.FULLSCREEN)
        assert lc.capture_type == CaptureType.FULLSCREEN
        assert lc.region is None

    def test_creation_with_region(self) -> None:
        rect = QRect(10, 20, 300, 400)
        lc = LastCapture(capture_type=CaptureType.REGION, region=rect)
        assert lc.capture_type == CaptureType.REGION
        assert lc.region == rect


class TestRepeatCaptureInitial:
    def test_can_repeat_false_initially(self) -> None:
        rc = RepeatCapture()
        assert rc.can_repeat() is False

    def test_last_capture_type_none_initially(self) -> None:
        rc = RepeatCapture()
        assert rc.last_capture_type is None


class TestRepeatCaptureRecord:
    def test_record_fullscreen_makes_repeatable(self) -> None:
        rc = RepeatCapture()
        rc.record(CaptureType.FULLSCREEN)
        assert rc.can_repeat() is True

    def test_record_fullscreen_sets_type(self) -> None:
        rc = RepeatCapture()
        rc.record(CaptureType.FULLSCREEN)
        assert rc.last_capture_type == CaptureType.FULLSCREEN

    def test_record_region_with_rect(self) -> None:
        rc = RepeatCapture()
        rect = QRect(10, 20, 100, 200)
        rc.record(CaptureType.REGION, region=rect)
        assert rc.last_capture_type == CaptureType.REGION
        assert rc._last.region == rect


class TestRepeatCaptureRepeat:
    def test_repeat_returns_none_when_no_previous(self) -> None:
        rc = RepeatCapture()
        assert rc.repeat() is None

    @patch("verdiclip.capture.repeat.ScreenCapture")
    def test_repeat_fullscreen(self, mock_screen, qapp) -> None:
        fake_pixmap = QPixmap(800, 600)
        mock_screen.capture_all_monitors.return_value = fake_pixmap

        rc = RepeatCapture()
        rc.record(CaptureType.FULLSCREEN)
        result = rc.repeat()

        mock_screen.capture_all_monitors.assert_called_once()
        assert result is fake_pixmap

    @patch("verdiclip.capture.repeat.WindowCapture")
    def test_repeat_active_window(self, mock_wincap, qapp) -> None:
        fake_pixmap = QPixmap(640, 480)
        mock_wincap.capture_active_window.return_value = fake_pixmap

        rc = RepeatCapture()
        rc.record(CaptureType.ACTIVE_WINDOW)
        result = rc.repeat()

        mock_wincap.capture_active_window.assert_called_once()
        assert result is fake_pixmap

    @patch("verdiclip.capture.repeat.ScreenCapture")
    def test_repeat_region_with_saved_region(self, mock_screen, qapp) -> None:
        fake_pixmap = QPixmap(300, 200)
        mock_screen.capture_region.return_value = fake_pixmap

        rect = QRect(50, 50, 300, 200)
        rc = RepeatCapture()
        rc.record(CaptureType.REGION, region=rect)
        result = rc.repeat()

        mock_screen.capture_region.assert_called_once_with(rect)
        assert result is fake_pixmap

    def test_repeat_region_without_saved_region_calls_callback(self) -> None:
        rc = RepeatCapture()
        rc.record(CaptureType.REGION)

        callback = MagicMock()
        result = rc.repeat(on_region_needed=callback)

        callback.assert_called_once()
        assert result is None

    def test_repeat_region_without_saved_region_no_callback(self) -> None:
        rc = RepeatCapture()
        rc.record(CaptureType.REGION)
        result = rc.repeat()
        assert result is None

    def test_repeat_window_pick_calls_callback(self) -> None:
        rc = RepeatCapture()
        rc.record(CaptureType.WINDOW_PICK)

        callback = MagicMock()
        result = rc.repeat(on_region_needed=callback)

        callback.assert_called_once()
        assert result is None
