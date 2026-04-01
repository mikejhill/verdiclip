"""Line drawing tool."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsScene, QGraphicsView

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)


class LineTool(BaseTool):
    """Draw straight lines with optional angle snapping."""

    def __init__(
        self,
        stroke_color: QColor | None = None,
        stroke_width: int = 3,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color or QColor("#FF0000")
        self._stroke_width = stroke_width
        self._origin: QPointF | None = None
        self._current_item: QGraphicsLineItem | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos
        self._current_item = QGraphicsLineItem()
        self._current_item.setPen(QPen(self._stroke_color, self._stroke_width))
        self._current_item.setFlag(QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable)
        self._current_item.setFlag(QGraphicsLineItem.GraphicsItemFlag.ItemIsMovable)
        self._scene.addItem(self._current_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item is None or self._origin is None:
            return
        end = scene_pos
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = self._snap_angle(self._origin, scene_pos)
        self._current_item.setLine(QLineF(self._origin, end))

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item:
            line = self._current_item.line()
            if line.length() < 3:
                self._scene.removeItem(self._current_item)
            elif self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._current_item, "Draw line")
        self._current_item = None
        self._origin = None

    @staticmethod
    def _snap_angle(origin: QPointF, pos: QPointF, snap_degrees: float = 15.0) -> QPointF:
        """Snap the endpoint to the nearest angle increment."""
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        angle = math.atan2(dy, dx)
        snap_rad = math.radians(snap_degrees)
        snapped = round(angle / snap_rad) * snap_rad
        length = math.hypot(dx, dy)
        return QPointF(
            origin.x() + length * math.cos(snapped),
            origin.y() + length * math.sin(snapped),
        )

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_color = color

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
