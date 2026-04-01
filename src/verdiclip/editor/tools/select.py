"""Selection tool for selecting, moving, and deleting items."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

logger = logging.getLogger(__name__)


class SelectTool(BaseTool):
    """Tool for selecting, moving, and deleting scene items."""

    def __init__(self) -> None:
        super().__init__()
        self._dragging = False
        self._drag_item: QGraphicsItem | None = None
        self._drag_start: QPointF | None = None
        self._item_start_pos: QPointF | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.ArrowCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        transform = self._view.transform() if self._view else self._scene.views()[0].transform()
        item = self._scene.itemAt(scene_pos, transform)
        if item and item.zValue() > -1000:  # Don't select background image
            item.setSelected(True)
            self._dragging = True
            self._drag_item = item
            self._drag_start = scene_pos
            self._item_start_pos = item.pos()
        else:
            # Deselect all
            for i in self._scene.selectedItems():
                i.setSelected(False)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._dragging and self._drag_item and self._drag_start:
            delta = scene_pos - self._drag_start
            new_pos = self._item_start_pos + delta if self._item_start_pos else QPointF()
            self._drag_item.setPos(new_pos)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        self._dragging = False
        self._drag_item = None
        self._drag_start = None
        self._item_start_pos = None
