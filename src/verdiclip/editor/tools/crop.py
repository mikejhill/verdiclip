"""Crop tool for trimming images."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
)

from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY, Z_CROP_OVERLAY
from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)

_CROP_BORDER_COLOR = QColor(0, 120, 215)
_CROP_DIM_COLOR = QColor(0, 0, 0, 120)


class CropTool(BaseTool):
    """Crop tool — draw a region and confirm with Enter/double-click."""

    def __init__(self) -> None:
        super().__init__()
        self._origin: QPointF | None = None
        self._crop_rect_item: QGraphicsRectItem | None = None
        self._dim_items: list[QGraphicsRectItem] = []

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def deactivate(self) -> None:
        self._clear_crop_ui()
        super().deactivate()

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        self._clear_crop_ui()
        self._origin = scene_pos

        self._crop_rect_item = QGraphicsRectItem()
        self._crop_rect_item.setPen(QPen(_CROP_BORDER_COLOR, 2, Qt.PenStyle.DashLine))
        self._crop_rect_item.setZValue(Z_CROP_OVERLAY)
        self._scene.addItem(self._crop_rect_item)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._crop_rect_item is None or self._origin is None:
            return
        rect = QRectF(self._origin, scene_pos).normalized()
        self._crop_rect_item.setRect(rect)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._crop_rect_item is None or self._origin is None:
            return

        rect = QRectF(self._origin, scene_pos).normalized()
        if rect.width() < 10 or rect.height() < 10:
            self._clear_crop_ui()
            return

        self._crop_rect_item.setRect(rect)
        self.apply_crop()

    def apply_crop(self) -> None:
        """Apply the current crop selection."""
        if not self._crop_rect_item or not self._scene:
            return

        crop_rect = self._crop_rect_item.rect()
        if crop_rect.width() < 10 or crop_rect.height() < 10:
            return

        # Find background pixmap
        bg_item = None
        for item in self._scene.items():
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() <= Z_BACKGROUND:
                bg_item = item
                break

        if not bg_item:
            return

        # Clamp crop rect to image bounds
        img_rect = QRectF(bg_item.pixmap().rect())
        crop_rect = crop_rect.intersected(img_rect)
        if crop_rect.width() < 10 or crop_rect.height() < 10:
            return

        # Identify annotation items fully outside the crop rect
        removed_items = []
        annotation_items = []
        for item in self._scene.items():
            if item is bg_item or item is self._crop_rect_item:
                continue
            if item.zValue() >= Z_BOUNDARY:
                continue
            if item.zValue() <= Z_BACKGROUND:
                continue
            # Skip child items of groups (e.g., ArrowItem shaft/head)
            if item.parentItem() is not None:
                continue
            annotation_items.append(item)
            item_rect = item.sceneBoundingRect()
            if not item_rect.intersects(crop_rect):
                removed_items.append(item)

        # Record all annotation positions BEFORE shifting
        item_positions = [
            (item, item.pos().x(), item.pos().y())
            for item in annotation_items
        ]

        # Create the cropped pixmap directly from the background
        src_rect = QRectF(crop_rect)
        bg_pixmap = bg_item.pixmap()
        result = bg_pixmap.copy(
            int(src_rect.x()), int(src_rect.y()),
            int(src_rect.width()), int(src_rect.height()),
        )

        # Adjust remaining annotation positions relative to new origin
        offset_x = crop_rect.x()
        offset_y = crop_rect.y()
        for item in annotation_items:
            if item in removed_items:
                continue
            pos = item.pos()
            item.setPos(pos.x() - offset_x, pos.y() - offset_y)

        self._clear_crop_ui()
        if self._view and hasattr(self._view, "crop_undoable"):
            self._view.crop_undoable(
                result, removed_items, item_positions,
                (offset_x, offset_y),
            )
        elif self._view and hasattr(self._view, "set_image"):
            self._view.set_image(result)
        else:
            self._scene.clear()
            new_bg = QGraphicsPixmapItem(result)
            new_bg.setZValue(Z_BACKGROUND)
            self._scene.addItem(new_bg)
            self._scene.setSceneRect(QRectF(result.rect()))

        logger.info("Crop applied: %dx%d", int(crop_rect.width()), int(crop_rect.height()))

    def cancel_crop(self) -> None:
        """Cancel the crop selection."""
        self._clear_crop_ui()

    def _clear_crop_ui(self) -> None:
        """Remove crop overlay elements from the scene."""
        if self._crop_rect_item and self._scene:
            self._scene.removeItem(self._crop_rect_item)
        self._crop_rect_item = None
        for item in self._dim_items:
            if self._scene:
                self._scene.removeItem(item)
        self._dim_items.clear()
        self._origin = None
