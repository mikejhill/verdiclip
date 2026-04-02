"""Numbered step marker tool."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLineEdit,
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)

_MARKER_RADIUS = 16
_MARKER_BG_COLOR = QColor("#E74C3C")
_MARKER_TEXT_COLOR = QColor("#FFFFFF")


class NumberMarkerItem(QGraphicsEllipseItem):
    """A numbered circle marker with an editable value."""

    def __init__(
        self,
        value: str,
        bg_color: QColor,
        text_color: QColor,
        parent: QGraphicsItem | None = None,
    ) -> None:
        r = _MARKER_RADIUS
        super().__init__(QRectF(-r, -r, 2 * r, 2 * r), parent)
        self._value = value
        self._bg_color = bg_color
        self._text_color = text_color

        self.setBrush(QBrush(bg_color))
        self.setPen(QPen(bg_color.darker(120), 2))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

        # Number text (child item)
        self._text_item = QGraphicsSimpleTextItem(value, self)
        font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        self._text_item.setFont(font)
        self._text_item.setBrush(QBrush(text_color))
        self._center_text()

    @property
    def value(self) -> str:
        """Return the current marker value."""
        return self._value

    @value.setter
    def value(self, new_value: str) -> None:
        """Set the marker value and update the display text."""
        self._value = new_value
        self._text_item.setText(new_value)
        self._center_text()

    def _center_text(self) -> None:
        """Center the text label within the circle."""
        text_rect = self._text_item.boundingRect()
        self._text_item.setPos(-text_rect.width() / 2, -text_rect.height() / 2)


class NumberTool(BaseTool):
    """Place auto-incrementing numbered circle markers with editable values."""

    def __init__(
        self,
        bg_color: QColor | None = None,
        text_color: QColor | None = None,
    ) -> None:
        super().__init__()
        self._bg_color = bg_color or _MARKER_BG_COLOR
        self._text_color = text_color or _MARKER_TEXT_COLOR
        self._counter = 0
        self._last_numeric_value = 0
        self._active_editor: QLineEdit | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def deactivate(self) -> None:
        self._dismiss_editor()
        super().deactivate()

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        # If an editor is open, dismiss it first
        self._dismiss_editor()

        self._counter = self._last_numeric_value + 1
        self._last_numeric_value = self._counter
        self._place_marker(scene_pos, str(self._counter))

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        pass

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        pass

    def _place_marker(self, center: QPointF, value: str) -> None:
        """Place a numbered circle marker at the given position."""
        marker = NumberMarkerItem(value, self._bg_color, self._text_color)
        self._scene.addItem(marker)
        marker.setPos(center)
        if self._view and hasattr(self._view, "add_item_undoable"):
            self._view.add_item_undoable(marker, "Add number marker")

        logger.debug("Number marker '%s' placed at (%.0f, %.0f)", value, center.x(), center.y())

    def show_editor_for(self, marker: NumberMarkerItem) -> None:
        """Display an inline editor for the selected marker."""
        if not self._view:
            return

        self._dismiss_editor()

        scene_pos = marker.scenePos()
        view_pos = self._view.mapFromScene(scene_pos)

        editor = QLineEdit(self._view)
        editor.setText(marker.value)
        editor.setFixedWidth(60)
        editor.selectAll()
        editor.move(
            view_pos.x() + _MARKER_RADIUS + 4,
            view_pos.y() - editor.sizeHint().height() // 2,
        )
        editor.show()
        editor.setFocus()

        def on_finished() -> None:
            new_value = editor.text().strip()
            if new_value and new_value != marker.value:
                marker.value = new_value
                # Update the counter tracking
                try:
                    numeric_val = int(new_value)
                    self._last_numeric_value = numeric_val
                except ValueError:
                    pass  # Non-numeric: next counter will use _last_numeric_value + 1
            editor.deleteLater()
            if self._active_editor is editor:
                self._active_editor = None

        editor.editingFinished.connect(on_finished)
        self._active_editor = editor

    def _dismiss_editor(self) -> None:
        """Close any active inline editor."""
        if self._active_editor is not None:
            with contextlib.suppress(RuntimeError):
                self._active_editor.editingFinished.emit()
            self._active_editor = None

    def reset_counter(self) -> None:
        """Reset the number counter to 0."""
        self._counter = 0
        self._last_numeric_value = 0
        logger.debug("Number counter reset.")

    def set_bg_color(self, color: QColor) -> None:
        self._bg_color = color

    def set_text_color(self, color: QColor) -> None:
        self._text_color = color
