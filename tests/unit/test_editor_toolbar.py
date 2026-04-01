"""Tests for verdiclip.editor.toolbar — ToolType and EditorToolbar."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import Qt

from verdiclip.editor.toolbar import EditorToolbar, ToolType

# ---------------------------------------------------------------------------
# ToolType enum
# ---------------------------------------------------------------------------

_EXPECTED_TOOL_TYPES = [
    "SELECT", "CROP", "RECTANGLE", "ELLIPSE", "LINE", "ARROW",
    "TEXT", "NUMBER", "HIGHLIGHT", "OBFUSCATE", "FREEHAND",
]


class TestToolTypeEnum:
    def test_has_expected_tool_types(self, qapp) -> None:
        names = [t.name for t in ToolType]
        assert names == _EXPECTED_TOOL_TYPES, (
            f"Expected tool types {_EXPECTED_TOOL_TYPES}, got {names}"
        )


# ---------------------------------------------------------------------------
# EditorToolbar
# ---------------------------------------------------------------------------


class TestEditorToolbarActions:
    def test_creates_all_tool_actions(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert len(toolbar._actions) == len(ToolType), (
            f"Expected {len(ToolType)} actions, got {len(toolbar._actions)}"
        )
        for tool_type in ToolType:
            assert tool_type in toolbar._actions, (
                f"Expected {tool_type} in toolbar actions"
            )


class TestEditorToolbarCurrentTool:
    def test_defaults_to_select(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.current_tool == ToolType.SELECT, (
            f"Expected default tool SELECT, got {toolbar.current_tool}"
        )


class TestEditorToolbarSetTool:
    def test_changes_checked_action(self, qapp) -> None:
        toolbar = EditorToolbar()
        toolbar.set_tool(ToolType.RECTANGLE)
        assert toolbar._actions[ToolType.RECTANGLE].isChecked(), (
            "Expected RECTANGLE action to be checked after set_tool"
        )

    def test_emits_tool_changed_signal(self, qapp) -> None:
        toolbar = EditorToolbar()
        handler = MagicMock()
        toolbar.tool_changed.connect(handler)
        toolbar.set_tool(ToolType.ARROW)
        handler.assert_called_once_with(ToolType.ARROW)


class TestEditorToolbarOrientation:
    def test_orientation_is_vertical(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.orientation() == Qt.Orientation.Vertical, (
            f"Expected Vertical orientation, got {toolbar.orientation()}"
        )

    def test_not_movable(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.isMovable() is False, (
            f"Expected toolbar not movable, got {toolbar.isMovable()}"
        )

    def test_not_floatable(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.isFloatable() is False, (
            f"Expected toolbar not floatable, got {toolbar.isFloatable()}"
        )


# ---------------------------------------------------------------------------
# Toolbar icons — every action has an icon, no text, and a tooltip with shortcut
# ---------------------------------------------------------------------------

_TOOL_SHORTCUTS: dict[str, str] = {
    "SELECT": "V", "CROP": "C", "RECTANGLE": "R", "ELLIPSE": "E",
    "LINE": "L", "ARROW": "A", "TEXT": "T", "NUMBER": "N",
    "HIGHLIGHT": "H", "OBFUSCATE": "O", "FREEHAND": "F",
}


class TestToolbarIcons:
    def test_each_action_has_non_null_icon(self, qapp) -> None:
        toolbar = EditorToolbar()
        for tool_type, action in toolbar._actions.items():
            assert not action.icon().isNull(), (
                f"Expected action for {tool_type.name} to have a non-null icon"
            )

    def test_each_action_has_empty_text(self, qapp) -> None:
        toolbar = EditorToolbar()
        for tool_type, action in toolbar._actions.items():
            assert action.text() == "", (
                f"Expected action text for {tool_type.name} to be empty (icons only), "
                f"got '{action.text()}'"
            )

    def test_each_action_tooltip_contains_shortcut(self, qapp) -> None:
        toolbar = EditorToolbar()
        for tool_type, action in toolbar._actions.items():
            shortcut = _TOOL_SHORTCUTS[tool_type.name]
            tooltip = action.toolTip()
            assert shortcut in tooltip, (
                f"Expected tooltip for {tool_type.name} to contain shortcut '{shortcut}', "
                f"got '{tooltip}'"
            )
