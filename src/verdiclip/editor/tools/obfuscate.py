"""Obfuscation tool for pixelating sensitive areas.

Provides a live pixelation mask that re-pixelates the underlying background
image whenever the overlay is moved, similar to Greenshot's obfuscation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt
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

BLOCK_SIZE = 12


class ObfuscationItem(QGraphicsPixmapItem):
    """A live pixelation mask that re-pixelates the background as it moves."""

    def __init__(
        self,
        bg_item: QGraphicsPixmapItem,
        size: QSizeF,
        parent: QGraphicsPixmapItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._bg_item = bg_item
        self._size = size
        self.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(
            QGraphicsPixmapItem.GraphicsItemFlag.ItemSendsGeometryChanges,
        )
        self._refresh_pixelation()

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsPixmapItem.GraphicsItemChange.ItemPositionHasChanged:
            self._refresh_pixelation()
        return super().itemChange(change, value)

    def _refresh_pixelation(self) -> None:
        """Re-pixelate the background region underneath this item."""
        bg_pixmap = self._bg_item.pixmap()
        pos = self.pos()
        int_rect = QRect(
            int(pos.x()),
            int(pos.y()),
            int(self._size.width()),
            int(self._size.height()),
        )
        int_rect = int_rect.intersected(
            QRect(0, 0, bg_pixmap.width(), bg_pixmap.height()),
        )
        if int_rect.isEmpty():
            self.setPixmap(QPixmap())
            return

        region = bg_pixmap.copy(int_rect)
        self.setPixmap(ObfuscateTool._pixelate(region, BLOCK_SIZE))


class ObfuscateTool(BaseTool):
    """Pixelate regions of the image for obfuscation."""

    def __init__(self) -> None:
        super().__init__()
        self._origin: QPointF | None = None
        self._preview_item: ObfuscationItem | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._origin is None or self._scene is None:
            return

        rect = QRectF(self._origin, scene_pos).normalized()
        if rect.width() < 2 or rect.height() < 2:
            return

        bg_item = self._find_background()
        if bg_item is None:
            return

        if self._preview_item is None:
            self._preview_item = ObfuscationItem(
                bg_item, rect.size(),
            )
            self._scene.addItem(self._preview_item)

        self._preview_item._size = rect.size()
        self._preview_item.setPos(rect.topLeft())

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._origin is None or self._scene is None:
            self._origin = None
            self._preview_item = None
            return

        rect = QRectF(self._origin, scene_pos).normalized()
        if rect.width() < 5 or rect.height() < 5:
            # Discard too-small regions; remove preview if it was added
            if self._preview_item is not None:
                self._scene.removeItem(self._preview_item)
                self._preview_item = None
            self._origin = None
            return

        if self._preview_item is not None:
            # Finalize the preview item — it's already in the scene
            self._preview_item._size = rect.size()
            self._preview_item.setPos(rect.topLeft())
            logger.debug(
                "Obfuscation applied at (%.0f,%.0f) %.0fx%.0f",
                rect.x(), rect.y(), rect.width(), rect.height(),
            )
        else:
            # No preview (e.g. very fast click-release) — create item directly
            self._apply_obfuscation(rect)

        self._origin = None
        self._preview_item = None

    def _find_background(self) -> QGraphicsPixmapItem | None:
        """Find the background pixmap item in the scene."""
        if not self._scene:
            return None
        for item in self._scene.items():
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() <= -1000:
                return item
        return None

    def _apply_obfuscation(self, rect: QRectF) -> None:
        """Create an ObfuscationItem for the given region."""
        if not self._scene:
            return

        bg_item = self._find_background()
        if not bg_item:
            logger.warning("No background image found for obfuscation.")
            return

        item = ObfuscationItem(bg_item, rect.size())
        item.setPos(rect.topLeft())
        self._scene.addItem(item)
        logger.debug(
            "Obfuscation applied at (%.0f,%.0f) %.0fx%.0f",
            rect.x(), rect.y(), rect.width(), rect.height(),
        )

    @staticmethod
    def _pixelate(pixmap: QPixmap, block_size: int = BLOCK_SIZE) -> QPixmap:
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
