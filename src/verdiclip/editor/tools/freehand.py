"""Freehand drawing tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene, QGraphicsView

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)


class FreehandTool(BaseTool):
    """Smooth freehand pen drawing tool."""

    def __init__(
        self,
        stroke_color: QColor | None = None,
        stroke_width: int = 3,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color or QColor("#FF0000")
        self._stroke_width = stroke_width
        self._path: QPainterPath | None = None
        self._current_item: QGraphicsPathItem | None = None
        self._last_point: QPointF | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._path = QPainterPath(scene_pos)
        self._last_point = scene_pos
        self._current_item = QGraphicsPathItem(self._path)
        pen = QPen(self._stroke_color, self._stroke_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._current_item.setPen(pen)
        self._current_item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable)
        self._scene.addItem(self._current_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._path is None or self._current_item is None or self._last_point is None:
            return
        # Use quadratic bezier for smoother curves
        mid = QPointF(
            (self._last_point.x() + scene_pos.x()) / 2,
            (self._last_point.y() + scene_pos.y()) / 2,
        )
        self._path.quadTo(self._last_point, mid)
        self._current_item.setPath(self._path)
        self._last_point = scene_pos

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._path and self._current_item:
            bounds = self._path.boundingRect()
            if bounds.width() < 2 and bounds.height() < 2 and self._scene:
                self._scene.removeItem(self._current_item)
            elif self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._current_item, "Draw freehand")
        self._path = None
        self._current_item = None
        self._last_point = None

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_color = color

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
