"""Obfuscation tool for pixelating sensitive areas.

Provides a live pixelation mask that re-pixelates the underlying background
image whenever the overlay is moved, similar to Greenshot's obfuscation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
)

from verdiclip.editor import Z_BACKGROUND
from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QStyleOptionGraphicsItem, QWidget

    from verdiclip.editor.canvas import EditorCanvas

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
        self._border_pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
        self._border_pen.setCosmetic(True)
        self.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(
            QGraphicsPixmapItem.GraphicsItemFlag.ItemSendsGeometryChanges,
        )
        self._show_border = True
        self._updating_geometry = False
        self._refresh_pixelation()

    def set_size(self, size: QSizeF) -> None:
        """Update the obfuscation region size and refresh pixelation."""
        self._size = size
        self._refresh_pixelation()

    def set_geometry(self, pos: QPointF, size: QSizeF) -> None:
        """Update position and size atomically with a single pixelation refresh."""
        self._size = size
        # Block itemChange from triggering an extra refresh during setPos
        self._updating_geometry = True
        self.setPos(pos)
        self._updating_geometry = False
        self._refresh_pixelation()

    def set_show_border(self, show: bool) -> None:
        """Toggle the dashed border visibility."""
        self._show_border = show
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        """Return the full item rect (not just the pixmap portion)."""
        base = QRectF(0, 0, self._size.width(), self._size.height())
        if self._show_border:
            pw = self._border_pen.widthF() / 2
            return base.adjusted(-pw, -pw, pw, pw)
        return base

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Draw the pixelated pixmap with a dashed border."""
        super().paint(painter, option, widget)  # pyright: ignore[reportArgumentType]
        if self._show_border:
            painter.setPen(self._border_pen)
            painter.drawRect(QRectF(0, 0, self._size.width(), self._size.height()))

    def itemChange(self, change: QGraphicsPixmapItem.GraphicsItemChange, value: object) -> object:  # noqa: N802
        if (
            change == QGraphicsPixmapItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._updating_geometry
        ):
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
        img_bounds = QRect(0, 0, bg_pixmap.width(), bg_pixmap.height())
        clipped = int_rect.intersected(img_bounds)
        if clipped.isEmpty():
            self.setPixmap(QPixmap())
            self.setOffset(0, 0)
            return

        region = bg_pixmap.copy(clipped)
        self.setPixmap(ObfuscateTool._pixelate(region, BLOCK_SIZE))
        # Offset the pixmap so it aligns with the correct image area
        self.setOffset(clipped.x() - int(pos.x()), clipped.y() - int(pos.y()))


class ObfuscateTool(BaseTool):
    """Pixelate regions of the image for obfuscation."""

    def __init__(self) -> None:
        super().__init__()
        self._origin: QPointF | None = None
        self._preview_item: ObfuscationItem | None = None

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
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

        self._preview_item.set_geometry(rect.topLeft(), rect.size())

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
            self._preview_item.set_geometry(rect.topLeft(), rect.size())
            self._preview_item.set_show_border(False)
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
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() <= Z_BACKGROUND:
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
