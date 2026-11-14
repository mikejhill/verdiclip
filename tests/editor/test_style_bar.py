"""Tests for the style bar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from verdiclip.document.style import TRANSPARENT, Color, HAlign, Style, VAlign
from verdiclip.editor.session import ToolId
from verdiclip.editor.style_bar import (
    FIELD_LAYOUT,
    LABEL_MARGINS,
    PALETTE,
    ColorButton,
    StyleBar,
    TextFit,
)


def test_field_labels_keep_clear_of_the_separator_before_them(qtbot: QtBot) -> None:
    """Each field label has room on its left, so "Text" doesn't touch the bar edge."""
    bar = StyleBar()
    qtbot.addWidget(bar)

    labels = bar.findChildren(QLabel)

    assert len(labels) == 4  # Line, fill, outline, and width
    for label in labels:
        margins = label.contentsMargins()
        assert margins.left() == LABEL_MARGINS[0] > 0
        assert margins.right() == LABEL_MARGINS[2]


# Each control's edit, applied to a default style


def _changes(bar: StyleBar) -> list[Callable[[Style], Style]]:
    changes: list[Callable[[Style], Style]] = []
    bar.style_changed.connect(changes.append)
    return changes


def _menu_texts(button: ColorButton) -> list[str]:
    menu = button.menu()
    assert menu is not None
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _menu_action(button: ColorButton, text: str) -> QAction:
    menu = button.menu()
    assert menu is not None
    return next(a for a in menu.actions() if a.text() == text)


@pytest.mark.parametrize(
    ("name", "field"),
    [("_stroke", "stroke"), ("_fill", "fill"), ("_outline", "outline")],
)
def test_color_choices_change_their_own_field(
    qtbot: QtBot, name: str, field: str
) -> None:
    """Picking a palette color changes only that color of the style."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = _changes(bar)
    button = getattr(bar, name)
    assert isinstance(button, ColorButton)

    _menu_action(button, PALETTE[4].hex).trigger()

    assert len(changes) == 1
    assert changes[0](Style()) == replace(Style(), **{field: PALETTE[4]})
    assert button.color == PALETTE[4]


def test_number_and_font_edits(qtbot: QtBot) -> None:
    """Width, font, size, bold and italic each change only their field."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = _changes(bar)
    base = Style()

    bar._width.setValue(9)
    bar._font_size.setValue(31)
    bar._font.setCurrentFont(QFont("Courier New"))
    bar._bold.setChecked(True)
    bar._italic.setChecked(True)
    bar._shadow.setChecked(True)

    applied = [change(base) for change in changes]
    family = bar._font.currentFont().family()
    assert replace(base, width=9.0) in applied
    assert replace(base, font_size=31) in applied
    assert replace(base, font_family=family) in applied
    assert replace(base, bold=True) in applied
    assert replace(base, italic=True) in applied
    assert replace(base, shadow=True) in applied
    assert len(applied) == 6


def test_alignment_buttons_are_exclusive_and_emit(qtbot: QtBot) -> None:
    """Alignment is one choice per axis, each emitting its value."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = _changes(bar)

    bar._h_align[HAlign.RIGHT].trigger()
    bar._v_align[VAlign.BOTTOM].trigger()

    assert [c(Style()) for c in changes] == [
        replace(Style(), h_align=HAlign.RIGHT),
        replace(Style(), v_align=VAlign.BOTTOM),
    ]
    assert bar._h_align[HAlign.RIGHT].isChecked()
    assert not bar._h_align[HAlign.LEFT].isChecked()
    assert all(a.isCheckable() for a in bar._v_align.values())


def test_populating_emits_nothing(qtbot: QtBot) -> None:
    """Showing a style never echoes it back as an edit."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = _changes(bar)
    fits: list[bool] = []
    bar.auto_fit_toggled.connect(fits.append)

    style = Style(
        stroke=PALETTE[2],
        fill=PALETTE[3],
        outline=PALETTE[5],
        width=7,
        font_size=22,
        bold=True,
        italic=True,
        shadow=True,
        h_align=HAlign.CENTER,
        v_align=VAlign.MIDDLE,
    )
    bar.show_for(FIELD_LAYOUT[ToolId.TEXT], style, TextFit.FIXED)

    assert changes == []
    assert fits == []
    assert bar._stroke.color == PALETTE[2]
    assert bar._fill.color == PALETTE[3]
    assert bar._outline.color == PALETTE[5]
    assert bar._width.value() == 7
    assert bar._font_size.value() == 22
    assert bar._bold.isChecked()
    assert bar._italic.isChecked()
    assert bar._shadow.isChecked()
    assert bar._h_align[HAlign.CENTER].isChecked()
    assert bar._v_align[VAlign.MIDDLE].isChecked()
    # Edits after populating are reported again
    bar._bold.setChecked(False)
    assert len(changes) == 1


