"""Tests for verdiclip.editor.history.EditorHistory."""

from __future__ import annotations

from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene

from verdiclip.editor.history import (
    AddItemCommand,
    EditorHistory,
    MoveItemCommand,
    RemoveItemCommand,
)


class TestUndoRedo:
    def test_undo_redo(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()

        history.push(AddItemCommand(scene, item, "Add rect"))
        assert item in scene.items()

        history.undo()
        assert item not in scene.items()

        history.redo()
        assert item in scene.items()


class TestCanUndoRedoStates:
    def test_can_undo_redo_states(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()

        assert history.can_undo() is False
        assert history.can_redo() is False

        history.push(AddItemCommand(scene, item))
        assert history.can_undo() is True
        assert history.can_redo() is False

        history.undo()
        assert history.can_undo() is False
        assert history.can_redo() is True


class TestClearHistory:
    def test_clear_history(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()

        history.push(AddItemCommand(scene, item))
        history.clear()

        assert history.can_undo() is False
        assert history.can_redo() is False


class TestRemoveItemCommand:
    def test_redo_removes_item_from_scene(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = RemoveItemCommand(scene, item, "Remove rect")
        cmd.redo()

        assert item not in scene.items()

    def test_undo_re_adds_item_to_scene(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = RemoveItemCommand(scene, item)
        cmd.redo()
        cmd.undo()

        assert item in scene.items()


class TestMoveItemCommand:
    def test_redo_moves_item_to_new_pos(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)
        item.setPos(10.0, 20.0)

        cmd = MoveItemCommand(item, (10.0, 20.0), (100.0, 200.0), "Move rect")
        cmd.redo()

        assert item.pos().x() == 100.0
        assert item.pos().y() == 200.0

    def test_undo_moves_item_to_old_pos(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)
        item.setPos(10.0, 20.0)

        cmd = MoveItemCommand(item, (10.0, 20.0), (100.0, 200.0))
        cmd.redo()
        cmd.undo()

        assert item.pos().x() == 10.0
        assert item.pos().y() == 20.0


class TestEditorHistoryStack:
    def test_stack_returns_qundostack(self, qapp) -> None:
        history = EditorHistory()
        assert isinstance(history.stack, QUndoStack)
