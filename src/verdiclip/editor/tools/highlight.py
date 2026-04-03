"""Highlight tool for semi-transparent overlays."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)

_DEFAULT_HIGHLIGHT_COLOR = QColor(255, 255, 0, 100)


class HighlightTool(BaseTool):
    """Draw semi-transparent highlight rectangles."""

    def __init__(self, color: QColor | None = None) -> None:
        super().__init__()
        self._color = color or _DEFAULT_HIGHLIGHT_COLOR
        self._origin: QPointF | None = None
        self._current_item: QGraphicsRectItem | None = None

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos
        self._current_item = QGraphicsRectItem()
        self._current_item.setPen(QPen(Qt.PenStyle.NoPen))
        self._current_item.setBrush(QBrush(self._color))
        self._current_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        self._scene.addItem(self._current_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item is None or self._origin is None:
            return
        rect = QRectF(self._origin, scene_pos).normalized()
        self._current_item.setRect(rect)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._current_item:
            rect = self._current_item.rect()
            if rect.width() < 5 and rect.height() < 5 and self._scene:
                self._scene.removeItem(self._current_item)
            elif self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._current_item, "Highlight area")
        self._current_item = None
        self._origin = None

    def set_color(self, color: QColor) -> None:
        """Set highlight color (should include alpha for transparency)."""
        self._color = color
