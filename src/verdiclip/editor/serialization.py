"""Element serialisation helpers for copy-paste."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor

# ---------------------------------------------------------------------------
# Element serialisation helpers (for copy-paste)
# ---------------------------------------------------------------------------

def _serialise_items(items: list) -> list[dict]:
    """Convert scene items into serialisable dicts for the internal clipboard."""
    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    result: list[dict] = []
    for item in items:
        data: dict = {"pos_x": item.pos().x(), "pos_y": item.pos().y()}

        try:
            from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
            if isinstance(item, ArrowItem):
                data["type"] = "arrow"
                data["p1_x"] = item.shaft_line.p1().x()
                data["p1_y"] = item.shaft_line.p1().y()
                data["p2_x"] = item.shaft_line.p2().x()
                data["p2_y"] = item.shaft_line.p2().y()
                data["color"] = item._stroke_color.name()
                data["width"] = item._stroke_width
                result.append(data)
                continue
        except ImportError:
            pass

        try:
            from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
            if isinstance(item, ObfuscationItem):
                data["type"] = "obfuscation"
                data["w"] = item._size.width()
                data["h"] = item._size.height()
                result.append(data)
                continue
        except ImportError:
            pass

        try:
            from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
            if isinstance(item, NumberMarkerItem):
                data["type"] = "number"
                data["value"] = item.value
                data["bg_color"] = item._bg_color.name()
                data["text_color"] = item._text_color.name()
                r = item.rect()
                data["radius"] = r.width() / 2.0
                result.append(data)
                continue
        except ImportError:
            pass

        if isinstance(item, QGraphicsRectItem):
            data["type"] = "rect"
            r = item.rect()
            data["x"] = r.x()
            data["y"] = r.y()
            data["w"] = r.width()
            data["h"] = r.height()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            data["brush_color"] = item.brush().color().name(QColor.NameFormat.HexArgb)
            result.append(data)
        elif isinstance(item, QGraphicsEllipseItem):
            data["type"] = "ellipse"
            r = item.rect()
            data["x"] = r.x()
            data["y"] = r.y()
            data["w"] = r.width()
            data["h"] = r.height()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            data["brush_color"] = item.brush().color().name(QColor.NameFormat.HexArgb)
            result.append(data)
        elif isinstance(item, QGraphicsLineItem):
            data["type"] = "line"
            ln = item.line()
            data["x1"] = ln.p1().x()
            data["y1"] = ln.p1().y()
            data["x2"] = ln.p2().x()
            data["y2"] = ln.p2().y()
            data["pen_color"] = item.pen().color().name()
            data["pen_width"] = item.pen().widthF()
            result.append(data)
        elif isinstance(item, QGraphicsTextItem):
            data["type"] = "text"
            data["html"] = item.toHtml()
            data["color"] = item.defaultTextColor().name()
            f = item.font()
            data["font_family"] = f.family()
            data["font_size"] = f.pointSize()
            result.append(data)
    return result


def _deserialise_items(data_list: list[dict]) -> list:
    """Reconstruct scene items from serialised dicts."""
    from PySide6.QtCore import QLineF, QPointF, QRectF  # noqa: PLC0415
    from PySide6.QtGui import QFont, QPen  # noqa: PLC0415
    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsPixmapItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    items: list = []
    for d in data_list:
        t = d.get("type")
        pos = QPointF(d.get("pos_x", 0), d.get("pos_y", 0))

        if t == "arrow":
            from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
            item = ArrowItem(
                QPointF(d["p1_x"], d["p1_y"]),
                QPointF(d["p2_x"], d["p2_y"]),
                QColor(d["color"]),
                d["width"],
            )
            item.setPos(pos)
            items.append(item)

        elif t == "obfuscation":
            from PySide6.QtCore import QSizeF  # noqa: PLC0415

            from verdiclip.editor.tools.obfuscate import ObfuscationItem  # noqa: PLC0415
            size = QSizeF(d["w"], d["h"])
            # bg_item is supplied post-deserialisation when added to a scene;
            # use a 1×1 transparent placeholder so the constructor succeeds.
            placeholder = QGraphicsPixmapItem()
            item = ObfuscationItem(placeholder, size)
            item.setPos(pos)
            items.append(item)

        elif t == "number":
            from verdiclip.editor.tools.number import NumberMarkerItem  # noqa: PLC0415
            item = NumberMarkerItem(d["value"], QColor(d["bg_color"]), QColor(d["text_color"]))
            r = d.get("radius", 16)
            item.setRect(QRectF(-r, -r, 2 * r, 2 * r))
            item._center_text()
            item.setPos(pos)
            items.append(item)

        elif t == "rect":
            item = QGraphicsRectItem(QRectF(d["x"], d["y"], d["w"], d["h"]))
            pen = QPen(QColor(d["pen_color"]), d["pen_width"])
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(d["brush_color"])))
            item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "ellipse":
            item = QGraphicsEllipseItem(QRectF(d["x"], d["y"], d["w"], d["h"]))
            item.setPen(QPen(QColor(d["pen_color"]), d["pen_width"]))
            item.setBrush(QBrush(QColor(d["brush_color"])))
            item.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "line":
            item = QGraphicsLineItem(QLineF(
                QPointF(d["x1"], d["y1"]), QPointF(d["x2"], d["y2"]),
            ))
            item.setPen(QPen(QColor(d["pen_color"]), d["pen_width"]))
            item.setFlag(QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

        elif t == "text":
            item = QGraphicsTextItem()
            item.setHtml(d["html"])
            item.setDefaultTextColor(QColor(d["color"]))
            item.setFont(QFont(d["font_family"], d["font_size"]))
            item.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable)
            item.setPos(pos)
            items.append(item)

    return items


# ---------------------------------------------------------------------------
# Item-level property application helpers
# ---------------------------------------------------------------------------

def _apply_stroke_to_item(item: object, color: QColor) -> None:
    """Apply *color* as the stroke (pen) of *item* in-place."""
    # ArrowItem has its own setter that keeps shaft + head in sync.
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            item.set_stroke_color(color)
            return
    except ImportError:
        pass

    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsTextItem,
    )
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem)):
        pen = item.pen()
        pen.setColor(color)
        item.setPen(pen)
    elif isinstance(item, QGraphicsTextItem):
        item.setDefaultTextColor(color)


def _apply_fill_to_item(item: object, color: QColor) -> None:
    """Apply *color* as the fill (brush) of *item* in-place."""
    from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsRectItem  # noqa: PLC0415
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
        item.setBrush(QBrush(color))


def _apply_width_to_item(item: object, width: int) -> None:
    """Apply *width* as the pen width of *item* in-place."""
    # ArrowItem has its own setter that preserves cap style.
    try:
        from verdiclip.editor.tools.arrow import ArrowItem  # noqa: PLC0415
        if isinstance(item, ArrowItem):
            item.set_stroke_width(width)
            return
    except ImportError:
        pass

    from PySide6.QtWidgets import (  # noqa: PLC0415
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
    )
    if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem)):
        pen = item.pen()
        pen.setWidth(width)
        item.setPen(pen)
