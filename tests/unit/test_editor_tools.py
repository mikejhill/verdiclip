"""Tests for editor drawing tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
)

from verdiclip.editor.tools.arrow import ArrowTool
from verdiclip.editor.tools.crop import CropTool
from verdiclip.editor.tools.ellipse import EllipseTool
from verdiclip.editor.tools.freehand import FreehandTool
from verdiclip.editor.tools.highlight import HighlightTool
from verdiclip.editor.tools.line import LineTool
from verdiclip.editor.tools.number import NumberTool
from verdiclip.editor.tools.obfuscate import ObfuscateTool, ObfuscationItem
from verdiclip.editor.tools.rectangle import RectangleTool
from verdiclip.editor.tools.select import SelectTool
from verdiclip.editor.tools.text import TextTool


def _make_mouse_event(button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    """Create a mock QMouseEvent with the given button and modifiers."""
    event = MagicMock()
    event.button.return_value = button
    event.modifiers.return_value = modifiers
    return event


def _simulate_draw(
    tool, scene, view, start: QPointF, end: QPointF,
    modifiers=Qt.KeyboardModifier.NoModifier,
):
    """Activate a tool and simulate a full press-move-release sequence."""
    tool.activate(scene, view)
    press_event = _make_mouse_event()
    move_event = _make_mouse_event(modifiers=modifiers)
    release_event = _make_mouse_event()
    tool.mouse_press(start, press_event)
    tool.mouse_move(end, move_event)
    tool.mouse_release(end, release_event)


def _make_scene_with_background(width=200, height=200):
    """Create a scene with a background QGraphicsPixmapItem at zValue -1000."""
    scene = QGraphicsScene()
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(100, 150, 200))
    bg = QGraphicsPixmapItem(pixmap)
    bg.setZValue(-1000)
    scene.addItem(bg)
    return scene, bg


# ---------------------------------------------------------------------------
# BaseTool
# ---------------------------------------------------------------------------


class TestBaseTool:
    def test_activate_sets_scene_and_view(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()  # concrete subclass
        tool.activate(scene, view)

        assert tool._scene is scene, f"Expected tool._scene to be scene, got {tool._scene}"
        assert tool._view is view, f"Expected tool._view to be view, got {tool._view}"
        assert tool._is_active is True, (
            f"Expected tool._is_active to be True, got {tool._is_active}"
        )

    def test_deactivate_clears_active_flag(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        tool.activate(scene, view)
        tool.deactivate()

        assert tool._is_active is False, (
            f"Expected tool._is_active to be False, got {tool._is_active}"
        )


# ---------------------------------------------------------------------------
# RectangleTool
# ---------------------------------------------------------------------------


class TestRectangleTool:
    def test_creates_rect(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 1, f"Expected len(rects) to be 1, got {len(rects)}"

    def test_rect_dimensions(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 20), QPointF(110, 80))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        rect = rect_item.rect()
        assert abs(rect.width() - 100) < 1, (
            f"Expected abs(rect.width() - 100) < 1, got {abs(rect.width() - 100)}"
        )
        assert abs(rect.height() - 60) < 1, (
            f"Expected abs(rect.height() - 60) < 1, got {abs(rect.height() - 60)}"
        )

    def test_shift_constrains_to_square(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        shift_event = _make_mouse_event(modifiers=Qt.KeyboardModifier.ShiftModifier)
        release_event = _make_mouse_event()

        tool.mouse_press(QPointF(10, 10), press_event)
        tool.mouse_move(QPointF(110, 60), shift_event)
        tool.mouse_release(QPointF(110, 60), release_event)

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        rect = rect_item.rect()
        assert abs(rect.width() - rect.height()) < 1, (
            f"Expected abs() < 1, got {abs(rect.width() - rect.height())}"
        )

    def test_custom_stroke_and_fill_colors(self, qapp) -> None:
        stroke = QColor("#00FF00")
        fill = QColor(0, 0, 255, 128)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool(stroke_color=stroke, fill_color=fill)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.pen().color().name() == stroke.name(), (
            f"Expected name() to be name(), got {rect_item.pen().color().name()}"
        )
        assert rect_item.brush().color().alpha() == fill.alpha(), (
            f"Expected alpha() to be alpha(), got {rect_item.brush().color().alpha()}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0, f"Expected len(rects) to be 0, got {len(rects)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(110, 110), event)
        tool.mouse_release(QPointF(110, 110), event)

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0, f"Expected len(rects) to be 0, got {len(rects)}"

    def test_set_stroke_color(self, qapp) -> None:
        tool = RectangleTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color, (
            f"Expected tool._stroke_color to be new_color, got {tool._stroke_color}"
        )

    def test_set_fill_color(self, qapp) -> None:
        tool = RectangleTool()
        new_color = QColor(255, 0, 0, 100)
        tool.set_fill_color(new_color)
        assert tool._fill_color == new_color, (
            f"Expected tool._fill_color to be new_color, got {tool._fill_color}"
        )

    def test_set_stroke_width(self, qapp) -> None:
        tool = RectangleTool()
        tool.set_stroke_width(5)
        assert tool._stroke_width == 5, (
            f"Expected tool._stroke_width to be 5, got {tool._stroke_width}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        flags = rect_item.flags()
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# EllipseTool
# ---------------------------------------------------------------------------


class TestEllipseTool:
    def test_creates_ellipse(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 1, f"Expected len(ellipses) to be 1, got {len(ellipses)}"

    def test_shift_constrains_to_circle(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        shift_event = _make_mouse_event(modifiers=Qt.KeyboardModifier.ShiftModifier)
        release_event = _make_mouse_event()

        tool.mouse_press(QPointF(10, 10), press_event)
        tool.mouse_move(QPointF(110, 60), shift_event)
        tool.mouse_release(QPointF(110, 60), release_event)

        ellipse_item = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)][0]
        rect = ellipse_item.rect()
        assert abs(rect.width() - rect.height()) < 1, (
            f"Expected abs() < 1, got {abs(rect.width() - rect.height())}"
        )

    def test_custom_stroke_and_fill_colors(self, qapp) -> None:
        stroke = QColor("#00FF00")
        fill = QColor(0, 0, 255, 128)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool(stroke_color=stroke, fill_color=fill)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipse_item = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)][0]
        assert ellipse_item.pen().color().name() == stroke.name(), (
            f"Expected name() to be name(), got {ellipse_item.pen().color().name()}"
        )
        assert ellipse_item.brush().color().alpha() == fill.alpha(), (
            f"Expected alpha() to be alpha(), got {ellipse_item.brush().color().alpha()}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 0, f"Expected len(ellipses) to be 0, got {len(ellipses)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 0, f"Expected len(ellipses) to be 0, got {len(ellipses)}"

    def test_set_stroke_color(self, qapp) -> None:
        tool = EllipseTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color, (
            f"Expected tool._stroke_color to be new_color, got {tool._stroke_color}"
        )

    def test_set_fill_color(self, qapp) -> None:
        tool = EllipseTool()
        new_color = QColor(255, 0, 0, 100)
        tool.set_fill_color(new_color)
        assert tool._fill_color == new_color, (
            f"Expected tool._fill_color to be new_color, got {tool._fill_color}"
        )

    def test_set_stroke_width(self, qapp) -> None:
        tool = EllipseTool()
        tool.set_stroke_width(7)
        assert tool._stroke_width == 7, (
            f"Expected tool._stroke_width to be 7, got {tool._stroke_width}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipse_item = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)][0]
        flags = ellipse_item.flags()
        assert flags & QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsEllipseItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# LineTool
# ---------------------------------------------------------------------------


class TestLineTool:
    def test_creates_line(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 1, f"Expected len(lines) to be 1, got {len(lines)}"

    def test_line_endpoints(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(10, 20), QPointF(110, 80))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        line = line_item.line()
        assert abs(line.p1().x() - 10) < 1, (
            f"Expected abs(line.p1().x() - 10) < 1, got {abs(line.p1().x() - 10)}"
        )
        assert abs(line.p1().y() - 20) < 1, (
            f"Expected abs(line.p1().y() - 20) < 1, got {abs(line.p1().y() - 20)}"
        )
        assert abs(line.p2().x() - 110) < 1, (
            f"Expected abs(line.p2().x() - 110) < 1, got {abs(line.p2().x() - 110)}"
        )
        assert abs(line.p2().y() - 80) < 1, (
            f"Expected abs(line.p2().y() - 80) < 1, got {abs(line.p2().y() - 80)}"
        )

    def test_shift_constrains_angle_snap(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        shift_event = _make_mouse_event(modifiers=Qt.KeyboardModifier.ShiftModifier)
        release_event = _make_mouse_event()

        tool.mouse_press(QPointF(0, 0), press_event)
        # Move at ~47 degrees — should snap to 45 degrees (3 * 15)
        tool.mouse_move(QPointF(100, 105), shift_event)
        tool.mouse_release(QPointF(100, 105), release_event)

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        line = line_item.line()
        # At 45 degrees, dx and dy should be approximately equal
        assert abs(line.p2().x() - line.p2().y()) < 2, (
            f"Expected abs() < 2, got {abs(line.p2().x() - line.p2().y())}"
        )

    def test_custom_stroke_color(self, qapp) -> None:
        stroke = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool(stroke_color=stroke)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        assert line_item.pen().color().name() == stroke.name(), (
            f"Expected name() to be name(), got {line_item.pen().color().name()}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 0, f"Expected len(lines) to be 0, got {len(lines)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 0, f"Expected len(lines) to be 0, got {len(lines)}"

    def test_set_stroke_color(self, qapp) -> None:
        tool = LineTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color, (
            f"Expected tool._stroke_color to be new_color, got {tool._stroke_color}"
        )

    def test_set_stroke_width(self, qapp) -> None:
        tool = LineTool()
        tool.set_stroke_width(10)
        assert tool._stroke_width == 10, (
            f"Expected tool._stroke_width to be 10, got {tool._stroke_width}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        flags = line_item.flags()
        assert flags & QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsLineItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# ArrowTool
# ---------------------------------------------------------------------------


class TestArrowTool:
    def test_creates_arrow_group(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        # Should contain at least a line item and a path item (arrowhead)
        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(lines) >= 1, f"Expected len(lines) >= 1, got {len(lines)}"
        assert len(paths) >= 1, f"Expected len(paths) >= 1, got {len(paths)}"

    def test_custom_stroke_color(self, qapp) -> None:
        stroke = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool(stroke_color=stroke)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_items = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(line_items) >= 1, f"Expected len(line_items) >= 1, got {len(line_items)}"
        assert line_items[0].pen().color().name() == stroke.name(), (
            f"Expected name() to be name(), got {line_items[0].pen().color().name()}"
        )

    def test_custom_stroke_width(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool(stroke_width=5)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_items = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(line_items) >= 1, f"Expected len(line_items) >= 1, got {len(line_items)}"
        assert line_items[0].pen().widthF() == 5, (
            f"Expected line_items[0].pen().widthF() to be 5, got {line_items[0].pen().widthF()}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        # After discard, scene should have no line/path items remaining
        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        groups = [i for i in scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(lines) == 0, f"Expected len(lines) to be 0, got {len(lines)}"
        assert len(paths) == 0, f"Expected len(paths) to be 0, got {len(paths)}"
        assert len(groups) == 0, f"Expected len(groups) to be 0, got {len(groups)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        assert len(scene.items()) == 0, (
            f"Expected len(scene.items()) to be 0, got {len(scene.items())}"
        )

    def test_set_stroke_color(self, qapp) -> None:
        tool = ArrowTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color, (
            f"Expected tool._stroke_color to be new_color, got {tool._stroke_color}"
        )

    def test_set_stroke_width(self, qapp) -> None:
        tool = ArrowTool()
        tool.set_stroke_width(8)
        assert tool._stroke_width == 8, (
            f"Expected tool._stroke_width to be 8, got {tool._stroke_width}"
        )

    def test_group_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), press_event)

        # Verify the group has correct flags before release
        assert tool._group is not None, f"Expected tool._group to not be None, got {tool._group}"
        flags = tool._group.flags()
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )

        move_event = _make_mouse_event()
        tool.mouse_move(QPointF(100, 100), move_event)
        release_event = _make_mouse_event()
        tool.mouse_release(QPointF(100, 100), release_event)


# ---------------------------------------------------------------------------
# FreehandTool
# ---------------------------------------------------------------------------


class TestFreehandTool:
    def test_creates_path(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)
        for i in range(1, 20):
            tool.mouse_move(QPointF(i * 5, i * 5), event)
        tool.mouse_release(QPointF(95, 95), event)

        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 1, f"Expected len(paths) to be 1, got {len(paths)}"

    def test_custom_stroke_color(self, qapp) -> None:
        stroke = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool(stroke_color=stroke)
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)
        for i in range(1, 20):
            tool.mouse_move(QPointF(i * 5, i * 5), event)
        tool.mouse_release(QPointF(95, 95), event)

        path_item = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)][0]
        assert path_item.pen().color().name() == stroke.name(), (
            f"Expected name() to be name(), got {path_item.pen().color().name()}"
        )

    def test_too_short_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        # No moves or tiny move — bounding rect < 2x2
        tool.mouse_release(QPointF(10, 10), event)

        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 0, f"Expected len(paths) to be 0, got {len(paths)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 0, f"Expected len(paths) to be 0, got {len(paths)}"

    def test_set_stroke_color(self, qapp) -> None:
        tool = FreehandTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color, (
            f"Expected tool._stroke_color to be new_color, got {tool._stroke_color}"
        )

    def test_set_stroke_width(self, qapp) -> None:
        tool = FreehandTool()
        tool.set_stroke_width(4)
        assert tool._stroke_width == 4, (
            f"Expected tool._stroke_width to be 4, got {tool._stroke_width}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)
        for i in range(1, 20):
            tool.mouse_move(QPointF(i * 5, i * 5), event)
        tool.mouse_release(QPointF(95, 95), event)

        path_item = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)][0]
        flags = path_item.flags()
        assert flags & QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsPathItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# TextTool
# ---------------------------------------------------------------------------


class TestTextTool:
    def test_places_text_item(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 1, f"Expected len(text_items) to be 1, got {len(text_items)}"
        assert text_items[0].pos().x() == 50, (
            f"Expected text_items[0].pos().x() to be 50, got {text_items[0].pos().x()}"
        )
        assert text_items[0].pos().y() == 50, (
            f"Expected text_items[0].pos().y() to be 50, got {text_items[0].pos().y()}"
        )

    def test_clicking_existing_text_enters_edit_mode(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        # Place a text item and give it content
        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        text_item = tool._active_item
        assert text_item is not None, f"Expected text_item to not be None, got {text_item}"
        text_item.setPlainText("Hello")

        # Finalize (simulating clicking elsewhere), then click on the same item
        tool._finalize_text()

        # Click at the text item position — itemAt will find it
        tool.mouse_press(QPointF(50, 50), event)

        # The active item should be the existing text item (re-entered edit mode)
        assert tool._active_item is text_item, (
            f"Expected tool._active_item to be text_item, got {tool._active_item}"
        )
        assert text_item.textInteractionFlags() == Qt.TextInteractionFlag.TextEditorInteraction, (
            f"Expected TextEditorInteraction, got {text_item.textInteractionFlags()}"
        )

    def test_deactivate_finalizes_text(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool._active_item.setPlainText("Test text")

        tool.deactivate()

        assert tool._active_item is None, (
            f"Expected tool._active_item to be None, got {tool._active_item}"
        )
        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 1, f"Expected len(text_items) to be 1, got {len(text_items)}"
        assert text_items[0].textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction, (
            f"Expected NoTextInteraction, got {text_items[0].textInteractionFlags()}"
        )

    def test_empty_text_removed_on_finalize(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        # Leave text empty (default)

        tool.deactivate()

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 0, f"Expected len(text_items) to be 0, got {len(text_items)}"

    def test_whitespace_only_text_removed(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool._active_item.setPlainText("   ")

        tool.deactivate()

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 0, f"Expected len(text_items) to be 0, got {len(text_items)}"

    def test_set_color(self, qapp) -> None:
        tool = TextTool()
        new_color = QColor("#0000FF")
        tool.set_color(new_color)
        assert tool._color == new_color, f"Expected tool._color to be new_color, got {tool._color}"

    def test_set_font(self, qapp) -> None:
        tool = TextTool()
        new_font = QFont("Courier", 20)
        tool.set_font(new_font)
        assert tool._font.family() == "Courier", (
            f"Expected tool._font.family() to be 'Courier', got {tool._font.family()}"
        )
        assert tool._font.pointSize() == 20, (
            f"Expected tool._font.pointSize() to be 20, got {tool._font.pointSize()}"
        )

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(50, 50), event)

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 0, f"Expected len(text_items) to be 0, got {len(text_items)}"

    def test_custom_color_applied_to_text(self, qapp) -> None:
        color = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool(color=color)
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        text_item = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)][0]
        assert text_item.defaultTextColor().name() == color.name(), (
            f"Expected name() to be name(), got {text_item.defaultTextColor().name()}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        text_item = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)][0]
        flags = text_item.flags()
        assert flags & QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# NumberTool
# ---------------------------------------------------------------------------


class TestNumberTool:
    def test_counter_increments(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_release(QPointF(50, 50), event)
        assert tool._counter == 1, f"Expected tool._counter to be 1, got {tool._counter}"

        tool.mouse_press(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)
        assert tool._counter == 2, f"Expected tool._counter to be 2, got {tool._counter}"

    def test_creates_group_with_ellipse_and_text(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        # Should contain ellipse and text items
        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        texts = [i for i in scene.items() if isinstance(i, QGraphicsSimpleTextItem)]
        assert len(ellipses) >= 1, f"Expected len(ellipses) >= 1, got {len(ellipses)}"
        assert len(texts) >= 1, f"Expected len(texts) >= 1, got {len(texts)}"

    def test_reset_counter(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_press(QPointF(100, 100), event)
        assert tool._counter == 2, f"Expected tool._counter to be 2, got {tool._counter}"

        tool.reset_counter()
        assert tool._counter == 0, f"Expected tool._counter to be 0, got {tool._counter}"

        tool.mouse_press(QPointF(150, 150), event)
        assert tool._counter == 1, f"Expected tool._counter to be 1, got {tool._counter}"

    def test_set_bg_color(self, qapp) -> None:
        tool = NumberTool()
        new_color = QColor("#0000FF")
        tool.set_bg_color(new_color)
        assert tool._bg_color == new_color, (
            f"Expected tool._bg_color to be new_color, got {tool._bg_color}"
        )

    def test_set_text_color(self, qapp) -> None:
        tool = NumberTool()
        new_color = QColor("#FFFF00")
        tool.set_text_color(new_color)
        assert tool._text_color == new_color, (
            f"Expected tool._text_color to be new_color, got {tool._text_color}"
        )

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(50, 50), event)

        assert tool._counter == 0, f"Expected tool._counter to be 0, got {tool._counter}"
        assert len(scene.items()) == 0, (
            f"Expected len(scene.items()) to be 0, got {len(scene.items())}"
        )

    def test_group_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        groups = [i for i in scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(groups) >= 1, f"Expected len(groups) >= 1, got {len(groups)}"
        flags = groups[0].flags()
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# HighlightTool
# ---------------------------------------------------------------------------


class TestHighlightTool:
    def test_creates_highlight_rect(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 1, f"Expected len(rects) to be 1, got {len(rects)}"

    def test_highlight_is_semi_transparent(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        brush_color = rect_item.brush().color()
        assert brush_color.alpha() < 255, (
            f"Expected brush_color.alpha() < 255, got {brush_color.alpha()}"
        )
        assert brush_color.alpha() > 0, (
            f"Expected brush_color.alpha() > 0, got {brush_color.alpha()}"
        )

    def test_highlight_has_no_pen(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.pen().style() == Qt.PenStyle.NoPen, (
            f"Expected style() to be NoPen, got {rect_item.pen().style()}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(12, 12))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0, f"Expected len(rects) to be 0, got {len(rects)}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0, f"Expected len(rects) to be 0, got {len(rects)}"

    def test_set_color(self, qapp) -> None:
        tool = HighlightTool()
        new_color = QColor(0, 255, 0, 80)
        tool.set_color(new_color)
        assert tool._color == new_color, f"Expected tool._color to be new_color, got {tool._color}"

    def test_custom_color_applied(self, qapp) -> None:
        custom = QColor(0, 255, 0, 80)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool(color=custom)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.brush().color().green() == 255, (
            f"Expected green() to be 255, got {rect_item.brush().color().green()}"
        )
        assert rect_item.brush().color().alpha() == 80, (
            f"Expected alpha() to be 80, got {rect_item.brush().color().alpha()}"
        )

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        flags = rect_item.flags()
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )


# ---------------------------------------------------------------------------
# ObfuscateTool
# ---------------------------------------------------------------------------


class TestObfuscateTool:
    def test_pixelates_region_of_background(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        # Should have the background + an ObfuscationItem overlay
        obfuscation_items = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        assert len(obfuscation_items) == 1, (
            f"Expected 1 ObfuscationItem, got {len(obfuscation_items)}"
        )

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(12, 12))

        # Only background should remain
        obfuscation_items = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        assert len(obfuscation_items) == 0, (
            f"Expected 0 ObfuscationItem, got {len(obfuscation_items)}"
        )

    def test_too_narrow_draw_discarded(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        # Width is ok (90) but height is too small (3)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 13))

        obfuscation_items = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        assert len(obfuscation_items) == 0, (
            f"Expected 0 ObfuscationItem, got {len(obfuscation_items)}"
        )

    def test_no_background_does_nothing(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        # No background means no pixelation overlay
        obfuscation_items = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        assert len(obfuscation_items) == 0, (
            f"Expected 0 ObfuscationItem, got {len(obfuscation_items)}"
        )

    def test_pixelate_static_method(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor(200, 100, 50))

        result = ObfuscateTool._pixelate(pixmap, 10)

        assert result.width() == 100, f"Expected result.width() to be 100, got {result.width()}"
        assert result.height() == 100, f"Expected result.height() to be 100, got {result.height()}"

    def test_right_click_ignored(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), _make_mouse_event())
        tool.mouse_release(QPointF(100, 100), _make_mouse_event())

        # Only background should remain — right-click didn't set origin
        obfuscation_items = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        assert len(obfuscation_items) == 0, (
            f"Expected 0 ObfuscationItem, got {len(obfuscation_items)}"
        )

    def test_overlay_is_selectable_and_movable(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        overlays = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        overlay = overlays[0]
        flags = overlay.flags()
        assert flags & QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert flags & QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, (
            f"Expected ItemIsMovable flag to be set, got {flags}"
        )

    def test_overlay_refreshes_on_move(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(60, 60))

        overlays = [
            i for i in scene.items() if isinstance(i, ObfuscationItem)
        ]
        overlay = overlays[0]
        pixmap_before = overlay.pixmap().toImage()

        # Move the item to a new position
        overlay.setPos(80, 80)
        pixmap_after = overlay.pixmap().toImage()

        # The pixmap should have been refreshed (different region of background)
        assert pixmap_after.size() == pixmap_before.size(), (
            "Pixmap size should remain the same after move"
        )


# ---------------------------------------------------------------------------
# CropTool
# ---------------------------------------------------------------------------


class TestCropTool:
    def test_creates_crop_rect(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        assert tool._crop_rect_item is not None, (
            f"Expected tool._crop_rect_item to not be None, got {tool._crop_rect_item}"
        )
        rect = tool._crop_rect_item.rect()
        assert rect.width() >= 10, f"Expected rect.width() >= 10, got {rect.width()}"
        assert rect.height() >= 10, f"Expected rect.height() >= 10, got {rect.height()}"

    def test_crop_rect_has_high_zvalue(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)

        assert tool._crop_rect_item is not None, (
            f"Expected tool._crop_rect_item to not be None, got {tool._crop_rect_item}"
        )
        assert tool._crop_rect_item.zValue() == 9999, (
            f"Expected zValue() to be 9999, got {tool._crop_rect_item.zValue()}"
        )

    def test_apply_crop_replaces_scene(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        tool.apply_crop()

        # Scene should have a new background pixmap
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 1, (
            f"Expected len(pixmap_items) to be 1, got {len(pixmap_items)}"
        )
        assert pixmap_items[0].zValue() == -1000, (
            f"Expected pixmap_items[0].zValue() to be -1000, got {pixmap_items[0].zValue()}"
        )

    def test_apply_crop_too_small_does_nothing(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        # Create a crop rect item but with a small rect
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(15, 15), event)
        # mouse_release should clear the UI due to too-small check
        tool.mouse_release(QPointF(15, 15), event)

        # crop_rect_item was cleared by _clear_crop_ui
        assert tool._crop_rect_item is None, (
            f"Expected tool._crop_rect_item to be None, got {tool._crop_rect_item}"
        )

    def test_cancel_crop_clears_ui(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        assert tool._crop_rect_item is not None, (
            f"Expected tool._crop_rect_item to not be None, got {tool._crop_rect_item}"
        )

        tool.cancel_crop()

        assert tool._crop_rect_item is None, (
            f"Expected tool._crop_rect_item to be None, got {tool._crop_rect_item}"
        )
        assert tool._origin is None, f"Expected tool._origin to be None, got {tool._origin}"

    def test_deactivate_clears_crop_ui(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        tool.deactivate()

        assert tool._crop_rect_item is None, (
            f"Expected tool._crop_rect_item to be None, got {tool._crop_rect_item}"
        )
        assert tool._origin is None, f"Expected tool._origin to be None, got {tool._origin}"

    def test_right_click_ignored(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        assert tool._crop_rect_item is None, (
            f"Expected tool._crop_rect_item to be None, got {tool._crop_rect_item}"
        )

    def test_apply_crop_without_selection_does_nothing(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        items_before = len(scene.items())
        tool.apply_crop()
        items_after = len(scene.items())

        assert items_before == items_after, (
            f"Expected items_before to be items_after, got {items_before}"
        )

    def test_apply_crop_without_background_does_nothing(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        # Manually create a crop rect
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        tool.apply_crop()

        # Should still have the crop rect (it wasn't cleared because no bg found)
        # Actually, apply_crop returns early if no bg_item, so the crop rect remains
        assert tool._crop_rect_item is not None, (
            f"Expected tool._crop_rect_item to not be None, got {tool._crop_rect_item}"
        )


# ---------------------------------------------------------------------------
# SelectTool
# ---------------------------------------------------------------------------


class TestSelectTool:
    def test_selects_item_on_click(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(25, 25), event)

        assert rect_item.isSelected(), (
            f"Expected rect_item.isSelected() to be truthy, got {rect_item.isSelected()}"
        )

    def test_moves_item_on_drag(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable)
        rect_item.setPos(10, 10)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        # Set up drag state directly (itemAt may not work without a visible view)
        tool._dragging = True
        tool._drag_item = rect_item
        tool._drag_start = QPointF(25, 25)
        tool._item_start_pos = rect_item.pos()

        event = _make_mouse_event()
        tool.mouse_move(QPointF(75, 75), event)

        # Item should have moved by delta (50, 50) from original (10, 10)
        assert abs(rect_item.pos().x() - 60) < 1, (
            f"Expected abs(rect_item.pos().x() - 60) < 1, got {abs(rect_item.pos().x() - 60)}"
        )
        assert abs(rect_item.pos().y() - 60) < 1, (
            f"Expected abs(rect_item.pos().y() - 60) < 1, got {abs(rect_item.pos().y() - 60)}"
        )

    def test_deselects_on_empty_click(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        # First select the item
        event = _make_mouse_event()
        tool.mouse_press(QPointF(25, 25), event)
        assert rect_item.isSelected(), (
            f"Expected rect_item.isSelected() to be truthy, got {rect_item.isSelected()}"
        )
        tool.mouse_release(QPointF(25, 25), event)

        # Click on empty space
        tool.mouse_press(QPointF(200, 200), event)
        assert not rect_item.isSelected(), (
            f"Expected rect_item.isSelected() to be falsy, got {rect_item.isSelected()}"
        )

    def test_ignores_background_item(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        # Background should NOT be selected (zValue <= -1000)
        assert not bg.isSelected(), f"Expected bg.isSelected() to be falsy, got {bg.isSelected()}"
        assert tool._dragging is False, f"Expected tool._dragging to be False, got {tool._dragging}"

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(25, 25), event)

        assert not rect_item.isSelected(), (
            f"Expected rect_item.isSelected() to be falsy, got {rect_item.isSelected()}"
        )

    def test_mouse_release_resets_drag_state(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(25, 25), event)
        assert tool._dragging is True, f"Expected tool._dragging to be True, got {tool._dragging}"

        tool.mouse_release(QPointF(25, 25), event)
        assert tool._dragging is False, f"Expected tool._dragging to be False, got {tool._dragging}"
        assert tool._drag_item is None, (
            f"Expected tool._drag_item to be None, got {tool._drag_item}"
        )
        assert tool._drag_start is None, (
            f"Expected tool._drag_start to be None, got {tool._drag_start}"
        )
        assert tool._item_start_pos is None, (
            f"Expected tool._item_start_pos to be None, got {tool._item_start_pos}"
        )

    def test_move_without_press_is_noop(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        original_pos = rect_item.pos()
        event = _make_mouse_event()
        tool.mouse_move(QPointF(75, 75), event)

        assert rect_item.pos() == original_pos, (
            f"Expected rect_item.pos() to be original_pos, got {rect_item.pos()}"
        )

    def test_drag_distance(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        rect_item = QGraphicsRectItem(0, 0, 50, 50)
        rect_item.setPos(10, 20)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable)
        scene.addItem(rect_item)

        tool = SelectTool()
        tool.activate(scene, view)

        # Set up drag state directly
        tool._dragging = True
        tool._drag_item = rect_item
        tool._drag_start = QPointF(25, 25)
        tool._item_start_pos = rect_item.pos()

        event = _make_mouse_event()
        tool.mouse_move(QPointF(75, 85), event)

        # Delta is (50, 60), original pos was (10, 20) → new pos (60, 80)
        assert abs(rect_item.pos().x() - 60) < 1, (
            f"Expected abs(rect_item.pos().x() - 60) < 1, got {abs(rect_item.pos().x() - 60)}"
        )
        assert abs(rect_item.pos().y() - 80) < 1, (
            f"Expected abs(rect_item.pos().y() - 80) < 1, got {abs(rect_item.pos().y() - 80)}"
        )


# ---------------------------------------------------------------------------
# Drawing tool undo integration
# ---------------------------------------------------------------------------


def _make_canvas_with_history():
    """Create an EditorCanvas with image and undo history wired up."""
    from verdiclip.editor.canvas import EditorCanvas
    from verdiclip.editor.history import EditorHistory
    canvas = EditorCanvas()
    pixmap = QPixmap(200, 200)
    pixmap.fill(QColor(200, 200, 200))
    canvas.set_image(pixmap)
    history = EditorHistory()
    canvas.set_history(history)
    return canvas, history


class TestRectangleToolUndo:
    def test_rectangle_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = RectangleTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        rects = [i for i in canvas._scene.items() if isinstance(i, QGraphicsRectItem)
                 and i.zValue() > -1000]
        assert len(rects) >= 1, (
            f"Expected at least 1 drawn rectangle, got {len(rects)}"
        )
        assert history.can_undo, "Expected undo to be available after drawing rectangle"

        history.undo()
        rects_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsRectItem)
                       and i.zValue() > -1000]
        # The boundary item is also a QGraphicsRectItem — filter by zValue
        drawn_rects = [r for r in rects_after if r.zValue() < 9000]
        assert len(drawn_rects) == 0, (
            f"Expected 0 drawn rectangles after undo, got {len(drawn_rects)}"
        )


class TestEllipseToolUndo:
    def test_ellipse_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = EllipseTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        ellipses = [i for i in canvas._scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) >= 1, "Expected at least 1 drawn ellipse"
        assert history.can_undo, "Expected undo available after drawing ellipse"

        history.undo()
        ellipses_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses_after) == 0, (
            f"Expected 0 ellipses after undo, got {len(ellipses_after)}"
        )


class TestLineToolUndo:
    def test_line_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = LineTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        lines = [i for i in canvas._scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) >= 1, "Expected at least 1 drawn line"
        assert history.can_undo, "Expected undo available after drawing line"

        history.undo()
        lines_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines_after) == 0, (
            f"Expected 0 lines after undo, got {len(lines_after)}"
        )


class TestHighlightToolUndo:
    def test_highlight_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = HighlightTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        assert history.can_undo, "Expected undo available after highlighting"
        history.undo()

        # Only boundary rect and pixmap should remain
        rects = [i for i in canvas._scene.items()
                 if isinstance(i, QGraphicsRectItem) and i.zValue() < 9000 and i.zValue() > -1000]
        assert len(rects) == 0, (
            f"Expected 0 highlight rects after undo, got {len(rects)}"
        )


class TestFreehandToolUndo:
    def test_freehand_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = FreehandTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(50, 50), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        paths = [i for i in canvas._scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) >= 1, "Expected at least 1 freehand path"
        assert history.can_undo, "Expected undo available after freehand drawing"

        history.undo()
        paths_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths_after) == 0, (
            f"Expected 0 freehand paths after undo, got {len(paths_after)}"
        )


class TestArrowToolUndo:
    def test_arrow_draw_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = ArrowTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)

        assert history.can_undo, "Expected undo available after drawing arrow"
        history.undo()
        # After undo, the arrow group should be gone
        groups = [i for i in canvas._scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(groups) == 0, (
            f"Expected 0 arrow groups after undo, got {len(groups)}"
        )


class TestNumberToolUndo:
    def test_number_marker_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = NumberTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        assert history.can_undo, "Expected undo available after placing number marker"
        groups = [i for i in canvas._scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(groups) >= 1, "Expected at least 1 number marker group"

        history.undo()
        groups_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(groups_after) == 0, (
            f"Expected 0 number marker groups after undo, got {len(groups_after)}"
        )


class TestTextToolUndo:
    def test_text_placement_is_undoable(self, qapp) -> None:
        canvas, history = _make_canvas_with_history()
        tool = TextTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        texts = [i for i in canvas._scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(texts) >= 1, "Expected at least 1 text item"
        assert history.can_undo, "Expected undo available after placing text"

        history.undo()
        texts_after = [i for i in canvas._scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(texts_after) == 0, (
            f"Expected 0 text items after undo, got {len(texts_after)}"
        )


# ---------------------------------------------------------------------------
# Coverage: TextTool — finalize with configured font and text content
# ---------------------------------------------------------------------------


class TestTextToolFinalizeWithFont:
    def test_text_item_uses_configured_font(self, qapp) -> None:
        custom_font = QFont("Consolas", 24)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool(font=custom_font)
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(30, 30), event)

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 1, (
            f"Expected 1 text item after click, got {len(text_items)}"
        )
        item_font = text_items[0].font()
        assert item_font.family() == "Consolas", (
            f"Expected font family 'Consolas', got '{item_font.family()}'"
        )
        assert item_font.pointSize() == 24, (
            f"Expected font point size 24, got {item_font.pointSize()}"
        )

    def test_finalize_preserves_text_content(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool._active_item.setPlainText("Hello World")

        tool._finalize_text()

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 1, (
            f"Expected finalized text item to remain in scene, got {len(text_items)}"
        )
        assert text_items[0].toPlainText() == "Hello World", (
            f"Expected text content 'Hello World', got '{text_items[0].toPlainText()}'"
        )
        assert text_items[0].textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction, (
            f"Expected NoTextInteraction after finalize, "
            f"got {text_items[0].textInteractionFlags()}"
        )


# ---------------------------------------------------------------------------
# Coverage: NumberTool — auto-increment marker labels
# ---------------------------------------------------------------------------


class TestNumberToolAutoIncrement:
    def test_three_placements_produce_labels_1_2_3(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        for pos in [QPointF(50, 50), QPointF(100, 100), QPointF(150, 150)]:
            tool.mouse_press(pos, event)

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsSimpleTextItem)]
        labels = sorted([item.text() for item in text_items])
        assert labels == ["1", "2", "3"], (
            f"Expected marker labels ['1', '2', '3'], got {labels}"
        )

    def test_counter_value_matches_placement_count(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        assert tool._counter == 1, (
            f"Expected counter to be 1 after first placement, got {tool._counter}"
        )
        tool.mouse_press(QPointF(20, 20), event)
        assert tool._counter == 2, (
            f"Expected counter to be 2 after second placement, got {tool._counter}"
        )
        tool.mouse_press(QPointF(30, 30), event)
        assert tool._counter == 3, (
            f"Expected counter to be 3 after third placement, got {tool._counter}"
        )


# ---------------------------------------------------------------------------
# Coverage: FreehandTool — mouse moves add points to path
# ---------------------------------------------------------------------------


class TestFreehandToolPathPoints:
    def test_mouse_moves_extend_path(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)

        move_points = [QPointF(10, 10), QPointF(20, 20), QPointF(30, 30), QPointF(40, 40)]
        for pt in move_points:
            tool.mouse_move(pt, event)

        assert tool._path is not None, "Expected path to exist during drawing"
        bounds = tool._path.boundingRect()
        assert bounds.width() > 0, (
            f"Expected path bounding rect width > 0 after moves, got {bounds.width()}"
        )
        assert bounds.height() > 0, (
            f"Expected path bounding rect height > 0 after moves, got {bounds.height()}"
        )

        tool.mouse_release(QPointF(40, 40), event)
        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 1, (
            f"Expected 1 path item after drawing, got {len(paths)}"
        )

    def test_path_grows_with_each_move(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)

        tool.mouse_move(QPointF(20, 20), event)
        length_after_first = tool._path.length()

        tool.mouse_move(QPointF(40, 40), event)
        length_after_second = tool._path.length()

        assert length_after_second > length_after_first, (
            f"Expected path length to grow after second move "
            f"({length_after_second} > {length_after_first})"
        )

        tool.mouse_release(QPointF(40, 40), event)

