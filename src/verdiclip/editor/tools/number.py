"""Numbered step marker tool."""

from __future__ import annotations

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
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)

_MARKER_RADIUS = 16
_MARKER_BG_COLOR = QColor("#E74C3C")
_MARKER_TEXT_COLOR = QColor("#FFFFFF")


class NumberTool(BaseTool):
    """Place auto-incrementing numbered circle markers."""

    def __init__(
        self,
        bg_color: QColor | None = None,
        text_color: QColor | None = None,
    ) -> None:
        super().__init__()
        self._bg_color = bg_color or _MARKER_BG_COLOR
        self._text_color = text_color or _MARKER_TEXT_COLOR
        self._counter = 0

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        self._counter += 1
        self._place_marker(scene_pos, self._counter)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        pass

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        pass

    def _place_marker(self, center: QPointF, number: int) -> None:
        """Place a numbered circle marker at the given position."""
        r = _MARKER_RADIUS

        # Circle background
        ellipse = QGraphicsEllipseItem(QRectF(-r, -r, 2 * r, 2 * r))
        ellipse.setBrush(QBrush(self._bg_color))
        ellipse.setPen(QPen(self._bg_color.darker(120), 2))

        # Number text
        text = QGraphicsSimpleTextItem(str(number))
        font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        text.setFont(font)
        text.setBrush(QBrush(self._text_color))

        # Center text in circle
        text_rect = text.boundingRect()
        text.setPos(-text_rect.width() / 2, -text_rect.height() / 2)

        # Group them
        group = self._scene.createItemGroup([ellipse, text])
        group.setPos(center)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

        logger.debug("Number marker #%d placed at (%.0f, %.0f)", number, center.x(), center.y())

    def reset_counter(self) -> None:
        """Reset the number counter to 0."""
        self._counter = 0
        logger.debug("Number counter reset.")

    def set_bg_color(self, color: QColor) -> None:
        self._bg_color = color

    def set_text_color(self, color: QColor) -> None:
        self._text_color = color
