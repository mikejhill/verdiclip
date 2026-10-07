"""Full-screen overlay on one monitor: drag a region or click a window."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol, override

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.geometry import Point, Rect

DRAG_THRESHOLD: Final = 3.0
MAGNIFIER_SOURCE_PX: Final = 30
MAGNIFIER_ZOOM: Final = 4
MAGNIFIER_OFFSET: Final = 24
DIM: Final = QColor(0, 0, 0, 110)
ACCENT: Final = QColor(0, 120, 215)


class WindowTarget(Protocol):
    """A window the user can click to capture."""

    @property
    def title(self) -> str:
        """Window title."""

    @property
    def bounds(self) -> Rect:
        """Window rectangle in physical desktop pixels."""


class SelectionOverlay(QWidget):
    """Shows the frozen screen of one monitor and reports the user's choice."""

    region_chosen = Signal(object)  # Rect (physical)
    window_chosen = Signal(object, str)  # Rect (physical), title
    cancelled = Signal()

    def __init__(
        self,
        geometry: ScreenGeometry,
        screen: QScreen,
        frozen: FrozenScreen,
        windows: Sequence[WindowTarget],
        *,
        show_magnifier: bool,
    ) -> None:
        super().__init__(None)
        self._geometry = geometry
        self._frozen = frozen
        self._image = frozen.crop(geometry.physical)
        self._image.setDevicePixelRatio(geometry.scale)
        self._windows = [w for w in windows if w.bounds.intersects(geometry.physical)]
        self._show_magnifier = show_magnifier
        self._press: Point | None = None
        self._cursor: Point | None = None
        self._dragging = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        logical = geometry.logical
        self.setGeometry(
            int(logical.x), int(logical.y), int(logical.width), int(logical.height)
        )

    @property
    def screen_geometry(self) -> ScreenGeometry:
        """Return the monitor this overlay covers."""
        return self._geometry

    def hovered_window(self) -> WindowTarget | None:
        """Return the topmost window under the cursor, if any."""
        if self._cursor is None:
            return None
        physical = self._geometry.to_physical(self._cursor)
        return next((w for w in self._windows if w.bounds.contains(physical)), None)

    def selection(self) -> Rect | None:
        """Return the dragged rectangle in local logical coordinates."""
        if not self._dragging or self._press is None or self._cursor is None:
            return None
        return Rect.from_points(self._press, self._clamped(self._cursor))

    # Painting

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the frozen screen, dimming, highlight, crosshair, and magnifier."""
        del event
        painter = QPainter(self)
        try:
            painter.drawImage(QPointF(0, 0), self._image)
            painter.fillRect(self.rect(), DIM)
            self._paint_target(painter)
            self._paint_crosshair(painter)
            if self._show_magnifier and self._cursor is not None:
                self._paint_magnifier(painter, self._cursor)
        finally:
            painter.end()

    def _paint_target(self, painter: QPainter) -> None:
        """Highlight the dragged region or the hovered window."""
        selection = self.selection()
        if selection is not None:
            size = self._physical_rect(selection)
            self._reveal(painter, selection, f"{int(size.width)} × {int(size.height)}")  # noqa: RUF001
            return
        window = self.hovered_window()
        if window is not None:
            local = self._geometry.local_rect(window.bounds)
            self._reveal(painter, local, window.title)

    def _reveal(self, painter: QPainter, area: Rect, label: str) -> None:
        """Undim ``area``, outline it, and show ``label`` beside it."""
        rect = QRectF(area.x, area.y, area.width, area.height)
        painter.drawImage(
            rect,
            self._image,
            QRectF(
                area.x * self._geometry.scale,
                area.y * self._geometry.scale,
                area.width * self._geometry.scale,
                area.height * self._geometry.scale,
            ),
        )
        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, 480)
        box = QRectF(0, 0, metrics.horizontalAdvance(text) + 12, metrics.height() + 6)
        above = area.y - box.height() - 4
        box.moveTo(max(0.0, area.x), above if above >= 0 else area.bottom + 4)
        painter.fillRect(box, QColor(20, 20, 20, 220))
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter.value), text)

    def _paint_crosshair(self, painter: QPainter) -> None:
        """Draw guide lines through the cursor."""
        if self._cursor is None or self._dragging:
            return
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1, Qt.PenStyle.DashLine))
        x, y = round(self._cursor.x), round(self._cursor.y)
        painter.drawLine(x, 0, x, self.height())
        painter.drawLine(0, y, self.width(), y)

    def _paint_magnifier(self, painter: QPainter, cursor: Point) -> None:
        """Draw a 4x loupe of the pixels around the cursor (UX-CAP-06)."""
        physical = self._geometry.to_physical(cursor)
        half = MAGNIFIER_SOURCE_PX // 2
        source = Rect(
            round(physical.x) - half,
            round(physical.y) - half,
            MAGNIFIER_SOURCE_PX,
            MAGNIFIER_SOURCE_PX,
        )
        patch = self._patch(source)
        size = MAGNIFIER_SOURCE_PX * MAGNIFIER_ZOOM
        x = cursor.x + MAGNIFIER_OFFSET
        y = cursor.y + MAGNIFIER_OFFSET
        if x + size > self.width():
            x = cursor.x - MAGNIFIER_OFFSET - size
        if y + size + 20 > self.height():
            y = cursor.y - MAGNIFIER_OFFSET - size - 20
        target = QRectF(x, y, size, size)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, on=False)
        painter.drawImage(target, patch)
        painter.restore()
        # Dark outer and light inner edge so the loupe reads on any content
        painter.setPen(QPen(QColor(20, 20, 20, 230), 1))
        painter.drawRect(target.adjusted(-2, -2, 2, 2))
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawRect(target)
        center = target.center()
        painter.setPen(QPen(QColor(255, 0, 0, 200), 1))
        painter.drawLine(
            QPointF(center.x() - 8, center.y()), QPointF(center.x() + 8, center.y())
        )
        painter.drawLine(
            QPointF(center.x(), center.y() - 8), QPointF(center.x(), center.y() + 8)
        )
        coords = QRectF(x, y + size, size, 20)
        painter.fillRect(coords, QColor(20, 20, 20, 220))
        painter.setPen(Qt.GlobalColor.white)
        label = f"{int(physical.x)}, {int(physical.y)}"
        painter.drawText(coords, int(Qt.AlignmentFlag.AlignCenter.value), label)

    def _patch(self, source: Rect) -> QImage:
        """Return ``source`` pixels from the frozen desktop, black outside it."""
        patch = QImage(
            int(source.width), int(source.height), QImage.Format.Format_RGB32
        )
        patch.fill(Qt.GlobalColor.black)
        inside = source.intersected(self._frozen.bounds)
        if not inside.is_empty:
            painter = QPainter(patch)
            painter.drawImage(
                QPointF(inside.x - source.x, inside.y - source.y),
                self._frozen.crop(inside),
            )
            painter.end()
        return patch

    # Input

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start a selection on left press; cancel on right press."""
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = self._local(event)
            self._cursor = self._press
            self._dragging = False

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Track the cursor and start dragging past the threshold."""
        self._cursor = self._local(event)
        if (
            self._press is not None
            and self._press.distance_to(self._cursor) >= DRAG_THRESHOLD
        ):
            self._dragging = True
        self.update()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Report a dragged region, a clicked window, or this whole monitor."""
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._cursor = self._local(event)
        selection = self.selection()
        self._press = None
        self._dragging = False
        if selection is not None:
            area = self._physical_rect(selection)
            if area.width >= DRAG_THRESHOLD and area.height >= DRAG_THRESHOLD:
                self.region_chosen.emit(area)
            self.update()
            return
        window = self.hovered_window()
        if window is not None:
            self.window_chosen.emit(
                window.bounds.intersected(self._frozen.bounds), window.title
            )
        else:
            self.region_chosen.emit(self._geometry.physical)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Cancel on Esc; move the cursor with arrow keys (Ctrl = 10 px)."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
        moves = {
            Qt.Key.Key_Left: QPoint(-step, 0),
            Qt.Key.Key_Right: QPoint(step, 0),
            Qt.Key.Key_Up: QPoint(0, -step),
            Qt.Key.Key_Down: QPoint(0, step),
        }
        delta = moves.get(Qt.Key(event.key()))
        if delta is None:
            super().keyPressEvent(event)
            return
        QCursor.setPos(QCursor.pos() + delta)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Hide the crosshair when the cursor moves to another monitor."""
        del event
        if self._press is None:
            self._cursor = None
            self.update()

    # Internals

    @staticmethod
    def _local(event: QMouseEvent) -> Point:
        """Return the event position in local logical coordinates."""
        pos = event.position()
        return Point(pos.x(), pos.y())

    def _clamped(self, point: Point) -> Point:
        """Clamp ``point`` to this overlay."""
        return Point(
            min(max(point.x, 0.0), float(self.width())),
            min(max(point.y, 0.0), float(self.height())),
        )

    def _physical_rect(self, local: Rect) -> Rect:
        """Map a local logical rectangle to whole physical pixels."""
        a = self._geometry.to_physical(local.top_left)
        b = self._geometry.to_physical(local.bottom_right)
        return Rect.from_points(a, b).rounded()
