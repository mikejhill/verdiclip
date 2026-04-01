"""Tests for editor drawing tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.editor.tools.ellipse import EllipseTool
from verdiclip.editor.tools.freehand import FreehandTool
from verdiclip.editor.tools.highlight import HighlightTool
from verdiclip.editor.tools.line import LineTool
from verdiclip.editor.tools.number import NumberTool
from verdiclip.editor.tools.rectangle import RectangleTool


def _make_mouse_event(button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    """Create a mock QMouseEvent with the given button and modifiers."""
    event = MagicMock()
    event.button.return_value = button
    event.modifiers.return_value = modifiers
    return event


def _simulate_draw(tool, scene, view, start: QPointF, end: QPointF):
    """Activate a tool and simulate a full press-move-release sequence."""
    tool.activate(scene, view)
    event = _make_mouse_event()
    tool.mouse_press(start, event)
    tool.mouse_move(end, event)
    tool.mouse_release(end, event)


class TestRectangleTool:
    def test_rectangle_tool_creates_rect(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 1


class TestEllipseTool:
    def test_ellipse_tool_creates_ellipse(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 1


class TestLineTool:
    def test_line_tool_creates_line(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 1


class TestFreehandTool:
    def test_freehand_tool_creates_path(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)
        # Multiple moves to build up a path with sufficient size
        for i in range(1, 20):
            tool.mouse_move(QPointF(i * 5, i * 5), event)
        tool.mouse_release(QPointF(95, 95), event)

        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 1


class TestHighlightTool:
    def test_highlight_tool_creates_rect(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 1


class TestNumberTool:
    def test_number_tool_increments(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_release(QPointF(50, 50), event)
        assert tool._counter == 1

        tool.mouse_press(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)
        assert tool._counter == 2

        # Each click creates a group (ellipse + text) on the scene
        assert len(scene.items()) > 0
