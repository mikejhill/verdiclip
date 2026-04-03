"""Tests for verdiclip.capture.window_picker — WindowPickerOverlay and WindowPicker."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QCursor, QPixmap

from verdiclip.capture.window_picker import WindowPicker, WindowPickerOverlay

# ---------------------------------------------------------------------------
# WindowPickerOverlay — initialisation
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayInit:
    def test_window_flags_include_frameless(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        flags = overlay.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint, (
            f"Expected FramelessWindowHint in flags, got {flags}"
        )

    def test_window_flags_include_stays_on_top(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        flags = overlay.windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint, (
            f"Expected WindowStaysOnTopHint in flags, got {flags}"
        )

    def test_window_flags_include_tool(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        flags = overlay.windowFlags()
        assert flags & Qt.WindowType.Tool, f"Expected Tool flag in flags, got {flags}"

    def test_cursor_is_cross(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        assert overlay.cursor().shape() == Qt.CursorShape.CrossCursor, (
            f"Expected CrossCursor, got {overlay.cursor().shape()}"
        )

    def test_mouse_tracking_enabled(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        assert overlay.hasMouseTracking() is True, (
            f"Expected mouse tracking enabled, got {overlay.hasMouseTracking()}"
        )

    def test_windows_list_empty_initially(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        assert overlay._windows == [], f"Expected _windows to be empty list, got {overlay._windows}"

    def test_hovered_hwnd_zero_initially(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        assert overlay._hovered_hwnd == 0, (
            f"Expected _hovered_hwnd to be 0, got {overlay._hovered_hwnd}"
        )

    def test_hovered_rect_none_initially(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        assert overlay._hovered_rect is None, (
            f"Expected _hovered_rect to be None, got {overlay._hovered_rect}"
        )


# ---------------------------------------------------------------------------
# WindowPickerOverlay — mouse press events
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayMousePress:
    def test_left_click_with_hovered_hwnd_emits_window_selected(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._hovered_hwnd = 12345
        overlay._hovered_rect = QRect(10, 20, 640, 480)

        emissions: list[tuple[int, QRect]] = []
        overlay.window_selected.connect(lambda h, r: emissions.append((h, r)))

        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        overlay.mousePressEvent(event)

        assert len(emissions) == 1, f"Expected 1 window_selected emission, got {len(emissions)}"
        assert emissions[0][0] == 12345, f"Expected hwnd 12345 in signal, got {emissions[0][0]}"
        assert emissions[0][1] == QRect(10, 20, 640, 480), (
            f"Expected window rect in signal, got {emissions[0][1]}"
        )

    def test_left_click_without_hovered_hwnd_does_not_emit(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._hovered_hwnd = 0

        emissions: list[tuple[int, QRect]] = []
        overlay.window_selected.connect(lambda h, r: emissions.append((h, r)))

        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        overlay.mousePressEvent(event)

        assert len(emissions) == 0, f"Expected no window_selected emission, got {len(emissions)}"

    def test_right_click_emits_cancelled(self, qapp) -> None:
        overlay = WindowPickerOverlay()

        cancelled: list[bool] = []
        overlay.cancelled.connect(lambda: cancelled.append(True))

        event = MagicMock()
        event.button.return_value = Qt.MouseButton.RightButton
        overlay.mousePressEvent(event)

        assert len(cancelled) == 1, f"Expected 1 cancelled emission, got {len(cancelled)}"


# ---------------------------------------------------------------------------
# WindowPickerOverlay — key press events
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayKeyPress:
    def test_escape_emits_cancelled(self, qapp) -> None:
        overlay = WindowPickerOverlay()

        cancelled: list[bool] = []
        overlay.cancelled.connect(lambda: cancelled.append(True))

        event = MagicMock()
        event.key.return_value = Qt.Key.Key_Escape
        overlay.keyPressEvent(event)

        assert len(cancelled) == 1, f"Expected 1 cancelled emission on Escape, got {len(cancelled)}"

    @pytest.mark.skipif(
        not hasattr(QCursor, "pos"),
        reason="QCursor.pos/setPos requires a display",
    )
    def test_arrow_key_moves_cursor_by_1_pixel(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._windows = []

        event = MagicMock()
        event.key.return_value = Qt.Key.Key_Right
        event.modifiers.return_value = Qt.KeyboardModifier.NoModifier

        with (
            patch.object(QCursor, "pos", return_value=QPoint(100, 100)),
            patch.object(QCursor, "setPos") as mock_set_pos,
        ):
            overlay.keyPressEvent(event)
            mock_set_pos.assert_called_once()
            new_pos = mock_set_pos.call_args[0][0]
            assert new_pos == QPoint(101, 100), (
                f"Expected cursor moved to (101, 100), got ({new_pos.x()}, {new_pos.y()})"
            )

    @pytest.mark.skipif(
        not hasattr(QCursor, "pos"),
        reason="QCursor.pos/setPos requires a display",
    )
    def test_ctrl_arrow_moves_cursor_by_10_pixels(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._windows = []

        event = MagicMock()
        event.key.return_value = Qt.Key.Key_Down
        event.modifiers.return_value = Qt.KeyboardModifier.ControlModifier

        with (
            patch.object(QCursor, "pos", return_value=QPoint(100, 100)),
            patch.object(QCursor, "setPos") as mock_set_pos,
        ):
            overlay.keyPressEvent(event)
            mock_set_pos.assert_called_once()
            new_pos = mock_set_pos.call_args[0][0]
            assert new_pos == QPoint(100, 110), (
                f"Expected cursor moved to (100, 110), got ({new_pos.x()}, {new_pos.y()})"
            )


# ---------------------------------------------------------------------------
# WindowPickerOverlay — hit-test / mouse move
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayHitTest:
    def test_smallest_window_containing_point_is_selected(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._virtual_offset = QPoint(0, 0)

        big_rect = QRect(0, 0, 800, 600)
        small_rect = QRect(100, 100, 200, 150)
        overlay._windows = [
            (1, "Big Window", big_rect),
            (2, "Small Window", small_rect),
        ]

        event = MagicMock()
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(150, 150)
        event.position.return_value = mock_pos

        overlay.mouseMoveEvent(event)

        assert overlay._hovered_hwnd == 2, (
            f"Expected smallest window hwnd=2, got {overlay._hovered_hwnd}"
        )
        assert overlay._hovered_rect == small_rect, (
            f"Expected hovered rect {small_rect}, got {overlay._hovered_rect}"
        )

    def test_no_window_at_point_clears_hover(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._virtual_offset = QPoint(0, 0)
        overlay._hovered_hwnd = 99
        overlay._hovered_rect = QRect(0, 0, 10, 10)

        overlay._windows = [
            (1, "Window", QRect(0, 0, 100, 100)),
        ]

        event = MagicMock()
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(500, 500)
        event.position.return_value = mock_pos

        overlay.mouseMoveEvent(event)

        assert overlay._hovered_hwnd == 0, (
            f"Expected hovered hwnd cleared to 0, got {overlay._hovered_hwnd}"
        )
        assert overlay._hovered_rect is None, (
            f"Expected hovered rect cleared to None, got {overlay._hovered_rect}"
        )

    def test_mouse_move_updates_hovered_state(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        overlay._virtual_offset = QPoint(0, 0)

        rect = QRect(50, 50, 200, 200)
        overlay._windows = [(42, "Test Window", rect)]

        event = MagicMock()
        mock_pos = MagicMock()
        mock_pos.toPoint.return_value = QPoint(100, 100)
        event.position.return_value = mock_pos

        overlay.mouseMoveEvent(event)

        assert overlay._hovered_hwnd == 42, f"Expected hovered hwnd 42, got {overlay._hovered_hwnd}"
        assert overlay._hovered_rect == rect, (
            f"Expected hovered rect {rect}, got {overlay._hovered_rect}"
        )


# ---------------------------------------------------------------------------
# WindowPickerOverlay — start
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayStart:
    @patch("verdiclip.capture.window_picker.WindowCapture")
    @patch("verdiclip.capture.window_picker.ScreenCapture")
    def test_start_captures_background_and_enumerates_windows(
        self, mock_screen, mock_wincap, qapp
    ) -> None:
        mock_screen.capture_all_monitors.return_value = QPixmap(1920, 1080)
        mock_wincap.enumerate_visible_windows.return_value = [
            (1, "Window 1", QRect(0, 0, 400, 300)),
        ]

        overlay = WindowPickerOverlay()
        # Mock the show/activate calls to avoid display dependency
        with (
            patch.object(overlay, "show"),
            patch.object(overlay, "activateWindow"),
            patch.object(overlay, "raise_"),
            patch.object(overlay, "setGeometry"),
        ):
            overlay.start()

        mock_screen.capture_all_monitors.assert_called_once()
        mock_wincap.enumerate_visible_windows.assert_called_once()
        assert overlay._background is not None, "Expected background to be captured after start()"
        assert len(overlay._windows) == 1, (
            f"Expected 1 window enumerated, got {len(overlay._windows)}"
        )


# ---------------------------------------------------------------------------
# WindowPickerOverlay — paintEvent
# ---------------------------------------------------------------------------


class TestWindowPickerOverlayPaintEvent:
    def test_paint_no_crash_without_background(self, qapp) -> None:
        overlay = WindowPickerOverlay()
        event = MagicMock()
        with patch("verdiclip.capture.window_picker.QPainter") as mock_cls:
            painter = mock_cls.return_value
            overlay.paintEvent(event)
            mock_cls.assert_called_once_with(overlay)
            painter.end.assert_called_once()


# ---------------------------------------------------------------------------
# WindowPicker — workflow
# ---------------------------------------------------------------------------


class TestWindowPickerStart:
    @patch("verdiclip.capture.window_picker.WindowPickerOverlay")
    def test_start_creates_overlay_and_connects_signals(self, mock_overlay_cls, qapp) -> None:
        mock_overlay = mock_overlay_cls.return_value
        mock_overlay.window_selected = MagicMock()
        mock_overlay.cancelled = MagicMock()

        picker = WindowPicker()
        picker.start()

        mock_overlay_cls.assert_called_once()
        mock_overlay.window_selected.connect.assert_called_once()
        mock_overlay.cancelled.connect.assert_called_once()
        mock_overlay.start.assert_called_once()


class TestWindowPickerOnWindowSelected:
    def test_on_window_selected_crops_from_frozen_background(self, qapp) -> None:
        picker = WindowPicker()
        # Set up overlay with a frozen background
        overlay = WindowPickerOverlay()
        bg = QPixmap(1920, 1080)
        bg.fill(Qt.GlobalColor.green)
        overlay._background = bg
        overlay._virtual_offset = QPoint(0, 0)
        picker._overlay = overlay

        captured: list[QPixmap] = []
        picker.window_captured.connect(lambda px: captured.append(px))

        window_rect = QRect(100, 100, 640, 480)
        picker._on_window_selected(12345, window_rect)

        assert len(captured) == 1, f"Expected 1 window_captured emission, got {len(captured)}"
        result = captured[0]
        assert not result.isNull(), "Expected captured pixmap to not be null"
        assert result.width() == 640, f"Expected cropped width 640, got {result.width()}"
        assert result.height() == 480, f"Expected cropped height 480, got {result.height()}"

    @patch("verdiclip.capture.window_picker.WindowCapture")
    def test_on_window_selected_falls_back_to_live_capture(self, mock_wincap, qapp) -> None:
        """Falls back to live capture when background is unavailable."""
        fake_pixmap = QPixmap(640, 480)
        mock_wincap.capture_window_by_handle.return_value = fake_pixmap

        picker = WindowPicker()
        picker._overlay = None  # No overlay (background unavailable)

        captured: list[QPixmap] = []
        picker.window_captured.connect(lambda px: captured.append(px))

        picker._on_window_selected(12345, QRect())

        mock_wincap.capture_window_by_handle.assert_called_once_with(
            12345, include_decorations=False
        )
        assert len(captured) == 1, f"Expected 1 window_captured emission, got {len(captured)}"
