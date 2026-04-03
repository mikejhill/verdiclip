"""Ellipse drawing tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsScene

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)


class EllipseTool(BaseTool):
    """Draw ellipses with configurable stroke and fill."""

    def __init__(
        self,
        stroke_color: QColor | None = None,
        fill_color: QColor | None = None,
        stroke_width: int = 3,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color or QColor("#FF0000")
        self._fill_color = fill_color or QColor(0, 0, 0, 0)
        self._stroke_width = stroke_width
        self._origin: QPointF | None = None
        self._current_item: QGraphicsEllipseItem | None = None

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos
        self._current_item = QGraphicsEllipseItem()
        self._current_item.setPen(QPen(self._stroke_color, self._stroke_width))
        self._current_item.setBrush(QBrush(self._fill_color))
        self._current_item.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable)
        self._scene.addItem(self._current_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item is None or self._origin is None:
            return
        rect = QRectF(self._origin, scene_pos).normalized()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            size = max(rect.width(), rect.height())
            if scene_pos.x() < self._origin.x():
                rect.setLeft(self._origin.x() - size)
            else:
                rect.setRight(self._origin.x() + size)
            if scene_pos.y() < self._origin.y():
                rect.setTop(self._origin.y() - size)
            else:
                rect.setBottom(self._origin.y() + size)
        self._current_item.setRect(rect)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item:
            rect = self._current_item.rect()
            if rect.width() < 3 and rect.height() < 3:
                assert self._scene is not None
                self._scene.removeItem(self._current_item)
            elif self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._current_item, "Draw ellipse")
        self._current_item = None
        self._origin = None

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_color = color

    def set_fill_color(self, color: QColor) -> None:
        self._fill_color = color

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
