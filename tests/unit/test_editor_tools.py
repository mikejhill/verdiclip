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
from verdiclip.editor.tools.obfuscate import ObfuscateTool
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

        assert tool._scene is scene
        assert tool._view is view
        assert tool._is_active is True

    def test_deactivate_clears_active_flag(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        tool.activate(scene, view)
        tool.deactivate()

        assert tool._is_active is False


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
        assert len(rects) == 1

    def test_rect_dimensions(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 20), QPointF(110, 80))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        rect = rect_item.rect()
        assert abs(rect.width() - 100) < 1
        assert abs(rect.height() - 60) < 1

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
        assert abs(rect.width() - rect.height()) < 1

    def test_custom_stroke_and_fill_colors(self, qapp) -> None:
        stroke = QColor("#00FF00")
        fill = QColor(0, 0, 255, 128)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool(stroke_color=stroke, fill_color=fill)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.pen().color().name() == stroke.name()
        assert rect_item.brush().color().alpha() == fill.alpha()

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0

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
        assert len(rects) == 0

    def test_set_stroke_color(self, qapp) -> None:
        tool = RectangleTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color

    def test_set_fill_color(self, qapp) -> None:
        tool = RectangleTool()
        new_color = QColor(255, 0, 0, 100)
        tool.set_fill_color(new_color)
        assert tool._fill_color == new_color

    def test_set_stroke_width(self, qapp) -> None:
        tool = RectangleTool()
        tool.set_stroke_width(5)
        assert tool._stroke_width == 5

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = RectangleTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        flags = rect_item.flags()
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable


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
        assert len(ellipses) == 1

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
        assert abs(rect.width() - rect.height()) < 1

    def test_custom_stroke_and_fill_colors(self, qapp) -> None:
        stroke = QColor("#00FF00")
        fill = QColor(0, 0, 255, 128)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool(stroke_color=stroke, fill_color=fill)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipse_item = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)][0]
        assert ellipse_item.pen().color().name() == stroke.name()
        assert ellipse_item.brush().color().alpha() == fill.alpha()

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 0

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        ellipses = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)]
        assert len(ellipses) == 0

    def test_set_stroke_color(self, qapp) -> None:
        tool = EllipseTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color

    def test_set_fill_color(self, qapp) -> None:
        tool = EllipseTool()
        new_color = QColor(255, 0, 0, 100)
        tool.set_fill_color(new_color)
        assert tool._fill_color == new_color

    def test_set_stroke_width(self, qapp) -> None:
        tool = EllipseTool()
        tool.set_stroke_width(7)
        assert tool._stroke_width == 7

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = EllipseTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        ellipse_item = [i for i in scene.items() if isinstance(i, QGraphicsEllipseItem)][0]
        flags = ellipse_item.flags()
        assert flags & QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsEllipseItem.GraphicsItemFlag.ItemIsMovable


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
        assert len(lines) == 1

    def test_line_endpoints(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(10, 20), QPointF(110, 80))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        line = line_item.line()
        assert abs(line.p1().x() - 10) < 1
        assert abs(line.p1().y() - 20) < 1
        assert abs(line.p2().x() - 110) < 1
        assert abs(line.p2().y() - 80) < 1

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
        assert abs(line.p2().x() - line.p2().y()) < 2

    def test_custom_stroke_color(self, qapp) -> None:
        stroke = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool(stroke_color=stroke)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        assert line_item.pen().color().name() == stroke.name()

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 0

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(lines) == 0

    def test_set_stroke_color(self, qapp) -> None:
        tool = LineTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color

    def test_set_stroke_width(self, qapp) -> None:
        tool = LineTool()
        tool.set_stroke_width(10)
        assert tool._stroke_width == 10

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = LineTool()
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_item = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)][0]
        flags = line_item.flags()
        assert flags & QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsLineItem.GraphicsItemFlag.ItemIsMovable


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
        assert len(lines) >= 1
        assert len(paths) >= 1

    def test_custom_stroke_color(self, qapp) -> None:
        stroke = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool(stroke_color=stroke)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_items = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(line_items) >= 1
        assert line_items[0].pen().color().name() == stroke.name()

    def test_custom_stroke_width(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool(stroke_width=5)
        _simulate_draw(tool, scene, view, QPointF(0, 0), QPointF(100, 100))

        line_items = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        assert len(line_items) >= 1
        assert line_items[0].pen().widthF() == 5

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(11, 11))

        # After discard, scene should have no line/path items remaining
        lines = [i for i in scene.items() if isinstance(i, QGraphicsLineItem)]
        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        groups = [i for i in scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(lines) == 0
        assert len(paths) == 0
        assert len(groups) == 0

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        assert len(scene.items()) == 0

    def test_set_stroke_color(self, qapp) -> None:
        tool = ArrowTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color

    def test_set_stroke_width(self, qapp) -> None:
        tool = ArrowTool()
        tool.set_stroke_width(8)
        assert tool._stroke_width == 8

    def test_group_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ArrowTool()
        tool.activate(scene, view)

        press_event = _make_mouse_event()
        tool.mouse_press(QPointF(0, 0), press_event)

        # Verify the group has correct flags before release
        assert tool._group is not None
        flags = tool._group.flags()
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable

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
        assert len(paths) == 1

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
        assert path_item.pen().color().name() == stroke.name()

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
        assert len(paths) == 0

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = FreehandTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        paths = [i for i in scene.items() if isinstance(i, QGraphicsPathItem)]
        assert len(paths) == 0

    def test_set_stroke_color(self, qapp) -> None:
        tool = FreehandTool()
        new_color = QColor("#0000FF")
        tool.set_stroke_color(new_color)
        assert tool._stroke_color == new_color

    def test_set_stroke_width(self, qapp) -> None:
        tool = FreehandTool()
        tool.set_stroke_width(4)
        assert tool._stroke_width == 4

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
        assert flags & QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsPathItem.GraphicsItemFlag.ItemIsMovable


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
        assert len(text_items) == 1
        assert text_items[0].pos().x() == 50
        assert text_items[0].pos().y() == 50

    def test_clicking_existing_text_enters_edit_mode(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        # Place a text item and give it content
        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        text_item = tool._active_item
        assert text_item is not None
        text_item.setPlainText("Hello")

        # Finalize (simulating clicking elsewhere), then click on the same item
        tool._finalize_text()

        # Click at the text item position — itemAt will find it
        tool.mouse_press(QPointF(50, 50), event)

        # The active item should be the existing text item (re-entered edit mode)
        assert tool._active_item is text_item
        assert text_item.textInteractionFlags() == Qt.TextInteractionFlag.TextEditorInteraction

    def test_deactivate_finalizes_text(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool._active_item.setPlainText("Test text")

        tool.deactivate()

        assert tool._active_item is None
        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 1
        assert text_items[0].textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction

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
        assert len(text_items) == 0

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
        assert len(text_items) == 0

    def test_set_color(self, qapp) -> None:
        tool = TextTool()
        new_color = QColor("#0000FF")
        tool.set_color(new_color)
        assert tool._color == new_color

    def test_set_font(self, qapp) -> None:
        tool = TextTool()
        new_font = QFont("Courier", 20)
        tool.set_font(new_font)
        assert tool._font.family() == "Courier"
        assert tool._font.pointSize() == 20

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(50, 50), event)

        text_items = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)]
        assert len(text_items) == 0

    def test_custom_color_applied_to_text(self, qapp) -> None:
        color = QColor("#00FF00")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool(color=color)
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        text_item = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)][0]
        assert text_item.defaultTextColor().name() == color.name()

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = TextTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        text_item = [i for i in scene.items() if isinstance(i, QGraphicsTextItem)][0]
        flags = text_item.flags()
        assert flags & QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable


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
        assert tool._counter == 1

        tool.mouse_press(QPointF(100, 100), event)
        tool.mouse_release(QPointF(100, 100), event)
        assert tool._counter == 2

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
        assert len(ellipses) >= 1
        assert len(texts) >= 1

    def test_reset_counter(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)
        tool.mouse_press(QPointF(100, 100), event)
        assert tool._counter == 2

        tool.reset_counter()
        assert tool._counter == 0

        tool.mouse_press(QPointF(150, 150), event)
        assert tool._counter == 1

    def test_set_bg_color(self, qapp) -> None:
        tool = NumberTool()
        new_color = QColor("#0000FF")
        tool.set_bg_color(new_color)
        assert tool._bg_color == new_color

    def test_set_text_color(self, qapp) -> None:
        tool = NumberTool()
        new_color = QColor("#FFFF00")
        tool.set_text_color(new_color)
        assert tool._text_color == new_color

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(50, 50), event)

        assert tool._counter == 0
        assert len(scene.items()) == 0

    def test_group_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = NumberTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        groups = [i for i in scene.items() if isinstance(i, QGraphicsItemGroup)]
        assert len(groups) >= 1
        flags = groups[0].flags()
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable


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
        assert len(rects) == 1

    def test_highlight_is_semi_transparent(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        brush_color = rect_item.brush().color()
        assert brush_color.alpha() < 255
        assert brush_color.alpha() > 0

    def test_highlight_has_no_pen(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.pen().style() == Qt.PenStyle.NoPen

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(12, 12))

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0

    def test_right_click_ignored(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        rects = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)]
        assert len(rects) == 0

    def test_set_color(self, qapp) -> None:
        tool = HighlightTool()
        new_color = QColor(0, 255, 0, 80)
        tool.set_color(new_color)
        assert tool._color == new_color

    def test_custom_color_applied(self, qapp) -> None:
        custom = QColor(0, 255, 0, 80)
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool(color=custom)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        assert rect_item.brush().color().green() == 255
        assert rect_item.brush().color().alpha() == 80

    def test_item_is_selectable_and_movable(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = HighlightTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(110, 110))

        rect_item = [i for i in scene.items() if isinstance(i, QGraphicsRectItem)][0]
        flags = rect_item.flags()
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable


