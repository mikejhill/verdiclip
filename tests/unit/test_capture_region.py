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
        assert flags & Qt.WindowType.FramelessWindowHint, (
            f"Expected flags & Qt.WindowType.FramelessWindowHint to be truthy,"
            f" got {flags & Qt.WindowType.FramelessWindowHint}"
        )
        assert flags & Qt.WindowType.WindowStaysOnTopHint, (
            f"Expected flags & Qt.WindowType.WindowStaysOnTopHint to be truthy,"
            f" got {flags & Qt.WindowType.WindowStaysOnTopHint}"
        )
        assert flags & Qt.WindowType.Tool, (
            f"Expected flags & Qt.WindowType.Tool to be truthy, got {flags & Qt.WindowType.Tool}"
        )

    def test_cursor_is_cross(self, qapp) -> None:
        selector = RegionSelector()
        assert selector.cursor().shape() == Qt.CursorShape.CrossCursor, (
            f"Expected selector.cursor().shape() to equal Qt.CursorShape.CrossCursor,"
            f" got {selector.cursor().shape()}"
        )

    def test_initial_state(self, qapp) -> None:
        selector = RegionSelector()
        assert selector._selection_rect() is None, (
            f"Expected _selection_rect() to be None initially, got {selector._selection_rect()}"
        )


