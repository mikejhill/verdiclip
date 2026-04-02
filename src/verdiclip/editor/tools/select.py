"""Selection tool for selecting, moving, and deleting items."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem

from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY
from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

logger = logging.getLogger(__name__)


def _resolve_top_level_item(item: QGraphicsItem) -> QGraphicsItem:
    """Walk up parent chain to find the top-level scene item."""
    while item.parentItem() is not None:
        item = item.parentItem()
    return item


class SelectTool(BaseTool):
    """Tool for selecting, moving, and deleting scene items."""

    def __init__(self) -> None:
        super().__init__()
        self._dragging = False
        self._rubber_banding = False
        self._drag_item: QGraphicsItem | None = None
        self._drag_start: QPointF | None = None
        self._item_start_pos: QPointF | None = None
        self._rubber_band_origin: QPointF | None = None
        self._rubber_band_rect: QGraphicsRectItem | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.ArrowCursor)

    def _is_annotation(self, item: QGraphicsItem) -> bool:
        """Return True if the item is a selectable annotation (not bg/boundary)."""
        z = item.zValue()
        return z > Z_BACKGROUND and z < Z_BOUNDARY

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        transform = (
            self._view.transform()
            if self._view
            else self._scene.views()[0].transform()
        )
        raw_item = self._scene.itemAt(scene_pos, transform)

        # Walk to top-level parent and check if it's a selectable annotation
        item = _resolve_top_level_item(raw_item) if raw_item else None

        if item and self._is_annotation(item):
            # Deselect others unless Ctrl is held
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                for i in self._scene.selectedItems():
                    if i is not item:
                        i.setSelected(False)
            item.setSelected(True)
            self._dragging = True
            self._drag_item = item
            self._drag_start = scene_pos
            self._item_start_pos = item.pos()
        else:
            # Start rubber band selection on empty space
            for i in self._scene.selectedItems():
                i.setSelected(False)
            self._rubber_banding = True
            self._rubber_band_origin = scene_pos
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber_band_rect = QGraphicsRectItem()
            self._rubber_band_rect.setPen(pen)
            fill = QColor(0, 120, 215, 30)
            self._rubber_band_rect.setBrush(fill)
            self._rubber_band_rect.setZValue(Z_BOUNDARY + 1)
            self._scene.addItem(self._rubber_band_rect)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._dragging and self._drag_item and self._drag_start is not None:
            delta = scene_pos - self._drag_start
            base = (
                self._item_start_pos
                if self._item_start_pos is not None
                else QPointF()
            )
            self._drag_item.setPos(base + delta)
        elif (
            self._rubber_banding
            and self._rubber_band_origin is not None
            and self._rubber_band_rect is not None
        ):
            rect = QRectF(
                self._rubber_band_origin, scene_pos,
            ).normalized()
            self._rubber_band_rect.setRect(rect)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._rubber_banding and self._rubber_band_rect is not None:
            # Select all annotation items within the rubber band
            rect = self._rubber_band_rect.rect()
            for item in self._scene.items(rect):
                top = _resolve_top_level_item(item)
                if self._is_annotation(top):
                    top.setSelected(True)
            self._scene.removeItem(self._rubber_band_rect)
            self._rubber_band_rect = None
            self._rubber_band_origin = None
            self._rubber_banding = False
            return

        if (
            self._dragging
            and self._drag_item is not None
            and self._item_start_pos is not None
            and self._drag_item.pos() != self._item_start_pos
            and self._view is not None
            and hasattr(self._view, "add_move_undoable")
        ):
            old = self._item_start_pos
            new = self._drag_item.pos()
            self._view.add_move_undoable(
                self._drag_item,
                (old.x(), old.y()),
                (new.x(), new.y()),
            )
        self._dragging = False
        self._drag_item = None
        self._drag_start = None
        self._item_start_pos = None

    def select_all(self) -> None:
        """Select all annotation items in the scene."""
        if not self._scene:
            return
        for item in self._scene.items():
            top = _resolve_top_level_item(item)
            if self._is_annotation(top) and not top.isSelected():
                top.setSelected(True)
