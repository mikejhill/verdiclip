"""Tests for verdiclip.editor.canvas — EditorCanvas and EditorWindow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene

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
        assert rect.width() == 100, (
            f"Expected scene rect width 100, got {rect.width()}"
        )
        assert rect.height() == 100, (
            f"Expected scene rect height 100, got {rect.height()}"
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
        assert rect.width() == 80, (
            f"Expected scene width 80 after open, got {rect.width()}"
        )
        assert rect.height() == 60, (
            f"Expected scene height 60 after open, got {rect.height()}"
        )

    @patch("verdiclip.editor.canvas.QFileDialog.getOpenFileName")
    def test_cancelled_dialog_does_nothing(
        self, mock_dialog, qapp, tmp_config,
    ) -> None:
        mock_dialog.return_value = ("", "")
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._open_file()
        rect = window._canvas.scene.sceneRect()
        assert rect.width() == 100, (
            f"Expected scene width unchanged at 100 after cancel, got {rect.width()}"
        )


class TestEditorWindowSaveFile:
    @patch("verdiclip.export.file_export.FileExporter.save_with_dialog")
    def test_delegates_to_file_exporter(
        self, mock_save, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file()
        mock_save.assert_called_once()


class TestEditorWindowSaveFileAs:
    @patch("verdiclip.export.file_export.FileExporter.save_as")
    def test_delegates_to_file_exporter(
        self, mock_save_as, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._save_file_as()
        mock_save_as.assert_called_once()


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

    def test_properties_obfuscation_strength_signal_connected(
        self, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        obfuscate_tool = window._tools[ToolType.OBFUSCATE]
        obfuscate_tool.set_block_size = MagicMock()
        window._properties.obfuscation_strength_changed.emit(16)
        obfuscate_tool.set_block_size.assert_called_once_with(16), (
            "Expected obfuscation_strength_changed signal to reach ObfuscateTool's set_block_size"
        )
