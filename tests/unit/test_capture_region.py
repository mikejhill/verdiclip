"""Tests for verdiclip.capture.region — RegionSelector and RegionCapture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap

from verdiclip.capture.region import RegionCapture, RegionSelector


class TestRegionSelectorInit:
    def test_window_flags(self, qapp) -> None:
        selector = RegionSelector()
        flags = selector.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.Tool

    def test_cursor_is_cross(self, qapp) -> None:
        selector = RegionSelector()
        assert selector.cursor().shape() == Qt.CursorShape.CrossCursor

    def test_initial_state(self, qapp) -> None:
        selector = RegionSelector()
        assert selector._origin is None
        assert selector._current is None
        assert selector._is_selecting is False


class TestRegionSelectorSelectionRect:
    def test_returns_none_when_no_selection(self, qapp) -> None:
        selector = RegionSelector()
        assert selector._selection_rect() is None

    def test_returns_none_when_only_origin(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        assert selector._selection_rect() is None

    def test_returns_normalized_rect(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        selector._current = QPoint(110, 110)
        rect = selector._selection_rect()
        assert isinstance(rect, QRect)
        assert rect.left() == 10
        assert rect.top() == 10
        assert rect.width() == 101
        assert rect.height() == 101

    def test_normalizes_inverted_selection(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(110, 110)
        selector._current = QPoint(10, 10)
        rect = selector._selection_rect()
        # QRect.normalized() adjusts so left <= right, top <= bottom
        assert rect.left() <= rect.right()
        assert rect.top() <= rect.bottom()
        assert rect.width() > 0
        assert rect.height() > 0


class TestRegionSelectorMousePress:
    def test_left_button_sets_origin_and_selecting(self, qapp) -> None:
        selector = RegionSelector()
        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(50, 75)
        event.position.return_value = mock_pos

        selector.mousePressEvent(event)

        assert selector._origin == QPoint(50, 75)
        assert selector._is_selecting is True


class TestRegionSelectorMouseMove:
    def test_updates_current(self, qapp) -> None:
        selector = RegionSelector()
        event = MagicMock()
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(200, 300)
        event.position.return_value = mock_pos

        selector.mouseMoveEvent(event)

        assert selector._current == QPoint(200, 300)


class TestRegionSelectorMouseRelease:
    def test_valid_selection_emits_signal_and_hides(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        selector._current = QPoint(100, 100)
        selector._is_selecting = True

        signal_received = []
        selector.region_selected.connect(lambda r: signal_received.append(r))

        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(100, 100)
        event.position.return_value = mock_pos

        selector.mouseReleaseEvent(event)

        assert len(signal_received) == 1
        assert isinstance(signal_received[0], QRect)
        assert not selector.isVisible()

    def test_tiny_selection_resets_origin(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        selector._current = QPoint(12, 12)
        selector._is_selecting = True

        signal_received = []
        selector.region_selected.connect(lambda r: signal_received.append(r))

        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(12, 12)
        event.position.return_value = mock_pos

        selector.mouseReleaseEvent(event)

        assert len(signal_received) == 0
        assert selector._origin is None


class TestRegionSelectorKeyPress:
    def test_escape_emits_cancelled_and_hides(self, qapp) -> None:
        selector = RegionSelector()

        cancelled = []
        selector.selection_cancelled.connect(lambda: cancelled.append(True))

        event = MagicMock()
        event.key.return_value = Qt.Key.Key_Escape

        selector.keyPressEvent(event)

        assert len(cancelled) == 1
        assert not selector.isVisible()


class TestRegionCaptureInit:
    def test_initial_state(self) -> None:
        rc = RegionCapture()
        assert rc._selector is None
        assert rc._last_region is None

    def test_last_region_initially_none(self) -> None:
        rc = RegionCapture()
        assert rc.last_region is None


class TestRegionCaptureStartSelection:
    @patch("verdiclip.capture.region.ScreenCapture")
    def test_creates_selector(self, mock_screen, qapp) -> None:
        mock_screen.capture_all_monitors.return_value = QPixmap(100, 100)
        rc = RegionCapture()
        rc.start_selection()
        assert rc._selector is not None
        assert isinstance(rc._selector, RegionSelector)


class TestRegionSelectorPaintEvent:
    """Tests for paintEvent covering overlay, selection, crosshair."""

    def test_paint_no_background(self, qapp) -> None:
        """No crash when _background is None."""
        selector = RegionSelector()
        event = MagicMock()
        with patch("verdiclip.capture.region.QPainter") as mock_cls:
            painter = mock_cls.return_value
            selector.paintEvent(event)
            mock_cls.assert_called_once_with(selector)
            painter.fillRect.assert_called_once()
            painter.end.assert_called_once()

    def test_paint_draws_background(self, qapp) -> None:
        """Background pixmap is drawn when available."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        event = MagicMock()
        with patch("verdiclip.capture.region.QPainter") as mock_cls:
            painter = mock_cls.return_value
            selector.paintEvent(event)
            painter.drawPixmap.assert_any_call(
                0, 0, selector._background
            )

    def test_paint_crosshair_when_not_selecting(self, qapp) -> None:
        """Crosshair lines drawn when cursor set, not selecting."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector._current = QPoint(400, 300)
        selector._is_selecting = False
        selector.resize(800, 600)
        event = MagicMock()
        with patch("verdiclip.capture.region.QPainter") as mock_cls:
            painter = mock_cls.return_value
            selector.paintEvent(event)
            # Crosshair (2) + magnifier crosshair (2) = 4+
            assert painter.drawLine.call_count >= 2

    def test_paint_selection_rect_and_label(self, qapp) -> None:
        """Active selection draws rect border and dimension label."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector._origin = QPoint(100, 100)
        selector._current = QPoint(300, 250)
        selector._is_selecting = True
        selector.resize(800, 600)
        event = MagicMock()
        with patch("verdiclip.capture.region.QPainter") as mock_cls:
            painter = mock_cls.return_value
            selector.paintEvent(event)
            painter.drawRect.assert_called()
            painter.drawText.assert_called()

    def test_paint_label_repositioned_near_top(self, qapp) -> None:
        """Label moves below selection when near screen top."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector._origin = QPoint(100, 5)
        selector._current = QPoint(300, 100)
        selector._is_selecting = True
        selector.resize(800, 600)
        event = MagicMock()
        with patch("verdiclip.capture.region.QPainter") as mock_cls:
            painter = mock_cls.return_value
            selector.paintEvent(event)
            label_pos = painter.drawText.call_args[0][0]
            # top=5 → 5-6=-1 < 20 → label at bottom+18
            assert label_pos.y() > 20

    def test_paint_calls_magnifier(self, qapp) -> None:
        """Magnifier is invoked when cursor and background exist."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector._current = QPoint(400, 300)
        selector.resize(800, 600)
        event = MagicMock()
        with (
            patch("verdiclip.capture.region.QPainter") as mock_cls,
            patch.object(selector, "_draw_magnifier") as mag,
        ):
            selector.paintEvent(event)
            mag.assert_called_once_with(
                mock_cls.return_value, QPoint(400, 300)
            )


