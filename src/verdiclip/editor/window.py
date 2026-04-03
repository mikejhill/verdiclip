"""Main editor window with toolbar, properties panel, and canvas."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QSlider,
    QStatusBar,
    QWidget,
)

from verdiclip import __app_name__
from verdiclip.editor.canvas import EditorCanvas
from verdiclip.editor.history import EditorHistory
from verdiclip.editor.properties import PropertiesPanel
from verdiclip.editor.serialization import (
    _apply_fill_to_item,
    _apply_stroke_to_item,
    _apply_width_to_item,
    _deserialise_items,
    _serialise_items,
)
from verdiclip.editor.toolbar import EditorToolbar, ToolType

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsItem

    from verdiclip.config import Config
    from verdiclip.editor.tools.base import BaseTool

logger = logging.getLogger(__name__)


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

    def _update_tool_font(self, font: QFont) -> None:
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

    # ------------------------------------------------------------------
    # Key events — intercept Esc when focus is on toolbar / properties
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        """Forward Esc and arrow keys to the canvas even when a toolbar widget has focus."""
        if event.key() in (
            Qt.Key.Key_Escape,
            Qt.Key.Key_Left, Qt.Key.Key_Right,
            Qt.Key.Key_Up, Qt.Key.Key_Down,
        ):
            self._canvas.setFocus()
            self._canvas.keyPressEvent(event)
            return
        super().keyPressEvent(event)

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

    def _sync_properties_from_selection(self, selected: list[QGraphicsItem]) -> None:
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
