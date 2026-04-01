"""Editor toolbar with tool buttons."""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)
from PySide6.QtWidgets import QToolBar, QWidget

logger = logging.getLogger(__name__)


class ToolType(Enum):
    """Available editor tools."""
    SELECT = auto()
    CROP = auto()
    RECTANGLE = auto()
    ELLIPSE = auto()
    LINE = auto()
    ARROW = auto()
    TEXT = auto()
    NUMBER = auto()
    HIGHLIGHT = auto()
    OBFUSCATE = auto()
    FREEHAND = auto()


_TOOL_CONFIG: dict[ToolType, tuple[str, str, str]] = {
    ToolType.SELECT:    ("Select",      "V", "Select and move items"),
    ToolType.CROP:      ("Crop",        "C", "Crop the image"),
    ToolType.RECTANGLE: ("Rectangle",   "R", "Draw a rectangle"),
    ToolType.ELLIPSE:   ("Ellipse",     "E", "Draw an ellipse"),
    ToolType.LINE:      ("Line",        "L", "Draw a line"),
    ToolType.ARROW:     ("Arrow",       "A", "Draw an arrow"),
    ToolType.TEXT:       ("Text",        "T", "Add text annotation"),
    ToolType.NUMBER:    ("Number",      "N", "Add numbered marker"),
    ToolType.HIGHLIGHT: ("Highlight",   "H", "Highlight an area"),
    ToolType.OBFUSCATE: ("Obfuscate",   "O", "Obfuscate/pixelate an area"),
    ToolType.FREEHAND:  ("Freehand",    "F", "Draw freehand"),
}


def _create_tool_icon(tool_type: ToolType) -> QIcon:
    """Create a simple programmatic icon for a tool type."""
    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(220, 220, 220), 2)
    p.setPen(pen)

    if tool_type == ToolType.SELECT:
        # Arrow cursor
        points = QPolygon([
            _qpoint(4, 2), _qpoint(4, 18), _qpoint(9, 14),
            _qpoint(14, 20), _qpoint(16, 18), _qpoint(11, 12), _qpoint(16, 10),
        ])
        p.setBrush(QColor(220, 220, 220))
        p.drawPolygon(points)
    elif tool_type == ToolType.CROP:
        # Crop marks (two L-shapes)
        p.drawLine(6, 2, 6, 18)
        p.drawLine(6, 18, 22, 18)
        p.drawLine(18, 22, 18, 6)
        p.drawLine(18, 6, 2, 6)
    elif tool_type == ToolType.RECTANGLE:
        p.drawRect(3, 5, 18, 14)
    elif tool_type == ToolType.ELLIPSE:
        p.drawEllipse(2, 4, 20, 16)
    elif tool_type == ToolType.LINE:
        p.drawLine(3, 20, 21, 4)
    elif tool_type == ToolType.ARROW:
        p.drawLine(3, 20, 20, 4)
        # Arrowhead
        points = QPolygon([_qpoint(20, 4), _qpoint(14, 6), _qpoint(17, 10)])
        p.setBrush(QColor(220, 220, 220))
        p.drawPolygon(points)
    elif tool_type == ToolType.TEXT:
        font = QFont("Segoe UI", 16, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "A")
    elif tool_type == ToolType.NUMBER:
        p.setBrush(QColor(220, 50, 50))
        p.drawEllipse(2, 2, 20, 20)
        font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255))
        p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "1")
    elif tool_type == ToolType.HIGHLIGHT:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 0, 120))
        p.drawRect(2, 7, 20, 10)
    elif tool_type == ToolType.OBFUSCATE:
        # Grid pattern (pixelation)
        p.setPen(Qt.PenStyle.NoPen)
        for row in range(4):
            for col in range(4):
                shade = 100 + ((row + col) % 3) * 60
                p.setBrush(QColor(shade, shade, shade))
                p.drawRect(2 + col * 5, 2 + row * 5, 5, 5)
    elif tool_type == ToolType.FREEHAND:
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.moveTo(3, 16)
        path.cubicTo(6, 4, 12, 20, 21, 8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    p.end()
    return QIcon(pixmap)


def _qpoint(x: int, y: int):
    """Helper to create a QPoint for QPolygon."""
    from PySide6.QtCore import QPoint
    return QPoint(x, y)


class EditorToolbar(QToolBar):
    """Vertical toolbar with tool selection buttons."""

    tool_changed = Signal(ToolType)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Tools", parent)
        self.setOrientation(Qt.Orientation.Vertical)
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(self.iconSize())

        self._action_group = QActionGroup(self)
        self._action_group.setExclusive(True)
        self._actions: dict[ToolType, QAction] = {}

        for tool_type in ToolType:
            name, shortcut, tooltip = _TOOL_CONFIG[tool_type]
            action = QAction(_create_tool_icon(tool_type), "", self)
            action.setCheckable(True)
            action.setToolTip(f"{tooltip} ({shortcut})")
            action.setShortcut(QKeySequence(shortcut))
            action.setData(tool_type)
            action.triggered.connect(lambda checked, tt=tool_type: self._on_tool_selected(tt))

            self._action_group.addAction(action)
            self._actions[tool_type] = action
            self.addAction(action)

        # Default to select tool
        self._actions[ToolType.SELECT].setChecked(True)

    def _on_tool_selected(self, tool_type: ToolType) -> None:
        logger.debug("Tool selected: %s", tool_type.name)
        self.tool_changed.emit(tool_type)

    @property
    def current_tool(self) -> ToolType:
        """Return the currently selected tool type."""
        checked = self._action_group.checkedAction()
        return checked.data() if checked else ToolType.SELECT

    def set_tool(self, tool_type: ToolType) -> None:
        """Programmatically set the active tool."""
        if tool_type in self._actions:
            self._actions[tool_type].setChecked(True)
            self.tool_changed.emit(tool_type)
