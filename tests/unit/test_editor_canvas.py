"""Tests for verdiclip.editor.canvas — EditorCanvas and EditorWindow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene

from verdiclip.editor.canvas import EditorCanvas, EditorWindow
from verdiclip.editor.toolbar import ToolType

# ---------------------------------------------------------------------------
# EditorCanvas
# ---------------------------------------------------------------------------


class TestEditorCanvasInit:
    def test_scene_is_created(self, qapp) -> None:
        canvas = EditorCanvas()
        assert isinstance(canvas.scene, QGraphicsScene), (
            f"Expected scene to be QGraphicsScene, got {type(canvas.scene).__name__}"
        )

    def test_no_pixmap_item_initially(self, qapp) -> None:
        canvas = EditorCanvas()
        assert canvas.pixmap_item is None, (
            f"Expected pixmap_item to be None initially, got {canvas.pixmap_item}"
        )


class TestEditorCanvasSetImage:
    def test_adds_pixmap_item(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert isinstance(canvas.pixmap_item, QGraphicsPixmapItem), (
            f"Expected QGraphicsPixmapItem, got {type(canvas.pixmap_item).__name__}"
        )

    def test_sets_scene_rect(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        rect = canvas.scene.sceneRect()
        # Scene rect is expanded with margin around the image
        assert rect.width() > 100, (
            f"Expected scene rect width > 100 (includes margin), got {rect.width()}"
        )
        assert rect.height() > 100, (
            f"Expected scene rect height > 100 (includes margin), got {rect.height()}"
        )
        img_rect = QRectF(pixmap.rect())
        assert rect.contains(img_rect), (
            f"Scene rect {rect} should fully contain image rect {img_rect}"
        )

    def test_updates_zoom(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert canvas._zoom_level == canvas.transform().m11(), (
            f"Expected zoom == m11, got {canvas._zoom_level} vs {canvas.transform().m11()}"
        )


class TestEditorCanvasSetTool:
    def test_activates_new_tool(self, qapp) -> None:
        canvas = EditorCanvas()
        tool = MagicMock()
        canvas.set_tool(tool)
        tool.activate.assert_called_once_with(canvas.scene, canvas)

    def test_deactivates_old_tool(self, qapp) -> None:
        canvas = EditorCanvas()
        old_tool = MagicMock()
        new_tool = MagicMock()
        canvas.set_tool(old_tool)
        canvas.set_tool(new_tool)
        old_tool.deactivate.assert_called_once()

    def test_set_tool_none_deactivates_current(self, qapp) -> None:
        canvas = EditorCanvas()
        tool = MagicMock()
        canvas.set_tool(tool)
        canvas.set_tool(None)
        tool.deactivate.assert_called_once()
        assert canvas._current_tool is None, (
            f"Expected _current_tool to be None, got {canvas._current_tool}"
        )


class TestEditorCanvasGetFlattenedPixmap:
    def test_returns_pixmap_with_correct_dimensions(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        result = canvas.get_flattened_pixmap()
        assert isinstance(result, QPixmap), (
            f"Expected QPixmap, got {type(result).__name__}"
        )
        assert result.width() == 100, (
            f"Expected flattened pixmap width 100, got {result.width()}"
        )
        assert result.height() == 100, (
            f"Expected flattened pixmap height 100, got {result.height()}"
        )


class TestEditorCanvasProperties:
    def test_pixmap_item_reflects_set_image(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert canvas.pixmap_item is not None, (
            "pixmap_item should be set after set_image"
        )
        assert canvas.pixmap_item.pixmap().width() == 100, (
            "pixmap_item width should match input pixmap"
        )


# ---------------------------------------------------------------------------
# EditorWindow
# ---------------------------------------------------------------------------


class TestEditorWindowCreation:
    def test_has_canvas(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert isinstance(window._canvas, EditorCanvas), (
            f"Expected _canvas to be EditorCanvas, got {type(window._canvas).__name__}"
        )

    def test_has_toolbar(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._toolbar is not None, (
            "Expected _toolbar to not be None"
        )

    def test_has_properties_panel(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._properties is not None, (
            "Expected _properties to not be None"
        )

    def test_has_statusbar(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._statusbar is not None, (
            "Expected _statusbar to not be None"
        )


class TestEditorWindowToolChanged:
    def test_updates_status_bar_message(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        window._on_tool_changed(ToolType.RECTANGLE)
        assert "Rectangle" in window._statusbar.currentMessage(), (
            f"Expected 'Rectangle' in status bar, got '{window._statusbar.currentMessage()}'"
        )


class TestEditorWindowTitle:
    def test_title_contains_verdiclip(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert "VerdiClip" in window.windowTitle(), (
            f"Expected 'VerdiClip' in window title, got '{window.windowTitle()}'"
        )


class TestEditorWindowMinimumSize:
    def test_minimum_size_is_800x600(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window.minimumWidth() == 800, (
            f"Expected minimum width 800, got {window.minimumWidth()}"
        )
        assert window.minimumHeight() == 600, (
            f"Expected minimum height 600, got {window.minimumHeight()}"
        )


# ---------------------------------------------------------------------------
# Helper factories for Qt events
# ---------------------------------------------------------------------------

def _make_wheel_event(
    angle_y: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    """Create a QWheelEvent with the given vertical angle delta."""
    pos = QPointF(50, 50)
    return QWheelEvent(
        pos,
        pos,
        QPoint(0, 0),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _make_mouse_event(
    event_type: QMouseEvent.Type,
    button: Qt.MouseButton,
    pos: QPointF | None = None,
) -> QMouseEvent:
    """Create a QMouseEvent with the given type and button."""
    if pos is None:
        pos = QPointF(50, 50)
    global_pos = QPointF(pos.x() + 100, pos.y() + 100)
    return QMouseEvent(
        event_type,
        pos,
        global_pos,
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


# ---------------------------------------------------------------------------
# EditorCanvas — wheelEvent
# ---------------------------------------------------------------------------


class TestEditorCanvasWheelEvent:
    def test_ctrl_scroll_up_zooms_in(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        zoom_before = canvas._zoom_level
        event = _make_wheel_event(
            120, Qt.KeyboardModifier.ControlModifier,
        )
        canvas.wheelEvent(event)
        assert canvas._zoom_level > zoom_before, (
            f"Expected zoom increase on Ctrl+scroll, got {canvas._zoom_level} (was {zoom_before})"
        )

    def test_ctrl_scroll_down_zooms_out(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        zoom_before = canvas._zoom_level
        event = _make_wheel_event(
            -120, Qt.KeyboardModifier.ControlModifier,
        )
        canvas.wheelEvent(event)
        assert canvas._zoom_level < zoom_before, (
            f"Expected zoom decrease on Ctrl+scroll, got {canvas._zoom_level} (was {zoom_before})"
        )

    def test_no_ctrl_passes_to_super(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        zoom_before = canvas._zoom_level
        event = _make_wheel_event(120)
        canvas.wheelEvent(event)
        assert canvas._zoom_level == zoom_before, (
            f"Expected zoom unchanged without Ctrl, got {canvas._zoom_level} (was {zoom_before})"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — mousePressEvent
# ---------------------------------------------------------------------------


class TestEditorCanvasMousePressEvent:
    def test_middle_button_starts_pan(self, qapp) -> None:
        canvas = EditorCanvas()
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonPress,
            Qt.MouseButton.MiddleButton,
        )
        canvas.mousePressEvent(event)
        assert canvas._is_panning is True, (
            f"Expected _is_panning True after middle-click, got {canvas._is_panning}"
        )

    def test_tool_delegation(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        tool = MagicMock()
        canvas.set_tool(tool)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonPress,
            Qt.MouseButton.LeftButton,
        )
        canvas.mousePressEvent(event)
        tool.mouse_press.assert_called_once()

    def test_no_tool_left_click_does_not_pan(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas.set_tool(None)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonPress,
            Qt.MouseButton.LeftButton,
        )
        canvas.mousePressEvent(event)
        assert canvas._is_panning is False, (
            "Left-click without tool should not start panning"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — mouseMoveEvent
# ---------------------------------------------------------------------------


class TestEditorCanvasMouseMoveEvent:
    def test_panning_accepts_event(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas._is_panning = True
        canvas._pan_start = QPointF(50, 50)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseMove,
            Qt.MouseButton.NoButton,
            QPointF(60, 60),
        )
        canvas.mouseMoveEvent(event)
        assert event.isAccepted(), (
            "Expected mouse move event to be accepted during panning"
        )

    def test_tool_delegation(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        tool = MagicMock()
        canvas.set_tool(tool)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseMove,
            Qt.MouseButton.NoButton,
        )
        canvas.mouseMoveEvent(event)
        tool.mouse_move.assert_called_once()

    def test_no_tool_move_does_not_change_pan_state(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas._is_panning = False
        canvas.set_tool(None)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseMove,
            Qt.MouseButton.NoButton,
        )
        canvas.mouseMoveEvent(event)
        assert canvas._is_panning is False, (
            "Mouse move without tool or panning should keep _is_panning False"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — mouseReleaseEvent
# ---------------------------------------------------------------------------


class TestEditorCanvasMouseReleaseEvent:
    def test_middle_button_stops_pan(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas._is_panning = True
        canvas._pan_start = QPointF(50, 50)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonRelease,
            Qt.MouseButton.MiddleButton,
        )
        canvas.mouseReleaseEvent(event)
        assert canvas._is_panning is False, (
            f"Expected _is_panning False after middle-release, got {canvas._is_panning}"
        )

    def test_tool_delegation(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        tool = MagicMock()
        canvas.set_tool(tool)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonRelease,
            Qt.MouseButton.LeftButton,
        )
        canvas.mouseReleaseEvent(event)
        tool.mouse_release.assert_called_once()

    def test_no_tool_calls_super(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas.set_tool(None)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonRelease,
            Qt.MouseButton.LeftButton,
        )
        canvas.mouseReleaseEvent(event)
        assert canvas._is_panning is False, (
            f"Expected _is_panning False after left-release, got {canvas._is_panning}"
        )


# ---------------------------------------------------------------------------
# EditorWindow — menu action methods
# ---------------------------------------------------------------------------


class TestEditorWindowOpenFile:
    @patch("verdiclip.editor.canvas.QFileDialog.getOpenFileName")
    def test_opens_and_loads_image(
        self, mock_dialog, qapp, tmp_config, tmp_path,
    ) -> None:
        img_path = tmp_path / "test.png"
        src = QPixmap(80, 60)
        src.save(str(img_path))
        mock_dialog.return_value = (str(img_path), "")

        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._open_file()

        rect = window._canvas.scene.sceneRect()
        img_rect = QRectF(0, 0, 80, 60)
        assert rect.contains(img_rect), (
            f"Scene rect {rect} should contain image rect {img_rect} after open"
        )
        assert rect.width() > 80, (
            f"Expected scene width > 80 (includes margin) after open, got {rect.width()}"
        )

    @patch("verdiclip.editor.canvas.QFileDialog.getOpenFileName")
    def test_cancelled_dialog_does_nothing(
        self, mock_dialog, qapp, tmp_config,
    ) -> None:
        mock_dialog.return_value = ("", "")
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._open_file()
        rect = window._canvas.scene.sceneRect()
        assert rect.width() > 100, (
            f"Expected scene width > 100 (includes margin) after cancel, got {rect.width()}"
        )


class TestEditorWindowSaveFile:
    @patch("verdiclip.export.file_export.FileExporter.save_with_dialog")
    def test_delegates_to_file_exporter(
        self, mock_save, qapp, tmp_config,
    ) -> None:
        mock_save.return_value = None  # Simulate no file saved
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file()
        mock_save.assert_called_once()

    @patch("verdiclip.export.file_export.FileExporter.save_with_dialog")
    def test_save_updates_title_with_file_path(
        self, mock_save, qapp, tmp_config,
    ) -> None:
        mock_save.return_value = r"C:\Pictures\screenshot.png"
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file()
        assert "screenshot.png" in window.windowTitle(), (
            f"Expected 'screenshot.png' in title, got '{window.windowTitle()}'"
        )
        assert window._file_label.text() == r"C:\Pictures\screenshot.png", (
            f"Expected full path in file label, got '{window._file_label.text()}'"
        )


class TestEditorWindowSaveFileAs:
    @patch("verdiclip.export.file_export.FileExporter.save_as")
    def test_delegates_to_file_exporter(
        self, mock_save_as, qapp, tmp_config,
    ) -> None:
        mock_save_as.return_value = None  # Simulate dialog cancelled
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file_as()
        mock_save_as.assert_called_once()

    @patch("verdiclip.export.file_export.FileExporter.save_as")
    def test_save_as_updates_title_with_file_path(
        self, mock_save_as, qapp, tmp_config,
    ) -> None:
        mock_save_as.return_value = r"C:\Output\my_image.png"
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file_as()
        assert "my_image.png" in window.windowTitle(), (
            f"Expected 'my_image.png' in title, got '{window.windowTitle()}'"
        )
        assert window._file_label.text() == r"C:\Output\my_image.png", (
            f"Expected full path in file label, got '{window._file_label.text()}'"
        )


class TestEditorWindowCopyToClipboard:
    @patch("verdiclip.export.clipboard.ClipboardExporter.copy")
    def test_delegates_to_clipboard_exporter(
        self, mock_copy, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._copy_to_clipboard()
        mock_copy.assert_called_once()

    @patch("verdiclip.export.clipboard.ClipboardExporter.copy")
    def test_updates_statusbar(
        self, mock_copy, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._copy_to_clipboard()
        assert "clipboard" in window._statusbar.currentMessage().lower(), (
            f"Expected 'clipboard' in status bar, got '{window._statusbar.currentMessage()}'"
        )


class TestEditorWindowPrint:
    @patch("verdiclip.export.printer.PrinterExporter.print_pixmap")
    def test_delegates_to_printer_exporter(
        self, mock_print, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._print()
        mock_print.assert_called_once()


# ---------------------------------------------------------------------------
# EditorWindow — tool registration (bug-fix coverage)
# ---------------------------------------------------------------------------


class TestEditorWindowToolRegistration:
    """_register_tools creates all 11 tools with correct types and wiring."""

    _EXPECTED_TOOL_TYPES: dict[ToolType, str] = {
        ToolType.SELECT: "SelectTool",
        ToolType.CROP: "CropTool",
        ToolType.RECTANGLE: "RectangleTool",
        ToolType.ELLIPSE: "EllipseTool",
        ToolType.LINE: "LineTool",
        ToolType.ARROW: "ArrowTool",
        ToolType.TEXT: "TextTool",
        ToolType.NUMBER: "NumberTool",
        ToolType.HIGHLIGHT: "HighlightTool",
        ToolType.OBFUSCATE: "ObfuscateTool",
        ToolType.FREEHAND: "FreehandTool",
    }

    def test_tools_dict_has_all_11_entries(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        assert len(window._tools) == 11, (
            f"Expected 11 tools registered, got {len(window._tools)}: "
            f"{list(window._tools.keys())}"
        )

    def test_tools_dict_contains_every_tool_type(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        for tool_type in ToolType:
            assert tool_type in window._tools, (
                f"Expected ToolType.{tool_type.name} in _tools dict, "
                f"missing from {list(window._tools.keys())}"
            )

    @pytest.mark.parametrize(
        ("tool_type", "expected_class_name"),
        list(_EXPECTED_TOOL_TYPES.items()),
        ids=[t.name for t in _EXPECTED_TOOL_TYPES],
    )
    def test_tool_is_correct_type(
        self, qapp, tmp_config, tool_type, expected_class_name,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        tool = window._tools[tool_type]
        actual_name = type(tool).__name__
        assert actual_name == expected_class_name, (
            f"Expected ToolType.{tool_type.name} → {expected_class_name}, "
            f"got {actual_name}"
        )

    def test_toolbar_tool_changed_sets_canvas_tool(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.RECTANGLE)
        expected = window._tools[ToolType.RECTANGLE]
        actual = window._canvas._current_tool
        assert actual is expected, (
            f"Expected canvas tool to be RectangleTool after toolbar signal, "
            f"got {type(actual).__name__ if actual else None}"
        )

    def test_toolbar_tool_changed_to_freehand(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.FREEHAND)
        expected = window._tools[ToolType.FREEHAND]
        actual = window._canvas._current_tool
        assert actual is expected, (
            f"Expected canvas tool to be FreehandTool after toolbar signal, "
            f"got {type(actual).__name__ if actual else None}"
        )

    def test_properties_stroke_color_signal_connected(self, qapp, tmp_config) -> None:
        from PySide6.QtGui import QColor
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.RECTANGLE)
        tool = window._canvas._current_tool
        tool.set_stroke_color = MagicMock()
        color = QColor(255, 0, 0)
        window._properties.stroke_color_changed.emit(color)
        tool.set_stroke_color.assert_called_once_with(color), (
            "Expected stroke_color_changed signal to reach active tool's set_stroke_color"
        )

    def test_properties_fill_color_signal_connected(self, qapp, tmp_config) -> None:
        from PySide6.QtGui import QColor
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.RECTANGLE)
        tool = window._canvas._current_tool
        tool.set_fill_color = MagicMock()
        color = QColor(0, 255, 0)
        window._properties.fill_color_changed.emit(color)
        tool.set_fill_color.assert_called_once_with(color), (
            "Expected fill_color_changed signal to reach active tool's set_fill_color"
        )

    def test_properties_stroke_width_signal_connected(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.RECTANGLE)
        tool = window._canvas._current_tool
        tool.set_stroke_width = MagicMock()
        window._properties.stroke_width_changed.emit(5)
        tool.set_stroke_width.assert_called_once_with(5), (
            "Expected stroke_width_changed signal to reach active tool's set_stroke_width"
        )

    def test_properties_font_signal_connected(self, qapp, tmp_config) -> None:
        from PySide6.QtGui import QFont
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._toolbar.tool_changed.emit(ToolType.TEXT)
        tool = window._canvas._current_tool
        tool.set_font = MagicMock()
        font = QFont("Arial", 12)
        window._properties.font_changed.emit(font)
        tool.set_font.assert_called_once_with(font), (
            "Expected font_changed signal to reach active tool's set_font"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — _delete_selected_items
# ---------------------------------------------------------------------------


def _make_canvas_with_image() -> EditorCanvas:
    """Create an EditorCanvas with a 100×100 blue image loaded."""
    canvas = EditorCanvas()
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.blue)
    canvas.set_image(pixmap)
    return canvas


def _make_key_event(key: Qt.Key) -> QKeyEvent:
    """Create a QKeyEvent for the given key."""
    return QKeyEvent(
        QKeyEvent.Type.KeyPress,
        key,
        Qt.KeyboardModifier.NoModifier,
    )


class TestEditorCanvasDeleteItems:
    def test_delete_selected_items_removes_from_scene(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        item = canvas.scene.addRect(10, 10, 50, 50)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setSelected(True)

        assert item in canvas.scene.items(), (
            "Pre-condition: item should be in scene before deletion"
        )
        canvas._delete_selected_items()
        assert item not in canvas.scene.items(), (
            f"Expected item to be removed from scene after _delete_selected_items, "
            f"but scene still contains {len(canvas.scene.items())} item(s)"
        )

    def test_delete_selected_items_does_not_remove_pixmap_item(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        pixmap_item = canvas.pixmap_item
        assert pixmap_item is not None, "Pre-condition: pixmap_item must exist"

        # Select the pixmap item (simulating accidental select-all)
        pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        pixmap_item.setSelected(True)

        canvas._delete_selected_items()
        assert pixmap_item in canvas.scene.items(), (
            "Expected pixmap_item (base image) to remain in scene after delete, "
            "but it was removed"
        )

    def test_keypress_delete_triggers_deletion(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        item = canvas.scene.addRect(10, 10, 50, 50)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setSelected(True)

        event = _make_key_event(Qt.Key.Key_Delete)
        canvas.keyPressEvent(event)
        assert item not in canvas.scene.items(), (
            "Expected Delete key to remove selected item from scene"
        )

    def test_keypress_backspace_triggers_deletion(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        item = canvas.scene.addRect(10, 10, 50, 50)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setSelected(True)

        event = _make_key_event(Qt.Key.Key_Backspace)
        canvas.keyPressEvent(event)
        assert item not in canvas.scene.items(), (
            "Expected Backspace key to remove selected item from scene"
        )


# ---------------------------------------------------------------------------
# EditorWindow — status bar with dimensions and title with file_path
# ---------------------------------------------------------------------------


class TestEditorWindowStatusBar:
    def test_status_bar_shows_image_dimensions(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        dim_text = window._dim_label.text()
        assert "100" in dim_text and "px" in dim_text, (
            f"Expected dimension label to contain '100' and 'px', got '{dim_text}'"
        )
        # Verify exact format  "W × H px"
        assert dim_text == "100 × 100 px", (
            f"Expected dimension label '100 × 100 px', got '{dim_text}'"
        )

    def test_window_title_contains_filename_when_file_path_given(
        self, qapp, tmp_config
    ) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config, file_path="C:\\images\\screenshot.png")
        title = window.windowTitle()
        assert "screenshot.png" in title, (
            f"Expected window title to contain 'screenshot.png', got '{title}'"
        )

    def test_window_title_contains_editor_when_no_file_path(
        self, qapp, tmp_config
    ) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config, file_path="")
        title = window.windowTitle()
        assert "Editor" in title, (
            f"Expected window title to contain 'Editor' when no file_path, got '{title}'"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — zoom controls
# ---------------------------------------------------------------------------


class TestEditorCanvasZoom:
    def test_initial_zoom_is_100_percent(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        assert canvas.zoom_level == pytest.approx(1.0, abs=0.01), (
            f"Expected initial zoom level 1.0 (100%), got {canvas.zoom_level}"
        )

    def test_zoom_in_increases_level(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        original = canvas.zoom_level
        canvas.zoom_in()
        assert canvas.zoom_level > original, (
            f"Expected zoom_in to increase level from {original}, got {canvas.zoom_level}"
        )

    def test_zoom_out_decreases_level(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        original = canvas.zoom_level
        canvas.zoom_out()
        assert canvas.zoom_level < original, (
            f"Expected zoom_out to decrease level from {original}, got {canvas.zoom_level}"
        )

    def test_zoom_reset_returns_to_100_percent(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        canvas.zoom_in()
        canvas.zoom_in()
        canvas.zoom_reset()
        assert canvas.zoom_level == pytest.approx(1.0, abs=0.01), (
            f"Expected zoom_reset to return to 1.0, got {canvas.zoom_level}"
        )

    def test_zoom_fit_changes_level(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        canvas.resize(200, 200)
        canvas.zoom_fit()
        # After fit, zoom level should be computed from viewport/image ratio
        assert canvas.zoom_level > 0, (
            f"Expected positive zoom level after zoom_fit, got {canvas.zoom_level}"
        )

    def test_zoom_in_respects_max_limit(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        for _ in range(100):
            canvas.zoom_in()
        assert canvas.zoom_level <= 16.0, (
            f"Expected zoom capped at 16.0, got {canvas.zoom_level}"
        )

    def test_zoom_out_respects_min_limit(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        for _ in range(100):
            canvas.zoom_out()
        assert canvas.zoom_level >= 0.1, (
            f"Expected zoom capped at >= 0.1, got {canvas.zoom_level}"
        )


class TestEditorWindowZoomControls:
    def test_zoom_button_shows_100_initially(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        assert window._zoom_button.text() == "100%", (
            f"Expected zoom button '100%' initially, got '{window._zoom_button.text()}'"
        )

    def test_zoom_in_updates_zoom_display(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._zoom_in()
        label = window._zoom_button.text()
        assert "%" in label, (
            f"Expected zoom button to contain '%' after zoom in, got '{label}'"
        )
        pct = int(label.replace("%", ""))
        assert pct > 100, (
            f"Expected zoom percentage > 100 after zoom_in, got {pct}"
        )

    def test_zoom_out_updates_zoom_display(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._zoom_out()
        label = window._zoom_button.text()
        pct = int(label.replace("%", ""))
        assert pct < 100, (
            f"Expected zoom percentage < 100 after zoom_out, got {pct}"
        )

    def test_zoom_100_resets_display(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._zoom_in()
        window._zoom_100()
        assert window._zoom_button.text() == "100%", (
            f"Expected zoom button '100%' after reset, got '{window._zoom_button.text()}'"
        )

    def test_ctrl_scroll_updates_zoom_display(self, qapp, tmp_config) -> None:
        """Zoom via Ctrl+scroll should update the footer zoom display."""
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent

        window = EditorWindow(QPixmap(200, 200), tmp_config)
        # Simulate Ctrl+scroll up
        event = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100),
            QPoint(0, 120), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        window._canvas.wheelEvent(event)
        pct = int(window._zoom_button.text().replace("%", ""))
        assert pct > 100, (
            f"Expected zoom > 100% after Ctrl+scroll up, got {pct}%"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — image boundary
# ---------------------------------------------------------------------------


class TestEditorCanvasImageBoundary:
    def test_boundary_item_created_on_set_image(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        assert canvas._boundary_item is not None, (
            "Expected _boundary_item to be created when image is set"
        )

    def test_boundary_rect_matches_image_size(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 150)
        canvas.set_image(pixmap)
        br = canvas._boundary_item.rect()
        assert br.width() == pytest.approx(200, abs=1), (
            f"Expected boundary width 200, got {br.width()}"
        )
        assert br.height() == pytest.approx(150, abs=1), (
            f"Expected boundary height 150, got {br.height()}"
        )

    def test_boundary_not_selectable(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        flags = canvas._boundary_item.flags()
        is_selectable = bool(
            flags & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        assert not is_selectable, (
            "Expected boundary item to NOT be selectable"
        )

    def test_boundary_not_movable(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        flags = canvas._boundary_item.flags()
        is_movable = bool(
            flags & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        assert not is_movable, (
            "Expected boundary item to NOT be movable"
        )

    def test_delete_does_not_remove_boundary(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        # Force-select boundary to test protection
        canvas._boundary_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True
        )
        canvas._boundary_item.setSelected(True)
        canvas._delete_selected_items()
        assert canvas._boundary_item in canvas.scene.items(), (
            "Expected boundary item to remain in scene after _delete_selected_items"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — undo via add_item_undoable
# ---------------------------------------------------------------------------


class TestEditorCanvasUndoIntegration:
    def test_add_item_undoable_registers_with_history(self, qapp) -> None:
        from verdiclip.editor.history import EditorHistory
        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        item = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item, "Test rect")

        assert history.can_undo, (
            "Expected history.can_undo to be True after add_item_undoable"
        )

    def test_undo_removes_item_added_via_undoable(self, qapp) -> None:
        from verdiclip.editor.history import EditorHistory
        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        item = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item, "Test rect")

        assert item in canvas.scene.items(), (
            "Pre-condition: item should be in scene before undo"
        )
        history.undo()
        assert item not in canvas.scene.items(), (
            "Expected item to be removed from scene after undo"
        )

    def test_redo_restores_undone_item(self, qapp) -> None:
        from verdiclip.editor.history import EditorHistory
        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        item = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item, "Test rect")

        history.undo()
        history.redo()
        assert item in canvas.scene.items(), (
            "Expected item to be restored to scene after redo"
        )

    def test_delete_with_history_is_undoable(self, qapp) -> None:
        from verdiclip.editor.history import EditorHistory
        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        item = canvas.scene.addRect(10, 10, 50, 50)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setSelected(True)
        canvas._delete_selected_items()

        assert item not in canvas.scene.items(), (
            "Pre-condition: item should be removed after delete"
        )
        history.undo()
        assert item in canvas.scene.items(), (
            "Expected deleted item to be restored after undo"
        )

    def test_add_item_undoable_without_history_is_noop(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        # No history set — should not crash
        item = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item, "Test rect")
        assert item in canvas.scene.items(), (
            "Item should remain in scene even without history configured"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — flattened pixmap export
# ---------------------------------------------------------------------------


class TestEditorCanvasExport:
    def test_flattened_pixmap_uses_image_bounds(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 150)
        pixmap.fill(Qt.GlobalColor.blue)
        canvas.set_image(pixmap)

        result = canvas.get_flattened_pixmap()
        assert result.width() == 200, (
            f"Expected flattened pixmap width 200, got {result.width()}"
        )
        assert result.height() == 150, (
            f"Expected flattened pixmap height 150, got {result.height()}"
        )

    def test_flattened_pixmap_excludes_boundary_item(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.red)
        canvas.set_image(pixmap)

        # Boundary should be temporarily hidden during export
        assert canvas._boundary_item.isVisible(), (
            "Pre-condition: boundary should be visible before export"
        )
        canvas.get_flattened_pixmap()
        assert canvas._boundary_item.isVisible(), (
            "Boundary should be visible again after export"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — crop key handling
# ---------------------------------------------------------------------------


class TestEditorCanvasCropKeyHandling:
    def test_enter_calls_apply_crop_on_crop_tool(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        mock_tool = MagicMock()
        mock_tool.apply_crop = MagicMock()
        canvas._current_tool = mock_tool

        event = _make_key_event(Qt.Key.Key_Return)
        canvas.keyPressEvent(event)
        mock_tool.apply_crop.assert_called_once(), (
            "Expected Enter key to call apply_crop on crop tool"
        )

    def test_escape_calls_cancel_crop_on_crop_tool(self, qapp) -> None:
        canvas = _make_canvas_with_image()
        mock_tool = MagicMock()
        mock_tool.cancel_crop = MagicMock()
        canvas._current_tool = mock_tool

        event = _make_key_event(Qt.Key.Key_Escape)
        canvas.keyPressEvent(event)
        mock_tool.cancel_crop.assert_called_once(), (
            "Expected Escape key to call cancel_crop on crop tool"
        )


# ---------------------------------------------------------------------------
# EditorWindow — View menu exists
# ---------------------------------------------------------------------------


class TestEditorWindowViewMenu:
    def test_view_menu_exists(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        menubar = window.menuBar()
        view_found = any(
            action.text().replace("&", "") == "View"
            for action in menubar.actions()
        )
        assert view_found, (
            "Expected a 'View' menu in the menu bar"
        )

    def test_view_menu_has_zoom_actions(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        menubar = window.menuBar()
        view_menu = None
        for action in menubar.actions():
            if action.text().replace("&", "") == "View":
                view_menu = action.menu()
                break

        assert view_menu is not None, "Expected to find View menu"
        action_texts = [a.text().replace("&", "") for a in view_menu.actions()]
        assert any("In" in t for t in action_texts), (
            f"Expected 'Zoom In' in View menu, got actions: {action_texts}"
        )
        assert any("Out" in t for t in action_texts), (
            f"Expected 'Zoom Out' in View menu, got actions: {action_texts}"
        )
        assert any("100" in t for t in action_texts), (
            f"Expected 'Zoom 100%' in View menu, got actions: {action_texts}"
        )
        assert any("Fit" in t for t in action_texts), (
            f"Expected 'Zoom to Fit' in View menu, got actions: {action_texts}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — Esc key cascade
# ---------------------------------------------------------------------------


class TestEditorCanvasEscKey:
    def test_escape_deselects_selected_items(self, qapp) -> None:
        """Pressing Esc when items are selected deselects all of them."""
        canvas = _make_canvas_with_image()
        from PySide6.QtWidgets import QGraphicsRectItem
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        canvas.scene.addItem(rect_item)
        rect_item.setSelected(True)
        assert rect_item.isSelected(), "Precondition: item should be selected"

        event = _make_key_event(Qt.Key.Key_Escape)
        canvas.keyPressEvent(event)

        assert not rect_item.isSelected(), (
            "Expected item to be deselected after Esc, but it is still selected"
        )

    def test_escape_emits_switch_to_select_when_nothing_selected(
        self, qapp,
    ) -> None:
        """Pressing Esc with no selection emits switch_to_select_requested."""
        canvas = _make_canvas_with_image()
        signal_received = []
        canvas.switch_to_select_requested.connect(
            lambda: signal_received.append(True)
        )

        event = _make_key_event(Qt.Key.Key_Escape)
        canvas.keyPressEvent(event)

        assert len(signal_received) == 1, (
            f"Expected switch_to_select_requested emitted once, "
            f"got {len(signal_received)} times"
        )

    def test_escape_does_not_emit_signal_when_items_deselected(
        self, qapp,
    ) -> None:
        """Esc with Select tool active deselects items without emitting switch_to_select."""
        canvas = _make_canvas_with_image()
        # Set the select tool so Esc only deselects (no tool switch needed)
        from verdiclip.editor.tools.select import SelectTool
        canvas.set_tool(SelectTool())

        from PySide6.QtWidgets import QGraphicsRectItem
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        canvas.scene.addItem(rect_item)
        rect_item.setSelected(True)

        signal_received = []
        canvas.switch_to_select_requested.connect(
            lambda: signal_received.append(True)
        )
        event = _make_key_event(Qt.Key.Key_Escape)
        canvas.keyPressEvent(event)

        assert len(signal_received) == 0, (
            "Expected no switch_to_select_requested when Select tool is active "
            f"and items were deselected, but signal was emitted {len(signal_received)} time(s)"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — Shift+scroll horizontal
# ---------------------------------------------------------------------------


class TestEditorCanvasShiftScroll:
    def test_shift_scroll_scrolls_horizontally(self, qapp) -> None:
        """Shift+scroll should change horizontal scrollbar, not zoom."""
        canvas = EditorCanvas()
        canvas.set_image(QPixmap(2000, 2000))
        canvas.resize(200, 200)
        canvas.show()

        zoom_before = canvas._zoom_level
        h_before = canvas.horizontalScrollBar().value()

        event = _make_wheel_event(
            120, Qt.KeyboardModifier.ShiftModifier,
        )
        canvas.wheelEvent(event)

        h_after = canvas.horizontalScrollBar().value()
        assert canvas._zoom_level == zoom_before, (
            f"Expected zoom unchanged with Shift+scroll, "
            f"got {canvas._zoom_level} (was {zoom_before})"
        )
        assert h_after != h_before, (
            f"Expected horizontal scroll to change, "
            f"but value remained {h_before}"
        )


# ---------------------------------------------------------------------------
# EditorWindow — zoom slider popup
# ---------------------------------------------------------------------------


class TestEditorWindowZoomSlider:
    def test_zoom_slider_initially_hidden(self, qapp, tmp_config) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        assert not window._zoom_slider_widget.isVisible(), (
            "Expected zoom slider popup to be hidden initially"
        )

    def test_toggle_zoom_slider_shows_popup(
        self, qapp, tmp_config,
    ) -> None:
        """Calling _toggle_zoom_slider makes the popup visible."""
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window.show()
        assert not window._zoom_slider_widget.isVisible(), (
            "Precondition: slider should be hidden before toggle"
        )

        window._toggle_zoom_slider()

        assert window._zoom_slider_widget.isVisible(), (
            "Expected zoom slider popup to be visible after toggle"
        )

    def test_zoom_slider_changes_zoom_level(
        self, qapp, tmp_config,
    ) -> None:
        """Setting the zoom slider value changes the canvas zoom level."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(200)
        actual_pct = int(window._canvas.zoom_level * 100)
        assert 195 <= actual_pct <= 205, (
            f"Expected zoom ~200% after slider set to 200, "
            f"got {actual_pct}%"
        )


