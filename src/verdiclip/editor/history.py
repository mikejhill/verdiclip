"""Undo/redo history management for the editor."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QUndoCommand, QUndoStack

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

logger = logging.getLogger(__name__)


class AddItemCommand(QUndoCommand):
    """Command to add a graphics item to the scene."""

    def __init__(self, scene: QGraphicsScene, item: QGraphicsItem, description: str = "Add item"):
        super().__init__(description)
        self._scene = scene
        self._item = item
        self._already_added = False

    def redo(self) -> None:
        # On first push the item is already in the scene
        if self._already_added:
            self._already_added = False
            return
        self._scene.addItem(self._item)

    def undo(self) -> None:
        self._scene.removeItem(self._item)


class RemoveItemCommand(QUndoCommand):
    """Command to remove a graphics item from the scene."""

    def __init__(
        self, scene: QGraphicsScene, item: QGraphicsItem, description: str = "Remove item",
    ):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        self._scene.removeItem(self._item)

    def undo(self) -> None:
        self._scene.addItem(self._item)


class MoveItemCommand(QUndoCommand):
    """Command to record a move of a graphics item."""

    def __init__(
        self,
        item: QGraphicsItem,
        old_pos: tuple[float, float],
        new_pos: tuple[float, float],
        description: str = "Move item",
    ):
        super().__init__(description)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos

    def redo(self) -> None:
        self._item.setPos(self._new_pos[0], self._new_pos[1])

    def undo(self) -> None:
        self._item.setPos(self._old_pos[0], self._old_pos[1])


class CropCommand(QUndoCommand):
    """Command to crop the image, undoable by restoring the original."""

    def __init__(
        self,
        canvas,
        old_pixmap,
        new_pixmap,
        removed_items: list,
        description: str = "Crop image",
    ):
        super().__init__(description)
        self._canvas = canvas
        self._old_pixmap = old_pixmap
        self._new_pixmap = new_pixmap
        self._removed_items = removed_items
        self._first_redo = True

    def redo(self) -> None:
        if self._first_redo:
            self._first_redo = False
            return
        self._canvas._replace_image(self._new_pixmap, self._removed_items, remove=True)

    def undo(self) -> None:
        self._canvas._replace_image(self._old_pixmap, self._removed_items, remove=False)


class EditorHistory:
    """Manages undo/redo for the editor using QUndoStack."""

    def __init__(self) -> None:
        self._stack = QUndoStack()

    def push(self, command: QUndoCommand) -> None:
        """Push a command onto the undo stack."""
        self._stack.push(command)

    def undo(self) -> None:
        """Undo the last command."""
        if self._stack.canUndo():
            text = self._stack.undoText()
            self._stack.undo()
            logger.debug("Undo: %s", text)

    def redo(self) -> None:
        """Redo the last undone command."""
        if self._stack.canRedo():
            text = self._stack.redoText()
            self._stack.redo()
            logger.debug("Redo: %s", text)

    def can_undo(self) -> bool:
        return self._stack.canUndo()

    def can_redo(self) -> bool:
        return self._stack.canRedo()

    def clear(self) -> None:
        """Clear all undo/redo history."""
        self._stack.clear()

    @property
    def stack(self) -> QUndoStack:
        """Return the underlying QUndoStack."""
        return self._stack
