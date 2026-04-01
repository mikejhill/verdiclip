"""Tests for verdiclip.editor.history.EditorHistory."""

from __future__ import annotations

from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene

from verdiclip.editor.history import AddItemCommand, EditorHistory


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
