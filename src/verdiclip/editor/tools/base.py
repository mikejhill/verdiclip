"""Abstract base class for editor tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QFont, QMouseEvent
    from PySide6.QtWidgets import QGraphicsScene

    from verdiclip.editor.canvas import EditorCanvas


class BaseTool(ABC):
    """Abstract base class for all editor tools."""

    def __init__(self) -> None:
        self._scene: QGraphicsScene | None = None
        self._view: EditorCanvas | None = None
        self._is_active = False

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        """Called when this tool is selected."""
        self._scene = scene
        self._view = view
        self._is_active = True

    def deactivate(self) -> None:
        """Called when switching away from this tool."""
        self._is_active = False

    @abstractmethod
    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Handle mouse press at the given scene position."""

    @abstractmethod
    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Handle mouse move at the given scene position."""

    @abstractmethod
    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Handle mouse release at the given scene position."""

    # ------------------------------------------------------------------
    # Optional property setters — tools may override to apply live values
    # ------------------------------------------------------------------

    def set_stroke_color(self, color: QColor) -> None:  # noqa: B027
        """Override to apply a new stroke color to the tool."""

    def set_fill_color(self, color: QColor) -> None:  # noqa: B027
        """Override to apply a new fill color to the tool."""

    def set_stroke_width(self, width: int) -> None:  # noqa: B027
        """Override to apply a new stroke width to the tool."""

    def set_font(self, font: QFont) -> None:  # noqa: B027
        """Override to apply a new font to the tool."""