# ---------------------------------------------------------------------------
# EditorWindow — _switch_to_select
# ---------------------------------------------------------------------------


class TestEditorWindowSwitchToSelect:
    def test_switch_to_select_sets_select_tool(
        self, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        # First switch to a non-select tool
        window._toolbar.set_tool(ToolType.RECTANGLE)
        assert window._toolbar.current_tool == ToolType.RECTANGLE, (
            "Precondition: should be on RECTANGLE tool"
        )

        window._switch_to_select()

        assert window._toolbar.current_tool == ToolType.SELECT, (
            f"Expected SELECT tool after _switch_to_select, "
            f"got {window._toolbar.current_tool}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — zoom_changed signal
# ---------------------------------------------------------------------------


class TestEditorCanvasZoomSignal:
    def test_zoom_in_emits_zoom_changed(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas.set_image(QPixmap(100, 100))
        received = []
        canvas.zoom_changed.connect(lambda v: received.append(v))

        canvas.zoom_in()

        assert len(received) == 1, (
            f"Expected zoom_changed emitted once, got {len(received)} times"
        )
        assert received[0] > 1.0, (
            f"Expected zoom level > 1.0 after zoom_in, got {received[0]}"
        )

    def test_ctrl_scroll_emits_zoom_changed(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas.set_image(QPixmap(100, 100))
        received = []
        canvas.zoom_changed.connect(lambda v: received.append(v))

        event = _make_wheel_event(
            120, Qt.KeyboardModifier.ControlModifier,
        )
        canvas.wheelEvent(event)

        assert len(received) == 1, (
            f"Expected zoom_changed emitted once on Ctrl+scroll, "
            f"got {len(received)} times"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — undo after set_image (crash fix)
# ---------------------------------------------------------------------------


class TestEditorCanvasUndoAfterSetImage:
    def test_set_image_clears_history_before_scene(self, qapp) -> None:
        """set_image should clear undo history before scene.clear() to avoid crashes."""
        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        # Add some items and undo history
        item = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item, "Test rect")
        assert history.can_undo(), (
            "Precondition: history should have undo entries"
        )

        # Load a new image — this should clear history first, then scene
        new_pixmap = QPixmap(300, 300)
        new_pixmap.fill(Qt.GlobalColor.red)
        canvas.set_image(new_pixmap)

        # History should be cleared — undo should not crash
        assert not history.can_undo(), (
            "Expected history to be cleared after set_image"
        )

    def test_undo_after_set_image_does_not_crash(self, qapp) -> None:
        """Calling undo after set_image should be safe (no RuntimeError)."""
        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        # Build up undo entries
        item1 = canvas.scene.addRect(10, 10, 50, 50)
        canvas.add_item_undoable(item1, "Rect 1")
        item2 = canvas.scene.addRect(60, 60, 50, 50)
        canvas.add_item_undoable(item2, "Rect 2")

        # Replace image — destroys old scene items
        new_pixmap = QPixmap(300, 300)
        new_pixmap.fill(Qt.GlobalColor.green)
        canvas.set_image(new_pixmap)

        # This should NOT raise RuntimeError
        history.undo()  # no-op since history was cleared


# ---------------------------------------------------------------------------
# EditorCanvas — zoom_to_point
# ---------------------------------------------------------------------------


class TestEditorCanvasZoomToPoint:
    def test_zoom_to_point_changes_level(self, qapp) -> None:
        """_zoom_to_point should change the zoom level."""
        canvas = _make_canvas_with_image()
        original = canvas.zoom_level

        center = canvas.viewport().rect().center()
        canvas._zoom_to_point(1.15, center)

        assert canvas.zoom_level != pytest.approx(original, abs=0.01), (
            f"Expected zoom level to change from {original}, "
            f"got {canvas.zoom_level}"
        )

    def test_zoom_to_point_emits_signal(self, qapp) -> None:
        """_zoom_to_point should emit zoom_changed signal."""
        canvas = _make_canvas_with_image()
        received = []
        canvas.zoom_changed.connect(lambda v: received.append(v))

        center = canvas.viewport().rect().center()
        canvas._zoom_to_point(1.15, center)

        assert len(received) == 1, (
            f"Expected zoom_changed emitted once, got {len(received)} times"
        )

    def test_zoom_to_point_respects_max_limit(self, qapp) -> None:
        """_zoom_to_point should not exceed MAX_ZOOM (16.0)."""
        canvas = _make_canvas_with_image()
        center = canvas.viewport().rect().center()

        # Try to zoom way past max
        for _ in range(100):
            canvas._zoom_to_point(1.5, center)

        assert canvas.zoom_level <= 16.0, (
            f"Expected zoom capped at 16.0, got {canvas.zoom_level}"
        )

    def test_zoom_to_point_respects_min_limit(self, qapp) -> None:
        """_zoom_to_point should not go below MIN_ZOOM (0.1)."""
        canvas = _make_canvas_with_image()
        center = canvas.viewport().rect().center()

        # Try to zoom way below min
        for _ in range(100):
            canvas._zoom_to_point(0.5, center)

        assert canvas.zoom_level >= 0.1, (
            f"Expected zoom capped at >= 0.1, got {canvas.zoom_level}"
        )

    def test_zoom_to_point_noop_at_boundary(self, qapp) -> None:
        """Zooming beyond limits should be a no-op (level unchanged)."""
        canvas = _make_canvas_with_image()
        center = canvas.viewport().rect().center()

        # Push to max
        for _ in range(100):
            canvas._zoom_to_point(1.5, center)
        level_at_max = canvas.zoom_level

        # One more should be a no-op
        canvas._zoom_to_point(1.5, center)
        assert canvas.zoom_level == pytest.approx(level_at_max, abs=0.001), (
            f"Expected no change at max, got {canvas.zoom_level}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — zoom_fit
# ---------------------------------------------------------------------------


class TestEditorCanvasZoomFit:
    def test_zoom_fit_updates_zoom_level(self, qapp) -> None:
        """zoom_fit should update _zoom_level from the resulting transform."""
        canvas = EditorCanvas()
        pixmap = QPixmap(800, 600)
        pixmap.fill(Qt.GlobalColor.blue)
        canvas.set_image(pixmap)
        canvas.resize(200, 200)

        canvas.zoom_fit()

        assert canvas.zoom_level > 0, (
            f"Expected positive zoom level after zoom_fit, got {canvas.zoom_level}"
        )

    def test_zoom_fit_emits_signal(self, qapp) -> None:
        """zoom_fit should emit zoom_changed signal."""
        canvas = _make_canvas_with_image()
        canvas.resize(200, 200)
        received = []
        canvas.zoom_changed.connect(lambda v: received.append(v))

        canvas.zoom_fit()

        assert len(received) == 1, (
            f"Expected zoom_changed emitted once, got {len(received)} times"
        )

    def test_zoom_fit_after_zoom_in(self, qapp) -> None:
        """zoom_fit after zoom_in should change the zoom level."""
        canvas = EditorCanvas()
        pixmap = QPixmap(800, 600)
        pixmap.fill(Qt.GlobalColor.blue)
        canvas.set_image(pixmap)
        canvas.resize(200, 200)

        canvas.zoom_in()
        canvas.zoom_in()
        zoomed_level = canvas.zoom_level

        canvas.zoom_fit()

        assert canvas.zoom_level != pytest.approx(zoomed_level, abs=0.01), (
            f"Expected zoom_fit to change level from {zoomed_level}, "
            f"got {canvas.zoom_level}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — zoom_reset
# ---------------------------------------------------------------------------


class TestEditorCanvasZoomReset:
    def test_zoom_reset_returns_to_1_0_after_zoom_in(self, qapp) -> None:
        """After zoom_in, zoom_reset should return to exactly 1.0."""
        canvas = _make_canvas_with_image()
        canvas.zoom_in()
        canvas.zoom_in()
        canvas.zoom_in()

        assert canvas.zoom_level != pytest.approx(1.0, abs=0.01), (
            "Precondition: zoom should be != 1.0 after zoom_in"
        )

        canvas.zoom_reset()

        assert canvas.zoom_level == pytest.approx(1.0, abs=0.01), (
            f"Expected zoom_reset to return to 1.0, got {canvas.zoom_level}"
        )

    def test_zoom_reset_returns_to_1_0_after_zoom_out(self, qapp) -> None:
        """After zoom_out, zoom_reset should return to exactly 1.0."""
        canvas = _make_canvas_with_image()
        canvas.zoom_out()
        canvas.zoom_out()

        canvas.zoom_reset()

        assert canvas.zoom_level == pytest.approx(1.0, abs=0.01), (
            f"Expected zoom_reset to return to 1.0, got {canvas.zoom_level}"
        )

    def test_zoom_reset_emits_signal(self, qapp) -> None:
        """zoom_reset should emit zoom_changed with 1.0."""
        canvas = _make_canvas_with_image()
        canvas.zoom_in()

        received = []
        canvas.zoom_changed.connect(lambda v: received.append(v))
        canvas.zoom_reset()

        assert len(received) == 1, (
            f"Expected zoom_changed emitted once, got {len(received)} times"
        )
        assert received[0] == pytest.approx(1.0, abs=0.01), (
            f"Expected zoom_changed signal value 1.0, got {received[0]}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — Ctrl+A select all integration
# ---------------------------------------------------------------------------


class TestEditorCanvasCtrlA:
    def test_ctrl_a_selects_all_annotations(self, qapp) -> None:
        """Ctrl+A with SelectTool active should select all annotation items."""
        from verdiclip.editor.tools.select import SelectTool

        canvas = _make_canvas_with_image()
        tool = SelectTool()
        canvas.set_tool(tool)

        item1 = canvas.scene.addRect(10, 10, 30, 30)
        item1.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item1.setZValue(0)

        item2 = canvas.scene.addRect(60, 60, 30, 30)
        item2.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item2.setZValue(0)

        # Simulate Ctrl+A
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )
        canvas.keyPressEvent(event)

        assert item1.isSelected(), (
            "Expected item1 to be selected after Ctrl+A"
        )
        assert item2.isSelected(), (
            "Expected item2 to be selected after Ctrl+A"
        )


# ---------------------------------------------------------------------------
# EditorWindow — zoom slider snaps to 10% intervals
# ---------------------------------------------------------------------------


class TestEditorWindowZoomSliderSnap:
    def test_slider_43_snaps_to_40(self, qapp, tmp_config) -> None:
        """Setting zoom slider to 43 should snap to 40%."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(43)
        assert window._zoom_slider.value() == 40, (
            f"Expected slider snapped to 40, got {window._zoom_slider.value()}"
        )

    def test_slider_47_snaps_to_50(self, qapp, tmp_config) -> None:
        """Setting zoom slider to 47 should snap to 50%."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(47)
        assert window._zoom_slider.value() == 50, (
            f"Expected slider snapped to 50, got {window._zoom_slider.value()}"
        )

    def test_slider_15_snaps_to_20(self, qapp, tmp_config) -> None:
        """Setting zoom slider to 15 should snap to 20%."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(15)
        assert window._zoom_slider.value() == 20, (
            f"Expected slider snapped to 20, got {window._zoom_slider.value()}"
        )

    def test_slider_exact_multiple_unchanged(self, qapp, tmp_config) -> None:
        """Setting zoom slider to an exact multiple (e.g. 100) stays at 100."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(100)
        assert window._zoom_slider.value() == 100, (
            f"Expected slider at 100, got {window._zoom_slider.value()}"
        )

    def test_slider_min_clamp_10(self, qapp, tmp_config) -> None:
        """Slider snaps value 10 to 10 (the minimum)."""
        window = EditorWindow(QPixmap(200, 200), tmp_config)
        window._zoom_slider.setValue(10)
        assert window._zoom_slider.value() == 10, (
            f"Expected slider at 10 (min), got {window._zoom_slider.value()}"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — crop is undoable
# ---------------------------------------------------------------------------


class TestEditorCanvasCropUndoable:
    def test_crop_undoable_changes_background(self, qapp) -> None:
        """crop_undoable should replace the background with the cropped image."""
        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()  # 100x100 blue
        history = EditorHistory()
        canvas.set_history(history)

        # Crop to a 50x50 region
        cropped = QPixmap(50, 50)
        cropped.fill(Qt.GlobalColor.red)
        canvas.crop_undoable(cropped, [], [], (0.0, 0.0))
        assert canvas.pixmap_item.pixmap().width() == 50, (
            f"Expected cropped width 50, got {canvas.pixmap_item.pixmap().width()}"
        )
        assert canvas.pixmap_item.pixmap().height() == 50, (
            f"Expected cropped height 50, got {canvas.pixmap_item.pixmap().height()}"
        )

    def test_crop_undoable_can_be_undone(self, qapp) -> None:
        """Undo after crop should restore the original image dimensions."""
        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()  # 100x100 blue
        history = EditorHistory()
        canvas.set_history(history)

        cropped = QPixmap(50, 50)
        cropped.fill(Qt.GlobalColor.red)
        canvas.crop_undoable(cropped, [], [], (0.0, 0.0))

        assert history.can_undo() is True, (
            "Expected can_undo() True after crop_undoable"
        )

        history.undo()

        assert canvas.pixmap_item.pixmap().width() == 100, (
            f"Expected restored width 100 after undo, "
            f"got {canvas.pixmap_item.pixmap().width()}"
        )
        assert canvas.pixmap_item.pixmap().height() == 100, (
            f"Expected restored height 100 after undo, "
            f"got {canvas.pixmap_item.pixmap().height()}"
        )

    def test_crop_undoable_preserves_history(self, qapp) -> None:
        """Crop should NOT clear undo history — prior actions should be undoable."""
        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()  # 100x100
        history = EditorHistory()
        canvas.set_history(history)

        # Add an annotation first
        item = canvas.scene.addRect(10, 10, 30, 30)
        canvas.add_item_undoable(item, "Test rect")
        assert history.can_undo() is True, (
            "Precondition: history should have undo after adding item"
        )

        # Now crop
        cropped = QPixmap(80, 80)
        cropped.fill(Qt.GlobalColor.green)
        canvas.crop_undoable(cropped, [], [], (0.0, 0.0))

        # Undo crop
        history.undo()
        assert canvas.pixmap_item.pixmap().width() == 100, (
            f"Expected restored width 100 after undoing crop, "
            f"got {canvas.pixmap_item.pixmap().width()}"
        )

        # Undo the annotation add — should still be possible
        assert history.can_undo() is True, (
            "Expected can_undo() True for prior annotation after undoing crop"
        )

    def test_crop_undoable_removes_items_outside_crop(self, qapp) -> None:
        """Items passed as removed_items should be removed from the scene."""
        from PySide6.QtWidgets import QGraphicsRectItem

        from verdiclip.editor.history import EditorHistory

        canvas = _make_canvas_with_image()
        history = EditorHistory()
        canvas.set_history(history)

        outside_item = QGraphicsRectItem(70, 70, 40, 40)
        canvas.scene.addItem(outside_item)

        cropped = QPixmap(50, 50)
        cropped.fill(Qt.GlobalColor.red)
        canvas.crop_undoable(cropped, [outside_item], [(outside_item, 70.0, 70.0)], (0.0, 0.0))

        assert outside_item not in canvas.scene.items(), (
            "Expected outside_item removed from scene after crop"
        )

        # Undo should restore the removed item
        history.undo()
        assert outside_item in canvas.scene.items(), (
            "Expected outside_item restored to scene after undo"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — number editor requires double-click
# ---------------------------------------------------------------------------


class TestEditorCanvasNumberEditorDoubleClick:
    def test_selecting_number_marker_does_not_emit_editor_signal(
        self, qapp, tmp_config,
    ) -> None:
        """Selecting a NumberMarkerItem should NOT emit number_editor_requested."""
        from verdiclip.editor.tools.number import NumberMarkerItem

        window = EditorWindow(QPixmap(200, 200), tmp_config)
        canvas = window._canvas

        # Place a NumberMarkerItem in the scene
        marker = NumberMarkerItem("1", QColor(255, 0, 0), QColor(255, 255, 255))
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        marker.setPos(50, 50)
        canvas.scene.addItem(marker)

        signal_received = []
        canvas.number_editor_requested.connect(
            lambda m: signal_received.append(m)
        )

        # Select the marker (simulating a click)
        marker.setSelected(True)

        assert len(signal_received) == 0, (
            f"Expected no number_editor_requested on selection, "
            f"but signal was emitted {len(signal_received)} time(s)"
        )

    def test_double_click_on_number_marker_emits_editor_signal(
        self, qapp, tmp_config,
    ) -> None:
        """Double-clicking a NumberMarkerItem should emit number_editor_requested."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        from verdiclip.editor.tools.select import SelectTool

        window = EditorWindow(QPixmap(200, 200), tmp_config)
        canvas = window._canvas

        # Switch to select tool
        select_tool = SelectTool()
        canvas.set_tool(select_tool)

        # Place a NumberMarkerItem in the scene
        marker = NumberMarkerItem("1", QColor(255, 0, 0), QColor(255, 255, 255))
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        marker.setPos(50, 50)
        canvas.scene.addItem(marker)

        signal_received = []
        canvas.number_editor_requested.connect(
            lambda m: signal_received.append(m)
        )

        # Patch _find_annotation_at to return the marker directly (avoids
        # coordinate mapping issues when the widget is not shown/laid out)
        with patch.object(select_tool, "_find_annotation_at", return_value=marker):
            event = _make_mouse_event(
                QMouseEvent.Type.MouseButtonDblClick,
                Qt.MouseButton.LeftButton,
                QPointF(50, 50),
            )
            canvas.mouseDoubleClickEvent(event)

        assert len(signal_received) == 1, (
            f"Expected number_editor_requested emitted once on double-click, "
            f"got {len(signal_received)} times"
        )
        assert signal_received[0] is marker, (
            "Expected signal to pass the clicked NumberMarkerItem"
        )


# ---------------------------------------------------------------------------
# EditorCanvas — element copy-paste (Ctrl+C / Ctrl+V)
# ---------------------------------------------------------------------------


class TestEditorCanvasElementCopyPaste:
    def test_serialise_deserialise_rect_round_trip(self, qapp) -> None:
        """A rectangle should survive serialise → deserialise round-trip."""
        from PySide6.QtGui import QBrush, QPen
        from PySide6.QtWidgets import QGraphicsRectItem

        from verdiclip.editor.canvas import (
            _deserialise_items,
            _serialise_items,
        )

        rect = QGraphicsRectItem(0, 0, 50, 30)
        rect.setPen(QPen(QColor("#FF0000"), 3.0))
        rect.setBrush(QBrush(QColor("#00FF00")))
        rect.setPos(10, 20)
        scene = QGraphicsScene()
        scene.addItem(rect)

        data = _serialise_items([rect])
        assert len(data) == 1
        assert data[0]["type"] == "rect"

        items = _deserialise_items(data)
        assert len(items) == 1
        restored = items[0]
        assert isinstance(restored, QGraphicsRectItem)
        assert restored.rect().width() == pytest.approx(50, abs=1)
        assert restored.rect().height() == pytest.approx(30, abs=1)

    def test_serialise_deserialise_arrow_round_trip(self, qapp) -> None:
        """An ArrowItem should survive serialise → deserialise round-trip."""
        from verdiclip.editor.canvas import (
            _deserialise_items,
            _serialise_items,
        )
        from verdiclip.editor.tools.arrow import ArrowItem

        arrow = ArrowItem(QPointF(0, 0), QPointF(80, 60), QColor("#0000FF"), 4)
        arrow.setPos(5, 5)
        scene = QGraphicsScene()
        scene.addItem(arrow)

        data = _serialise_items([arrow])
        assert len(data) == 1
        assert data[0]["type"] == "arrow"

        items = _deserialise_items(data)
        assert len(items) == 1
        restored = items[0]
        assert isinstance(restored, ArrowItem)

    def test_paste_offsets_position(self, qapp, tmp_config) -> None:
        """Pasted elements should be offset from originals."""
        from PySide6.QtWidgets import QGraphicsRectItem

        from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY
        from verdiclip.editor.canvas import _serialise_items

        window = EditorWindow(QPixmap(200, 200), tmp_config)
        canvas = window._canvas
        rect = QGraphicsRectItem(0, 0, 30, 30)
        rect.setPos(10, 10)
        canvas.scene.addItem(rect)

        data = _serialise_items([rect])
        window._element_clipboard = data

        window._paste_elements()
        # Filter to annotation-layer rects, excluding bg, boundary, and handles
        all_rects = [
            i for i in canvas.scene.items()
            if isinstance(i, QGraphicsRectItem) and i is not rect
            and not isinstance(i, QGraphicsPixmapItem)
            and Z_BACKGROUND < i.zValue() < Z_BOUNDARY
            and i.rect().width() >= 10 and i.rect().height() >= 10
            and i.parentItem() is None
        ]
        assert len(all_rects) >= 1, "Expected a pasted rectangle in the scene"
        pasted = all_rects[0]
        assert pasted.pos().x() == pytest.approx(25, abs=1)
        assert pasted.pos().y() == pytest.approx(25, abs=1)


# ---------------------------------------------------------------------------
# EditorCanvas — default toolbar visibility
# ---------------------------------------------------------------------------


class TestEditorCanvasDefaultToolbar:
    def test_select_tool_shows_stroke_width_fill(self, qapp, tmp_config) -> None:
        """SELECT tool with no selection should show stroke, fill, and width."""
        from unittest.mock import patch as _patch

        window = EditorWindow(QPixmap(100, 100), tmp_config)
        with _patch.object(window._properties, "set_visible_properties") as mock_svp:
            window._update_properties_visibility(ToolType.SELECT)
            mock_svp.assert_called_once_with(
                stroke=True, fill=True, width=True, font=False, caps=False,
            )

    def test_empty_selection_restores_default_toolbar(self, qapp, tmp_config) -> None:
        """When selection becomes empty, toolbar should restore default properties."""
        from unittest.mock import patch as _patch

        window = EditorWindow(QPixmap(100, 100), tmp_config)
        canvas = window._canvas

        # Ensure we're on SELECT tool
        from verdiclip.editor.tools.select import SelectTool
        canvas.set_tool(SelectTool())

        item = canvas.scene.addRect(10, 10, 30, 30)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setSelected(True)
        item.setSelected(False)

        with _patch.object(window._properties, "set_visible_properties") as mock_svp:
            window._on_selection_changed()
            mock_svp.assert_called_once_with(
                stroke=True, fill=True, width=True, font=False, caps=False,
            )


# ---------------------------------------------------------------------------
# EditorWindow — Esc forwarded from toolbar to canvas
# ---------------------------------------------------------------------------


class TestEditorWindowEscForward:
    def test_esc_forwarded_to_canvas(self, qapp, tmp_config) -> None:
        """Pressing Esc on EditorWindow should forward to canvas."""
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch

        window = EditorWindow(QPixmap(100, 100), tmp_config)

        with _patch.object(window._canvas, "keyPressEvent") as mock_kpe:
            event = MagicMock()
            event.key.return_value = Qt.Key.Key_Escape
            event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
            window.keyPressEvent(event)
            mock_kpe.assert_called_once()

    def test_arrow_keys_forwarded_to_canvas(self, qapp, tmp_config) -> None:
        """Pressing arrow keys on EditorWindow should forward to canvas."""
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch

        window = EditorWindow(QPixmap(100, 100), tmp_config)

        with _patch.object(window._canvas, "keyPressEvent") as mock_kpe:
            event = MagicMock()
            event.key.return_value = Qt.Key.Key_Right
            event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
            window.keyPressEvent(event)
            mock_kpe.assert_called_once()


# ---------------------------------------------------------------------------
# DPI awareness — screen capture pixmap
# ---------------------------------------------------------------------------


class TestScreenCaptureDPI:
    def test_mss_pixmap_device_pixel_ratio_is_one(self, qapp) -> None:
        """Captured pixmaps should have devicePixelRatio == 1."""
        from verdiclip.capture.screen import ScreenCapture

        pixmap = ScreenCapture.capture_primary_monitor()
        assert pixmap.devicePixelRatio() == 1.0, (
            f"Expected devicePixelRatio 1.0, got {pixmap.devicePixelRatio()}"
        )
