"""Text annotation tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsTextItem,
)

from verdiclip.editor import Z_BACKGROUND
from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)


class TextTool(BaseTool):
    """Place and edit text annotations."""

    def __init__(
        self,
        color: QColor | None = None,
        font: QFont | None = None,
    ) -> None:
        super().__init__()
        self._color = color or QColor("#FF0000")
        self._font = font or QFont("Arial", 14)
        self._active_item: QGraphicsTextItem | None = None

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        """Set I-beam cursor and prepare for text placement."""
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.IBeamCursor)

    def deactivate(self) -> None:
        """Finalize active text and deactivate the tool."""
        self._finalize_text()
        super().deactivate()

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Place a new text item or edit an existing one at the click position."""
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        # Finalize any previously active text
        self._finalize_text()

        # Check if clicking on an existing text item
        transform = self._view.transform() if self._view else self._scene.views()[0].transform()
        item = self._scene.itemAt(scene_pos, transform)
        if isinstance(item, QGraphicsTextItem) and item.zValue() > Z_BACKGROUND:
            self._active_item = item
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            item.setFocus()
            return

        # Create new text item
        text_item = QGraphicsTextItem()
        text_item.setPos(scene_pos)
        text_item.setDefaultTextColor(self._color)
        text_item.setFont(self._font)
        text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        text_item.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable)
        self._scene.addItem(text_item)
        if self._view and hasattr(self._view, "add_item_undoable"):
            self._view.add_item_undoable(text_item, "Add text")
        text_item.setFocus()
        self._active_item = text_item
        logger.debug("Text item placed at (%.0f, %.0f)", scene_pos.x(), scene_pos.y())

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """No-op; text is placed on click."""

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """No-op; text editing is handled by the text item."""

    def _finalize_text(self) -> None:
        """Stop editing the active text item."""
        if self._active_item:
            self._active_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            if not self._active_item.toPlainText().strip() and self._scene:
                self._scene.removeItem(self._active_item)
            self._active_item = None

    def set_color(self, color: QColor) -> None:
        """Update the text color for new annotations."""
        self._color = color

    def set_font(self, font: QFont) -> None:
        """Update the font for new text annotations."""
        self._font = font
