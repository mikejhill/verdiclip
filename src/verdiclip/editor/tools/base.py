"""Abstract base class for editor tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class BaseTool(ABC):
    """Abstract base class for all editor tools."""

    def __init__(self) -> None:
        self._scene: QGraphicsScene | None = None
        self._view: QGraphicsView | None = None
        self._is_active = False

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
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