def test_labels_follow_the_tool(qtbot: QtBot) -> None:
    """The field labels name what each control means for the tool."""
    bar = StyleBar()
    qtbot.addWidget(bar)

    bar.show_for(FIELD_LAYOUT[ToolId.TEXT], Style())
    assert bar._stroke_label.text() == "Text"
    assert bar._fill_label.text() == "Background"
    assert bar._outline_label.text() == "Outline"
    assert bar._width_label.text() == "Width"

    bar.show_for(FIELD_LAYOUT[ToolId.OBFUSCATE], Style())
    assert bar._width_label.text() == "Pixel size"
    assert bar._fill_label.text() == ""


def test_font_family_is_shown(qtbot: QtBot) -> None:
    """The font box shows the style's family."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    family = bar._font.itemText(bar._font.count() - 1)

    bar.show_for(FIELD_LAYOUT[ToolId.TEXT], Style(font_family=family))

    assert bar._font.currentFont().family() == family


def test_controls_explain_themselves(qtbot: QtBot) -> None:
    """Each control has a tooltip and B / I look bold and italic."""
    bar = StyleBar()
    qtbot.addWidget(bar)

    assert bar._stroke.toolTip() == "Line or text color"
    assert bar._fill.toolTip() == "Fill color"
    assert bar._outline.toolTip() == "Outline color"
    assert bar._width.toolTip() == "Line width in pixels"
    assert bar._bold.toolTip() == "Bold"
    assert bar._italic.toolTip() == "Italic"
    assert bar._bold.font().bold()
    assert not bar._bold.font().italic()
    assert bar._italic.font().italic()
    assert not bar._italic.font().bold()
    assert bar._bold.isCheckable()
    assert bar._italic.isCheckable()
    assert bar._shadow.toolTip() == "Drop shadow"
    assert bar._auto_fit.toolTip() == "Size the box to its text"


class TestColorButton:
    """The swatch button's palette menu."""

    def test_menu_with_none(self, qtbot: QtBot) -> None:
        """None first, then the palette, then More colors…."""
        button = ColorButton(allow_none=True)
        qtbot.addWidget(button)
        assert _menu_texts(button) == [
            "None",
            *(c.hex for c in PALETTE),
            "More colors…",
        ]
        menu = button.menu()
        assert menu is not None
        assert all(not a.icon().isNull() for a in menu.actions()[: len(PALETTE) + 1])

    def test_menu_without_none(self, qtbot: QtBot) -> None:
        """A stroke can't be none."""
        button = ColorButton(allow_none=False)
        qtbot.addWidget(button)
        assert _menu_texts(button)[0] == PALETTE[0].hex
        assert "None" not in _menu_texts(button)

    def test_none_chooses_transparent(self, qtbot: QtBot) -> None:
        """Choosing None emits and shows transparent."""
        button = ColorButton(allow_none=True)
        qtbot.addWidget(button)
        button.set_color(PALETTE[0])
        chosen: list[Color] = []
        button.color_chosen.connect(chosen.append)

        _menu_action(button, "None").trigger()

        assert chosen == [TRANSPARENT]
        assert button.color == TRANSPARENT

    def test_swatches_differ_by_color(self, qtbot: QtBot) -> None:
        """A swatch is drawn in its color; none gets a slash on white."""
        del qtbot
        red = ColorButton.swatch(PALETTE[0]).pixmap(20, 20).toImage()
        none = ColorButton.swatch(TRANSPARENT).pixmap(20, 20).toImage()

        assert red.pixelColor(10, 10) == QColor(PALETTE[0].hex)
        assert none.pixelColor(5, 5) == QColor("white")
        assert none.pixelColor(10, 9) == QColor(220, 0, 0)
        assert red.pixelColor(0, 0).alpha() == 0  # Transparent margin
        assert red.pixelColor(1, 1) == QColor(110, 110, 110)  # Gray border
