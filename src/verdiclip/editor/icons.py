"""Vector-drawn toolbar icons, tinted to the current palette."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from verdiclip.editor.session import ToolId

SIZE: Final = 48  # Drawn at 2x and scaled down for crisp high-DPI icons
GEAR_TEETH: Final = 6
GEAR_TIP_RADIUS: Final = 20.0
GEAR_ROOT_RADIUS: Final = 14.5
GEAR_TIP_HALF_ANGLE: Final = 11.0  # Degrees either side of a tooth's center
GEAR_ROOT_HALF_ANGLE: Final = 19.0


class IconFactory:
    """Draw simple, consistent icons for tools and actions."""

    def __init__(self, ink: QColor) -> None:
        self._ink = ink
        # Contrasting color for glyph details drawn on top of ink
        light_ink = ink.lightness() > 128  # noqa: PLR2004 — midpoint of 0-255
        self._paper = QColor(30, 30, 30) if light_ink else QColor(255, 255, 255)

    def tool(self, tool: ToolId) -> QIcon:
        """Return the icon for ``tool``."""
        pixmap, painter = self._canvas()
        try:
            self._draw_tool(painter, tool)
        finally:
            painter.end()
        return QIcon(pixmap)

    def action(self, name: str) -> QIcon:
        """Return the icon for a command action (copy_image, save, undo, ...)."""
        pixmap, painter = self._canvas()
        try:
            self._draw_action(painter, name)
        finally:
            painter.end()
        return QIcon(pixmap)

    def brand(self, size: int = 64) -> QIcon:
        """Return the capture-corner V, keeping its SVG for every display scale."""
        source = Path(__file__).parents[1] / "assets" / "brand.svg"
        icon = QIcon(str(source))
        # Prime the requested raster size; Qt retains the vector for other sizes.
        icon.addPixmap(icon.pixmap(size, size))
        return icon

    def style(self, name: str) -> QIcon:
        """Return the icon for a style-bar toggle (align_left, shadow, ...)."""
        pixmap, painter = self._canvas()
        try:
            self._draw_style(painter, name)
        finally:
            painter.end()
        return QIcon(pixmap)

    # Internals

    def _draw_style(self, p: QPainter, name: str) -> None:
        """Draw the glyph for style toggle ``name``."""
        lengths = (32, 22, 32, 18)
        if name in {"align_left", "align_center", "align_right"}:
            share = {"align_left": 0.0, "align_center": 0.5, "align_right": 1.0}[name]
            for row, length in enumerate(lengths):
                x = 8 + (32 - length) * share
                y = 12 + 8 * row
                p.drawLine(QPointF(x, y), QPointF(x + length, y))
        elif name in {"align_top", "align_middle", "align_bottom"}:
            p.setPen(self._pen(3))
            p.drawRect(QRectF(6, 6, 36, 36))
            p.setPen(self._pen(4))
            top = {"align_top": 14.0, "align_middle": 20.5, "align_bottom": 27.0}[name]
            for row in range(2):
                p.drawLine(QPointF(14, top + 7 * row), QPointF(34, top + 7 * row))
        elif name == "shadow":
            shade = QColor(self._ink)
            shade.setAlpha(110)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shade)
            p.drawRect(QRectF(14, 16, 28, 24))
            p.setPen(self._pen(3))
            p.setBrush(self._paper)
            p.drawRect(QRectF(7, 8, 28, 24))
        else:  # auto_fit: a "T" hugged by corner brackets
            self._draw_t(p, QRectF(16, 15, 16, 19))
            p.setPen(self._pen(3))
            for x, y, dx, dy in (
                (7, 7, 1, 1),
                (41, 7, -1, 1),
                (7, 41, 1, -1),
                (41, 41, -1, -1),
            ):
                p.drawPolyline(
                    QPolygonF(
                        [QPointF(x, y + 9 * dy), QPointF(x, y), QPointF(x + 9 * dx, y)]
                    )
                )

    def _canvas(self) -> tuple[QPixmap, QPainter]:
        """Return a transparent pixmap and a painter set up with the ink pen."""
        pixmap = QPixmap(SIZE, SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen(4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        return pixmap, painter

    def _pen(self, width: float) -> QPen:
        """Return an ink pen."""
        return QPen(
            self._ink,
            width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

    def _draw_t(self, p: QPainter, bounds: QRectF) -> None:
        """Draw a fixed toolbar symbol without resolving any text font."""
        x, y, width, height = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._ink)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(x, y),
                    QPointF(x + width, y),
                    QPointF(x + width, y + height / 4),
                    QPointF(x + width * 5 / 8, y + height / 4),
                    QPointF(x + width * 5 / 8, y + height),
                    QPointF(x + width * 3 / 8, y + height),
                    QPointF(x + width * 3 / 8, y + height / 4),
                    QPointF(x, y + height / 4),
                ]
            )
        )

    def _draw_tool(self, p: QPainter, tool: ToolId) -> None:  # noqa: C901, PLR0912 — one glyph per tool
        """Draw the glyph for ``tool``."""
        if tool is ToolId.SELECT:
            p.setBrush(self._ink)
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(14, 8),
                        QPointF(14, 38),
                        QPointF(22, 30),
                        QPointF(28, 42),
                        QPointF(33, 40),
                        QPointF(27, 28),
                        QPointF(37, 28),
                    ]
                )
            )
        elif tool is ToolId.CROP:
            p.drawPolyline(
                QPolygonF([QPointF(14, 6), QPointF(14, 34), QPointF(42, 34)])
            )
            p.drawPolyline(
                QPolygonF([QPointF(6, 14), QPointF(34, 14), QPointF(34, 42)])
            )
        elif tool is ToolId.RECTANGLE:
            p.drawRect(QRectF(8, 12, 32, 24))
        elif tool is ToolId.ELLIPSE:
            p.drawEllipse(QRectF(7, 11, 34, 26))
        elif tool is ToolId.LINE:
            p.drawLine(QPointF(9, 39), QPointF(39, 9))
        elif tool is ToolId.ARROW:
            p.drawLine(QPointF(9, 39), QPointF(33, 15))
            p.setBrush(self._ink)
            p.drawPolygon(QPolygonF([QPointF(40, 8), QPointF(24, 14), QPointF(34, 24)]))
        elif tool is ToolId.TEXT:
            self._draw_t(p, QRectF(12, 11, 24, 27))
        elif tool is ToolId.COUNTER:
            p.setBrush(self._ink)
            p.drawEllipse(QRectF(8, 8, 32, 32))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._paper)
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(17, 20),
                        QPointF(22, 16),
                        QPointF(27, 16),
                        QPointF(27, 30),
                        QPointF(31, 30),
                        QPointF(31, 34),
                        QPointF(18, 34),
                        QPointF(18, 30),
                        QPointF(23, 30),
                        QPointF(23, 21),
                        QPointF(20, 23),
                    ]
                )
            )
        elif tool is ToolId.HIGHLIGHT:
            p.fillRect(QRectF(6, 18, 36, 14), QColor(255, 235, 59, 200))
            p.drawLine(QPointF(10, 25), QPointF(38, 25))
        elif tool is ToolId.OBFUSCATE:
            p.setPen(Qt.PenStyle.NoPen)
            for row in range(4):
                for col in range(4):
                    shade = 90 + 40 * ((row + col) % 3)
                    p.fillRect(
                        QRectF(8 + col * 8, 8 + row * 8, 8, 8),
                        QColor(shade, shade, shade),
                    )
        else:
            path = QPainterPath(QPointF(8, 34))
            path.cubicTo(QPointF(16, 6), QPointF(24, 44), QPointF(40, 12))
            p.drawPath(path)

    def _draw_action(self, p: QPainter, name: str) -> None:
        """Draw the glyph for action ``name``."""
        drawers = {
            "copy_image": self._draw_copy,
            "save": self._draw_save,
            "undo": lambda q: self._draw_curved_arrow(q, mirror=False),
            "redo": lambda q: self._draw_curved_arrow(q, mirror=True),
            "settings": self._draw_gear,
            "image": self._draw_rotate_frame,
        }
        drawers[name](p)

    def _draw_copy(self, p: QPainter) -> None:
        """Two stacked pages; the back one shows only its exposed edges."""
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(16, 10),
                    QPointF(16, 6),
                    QPointF(40, 6),
                    QPointF(40, 30),
                    QPointF(36, 30),
                ]
            )
        )
        p.drawRoundedRect(QRectF(8, 14, 26, 28), 3, 3)

    def _draw_save(self, p: QPainter) -> None:
        """A floppy disk."""
        body = QPainterPath()
        body.moveTo(8, 8)
        body.lineTo(33, 8)
        body.lineTo(40, 15)
        body.lineTo(40, 40)
        body.lineTo(8, 40)
        body.closeSubpath()
        p.drawPath(body)
        p.drawRect(QRectF(15, 8, 15, 10))
        p.drawRect(QRectF(14, 27, 20, 13))

    def _draw_curved_arrow(self, p: QPainter, *, mirror: bool) -> None:
        """A hooked arrow pointing left (undo) or right (redo)."""
        if mirror:
            p.translate(SIZE, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(12, 20))
        path.cubicTo(QPointF(26, 8), QPointF(42, 16), QPointF(38, 30))
        path.cubicTo(QPointF(35, 38), QPointF(28, 40), QPointF(22, 40))
        p.drawPath(path)
        p.setBrush(self._ink)
        p.drawPolygon(QPolygonF([QPointF(6, 20), QPointF(18, 11), QPointF(18, 29)]))

    def _draw_rotate_frame(self, p: QPainter) -> None:
        """A picture frame with a turning arrow: rotate, flip, resize."""
        p.drawRoundedRect(QRectF(7, 17, 24, 24), 3, 3)
        path = QPainterPath(QPointF(20, 9))
        path.cubicTo(QPointF(32, 4), QPointF(42, 12), QPointF(41, 26))
        p.drawPath(path)
        p.setBrush(self._ink)
        p.drawPolygon(QPolygonF([QPointF(35, 24), QPointF(47, 24), QPointF(41, 33)]))

    def _draw_gear(self, p: QPainter) -> None:
        """A cog: one outline with six broad, flat-topped teeth and a hub hole.

        Broad teeth joined into a single outline read as a cog; thin spokes
        around a ring read as a sun.
        """
        outline = QPolygonF()
        for tooth in range(GEAR_TEETH):
            center = tooth * 360 / GEAR_TEETH
            for angle, radius in (
                (center - GEAR_ROOT_HALF_ANGLE, GEAR_ROOT_RADIUS),
                (center - GEAR_TIP_HALF_ANGLE, GEAR_TIP_RADIUS),
                (center + GEAR_TIP_HALF_ANGLE, GEAR_TIP_RADIUS),
                (center + GEAR_ROOT_HALF_ANGLE, GEAR_ROOT_RADIUS),
            ):
                outline.append(_polar(angle, radius))
        p.drawPolygon(outline)
        p.drawEllipse(QPointF(SIZE / 2, SIZE / 2), 6, 6)


def _polar(degrees: float, radius: float) -> QPointF:
    """Return the icon point ``radius`` from the center at ``degrees``."""
    rad = math.radians(degrees)
    return QPointF(SIZE / 2 + radius * math.sin(rad), SIZE / 2 - radius * math.cos(rad))
