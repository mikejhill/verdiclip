"""Interactive window picker overlay — hover to highlight, click to capture."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture.screen import ScreenCapture
from verdiclip.capture.window import WindowCapture

logger = logging.getLogger(__name__)


class WindowPickerOverlay(QWidget):
    """Fullscreen transparent overlay that highlights windows under the cursor."""

    window_selected = Signal(int)  # Emits hwnd of clicked window
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        self._windows: list[tuple[int, str, QRect]] = []
        self._hovered_hwnd: int = 0
        self._hovered_rect: QRect | None = None
        self._background: QPixmap | None = None
        self._virtual_offset = QPoint(0, 0)

    def start(self) -> None:
        """Capture background and show the overlay across all monitors."""
        self._background = ScreenCapture.capture_all_monitors()
        self._windows = WindowCapture.enumerate_visible_windows(
            exclude_hwnd=int(self.winId()),
        )

        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(virtual_geo)
        self._virtual_offset = virtual_geo.topLeft()
        self.show()
        self.activateWindow()
        self.raise_()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if self._background:
            painter.drawPixmap(0, 0, self._background)

        # Dim the entire screen
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        # Highlight the hovered window
        if self._hovered_rect:
            local_rect = self._hovered_rect.translated(-self._virtual_offset)
            # Draw the un-dimmed window area
            if self._background:
                painter.drawPixmap(local_rect, self._background, local_rect)
            # Draw a highlight border
            pen = QPen(QColor(0, 174, 255), 3)
            painter.setPen(pen)
            painter.drawRect(local_rect)

        # Instruction text
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setPointSize(14)
        painter.setFont(font)
        msg = "Click a window to capture it. Press Escape to cancel."
        if self._hovered_rect:
            title = ""
            for hwnd, t, _r in self._windows:
                if hwnd == self._hovered_hwnd:
                    title = t
                    break
            if title:
                msg = f'"{title}" — Click to capture. Escape to cancel.'
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                         f"\n{msg}")
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        screen_pos = event.position().toPoint() + self._virtual_offset
        best_hwnd = 0
        best_rect: QRect | None = None
        best_area = float("inf")

        for hwnd, _title, rect in self._windows:
            if rect.contains(screen_pos):
                area = rect.width() * rect.height()
                if area < best_area:
                    best_area = area
                    best_hwnd = hwnd
                    best_rect = rect

        if best_hwnd != self._hovered_hwnd:
            self._hovered_hwnd = best_hwnd
            self._hovered_rect = best_rect
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hovered_hwnd:
            self.hide()
            self.window_selected.emit(self._hovered_hwnd)
        elif event.button() == Qt.MouseButton.RightButton:
            self.hide()
            self.cancelled.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
            return

        step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
        dx, dy = 0, 0
        if event.key() == Qt.Key.Key_Left:
            dx = -step
        elif event.key() == Qt.Key.Key_Right:
            dx = step
        elif event.key() == Qt.Key.Key_Up:
            dy = -step
        elif event.key() == Qt.Key.Key_Down:
            dy = step

        if dx or dy:
            from PySide6.QtGui import QCursor

            new_pos = QCursor.pos() + QPoint(dx, dy)
            QCursor.setPos(new_pos)
            # Trigger the same hit-test logic as mouse movement
            self._current = self.mapFromGlobal(new_pos)
            screen_pos = new_pos
            best_hwnd = 0
            best_rect: QRect | None = None
            best_area = float("inf")
            for hwnd, _title, rect in self._windows:
                if rect.contains(screen_pos):
                    area = rect.width() * rect.height()
                    if area < best_area:
                        best_area = area
                        best_hwnd = hwnd
                        best_rect = rect
            if best_hwnd != self._hovered_hwnd:
                self._hovered_hwnd = best_hwnd
                self._hovered_rect = best_rect
            self.update()


class WindowPicker(QObject):
    """Manages the interactive window picker workflow."""

    window_captured = Signal(QPixmap)

    def __init__(self) -> None:
        super().__init__()
        self._overlay: WindowPickerOverlay | None = None

    def start(self) -> None:
        self._overlay = WindowPickerOverlay()
        self._overlay.window_selected.connect(self._on_window_selected)
        self._overlay.cancelled.connect(
            lambda: logger.info("Interactive window capture cancelled.")
        )
        self._overlay.start()

    def _on_window_selected(self, hwnd: int) -> None:
        pixmap = WindowCapture.capture_window_by_handle(hwnd, include_decorations=False)
        self.window_captured.emit(pixmap)
