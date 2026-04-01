"""Editor toolbar with tool buttons."""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
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
            action = QAction(name, self)
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