class TestRegionSelectorDrawMagnifier:
    """Tests for _draw_magnifier edge positioning."""

    def test_magnifier_normal_position(self, qapp) -> None:
        """Magnifier placed to bottom-right of cursor."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(200, 200))
        painter.drawPixmap.assert_called_once()
        painter.drawRect.assert_called_once()
        mag = painter.drawRect.call_args[0][0]
        assert mag.x() == 220
        assert mag.y() == 220

    def test_magnifier_right_edge_overflow(self, qapp) -> None:
        """Magnifier shifts left when cursor near right edge."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(750, 200))
        mag = painter.drawRect.call_args[0][0]
        # 750+20+120 > 800, so mag_x = 750-120-20 = 610
        assert mag.x() == 610

    def test_magnifier_bottom_edge_overflow(self, qapp) -> None:
        """Magnifier shifts up when cursor near bottom edge."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(200, 550))
        mag = painter.drawRect.call_args[0][0]
        # 550+20+120 > 600, so mag_y = 550-120-20 = 410
        assert mag.y() == 410

    def test_magnifier_draws_crosshair_lines(self, qapp) -> None:
        """Magnifier draws two crosshair lines at center."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(200, 200))
        assert painter.drawLine.call_count == 2


class TestRegionCaptureWorkflow:
    """Tests for RegionCapture signal handling (lines 200-208)."""

    @patch("verdiclip.capture.region.ScreenCapture")
    def test_on_captured_callback_invoked(
        self, mock_screen, qapp
    ) -> None:
        """region_selected triggers capture and callback."""
        mock_screen.capture_all_monitors.return_value = QPixmap(
            100, 100
        )
        mock_screen.capture_region.return_value = QPixmap(50, 50)

        captured = []
        rc = RegionCapture()
        rc.start_selection(
            on_captured=lambda p: captured.append(p)
        )

        test_rect = QRect(10, 10, 50, 50)
        rc._selector.region_selected.emit(test_rect)

        assert len(captured) == 1
        assert rc.last_region == test_rect
        mock_screen.capture_region.assert_called_once_with(
            test_rect
        )

    @patch("verdiclip.capture.region.ScreenCapture")
    def test_on_cancelled_callback_invoked(
        self, mock_screen, qapp
    ) -> None:
        """selection_cancelled triggers cancel callback."""
        mock_screen.capture_all_monitors.return_value = QPixmap(
            100, 100
        )

        cancelled = []
        rc = RegionCapture()
        rc.start_selection(
            on_cancelled=lambda: cancelled.append(True)
        )
        rc._selector.selection_cancelled.emit()

        assert len(cancelled) == 1
