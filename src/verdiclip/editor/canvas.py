"""The zoomable, scrollable canvas that displays and edits a document."""

from __future__ import annotations

import math
from typing import Final, override

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from verdiclip.document.annotations import (
    Annotation,
    HandleRole,
    LabeledBox,
    ObfuscateShape,
)
from verdiclip.editor.chrome import ChromeStyle
from verdiclip.editor.inline_editors import InlineEditors
from verdiclip.editor.session import EditorSession
from verdiclip.editor.tools import CursorKind, Pointer, Tool
from verdiclip.geometry import Point, Rect
from verdiclip.render.renderer import Renderer

MIN_ZOOM: Final = 0.1
MAX_ZOOM: Final = 16.0
ZOOM_STEP: Final = 1.25
HIT_TOLERANCE_PX: Final = 5.0
HANDLE_SIZE_PX: Final = 8.0
FIT_MARGIN_PX: Final = 24
ACCENT: Final = QColor(0, 120, 215)
CHECKER_LIGHT: Final = QColor(204, 204, 204)
CHECKER_DARK: Final = QColor(153, 153, 153)

CURSORS: Final[dict[CursorKind, Qt.CursorShape]] = {
    CursorKind.ARROW: Qt.CursorShape.ArrowCursor,
    CursorKind.CROSS: Qt.CursorShape.CrossCursor,
    CursorKind.MOVE: Qt.CursorShape.SizeAllCursor,
    CursorKind.IBEAM: Qt.CursorShape.IBeamCursor,
    CursorKind.POINT: Qt.CursorShape.PointingHandCursor,
    CursorKind.RESIZE_H: Qt.CursorShape.SizeHorCursor,
    CursorKind.RESIZE_V: Qt.CursorShape.SizeVerCursor,
    CursorKind.RESIZE_FDIAG: Qt.CursorShape.SizeFDiagCursor,
    CursorKind.RESIZE_BDIAG: Qt.CursorShape.SizeBDiagCursor,
}


