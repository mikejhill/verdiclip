"""Tests for editor serialization helpers (copy-paste round-trip)."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, QSizeF
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
)

from verdiclip.editor.serialization import (
    _apply_fill_to_item,
    _apply_stroke_to_item,
    _apply_width_to_item,
    _deserialise_items,
    _serialise_items,
)
from verdiclip.editor.tools.arrow import ArrowItem
from verdiclip.editor.tools.number import NumberMarkerItem
from verdiclip.editor.tools.obfuscate import ObfuscationItem

# ---------------------------------------------------------------------------
# Round-trip: serialise → deserialise
# ---------------------------------------------------------------------------


class TestRectRoundTrip:
    def test_rect_round_trip(self, qapp) -> None:
        item = QGraphicsRectItem(QRectF(5, 10, 80, 40))
        item.setPen(QPen(QColor("#FF0000"), 3))
        item.setBrush(QBrush(QColor(0, 255, 0, 128)))
        item.setPos(QPointF(20, 30))

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "rect"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        r = restored[0]
        assert isinstance(r, QGraphicsRectItem)
        assert r.rect().width() == 80
        assert r.rect().height() == 40
        assert r.pen().color().name() == "#ff0000"
        assert r.pos() == QPointF(20, 30)


class TestEllipseRoundTrip:
    def test_ellipse_round_trip(self, qapp) -> None:
        item = QGraphicsEllipseItem(QRectF(0, 0, 60, 60))
        item.setPen(QPen(QColor("#00FF00"), 2))
        item.setBrush(QBrush(QColor(255, 0, 0, 200)))
        item.setPos(QPointF(10, 15))

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "ellipse"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        e = restored[0]
        assert isinstance(e, QGraphicsEllipseItem)
        assert e.rect().width() == 60
        assert e.pen().color().name() == "#00ff00"
        assert e.pos() == QPointF(10, 15)


class TestLineRoundTrip:
    def test_line_round_trip(self, qapp) -> None:
        item = QGraphicsLineItem(QLineF(QPointF(0, 0), QPointF(100, 50)))
        item.setPen(QPen(QColor("#0000FF"), 4))
        item.setPos(QPointF(5, 5))

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "line"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        ln = restored[0]
        assert isinstance(ln, QGraphicsLineItem)
        assert ln.line().p2().x() == 100
        assert ln.line().p2().y() == 50
        assert ln.pen().color().name() == "#0000ff"


class TestTextRoundTrip:
    def test_text_round_trip(self, qapp) -> None:
        item = QGraphicsTextItem()
        item.setPlainText("Hello, VerdiClip!")
        item.setDefaultTextColor(QColor("#333333"))
        item.setFont(QFont("Arial", 14))
        item.setPos(QPointF(50, 50))

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "text"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        t = restored[0]
        assert isinstance(t, QGraphicsTextItem)
        assert "Hello, VerdiClip!" in t.toPlainText()
        assert t.defaultTextColor().name() == "#333333"
        assert t.pos() == QPointF(50, 50)


class TestArrowRoundTrip:
    def test_arrow_round_trip(self, qapp) -> None:
        scene = QGraphicsScene()
        item = ArrowItem(QPointF(0, 0), QPointF(80, 40), QColor("#FF00FF"), 3)
        scene.addItem(item)

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "arrow"
        assert data[0]["color"] == "#ff00ff"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        a = restored[0]
        assert isinstance(a, ArrowItem)
        assert a._stroke_color.name() == "#ff00ff"
        assert a._stroke_width == 3


class TestObfuscationRoundTrip:
    def test_obfuscation_round_trip(self, qapp) -> None:
        bg_pixmap = QPixmap(200, 200)
        bg_pixmap.fill(QColor(100, 100, 100))
        bg_item = QGraphicsPixmapItem(bg_pixmap)
        scene = QGraphicsScene()
        scene.addItem(bg_item)

        item = ObfuscationItem(bg_item, QSizeF(100, 50))
        item.setPos(QPointF(10, 20))
        scene.addItem(item)

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "obfuscation"
        assert data[0]["w"] == 100
        assert data[0]["h"] == 50

        restored = _deserialise_items(data)
        assert len(restored) == 1
        assert isinstance(restored[0], ObfuscationItem)


class TestNumberMarkerRoundTrip:
    def test_number_marker_round_trip(self, qapp) -> None:
        item = NumberMarkerItem("5", QColor("#FF0000"), QColor("#FFFFFF"))
        item.setPos(QPointF(30, 40))

        data = _serialise_items([item])
        assert len(data) == 1
        assert data[0]["type"] == "number"
        assert data[0]["value"] == "5"

        restored = _deserialise_items(data)
        assert len(restored) == 1
        n = restored[0]
        assert isinstance(n, NumberMarkerItem)
        assert n.value == "5"


# ---------------------------------------------------------------------------
# Multi-item serialisation
# ---------------------------------------------------------------------------


class TestMultiItemRoundTrip:
    def test_mixed_items_round_trip(self, qapp) -> None:
        scene = QGraphicsScene()
        rect = QGraphicsRectItem(QRectF(0, 0, 50, 30))
        rect.setPen(QPen(QColor("#111111"), 1))
        rect.setBrush(QBrush(QColor(0, 0, 0, 0)))
        scene.addItem(rect)

        line = QGraphicsLineItem(QLineF(QPointF(0, 0), QPointF(40, 40)))
        line.setPen(QPen(QColor("#222222"), 2))
        scene.addItem(line)

        text = QGraphicsTextItem()
        text.setPlainText("test")
        text.setDefaultTextColor(QColor("#000000"))
        text.setFont(QFont("Courier", 10))
        scene.addItem(text)

        data = _serialise_items([rect, line, text])
        assert len(data) == 3

        restored = _deserialise_items(data)
        assert len(restored) == 3
        types = {type(i).__name__ for i in restored}
        assert "QGraphicsRectItem" in types
        assert "QGraphicsLineItem" in types
        assert "QGraphicsTextItem" in types

    def test_empty_list_round_trip(self, qapp) -> None:
        data = _serialise_items([])
        assert data == []
        restored = _deserialise_items(data)
        assert restored == []


# ---------------------------------------------------------------------------
# _deserialise_items edge cases
# ---------------------------------------------------------------------------


class TestDeserialiseEdgeCases:
    def test_unknown_type_skipped(self, qapp) -> None:
        data = [{"type": "unknown_widget", "pos_x": 0, "pos_y": 0}]
        restored = _deserialise_items(data)
        assert len(restored) == 0, "Unknown types should be silently skipped"

    def test_missing_pos_defaults_to_origin(self, qapp) -> None:
        data = [
            {
                "type": "rect",
                "x": 0,
                "y": 0,
                "w": 50,
                "h": 50,
                "pen_color": "#000000",
                "pen_width": 1,
                "brush_color": "#00000000",
            }
        ]
        restored = _deserialise_items(data)
        assert len(restored) == 1
        assert restored[0].pos() == QPointF(0, 0)


# ---------------------------------------------------------------------------
# _apply_* helpers
# ---------------------------------------------------------------------------


class TestApplyStrokeToItem:
    def test_rect_stroke(self, qapp) -> None:
        item = QGraphicsRectItem(QRectF(0, 0, 50, 50))
        item.setPen(QPen(QColor("#000000"), 2))
        _apply_stroke_to_item(item, QColor("#FF0000"))
        assert item.pen().color().name() == "#ff0000"

    def test_ellipse_stroke(self, qapp) -> None:
        item = QGraphicsEllipseItem(QRectF(0, 0, 50, 50))
        item.setPen(QPen(QColor("#000000"), 2))
        _apply_stroke_to_item(item, QColor("#00FF00"))
        assert item.pen().color().name() == "#00ff00"

    def test_line_stroke(self, qapp) -> None:
        item = QGraphicsLineItem(QLineF(QPointF(0, 0), QPointF(50, 50)))
        item.setPen(QPen(QColor("#000000"), 2))
        _apply_stroke_to_item(item, QColor("#0000FF"))
        assert item.pen().color().name() == "#0000ff"

    def test_text_stroke_sets_default_color(self, qapp) -> None:
        item = QGraphicsTextItem("test")
        _apply_stroke_to_item(item, QColor("#ABCDEF"))
        assert item.defaultTextColor().name() == "#abcdef"

    def test_arrow_stroke(self, qapp) -> None:
        scene = QGraphicsScene()
        item = ArrowItem(QPointF(0, 0), QPointF(50, 50), QColor("#000000"), 2)
        scene.addItem(item)
        _apply_stroke_to_item(item, QColor("#FF00FF"))
        assert item._stroke_color.name() == "#ff00ff"


class TestApplyFillToItem:
    def test_rect_fill(self, qapp) -> None:
        item = QGraphicsRectItem(QRectF(0, 0, 50, 50))
        _apply_fill_to_item(item, QColor(255, 0, 0, 128))
        assert item.brush().color().red() == 255
        assert item.brush().color().alpha() == 128

    def test_ellipse_fill(self, qapp) -> None:
        item = QGraphicsEllipseItem(QRectF(0, 0, 50, 50))
        _apply_fill_to_item(item, QColor(0, 255, 0, 64))
        assert item.brush().color().green() == 255
        assert item.brush().color().alpha() == 64

    def test_line_fill_is_noop(self, qapp) -> None:
        item = QGraphicsLineItem(QLineF(QPointF(0, 0), QPointF(50, 50)))
        _apply_fill_to_item(item, QColor(255, 0, 0))
        # Lines have no brush — this should not raise


class TestApplyWidthToItem:
    def test_rect_width(self, qapp) -> None:
        item = QGraphicsRectItem(QRectF(0, 0, 50, 50))
        item.setPen(QPen(QColor("#000000"), 1))
        _apply_width_to_item(item, 8)
        assert item.pen().width() == 8

    def test_line_width(self, qapp) -> None:
        item = QGraphicsLineItem(QLineF(QPointF(0, 0), QPointF(50, 50)))
        item.setPen(QPen(QColor("#000000"), 1))
        _apply_width_to_item(item, 5)
        assert item.pen().width() == 5

    def test_arrow_width(self, qapp) -> None:
        scene = QGraphicsScene()
        item = ArrowItem(QPointF(0, 0), QPointF(50, 50), QColor("#000000"), 2)
        scene.addItem(item)
        _apply_width_to_item(item, 6)
        assert item._stroke_width == 6
