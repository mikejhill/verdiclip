"""Region selection overlay for screenshot capture."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from verdiclip.capture.screen import ScreenCapture

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QPaintEvent

logger = logging.getLogger(__name__)

_OVERLAY_COLOR = QColor(0, 0, 0, 100)
_SELECTION_BORDER_COLOR = QColor(0, 120, 215)
_SELECTION_BORDER_WIDTH = 2
_CROSSHAIR_COLOR = QColor(200, 200, 200, 180)
_DIMENSION_FONT_SIZE = 11
_MAGNIFIER_SIZE = 120
_MAGNIFIER_ZOOM = 4


class RegionSelector(QWidget):
    """Full-screen transparent overlay for selecting a screen region."""

    region_selected = Signal(QRect)
    selection_cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._background: QPixmap | None = None
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._is_selecting = False

    def start(self) -> None:
        """Capture the screen and show the overlay."""
        self._background = ScreenCapture.capture_all_monitors()
        self.setGeometry(0, 0, self._background.width(), self._background.height())
        self.showFullScreen()
        self.activateWindow()
        logger.debug("Region selector overlay shown.")

    def _selection_rect(self) -> QRect | None:
        """Return the normalized selection rectangle, or None if not selecting."""
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the overlay with dimmed background and selection highlight."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._background:
            painter.drawPixmap(0, 0, self._background)

        # Dim overlay
        painter.fillRect(self.rect(), _OVERLAY_COLOR)

        selection = self._selection_rect()
        if selection and self._background:
            # Draw the un-dimmed selected region
            painter.drawPixmap(selection, self._background, selection)

            # Selection border
            pen = QPen(_SELECTION_BORDER_COLOR, _SELECTION_BORDER_WIDTH)
            pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(selection)

            # Dimension label
            label = f"{selection.width()} × {selection.height()}"
            font = QFont("Segoe UI", _DIMENSION_FONT_SIZE)
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.white)
            label_pos = QPoint(selection.left() + 4, selection.top() - 6)
            if label_pos.y() < 20:
                label_pos = QPoint(selection.left() + 4, selection.bottom() + 18)
            painter.drawText(label_pos, label)

        # Crosshair at cursor
        if self._current and not self._is_selecting:
            painter.setPen(QPen(_CROSSHAIR_COLOR, 1, Qt.PenStyle.DashLine))
            painter.drawLine(self._current.x(), 0, self._current.x(), self.height())
            painter.drawLine(0, self._current.y(), self.width(), self._current.y())

        # Magnifier
        if self._current and self._background:
            self._draw_magnifier(painter, self._current)

        painter.end()

    def _draw_magnifier(self, painter: QPainter, pos: QPoint) -> None:
        """Draw a magnified view of the area around the cursor."""
        src_size = _MAGNIFIER_SIZE // _MAGNIFIER_ZOOM
        src_rect = QRect(
            pos.x() - src_size // 2,
            pos.y() - src_size // 2,
            src_size, src_size,
        )

        # Position magnifier in a corner that doesn't overlap cursor
        mag_x = pos.x() + 20
        mag_y = pos.y() + 20
        if mag_x + _MAGNIFIER_SIZE > self.width():
            mag_x = pos.x() - _MAGNIFIER_SIZE - 20
        if mag_y + _MAGNIFIER_SIZE > self.height():
            mag_y = pos.y() - _MAGNIFIER_SIZE - 20

        mag_rect = QRect(mag_x, mag_y, _MAGNIFIER_SIZE, _MAGNIFIER_SIZE)

        if self._background:
            cropped = self._background.copy(src_rect)
            scaled = cropped.scaled(
                QSize(_MAGNIFIER_SIZE, _MAGNIFIER_SIZE),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            painter.drawPixmap(mag_rect.topLeft(), scaled)

            # Border
            painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
            painter.drawRect(mag_rect)

            # Crosshair in magnifier center
            center = mag_rect.center()
            painter.setPen(QPen(QColor(255, 0, 0, 180), 1))
            painter.drawLine(center.x() - 8, center.y(), center.x() + 8, center.y())
            painter.drawLine(center.x(), center.y() - 8, center.x(), center.y() + 8)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start selection on left-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self._is_selecting = True
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Update selection rectangle or cursor position."""
        self._current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Complete selection on left-button release."""
        if event.button() == Qt.MouseButton.LeftButton and self._is_selecting:
            self._is_selecting = False
            selection = self._selection_rect()
            if selection and selection.width() > 5 and selection.height() > 5:
                logger.info(
                    "Region selected: (%d,%d) %dx%d",
                    selection.x(), selection.y(),
                    selection.width(), selection.height(),
                )
                self.hide()
                self.region_selected.emit(selection)
            else:
                self._origin = None
                self._current = event.position().toPoint()
                self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Cancel selection on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            logger.debug("Region selection cancelled.")
            self.hide()
            self.selection_cancelled.emit()


class RegionCapture:
    """Manages region-based screenshot capture."""

    def __init__(self) -> None:
        self._selector: RegionSelector | None = None
        self._last_region: QRect | None = None

    def start_selection(
        self,
        on_captured: callable | None = None,
        on_cancelled: callable | None = None,
    ) -> None:
        """Show the region selection overlay."""
        self._selector = RegionSelector()

        if on_captured:
            def handle_region(rect: QRect) -> None:
                self._last_region = rect
                pixmap = ScreenCapture.capture_region(rect)
                on_captured(pixmap)

            self._selector.region_selected.connect(handle_region)

        if on_cancelled:
            self._selector.selection_cancelled.connect(on_cancelled)

        self._selector.start()

    @property
    def last_region(self) -> QRect | None:
        """Return the last selected region rectangle."""
        return self._last_region
