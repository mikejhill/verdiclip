"""Full-screen overlays, one per monitor: drag a region or click a window."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final, Protocol, override

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QScreen,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import ScreenGeometry
from verdiclip.geometry import Point, Rect

DRAG_THRESHOLD: Final = 3.0
MAGNIFIER_SOURCE_PX: Final = 31  # Odd, so the cursor pixel sits dead center
MAGNIFIER_ZOOM: Final = 4
MAGNIFIER_OFFSET: Final = 24
DIM: Final = QColor(0, 0, 0, 110)
ACCENT: Final = QColor(0, 120, 215)
RETICLE: Final = QColor(255, 0, 0, 140)


class WindowTarget(Protocol):
    """A window the user can click to capture."""

    @property
    def title(self) -> str:
        """Window title."""

    @property
    def bounds(self) -> Rect:
        """Window rectangle in physical desktop pixels."""


class SharedSelection(QObject):
    """The pointer and the drag, shared by the overlays on every monitor.

    Positions are physical desktop pixels, so a selection can start on one
    monitor and end on another, even when the monitors' scales differ.
    """

    changed = Signal()

    def __init__(self, screens: Sequence[ScreenGeometry] = ()) -> None:
        super().__init__()
        self._screens = list(screens)
        self._pointer: Point | None = None
        self._anchor: Point | None = None
        self._dragging = False

    def add_screen(self, screen: ScreenGeometry) -> None:
        """Include ``screen`` when mapping positions."""
        if screen not in self._screens:
            self._screens.append(screen)

    @property
    def pointer(self) -> Point | None:
        """Return the pointer position, or None before it is known."""
        return self._pointer

    @property
    def pressed(self) -> bool:
        """True between a press (mouse or ``Enter``) and its release."""
        return self._anchor is not None

    @property
    def dragging(self) -> bool:
        """True once the pointer has moved past the drag threshold since the press."""
        return self._dragging

    def area(self) -> Rect | None:
        """Return the dragged rectangle, snapped to whole pixels, while dragging."""
        if not self._dragging or self._anchor is None or self._pointer is None:
            return None
        return Rect.from_points(self._anchor, self._pointer).rounded()

    def screen_at(self, physical: Point) -> ScreenGeometry | None:
        """Return the monitor showing ``physical``, if any."""
        return next((s for s in self._screens if _covers(s.physical, physical)), None)

    def to_physical(self, logical: Point) -> Point:
        """Map a Qt global position to physical pixels on the nearest monitor.

        Qt keeps each monitor's top-left at its physical position and scales
        only its size, so the monitor holding a global position is found in
        logical space (the right and bottom edges belong to the next monitor),
        then the offset from its corner is scaled.
        """
        screen = next((s for s in self._screens if _covers(s.logical, logical)), None)
        if screen is None:  # Off every monitor (a gap): snap to the nearest
            screen = min(
                self._screens,
                key=lambda s: _clamp(logical, s.logical).distance_to(logical),
            )
        return screen.to_physical(
            _clamp(logical, screen.logical) - screen.logical.top_left
        )

    def move_to(self, physical: Point) -> None:
        """Move the pointer; a press becomes a drag past the threshold."""
        self._track(physical)
        self.changed.emit()

    def press(self, physical: Point) -> None:
        """Start a selection at ``physical``."""
        self._pointer = self._anchor = physical
        self._dragging = False
        self.changed.emit()

    def release(self, physical: Point) -> Rect | None:
        """End the press at ``physical``; return the dragged area (None for a click)."""
        self._track(physical)
        area = self.area()
        self._anchor = None
        self._dragging = False
        self.changed.emit()
        return area

    def _track(self, physical: Point) -> None:
        """Record the pointer and start dragging once it moves far enough."""
        self._pointer = physical
        if (
            self._anchor is not None
            and self._anchor.distance_to(physical) >= DRAG_THRESHOLD
        ):
            self._dragging = True


def _covers(area: Rect, point: Point) -> bool:
    """Return True if ``point`` is on ``area`` (right and bottom edges excluded)."""
    return area.left <= point.x < area.right and area.top <= point.y < area.bottom


def _clamp(point: Point, area: Rect) -> Point:
    """Return the point of ``area`` nearest to ``point``."""
    return Point(
        min(max(point.x, area.left), area.right),
        min(max(point.y, area.top), area.bottom),
    )


class SelectionOverlay(QWidget):
    """Shows the frozen screen of one monitor and reports the user's choice.

    Overlays sharing one ``SharedSelection`` act as a single surface: the
    selection and the window highlight continue across monitors, and ``Enter``
    or a click on any of them acts at the pointer wherever it is.
    """

    region_chosen = Signal(object)  # Rect (physical)
    window_chosen = Signal(object, str)  # Rect (physical), title
    window_selected = Signal(
        object, object
    )  # immutable window snapshot, physical point
    cancelled = Signal()

    def __init__(  # noqa: PLR0913
        self,
        geometry: ScreenGeometry,
        screen: QScreen,
        frozen: FrozenScreen,
        windows: Sequence[WindowTarget],
        *,
        show_magnifier: bool,
        shared: SharedSelection | None = None,
        external_input: bool = False,
    ) -> None:
        super().__init__(None)
        self._geometry = geometry
        self._frozen = frozen
        self._image = frozen.crop(geometry.physical)
        self._image.setDevicePixelRatio(geometry.scale)
        self._windows = list(windows)
        self._show_magnifier = show_magnifier
        self._shared = shared if shared is not None else SharedSelection()
        self._shared.add_screen(geometry)
        self._shared.changed.connect(self._on_shared_changed)
        self._shown: tuple[object, ...] | None = None
        self._finishing = False
        self._external_input = external_input
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if external_input:
            flags |= (
                Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput
            )
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        logical = geometry.logical
        self.setGeometry(
            int(logical.x), int(logical.y), int(logical.width), int(logical.height)
        )

    def dismiss(self) -> None:
        """Close as part of an owner-controlled completion, without cancelling."""
        self._finishing = True
        self.close()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Let the capture owner terminate the whole session on window close."""
        super().closeEvent(event)
        if not self._finishing:
            self.cancelled.emit()

    @property
    def screen_geometry(self) -> ScreenGeometry:
        """Return the monitor this overlay covers."""
        return self._geometry

    @property
    def shared(self) -> SharedSelection:
        """Return the selection this overlay shares with the other monitors."""
        return self._shared

    def hovered_window(self) -> WindowTarget | None:
        """Return the topmost window under the cursor, if the cursor is here."""
        pointer = self._pointer_here()
        return None if pointer is None else self._window_at(pointer)

    def selection(self) -> Rect | None:
        """Return the part of the dragged rectangle on this monitor (local logical)."""
        area = self._shared.area()
        if area is None:
            return None
        here = area.intersected(self._geometry.physical)
        return None if here.is_empty else self._geometry.local_rect(here)

    # Painting

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the frozen screen, dimming, highlight, crosshair, and magnifier."""
        del event
        painter = QPainter(self)
        try:
            painter.drawImage(QPointF(0, 0), self._image)
            painter.fillRect(self.rect(), DIM)
            self._paint_target(painter)
            self._paint_crosshair(painter)
            cursor = self._local_cursor()
            if self._show_magnifier and cursor is not None:
                self._paint_magnifier(painter, cursor)
        finally:
            painter.end()

    def _paint_target(self, painter: QPainter) -> None:
        """Highlight the dragged region or the window under the pointer."""
        selection = self.selection()
        if selection is not None:
            area = self._shared.area()
            label = (
                f"{int(area.width)} × {int(area.height)}"  # noqa: RUF001
                if area is not None and self._pointer_here() is not None
                else ""
            )
            self._reveal(painter, selection, label)
            return
        window = self._highlighted()
        if window is not None:
            # The title shows on the cursor's monitor; other monitors the
            # window reaches onto light up their part of it
            title = window.title if self._pointer_here() is not None else ""
            self._reveal(painter, self._geometry.local_rect(window.bounds), title)

    def _reveal(self, painter: QPainter, area: Rect, label: str) -> None:
        """Undim ``area``, outline it, and show ``label`` (if any) beside it."""
        rect = QRectF(area.x, area.y, area.width, area.height)
        painter.drawImage(
            rect,
            self._image,
            QRectF(
                area.x * self._geometry.scale,
                area.y * self._geometry.scale,
                area.width * self._geometry.scale,
                area.height * self._geometry.scale,
            ),
        )
        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        if not label:
            return
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, 480)
        box = QRectF(0, 0, metrics.horizontalAdvance(text) + 12, metrics.height() + 6)
        above = area.y - box.height() - 4
        box.moveTo(max(0.0, area.x), above if above >= 0 else area.bottom + 4)
        painter.fillRect(box, QColor(20, 20, 20, 220))
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter.value), text)

    def _paint_crosshair(self, painter: QPainter) -> None:
        """Draw guide lines through the cursor."""
        cursor = self._local_cursor()
        if cursor is None or self._shared.dragging:
            return
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1, Qt.PenStyle.DashLine))
        x, y = round(cursor.x), round(cursor.y)
        painter.drawLine(x, 0, x, self.height())
        painter.drawLine(0, y, self.width(), y)

    def _paint_magnifier(self, painter: QPainter, cursor: Point) -> None:
        """Draw a 4x loupe of the pixels around the cursor (UX-CAP-06)."""
        px, py = self._cursor_pixel(cursor)
        half = MAGNIFIER_SOURCE_PX // 2
        patch = self._patch(
            Rect(px - half, py - half, MAGNIFIER_SOURCE_PX, MAGNIFIER_SOURCE_PX)
        )
        size = MAGNIFIER_SOURCE_PX * MAGNIFIER_ZOOM
        x = round(cursor.x) + MAGNIFIER_OFFSET
        y = round(cursor.y) + MAGNIFIER_OFFSET
        if x + size > self.width():
            x = round(cursor.x) - MAGNIFIER_OFFSET - size
        if y + size + 20 > self.height():
            y = round(cursor.y) - MAGNIFIER_OFFSET - size - 20
        target = QRectF(x, y, size, size)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, on=False)
        painter.drawImage(target, patch)
        painter.restore()
        self._paint_reticle(painter, target)
        # Dark outer and light inner edge so the loupe reads on any content
        painter.setPen(QPen(QColor(20, 20, 20, 230), 1))
        painter.drawRect(target.adjusted(-2, -2, 2, 2))
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawRect(target)
        coords = QRectF(x, y + size, size, 20)
        painter.fillRect(coords, QColor(20, 20, 20, 220))
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(coords, int(Qt.AlignmentFlag.AlignCenter.value), f"{px}, {py}")

    @staticmethod
    def _paint_reticle(painter: QPainter, loupe: QRectF) -> None:
        """Cross the loupe with bands one source pixel wide, framing the center pixel.

        The bands stop one pixel short of the center so the pixel under the
        cursor stays unobscured; its outline marks exactly what will be captured.
        """
        cell = MAGNIFIER_ZOOM
        center = QRectF(
            loupe.x() + (MAGNIFIER_SOURCE_PX // 2) * cell,
            loupe.y() + (MAGNIFIER_SOURCE_PX // 2) * cell,
            cell,
            cell,
        )
        gap = center.adjusted(-cell, -cell, cell, cell)
        bands = (
            QRectF(loupe.left(), center.top(), gap.left() - loupe.left(), cell),
            QRectF(gap.right(), center.top(), loupe.right() - gap.right(), cell),
            QRectF(center.left(), loupe.top(), cell, gap.top() - loupe.top()),
            QRectF(center.left(), gap.bottom(), cell, loupe.bottom() - gap.bottom()),
        )
        for band in bands:
            painter.fillRect(band, RETICLE)
        painter.setPen(QPen(RETICLE, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # An aliased 1 px pen covers x..x+w, so this rings the cell just outside it
        painter.drawRect(QRectF(center.x() - 1, center.y() - 1, cell + 1, cell + 1))

    def _cursor_pixel(self, cursor: Point) -> tuple[int, int]:
        """Return the physical pixel under ``cursor`` (floored, so negative x works)."""
        physical = self._geometry.to_physical(cursor)
        return math.floor(physical.x), math.floor(physical.y)

    def _patch(self, source: Rect) -> QImage:
        """Return ``source`` pixels from the frozen desktop, black outside it."""
        patch = QImage(
            int(source.width), int(source.height), QImage.Format.Format_RGB32
        )
        patch.fill(Qt.GlobalColor.black)
        inside = source.intersected(self._frozen.bounds)
        if not inside.is_empty:
            painter = QPainter(patch)
            painter.drawImage(
                QPointF(inside.x - source.x, inside.y - source.y),
                self._frozen.crop(inside),
            )
            painter.end()
        return patch

    # Input

    def receive_input(self, event: SelectionInput) -> None:
        """Apply one broker command directly, without Qt popup/native focus routing."""
        if event.kind is InputKind.CANCEL:
            self.cancelled.emit()
            return
        self._shared.move_to(event.point)
        if event.kind is InputKind.PRESS:
            if self._shared.pressed:
                self._finish(event.point)
            else:
                self._shared.press(event.point)
        elif event.kind is InputKind.RELEASE and self._shared.pressed:
            self._finish(event.point)
        elif event.kind is InputKind.ENTER:
            self._enter()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start a selection on left press; cancel on right press.

        A left press while an ``Enter`` selection is open finishes it there.
        """
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        at = self._physical(event)
        if self._shared.pressed:
            self._finish(at)
        else:
            self._shared.press(at)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Track the pointer, here or (while pressed) over another monitor."""
        self._shared.move_to(self._physical(event))

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Report a dragged region, a clicked window, or the monitor clicked."""
        if event.button() != Qt.MouseButton.LeftButton or not self._shared.pressed:
            return
        self._finish(self._physical(event))

    def _finish(self, at: Point) -> None:
        """End the press at ``at``: report a region, a window, or that monitor."""
        area = self._shared.release(at)
        if area is not None:
            if area.width >= DRAG_THRESHOLD and area.height >= DRAG_THRESHOLD:
                self.region_chosen.emit(area)
            return
        window = self._window_at(at)
        if window is not None:
            if self._external_input:
                self.window_selected.emit(window, at)
            else:
                self.window_chosen.emit(
                    window.bounds.intersected(self._frozen.bounds), window.title
                )
            return
        screen = self._shared.screen_at(at) or self._geometry
        self.region_chosen.emit(screen.physical)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Cancel on Esc; Enter starts/ends a selection; arrows move (Ctrl = 10 px)."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self._enter()
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
        moves = {
            Qt.Key.Key_Left: QPoint(-step, 0),
            Qt.Key.Key_Right: QPoint(step, 0),
            Qt.Key.Key_Up: QPoint(0, -step),
            Qt.Key.Key_Down: QPoint(0, step),
        }
        delta = moves.get(Qt.Key(event.key()))
        if delta is None:
            super().keyPressEvent(event)
            return
        if self._external_input and self._shared.pointer is not None:
            self._shared.move_to(self._shared.pointer + Point(delta.x(), delta.y()))
        else:
            QCursor.setPos(QCursor.pos() + delta)

    def _enter(self) -> None:
        """Press at the pointer, or finish the press already started (UX-CAP-15).

        Works from whichever overlay has focus, wherever the pointer is.
        """
        pointer = self._shared.pointer
        if pointer is None:
            return
        if self._shared.pressed:
            self._finish(pointer)
        else:
            self._shared.press(pointer)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Show guides and the window highlight at once, before any mouse move."""
        super().showEvent(event)
        if not self._external_input:
            self.sync_cursor()

    def sync_cursor(self) -> None:
        """Pick up the current pointer position."""
        pos = QCursor.pos()
        self._shared.move_to(self._shared.to_physical(Point(pos.x(), pos.y())))

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Hide the crosshair when the cursor moves to another monitor."""
        del event
        if not self._external_input and not self._shared.pressed:
            self.sync_cursor()

    # Internals

    def _on_shared_changed(self) -> None:
        """Repaint when what this monitor shows has changed."""
        shown = (
            self._local_cursor(),
            self.selection(),
            self._highlighted(),
            self._pointer_here() is not None,
            self._shared.area(),
        )
        if shown != self._shown:
            self._shown = shown
            self.update()

    def _pointer_here(self) -> Point | None:
        """Return the pointer (physical) if it is on this monitor."""
        pointer = self._shared.pointer
        if pointer is None or not _covers(self._geometry.physical, pointer):
            return None
        return pointer

    def _local_cursor(self) -> Point | None:
        """Return the pointer in local logical coordinates if it is on this monitor."""
        pointer = self._pointer_here()
        return None if pointer is None else self._geometry.to_local(pointer)

    def _highlighted(self) -> WindowTarget | None:
        """Return the window under the pointer if it reaches onto this monitor."""
        pointer = self._shared.pointer
        if pointer is None or self._shared.dragging:
            return None
        window = self._window_at(pointer)
        if window is None or not window.bounds.intersects(self._geometry.physical):
            return None
        return window

    def _window_at(self, physical: Point) -> WindowTarget | None:
        """Return the topmost window at ``physical``."""
        return next((w for w in self._windows if w.bounds.contains(physical)), None)

    def _physical(self, event: QMouseEvent) -> Point:
        """Return the event position in physical desktop pixels."""
        pos = event.globalPosition()
        return self._shared.to_physical(Point(pos.x(), pos.y()))
