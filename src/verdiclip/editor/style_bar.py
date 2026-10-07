"""Toolbar for the stroke, fill, width, and font of annotations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QFontComboBox,
    QLabel,
    QMenu,
    QSpinBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from verdiclip.document.annotations import AnnotationKind
from verdiclip.document.style import TRANSPARENT, Color, Style
from verdiclip.editor.session import ToolId
from verdiclip.render.renderer import QtConvert

PALETTE: Final = (
    Color(0xE5, 0x1C, 0x23),
    Color(0xFF, 0x98, 0x00),
    Color(0xFF, 0xEB, 0x3B),
    Color(0x4C, 0xAF, 0x50),
    Color(0x21, 0x96, 0xF3),
    Color(0x9C, 0x27, 0xB0),
    Color(0x00, 0x00, 0x00),
    Color(0x75, 0x75, 0x75),
    Color(0xFF, 0xFF, 0xFF),
)


class StyleField(StrEnum):
    """A style property the bar can show."""

    STROKE = "stroke"
    FILL = "fill"
    WIDTH = "width"
    FONT = "font"


@dataclass(frozen=True, slots=True)
class FieldLabels:
    """Which fields a tool uses and what to call them."""

    stroke: str = ""
    fill: str = ""
    width: str = ""
    font: bool = False

    @property
    def fields(self) -> frozenset[StyleField]:
        """Return the fields with a label."""
        shown = {
            StyleField.STROKE: bool(self.stroke),
            StyleField.FILL: bool(self.fill),
            StyleField.WIDTH: bool(self.width),
            StyleField.FONT: self.font,
        }
        return frozenset(f for f, visible in shown.items() if visible)


_LABELED: Final = FieldLabels(
    stroke="Line & text", fill="Fill", width="Width", font=True
)
_LINE: Final = FieldLabels(stroke="Line", width="Width")
FIELD_LAYOUT: Final[dict[str, FieldLabels]] = {
    ToolId.SELECT: FieldLabels(),
    ToolId.CROP: FieldLabels(),
    ToolId.RECTANGLE: _LABELED,
    ToolId.ELLIPSE: _LABELED,
    ToolId.LINE: _LINE,
    ToolId.ARROW: _LINE,
    ToolId.FREEHAND: _LINE,
    ToolId.TEXT: FieldLabels(stroke="Text", fill="Background", font=True),
    ToolId.COUNTER: FieldLabels(stroke="Number", fill="Circle", font=True),
    ToolId.HIGHLIGHT: FieldLabels(fill="Color"),
    ToolId.OBFUSCATE: FieldLabels(width="Pixel size"),
}


class ColorButton(QToolButton):
    """A swatch button with a palette menu, "None", and "More colors…"."""

    color_chosen = Signal(object)  # Color

    def __init__(self, *, allow_none: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = TRANSPARENT
        self._allow_none = allow_none
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIconSize(QSize(20, 20))
        self.setMenu(self._build_menu())

    @property
    def color(self) -> Color:
        """Return the color shown."""
        return self._color

    def set_color(self, color: Color) -> None:
        """Show ``color`` without emitting."""
        self._color = color
        self.setIcon(self.swatch(color))

    @staticmethod
    def swatch(color: Color) -> QIcon:
        """Return a square icon of ``color`` (diagonal slash for none)."""
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QColor(110, 110, 110))
        if color.is_transparent:
            painter.setBrush(Qt.GlobalColor.white)
            painter.drawRect(1, 1, 17, 17)
            painter.setPen(QColor(220, 0, 0))
            painter.drawLine(2, 17, 17, 2)
        else:
            painter.setBrush(QtConvert.color(color))
            painter.drawRect(1, 1, 17, 17)
        painter.end()
        return QIcon(pixmap)

    def _build_menu(self) -> QMenu:
        """Return the palette menu."""
        menu = QMenu(self)
        if self._allow_none:
            none = menu.addAction(self.swatch(TRANSPARENT), "None")
            none.triggered.connect(lambda: self._choose(TRANSPARENT))
        for color in PALETTE:
            action = menu.addAction(self.swatch(color), color.hex)
            action.triggered.connect(lambda _=False, c=color: self._choose(c))
        menu.addSeparator()
        more = menu.addAction("More colors…")
        more.triggered.connect(self._pick_custom)
        return menu

    def _pick_custom(self) -> None:
        """Choose any color, including transparency."""
        chosen = QColorDialog.getColor(
            QtConvert.color(self._color),
            self,
            "Choose color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if chosen.isValid():
            self._choose(
                Color(chosen.red(), chosen.green(), chosen.blue(), chosen.alpha())
            )

    def _choose(self, color: Color) -> None:
        """Show and emit ``color``."""
        self.set_color(color)
        self.color_chosen.emit(color)


class StyleBar(QToolBar):
    """Edits the style of the selection, or of the active tool when none."""

    style_changed = Signal(object)  # Callable[[Style], Style]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Style", parent)
        self.setMovable(False)
        self._updating = False
        self._stroke_label = QLabel()
        self._stroke = ColorButton(allow_none=False)
        self._fill_label = QLabel()
        self._fill = ColorButton(allow_none=True)
        self._width_label = QLabel()
        self._width = QSpinBox()
        self._width.setRange(1, 60)
        self._font_label = QLabel()
        self._font = QFontComboBox()
        self._font.setMaximumWidth(170)
        self._font.setToolTip("Font")
        self._font_size = QSpinBox()
        self._font_size.setRange(6, 200)
        self._bold = QAction("B", self)
        self._italic = QAction("I", self)
        self._actions: dict[StyleField, list[QAction]] = {}
        self._build()
        self.show_for(FieldLabels(), Style())

    def tallest_height(self) -> int:
        """Return the bar height with every field shown (so it never resizes)."""
        widgets = (self._font, self._font_size, self._width, self._stroke)
        tallest = max(w.sizeHint().height() for w in widgets)
        margins = self.contentsMargins()
        layout = self.layout()
        spacing = layout.spacing() if layout is not None else 0
        return tallest + margins.top() + margins.bottom() + 2 * spacing

    def show_for(self, labels: FieldLabels, style: Style) -> None:
        """Show the fields in ``labels`` populated from ``style``."""
        self._updating = True
        try:
            fields = labels.fields
            for field, actions in self._actions.items():
                for action in actions:
                    action.setVisible(field in fields)
            self._stroke_label.setText(labels.stroke)
            self._fill_label.setText(labels.fill)
            self._width_label.setText(labels.width)
            self._stroke.set_color(style.stroke)
            self._fill.set_color(style.fill)
            self._width.setValue(round(style.width))
            self._font.setCurrentFont(QFont(style.font_family))
            self._font_size.setValue(style.font_size)
            self._bold.setChecked(style.bold)
            self._italic.setChecked(style.italic)
        finally:
            self._updating = False

    # Building

    def _build(self) -> None:
        """Add widgets and wire their edits."""
        self._group(StyleField.STROKE, self._stroke_label, self._stroke)
        self._group(StyleField.FILL, self._fill_label, self._fill)
        self._group(StyleField.WIDTH, self._width_label, self._width)
        self._group(StyleField.FONT, self._font_label, self._font, self._font_size)
        for action, tip in ((self._bold, "Bold"), (self._italic, "Italic")):
            font = QFont()
            font.setBold(action is self._bold)
            font.setItalic(action is self._italic)
            action.setFont(font)
            action.setCheckable(True)
            action.setToolTip(tip)
            self.addAction(action)
            self._actions[StyleField.FONT].append(action)
        self._stroke.setToolTip("Line or text color")
        self._fill.setToolTip("Fill color")
        self._width.setToolTip("Line width in pixels")
        self._stroke.color_chosen.connect(
            lambda c: self._emit(lambda s: replace(s, stroke=c))
        )
        self._fill.color_chosen.connect(
            lambda c: self._emit(lambda s: replace(s, fill=c))
        )
        self._width.valueChanged.connect(
            lambda v: self._emit(lambda s: replace(s, width=float(v)))
        )
        self._font.currentFontChanged.connect(
            lambda f: self._emit(lambda s: replace(s, font_family=f.family()))
        )
        self._font_size.valueChanged.connect(
            lambda v: self._emit(lambda s: replace(s, font_size=v))
        )
        self._bold.toggled.connect(lambda on: self._emit(lambda s: replace(s, bold=on)))
        self._italic.toggled.connect(
            lambda on: self._emit(lambda s: replace(s, italic=on))
        )

    def _group(self, field: StyleField, *widgets: QWidget) -> None:
        """Add ``widgets`` and remember their actions for show/hide."""
        actions = [self.addWidget(w) for w in widgets]
        actions.append(self.addSeparator())
        self._actions[field] = actions

    def _emit(self, change: object) -> None:
        """Emit a style change unless the bar is being populated."""
        if not self._updating:
            self.style_changed.emit(change)


KIND_TOOLS: Final[dict[AnnotationKind, ToolId]] = {
    kind: ToolId(kind.value) for kind in AnnotationKind
}
