"""Undo/redo history management for the editor."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QUndoCommand, QUndoStack

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)


class AddItemCommand(QUndoCommand):
    """Command to add a graphics item to the scene."""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "Add item",
    ) -> None:
        super().__init__(description)
        self._scene = scene
        self._item = item
        self._already_added = False

    def redo(self) -> None:
        """Re-add the item to the scene."""
        # On first push the item is already in the scene
        if self._already_added:
            self._already_added = False
            return
        self._scene.addItem(self._item)

    def undo(self) -> None:
        """Remove the item from the scene."""
        self._scene.removeItem(self._item)


class RemoveItemCommand(QUndoCommand):
    """Command to remove a graphics item from the scene."""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "Remove item",
    ) -> None:
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        """Remove the item from the scene."""
        self._scene.removeItem(self._item)

    def undo(self) -> None:
        """Re-add the item to the scene."""
        self._scene.addItem(self._item)


class MoveItemCommand(QUndoCommand):
    """Command to record a move of a graphics item."""

    def __init__(
        self,
        item: QGraphicsItem,
        old_pos: tuple[float, float],
        new_pos: tuple[float, float],
        description: str = "Move item",
    ) -> None:
        super().__init__(description)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos

    def redo(self) -> None:
        """Move the item to the new position."""
        self._item.setPos(self._new_pos[0], self._new_pos[1])

    def undo(self) -> None:
        """Restore the item to the old position."""
        self._item.setPos(self._old_pos[0], self._old_pos[1])


class MultipleMoveCommand(QUndoCommand):
    """Command to record a simultaneous move of multiple graphics items."""

    def __init__(
        self,
        moves: list[tuple[Any, Any, Any]],
        description: str = "Move items",
    ) -> None:
        """Initialise with a list of ``(item, old_pos, new_pos)`` tuples.

        Each position is a ``(float, float)`` pair or a ``QPointF``.
        """
        super().__init__(description)
        self._moves = moves

    def redo(self) -> None:
        """Move all items to their new positions."""
        for item, _old, new in self._moves:
            if hasattr(new, "x"):
                item.setPos(new.x(), new.y())
            else:
                item.setPos(new[0], new[1])

    def undo(self) -> None:
        """Restore all items to their old positions."""
        for item, old, _new in self._moves:
            if hasattr(old, "x"):
                item.setPos(old.x(), old.y())
            else:
                item.setPos(old[0], old[1])


class ResizeItemCommand(QUndoCommand):
    """Command to record a resize of a single annotation item.

    Stores the item's geometry before and after the resize so the operation
    can be fully undone and redone.  Geometry is stored as a snapshot dict
    rather than a typed structure so that the command works uniformly for
    ``QGraphicsRectItem``, ``QGraphicsEllipseItem``, ``QGraphicsLineItem``,
    and ``ObfuscationItem``.
    """

    def __init__(
        self,
        item: QGraphicsItem,
        old_geometry: dict[str, Any],
        new_geometry: dict[str, Any],
        description: str = "Resize item",
    ) -> None:
        super().__init__(description)
        self._item = item
        self._old = old_geometry
        self._new = new_geometry

    def redo(self) -> None:
        """Apply the new geometry to the item."""
        _apply_geometry(self._item, self._new)

    def undo(self) -> None:
        """Restore the old geometry on the item."""
        _apply_geometry(self._item, self._old)


def capture_geometry(item: QGraphicsItem) -> dict[str, Any]:
    """Return a geometry snapshot for *item* suitable for ``ResizeItemCommand``."""
    from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsRectItem

    # ArrowItem: store both scene endpoints
    try:
        from verdiclip.editor.tools.arrow import ArrowItem

        if isinstance(item, ArrowItem):
            return {"type": "arrow", "p1": item.get_scene_p1(), "p2": item.get_scene_p2()}
    except ImportError:
        pass

    # NumberMarkerItem: store bounding rect (radius encodes size)
    try:
        from verdiclip.editor.tools.number import NumberMarkerItem

        if isinstance(item, NumberMarkerItem):
            return {"type": "number_marker", "rect": item.rect()}
    except ImportError:
        pass

    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        return {"type": "rect", "rect": item.rect()}
    if isinstance(item, QGraphicsLineItem):
        return {"type": "line", "line": item.line()}
    try:
        from verdiclip.editor.tools.obfuscate import ObfuscationItem

        if isinstance(item, ObfuscationItem):
            return {"type": "obfuscate", "pos": item.pos(), "size": item._size}
    except ImportError:
        pass
    return {}


def _apply_geometry(item: Any, geometry: dict[str, Any]) -> None:
    """Apply a geometry snapshot to *item*."""
    gtype = geometry.get("type")
    if gtype == "arrow":
        try:
            from verdiclip.editor.tools.arrow import ArrowItem

            if isinstance(item, ArrowItem):
                item.set_scene_p1(geometry["p1"])
                item.set_scene_p2(geometry["p2"])
        except ImportError:
            pass
    elif gtype == "number_marker":
        item.setRect(geometry["rect"])
        item._center_text()
    elif gtype == "rect":
        item.setRect(geometry["rect"])
    elif gtype == "line":
        item.setLine(geometry["line"])
    elif gtype == "obfuscate":
        item.set_geometry(geometry["pos"], geometry["size"])


class CropCommand(QUndoCommand):
    """Command to crop the image, undoable by restoring the original."""

    def __init__(
        self,
        canvas: EditorCanvas,
        old_pixmap: QPixmap,
        new_pixmap: QPixmap,
        removed_items: list[QGraphicsItem],
        item_positions: list[tuple[QGraphicsItem, float, float]],
        crop_offset: tuple[float, float],
        description: str = "Crop image",
    ) -> None:
        super().__init__(description)
        self._canvas = canvas
        self._old_pixmap = old_pixmap
        self._new_pixmap = new_pixmap
        self._removed_items = removed_items
        self._item_positions = item_positions  # [(item, old_x, old_y), ...]
        self._crop_offset = crop_offset  # (offset_x, offset_y)
        self._first_redo = True

    def redo(self) -> None:
        """Apply the crop by replacing the image and removing clipped items."""
        if self._first_redo:
            self._first_redo = False
            return
        self._canvas._replace_image(self._new_pixmap, self._removed_items, remove=True)
        # Shift remaining items by crop offset
        ox, oy = self._crop_offset
        for item, _old_x, _old_y in self._item_positions:
            if item not in self._removed_items and item.scene():
                pos = item.pos()
                item.setPos(pos.x() - ox, pos.y() - oy)

    def undo(self) -> None:
        """Restore the original image and item positions."""
        self._canvas._replace_image(self._old_pixmap, self._removed_items, remove=False)
        # Restore all items to their original positions
        for item, old_x, old_y in self._item_positions:
            item.setPos(old_x, old_y)


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
        """Return whether an undo operation is available."""
        return self._stack.canUndo()

    def can_redo(self) -> bool:
        """Return whether a redo operation is available."""
        return self._stack.canRedo()

    def clear(self) -> None:
        """Clear all undo/redo history."""
        self._stack.clear()

    @property
    def stack(self) -> QUndoStack:
        """Return the underlying QUndoStack."""
        return self._stack
