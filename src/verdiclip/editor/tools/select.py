"""Selection tool for selecting, moving, resizing, and deleting items."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem

from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY
from verdiclip.editor.tools.base import BaseTool
from verdiclip.editor.tools.handles import HandleRole, ResizeHandle, create_handles_for_item

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

    from verdiclip.editor.canvas import EditorCanvas

logger = logging.getLogger(__name__)


def _resolve_top_level_item(item: QGraphicsItem) -> QGraphicsItem:
    """Walk up parent chain to find the top-level scene item."""
    while item.parentItem() is not None:
        item = item.parentItem()
    return item


class SelectTool(BaseTool):
    """Tool for selecting, moving, resizing, and deleting scene items."""

    def __init__(self) -> None:
        super().__init__()
        # Drag state
        self._dragging = False
        self._drag_start: QPointF | None = None
        self._drag_items: list[QGraphicsItem] = []
        self._drag_starts: dict[int, QPointF] = {}

        # Resize state
        self._resizing = False
        self._active_handle: ResizeHandle | None = None
        self._resize_start: QPointF | None = None
        self._resize_old_geometry: dict[str, Any] | None = None

        # Rubber band
        self._rubber_banding = False
        self._rubber_band_origin: QPointF | None = None
        self._rubber_band_rect: QGraphicsRectItem | None = None

        # Current handles in the scene
        self._handles: list[ResizeHandle] = []

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate(self, scene: QGraphicsScene, view: EditorCanvas) -> None:
        """Set arrow cursor and enable item interaction."""
        super().activate(scene, view)
        if view:
            # Unset view-level cursor so QGraphicsItem cursors (e.g., on
            # resize handles) are respected by Qt's scene cursor management.
            view.unsetCursor()
            view.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def deactivate(self) -> None:
        """Clear handles and deactivate the tool."""
        self.clear_handles()
        super().deactivate()

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def _is_annotation(self, item: QGraphicsItem) -> bool:
        """Return True if the item is a selectable annotation (not bg/boundary/handle)."""
        z = item.zValue()
        return z > Z_BACKGROUND and z < Z_BOUNDARY

    def _find_annotation_at(self, scene_pos: QPointF) -> QGraphicsItem | None:
        """Find the topmost annotation item at the given scene position."""
        if not self._scene:
            return None
        transform = self._view.transform() if self._view else self._scene.views()[0].transform()
        for raw in self._scene.items(
            scene_pos,
            Qt.ItemSelectionMode.IntersectsItemShape,
            Qt.SortOrder.DescendingOrder,
            transform,
        ):
            top = _resolve_top_level_item(raw)
            if self._is_annotation(top):
                return top
        return None

    def _find_handle_at(self, scene_pos: QPointF) -> ResizeHandle | None:
        """Return the resize handle at *scene_pos*, if any."""
        if not self._scene or not self._handles:
            return None
        transform = self._view.transform() if self._view else self._scene.views()[0].transform()
        for raw in self._scene.items(
            scene_pos,
            Qt.ItemSelectionMode.IntersectsItemShape,
            Qt.SortOrder.DescendingOrder,
            transform,
        ):
            if isinstance(raw, ResizeHandle):
                return raw
        return None

    # ------------------------------------------------------------------
    # Handle management (called by EditorWindow on selection changes)
    # ------------------------------------------------------------------

    def update_selection_handles(self, selected_items: list[QGraphicsItem]) -> None:
        """Refresh resize handles for the current selection.

        Handles are shown only when exactly one resizable item is selected.
        """
        self.clear_handles()
        if len(selected_items) != 1 or not self._scene:
            return
        item = selected_items[0]
        self._handles = create_handles_for_item(item)
        for h in self._handles:
            self._scene.addItem(h)

    def clear_handles(self) -> None:
        """Remove all resize handles from the scene."""
        for h in self._handles:
            if h.scene():
                h.scene().removeItem(h)
        self._handles.clear()

    def refresh_handle_positions(self) -> None:
        """Update handle positions after target geometry changes."""
        for h in self._handles:
            h.update_position()

    def _snap_endpoint_delta(
        self,
        handle: ResizeHandle,
        scene_pos: QPointF,
    ) -> QPointF:
        """Compute a delta that snaps the moved endpoint to 45° relative to the other end."""
        import math

        from verdiclip.editor.tools.handles import compute_handle_scene_pos

        target = handle.target
        moving = handle.role
        # Determine the fixed endpoint (the one NOT being dragged)
        fixed_role = HandleRole.LINE_P2 if moving == HandleRole.LINE_P1 else HandleRole.LINE_P1
        fixed_pos = compute_handle_scene_pos(target, fixed_role)
        if fixed_pos is None:
            assert self._resize_start is not None
            return scene_pos - self._resize_start

        # Snap the cursor position to 45° from the fixed endpoint
        dx = scene_pos.x() - fixed_pos.x()
        dy = scene_pos.y() - fixed_pos.y()
        angle = math.atan2(dy, dx)
        snap_rad = math.radians(45.0)
        snapped = round(angle / snap_rad) * snap_rad
        length = math.hypot(dx, dy)
        snapped_pos = QPointF(
            fixed_pos.x() + length * math.cos(snapped),
            fixed_pos.y() + length * math.sin(snapped),
        )
        # The current position of the moving handle
        current_pos = compute_handle_scene_pos(target, moving)
        if current_pos is None:
            assert self._resize_start is not None
            return scene_pos - self._resize_start
        return snapped_pos - current_pos

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Handle click on handles, items, or empty space for selection."""
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return

        # Priority 1: click on a resize handle
        handle = self._find_handle_at(scene_pos)
        if handle:
            self._resizing = True
            self._active_handle = handle
            self._resize_start = scene_pos
            from verdiclip.editor.history import capture_geometry

            self._resize_old_geometry = capture_geometry(handle.target)
            return

        # Priority 2: click on an annotation item
        item = self._find_annotation_at(scene_pos)
        if item and self._is_annotation(item):
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                for i in self._scene.selectedItems():
                    if i is not item:
                        i.setSelected(False)
            item.setSelected(True)
            self._dragging = True
            self._drag_start = scene_pos
            # Capture start positions for ALL selected items (multi-drag)
            self._drag_items = list(self._scene.selectedItems())
            self._drag_starts = {id(i): i.pos() for i in self._drag_items}
            return

        # Priority 3: click on empty space → rubber band selection
        for i in self._scene.selectedItems():
            i.setSelected(False)
        self._rubber_banding = True
        self._rubber_band_origin = scene_pos
        pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._rubber_band_rect = QGraphicsRectItem()
        self._rubber_band_rect.setPen(pen)
        self._rubber_band_rect.setBrush(QColor(0, 120, 215, 30))
        self._rubber_band_rect.setZValue(Z_BOUNDARY + 1)
        self._scene.addItem(self._rubber_band_rect)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Handle drag for resizing, moving, or rubber band selection."""
        if self._resizing and self._active_handle and self._resize_start is not None:
            delta = scene_pos - self._resize_start
            # Shift → 45° snap for line/arrow endpoint handles
            if (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and self._active_handle.role in (HandleRole.LINE_P1, HandleRole.LINE_P2)
            ):
                delta = self._snap_endpoint_delta(self._active_handle, scene_pos)
            self._active_handle.apply_drag(delta)
            self._resize_start = scene_pos
            # Keep other handles in sync
            for h in self._handles:
                if h is not self._active_handle:
                    h.update_position()
            return

        if self._dragging and self._drag_start is not None:
            delta = scene_pos - self._drag_start
            for drag_item in self._drag_items:
                start = self._drag_starts.get(id(drag_item))
                if start is not None:
                    drag_item.setPos(start + delta)
            # Keep handles in sync while dragging
            self.refresh_handle_positions()
            return

        if (
            self._rubber_banding
            and self._rubber_band_origin is not None
            and self._rubber_band_rect is not None
        ):
            rect = QRectF(self._rubber_band_origin, scene_pos).normalized()
            self._rubber_band_rect.setRect(rect)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        """Finalize resize, move, or rubber band selection with undo support."""
        # Finish resize
        if self._resizing and self._active_handle:
            handle = self._active_handle
            can_undo = (
                self._resize_old_geometry
                and self._view
                and hasattr(self._view, "add_resize_undoable")
            )
            if can_undo:
                from verdiclip.editor.history import capture_geometry

                new_geometry = capture_geometry(handle.target)
                assert self._view is not None
                assert self._resize_old_geometry is not None
                self._view.add_resize_undoable(
                    handle.target,
                    self._resize_old_geometry,
                    new_geometry,
                )
            self._resizing = False
            self._active_handle = None
            self._resize_start = None
            self._resize_old_geometry = None
            return

        # Finish rubber band
        if self._rubber_banding and self._rubber_band_rect is not None:
            rect = self._rubber_band_rect.rect()
            assert self._scene is not None
            for item in self._scene.items(rect, Qt.ItemSelectionMode.IntersectsItemShape):
                top = _resolve_top_level_item(item)
                if self._is_annotation(top):
                    top.setSelected(True)
            self._scene.removeItem(self._rubber_band_rect)
            self._rubber_band_rect = None
            self._rubber_band_origin = None
            self._rubber_banding = False
            return

        # Finish item drag — push multi-move undo if items actually moved
        if self._dragging and self._drag_items and self._drag_start is not None:
            moves = []
            for drag_item in self._drag_items:
                old = self._drag_starts.get(id(drag_item))
                if old is not None:
                    new = drag_item.pos()
                    if new != old:
                        moves.append((drag_item, old, new))
            if moves and self._view and hasattr(self._view, "add_moves_undoable"):
                self._view.add_moves_undoable(moves)
            elif (
                moves
                and len(moves) == 1
                and self._view
                and hasattr(self._view, "add_move_undoable")
            ):
                item, old, new = moves[0]
                self._view.add_move_undoable(item, (old.x(), old.y()), (new.x(), new.y()))

        self._dragging = False
        self._drag_start = None
        self._drag_items = []
        self._drag_starts = {}

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def select_all(self) -> None:
        """Select all annotation items in the scene."""
        if not self._scene:
            return
        for item in self._scene.items():
            top = _resolve_top_level_item(item)
            if self._is_annotation(top) and not top.isSelected():
                top.setSelected(True)
