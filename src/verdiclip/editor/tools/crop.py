"""Crop tool for trimming images."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

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

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
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
        self._crop_rect_item.setZValue(9999)
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

        # If double-click, apply immediately
        if event.button() == Qt.MouseButton.LeftButton:
            # Apply crop on double-click (user can also press Enter via key handler)
            pass

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
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() <= -1000:
                bg_item = item
                break

        if not bg_item:
            return

        # Render the scene into the crop rect
        from PySide6.QtGui import QPainter
        result = QPixmap(int(crop_rect.width()), int(crop_rect.height()))
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self._scene.render(painter, QRectF(result.rect()), crop_rect)
        painter.end()

        # Replace scene contents
        self._clear_crop_ui()
        self._scene.clear()
        new_bg = QGraphicsPixmapItem(result)
        new_bg.setZValue(-1000)
        self._scene.addItem(new_bg)
        self._scene.setSceneRect(QRectF(result.rect()))

        if self._view:
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

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
