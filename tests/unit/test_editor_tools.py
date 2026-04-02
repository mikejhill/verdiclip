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
        assert not (flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert not (flags & QGraphicsEllipseItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert not (flags & QGraphicsLineItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), press_event)

        # The in-progress arrow is an ArrowItem already in the scene
        assert tool._arrow is not None, "Expected _arrow to be set during draw"
        assert isinstance(tool._arrow, ArrowItem), "Expected _arrow to be an ArrowItem"
        flags = tool._arrow.flags()
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert not (flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert not (flags & QGraphicsPathItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert not (flags & QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert tool._last_numeric_value == 1, (
            f"Expected _last_numeric_value 1, got {tool._last_numeric_value}"
        )

        tool.mouse_press(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)
        assert tool._last_numeric_value == 2, (
            f"Expected _last_numeric_value 2, got {tool._last_numeric_value}"
        )

    def test_creates_marker_with_ellipse_and_text(self, qapp) -> None:
        from verdiclip.editor.tools.number import NumberMarkerItem
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        markers = [i for i in scene.items() if isinstance(i, NumberMarkerItem)]
        assert len(markers) == 1, f"Expected 1 NumberMarkerItem, got {len(markers)}"
        assert markers[0].value == "1", (
            f"Expected marker value '1', got '{markers[0].value}'"
        )

    def test_reset_counter(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_press(QPointF(100, 100), event)
        assert tool._last_numeric_value == 2, (
            f"Expected _last_numeric_value 2, got {tool._last_numeric_value}"
        )

        tool.reset_counter()
        assert tool._last_numeric_value == 0, (
            f"Expected _last_numeric_value 0, got {tool._last_numeric_value}"
        )

        tool.mouse_press(QPointF(150, 150), event)
        assert tool._last_numeric_value == 1, (
            f"Expected _last_numeric_value 1, got {tool._last_numeric_value}"
        )

    def test_marker_value_editable(self, qapp) -> None:
        """NumberMarkerItem value can be set and read."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        marker = NumberMarkerItem("1", QColor("#E74C3C"), QColor("#FFFFFF"))
        assert marker.value == "1", f"Expected value '1', got '{marker.value}'"

        marker.value = "42"
        assert marker.value == "42", f"Expected value '42', got '{marker.value}'"

    def test_marker_accepts_non_numeric_value(self, qapp) -> None:
        """NumberMarkerItem accepts non-numeric values like 'A' or 'X'."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        marker = NumberMarkerItem("A", QColor("#E74C3C"), QColor("#FFFFFF"))
        assert marker.value == "A", f"Expected value 'A', got '{marker.value}'"

    def test_next_counter_after_numeric_edit(self, qapp) -> None:
        """After editing a marker to numeric value, next counter follows it."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        # Simulate editing the marker to value "10"
        markers = [i for i in scene.items() if isinstance(i, NumberMarkerItem)]
        markers[0].value = "10"
        tool._last_numeric_value = 10  # Simulates editor callback

        tool.mouse_press(QPointF(100, 100), event)
        new_markers = [i for i in scene.items() if isinstance(i, NumberMarkerItem)]
        values = sorted([m.value for m in new_markers])
        assert "11" in values, (
            f"Expected next marker value '11' after editing to '10', got {values}"
        )

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

        assert tool._last_numeric_value == 0, (
            f"Expected _last_numeric_value 0, got {tool._last_numeric_value}"
        )
        assert len(scene.items()) == 0, (
            f"Expected len(scene.items()) to be 0, got {len(scene.items())}"
        )

    def test_marker_is_selectable_not_movable(self, qapp) -> None:
        """Markers are selectable but not ItemIsMovable (SelectTool handles drag)."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        markers = [i for i in scene.items() if isinstance(i, NumberMarkerItem)]
        assert len(markers) >= 1, f"Expected at least 1 marker, got {len(markers)}"
        flags = markers[0].flags()
        assert flags & NumberMarkerItem.GraphicsItemFlag.ItemIsSelectable, (
            f"Expected ItemIsSelectable flag to be set, got {flags}"
        )
        assert not (flags & NumberMarkerItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should NOT be set — SelectTool handles movement manually"
        )

    def test_marker_text_updates_when_value_changed(
        self, qapp,
    ) -> None:
        """Changing value property updates the _text_item display."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        marker = NumberMarkerItem(
            "1", QColor("#E74C3C"), QColor("#FFFFFF"),
        )
        assert marker._text_item.text() == "1", (
            f"Precondition: expected text '1', "
            f"got '{marker._text_item.text()}'"
        )

        marker.value = "99"

        assert marker._text_item.text() == "99", (
            f"Expected _text_item.text() '99' after value change, "
            f"got '{marker._text_item.text()}'"
        )

    def test_marker_non_numeric_value_displays_correctly(
        self, qapp,
    ) -> None:
        """Non-numeric value like 'X' is rendered in _text_item."""
        from verdiclip.editor.tools.number import NumberMarkerItem
        marker = NumberMarkerItem(
            "A", QColor("#E74C3C"), QColor("#FFFFFF"),
        )

        marker.value = "X"

        assert marker._text_item.text() == "X", (
            f"Expected _text_item.text() 'X' for non-numeric value, "
            f"got '{marker._text_item.text()}'"
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
        assert not (flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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
        assert not (flags & QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable), (
            "ItemIsMovable should not be set — SelectTool handles movement"
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

    def test_obfuscation_item_set_size_refreshes_pixelation(
        self, qapp,
    ) -> None:
        """set_size updates the internal pixmap to reflect the new size."""
        from PySide6.QtCore import QSizeF
        scene, bg = _make_scene_with_background(200, 200)

        item = ObfuscationItem(bg, QSizeF(40, 40))
        item.setPos(10, 10)
        scene.addItem(item)
        pixmap_before = item.pixmap()

        item.set_size(QSizeF(80, 80))
        pixmap_after = item.pixmap()

        assert not pixmap_after.isNull(), (
            "Expected non-null pixmap after set_size"
        )
        assert (
            pixmap_after.width() != pixmap_before.width()
            or pixmap_after.height() != pixmap_before.height()
        ), (
            f"Expected pixmap dimensions to change after set_size, "
            f"before={pixmap_before.width()}x{pixmap_before.height()}, "
            f"after={pixmap_after.width()}x{pixmap_after.height()}"
        )

    def test_obfuscation_item_border_visible_during_creation(
        self, qapp,
    ) -> None:
        """During mouse_move, preview item should have _show_border True."""
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), press_event)
        move_event = _make_mouse_event()
        tool.mouse_move(QPointF(80, 80), move_event)

        assert tool._preview_item is not None, (
            "Expected _preview_item to exist during mouse_move"
        )
        assert tool._preview_item._show_border is True, (
            "Expected _show_border True during creation, "
            f"got {tool._preview_item._show_border}"
        )

    def test_obfuscation_item_border_hidden_after_finalize(
        self, qapp,
    ) -> None:
        """After mouse_release the finalized item has _show_border False."""
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(
            tool, scene, view,
            QPointF(10, 10), QPointF(80, 80),
        )

        overlays = [
            i for i in scene.items()
            if isinstance(i, ObfuscationItem)
        ]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem, got {len(overlays)}"
        )
        assert overlays[0]._show_border is False, (
            "Expected _show_border False after finalize, "
            f"got {overlays[0]._show_border}"
        )


# ---------------------------------------------------------------------------
# CropTool
# ---------------------------------------------------------------------------


class TestCropTool:
    def test_crop_rect_visible_during_drag(self, qapp) -> None:
        """Crop rect is visible during mouse_move (before release)."""
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)

        assert tool._crop_rect_item is not None, (
            "Expected crop rect to be visible during drag"
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

    def test_mouse_release_applies_crop(self, qapp) -> None:
        """Crop is applied immediately on mouse release."""
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        # After release, crop is applied and crop UI is cleared
        assert tool._crop_rect_item is None, (
            "Expected crop rect cleared after crop applied on release"
        )
        # Scene should have a new background pixmap
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 1, (
            f"Expected 1 pixmap item after crop, got {len(pixmap_items)}"
        )
        assert pixmap_items[0].zValue() == -1000, (
            f"Expected zValue -1000, got {pixmap_items[0].zValue()}"
        )

    def test_too_small_crop_discarded_on_release(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(15, 15), event)
        tool.mouse_release(QPointF(15, 15), event)

        assert tool._crop_rect_item is None, (
            f"Expected tool._crop_rect_item to be None, got {tool._crop_rect_item}"
        )

    def test_cancel_crop_clears_ui_during_drag(self, qapp) -> None:
        """Cancel clears the crop rect before mouse release."""
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)

        # Cancel before release
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

    def test_crop_without_background_does_not_crop(self, qapp) -> None:
        """When there is no background pixmap, crop has no effect."""
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)

        # apply_crop is called on release but no bg_item means it does nothing
        tool.mouse_release(QPointF(200, 200), event)

        # No pixmap items should exist since there was no background
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 0, (
            f"Expected no pixmap items, got {len(pixmap_items)}"
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

        # Set up drag state using the new multi-item API
        tool._dragging = True
        tool._drag_start = QPointF(25, 25)
        tool._drag_items = [rect_item]
        tool._drag_starts = {id(rect_item): rect_item.pos()}

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
        assert tool._drag_start is None, (
            f"Expected tool._drag_start to be None, got {tool._drag_start}"
        )
        assert tool._drag_items == [], (
            f"Expected tool._drag_items to be empty, got {tool._drag_items}"
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

        # Set up drag state using the new multi-item API
        tool._dragging = True
        tool._drag_start = QPointF(25, 25)
        tool._drag_items = [rect_item]
        tool._drag_starts = {id(rect_item): rect_item.pos()}

        event = _make_mouse_event()
        tool.mouse_move(QPointF(75, 85), event)

        # Delta is (50, 60), original pos was (10, 20) → new pos (60, 80)
        assert abs(rect_item.pos().x() - 60) < 1, (
            f"Expected abs(rect_item.pos().x() - 60) < 1, "
            f"got {abs(rect_item.pos().x() - 60)}"
        )
        assert abs(rect_item.pos().y() - 80) < 1, (
            f"Expected abs(rect_item.pos().y() - 80) < 1, "
            f"got {abs(rect_item.pos().y() - 80)}"
        )

    def test_select_tool_cannot_select_boundary_item(
        self, qapp,
    ) -> None:
        """Boundary rect (zValue >= Z_BOUNDARY) must not be selectable."""
        from verdiclip.editor.canvas import EditorCanvas
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        pixmap.fill(QColor(100, 100, 100))
        canvas.set_image(pixmap)

        tool = SelectTool()
        tool.activate(canvas._scene, canvas)

        # Click at center of the image — boundary overlaps it
        event = _make_mouse_event()
        tool.mouse_press(QPointF(100, 100), event)

        boundary = canvas._boundary_item
        assert boundary is not None, (
            "Precondition: boundary item should exist"
        )
        assert not boundary.isSelected(), (
            "Boundary rect must NOT be selected by SelectTool, "
            f"but isSelected() returned {boundary.isSelected()}"
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
        from verdiclip.editor.tools.number import NumberMarkerItem
        canvas, history = _make_canvas_with_history()
        tool = NumberTool()
        tool.activate(canvas._scene, canvas)
        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        assert history.can_undo, "Expected undo available after placing number marker"
        markers = [i for i in canvas._scene.items() if isinstance(i, NumberMarkerItem)]
        assert len(markers) >= 1, "Expected at least 1 NumberMarkerItem"

        history.undo()
        markers_after = [i for i in canvas._scene.items() if isinstance(i, NumberMarkerItem)]
        assert len(markers_after) == 0, (
            f"Expected 0 markers after undo, got {len(markers_after)}"
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


# ---------------------------------------------------------------------------
# SelectTool — _resolve_top_level_item (parent-walking)
# ---------------------------------------------------------------------------


class TestResolveTopLevelItem:
    def test_returns_item_when_no_parent(self, qapp) -> None:
        """An item with no parent should be returned as-is."""
        from verdiclip.editor.tools.select import _resolve_top_level_item

        scene = QGraphicsScene()
        rect = QGraphicsRectItem(0, 0, 50, 50)
        scene.addItem(rect)

        result = _resolve_top_level_item(rect)
        assert result is rect, (
            f"Expected the same item back when it has no parent, got {result}"
        )

    def test_walks_single_parent(self, qapp) -> None:
        """A child should resolve to its parent."""
        from verdiclip.editor.tools.select import _resolve_top_level_item

        scene = QGraphicsScene()
        parent = QGraphicsRectItem(0, 0, 80, 80)
        child = QGraphicsSimpleTextItem("X", parent)
        scene.addItem(parent)

        result = _resolve_top_level_item(child)
        assert result is parent, (
            f"Expected child to resolve to parent, got {result}"
        )

    def test_walks_nested_parents(self, qapp) -> None:
        """A deeply nested item should resolve to the top-level ancestor."""
        from verdiclip.editor.tools.select import _resolve_top_level_item

        scene = QGraphicsScene()
        grandparent = QGraphicsRectItem(0, 0, 100, 100)
        parent = QGraphicsRectItem(0, 0, 60, 60, grandparent)
        child = QGraphicsSimpleTextItem("Y", parent)
        scene.addItem(grandparent)

        result = _resolve_top_level_item(child)
        assert result is grandparent, (
            f"Expected child to resolve to grandparent, got {result}"
        )

    def test_click_on_child_selects_parent_item(self, qapp) -> None:
        """Clicking on a child graphics item should select the top-level parent."""
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        view.resize(400, 400)
        view.show()

        parent = QGraphicsRectItem(0, 0, 80, 80)
        parent.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        child = QGraphicsSimpleTextItem("X", parent)
        child.setPos(10, 10)
        scene.addItem(parent)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(15, 15), event)

        assert parent.isSelected(), (
            "Expected parent item to be selected after clicking on child's area"
        )


# ---------------------------------------------------------------------------
# SelectTool — rubber band multi-selection
# ---------------------------------------------------------------------------


class TestSelectToolRubberBand:
    def test_rubber_band_created_on_empty_space_click(self, qapp) -> None:
        """Clicking on empty space should start rubber band selection."""
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 400, 400)
        view = QGraphicsView(scene)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(200, 200), event)

        assert tool._rubber_banding is True, (
            f"Expected _rubber_banding to be True, got {tool._rubber_banding}"
        )
        assert tool._rubber_band_rect is not None, (
            "Expected _rubber_band_rect to be created"
        )
        assert tool._rubber_band_rect.zValue() > 9000, (
            f"Expected rubber band zValue > Z_BOUNDARY, "
            f"got {tool._rubber_band_rect.zValue()}"
        )

    def test_rubber_band_rect_updates_on_move(self, qapp) -> None:
        """Dragging should update the rubber band rectangle."""
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 400, 400)
        view = QGraphicsView(scene)

        tool = SelectTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), press_event)

        move_event = _make_mouse_event()
        tool.mouse_move(QPointF(200, 200), move_event)

        rect = tool._rubber_band_rect.rect()
        assert rect.width() > 0, (
            f"Expected rubber band width > 0, got {rect.width()}"
        )
        assert rect.height() > 0, (
            f"Expected rubber band height > 0, got {rect.height()}"
        )

    def test_rubber_band_selects_items_within(self, qapp) -> None:
        """Items within the rubber band area should be selected on release."""
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 400, 400)
        view = QGraphicsView(scene)

        item1 = QGraphicsRectItem(10, 10, 30, 30)
        item1.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        item1.setZValue(0)
        scene.addItem(item1)

        item2 = QGraphicsRectItem(50, 50, 30, 30)
        item2.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        item2.setZValue(0)
        scene.addItem(item2)

        item3 = QGraphicsRectItem(300, 300, 30, 30)
        item3.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        item3.setZValue(0)
        scene.addItem(item3)

        tool = SelectTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), press_event)
        move_event = _make_mouse_event()
        tool.mouse_move(QPointF(100, 100), move_event)
        release_event = _make_mouse_event()
        tool.mouse_release(QPointF(100, 100), release_event)

        assert item1.isSelected(), (
            "Expected item1 inside rubber band to be selected"
        )
        assert item2.isSelected(), (
            "Expected item2 inside rubber band to be selected"
        )
        assert not item3.isSelected(), (
            "Expected item3 outside rubber band to NOT be selected"
        )

    def test_rubber_band_cleaned_up_on_release(self, qapp) -> None:
        """Rubber band rect should be removed from scene after release."""
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 400, 400)
        view = QGraphicsView(scene)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        assert tool._rubber_band_rect is None, (
            "Expected _rubber_band_rect to be None after release"
        )
        assert tool._rubber_banding is False, (
            f"Expected _rubber_banding to be False, got {tool._rubber_banding}"
        )
        rubber_rects = [
            i for i in scene.items()
            if isinstance(i, QGraphicsRectItem) and i.zValue() > 9000
        ]
        assert len(rubber_rects) == 0, (
            f"Expected 0 rubber band rects in scene, got {len(rubber_rects)}"
        )

    def test_rubber_band_ignores_background_items(self, qapp) -> None:
        """Rubber band should not select background items."""
        scene, bg = _make_scene_with_background(200, 200)
        scene.setSceneRect(0, 0, 400, 400)
        view = QGraphicsView(scene)

        annotation = QGraphicsRectItem(10, 10, 30, 30)
        annotation.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        annotation.setZValue(0)
        scene.addItem(annotation)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), event)
        tool.mouse_move(QPointF(300, 300), event)
        tool.mouse_release(QPointF(300, 300), event)

        assert annotation.isSelected(), (
            "Annotation inside band should be selected"
        )
        assert not bg.isSelected(), (
            "Background item should NOT be selected by rubber band"
        )


# ---------------------------------------------------------------------------
# SelectTool — select_all
# ---------------------------------------------------------------------------


class TestSelectToolSelectAll:
    def test_select_all_selects_annotations(self, qapp) -> None:
        """select_all should select all annotation items."""
        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        item1 = QGraphicsRectItem(0, 0, 50, 50)
        item1.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        item1.setZValue(0)
        scene.addItem(item1)

        item2 = QGraphicsEllipseItem(60, 60, 40, 40)
        item2.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable)
        item2.setZValue(0)
        scene.addItem(item2)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.select_all()

        assert item1.isSelected(), (
            "Expected item1 to be selected by select_all"
        )
        assert item2.isSelected(), (
            "Expected item2 to be selected by select_all"
        )

    def test_select_all_ignores_background(self, qapp) -> None:
        """select_all should not select background items."""
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)

        annotation = QGraphicsRectItem(10, 10, 30, 30)
        annotation.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        annotation.setZValue(0)
        scene.addItem(annotation)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.select_all()

        assert annotation.isSelected(), (
            "Annotation should be selected by select_all"
        )
        assert not bg.isSelected(), (
            "Background should NOT be selected by select_all"
        )

    def test_select_all_ignores_boundary(self, qapp) -> None:
        """select_all should not select boundary items."""
        from verdiclip.editor import Z_BOUNDARY

        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        boundary = QGraphicsRectItem(0, 0, 200, 200)
        boundary.setZValue(Z_BOUNDARY)
        scene.addItem(boundary)

        annotation = QGraphicsRectItem(10, 10, 30, 30)
        annotation.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        annotation.setZValue(0)
        scene.addItem(annotation)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.select_all()

        assert annotation.isSelected(), (
            "Annotation should be selected by select_all"
        )
        assert not boundary.isSelected(), (
            "Boundary should NOT be selected by select_all"
        )

    def test_select_all_noop_without_scene(self, qapp) -> None:
        """select_all should be a no-op when tool has no scene."""
        tool = SelectTool()
        tool.select_all()  # Should not raise


# ---------------------------------------------------------------------------
# SelectTool — Ctrl+click multi-select
# ---------------------------------------------------------------------------


class TestSelectToolCtrlClick:
    def test_ctrl_click_adds_to_selection(self, qapp) -> None:
        """Ctrl+click should add items to the existing selection."""
        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        item1 = QGraphicsRectItem(0, 0, 50, 50)
        item1.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item1)

        item2 = QGraphicsRectItem(100, 100, 50, 50)
        item2.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item2)

        tool = SelectTool()
        tool.activate(scene, view)

        # Select first item normally
        event1 = _make_mouse_event()
        tool.mouse_press(QPointF(25, 25), event1)
        tool.mouse_release(QPointF(25, 25), event1)

        assert item1.isSelected(), "Precondition: item1 should be selected"

        # Ctrl+click on second item
        ctrl_event = _make_mouse_event(
            modifiers=Qt.KeyboardModifier.ControlModifier,
        )
        tool.mouse_press(QPointF(125, 125), ctrl_event)

        assert item1.isSelected(), (
            "Expected item1 to remain selected during Ctrl+click"
        )
        assert item2.isSelected(), (
            "Expected item2 to be added to selection on Ctrl+click"
        )

    def test_click_without_ctrl_deselects_others(self, qapp) -> None:
        """Normal click (no Ctrl) should deselect existing items."""
        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        item1 = QGraphicsRectItem(0, 0, 50, 50)
        item1.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item1)

        item2 = QGraphicsRectItem(100, 100, 50, 50)
        item2.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item2)

        tool = SelectTool()
        tool.activate(scene, view)

        # Select both via direct API
        item1.setSelected(True)
        item2.setSelected(True)

        # Normal click on item1 — should deselect item2
        event = _make_mouse_event()
        tool.mouse_press(QPointF(25, 25), event)

        assert item1.isSelected(), "Clicked item should be selected"
        assert not item2.isSelected(), (
            "Other item should be deselected on normal click (no Ctrl)"
        )


# ---------------------------------------------------------------------------
# ObfuscationItem — set_geometry (atomic position+size)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ObfuscateTool — extending past image bounds
# ---------------------------------------------------------------------------


class TestObfuscateToolExtendsPastImageBounds:
    def test_drag_past_top_left_allows_negative_position(self, qapp) -> None:
        """Dragging from inside to past top-left should allow negative coordinates."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(50, 50), QPointF(-20, -20))

        overlays = [i for i in scene.items() if isinstance(i, ObfuscationItem)]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem, got {len(overlays)}"
        )
        item = overlays[0]
        assert item.pos().x() < 0, (
            f"Expected negative pos.x when dragged past top-left, got {item.pos().x()}"
        )
        assert item.pos().y() < 0, (
            f"Expected negative pos.y when dragged past top-left, got {item.pos().y()}"
        )

    def test_drag_past_top_left_preserves_full_size(self, qapp) -> None:
        """Size should reflect the full drag area (70x70 from (50,50) to (-20,-20))."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(50, 50), QPointF(-20, -20))

        overlays = [i for i in scene.items() if isinstance(i, ObfuscationItem)]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem, got {len(overlays)}"
        )
        item = overlays[0]
        assert abs(item._size.width() - 70) < 1, (
            f"Expected _size.width ~70 (full drag), got {item._size.width()}"
        )
        assert abs(item._size.height() - 70) < 1, (
            f"Expected _size.height ~70 (full drag), got {item._size.height()}"
        )

    def test_drag_past_bottom_right_allows_extended_size(self, qapp) -> None:
        """Dragging past bottom-right should create full-size item."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(60, 60), QPointF(150, 150))

        overlays = [i for i in scene.items() if isinstance(i, ObfuscationItem)]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem, got {len(overlays)}"
        )
        item = overlays[0]
        assert abs(item._size.width() - 90) < 1, (
            f"Expected _size.width ~90 (full drag), got {item._size.width()}"
        )
        assert abs(item._size.height() - 90) < 1, (
            f"Expected _size.height ~90 (full drag), got {item._size.height()}"
        )

    def test_pixmap_offset_when_extending_past_top(self, qapp) -> None:
        """When item extends past top-left, pixmap offset aligns with image region."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(50, 50), QPointF(-20, -20))

        overlays = [i for i in scene.items() if isinstance(i, ObfuscationItem)]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem, got {len(overlays)}"
        )
        item = overlays[0]
        # The item starts at (-20, -20), so the pixmap offset should be (20, 20)
        # to align with the image starting at (0, 0)
        assert item.offset().x() > 0, (
            f"Expected positive pixmap x offset, got {item.offset().x()}"
        )
        assert item.offset().y() > 0, (
            f"Expected positive pixmap y offset, got {item.offset().y()}"
        )

    def test_fully_outside_image_creates_empty_obfuscation(self, qapp) -> None:
        """Dragging entirely outside the image creates an item with empty pixmap."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(-50, -50), QPointF(-10, -10))

        overlays = [i for i in scene.items() if isinstance(i, ObfuscationItem)]
        assert len(overlays) == 1, (
            f"Expected 1 ObfuscationItem for out-of-bounds drag, got {len(overlays)}"
        )
        item = overlays[0]
        assert item.pixmap().isNull(), (
            "Expected null pixmap for obfuscation fully outside image bounds"
        )


# ---------------------------------------------------------------------------
# CropTool — clamping to image bounds
# ---------------------------------------------------------------------------


class TestCropToolClampToImageBounds:
    def test_crop_past_bottom_right_clamps_to_image_size(self, qapp) -> None:
        """Crop rect extending beyond image is clamped to image bounds."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(150, 150), event)
        tool.mouse_release(QPointF(150, 150), event)

        # After crop, the resulting background should be 90x90 (clamped from 140x140)
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) >= 1, (
            f"Expected at least 1 pixmap item after crop, got {len(pixmap_items)}"
        )
        bg_item = [i for i in pixmap_items if i.zValue() <= -1000][0]
        w = bg_item.pixmap().width()
        h = bg_item.pixmap().height()
        assert w == 90, (
            f"Expected cropped width 90 (clamped to image), got {w}"
        )
        assert h == 90, (
            f"Expected cropped height 90 (clamped to image), got {h}"
        )

    def test_crop_past_top_left_clamps_to_zero(self, qapp) -> None:
        """Crop starting before (0,0) is clamped to image origin."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(-20, -20), event)
        tool.mouse_move(QPointF(50, 50), event)
        tool.mouse_release(QPointF(50, 50), event)

        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        bg_item = [i for i in pixmap_items if i.zValue() <= -1000][0]
        w = bg_item.pixmap().width()
        h = bg_item.pixmap().height()
        assert w == 50, (
            f"Expected cropped width 50 (clamped at origin), got {w}"
        )
        assert h == 50, (
            f"Expected cropped height 50 (clamped at origin), got {h}"
        )

    def test_crop_entirely_outside_does_nothing(self, qapp) -> None:
        """Crop rect entirely outside the image should not change the background."""
        scene, bg = _make_scene_with_background(100, 100)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(-50, -50), event)
        tool.mouse_move(QPointF(-10, -10), event)
        tool.mouse_release(QPointF(-10, -10), event)

        # Background should be unchanged — 100x100
        pixmap_items = [
            i for i in scene.items()
            if isinstance(i, QGraphicsPixmapItem) and i.zValue() <= -1000
        ]
        assert len(pixmap_items) == 1, (
            f"Expected 1 background pixmap (unchanged), got {len(pixmap_items)}"
        )
        assert pixmap_items[0].pixmap().width() == 100, (
            f"Expected unchanged width 100, got {pixmap_items[0].pixmap().width()}"
        )


class TestObfuscationItemSetGeometry:
    def test_set_geometry_updates_position_and_size(self, qapp) -> None:
        """set_geometry should update both pos and size atomically."""
        from PySide6.QtCore import QSizeF

        scene, bg = _make_scene_with_background(200, 200)
        item = ObfuscationItem(bg, QSizeF(40, 40))
        item.setPos(10, 10)
        scene.addItem(item)

        new_pos = QPointF(50, 60)
        new_size = QSizeF(80, 70)
        item.set_geometry(new_pos, new_size)

        assert abs(item.pos().x() - 50) < 1, (
            f"Expected pos.x ~50 after set_geometry, got {item.pos().x()}"
        )
        assert abs(item.pos().y() - 60) < 1, (
            f"Expected pos.y ~60 after set_geometry, got {item.pos().y()}"
        )
        assert item._size.width() == 80, (
            f"Expected _size.width 80, got {item._size.width()}"
        )
        assert item._size.height() == 70, (
            f"Expected _size.height 70, got {item._size.height()}"
        )
        assert not item.pixmap().isNull(), (
            "Expected non-null pixmap after set_geometry"
        )

    def test_set_geometry_prevents_double_refresh(self, qapp) -> None:
        """_updating_geometry flag should prevent extra refresh from itemChange."""
        from PySide6.QtCore import QSizeF

        scene, bg = _make_scene_with_background(200, 200)
        item = ObfuscationItem(bg, QSizeF(40, 40))
        item.setPos(10, 10)
        scene.addItem(item)

        refresh_calls = []
        original_refresh = item._refresh_pixelation

        def counting_refresh():
            refresh_calls.append(1)
            original_refresh()

        item._refresh_pixelation = counting_refresh

        item.set_geometry(QPointF(50, 60), QSizeF(80, 70))

        assert len(refresh_calls) == 1, (
            f"Expected exactly 1 refresh call during set_geometry, "
            f"got {len(refresh_calls)}"
        )

    def test_updating_geometry_flag_reset_after_set_geometry(
        self, qapp,
    ) -> None:
        """_updating_geometry should be False after set_geometry completes."""
        from PySide6.QtCore import QSizeF

        scene, bg = _make_scene_with_background(200, 200)
        item = ObfuscationItem(bg, QSizeF(40, 40))
        item.setPos(10, 10)
        scene.addItem(item)

        item.set_geometry(QPointF(50, 60), QSizeF(80, 70))

        assert item._updating_geometry is False, (
            f"Expected _updating_geometry to be False after set_geometry, "
            f"got {item._updating_geometry}"
        )


# ---------------------------------------------------------------------------
# SelectTool — _find_annotation_at skips boundary items
# ---------------------------------------------------------------------------


class TestSelectToolFindAnnotationAt:
    """Test that _find_annotation_at correctly skips non-annotation items."""

    def test_find_annotation_at_skips_boundary_rect(self, qapp) -> None:
        """Regression: boundary rect (z=Z_BOUNDARY) intercepted itemAt, preventing
        annotation selection. _find_annotation_at should skip it."""
        from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY

        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        # Background
        bg = QGraphicsRectItem(0, 0, 200, 200)
        bg.setZValue(Z_BACKGROUND)
        scene.addItem(bg)

        # Boundary rect covers entire image area (highest z below crop overlay)
        boundary = QGraphicsRectItem(0, 0, 200, 200)
        boundary.setZValue(Z_BOUNDARY)
        scene.addItem(boundary)

        # Annotation underneath
        annotation = QGraphicsRectItem(10, 10, 30, 30)
        annotation.setZValue(0)
        annotation.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(annotation)

        tool = SelectTool()
        tool.activate(scene, view)

        found = tool._find_annotation_at(QPointF(25, 25))
        assert found is annotation, (
            f"Expected _find_annotation_at to return the annotation, "
            f"got {found} (boundary rect is intercepting)"
        )

    def test_find_annotation_at_returns_none_on_empty(self, qapp) -> None:
        """_find_annotation_at should return None when no annotation is at the point."""
        from verdiclip.editor import Z_BACKGROUND, Z_BOUNDARY

        scene = QGraphicsScene()
        view = QGraphicsView(scene)

        bg = QGraphicsRectItem(0, 0, 200, 200)
        bg.setZValue(Z_BACKGROUND)
        scene.addItem(bg)

        boundary = QGraphicsRectItem(0, 0, 200, 200)
        boundary.setZValue(Z_BOUNDARY)
        scene.addItem(boundary)

        tool = SelectTool()
        tool.activate(scene, view)

        found = tool._find_annotation_at(QPointF(100, 100))
        assert found is None, (
            f"Expected None when no annotation at position, got {found}"
        )

    def test_find_annotation_at_returns_none_without_scene(self, qapp) -> None:
        """_find_annotation_at should return None when tool has no scene."""
        tool = SelectTool()
        found = tool._find_annotation_at(QPointF(50, 50))
        assert found is None, (
            "Expected None when tool has no scene"
        )


# ---------------------------------------------------------------------------
# Crop undo — position restoration
# ---------------------------------------------------------------------------


class TestCropUndoPositionRestoration:
    """Test that crop undo restores annotation positions correctly."""

    def test_crop_undo_restores_annotation_positions(self, qapp) -> None:
        """Regression: crop undo shifted elements because positions weren't restored."""
        from verdiclip.editor.canvas import EditorCanvas
        from verdiclip.editor.history import EditorHistory

        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.blue)
        canvas.set_image(pixmap)
        history = EditorHistory()
        canvas.set_history(history)

        # Add annotation at (30, 40)
        annotation = QGraphicsRectItem(0, 0, 20, 20)
        annotation.setPos(30, 40)
        canvas.scene.addItem(annotation)

        # Crop from (10, 10) to (90, 90) — annotation moves to (20, 30)
        cropped = QPixmap(80, 80)
        cropped.fill(Qt.GlobalColor.red)
        item_positions = [(annotation, 30.0, 40.0)]
        canvas.crop_undoable(cropped, [], item_positions, (10.0, 10.0))
        annotation.setPos(20, 30)  # Simulate the shift that crop tool does

        # Undo should restore to original position (30, 40)
        history.undo()

        assert abs(annotation.pos().x() - 30) < 1, (
            f"Expected annotation x ~30 after crop undo, got {annotation.pos().x()}"
        )
        assert abs(annotation.pos().y() - 40) < 1, (
            f"Expected annotation y ~40 after crop undo, got {annotation.pos().y()}"
        )

    def test_crop_redo_reapplies_position_offset(self, qapp) -> None:
        """Crop redo should re-shift annotation positions by the crop offset."""
        from verdiclip.editor.canvas import EditorCanvas
        from verdiclip.editor.history import EditorHistory

        canvas = EditorCanvas()
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.blue)
        canvas.set_image(pixmap)
        history = EditorHistory()
        canvas.set_history(history)

        annotation = QGraphicsRectItem(0, 0, 20, 20)
        annotation.setPos(30, 40)
        canvas.scene.addItem(annotation)

        cropped = QPixmap(80, 80)
        cropped.fill(Qt.GlobalColor.red)
        item_positions = [(annotation, 30.0, 40.0)]
        canvas.crop_undoable(cropped, [], item_positions, (10.0, 10.0))
        annotation.setPos(20, 30)

        history.undo()  # restores to (30, 40)
        history.redo()  # should shift back to (20, 30)

        assert abs(annotation.pos().x() - 20) < 1, (
            f"Expected annotation x ~20 after crop redo, got {annotation.pos().x()}"
        )
        assert abs(annotation.pos().y() - 30) < 1, (
            f"Expected annotation y ~30 after crop redo, got {annotation.pos().y()}"
        )


