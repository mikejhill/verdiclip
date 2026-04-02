"""Arrow drawing tool."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)

_ARROWHEAD_LENGTH = 20
_ARROWHEAD_ANGLE = math.radians(25)


class ArrowTool(BaseTool):
    """Draw lines with arrowheads."""

    def __init__(
        self,
        stroke_color: QColor | None = None,
        stroke_width: int = 3,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color or QColor("#FF0000")
        self._stroke_width = stroke_width
        self._origin: QPointF | None = None
        self._line_item: QGraphicsLineItem | None = None
        self._head_item: QGraphicsPathItem | None = None
        self._group: QGraphicsItemGroup | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos
        pen = QPen(self._stroke_color, self._stroke_width)

        self._group = self._scene.createItemGroup([])
        self._group.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable)

        self._line_item = QGraphicsLineItem(QLineF(scene_pos, scene_pos))
        self._line_item.setPen(pen)
        self._group.addToGroup(self._line_item)

        self._head_item = QGraphicsPathItem()
        self._head_item.setPen(pen)
        self._head_item.setBrush(QBrush(self._stroke_color))
        self._group.addToGroup(self._head_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._line_item is None or self._origin is None:
            return
        self._line_item.setLine(QLineF(self._origin, scene_pos))
        self._update_arrowhead(self._origin, scene_pos)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._line_item and self._origin:
            length = QLineF(self._origin, scene_pos).length()
            if length < 5 and self._group and self._scene:
                self._scene.destroyItemGroup(self._group)
                if self._line_item.scene():
                    self._scene.removeItem(self._line_item)
                if self._head_item and self._head_item.scene():
                    self._scene.removeItem(self._head_item)
            elif self._group and self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._group, "Draw arrow")
        self._line_item = None
        self._head_item = None
        self._group = None
        self._origin = None

    def _update_arrowhead(self, start: QPointF, end: QPointF) -> None:
        """Update the arrowhead polygon at the end point."""
        if not self._head_item:
            return
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        angle = math.atan2(dy, dx)

        p1 = QPointF(
            end.x() - _ARROWHEAD_LENGTH * math.cos(angle - _ARROWHEAD_ANGLE),
            end.y() - _ARROWHEAD_LENGTH * math.sin(angle - _ARROWHEAD_ANGLE),
        )
        p2 = QPointF(
            end.x() - _ARROWHEAD_LENGTH * math.cos(angle + _ARROWHEAD_ANGLE),
            end.y() - _ARROWHEAD_LENGTH * math.sin(angle + _ARROWHEAD_ANGLE),
        )

        path = QPainterPath()
        path.moveTo(end)
        path.lineTo(p1)
        path.lineTo(p2)
        path.closeSubpath()
        self._head_item.setPath(path)

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_color = color

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
