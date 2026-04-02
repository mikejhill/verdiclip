"""Tests for verdiclip.editor.history.EditorHistory."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
)

from verdiclip.editor.history import (
    AddItemCommand,
    CropCommand,
    EditorHistory,
    MoveItemCommand,
    MultipleMoveCommand,
    RemoveItemCommand,
    ResizeItemCommand,
    capture_geometry,
)


class TestUndoRedo:
    def test_undo_redo(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()

        history.push(AddItemCommand(scene, item, "Add rect"))
        assert item in scene.items(), (
            "Expected item in scene after AddItemCommand push"
        )

        history.undo()
        assert item not in scene.items(), (
            "Expected item removed from scene after undo"
        )

        history.redo()
        assert item in scene.items(), (
            "Expected item back in scene after redo"
        )


class TestCanUndoRedoStates:
    def test_empty_history_cannot_undo(self, qapp) -> None:
        history = EditorHistory()
        assert history.can_undo() is False, (
            f"Expected can_undo() False on empty history, got {history.can_undo()}"
        )

    def test_empty_history_cannot_redo(self, qapp) -> None:
        history = EditorHistory()
        assert history.can_redo() is False, (
            f"Expected can_redo() False on empty history, got {history.can_redo()}"
        )

    def test_can_undo_after_push(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()
        history.push(AddItemCommand(scene, item))
        assert history.can_undo() is True, (
            f"Expected can_undo() True after push, got {history.can_undo()}"
        )

    def test_cannot_redo_after_push(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()
        history.push(AddItemCommand(scene, item))
        assert history.can_redo() is False, (
            f"Expected can_redo() False after push, got {history.can_redo()}"
        )

    def test_cannot_undo_after_undo_single_item(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()
        history.push(AddItemCommand(scene, item))
        history.undo()
        assert history.can_undo() is False, (
            f"Expected can_undo() False after undo, got {history.can_undo()}"
        )

    def test_can_redo_after_undo(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()
        history.push(AddItemCommand(scene, item))
        history.undo()
        assert history.can_redo() is True, (
            f"Expected can_redo() True after undo, got {history.can_redo()}"
        )


class TestClearHistory:
    def test_clear_history(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        history = EditorHistory()

        history.push(AddItemCommand(scene, item))
        history.clear()

        assert history.can_undo() is False, (
            f"Expected can_undo() False after clear, got {history.can_undo()}"
        )
        assert history.can_redo() is False, (
            f"Expected can_redo() False after clear, got {history.can_redo()}"
        )


class TestRemoveItemCommand:
    def test_redo_removes_item_from_scene(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = RemoveItemCommand(scene, item, "Remove rect")
        cmd.redo()

        assert item not in scene.items(), (
            "Expected item removed from scene after RemoveItemCommand.redo()"
        )

    def test_undo_re_adds_item_to_scene(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = RemoveItemCommand(scene, item)
        cmd.redo()
        cmd.undo()

        assert item in scene.items(), (
            "Expected item back in scene after RemoveItemCommand.undo()"
        )


class TestMoveItemCommand:
    def test_redo_moves_item_to_new_pos(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)
        item.setPos(10.0, 20.0)

        cmd = MoveItemCommand(item, (10.0, 20.0), (100.0, 200.0), "Move rect")
        cmd.redo()

        assert item.pos().x() == 100.0, (
            f"Expected item x=100.0 after redo, got {item.pos().x()}"
        )
        assert item.pos().y() == 200.0, (
            f"Expected item y=200.0 after redo, got {item.pos().y()}"
        )

    def test_undo_moves_item_to_old_pos(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)
        item.setPos(10.0, 20.0)

        cmd = MoveItemCommand(item, (10.0, 20.0), (100.0, 200.0))
        cmd.redo()
        cmd.undo()

        assert item.pos().x() == 10.0, (
            f"Expected item x=10.0 after undo, got {item.pos().x()}"
        )
        assert item.pos().y() == 20.0, (
            f"Expected item y=20.0 after undo, got {item.pos().y()}"
        )


class TestEditorHistoryStack:
    def test_stack_returns_qundostack(self, qapp) -> None:
        history = EditorHistory()
        assert isinstance(history.stack, QUndoStack), (
            f"Expected stack to be QUndoStack, got {type(history.stack).__name__}"
        )


class TestAddItemCommandAlreadyAdded:
    """Test the _already_added flag used when items are already in the scene."""

    def test_first_push_skips_redo_when_already_added(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)  # Item already in scene

        cmd = AddItemCommand(scene, item, "Add rect")
        cmd._already_added = True
        history = EditorHistory()
        history.push(cmd)

        # Item should still be in scene (redo was skipped on first push)
        assert item in scene.items(), (
            "Expected item to remain in scene on first push with _already_added=True"
        )

    def test_undo_removes_already_added_item(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = AddItemCommand(scene, item, "Add rect")
        cmd._already_added = True
        history = EditorHistory()
        history.push(cmd)

        history.undo()
        assert item not in scene.items(), (
            "Expected item removed from scene after undo of already-added item"
        )

    def test_redo_after_undo_adds_item_back(self, qapp) -> None:
        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(item)

        cmd = AddItemCommand(scene, item, "Add rect")
        cmd._already_added = True
        history = EditorHistory()
        history.push(cmd)

        history.undo()
        history.redo()
        assert item in scene.items(), (
            "Expected item back in scene after redo (second redo uses addItem)"
        )


class TestCropCommand:
    """Test CropCommand redo/undo cycle with a mock canvas."""

    def test_first_redo_is_noop(self, qapp) -> None:
        """First redo (on push) should skip since crop is already applied."""
        from unittest.mock import MagicMock

        from PySide6.QtGui import QPixmap

        canvas = MagicMock()
        old_pixmap = QPixmap(100, 100)
        new_pixmap = QPixmap(50, 50)
        removed = [QGraphicsRectItem(0, 0, 10, 10)]

        cmd = CropCommand(canvas, old_pixmap, new_pixmap, removed, [], (0.0, 0.0))
        cmd.redo()

        canvas._replace_image.assert_not_called(), (
            "Expected _replace_image NOT called on first redo (no-op)"
        )

    def test_second_redo_applies_new_pixmap(self, qapp) -> None:
        """Second redo (after undo) should call _replace_image with new pixmap."""
        from unittest.mock import MagicMock

        from PySide6.QtGui import QPixmap

        canvas = MagicMock()
        old_pixmap = QPixmap(100, 100)
        new_pixmap = QPixmap(50, 50)
        removed = [QGraphicsRectItem(0, 0, 10, 10)]

        cmd = CropCommand(canvas, old_pixmap, new_pixmap, removed, [], (0.0, 0.0))
        cmd.redo()   # first redo (no-op)
        cmd.undo()   # undo
        cmd.redo()   # second redo — should apply

        canvas._replace_image.assert_called_with(
            new_pixmap, removed, remove=True,
        )

    def test_undo_restores_old_pixmap(self, qapp) -> None:
        """Undo should call _replace_image with old pixmap and remove=False."""
        from unittest.mock import MagicMock

        from PySide6.QtGui import QPixmap

        canvas = MagicMock()
        old_pixmap = QPixmap(100, 100)
        new_pixmap = QPixmap(50, 50)
        removed = [QGraphicsRectItem(0, 0, 10, 10)]

        cmd = CropCommand(canvas, old_pixmap, new_pixmap, removed, [], (0.0, 0.0))
        cmd.redo()   # first redo (no-op)
        cmd.undo()

        canvas._replace_image.assert_called_once_with(
            old_pixmap, removed, remove=False,
        )

    def test_undo_redo_cycle_with_history(self, qapp) -> None:
        """Full undo/redo cycle via EditorHistory with mock canvas."""
        from unittest.mock import MagicMock

        from PySide6.QtGui import QPixmap

        canvas = MagicMock()
        old_pixmap = QPixmap(100, 100)
        new_pixmap = QPixmap(50, 50)
        removed = []

        history = EditorHistory()
        cmd = CropCommand(canvas, old_pixmap, new_pixmap, removed, [], (0.0, 0.0))
        history.push(cmd)

        assert history.can_undo() is True, (
            "Expected can_undo() True after pushing CropCommand"
        )
        # First redo was no-op, so _replace_image should not have been called yet
        canvas._replace_image.assert_not_called()

        history.undo()
        canvas._replace_image.assert_called_once_with(
            old_pixmap, removed, remove=False,
        )

        history.redo()
        assert canvas._replace_image.call_count == 2, (
            f"Expected 2 _replace_image calls (undo + redo), "
            f"got {canvas._replace_image.call_count}"
        )

    def test_crop_command_description(self, qapp) -> None:
        """CropCommand should have 'Crop image' as its description."""
        from unittest.mock import MagicMock

        from PySide6.QtGui import QPixmap

        canvas = MagicMock()
        cmd = CropCommand(canvas, QPixmap(100, 100), QPixmap(50, 50), [], [], (0.0, 0.0))
        assert cmd.text() == "Crop image", (
            f"Expected description 'Crop image', got '{cmd.text()}'"
        )


# ---------------------------------------------------------------------------
# New commands: MultipleMoveCommand and ResizeItemCommand
# ---------------------------------------------------------------------------


class TestMultipleMoveCommand:
    def test_redo_moves_all_items(self, qapp) -> None:

        from verdiclip.editor.history import MultipleMoveCommand

        scene = QGraphicsScene()
        a = QGraphicsRectItem(0, 0, 10, 10)
        b = QGraphicsRectItem(0, 0, 10, 10)
        scene.addItem(a)
        scene.addItem(b)
        a.setPos(0, 0)
        b.setPos(10, 10)

        moves = [
            (a, QPointF(0, 0), QPointF(50, 60)),
            (b, QPointF(10, 10), QPointF(80, 90)),
        ]
        cmd = MultipleMoveCommand(moves)
        cmd.redo()

        assert abs(a.pos().x() - 50) < 0.01, f"Expected a.x=50, got {a.pos().x()}"
        assert abs(b.pos().y() - 90) < 0.01, f"Expected b.y=90, got {b.pos().y()}"

    def test_undo_restores_all_items(self, qapp) -> None:

        from verdiclip.editor.history import MultipleMoveCommand

        scene = QGraphicsScene()
        a = QGraphicsRectItem(0, 0, 10, 10)
        scene.addItem(a)
        a.setPos(50, 60)

        moves = [(a, QPointF(0, 0), QPointF(50, 60))]
        cmd = MultipleMoveCommand(moves)
        cmd.undo()

        assert abs(a.pos().x()) < 0.01, f"Expected a.x=0 after undo, got {a.pos().x()}"
        assert abs(a.pos().y()) < 0.01, f"Expected a.y=0 after undo, got {a.pos().y()}"

    def test_default_description(self, qapp) -> None:
        cmd = MultipleMoveCommand([])
        assert cmd.text() == "Move items"


class TestResizeItemCommand:
    def test_undo_restores_rect(self, qapp) -> None:
        from PySide6.QtCore import QRectF


        scene = QGraphicsScene()
        item = QGraphicsRectItem(10, 20, 80, 60)
        scene.addItem(item)

        old = capture_geometry(item)
        item.setRect(QRectF(0, 0, 40, 30))
        new = capture_geometry(item)

        cmd = ResizeItemCommand(item, old, new)
        cmd.undo()

        r = item.rect()
        assert abs(r.x() - 10) < 0.01, f"Expected rect.x=10 after undo, got {r.x()}"
        assert abs(r.width() - 80) < 0.01, f"Expected rect.width=80 after undo, got {r.width()}"

    def test_redo_applies_new_geometry(self, qapp) -> None:
        from PySide6.QtCore import QRectF


        scene = QGraphicsScene()
        item = QGraphicsRectItem(10, 20, 80, 60)
        scene.addItem(item)

        old = capture_geometry(item)
        new_rect = QRectF(0, 0, 40, 30)
        new = {"type": "rect", "rect": new_rect}

        cmd = ResizeItemCommand(item, old, new)
        cmd.redo()

        r = item.rect()
        assert abs(r.width() - 40) < 0.01, f"Expected rect.width=40 after redo, got {r.width()}"

    def test_capture_geometry_line(self, qapp) -> None:

        from verdiclip.editor.history import capture_geometry

        scene = QGraphicsScene()
        item = QGraphicsLineItem(QLineF(0, 0, 100, 50))
        scene.addItem(item)

        geom = capture_geometry(item)
        assert geom["type"] == "line"
        assert abs(geom["line"].x2() - 100) < 0.01


# ---------------------------------------------------------------------------
# Handles module
# ---------------------------------------------------------------------------


class TestResizeHandles:
    def test_create_handles_rect(self, qapp) -> None:
        from verdiclip.editor.tools.handles import create_handles_for_item

        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 100, 80)
        scene.addItem(item)

        handles = create_handles_for_item(item)
        assert len(handles) == 8, f"Expected 8 handles for rect, got {len(handles)}"

    def test_create_handles_ellipse(self, qapp) -> None:
        from verdiclip.editor.tools.handles import create_handles_for_item


        scene = QGraphicsScene()
        item = QGraphicsEllipseItem(0, 0, 60, 40)
        scene.addItem(item)

        handles = create_handles_for_item(item)
        assert len(handles) == 8, f"Expected 8 handles for ellipse, got {len(handles)}"

    def test_create_handles_line(self, qapp) -> None:

        from verdiclip.editor.tools.handles import HandleRole, create_handles_for_item

        scene = QGraphicsScene()
        item = QGraphicsLineItem(QLineF(10, 10, 100, 80))
        scene.addItem(item)

        handles = create_handles_for_item(item)
        assert len(handles) == 2, f"Expected 2 handles for line, got {len(handles)}"
        roles = {h.role for h in handles}
        assert HandleRole.LINE_P1 in roles
        assert HandleRole.LINE_P2 in roles

    def test_create_handles_text_returns_empty(self, qapp) -> None:
        from verdiclip.editor.tools.handles import create_handles_for_item

        scene = QGraphicsScene()
        item = QGraphicsTextItem("hello")
        scene.addItem(item)

        handles = create_handles_for_item(item)
        assert handles == [], "Expected no handles for text item"

    def test_handle_apply_drag_resizes_rect(self, qapp) -> None:

        from verdiclip.editor.tools.handles import HandleRole, ResizeHandle

        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 100, 80)
        scene.addItem(item)

        handle = ResizeHandle(item, HandleRole.SE)
        scene.addItem(handle)
        handle.apply_drag(QPointF(10, 5))

        r = item.rect()
        assert abs(r.width() - 110) < 0.01, f"Expected width=110 after SE drag, got {r.width()}"
        assert abs(r.height() - 85) < 0.01, f"Expected height=85 after SE drag, got {r.height()}"

    def test_handle_does_not_shrink_below_minimum(self, qapp) -> None:

        from verdiclip.editor.tools.handles import HandleRole, ResizeHandle

        scene = QGraphicsScene()
        item = QGraphicsRectItem(0, 0, 10, 10)
        scene.addItem(item)

        handle = ResizeHandle(item, HandleRole.SE)
        scene.addItem(handle)
        # Drag SE inward far enough to push below min size
        handle.apply_drag(QPointF(-20, -20))

        r = item.rect()
        assert r.width() >= 4, f"Expected width >= 4 (min), got {r.width()}"
        assert r.height() >= 4, f"Expected height >= 4 (min), got {r.height()}"

    def test_line_handle_moves_endpoint(self, qapp) -> None:

        from verdiclip.editor.tools.handles import HandleRole, ResizeHandle

        scene = QGraphicsScene()
        item = QGraphicsLineItem(QLineF(0, 0, 100, 0))
        scene.addItem(item)

        handle = ResizeHandle(item, HandleRole.LINE_P2)
        scene.addItem(handle)
        handle.apply_drag(QPointF(20, 10))

        line = item.line()
        assert abs(line.x2() - 120) < 0.01, f"Expected p2.x=120, got {line.x2()}"
        assert abs(line.y2() - 10) < 0.01, f"Expected p2.y=10, got {line.y2()}"
