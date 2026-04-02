"""Resize and endpoint handles for annotation items.

Each ``ResizeHandle`` is a small square item placed at a key location around
an annotation (corners, edge mid-points, or line endpoints).  The ``SelectTool``
adds these to the scene when exactly one resizable item is selected and removes
them on deselect or tool switch.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import TYPE_CHECKING

from PySide6.QtCore import QLineF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
)

from verdiclip.editor import Z_BOUNDARY

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsItem

logger = logging.getLogger(__name__)

# Visual constants
_HANDLE_SIZE: float = 8.0
_HALF: float = _HANDLE_SIZE / 2.0
_MIN_SIZE: float = 4.0
_MIN_MARKER_RADIUS: float = 8.0

_HANDLE_PEN = QPen(QColor(0, 120, 215), 1)
_HANDLE_BRUSH = QBrush(QColor(255, 255, 255))


class HandleRole(Enum):
    """Position role for a resize or endpoint handle."""

    NW = auto()
    N = auto()
    NE = auto()
    E = auto()
    SE = auto()
    S = auto()
    SW = auto()
    W = auto()
    LINE_P1 = auto()
    LINE_P2 = auto()


_RECT_ROLES: list[HandleRole] = [
    HandleRole.NW,
    HandleRole.N,
    HandleRole.NE,
    HandleRole.E,
    HandleRole.SE,
    HandleRole.S,
    HandleRole.SW,
    HandleRole.W,
]

_CORNER_ROLES: list[HandleRole] = [
    HandleRole.NW,
    HandleRole.NE,
    HandleRole.SE,
    HandleRole.SW,
]

_ROLE_CURSORS: dict[HandleRole, Qt.CursorShape] = {
    HandleRole.NW: Qt.CursorShape.SizeFDiagCursor,
    HandleRole.N: Qt.CursorShape.SizeVerCursor,
    HandleRole.NE: Qt.CursorShape.SizeBDiagCursor,
    HandleRole.E: Qt.CursorShape.SizeHorCursor,
    HandleRole.SE: Qt.CursorShape.SizeFDiagCursor,
    HandleRole.S: Qt.CursorShape.SizeVerCursor,
    HandleRole.SW: Qt.CursorShape.SizeBDiagCursor,
    HandleRole.W: Qt.CursorShape.SizeHorCursor,
    HandleRole.LINE_P1: Qt.CursorShape.CrossCursor,
    HandleRole.LINE_P2: Qt.CursorShape.CrossCursor,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scene_rect_for(item: QGraphicsItem) -> QRectF | None:
    """Return the scene-coordinate bounding rectangle for rect/ellipse items."""
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        r = item.rect()
        p = item.pos()
        return QRectF(p.x() + r.x(), p.y() + r.y(), r.width(), r.height())

    try:
        from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
        if isinstance(item, ObfuscationItem):
            p = item.pos()
            s = item._size
            return QRectF(p.x(), p.y(), s.width(), s.height())
    except ImportError:
        pass

    return None


def compute_handle_scene_pos(item: QGraphicsItem, role: HandleRole) -> QPointF | None:
    """Return the scene-space anchor position for *role* on *item*."""
    # Arrow endpoints
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            if role == HandleRole.LINE_P1:
                return item.get_scene_p1()
            if role == HandleRole.LINE_P2:
                return item.get_scene_p2()
            return None
    except ImportError:
        pass

    if role in (HandleRole.LINE_P1, HandleRole.LINE_P2):
        if isinstance(item, QGraphicsLineItem):
            line = item.line()
            pt = line.p1() if role == HandleRole.LINE_P1 else line.p2()
            return item.mapToScene(pt)
        return None

    r = _scene_rect_for(item)
    if r is None:
        return None

    cx = (r.left() + r.right()) / 2.0
    cy = (r.top() + r.bottom()) / 2.0

    positions: dict[HandleRole, QPointF] = {
        HandleRole.NW: QPointF(r.left(), r.top()),
        HandleRole.N:  QPointF(cx,       r.top()),
        HandleRole.NE: QPointF(r.right(), r.top()),
        HandleRole.E:  QPointF(r.right(), cy),
        HandleRole.SE: QPointF(r.right(), r.bottom()),
        HandleRole.S:  QPointF(cx,        r.bottom()),
        HandleRole.SW: QPointF(r.left(),  r.bottom()),
        HandleRole.W:  QPointF(r.left(),  cy),
    }
    return positions.get(role)


def apply_resize(item: QGraphicsItem, role: HandleRole, scene_delta: QPointF) -> None:
    """Apply a scene-coordinate drag *scene_delta* to resize/reshape *item*.

    No-op when the delta would shrink below the minimum allowed size.
    """
    # Arrow endpoint handles
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            if role == HandleRole.LINE_P1:
                item.set_scene_p1(item.get_scene_p1() + scene_delta)
            elif role == HandleRole.LINE_P2:
                item.set_scene_p2(item.get_scene_p2() + scene_delta)
            return
    except ImportError:
        pass

    if role in (HandleRole.LINE_P1, HandleRole.LINE_P2):
        if isinstance(item, QGraphicsLineItem):
            # Delta in item-local coords equals scene delta because items have no rotation.
            line = item.line()
            if role == HandleRole.LINE_P1:
                item.setLine(QLineF(line.p1() + scene_delta, line.p2()))
            else:
                item.setLine(QLineF(line.p1(), line.p2() + scene_delta))
        return

    # NumberMarkerItem: 1:1 (circular) resize
    try:
        from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
        if isinstance(item, NumberMarkerItem):
            _resize_number_marker(item, role, scene_delta)
            return
    except ImportError:
        pass

    try:
        from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
        if isinstance(item, ObfuscationItem):
            _resize_obfuscation(item, role, scene_delta)
            return
    except ImportError:
        pass

    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        _resize_rect_item(item, role, scene_delta)


def _resize_rect_item(item: QGraphicsRectItem | QGraphicsEllipseItem,
                      role: HandleRole, delta: QPointF) -> None:
    dx, dy = delta.x(), delta.y()
    r = item.rect()
    x1, y1, x2, y2 = r.left(), r.top(), r.right(), r.bottom()

    match role:
        case HandleRole.NW:
            x1, y1 = x1 + dx, y1 + dy
        case HandleRole.N:
            y1 = y1 + dy
        case HandleRole.NE:
            x2, y1 = x2 + dx, y1 + dy
        case HandleRole.E:
            x2 = x2 + dx
        case HandleRole.SE:
            x2, y2 = x2 + dx, y2 + dy
        case HandleRole.S:
            y2 = y2 + dy
        case HandleRole.SW:
            x1, y2 = x1 + dx, y2 + dy
        case HandleRole.W:
            x1 = x1 + dx
        case _:
            return

    new_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
    if new_rect.width() >= _MIN_SIZE and new_rect.height() >= _MIN_SIZE:
        item.setRect(new_rect)


def _resize_obfuscation(item: object, role: HandleRole, delta: QPointF) -> None:
    from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
    assert isinstance(item, ObfuscationItem)

    dx, dy = delta.x(), delta.y()
    pos = item.pos()
    size = item._size
    x1, y1 = pos.x(), pos.y()
    x2, y2 = x1 + size.width(), y1 + size.height()

    match role:
        case HandleRole.NW:
            x1, y1 = x1 + dx, y1 + dy
        case HandleRole.N:
            y1 = y1 + dy
        case HandleRole.NE:
            x2, y1 = x2 + dx, y1 + dy
        case HandleRole.E:
            x2 = x2 + dx
        case HandleRole.SE:
            x2, y2 = x2 + dx, y2 + dy
        case HandleRole.S:
            y2 = y2 + dy
        case HandleRole.SW:
            x1, y2 = x1 + dx, y2 + dy
        case HandleRole.W:
            x1 = x1 + dx
        case _:
            return

    w, h = x2 - x1, y2 - y1
    if w >= _MIN_SIZE and h >= _MIN_SIZE:
        item.set_geometry(QPointF(x1, y1), QSizeF(w, h))


def _resize_number_marker(item: object, role: HandleRole, delta: QPointF) -> None:
    """Resize a NumberMarkerItem keeping width == height (circular)."""
    from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
    assert isinstance(item, NumberMarkerItem)

    dx, dy = delta.x(), delta.y()

    # Map corner drag direction to a signed radius change.
    # Each corner handle sits at (±r, ±r); dragging it outward grows the circle.
    match role:
        case HandleRole.NW:
            dr = (-dx - dy) / 2.0   # Up-left → grow
        case HandleRole.NE:
            dr = (dx - dy) / 2.0    # Up-right → grow
        case HandleRole.SE:
            dr = (dx + dy) / 2.0    # Down-right → grow
        case HandleRole.SW:
            dr = (-dx + dy) / 2.0   # Down-left → grow
        case _:
            return

    current_r = item.rect().width() / 2.0
    new_r = max(_MIN_MARKER_RADIUS, current_r + dr)
    item.setRect(QRectF(-new_r, -new_r, 2.0 * new_r, 2.0 * new_r))
    item._center_text()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ResizeHandle(QGraphicsRectItem):
    """A small square handle for resizing or reshaping an annotation item.

    The handle renders itself at z > Z_BOUNDARY so it is always on top and
    is not treated as an annotation by ``SelectTool._is_annotation``.
    """

    def __init__(self, target: QGraphicsItem, role: HandleRole) -> None:
        super().__init__(-_HALF, -_HALF, _HANDLE_SIZE, _HANDLE_SIZE)
        self._target = target
        self._role = role
        self.setPen(_HANDLE_PEN)
        self.setBrush(_HANDLE_BRUSH)
        self.setZValue(Z_BOUNDARY + 2)
        self.setCursor(_ROLE_CURSORS.get(role, Qt.CursorShape.CrossCursor))
        self.update_position()

    @property
    def target(self) -> QGraphicsItem:
        """The annotation item this handle is attached to."""
        return self._target

    @property
    def role(self) -> HandleRole:
        """The position role this handle represents."""
        return self._role

    def update_position(self) -> None:
        """Reposition this handle based on the current target geometry."""
        pos = compute_handle_scene_pos(self._target, self._role)
        if pos is not None:
            self.setPos(pos)

    def apply_drag(self, scene_delta: QPointF) -> None:
        """Drag the handle by *scene_delta*, resizing the target, then reposition."""
        apply_resize(self._target, self._role, scene_delta)
        self.update_position()


def create_handles_for_item(item: QGraphicsItem) -> list[ResizeHandle]:
    """Create the appropriate resize/reshape handles for *item*.

    Returns an empty list for item types that do not support resizing
    (e.g., text items, freehand paths).
    """
    # Arrow: two endpoint handles
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            return [ResizeHandle(item, HandleRole.LINE_P1), ResizeHandle(item, HandleRole.LINE_P2)]
    except ImportError:
        pass

    # NumberMarkerItem: four corner handles (1:1 resize only)
    try:
        from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
        if isinstance(item, NumberMarkerItem):
            return [ResizeHandle(item, r) for r in _CORNER_ROLES]
    except ImportError:
        pass

    # ObfuscationItem: eight handles
    try:
        from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
        if isinstance(item, ObfuscationItem):
            return [ResizeHandle(item, r) for r in _RECT_ROLES]
    except ImportError:
        pass

    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        return [ResizeHandle(item, r) for r in _RECT_ROLES]

    if isinstance(item, QGraphicsLineItem):
        return [ResizeHandle(item, HandleRole.LINE_P1), ResizeHandle(item, HandleRole.LINE_P2)]

    return []
