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
    number_editor_requested = Signal(object)  # Emitted with NumberMarkerItem on double-click

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        from PySide6.QtGui import QPainter
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
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
        # Clear undo history first — commands hold references to scene items
        if self._history:
            self._history.clear()
        self._scene.clear()
        self._setup_background(pixmap)

        self.resetTransform()
        self._zoom_level = 1.0
        self.centerOn(QRectF(pixmap.rect()).center())
        logger.info("Image loaded: %dx%d", pixmap.width(), pixmap.height())

    def _setup_background(self, pixmap: QPixmap) -> None:
        """Set up the background pixmap and boundary rect."""
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

    def crop_undoable(
        self, new_pixmap: QPixmap, removed_items: list,
        item_positions: list[tuple], crop_offset: tuple[float, float],
    ) -> None:
        """Replace the background with a cropped image while keeping undo history."""
        if not self._history or not self._pixmap_item:
            self.set_image(new_pixmap)
            return

        old_pixmap = self._pixmap_item.pixmap()

        # Remove items from scene (they're outside the crop)
        for item in removed_items:
            self._scene.removeItem(item)

        # Replace background and boundary
        self._scene.removeItem(self._pixmap_item)
        if self._boundary_item:
            self._scene.removeItem(self._boundary_item)
        self._setup_background(new_pixmap)

        from verdiclip.editor.history import CropCommand
        cmd = CropCommand(
            self, old_pixmap, new_pixmap, removed_items,
            item_positions, crop_offset,
        )
        self._history.push(cmd)
        logger.info("Crop applied (undoable): %dx%d", new_pixmap.width(), new_pixmap.height())

    def _replace_image(self, pixmap: QPixmap, items: list, *, remove: bool) -> None:
        """Replace the background image during undo/redo of a crop.

        Args:
            pixmap: The new background pixmap.
            items: Items that were removed by the crop.
            remove: If True, remove *items* from scene (redo). If False, restore them (undo).
        """
        # Remove old background and boundary
        if self._pixmap_item and self._pixmap_item.scene():
            self._scene.removeItem(self._pixmap_item)
        if self._boundary_item and self._boundary_item.scene():
            self._scene.removeItem(self._boundary_item)

        self._setup_background(pixmap)

        for item in items:
            if remove:
                if item.scene():
                    self._scene.removeItem(item)
            else:
                if not item.scene():
                    self._scene.addItem(item)

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

    def add_moves_undoable(self, moves: list[tuple]) -> None:
        """Record a simultaneous multi-item move as a single undo command."""
        from verdiclip.editor.history import MultipleMoveCommand
        if self._history and moves:
            self._history.push(MultipleMoveCommand(moves))

    def add_resize_undoable(self, item, old_geometry: dict, new_geometry: dict) -> None:
        """Record an item resize on the undo stack."""
        from verdiclip.editor.history import ResizeItemCommand
        if self._history:
            self._history.push(ResizeItemCommand(item, old_geometry, new_geometry))

    def set_history(self, history: EditorHistory) -> None:
        """Set the history instance for undo support."""
        self._history = history

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom with Ctrl+scroll, scroll horizontally with Shift+scroll."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = _ZOOM_FACTOR if event.angleDelta().y() > 0 else 1.0 / _ZOOM_FACTOR
            # Zoom to the point under the cursor
            view_pos = event.position()
            self._zoom_to_point(factor, view_pos.toPoint())
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Handle double-click to open inline editors (e.g., number markers)."""
        if self._current_tool:
            from verdiclip.editor.tools.select import SelectTool
            if isinstance(self._current_tool, SelectTool):
                item = self._current_tool._find_annotation_at(
                    self.mapToScene(event.position().toPoint()),
                )
                if item:
                    from verdiclip.editor.tools.number import NumberMarkerItem
                    if isinstance(item, NumberMarkerItem):
                        self.number_editor_requested.emit(item)
                        event.accept()
                        return
        super().mouseDoubleClickEvent(event)

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
        # If a text item is being edited, let the scene/item handle the key event first.
        from PySide6.QtWidgets import QGraphicsTextItem  # noqa: PLC0415
        focus = self._scene.focusItem()
        if (
            isinstance(focus, QGraphicsTextItem)
            and focus.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
        ):
            super().keyPressEvent(event)
            return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_items()
            self._delete_selected_items()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._current_tool and hasattr(self._current_tool, "apply_crop"):
                self._current_tool.apply_crop()
        elif event.key() == Qt.Key.Key_Escape:
            from verdiclip.editor.tools.select import SelectTool  # noqa: PLC0415

            is_select = isinstance(self._current_tool, SelectTool)

            # Cancel active crop UI if present
            if (
                self._current_tool
                and hasattr(self._current_tool, "cancel_crop")
                and hasattr(self._current_tool, "_crop_rect_item")
                and self._current_tool._crop_rect_item is not None
            ):
                self._current_tool.cancel_crop()
            # Select tool: deselect any selected items
            elif is_select and self._scene.selectedItems():
                for item in self._scene.selectedItems():
                    item.setSelected(False)
            # Non-Select tool (or no tool): deselect and switch to Select
            elif not is_select:
                for item in self._scene.selectedItems():
                    item.setSelected(False)
                self.switch_to_select_requested.emit()
        elif (
            event.key() == Qt.Key.Key_A
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            from verdiclip.editor.tools.select import SelectTool
            if isinstance(self._current_tool, SelectTool):
                self._current_tool.select_all()
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
        center = self.viewport().rect().center()
        self._zoom_to_point(_ZOOM_FACTOR, center)

    def zoom_out(self) -> None:
        """Zoom out by one step (anchored to viewport center for menu/keyboard)."""
        center = self.viewport().rect().center()
        self._zoom_to_point(1.0 / _ZOOM_FACTOR, center)

    def zoom_reset(self) -> None:
        """Reset to 100% zoom, centered on the image."""
        self.resetTransform()
        self._zoom_level = 1.0
        if self._pixmap_item:
            self.centerOn(self._pixmap_item)
        self.zoom_changed.emit(self._zoom_level)

    def zoom_fit(self) -> None:
        """Fit the image in the viewport."""
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = self.transform().m11()
        self.zoom_changed.emit(self._zoom_level)

    def _zoom_to_point(self, factor: float, view_pos) -> None:
        """Zoom anchored to a specific viewport pixel position."""
        new_zoom = self._zoom_level * factor
        if not (_MIN_ZOOM <= new_zoom <= _MAX_ZOOM):
            return
        # Map the anchor point to scene coords before scaling
        scene_pos = self.mapToScene(view_pos)
        self.scale(factor, factor)
        self._zoom_level = new_zoom
        # Map the same scene point to new viewport coords and adjust scroll
        new_view_pos = self.mapFromScene(scene_pos)
        delta = new_view_pos - view_pos
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + int(delta.x())
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + int(delta.y())
        )
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
        # Guard flag: True while the properties panel is being updated from a
        # selected item's state, preventing feedback loops with tool setters.
        self._updating_from_selection = False

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
        self._canvas.number_editor_requested.connect(self._on_number_editor_requested)
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

        copy_action = QAction("Copy Image to &Clipboard", self)
        copy_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
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

        copy_el_action = QAction("&Copy", self)
        copy_el_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_el_action.triggered.connect(self._copy_elements)
        edit_menu.addAction(copy_el_action)

        paste_el_action = QAction("&Paste", self)
        paste_el_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_el_action.triggered.connect(self._paste_elements)
        edit_menu.addAction(paste_el_action)

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

        # Zoom-to-fit button (icon)
        self._zoom_fit_button = QPushButton("⊞")
        self._zoom_fit_button.setFlat(True)
        self._zoom_fit_button.setToolTip("Zoom to fit image in viewport")
        self._zoom_fit_button.setFixedWidth(28)
        self._zoom_fit_button.clicked.connect(self._zoom_fit)
        self._statusbar.addPermanentWidget(self._zoom_fit_button)

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
        if self._updating_from_selection:
            return
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_stroke_color"):
            tool.set_stroke_color(color)
        # Apply to currently selected items
        for item in self._canvas.scene.selectedItems():
            _apply_stroke_to_item(item, color)

    def _update_tool_fill_color(self, color: QColor) -> None:
        if self._updating_from_selection:
            return
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_fill_color"):
            tool.set_fill_color(color)
        for item in self._canvas.scene.selectedItems():
            _apply_fill_to_item(item, color)

    def _update_tool_stroke_width(self, width: int) -> None:
        if self._updating_from_selection:
            return
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_stroke_width"):
            tool.set_stroke_width(width)
        for item in self._canvas.scene.selectedItems():
            _apply_width_to_item(item, width)

    def _update_tool_font(self, font) -> None:
        if self._updating_from_selection:
            return
        tool = self._canvas.current_tool
        if tool and hasattr(tool, "set_font"):
            tool.set_font(font)
        from PySide6.QtWidgets import QGraphicsTextItem  # noqa: PLC0415
        for item in self._canvas.scene.selectedItems():
            if isinstance(item, QGraphicsTextItem):
                item.setFont(font)

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
        """Apply zoom level from the slider, snapping to 10% intervals."""
        snap_interval = 10
        snapped = round(value / snap_interval) * snap_interval
        snapped = max(10, min(400, snapped))
        if self._zoom_slider.value() != snapped:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(snapped)
            self._zoom_slider.blockSignals(False)
        target_zoom = snapped / 100.0
        current_zoom = self._canvas.zoom_level
        if abs(current_zoom - target_zoom) > 0.001:
            factor = target_zoom / current_zoom
            center = self._canvas.viewport().rect().center()
            self._canvas._zoom_to_point(factor, center)

    def _update_zoom_label(self) -> None:
        pct = int(self._canvas.zoom_level * 100)
        self._zoom_button.setText(f"{pct}%")

    def _on_tool_changed(self, tool_type: ToolType) -> None:
        """Handle tool selection from toolbar."""
        from verdiclip.editor.tools.select import SelectTool  # noqa: PLC0415

        # Clear handles when leaving the Select tool
        prev_tool = self._canvas.current_tool
        if isinstance(prev_tool, SelectTool):
            prev_tool.clear_handles()

        tool = self._tools.get(tool_type)

        # Sync current panel values to the incoming tool
        if tool is not None:
            if hasattr(tool, "set_stroke_color"):
                tool.set_stroke_color(self._properties.stroke_color)
            if hasattr(tool, "set_fill_color"):
                tool.set_fill_color(self._properties.fill_color)
            if hasattr(tool, "set_stroke_width"):
                tool.set_stroke_width(self._properties.stroke_width)
            if hasattr(tool, "set_font"):
                tool.set_font(self._properties.current_font)

        self._canvas.set_tool(tool)
        self._update_properties_visibility(tool_type)
        self._statusbar.showMessage(f"Tool: {tool_type.name.title()}")
        logger.debug("Switched to tool: %s", tool_type.name)

    def _switch_to_select(self) -> None:
        """Switch to the Select tool (triggered by Esc when nothing is selected)."""
        self._toolbar.set_tool(ToolType.SELECT)

    def _on_selection_changed(self) -> None:
        """Sync the properties panel with the selected item and update handles."""
        from verdiclip.editor.tools.number import NumberMarkerItem, NumberTool  # noqa: PLC0415
        from verdiclip.editor.tools.select import SelectTool  # noqa: PLC0415

        selected = self._canvas.scene.selectedItems()

        # Dismiss the number editor when a non-counter item is selected
        number_tool = self._tools.get(ToolType.NUMBER)
        if (
            isinstance(number_tool, NumberTool)
            and not (len(selected) == 1 and isinstance(selected[0], NumberMarkerItem))
        ):
            number_tool._dismiss_editor()

        # Update resize handles (only for Select tool)
        select_tool = self._tools.get(ToolType.SELECT)
        if isinstance(select_tool, SelectTool):
            if self._canvas.current_tool is select_tool:
                select_tool.update_selection_handles(selected)
            else:
                select_tool.clear_handles()

        if selected:
            # Read back properties from a single selected item
            self._sync_properties_from_selection(selected)
        else:
            # No selection → restore default toolbar for the current tool
            self._update_properties_visibility(self._toolbar.current_tool)

    def _on_number_editor_requested(self, marker) -> None:
        """Open inline editor for a NumberMarkerItem on double-click."""
        from verdiclip.editor.tools.number import NumberMarkerItem, NumberTool  # noqa: PLC0415

        if isinstance(marker, NumberMarkerItem):
            number_tool = self._tools.get(ToolType.NUMBER)
            if isinstance(number_tool, NumberTool):
                number_tool.show_editor_for(marker)

    def _sync_properties_from_selection(self, selected: list) -> None:
        """Read properties from the single selected item into the properties panel.

        Blocked by ``_updating_from_selection`` to prevent recursive signal loops.
        """
        if len(selected) != 1:
            return

        item = selected[0]
        self._updating_from_selection = True
        try:
            from PySide6.QtWidgets import (  # noqa: PLC0415
                QGraphicsEllipseItem,
                QGraphicsLineItem,
                QGraphicsRectItem,
                QGraphicsTextItem,
            )

            try:
                from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
                _is_obfuscation = isinstance(item, ObfuscationItem)
            except ImportError:
                _is_obfuscation = False

            # ArrowItem: read shaft colour and width
            try:
                from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
                if isinstance(item, ArrowItem):
                    pen = item._shaft.pen()
                    self._properties.set_stroke_color(pen.color())
                    self._properties.set_stroke_width(max(1, int(pen.widthF())))
                    self._update_properties_visibility_for_item(item)
                    return
            except ImportError:
                pass

            if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
                pen = item.pen()
                self._properties.set_stroke_color(pen.color())
                self._properties.set_stroke_width(max(1, int(pen.widthF())))
                brush = item.brush()
                from PySide6.QtCore import Qt as _Qt  # noqa: PLC0415
                if brush.style() == _Qt.BrushStyle.NoBrush or brush.color().alpha() == 0:
                    self._properties.set_fill_color(QColor(0, 0, 0, 0))
                else:
                    self._properties.set_fill_color(brush.color())
                self._update_properties_visibility_for_item(item)

            elif isinstance(item, QGraphicsLineItem):
                pen = item.pen()
                self._properties.set_stroke_color(pen.color())
                self._properties.set_stroke_width(max(1, int(pen.widthF())))
                self._update_properties_visibility_for_item(item)

            elif isinstance(item, QGraphicsTextItem):
                self._properties.set_stroke_color(item.defaultTextColor())
                self._properties.set_font(item.font())
                self._update_properties_visibility_for_item(item)

            elif _is_obfuscation:
                # Obfuscation has no configurable stroke/fill
                self._properties.set_visible_properties(stroke=False, fill=False, width=False)

        finally:
            self._updating_from_selection = False

    def _update_properties_visibility(self, tool_type: ToolType) -> None:
        """Show or hide properties based on the active tool type.

        The SELECT tool shows stroke, fill, and width so users can configure
        defaults for new elements even when nothing is selected.
        """
        if tool_type == ToolType.SELECT:
            self._properties.set_visible_properties(
                stroke=True, fill=True, width=True, font=False, caps=False,
            )
            return
        show_stroke = tool_type not in (ToolType.CROP, ToolType.OBFUSCATE)
        show_fill = tool_type in (ToolType.RECTANGLE, ToolType.ELLIPSE, ToolType.HIGHLIGHT)
        show_width = tool_type not in (
            ToolType.CROP, ToolType.OBFUSCATE,
            ToolType.TEXT, ToolType.NUMBER,
        )
        show_font = tool_type in (ToolType.TEXT, ToolType.NUMBER)
        show_caps = tool_type in (ToolType.LINE, ToolType.ARROW)
        self._properties.set_visible_properties(
            stroke=show_stroke, fill=show_fill, width=show_width,
            font=show_font, caps=show_caps,
        )

    def _update_properties_visibility_for_item(self, item: object) -> None:
        """Show or hide properties based on the type of the selected item."""
        from PySide6.QtWidgets import (  # noqa: PLC0415
            QGraphicsEllipseItem,
            QGraphicsLineItem,
            QGraphicsRectItem,
            QGraphicsTextItem,
        )

        # ArrowItem: stroke + width + caps
        try:
            from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
            if isinstance(item, ArrowItem):
                self._properties.set_visible_properties(
                    stroke=True, fill=False, width=True, font=False, caps=True,
                )
                return
        except ImportError:
            pass

        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            self._properties.set_visible_properties(
                stroke=True, fill=True, width=True, font=False, caps=False,
            )
        elif isinstance(item, QGraphicsLineItem):
            self._properties.set_visible_properties(
                stroke=True, fill=False, width=True, font=False, caps=True,
            )
        elif isinstance(item, QGraphicsTextItem):
            self._properties.set_visible_properties(
                stroke=True, fill=False, width=False, font=True, caps=False,
            )

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

    # ------------------------------------------------------------------
    # Element copy / paste
    # ------------------------------------------------------------------

    def _copy_elements(self) -> None:
        """Duplicate selected annotation elements into an internal clipboard."""
        selected = self._canvas.scene.selectedItems()
        if not selected:
            self._statusbar.showMessage("Nothing selected to copy", 2000)
            return
        self._element_clipboard = _serialise_items(selected)
        n = len(self._element_clipboard)
        self._statusbar.showMessage(f"Copied {n} element{'s' if n != 1 else ''}", 2000)

    def _paste_elements(self) -> None:
        """Paste previously copied elements with a small offset."""
        if not hasattr(self, "_element_clipboard") or not self._element_clipboard:
            self._statusbar.showMessage("Nothing to paste", 2000)
            return

        pasted = _deserialise_items(self._element_clipboard)
        if not pasted:
            return

        # Offset so the paste isn't exactly on top of the original
        from PySide6.QtCore import QPointF  # noqa: PLC0415
        offset = QPointF(15, 15)
        for item in pasted:
            item.setPos(item.pos() + offset)
            self._canvas.scene.addItem(item)
            if hasattr(self._canvas, "add_item_undoable"):
                self._canvas.add_item_undoable(item, "Paste element")
            item.setSelected(True)

        n = len(pasted)
        self._statusbar.showMessage(f"Pasted {n} element{'s' if n != 1 else ''}", 2000)

    def _print(self) -> None:
        from verdiclip.export.printer import PrinterExporter
        PrinterExporter.print_pixmap(self._canvas.get_flattened_pixmap(), self)


# ---------------------------------------------------------------------------
# Element serialisation helpers (for copy-paste)
# ---------------------------------------------------------------------------

def _serialise_items(items: list) -> list[dict]:
    """Convert scene items into serialisable dicts for the internal clipboard."""
    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    result: list[dict] = []
    for item in items:
        data: dict = {"pos_x": item.pos().x(), "pos_y": item.pos().y()}

        try:
            from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
            if isinstance(item, ArrowItem):
                data["type"] = "arrow"
                data["p1_x"] = item.shaft_line.p1().x()
                data["p1_y"] = item.shaft_line.p1().y()
                data["p2_x"] = item.shaft_line.p2().x()
                data["p2_y"] = item.shaft_line.p2().y()
                data["color"] = item._stroke_color.name()
                data["width"] = item._stroke_width
                result.append(data)
                continue
        except ImportError:
            pass

        try:
            from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
            if isinstance(item, ObfuscationItem):
                data["type"] = "obfuscation"
                data["w"] = item._size.width()
                data["h"] = item._size.height()
                result.append(data)
                continue
        except ImportError:
            pass

        try:
            from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
            if isinstance(item, NumberMarkerItem):
                data["type"] = "number"
                data["value"] = item.value
                data["bg_color"] = item._bg_color.name()
                data["text_color"] = item._text_color.name()
                r = item.rect()
                data["radius"] = r.width() / 2.0
                result.append(data)
                continue
        except ImportError:
            pass

        if isinstance(item, QGraphicsRectItem):
            data["type"] = "rect"
            r = item.rect()
            data["x"] = r.x()
            data["y"] = r.y()
            data["w"] = r.width()
            data["h"] = r.height()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            data["brush_color"] = item.brush().color().name(QColor.NameFormat.HexArgb)
            result.append(data)
        elif isinstance(item, QGraphicsEllipseItem):
            data["type"] = "ellipse"
            r = item.rect()
            data["x"] = r.x()
            data["y"] = r.y()
            data["w"] = r.width()
            data["h"] = r.height()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            data["brush_color"] = item.brush().color().name(QColor.NameFormat.HexArgb)
            result.append(data)
        elif isinstance(item, QGraphicsLineItem):
            data["type"] = "line"
            ln = item.line()
            data["x1"] = ln.p1().x()
            data["y1"] = ln.p1().y()
            data["x2"] = ln.p2().x()
            data["y2"] = ln.p2().y()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            result.append(data)
        elif isinstance(item, QGraphicsTextItem):
            data["type"] = "text"
            data["html"] = item.toHtml()
            data["color"] = item.defaultTextColor().name()
            f = item.font()
            data["font_family"] = f.family()
            data["font_size"] = f.pointSize()
            result.append(data)
    return result


def _deserialise_items(data_list: list[dict]) -> list:
    """Reconstruct scene items from serialised dicts."""
    from PySide6.QtCore import QLineF, QPointF, QRectF  # noqa: PLC0415
    from PySide6.QtGui import QBrush, QFont, QPen  # noqa: PLC0415
    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    items: list = []
    for d in data_list:
        t = d.get("type")
        pos = QPointF(d.get("pos_x", 0), d.get("pos_y", 0))

        if t == "arrow":
            from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
            item = ArrowItem(
                QPointF(d["p1_x"], d["p1_y"]),
                QPointF(d["p2_x"], d["p2_y"]),
                QColor(d["color"]),
                d["width"],
            )
            item.setPos(pos)
            items.append(item)

        elif t == "obfuscation":
            from PySide6.QtCore import QSizeF  # noqa: PLC0415

            from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
            item = ObfuscationItem()
            item.set_geometry(pos, QSizeF(d["w"], d["h"]))
            items.append(item)

        elif t == "number":
            from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
            item = NumberMarkerItem(d["value"], QColor(d["bg_color"]), QColor(d["text_color"]))
            r = d.get("radius", 16)
            item.setRect(QRectF(-r, -r, 2 * r, 2 * r))
            item._center_text()
            item.setPos(pos)
            items.append(item)

        elif t == "rect":
            item = QGraphicsRectItem(QRectF(d["x"], d["y"], d["w"], d["h"]))
            pen = QPen(QColor(d["pen_color"]), d["pen_width"])
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(d["brush_color"])))
            item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "ellipse":
            item = QGraphicsEllipseItem(QRectF(d["x"], d["y"], d["w"], d["h"]))
            item.setPen(QPen(QColor(d["pen_color"]), d["pen_width"]))
            item.setBrush(QBrush(QColor(d["brush_color"])))
            item.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "line":
            item = QGraphicsLineItem(QLineF(
                QPointF(d["x1"], d["y1"]), QPointF(d["x2"], d["y2"]),
            ))
            item.setPen(QPen(QColor(d["pen_color"]), d["pen_width"]))
            item.setFlag(QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "text":
            item = QGraphicsTextItem()
            item.setHtml(d["html"])
            item.setDefaultTextColor(QColor(d["color"]))
            item.setFont(QFont(d["font_family"], d["font_size"]))
            item.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

    return items


# ---------------------------------------------------------------------------
# Item-level property application helpers
# ---------------------------------------------------------------------------

def _apply_stroke_to_item(item: object, color: QColor) -> None:
    """Apply *color* as the stroke (pen) of *item* in-place."""
    # ArrowItem has its own setter that keeps shaft + head in sync.
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            item.set_stroke_color(color)
            return
    except ImportError:
        pass

    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem)):
        pen = item.pen()
        pen.setColor(color)
        item.setPen(pen)
    elif isinstance(item, QGraphicsTextItem):
        item.setDefaultTextColor(color)


def _apply_fill_to_item(item: object, color: QColor) -> None:
    """Apply *color* as the fill (brush) of *item* in-place."""
    from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsRectItem  # noqa: PLC0415
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        item.setBrush(QBrush(color))


def _apply_width_to_item(item: object, width: int) -> None:
    """Apply *width* as the pen width of *item* in-place."""
    # ArrowItem has its own setter that preserves cap style.
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            item.set_stroke_width(width)
            return
    except ImportError:
        pass

    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
    )
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem)):
        pen = item.pen()
        pen.setWidth(width)
        item.setPen(pen)
