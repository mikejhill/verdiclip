"""Editor canvas (QGraphicsView subclass) for image annotation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY

if TYPE_CHECKING:
    from PySide6.QtGui import (
        QKeyEvent,
        QMouseEvent,
    )

    from verdiclip.editor.history import EditorHistory
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
        elif event.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right,
            Qt.Key.Key_Up, Qt.Key.Key_Down,
        ):
            selected = self._scene.selectedItems()
            if selected:
                step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
                dx, dy = 0.0, 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                from PySide6.QtCore import QPointF as _QPointF  # noqa: PLC0415
                offset = _QPointF(dx, dy)
                for item in selected:
                    item.setPos(item.pos() + offset)
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


# Backward-compatible re-exports (moved to dedicated modules)
from verdiclip.editor.serialization import (  # noqa: F401, E402
    _deserialise_items,
    _serialise_items,
)
from verdiclip.editor.window import EditorWindow  # noqa: F401, E402
