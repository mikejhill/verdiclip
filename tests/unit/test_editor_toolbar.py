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
    def test_has_all_values(self, qapp) -> None:
        names = [t.name for t in ToolType]
        assert names == _EXPECTED_TOOL_TYPES

    def test_count(self, qapp) -> None:
        assert len(ToolType) == 11


# ---------------------------------------------------------------------------
# EditorToolbar
# ---------------------------------------------------------------------------


class TestEditorToolbarActions:
    def test_creates_all_tool_actions(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert len(toolbar._actions) == len(ToolType)
        for tool_type in ToolType:
            assert tool_type in toolbar._actions


class TestEditorToolbarCurrentTool:
    def test_defaults_to_select(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.current_tool == ToolType.SELECT


class TestEditorToolbarSetTool:
    def test_changes_checked_action(self, qapp) -> None:
        toolbar = EditorToolbar()
        toolbar.set_tool(ToolType.RECTANGLE)
        assert toolbar._actions[ToolType.RECTANGLE].isChecked()

    def test_emits_tool_changed_signal(self, qapp) -> None:
        toolbar = EditorToolbar()
        handler = MagicMock()
        toolbar.tool_changed.connect(handler)
        toolbar.set_tool(ToolType.ARROW)
        handler.assert_called_once_with(ToolType.ARROW)


class TestEditorToolbarSignal:
    def test_signal_emits_correct_tool_type(self, qapp) -> None:
        toolbar = EditorToolbar()
        handler = MagicMock()
        toolbar.tool_changed.connect(handler)
        toolbar.set_tool(ToolType.FREEHAND)
        handler.assert_called_once_with(ToolType.FREEHAND)


class TestEditorToolbarOrientation:
    def test_orientation_is_vertical(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.orientation() == Qt.Orientation.Vertical

    def test_not_movable(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.isMovable() is False

    def test_not_floatable(self, qapp) -> None:
        toolbar = EditorToolbar()
        assert toolbar.isFloatable() is False
