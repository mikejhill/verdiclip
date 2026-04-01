"""Tests for verdiclip.editor.canvas — EditorCanvas and EditorWindow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
        assert isinstance(canvas.scene, QGraphicsScene)

    def test_no_pixmap_item_initially(self, qapp) -> None:
        canvas = EditorCanvas()
        assert canvas.pixmap_item is None


class TestEditorCanvasSetImage:
    def test_adds_pixmap_item(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert isinstance(canvas.pixmap_item, QGraphicsPixmapItem)

    def test_sets_scene_rect(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        rect = canvas.scene.sceneRect()
        assert rect.width() == 100
        assert rect.height() == 100

    def test_updates_zoom(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert canvas._zoom_level == canvas.transform().m11()


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
        assert canvas._current_tool is None


class TestEditorCanvasGetFlattenedPixmap:
    def test_returns_pixmap_with_correct_dimensions(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        result = canvas.get_flattened_pixmap()
        assert isinstance(result, QPixmap)
        assert result.width() == 100
        assert result.height() == 100


class TestEditorCanvasProperties:
    def test_scene_returns_graphics_scene(self, qapp) -> None:
        canvas = EditorCanvas()
        assert isinstance(canvas.scene, QGraphicsScene)

    def test_pixmap_item_none_initially(self, qapp) -> None:
        canvas = EditorCanvas()
        assert canvas.pixmap_item is None

    def test_pixmap_item_after_set_image(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        canvas.set_image(pixmap)
        assert canvas.pixmap_item is not None


# ---------------------------------------------------------------------------
# EditorWindow
# ---------------------------------------------------------------------------


class TestEditorWindowCreation:
    def test_has_canvas(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert isinstance(window._canvas, EditorCanvas)

    def test_has_toolbar(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._toolbar is not None

    def test_has_properties_panel(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._properties is not None

    def test_has_statusbar(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window._statusbar is not None


class TestEditorWindowToolChanged:
    def test_updates_status_bar_message(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        window._on_tool_changed(ToolType.RECTANGLE)
        assert "Rectangle" in window._statusbar.currentMessage()


class TestEditorWindowTitle:
    def test_title_contains_verdiclip(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert "VerdiClip" in window.windowTitle()


class TestEditorWindowMinimumSize:
    def test_minimum_size_is_800x600(self, qapp, tmp_config) -> None:
        pixmap = QPixmap(100, 100)
        window = EditorWindow(pixmap, tmp_config)
        assert window.minimumWidth() == 800
        assert window.minimumHeight() == 600


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
        assert canvas._zoom_level > zoom_before

    def test_ctrl_scroll_down_zooms_out(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        zoom_before = canvas._zoom_level
        event = _make_wheel_event(
            -120, Qt.KeyboardModifier.ControlModifier,
        )
        canvas.wheelEvent(event)
        assert canvas._zoom_level < zoom_before

    def test_no_ctrl_passes_to_super(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        canvas.set_image(pixmap)
        zoom_before = canvas._zoom_level
        event = _make_wheel_event(120)
        canvas.wheelEvent(event)
        assert canvas._zoom_level == zoom_before


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
        assert canvas._is_panning is True

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

    def test_no_tool_calls_super(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas.set_tool(None)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseButtonPress,
            Qt.MouseButton.LeftButton,
        )
        canvas.mousePressEvent(event)
        assert canvas._is_panning is False


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
        assert event.isAccepted()

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

    def test_no_tool_no_pan_calls_super(self, qapp) -> None:
        canvas = EditorCanvas()
        canvas._is_panning = False
        canvas.set_tool(None)
        event = _make_mouse_event(
            QMouseEvent.Type.MouseMove,
            Qt.MouseButton.NoButton,
        )
        canvas.mouseMoveEvent(event)
        assert canvas._is_panning is False


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
        assert canvas._is_panning is False

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
        assert canvas._is_panning is False


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
        assert rect.width() == 80
        assert rect.height() == 60

    @patch("verdiclip.editor.canvas.QFileDialog.getOpenFileName")
    def test_cancelled_dialog_does_nothing(
        self, mock_dialog, qapp, tmp_config,
    ) -> None:
        mock_dialog.return_value = ("", "")
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._open_file()
        rect = window._canvas.scene.sceneRect()
        assert rect.width() == 100


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
        assert "clipboard" in window._statusbar.currentMessage().lower()


class TestEditorWindowPrint:
    @patch("verdiclip.export.printer.PrinterExporter.print_pixmap")
    def test_delegates_to_printer_exporter(
        self, mock_print, qapp, tmp_config,
    ) -> None:
        window = EditorWindow(QPixmap(100, 100), tmp_config)
        window._print()
        mock_print.assert_called_once()