class TestRegionSelectorSelectionRect:
    def test_returns_none_when_no_selection(self, qapp) -> None:
        selector = RegionSelector()
        assert selector._selection_rect() is None, (
            f"Expected selector._selection_rect() to be None, got {selector._selection_rect()}"
        )

    def test_returns_none_when_only_origin(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        assert selector._selection_rect() is None, (
            f"Expected selector._selection_rect() to be None, got {selector._selection_rect()}"
        )

    def test_returns_normalized_rect(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(10, 10)
        selector._current = QPoint(110, 110)
        rect = selector._selection_rect()
        assert isinstance(rect, QRect), f"Expected rect to be instance of QRect, got {type(rect)}"
        assert rect.left() == 10, f"Expected rect.left() to equal 10, got {rect.left()}"
        assert rect.top() == 10, f"Expected rect.top() to equal 10, got {rect.top()}"
        assert rect.width() == 101, f"Expected rect.width() to equal 101, got {rect.width()}"
        assert rect.height() == 101, f"Expected rect.height() to equal 101, got {rect.height()}"

    def test_normalizes_inverted_selection(self, qapp) -> None:
        selector = RegionSelector()
        selector._origin = QPoint(110, 110)
        selector._current = QPoint(10, 10)
        rect = selector._selection_rect()
        # QRect.normalized() adjusts so left <= right, top <= bottom
        assert rect.left() <= rect.right(), (
            f"Expected rect.left() <= rect.right(), got {rect.left()}"
        )
        assert rect.top() <= rect.bottom(), (
            f"Expected rect.top() <= rect.bottom(), got {rect.top()}"
        )
        assert rect.width() > 0, f"Expected rect.width() > 0, got {rect.width()}"
        assert rect.height() > 0, f"Expected rect.height() > 0, got {rect.height()}"


class TestRegionSelectorMousePress:
    def test_left_button_sets_origin_and_selecting(self, qapp) -> None:
        selector = RegionSelector()
        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(50, 75)
        event.position.return_value = mock_pos

        selector.mousePressEvent(event)

        assert selector._origin == QPoint(50, 75), (
            f"Expected selector._origin to equal QPoint(50, 75), got {selector._origin}"
        )
        assert selector._is_selecting is True, (
            f"Expected selector._is_selecting to be True, got {selector._is_selecting}"
        )


class TestRegionSelectorMouseMove:
    def test_updates_current(self, qapp) -> None:
        selector = RegionSelector()
        event = MagicMock()
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(200, 300)
        event.position.return_value = mock_pos

        selector.mouseMoveEvent(event)

        assert selector._current == QPoint(200, 300), (
            f"Expected selector._current to equal QPoint(200, 300), got {selector._current}"
        )


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

        assert len(signal_received) == 1, (
            f"Expected len(signal_received) to equal 1, got {len(signal_received)}"
        )
        assert isinstance(signal_received[0], QRect), (
            f"Expected signal_received[0] to be instance of QRect, got {type(signal_received[0])}"
        )
        assert not selector.isVisible(), (
            f"Expected selector.isVisible() to be falsy, got {selector.isVisible()}"
        )

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

        assert len(signal_received) == 0, (
            f"Expected len(signal_received) to equal 0, got {len(signal_received)}"
        )
        assert selector._origin is None, (
            f"Expected selector._origin to be None, got {selector._origin}"
        )


class TestRegionSelectorKeyPress:
    def test_escape_emits_cancelled_and_hides(self, qapp) -> None:
        selector = RegionSelector()

        cancelled = []
        selector.selection_cancelled.connect(lambda: cancelled.append(True))

        event = MagicMock()
        event.key.return_value = Qt.Key.Key_Escape

        selector.keyPressEvent(event)

        assert len(cancelled) == 1, f"Expected len(cancelled) to equal 1, got {len(cancelled)}"
        assert not selector.isVisible(), (
            f"Expected selector.isVisible() to be falsy, got {selector.isVisible()}"
        )


class TestRegionCaptureInit:
    def test_initial_state(self) -> None:
        rc = RegionCapture()
        assert rc._selector is None, f"Expected rc._selector to be None, got {rc._selector}"
        assert rc._last_region is None, (
            f"Expected rc._last_region to be None, got {rc._last_region}"
        )

    def test_last_region_initially_none(self) -> None:
        rc = RegionCapture()
        assert rc.last_region is None, f"Expected rc.last_region to be None, got {rc.last_region}"


class TestRegionCaptureStartSelection:
    @patch("verdiclip.capture.region.ScreenCapture")
    def test_creates_selector(self, mock_screen, qapp) -> None:
        mock_screen.capture_all_monitors.return_value = QPixmap(100, 100)
        rc = RegionCapture()
        rc.start_selection()
        assert rc._selector is not None, f"Expected rc._selector to not be None, got {rc._selector}"
        assert isinstance(rc._selector, RegionSelector), (
            f"Expected rc._selector to be instance of RegionSelector, got {type(rc._selector)}"
        )


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
            assert painter.drawLine.call_count >= 2, (
                f"Expected painter.drawLine.call_count >= 2, got {painter.drawLine.call_count}"
            )

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
            assert label_pos.y() > 20, f"Expected label_pos.y() > 20, got {label_pos.y()}"

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
        assert mag.x() == 220, f"Expected mag.x() to equal 220, got {mag.x()}"
        assert mag.y() == 220, f"Expected mag.y() to equal 220, got {mag.y()}"

    def test_magnifier_right_edge_overflow(self, qapp) -> None:
        """Magnifier shifts left when cursor near right edge."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(750, 200))
        mag = painter.drawRect.call_args[0][0]
        # 750+20+120 > 800, so mag_x = 750-120-20 = 610
        assert mag.x() == 610, f"Expected mag.x() to equal 610, got {mag.x()}"

    def test_magnifier_bottom_edge_overflow(self, qapp) -> None:
        """Magnifier shifts up when cursor near bottom edge."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(200, 550))
        mag = painter.drawRect.call_args[0][0]
        # 550+20+120 > 600, so mag_y = 550-120-20 = 410
        assert mag.y() == 410, f"Expected mag.y() to equal 410, got {mag.y()}"

    def test_magnifier_draws_crosshair_lines(self, qapp) -> None:
        """Magnifier draws two crosshair lines at center."""
        selector = RegionSelector()
        selector._background = QPixmap(800, 600)
        selector.resize(800, 600)
        painter = MagicMock()
        selector._draw_magnifier(painter, QPoint(200, 200))
        assert painter.drawLine.call_count == 2, (
            f"Expected painter.drawLine.call_count to equal 2, got {painter.drawLine.call_count}"
        )


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

        assert len(captured) == 1, f"Expected len(captured) to equal 1, got {len(captured)}"
        assert rc.last_region == test_rect, (
            f"Expected rc.last_region to equal test_rect, got {rc.last_region}"
        )
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

        assert len(cancelled) == 1, f"Expected len(cancelled) to equal 1, got {len(cancelled)}"


class TestRegionSelectorMultiMonitor:
    """Tests for multi-monitor support using virtual desktop geometry."""

    @patch("verdiclip.capture.region.ScreenCapture")
    def test_start_uses_virtual_geometry_instead_of_show_full_screen(
        self, mock_screen, qapp
    ) -> None:
        """start() sets widget geometry to virtual desktop, not showFullScreen."""
        mock_screen.capture_all_monitors.return_value = QPixmap(5760, 1080)
        mock_primary = MagicMock()
        mock_primary.virtualGeometry.return_value = QRect(-1920, 0, 5760, 1080)
        with patch(
            "PySide6.QtWidgets.QApplication.primaryScreen",
            return_value=mock_primary,
        ):
            selector = RegionSelector()
            selector.start()

            geo = selector.geometry()
            assert geo.x() == -1920, (
                f"Expected geometry x to equal -1920 (virtual left edge), got {geo.x()}"
            )
            assert geo.y() == 0, (
                f"Expected geometry y to equal 0, got {geo.y()}"
            )
            assert geo.width() == 5760, (
                f"Expected geometry width to equal 5760 (total virtual width), got {geo.width()}"
            )
            assert geo.height() == 1080, (
                f"Expected geometry height to equal 1080, got {geo.height()}"
            )

    @patch("verdiclip.capture.region.ScreenCapture")
    def test_virtual_offset_stored_from_geometry_top_left(
        self, mock_screen, qapp
    ) -> None:
        """start() stores _virtual_offset from the virtual geometry's top-left corner."""
        mock_screen.capture_all_monitors.return_value = QPixmap(5760, 1080)
        mock_primary = MagicMock()
        mock_primary.virtualGeometry.return_value = QRect(-1920, 0, 5760, 1080)
        with patch(
            "PySide6.QtWidgets.QApplication.primaryScreen",
            return_value=mock_primary,
        ):
            selector = RegionSelector()
            selector.start()

            offset = selector._virtual_offset
            assert offset.x() == -1920, (
                f"Expected _virtual_offset.x() to equal -1920, got {offset.x()}"
            )
            assert offset.y() == 0, (
                f"Expected _virtual_offset.y() to equal 0, got {offset.y()}"
            )

    @patch("verdiclip.capture.region.ScreenCapture")
    def test_mouse_release_emits_coordinates_translated_by_virtual_offset(
        self, mock_screen, qapp
    ) -> None:
        """mouseReleaseEvent emits screen_rect translated by _virtual_offset."""
        mock_screen.capture_all_monitors.return_value = QPixmap(5760, 1080)
        mock_primary = MagicMock()
        mock_primary.virtualGeometry.return_value = QRect(-1920, 0, 5760, 1080)
        with patch(
            "PySide6.QtWidgets.QApplication.primaryScreen",
            return_value=mock_primary,
        ):
            selector = RegionSelector()
            selector.start()

            # Simulate a selection in widget coordinates
            selector._origin = QPoint(100, 100)
            selector._current = QPoint(300, 300)
            selector._is_selecting = True

            emitted = []
            selector.region_selected.connect(lambda r: emitted.append(r))

            event = MagicMock()
            event.button.return_value = Qt.MouseButton.LeftButton
            mock_pos = MagicMock()
            mock_pos.toPoint.return_value = QPoint(300, 300)
            event.position.return_value = mock_pos

            selector.mouseReleaseEvent(event)

            assert len(emitted) == 1, (
                f"Expected exactly 1 signal emission, got {len(emitted)}"
            )
            rect = emitted[0]
            # Widget rect (100,100)→(300,300) translated by offset (-1920, 0)
            assert rect.x() == 100 + (-1920), (
                f"Expected emitted rect x to equal {100 + (-1920)} (widget x + offset),"
                f" got {rect.x()}"
            )
            assert rect.y() == 100 + 0, (
                f"Expected emitted rect y to equal 100 (widget y + offset 0),"
                f" got {rect.y()}"
            )
            assert rect.width() == 201, (
                f"Expected emitted rect width to equal 201, got {rect.width()}"
            )
            assert rect.height() == 201, (
                f"Expected emitted rect height to equal 201, got {rect.height()}"
            )