class CanvasView(QAbstractScrollArea):
    """Displays the document through the renderer and routes input to the tool."""

    zoom_changed = Signal(float)
    cursor_moved = Signal(object)  # Point in image coordinates, or None
    escape_unhandled = Signal()
    enter_unhandled = Signal()

    def __init__(
        self, session: EditorSession, renderer: Renderer, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._renderer = renderer
        self._tool: Tool | None = None
        self._zoom = 1.0
        self._pixmap = QPixmap.fromImage(session.document.image)
        self._pixmap_key = session.document.image.cacheKey()
        self._pan_anchor: QPointF | None = None
        self._space_held = False
        self._editors = InlineEditors(self, session)
        self._backdrop = ChromeStyle(self.palette()).backdrop
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        session.document.subscribe(self._on_document_changed)
        session.selection.subscribe(self.refresh)

    # Properties

    @property
    def zoom(self) -> float:
        """Return the zoom factor (1.0 = 100%)."""
        return self._zoom

    @property
    def tool(self) -> Tool | None:
        """Return the active tool."""
        return self._tool

    @property
    def editors(self) -> InlineEditors:
        """Return the inline text and counter editors."""
        return self._editors

    @property
    def tolerance(self) -> float:
        """Return the hit tolerance in image pixels at the current zoom."""
        return HIT_TOLERANCE_PX / self._zoom

    # Tool

    def set_tool(self, tool: Tool) -> None:
        """Make ``tool`` active."""
        if self._tool is not None:
            self._tool.deactivate()
        self._editors.commit()
        self._tool = tool
        self.viewport().setCursor(CURSORS[tool.cursor(Pointer(Point(-1e9, -1e9)))])
        self.refresh()

    # Coordinates

    def origin(self) -> QPointF:
        """Return where the crop's top-left corner sits in the viewport.

        Centering snaps to whole pixels so 100% zoom stays pixel-crisp.
        """
        crop = self._session.document.crop
        content_w = crop.width * self._zoom
        content_h = crop.height * self._zoom
        vp = self.viewport()
        x = (
            (vp.width() - content_w) // 2
            if content_w < vp.width()
            else -self.horizontalScrollBar().value()
        )
        y = (
            (vp.height() - content_h) // 2
            if content_h < vp.height()
            else -self.verticalScrollBar().value()
        )
        return QPointF(x, y)

    def to_view(self, point: Point) -> QPointF:
        """Map image coordinates to viewport coordinates."""
        crop = self._session.document.crop
        o = self.origin()
        return QPointF(
            o.x() + (point.x - crop.x) * self._zoom,
            o.y() + (point.y - crop.y) * self._zoom,
        )

    def to_image(self, pos: QPointF) -> Point:
        """Map viewport coordinates to image coordinates."""
        crop = self._session.document.crop
        o = self.origin()
        return Point(
            crop.x + (pos.x() - o.x()) / self._zoom,
            crop.y + (pos.y() - o.y()) / self._zoom,
        )

    def rect_to_view(self, rect: Rect) -> QRectF:
        """Map an image rectangle to the viewport."""
        top_left = self.to_view(rect.top_left)
        return QRectF(
            top_left.x(),
            top_left.y(),
            rect.width * self._zoom,
            rect.height * self._zoom,
        )

    # Zoom

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """Zoom to ``zoom``, keeping the image point under ``anchor`` fixed."""
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
        if math.isclose(zoom, self._zoom):
            return
        vp_center = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        anchor_view = anchor if anchor is not None else vp_center
        fixed = self.to_image(anchor_view)
        self._zoom = zoom
        self._update_scrollbars()
        drift = self.to_view(fixed) - anchor_view
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + round(drift.x())
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + round(drift.y())
        )
        self._editors.reposition()
        self.zoom_changed.emit(self._zoom)
        self.refresh()

    def zoom_in(self) -> None:
        """Zoom in one step around the viewport center."""
        self.set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        """Zoom out one step around the viewport center."""
        self.set_zoom(self._zoom / ZOOM_STEP)

    def zoom_actual(self) -> None:
        """Zoom to 100%."""
        self.set_zoom(1.0)

    def zoom_fit(self) -> None:
        """Zoom so the whole visible image fits the viewport."""
        self.set_zoom(self.fit_zoom())

    def fit_zoom(self) -> float:
        """Return the zoom at which the visible image fits the viewport."""
        crop = self._session.document.crop
        vp = self.viewport()
        avail_w = max(1, vp.width() - FIT_MARGIN_PX)
        avail_h = max(1, vp.height() - FIT_MARGIN_PX)
        return min(avail_w / crop.width, avail_h / crop.height)

    def show_initial(self) -> None:
        """Show at 100%, or fit if larger than the viewport (UX-CAP-11)."""
        self._update_scrollbars()
        if self.fit_zoom() < 1.0:
            self.zoom_fit()

    def refresh(self) -> None:
        """Repaint the viewport."""
        self.viewport().update()

    # Painting

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint backdrop, image, annotations, tool preview, and selection UI."""
        del event
        painter = QPainter(self.viewport())
        try:
            painter.fillRect(self.viewport().rect(), self._backdrop)
            self._paint_document(painter)
            self._paint_overlays(painter)
        finally:
            painter.end()

    def _paint_document(self, painter: QPainter) -> None:
        """Paint the cropped image with annotations in image coordinates."""
        doc = self._session.document
        crop = doc.crop
        crop_view = self.rect_to_view(crop)
        painter.fillRect(crop_view, self._checker_brush())
        painter.save()
        painter.setClipRect(crop_view)
        o = self.origin()
        painter.translate(o)
        painter.scale(self._zoom, self._zoom)
        painter.translate(-crop.x, -crop.y)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        smooth = self._zoom < 1.0
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        shown = self._displayed(doc.visible_annotations)
        self._renderer.paint_all(painter, doc.image, shown)
        if self._tool is not None:
            self._renderer.paint_all(painter, doc.image, self._tool.preview)
        painter.restore()

    def _displayed(self, annotations: tuple[Annotation, ...]) -> list[Annotation]:
        """Return what to draw: the edited item is hidden, or just its label."""
        editing = self._editors.editing_id
        if editing is None:
            return list(annotations)
        shown: list[Annotation] = []
        for annotation in annotations:
            if annotation.id != editing:
                shown.append(annotation)
            elif isinstance(annotation, LabeledBox):
                shown.append(annotation.with_text(""))
        return shown

    def _paint_overlays(self, painter: QPainter) -> None:
        """Paint the tool frame, selection outlines, and handles in view space."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)
        frame = self._tool.frame if self._tool is not None else None
        if frame is not None:
            self._paint_frame(painter, frame)
        preview = self._tool.preview if self._tool is not None else ()
        for annotation in preview:
            if isinstance(annotation, ObfuscateShape):
                self._paint_outline(painter, annotation)
        selected = self._session.selected()
        for annotation in selected:
            # A lone line or arrow is clearer with just its endpoint handles
            if len(selected) == 1 and HandleRole.START in annotation.handles():
                continue
            self._paint_outline(painter, annotation)
        if len(selected) == 1:
            self._paint_handles(painter, selected[0])

    def _paint_frame(self, painter: QPainter, frame: Rect) -> None:
        """Paint a rubber band or crop frame, dimming outside it for crops."""
        frame_view = self.rect_to_view(frame)
        dims = self._tool is not None and self._tool.dims_outside_frame
        if dims:
            crop_view = self.rect_to_view(self._session.document.crop)
            painter.save()
            painter.setClipRect(crop_view)
            for part in self._outside(crop_view, frame_view):
                painter.fillRect(part, QColor(0, 0, 0, 120))
            painter.restore()
        painter.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
        if dims:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.setBrush(QColor(0, 120, 215, 30))
        painter.drawRect(frame_view)

    @staticmethod
    def _outside(outer: QRectF, inner: QRectF) -> list[QRectF]:
        """Return up to four rectangles covering ``outer`` minus ``inner``."""
        return [
            QRectF(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),
            QRectF(
                outer.left(),
                inner.bottom(),
                outer.width(),
                outer.bottom() - inner.bottom(),
            ),
            QRectF(
                outer.left(), inner.top(), inner.left() - outer.left(), inner.height()
            ),
            QRectF(
                inner.right(),
                inner.top(),
                outer.right() - inner.right(),
                inner.height(),
            ),
        ]

    def _paint_outline(self, painter: QPainter, annotation: Annotation) -> None:
        """Paint a dashed selection outline around ``annotation``."""
        painter.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect_to_view(annotation.bounds).adjusted(-2, -2, 2, 2))

    def _paint_handles(self, painter: QPainter, annotation: Annotation) -> None:
        """Paint the grab handles of the single selected annotation."""
        painter.setPen(QPen(ACCENT, 1))
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        half = HANDLE_SIZE_PX / 2
        for pos in annotation.handles().values():
            center = self.to_view(pos)
            painter.drawRect(
                QRectF(
                    center.x() - half, center.y() - half, HANDLE_SIZE_PX, HANDLE_SIZE_PX
                )
            )

    @staticmethod
    def _checker_brush() -> QBrush:
        """Return a checkerboard brush for transparent areas."""
        tile = QImage(16, 16, QImage.Format.Format_RGB32)
        tile.fill(CHECKER_LIGHT)
        painter = QPainter(tile)
        painter.fillRect(0, 0, 8, 8, CHECKER_DARK)
        painter.fillRect(8, 8, 8, 8, CHECKER_DARK)
        painter.end()
        return QBrush(tile)

    # Input

    def _pointer(self, event: QMouseEvent) -> Pointer:
        """Build a Pointer from a mouse event."""
        mods = event.modifiers()
        return Pointer(
            pos=self.to_image(event.position()),
            shift=bool(mods & Qt.KeyboardModifier.ShiftModifier),
            ctrl=bool(mods & Qt.KeyboardModifier.ControlModifier),
            tolerance=self.tolerance,
        )

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Pan with middle button or Space+left; otherwise forward to the tool."""
        self.setFocus()
        button = event.button()
        panning = button == Qt.MouseButton.MiddleButton or (
            button == Qt.MouseButton.LeftButton and self._space_held
        )
        if panning:
            self._pan_anchor = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if button != Qt.MouseButton.LeftButton or self._tool is None:
            return
        self._editors.commit()
        self._tool.press(self._pointer(event))
        self.refresh()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Pan, drag with the tool, or update the hover cursor."""
        pointer = self._pointer(event)
        self.cursor_moved.emit(pointer.pos)
        if self._pan_anchor is not None:
            delta = event.position() - self._pan_anchor
            self._pan_anchor = event.position()
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - round(delta.x()))
            v.setValue(v.value() - round(delta.y()))
            return
        if self._tool is None:
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._tool.move(pointer)
            self.refresh()
        else:
            cursor = (
                Qt.CursorShape.OpenHandCursor
                if self._space_held
                else CURSORS[self._tool.cursor(pointer)]
            )
            self.viewport().setCursor(cursor)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish panning or the tool gesture."""
        if self._pan_anchor is not None:
            self._pan_anchor = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._tool is not None:
            self._tool.release(self._pointer(event))
            self.refresh()

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Forward double-clicks to the tool."""
        if event.button() == Qt.MouseButton.LeftButton and self._tool is not None:
            self._tool.double_click(self._pointer(event))
            self.refresh()

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Clear the status-bar position when the pointer leaves."""
        del event
        self.cursor_moved.emit(None)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Ctrl zooms at the cursor, Shift scrolls horizontally, else scroll."""
        mods = event.modifiers()
        steps = event.angleDelta().y() / 120
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self._zoom * (ZOOM_STEP**steps), event.position())
            event.accept()
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle Esc, Enter, arrow-key nudges, and Space panning."""
        key = event.key()
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if key == Qt.Key.Key_Escape:
            self._handle_escape()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._tool is None or not self._tool.confirm():
                self.enter_unhandled.emit()
            self.refresh()
            return
        if self._nudge(event):
            return
        super().keyPressEvent(event)

    @override
    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Leave Space panning mode."""
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        super().keyReleaseEvent(event)

    def _handle_escape(self) -> None:
        """Back out one level: tool work, then selection, then to Select tool."""
        if self._tool is not None and self._tool.cancel():
            self.refresh()
            return
        if len(self._session.selection):
            self._session.selection.clear()
            return
        self.escape_unhandled.emit()

    def _nudge(self, event: QKeyEvent) -> bool:
        """Move the selection with the arrow keys; return True if handled."""
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1.0
        deltas = {
            Qt.Key.Key_Left: Point(-step, 0),
            Qt.Key.Key_Right: Point(step, 0),
            Qt.Key.Key_Up: Point(0, -step),
            Qt.Key.Key_Down: Point(0, step),
        }
        delta = deltas.get(Qt.Key(event.key()))
        if delta is None:
            return False
        return self._session.nudge_selected(delta)

    # Layout

    @override
    def changeEvent(self, event: QEvent) -> None:
        """Follow the theme: recolor the area around the image."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._backdrop = ChromeStyle(self.palette()).backdrop
            self.refresh()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep scroll ranges in sync with the viewport size."""
        super().resizeEvent(event)
        self._update_scrollbars()
        self._editors.reposition()

    @override
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Repaint and move inline editors when scrolled."""
        del dx, dy
        self._editors.reposition()
        self.refresh()

    def _update_scrollbars(self) -> None:
        """Set scroll ranges from the zoomed content size."""
        crop = self._session.document.crop
        vp = self.viewport()
        content_w = math.ceil(crop.width * self._zoom)
        content_h = math.ceil(crop.height * self._zoom)
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setRange(0, max(0, content_w - vp.width()))
        v.setRange(0, max(0, content_h - vp.height()))
        h.setPageStep(vp.width())
        v.setPageStep(vp.height())
        h.setSingleStep(24)
        v.setSingleStep(24)

    def _on_document_changed(self) -> None:
        """Re-layout after crops and repaint after any change."""
        image = self._session.document.image
        if image.cacheKey() != self._pixmap_key:
            # Rotate, flip, and resize replace the base pixels
            self._pixmap = QPixmap.fromImage(image)
            self._pixmap_key = image.cacheKey()
        self._update_scrollbars()
        self._editors.reposition()
        self.refresh()
