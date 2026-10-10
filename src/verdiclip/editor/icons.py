"""Vector-drawn toolbar icons, tinted to the current palette."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from verdiclip.editor.session import ToolId

SIZE: Final = 48  # Drawn at 2x and scaled down for crisp high-DPI icons


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
        """Return the app icon: a white "V" on a green rounded square."""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 64, size / 64)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(46, 160, 67))
        painter.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
        painter.setPen(
            QPen(
                Qt.GlobalColor.white,
                8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPolyline(
            QPolygonF([QPointF(17, 18), QPointF(32, 47), QPointF(47, 18)])
        )
        painter.end()
        return QIcon(pixmap)

    # Internals

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
            font = QFont("Segoe UI", 1)
            font.setPixelSize(36)
            font.setBold(True)
            p.setFont(font)
            p.drawText(
                QRectF(0, 0, SIZE, SIZE), int(Qt.AlignmentFlag.AlignCenter.value), "T"
            )
        elif tool is ToolId.COUNTER:
            p.setBrush(self._ink)
            p.drawEllipse(QRectF(8, 8, 32, 32))
            font = QFont("Segoe UI", 1)
            font.setPixelSize(22)
            font.setBold(True)
            p.setFont(font)
            p.setPen(self._paper)
            p.drawText(
                QRectF(8, 8, 32, 32), int(Qt.AlignmentFlag.AlignCenter.value), "1"
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

    def _draw_gear(self, p: QPainter) -> None:
        """A cog wheel."""
        p.save()
        p.translate(24, 24)
        p.setPen(self._pen(6))
        for _ in range(8):
            p.drawLine(QPointF(0, -13), QPointF(0, -18))
            p.rotate(45)
        p.restore()
        p.drawEllipse(QPointF(24, 24), 12, 12)
        p.drawEllipse(QPointF(24, 24), 4.5, 4.5)
