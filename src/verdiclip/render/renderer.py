"""The single place annotations are painted, for both the canvas and exports."""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from collections.abc import Iterable
from itertools import pairwise
from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QTextLayout,
    QTextOption,
)

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    HighlightShape,
    LabeledBox,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.document import Document
from verdiclip.document.style import (
    SHADOW_BLUR,
    SHADOW_OFFSET,
    Color,
    HAlign,
    Style,
    VAlign,
)
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)

TEXT_PADDING: Final = 4.0
# Concentric strokes, widest first, that fade the shadow's edge
_SHADOW_SPREADS: Final = (2 * SHADOW_BLUR, 5.0, 2.0)
_SHADOW_ALPHA: Final = 28
H_ALIGN: Final = {
    HAlign.LEFT: Qt.AlignmentFlag.AlignLeft,
    HAlign.CENTER: Qt.AlignmentFlag.AlignHCenter,
    HAlign.RIGHT: Qt.AlignmentFlag.AlignRight,
}
# Where wrapped text sits in leftover height: 0 top, 0.5 middle, 1 bottom
V_SHARE: Final = {VAlign.TOP: 0.0, VAlign.MIDDLE: 0.5, VAlign.BOTTOM: 1.0}
_V_ALIGN: Final = {
    VAlign.TOP: Qt.AlignmentFlag.AlignTop,
    VAlign.MIDDLE: Qt.AlignmentFlag.AlignVCenter,
    VAlign.BOTTOM: Qt.AlignmentFlag.AlignBottom,
}
_PIXELATE_CACHE_SIZE: Final = 64
# Fraction trimmed from each side so ellipse labels fit the inscribed rectangle
ELLIPSE_LABEL_INSET: Final = (1 - 1 / math.sqrt(2)) / 2


class QtConvert:
    """Convert model value objects to Qt types."""

    @staticmethod
    def color(color: Color) -> QColor:
        """Return ``color`` as a QColor."""
        return QColor(color.red, color.green, color.blue, color.alpha)

    @staticmethod
    def point(point: Point) -> QPointF:
        """Return ``point`` as a QPointF."""
        return QPointF(point.x, point.y)

    @staticmethod
    def rect(rect: Rect) -> QRectF:
        """Return ``rect`` as a QRectF."""
        return QRectF(rect.x, rect.y, rect.width, rect.height)

    @staticmethod
    def to_point(point: QPointF) -> Point:
        """Return a Qt point as a model Point."""
        return Point(point.x(), point.y())

    @staticmethod
    def font(style: Style) -> QFont:
        """Return the QFont described by ``style`` (size in image pixels)."""
        font = QFont(style.font_family)
        font.setPixelSize(style.font_size)
        font.setBold(style.bold)
        font.setItalic(style.italic)
        return font