# ---------------------------------------------------------------------------
# ObfuscateTool
# ---------------------------------------------------------------------------


class TestObfuscateTool:
    def test_pixelates_region_of_background(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        # Should have the background + a pixelated overlay
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 2  # bg + overlay

    def test_too_small_draw_discarded(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(12, 12))

        # Only background should remain
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 1

    def test_too_narrow_draw_discarded(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        # Width is ok (90) but height is too small (3)
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 13))

        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 1

    def test_no_background_does_nothing(self, qapp) -> None:
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        # No background means no pixelation overlay
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 0

    def test_pixelate_static_method(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor(200, 100, 50))

        result = ObfuscateTool._pixelate(pixmap, 10)

        assert result.width() == 100
        assert result.height() == 100

    def test_set_block_size(self, qapp) -> None:
        tool = ObfuscateTool()
        tool.set_block_size(20)
        assert tool._block_size == 20

    def test_set_block_size_clamped_low(self, qapp) -> None:
        tool = ObfuscateTool()
        tool.set_block_size(1)
        assert tool._block_size == 2

    def test_set_block_size_clamped_high(self, qapp) -> None:
        tool = ObfuscateTool()
        tool.set_block_size(100)
        assert tool._block_size == 64

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
        pixmap_items = [i for i in scene.items() if isinstance(i, QGraphicsPixmapItem)]
        assert len(pixmap_items) == 1

    def test_overlay_is_selectable_and_movable(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)
        tool = ObfuscateTool()
        _simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(100, 100))

        overlays = [
            i for i in scene.items()
            if isinstance(i, QGraphicsPixmapItem) and i is not bg
        ]
        overlay = overlays[0]
        flags = overlay.flags()
        assert flags & QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable
        assert flags & QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable


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

        assert tool._crop_rect_item is not None
        rect = tool._crop_rect_item.rect()
        assert rect.width() >= 10
        assert rect.height() >= 10

    def test_crop_rect_has_high_zvalue(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)

        assert tool._crop_rect_item is not None
        assert tool._crop_rect_item.zValue() == 9999

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
        assert len(pixmap_items) == 1
        assert pixmap_items[0].zValue() == -1000

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
        assert tool._crop_rect_item is None

    def test_cancel_crop_clears_ui(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(10, 10), event)
        tool.mouse_move(QPointF(200, 200), event)
        tool.mouse_release(QPointF(200, 200), event)

        assert tool._crop_rect_item is not None

        tool.cancel_crop()

        assert tool._crop_rect_item is None
        assert tool._origin is None

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

        assert tool._crop_rect_item is None
        assert tool._origin is None

    def test_right_click_ignored(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        event = _make_mouse_event(button=Qt.MouseButton.RightButton)
        tool.mouse_press(QPointF(10, 10), event)

        assert tool._crop_rect_item is None

    def test_apply_crop_without_selection_does_nothing(self, qapp) -> None:
        scene, bg = _make_scene_with_background(400, 400)
        view = QGraphicsView(scene)
        tool = CropTool()
        tool.activate(scene, view)

        items_before = len(scene.items())
        tool.apply_crop()
        items_after = len(scene.items())

        assert items_before == items_after

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
        assert tool._crop_rect_item is not None


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

        assert rect_item.isSelected()

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
        assert abs(rect_item.pos().x() - 60) < 1
        assert abs(rect_item.pos().y() - 60) < 1

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
        assert rect_item.isSelected()
        tool.mouse_release(QPointF(25, 25), event)

        # Click on empty space
        tool.mouse_press(QPointF(200, 200), event)
        assert not rect_item.isSelected()

    def test_ignores_background_item(self, qapp) -> None:
        scene, bg = _make_scene_with_background(200, 200)
        view = QGraphicsView(scene)

        tool = SelectTool()
        tool.activate(scene, view)

        event = _make_mouse_event()
        tool.mouse_press(QPointF(50, 50), event)

        # Background should NOT be selected (zValue <= -1000)
        assert not bg.isSelected()
        assert tool._dragging is False

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

        assert not rect_item.isSelected()

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
        assert tool._dragging is True

        tool.mouse_release(QPointF(25, 25), event)
        assert tool._dragging is False
        assert tool._drag_item is None
        assert tool._drag_start is None
        assert tool._item_start_pos is None

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

        assert rect_item.pos() == original_pos

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
        assert abs(rect_item.pos().x() - 60) < 1
        assert abs(rect_item.pos().y() - 80) < 1
