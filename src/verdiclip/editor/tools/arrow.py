"""Arrow drawing tool and selectable ArrowItem annotation."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.editor.tools.base import BaseTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)

_ARROWHEAD_LENGTH = 20
_ARROWHEAD_ANGLE = math.radians(25)


# ---------------------------------------------------------------------------
# Shared geometry helpers
# ---------------------------------------------------------------------------

def _build_arrowhead_path(
    p1: QPointF, p2: QPointF, stroke_width: int = 3,
) -> QPainterPath:
    """Return a filled triangle path for an arrowhead pointing from *p1* to *p2*.

    The arrowhead scales with *stroke_width* so it remains visible and
    proportional at any line thickness.
    """
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    angle = math.atan2(dy, dx)

    # Scale the arrowhead with stroke width (base size + proportional growth)
    head_length = _ARROWHEAD_LENGTH + stroke_width * 1.5

    left = QPointF(
        p2.x() - head_length * math.cos(angle - _ARROWHEAD_ANGLE),
        p2.y() - head_length * math.sin(angle - _ARROWHEAD_ANGLE),
    )
    right = QPointF(
        p2.x() - head_length * math.cos(angle + _ARROWHEAD_ANGLE),
        p2.y() - head_length * math.sin(angle + _ARROWHEAD_ANGLE),
    )

    path = QPainterPath()
    path.moveTo(p2)    # Tip of the arrow
    path.lineTo(left)
    path.lineTo(right)
    path.closeSubpath()
    return path


def _snap_45(origin: QPointF, pos: QPointF) -> QPointF:
    """Snap *pos* to the nearest 45-degree angle from *origin*."""
    dx = pos.x() - origin.x()
    dy = pos.y() - origin.y()
    angle = math.atan2(dy, dx)
    snap_rad = math.radians(45.0)
    snapped = round(angle / snap_rad) * snap_rad
    length = math.hypot(dx, dy)
    return QPointF(
        origin.x() + length * math.cos(snapped),
        origin.y() + length * math.sin(snapped),
    )


# ---------------------------------------------------------------------------
# ArrowItem — persistent annotation item
# ---------------------------------------------------------------------------

class ArrowItem(QGraphicsItemGroup):
    """Selectable arrow annotation comprising a shaft line and a filled arrowhead.

    Endpoints are stored in item-local coordinates.  When the item has not been
    moved (``pos() == (0, 0)``), local coordinates equal scene coordinates.  Use
    :meth:`get_scene_p1` / :meth:`get_scene_p2` for scene-space positions and
    :meth:`set_scene_p1` / :meth:`set_scene_p2` to move endpoints from scene
    space (e.g., from a resize handle drag).
    """

    def __init__(
        self,
        p1: QPointF,
        p2: QPointF,
        stroke_color: QColor,
        stroke_width: int,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color
        self._stroke_width = stroke_width

        # Shaft: FlatCap so the stroke stops exactly at the arrowhead junction.
        shaft_pen = QPen(stroke_color, stroke_width)
        shaft_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        self._shaft = QGraphicsLineItem()
        self._shaft.setPen(shaft_pen)
        self.addToGroup(self._shaft)

        # Arrowhead: filled triangle with no stroke border → perfectly sharp tip.
        self._head = QGraphicsPathItem()
        self._head.setPen(QPen(Qt.PenStyle.NoPen))
        self._head.setBrush(QBrush(stroke_color))
        self.addToGroup(self._head)

        self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable)
        self._logical_p1 = QPointF(p1)
        self._logical_p2 = QPointF(p2)
        self.update_endpoints(p1, p2)

    def boundingRect(self) -> QRectF:  # noqa: N802
        """Return combined bounding rect of shaft and head children.

        QGraphicsItemGroup.boundingRect() can return empty when children are
        positioned in local space.  This override ensures hit-testing, scene
        bounding-rect queries, and crop intersection checks work correctly.
        """
        return self.childrenBoundingRect()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def update_endpoints(self, p1: QPointF, p2: QPointF) -> None:
        """Set shaft and arrowhead geometry for *p1* → *p2* (item-local coords)."""
        self._logical_p1 = QPointF(p1)
        self._logical_p2 = QPointF(p2)
        self._head.setPath(_build_arrowhead_path(p1, p2, self._stroke_width))
        # Shorten shaft so it ends at the arrowhead base, preventing the
        # thick stroke from covering the pointed tip.
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.sqrt(dx * dx + dy * dy)
        if length > 0:
            head_length = _ARROWHEAD_LENGTH + self._stroke_width * 1.5
            ratio = max(0, (length - head_length)) / length
            shaft_p2 = QPointF(
                p1.x() + dx * ratio,
                p1.y() + dy * ratio,
            )
            self._shaft.setLine(QLineF(p1, shaft_p2))
        else:
            self._shaft.setLine(QLineF(p1, p2))

    @property
    def shaft_line(self) -> QLineF:
        """The logical endpoint-to-endpoint line (item-local coordinates)."""
        return QLineF(self._logical_p1, self._logical_p2)

    def get_scene_p1(self) -> QPointF:
        """Return the tail endpoint in scene coordinates."""
        return self.mapToScene(self._logical_p1)

    def get_scene_p2(self) -> QPointF:
        """Return the tip (arrowhead) endpoint in scene coordinates."""
        return self.mapToScene(self._logical_p2)

    def set_scene_p1(self, scene_pos: QPointF) -> None:
        """Move the tail endpoint, keeping the tip fixed (scene coords)."""
        local = self.mapFromScene(scene_pos)
        self.update_endpoints(local, self._logical_p2)

    def set_scene_p2(self, scene_pos: QPointF) -> None:
        """Move the tip endpoint, keeping the tail fixed (scene coords)."""
        local = self.mapFromScene(scene_pos)
        self.update_endpoints(self._logical_p1, local)

    # ------------------------------------------------------------------
    # Property setters (for the properties panel)
    # ------------------------------------------------------------------

    def set_stroke_color(self, color: QColor) -> None:
        """Update shaft and arrowhead colour."""
        self._stroke_color = color
        pen = self._shaft.pen()
        pen.setColor(color)
        self._shaft.setPen(pen)
        self._head.setBrush(QBrush(color))

    def set_stroke_width(self, width: int) -> None:
        """Update shaft line width and rescale arrowhead to match."""
        self._stroke_width = width
        pen = self._shaft.pen()
        pen.setWidth(width)
        self._shaft.setPen(pen)
        # Rebuild shaft and arrowhead with the new width
        self.update_endpoints(self._logical_p1, self._logical_p2)


# ---------------------------------------------------------------------------
# ArrowTool — drawing interaction
# ---------------------------------------------------------------------------

class ArrowTool(BaseTool):
    """Draw lines with arrowheads."""

    def __init__(
        self,
        stroke_color: QColor | None = None,
        stroke_width: int = 3,
    ) -> None:
        super().__init__()
        self._stroke_color = stroke_color or QColor("#FF0000")
        self._stroke_width = stroke_width
        self._origin: QPointF | None = None
        self._arrow: ArrowItem | None = None

    def activate(self, scene: QGraphicsScene, view: QGraphicsView) -> None:
        super().activate(scene, view)
        if view:
            view.setCursor(Qt.CursorShape.CrossCursor)

    def mouse_press(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if not self._scene or event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = scene_pos
        self._arrow = ArrowItem(scene_pos, scene_pos, self._stroke_color, self._stroke_width)
        self._scene.addItem(self._arrow)

    def mouse_move(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._arrow is None or self._origin is None:
            return
        end = scene_pos
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = _snap_45(self._origin, scene_pos)
        self._arrow.update_endpoints(self._origin, end)

    def mouse_release(self, scene_pos: QPointF, event: QMouseEvent) -> None:
        if self._arrow and self._origin:
            length = QLineF(self._origin, scene_pos).length()
            if length < 5:
                if self._arrow.scene():
                    self._scene.removeItem(self._arrow)
            elif self._view and hasattr(self._view, "add_item_undoable"):
                self._view.add_item_undoable(self._arrow, "Draw arrow")
        self._arrow = None
        self._origin = None

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_color = color

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
