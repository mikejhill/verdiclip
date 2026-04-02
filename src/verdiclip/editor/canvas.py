"""Editor window with annotation canvas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QKeySequence,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMainWindow,
    QSlider,
    QStatusBar,
    QWidget,
)

from verdiclip import __app_name__
from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY
from verdiclip.editor.history import EditorHistory
from verdiclip.editor.properties import PropertiesPanel
from verdiclip.editor.toolbar import EditorToolbar, ToolType

if TYPE_CHECKING:
    from PySide6.QtGui import (
        QKeyEvent,
        QMouseEvent,
    )

    from verdiclip.config import Config
    from verdiclip.editor.tools.base import BaseTool

logger = logging.getLogger(__name__)

_ZOOM_FACTOR = 1.15
_MIN_ZOOM = 0.1
_MAX_ZOOM = 16.0


class EditorCanvas(QGraphicsView):
    """QGraphicsView-based canvas for image editing and annotation."""

    switch_to_select_requested = Signal()
    zoom_changed = Signal(float)  # Emitted with new zoom_level after any zoom change

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        from PySide6.QtGui import QPainter
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Solid neutral background — easy to distinguish from image content
        self.setBackgroundBrush(QBrush(QColor(45, 45, 48)))

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._boundary_item: QGraphicsRectItem | None = None
        self._zoom_level: float = 1.0
        self._current_tool: BaseTool | None = None
        self._is_panning = False
        self._history: EditorHistory | None = None

    def set_image(self, pixmap: QPixmap) -> None:
        """Load an image onto the canvas at 100% zoom, centered."""
        self._scene.clear()
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._pixmap_item.setZValue(Z_BACKGROUND)
        self._scene.addItem(self._pixmap_item)

        # Draw a visible border around the image area
        img_rect = QRectF(pixmap.rect())
        border_pen = QPen(QColor(100, 100, 100), 1.0)
        border_pen.setCosmetic(True)  # Constant width regardless of zoom
        self._boundary_item = QGraphicsRectItem(img_rect)
        self._boundary_item.setPen(border_pen)
        self._boundary_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._boundary_item.setZValue(Z_BOUNDARY)  # Above annotations, below nothing
        self._boundary_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._boundary_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, False)
        self._boundary_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._boundary_item)

        # Expand scene rect to allow placing items outside the image
        margin = max(pixmap.width(), pixmap.height()) * 0.5
        expanded = img_rect.adjusted(-margin, -margin, margin, margin)
        self._scene.setSceneRect(expanded)

        self.resetTransform()
        self._zoom_level = 1.0
        self.centerOn(img_rect.center())
        logger.info("Image loaded: %dx%d", pixmap.width(), pixmap.height())

    def set_tool(self, tool: BaseTool | None) -> None:
        """Set the active drawing tool."""
        if self._current_tool:
            self._current_tool.deactivate()
        self._current_tool = tool
        if tool:
            tool.activate(self._scene, self)

    def add_item_undoable(self, item, description: str = "Add item") -> None:
        """Register a scene item with the undo stack (item already in scene)."""
        from verdiclip.editor.history import AddItemCommand
        if self._history:
            cmd = AddItemCommand(self._scene, item, description)
            cmd._already_added = True
            self._history.push(cmd)
        # Item is already in the scene from the tool's mouse_press

    def add_move_undoable(
        self, item, old_pos: tuple[float, float], new_pos: tuple[float, float],
    ) -> None:
        """Record an item move on the undo stack."""
        from verdiclip.editor.history import MoveItemCommand
        if self._history:
            cmd = MoveItemCommand(item, old_pos, new_pos)
            self._history.push(cmd)

    def set_history(self, history: EditorHistory) -> None:
        """Set the history instance for undo support."""
        self._history = history

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom with Ctrl+scroll, scroll horizontally with Shift+scroll."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = _ZOOM_FACTOR if event.angleDelta().y() > 0 else 1.0 / _ZOOM_FACTOR
            self._apply_zoom(factor)
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+scroll: horizontal scrolling at normal speed
            delta = event.angleDelta().y()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta
            )
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press — pan with middle button, delegate to tool otherwise."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        elif self._current_tool:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._current_tool.mouse_press(scene_pos, event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move — pan or delegate to tool."""
        if self._is_panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x())
            )
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y())
            )
            event.accept()
        elif self._current_tool:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._current_tool.mouse_move(scene_pos, event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release — stop pan or delegate to tool."""
        if event.button() == Qt.MouseButton.MiddleButton and self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        elif self._current_tool:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._current_tool.mouse_release(scene_pos, event)
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle key presses — Delete removes items, Enter confirms crop, Esc deselects."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_items()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._current_tool and hasattr(self._current_tool, "apply_crop"):
                self._current_tool.apply_crop()
        elif event.key() == Qt.Key.Key_Escape:
            if self._current_tool and hasattr(self._current_tool, "cancel_crop"):
                self._current_tool.cancel_crop()
            elif self._scene.selectedItems():
                for item in self._scene.selectedItems():
                    item.setSelected(False)
            else:
                self.switch_to_select_requested.emit()
        else:
            super().keyPressEvent(event)

    @property
    def current_tool(self) -> BaseTool | None:
        """Return the currently active tool."""
        return self._current_tool

    def delete_selected(self) -> None:
        """Delete all selected items (excluding background and boundary)."""
        self._delete_selected_items()

    def _delete_selected_items(self) -> None:
        """Remove all currently selected annotation items from the scene."""
        from verdiclip.editor.history import RemoveItemCommand
        selected = self._scene.selectedItems()
        removed = 0
        for item in selected:
            if item is self._pixmap_item or item is self._boundary_item:
                continue
            if self._history:
                cmd = RemoveItemCommand(self._scene, item, "Delete item")
                self._history.push(cmd)
            else:
                self._scene.removeItem(item)
            removed += 1
        if removed:
            logger.info("Deleted %d annotation item(s)", removed)

    def zoom_in(self) -> None:
        """Zoom in by one step (anchored to viewport center for menu/keyboard)."""
        self._apply_zoom_at_center(_ZOOM_FACTOR)

    def zoom_out(self) -> None:
        """Zoom out by one step (anchored to viewport center for menu/keyboard)."""
        self._apply_zoom_at_center(1.0 / _ZOOM_FACTOR)

    def zoom_reset(self) -> None:
        """Reset to 100% zoom."""
        factor = 1.0 / self._zoom_level
        self.scale(factor, factor)
        self._zoom_level = 1.0
        self.zoom_changed.emit(self._zoom_level)

    def zoom_fit(self) -> None:
        """Fit the image in the viewport."""
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = self.transform().m11()
        self.zoom_changed.emit(self._zoom_level)

    def _apply_zoom(self, factor: float) -> None:
        new_zoom = self._zoom_level * factor
        if _MIN_ZOOM <= new_zoom <= _MAX_ZOOM:
            self.scale(factor, factor)
            self._zoom_level = new_zoom
            self.zoom_changed.emit(self._zoom_level)

    def _apply_zoom_at_center(self, factor: float) -> None:
        """Zoom anchored to the viewport center (for keyboard/menu-triggered zoom)."""
        new_zoom = self._zoom_level * factor
        if _MIN_ZOOM <= new_zoom <= _MAX_ZOOM:
            # Temporarily change anchor to viewport center for non-mouse zoom
            old_anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(factor, factor)
            self.setTransformationAnchor(old_anchor)
            self._zoom_level = new_zoom
            self.zoom_changed.emit(self._zoom_level)

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    @property
    def scene(self) -> QGraphicsScene:
        return self._scene

    @property
    def pixmap_item(self) -> QGraphicsPixmapItem | None:
        return self._pixmap_item

    def get_flattened_pixmap(self) -> QPixmap:
        """Render only the image area (with annotations) to a QPixmap."""
        if self._pixmap_item:
            rect = QRectF(self._pixmap_item.pixmap().rect())
        else:
            rect = self._scene.sceneRect()

        # Temporarily hide the boundary border so it doesn't render into the export
        boundary_was_visible = False
        if self._boundary_item:
            boundary_was_visible = self._boundary_item.isVisible()
            self._boundary_item.setVisible(False)

        pixmap = QPixmap(int(rect.width()), int(rect.height()))
        pixmap.fill(Qt.GlobalColor.transparent)
        from PySide6.QtGui import QPainter
        painter = QPainter(pixmap)
        self._scene.render(painter, QRectF(pixmap.rect()), rect)
        painter.end()

        if self._boundary_item:
            self._boundary_item.setVisible(boundary_was_visible)
        return pixmap


class EditorWindow(QMainWindow):
    """Main editor window with canvas, toolbar, and properties."""

    def __init__(self, pixmap: QPixmap, config: Config, file_path: str = "") -> None:
        super().__init__()
        self._config = config
        self._history = EditorHistory()
        self._tools: dict[ToolType, BaseTool] = {}
        self._file_path = file_path
        self._image_size = (pixmap.width(), pixmap.height())

        title = f"{__app_name__} — Editor"
        if file_path:
            import os
            title = f"{os.path.basename(file_path)} — {__app_name__} Editor"
        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)
        self.resize(1200, 800)

        self._setup_canvas(pixmap)
        self._setup_toolbar()
        self._setup_properties()
        self._setup_menus()
        self._setup_statusbar()
        self._register_tools()

        # Set initial tool
        self._on_tool_changed(ToolType.SELECT)

    def _setup_canvas(self, pixmap: QPixmap) -> None:
        self._canvas = EditorCanvas()
        self._canvas.set_history(self._history)
        self.setCentralWidget(self._canvas)
        self._canvas.set_image(pixmap)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.switch_to_select_requested.connect(self._switch_to_select)
        self._canvas.scene.selectionChanged.connect(self._on_selection_changed)

    def _setup_toolbar(self) -> None:
        self._toolbar = EditorToolbar()
        self._toolbar.tool_changed.connect(self._on_tool_changed)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self._toolbar)

    def _setup_properties(self) -> None:
        self._properties = PropertiesPanel()
        props_toolbar = self.addToolBar("Properties")
        props_toolbar.setMovable(False)
        props_toolbar.addWidget(self._properties)

    def _setup_menus(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._save_file_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        copy_action = QAction("&Copy to Clipboard", self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self._copy_to_clipboard)
        file_menu.addAction(copy_action)

        print_action = QAction("&Print...", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self._print)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        close_action = QAction("C&lose", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._history.undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._history.redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        delete_action = QAction("&Delete Selected", self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._canvas.delete_selected)
        edit_menu.addAction(delete_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut(QKeySequence("Ctrl+="))
        zoom_in_action.triggered.connect(self._zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_action.triggered.connect(self._zoom_out)
        view_menu.addAction(zoom_out_action)

        zoom_100_action = QAction("Zoom &100%", self)
        zoom_100_action.setShortcut(QKeySequence("Ctrl+0"))
        zoom_100_action.triggered.connect(self._zoom_100)
        view_menu.addAction(zoom_100_action)

        zoom_fit_action = QAction("Zoom to &Fit", self)
        zoom_fit_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        zoom_fit_action.triggered.connect(self._zoom_fit)
        view_menu.addAction(zoom_fit_action)

    def _setup_statusbar(self) -> None:
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        # File name label (left side via showMessage fallback)
        self._file_label = QLabel("")
        self._statusbar.addWidget(self._file_label)
        if self._file_path:
            import os
            self._file_label.setText(os.path.basename(self._file_path))

        # Permanent labels for image info (right side)
        w, h = self._image_size
        self._dim_label = QLabel(f"{w} × {h} px")
        self._statusbar.addPermanentWidget(self._dim_label)

        # Zoom control: clickable label that toggles a slider popup
        self._zoom_button = QPushButton("100%")
        self._zoom_button.setFlat(True)
        self._zoom_button.setToolTip("Click to adjust zoom level")
        self._zoom_button.setFixedWidth(60)
        self._zoom_button.clicked.connect(self._toggle_zoom_slider)
        self._statusbar.addPermanentWidget(self._zoom_button)

        # Zoom slider popup (hidden by default)
        self._zoom_slider_widget = QWidget(self)
        slider_layout = QVBoxLayout(self._zoom_slider_widget)
        slider_layout.setContentsMargins(4, 4, 4, 4)

        slider_row = QHBoxLayout()
        self._zoom_slider_label_min = QLabel("10%")
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(10, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._zoom_slider.setTickInterval(50)
        self._zoom_slider_label_max = QLabel("400%")
        slider_row.addWidget(self._zoom_slider_label_min)
        slider_row.addWidget(self._zoom_slider)
        slider_row.addWidget(self._zoom_slider_label_max)
        slider_layout.addLayout(slider_row)

        self._zoom_slider_widget.setFixedWidth(300)
        self._zoom_slider_widget.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)

        self._statusbar.showMessage("Ready")

    def _register_tools(self) -> None:
        """Create and register all tool instances."""
        from verdiclip.editor.tools.arrow import ArrowTool
        from verdiclip.editor.tools.crop import CropTool
        from verdiclip.editor.tools.ellipse import EllipseTool
        from verdiclip.editor.tools.freehand import FreehandTool
        from verdiclip.editor.tools.highlight import HighlightTool
        from verdiclip.editor.tools.line import LineTool
        from verdiclip.editor.tools.number import NumberTool
        from verdiclip.editor.tools.obfuscate import ObfuscateTool
        from verdiclip.editor.tools.rectangle import RectangleTool
        from verdiclip.editor.tools.select import SelectTool
        from verdiclip.editor.tools.text import TextTool

        self._tools = {
            ToolType.SELECT: SelectTool(),
            ToolType.CROP: CropTool(),
            ToolType.RECTANGLE: RectangleTool(),
            ToolType.ELLIPSE: EllipseTool(),
            ToolType.LINE: LineTool(),
            ToolType.ARROW: ArrowTool(),
            ToolType.TEXT: TextTool(),
            ToolType.NUMBER: NumberTool(),
            ToolType.HIGHLIGHT: HighlightTool(),
            ToolType.OBFUSCATE: ObfuscateTool(),
            ToolType.FREEHAND: FreehandTool(),
        }

        # Wire properties panel signals to active tools
        self._properties.stroke_color_changed.connect(self._update_tool_stroke_color)
        self._properties.fill_color_changed.connect(self._update_tool_fill_color)
        self._properties.stroke_width_changed.connect(self._update_tool_stroke_width)
        self._properties.font_changed.connect(self._update_tool_font)

    def _update_tool_stroke_color(self, color: QColor) -> None:
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_stroke_color"):
            tool.set_stroke_color(color)

    def _update_tool_fill_color(self, color: QColor) -> None:
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_fill_color"):
            tool.set_fill_color(color)

    def _update_tool_stroke_width(self, width: int) -> None:
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_stroke_width"):
            tool.set_stroke_width(width)

    def _update_tool_font(self, font) -> None:
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_font"):
            tool.set_font(font)

    def _zoom_in(self) -> None:
        self._canvas.zoom_in()

    def _zoom_out(self) -> None:
        self._canvas.zoom_out()

    def _zoom_100(self) -> None:
        self._canvas.zoom_reset()

    def _zoom_fit(self) -> None:
        self._canvas.zoom_fit()

    def _on_zoom_changed(self, zoom_level: float) -> None:
        """Update the zoom display whenever the canvas zoom changes."""
        pct = int(zoom_level * 100)
        self._zoom_button.setText(f"{pct}%")
        # Sync the slider without triggering a recursive zoom change
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(max(10, min(400, pct)))
        self._zoom_slider.blockSignals(False)

    def _toggle_zoom_slider(self) -> None:
        """Show or hide the zoom slider popup above the zoom button."""
        if self._zoom_slider_widget.isVisible():
            self._zoom_slider_widget.hide()
        else:
            btn_pos = self._zoom_button.mapToGlobal(self._zoom_button.rect().topLeft())
            half_popup = self._zoom_slider_widget.width() // 2
            half_btn = self._zoom_button.width() // 2
            popup_x = btn_pos.x() - half_popup + half_btn
            popup_y = btn_pos.y() - self._zoom_slider_widget.sizeHint().height()
            from PySide6.QtCore import QPoint
            self._zoom_slider_widget.move(QPoint(popup_x, popup_y))
            self._zoom_slider_widget.show()

    def _on_zoom_slider_changed(self, value: int) -> None:
        """Apply zoom level from the slider."""
        target_zoom = value / 100.0
        current_zoom = self._canvas.zoom_level
        if abs(current_zoom - target_zoom) > 0.001:
            factor = target_zoom / current_zoom
            self._canvas._apply_zoom_at_center(factor)

    def _update_zoom_label(self) -> None:
        pct = int(self._canvas.zoom_level * 100)
        self._zoom_button.setText(f"{pct}%")

    def _on_tool_changed(self, tool_type: ToolType) -> None:
        """Handle tool selection from toolbar."""
        tool = self._tools.get(tool_type)
        self._canvas.set_tool(tool)
        self._statusbar.showMessage(f"Tool: {tool_type.name.title()}")
        logger.debug("Switched to tool: %s", tool_type.name)

    def _switch_to_select(self) -> None:
        """Switch to the Select tool (triggered by Esc when nothing is selected)."""
        self._toolbar.set_tool(ToolType.SELECT)

    def _on_selection_changed(self) -> None:
        """Show an inline editor when a NumberMarkerItem is selected."""
        from verdiclip.editor.tools.number import NumberMarkerItem, NumberTool

        selected = self._canvas.scene.selectedItems()
        if len(selected) == 1 and isinstance(selected[0], NumberMarkerItem):
            number_tool = self._tools.get(ToolType.NUMBER)
            if isinstance(number_tool, NumberTool):
                number_tool.show_editor_for(selected[0])

    def _open_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp);;All Files (*)",
        )
        if file_path:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._canvas.set_image(pixmap)
                self._history.clear()
                self._update_title(file_path)

    def _save_file(self) -> None:
        from verdiclip.export.file_export import FileExporter
        path = FileExporter.save_with_dialog(
            self._canvas.get_flattened_pixmap(), self._config, self
        )
        if path:
            self._file_path = path
            self._update_title(path)

    def _save_file_as(self) -> None:
        from verdiclip.export.file_export import FileExporter
        path = FileExporter.save_as(self._canvas.get_flattened_pixmap(), self)
        if path:
            self._file_path = path
            self._update_title(path)

    def _update_title(self, file_path: str) -> None:
        """Update the window title and file label to reflect the save location."""
        import os
        basename = os.path.basename(file_path)
        self.setWindowTitle(f"{basename} — {__app_name__} Editor")
        self._file_label.setText(file_path)

    def _copy_to_clipboard(self) -> None:
        from verdiclip.export.clipboard import ClipboardExporter
        ClipboardExporter.copy(self._canvas.get_flattened_pixmap())
        self._statusbar.showMessage("Copied to clipboard", 3000)

    def _print(self) -> None:
        from verdiclip.export.printer import PrinterExporter
        PrinterExporter.print_pixmap(self._canvas.get_flattened_pixmap(), self)