class Renderer:
    """Paint annotations with QPainter in image coordinates."""

    def __init__(self) -> None:
        self._pixelated: OrderedDict[tuple[int, ...], QImage] = OrderedDict()

    # Public API

    def flatten(self, document: Document) -> QImage:
        """Return the cropped image with every annotation drawn onto it."""
        crop = document.crop.rounded()
        result = QImage(
            int(crop.width), int(crop.height), QImage.Format.Format_ARGB32_Premultiplied
        )
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(-crop.x, -crop.y)
            painter.drawImage(QPointF(0, 0), document.image)
            self.paint_all(painter, document.image, document.visible_annotations)
        finally:
            painter.end()
        return result

    def paint_all(
        self, painter: QPainter, image: QImage, annotations: Iterable[Annotation]
    ) -> None:
        """Paint ``annotations`` bottom to top."""
        for annotation in annotations:
            self.paint(painter, image, annotation)

    def paint(self, painter: QPainter, image: QImage, annotation: Annotation) -> None:
        """Paint one annotation; ``image`` is the base image for obfuscation."""
        painter.save()
        try:
            self._dispatch(painter, image, annotation)
        finally:
            painter.restore()

    @staticmethod
    def text_inset(style: Style) -> float:
        """Return the space between a text box's edge and its text."""
        outline = 0.0 if style.outline.is_transparent else style.width / 2
        return TEXT_PADDING + outline

    @staticmethod
    def measure_text(text: str, style: Style) -> tuple[float, float]:
        """Return the padded width and height of ``text`` drawn in ``style``."""
        metrics = QFontMetricsF(QtConvert.font(style))
        lines = text.split("\n") or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = metrics.lineSpacing() * len(lines)
        inset = Renderer.text_inset(style)
        return width + 2 * inset, height + 2 * inset

    @staticmethod
    def fit(annotation: Annotation) -> Annotation:
        """Return ``annotation`` resized to its text when it is auto-fit text."""
        if isinstance(annotation, TextNote) and annotation.auto_fit:
            return annotation.fitted(
                *Renderer.measure_text(annotation.text, annotation.style)
            )
        return annotation

    @staticmethod
    def text_flags(style: Style) -> int:
        """Return the Qt alignment flags for text drawn in ``style``."""
        return int((H_ALIGN[style.h_align] | _V_ALIGN[style.v_align]).value)

    # Dispatch

    def _dispatch(
        self, painter: QPainter, image: QImage, annotation: Annotation
    ) -> None:
        """Route to the painter for the annotation's type."""
        match annotation:
            case RectangleShape():
                self._paint_rectangle(painter, annotation)
            case EllipseShape():
                self._paint_ellipse(painter, annotation)
            case HighlightShape():
                self._paint_highlight(painter, annotation)
            case ObfuscateShape():
                self._paint_obfuscate(painter, image, annotation)
            case ArrowShape():
                self._paint_arrow(painter, annotation)
            case LineShape():
                self._paint_line(painter, annotation)
            case FreehandShape():
                self._paint_freehand(painter, annotation)
            case TextNote():
                self._paint_text(painter, annotation)
            case CounterMarker():
                self._paint_counter(painter, annotation)
            case _:
                logger.warning("No painter for %s", type(annotation).__name__)

    # Shapes

    @staticmethod
    def _stroke_pen(
        style: Style, *, cap: Qt.PenCapStyle, join: Qt.PenJoinStyle
    ) -> QPen:
        """Return a solid pen for ``style``."""
        pen = QPen(QtConvert.color(style.stroke), style.width)
        pen.setCapStyle(cap)
        pen.setJoinStyle(join)
        return pen

    def _paint_rectangle(self, painter: QPainter, shape: RectangleShape) -> None:
        """Paint a rectangle with sharp (miter) corners."""
        pen = self._stroke_pen(
            shape.style, cap=Qt.PenCapStyle.SquareCap, join=Qt.PenJoinStyle.MiterJoin
        )
        if shape.style.shadow:
            filled = not shape.style.fill.is_transparent
            path = self._box_path(shape.rect, pen, filled=filled, outlined=True)
            self._paint_shadow(painter, path)
        painter.setPen(pen)
        painter.setBrush(QBrush(QtConvert.color(shape.style.fill)))
        painter.drawRect(QtConvert.rect(shape.rect))
        self._paint_label(painter, shape)

    def _paint_ellipse(self, painter: QPainter, shape: EllipseShape) -> None:
        """Paint an ellipse."""
        painter.setPen(
            self._stroke_pen(
                shape.style, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin
            )
        )
        painter.setBrush(QBrush(QtConvert.color(shape.style.fill)))
        painter.drawEllipse(QtConvert.rect(shape.rect))
        # Keep ellipse labels inside the curve: use the inscribed rectangle
        self._paint_label(painter, shape, inset=ELLIPSE_LABEL_INSET)

    @staticmethod
    def _box_path(
        rect: Rect, pen: QPen, *, filled: bool, outlined: bool
    ) -> QPainterPath:
        """Return the area a box covers: its fill, its outline, or both."""
        area = QPainterPath()
        area.addRect(QtConvert.rect(rect))
        outline = QPainterPathStroker(pen).createStroke(area)
        if filled and outlined:
            return area.united(outline)
        return area if filled else outline

    @staticmethod
    def _paint_shadow(painter: QPainter, path: QPainterPath) -> None:
        """Paint a soft drop shadow of ``path`` below and to the right."""
        painter.save()
        painter.translate(SHADOW_OFFSET, SHADOW_OFFSET)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        color = QColor(0, 0, 0, _SHADOW_ALPHA)
        for spread in _SHADOW_SPREADS:
            pen = QPen(color, spread)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
        painter.fillPath(path, color)
        painter.restore()

    @staticmethod
    def _paint_highlight(painter: QPainter, shape: HighlightShape) -> None:
        """Multiply a translucent color onto the image so text stays readable."""
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.fillRect(QtConvert.rect(shape.rect), QtConvert.color(shape.style.fill))

    def _paint_obfuscate(
        self, painter: QPainter, image: QImage, shape: ObfuscateShape
    ) -> None:
        """Paint the image beneath ``shape`` pixelated."""
        area = shape.rect.rounded().intersected(
            Rect(0, 0, image.width(), image.height())
        )
        if area.is_empty:
            return
        pixelated = self._pixelate(image, area, shape.block_size)
        painter.drawImage(QPointF(area.x, area.y), pixelated)

    def _paint_line(self, painter: QPainter, shape: LineShape) -> None:
        """Paint a straight line with round caps."""
        painter.setPen(
            self._stroke_pen(
                shape.style, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin
            )
        )
        painter.drawLine(QtConvert.point(shape.start), QtConvert.point(shape.end))

    def _paint_arrow(self, painter: QPainter, shape: ArrowShape) -> None:
        """Paint the shaft up to the head base, then a filled pointed head."""
        if shape.start.distance_to(shape.end) > shape.head_length:
            painter.setPen(
                self._stroke_pen(
                    shape.style,
                    cap=Qt.PenCapStyle.FlatCap,
                    join=Qt.PenJoinStyle.MiterJoin,
                )
            )
            painter.drawLine(
                QtConvert.point(shape.start), QtConvert.point(shape.shaft_end())
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QtConvert.color(shape.style.stroke)))
        painter.drawPolygon(
            QPolygonF([QtConvert.point(p) for p in shape.head_polygon()])
        )

    def _paint_freehand(self, painter: QPainter, shape: FreehandShape) -> None:
        """Paint a smoothed path through the stroke points."""
        painter.setPen(
            self._stroke_pen(
                shape.style, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(shape.points) == 1:
            painter.drawPoint(QtConvert.point(shape.points[0]))
            return
        painter.drawPath(self._smooth_path(shape.points))

    @staticmethod
    def _smooth_path(points: tuple[Point, ...]) -> QPainterPath:
        """Return a quadratic path through the midpoints of ``points``."""
        path = QPainterPath(QtConvert.point(points[0]))
        for current, following in pairwise(points[1:]):
            mid = Point((current.x + following.x) / 2, (current.y + following.y) / 2)
            path.quadTo(QtConvert.point(current), QtConvert.point(mid))
        path.lineTo(QtConvert.point(points[-1]))
        return path

    # Text

    @staticmethod
    def label_area(box: LabeledBox, *, inset: float = 0.0) -> Rect:
        """Return where a box's label is laid out (inside stroke and padding)."""
        margin = box.style.width / 2 + TEXT_PADDING
        inner = box.rect.adjusted(-margin)
        dx, dy = inner.width * inset, inner.height * inset
        return Rect(
            inner.x + dx,
            inner.y + dy,
            max(0.0, inner.width - 2 * dx),
            max(0.0, inner.height - 2 * dy),
        )

    def _paint_label(
        self, painter: QPainter, box: LabeledBox, *, inset: float = 0.0
    ) -> None:
        """Draw a box's text centered and word-wrapped, clipped to the box."""
        if not box.text:
            return
        area = QtConvert.rect(self.label_area(box, inset=inset))
        painter.save()
        painter.setClipRect(QtConvert.rect(box.rect), Qt.ClipOperation.IntersectClip)
        painter.setFont(QtConvert.font(box.style))
        painter.setPen(QtConvert.color(box.style.stroke))
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        painter.drawText(area, int(flags.value), box.text)
        painter.restore()

    def _paint_text(self, painter: QPainter, note: TextNote) -> None:
        """Paint the text box shadow, background, outline, and text."""
        style = note.style
        pen = QPen(QtConvert.color(style.outline), style.width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        filled = not style.fill.is_transparent
        if style.shadow and (filled or note.has_outline):
            path = self._box_path(
                note.rect, pen, filled=filled, outlined=note.has_outline
            )
            self._paint_shadow(painter, path)
        elif style.shadow:
            # A bare caption casts the shadow of its letters
            shadow = QColor(0, 0, 0, 3 * _SHADOW_ALPHA)
            self._draw_text(painter, note, shadow, offset=2.0)
        box = QtConvert.rect(note.rect)
        if filled:
            painter.fillRect(box, QtConvert.color(style.fill))
        if note.has_outline:
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box)
        self._draw_text(painter, note, QtConvert.color(style.stroke))

    def _draw_text(
        self, painter: QPainter, note: TextNote, color: QColor, offset: float = 0.0
    ) -> None:
        """Draw a note's text; fixed-size boxes wrap and clip it to the box."""
        inset = self.text_inset(note.style)
        area = note.rect.adjusted(-inset).translated(Point(offset, offset))
        painter.save()
        if not note.auto_fit:
            painter.setClipRect(
                QtConvert.rect(note.rect), Qt.ClipOperation.IntersectClip
            )
        font = QtConvert.font(note.style)
        painter.setFont(font)
        painter.setPen(color)
        if note.auto_fit:
            flags = self.text_flags(note.style)
            painter.drawText(QtConvert.rect(area), flags, note.text)
        else:
            self._draw_wrapped(painter, note, area, font)
        painter.restore()

    @staticmethod
    def _draw_wrapped(
        painter: QPainter, note: TextNote, area: Rect, font: QFont
    ) -> None:
        """Draw text wrapped like the inline editor: at words, else anywhere."""
        option = QTextOption(H_ALIGN[note.style.h_align])
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # QTextLayout breaks lines only at the Unicode line separator
        layout = QTextLayout(note.text.replace("\n", "\u2028"), font)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while (line := layout.createLine()).isValid():
            line.setLineWidth(area.width)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        share = V_SHARE[note.style.v_align]
        top = area.y + (area.height - height) * share
        layout.draw(painter, QPointF(area.x, top))

    @staticmethod
    def _paint_counter(painter: QPainter, marker: CounterMarker) -> None:
        """Paint a filled circle with its label centered."""
        circle = QtConvert.rect(marker.rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QtConvert.color(marker.style.fill)))
        painter.drawEllipse(circle)
        font = QtConvert.font(marker.style)
        font.setBold(True)
        size = marker.radius * (1.1 if len(marker.label) <= 2 else 0.8)  # noqa: PLR2004
        font.setPixelSize(max(6, round(size)))
        painter.setFont(font)
        painter.setPen(QtConvert.color(marker.style.stroke))
        painter.drawText(circle, int(Qt.AlignmentFlag.AlignCenter.value), marker.label)

    # Pixelation

    def _pixelate(self, image: QImage, area: Rect, block: int) -> QImage:
        """Return ``area`` of ``image`` reduced to ``block``-sized cells (cached)."""
        key = (
            image.cacheKey(),
            int(area.x),
            int(area.y),
            int(area.width),
            int(area.height),
            block,
        )
        cached = self._pixelated.get(key)
        if cached is not None:
            self._pixelated.move_to_end(key)
            return cached
        region = image.copy(int(area.x), int(area.y), int(area.width), int(area.height))
        small = region.scaled(
            max(1, region.width() // block),
            max(1, region.height() // block),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = small.scaled(
            region.width(),
            region.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._pixelated[key] = result
        if len(self._pixelated) > _PIXELATE_CACHE_SIZE:
            self._pixelated.popitem(last=False)
        return result
