"""Obfuscation tool for pixelating sensitive areas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)


class ObfuscateTool(BaseTool):
    """Pixelate regions of the image for obfuscation."""

    def __init__(self, block_size: int = 12) -> None:
        super().__init__()
        self._block_size = block_size
        self._origin: QPointF | None = None
        self._preview_item: QGraphicsPixmapItem | None = None
        self._selection_rect: QRectF | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._origin is None:
            return
        self._selection_rect = QRectF(self._origin, scene_pos).normalized()

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._origin is None or self._scene is None:
            self._origin = None
            return

        rect = QRectF(self._origin, scene_pos).normalized()
        if rect.width() < 5 or rect.height() < 5:
            self._origin = None
            return

        self._apply_pixelation(rect)
        self._origin = None
        self._selection_rect = None

    def _apply_pixelation(self, rect: QRectF) -> None:
        """Apply pixelation effect to the given region."""
        if not self._scene:
            return

        # Find the background pixmap item
        bg_item = None
        for item in self._scene.items():
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() <= -1000:
                bg_item = item
                break

        if not bg_item:
            logger.warning("No background image found for obfuscation.")
            return

        pixmap = bg_item.pixmap()
        int_rect = QRect(
            int(rect.x()), int(rect.y()),
            int(rect.width()), int(rect.height()),
        )
        int_rect = int_rect.intersected(QRect(0, 0, pixmap.width(), pixmap.height()))
        if int_rect.isEmpty():
            return

        region = pixmap.copy(int_rect)
        pixelated = self._pixelate(region, self._block_size)

        # Place pixelated overlay
        overlay = QGraphicsPixmapItem(pixelated)
        overlay.setPos(int_rect.x(), int_rect.y())
        overlay.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable)
        overlay.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable)
        self._scene.addItem(overlay)
        logger.debug(
            "Obfuscation applied at (%d,%d) %dx%d with block_size=%d",
            int_rect.x(), int_rect.y(), int_rect.width(), int_rect.height(),
            self._block_size,
        )

    @staticmethod
    def _pixelate(pixmap: QPixmap, block_size: int) -> QPixmap:
        """Apply pixelation effect to a QPixmap."""
        img = pixmap.toImage()
        w, h = img.width(), img.height()
        small_w = max(1, w // block_size)
        small_h = max(1, h // block_size)
        ignore_ratio = Qt.AspectRatioMode.IgnoreAspectRatio
        fast_transform = Qt.TransformationMode.FastTransformation
        small = img.scaled(small_w, small_h, ignore_ratio, fast_transform)
        pixelated = small.scaled(w, h, ignore_ratio, fast_transform)
        return QPixmap.fromImage(pixelated)

    def set_block_size(self, size: int) -> None:
        """Set the pixelation block size."""
        self._block_size = max(2, min(size, 64))