# ---------------------------------------------------------------------------
# ObfuscationItem — bounding rect uses full size
# ---------------------------------------------------------------------------


class TestObfuscationItemBoundingRect:
    """Test that ObfuscationItem's boundingRect reflects the full _size."""

    def test_bounding_rect_matches_full_size(self, qapp) -> None:
        """boundingRect should cover the full item size, not just the pixmap area."""
        from PySide6.QtCore import QSizeF

        scene, bg = _make_scene_with_background(100, 100)
        item = ObfuscationItem(bg, QSizeF(80, 60))
        item.setPos(-20, -10)
        item.set_show_border(False)  # Exclude border pen from rect
        scene.addItem(item)

        rect = item.boundingRect()
        assert abs(rect.width() - 80) < 1, (
            f"Expected boundingRect width ~80, got {rect.width()}"
        )
        assert abs(rect.height() - 60) < 1, (
            f"Expected boundingRect height ~60, got {rect.height()}"
        )


# ---------------------------------------------------------------------------
# SelectTool — handle management
# ---------------------------------------------------------------------------


class TestSelectToolHandles:
    def test_update_selection_handles_adds_handles(self, qapp) -> None:
        from verdiclip.editor.tools.handles import ResizeHandle  # noqa: PLC0415

        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        item = QGraphicsRectItem(0, 0, 100, 80)
        item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.update_selection_handles([item])

        assert len(tool._handles) == 8, f"Expected 8 handles for rect, got {len(tool._handles)}"
        for h in tool._handles:
            assert isinstance(h, ResizeHandle), "Expected handle to be a ResizeHandle"
            assert h.scene() is scene, "Handle not added to scene"

    def test_clear_handles_removes_from_scene(self, qapp) -> None:
        from verdiclip.editor.tools.handles import ResizeHandle  # noqa: PLC0415

        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        item = QGraphicsRectItem(0, 0, 100, 80)
        scene.addItem(item)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.update_selection_handles([item])
        assert len(tool._handles) == 8

        tool.clear_handles()
        assert tool._handles == [], "Expected handles list to be empty after clear"
        for scene_item in scene.items():
            assert not isinstance(scene_item, ResizeHandle), "Stale handle left in scene"

    def test_no_handles_for_multi_selection(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        a = QGraphicsRectItem(0, 0, 50, 50)
        b = QGraphicsRectItem(60, 0, 50, 50)
        scene.addItem(a)
        scene.addItem(b)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.update_selection_handles([a, b])

        assert tool._handles == [], "Expected no handles for multi-selection (2 items)"

    def test_no_handles_for_text_item(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        item = QGraphicsTextItem("hello")
        scene.addItem(item)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.update_selection_handles([item])

        assert tool._handles == [], "Expected no handles for unsupported item type (text)"

    def test_deactivate_clears_handles(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        item = QGraphicsRectItem(0, 0, 100, 80)
        scene.addItem(item)

        tool = SelectTool()
        tool.activate(scene, view)
        tool.update_selection_handles([item])
        assert len(tool._handles) == 8

        tool.deactivate()
        assert tool._handles == [], "Expected handles cleared on deactivate"

