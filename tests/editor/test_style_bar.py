"""Tests for the style bar."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QFontComboBox, QLabel, QWidget
from pytestqt.qtbot import QtBot

from verdiclip.document.style import Style
from verdiclip.editor import style_bar
from verdiclip.editor.session import ToolId
from verdiclip.editor.style_bar import FIELD_LAYOUT, LABEL_MARGINS, StyleBar, StyleField


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


def test_non_font_modes_and_height_never_populate_font_families(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a Select editor must not pay for an unused font database."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A hidden font picker was constructed")

    monkeypatch.setattr(style_bar, "QFontComboBox", forbidden)
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = QSignalSpy(bar.style_changed)
    for tool in (ToolId.SELECT, ToolId.CROP, ToolId.LINE, ToolId.HIGHLIGHT):
        bar.show_for(FIELD_LAYOUT[tool], Style(font_family="Uninstalled test family"))
        assert bar.tallest_height() > 0
    assert changes.count() == 0


@pytest.mark.parametrize(
    "tool", [ToolId.TEXT, ToolId.RECTANGLE, ToolId.ELLIPSE, ToolId.COUNTER]
)
def test_first_font_mode_uses_current_style_without_emitting_an_edit(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tool: ToolId
) -> None:
    """Deferred creation preserves native picker behavior and style ownership."""
    native_type = style_bar.QFontComboBox
    made: list[QFontComboBox] = []

    def construct(parent: QWidget | None = None) -> QFontComboBox:
        widget = native_type(parent)
        made.append(widget)
        return widget

    monkeypatch.setattr(style_bar, "QFontComboBox", construct)
    bar = StyleBar()
    qtbot.addWidget(bar)
    changes = QSignalSpy(bar.style_changed)
    hidden = Style(font_family="Uninstalled original family", font_size=11)
    bar.show_for(FIELD_LAYOUT[ToolId.SELECT], hidden)
    current = replace(hidden, font_size=29, bold=True, italic=True)
    bar.show_for(FIELD_LAYOUT[tool], current)
    controls = bar._font_controls
    assert controls is not None
    assert len(made) == 1
    assert isinstance(controls.family, native_type)
    assert controls.family.accessibleName() == "Font family"
    assert controls.size.accessibleName() == "Font size"
    assert controls.size.value() == current.font_size
    assert controls.bold.isChecked()
    assert controls.italic.isChecked()
    bar.show_for(FIELD_LAYOUT[ToolId.SELECT], Style())
    bar.show_for(FIELD_LAYOUT[tool], replace(current, font_size=17))
    assert len(made) == 1
    assert controls.size.value() == 17
    assert changes.count() == 0
    actions = bar.actions()
    family_action = next(
        a for a in actions if bar.widgetForAction(a) is controls.family
    )
    assert actions.index(family_action) < actions.index(controls.bold)
    assert actions.index(controls.italic) < actions.index(bar._shadow)
    assert all(a.isVisible() for a in bar._actions[StyleField.FONT])


def test_deferred_font_edits_apply_to_the_current_style(qtbot: QtBot) -> None:
    """Lazy signal connections must keep editing rather than freeze initial state."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    bar.show_for(FIELD_LAYOUT[ToolId.TEXT], Style(font_size=12))
    controls = bar._font_controls
    assert controls is not None
    changes = QSignalSpy(bar.style_changed)
    other = Style(font_family="Other current style", font_size=19, width=7)

    def changed_style() -> Style:
        change = changes.at(changes.count() - 1)[0]
        assert callable(change)
        updated = change(other)
        assert isinstance(updated, Style)
        return updated

    controls.size.setValue(27)
    updated = changed_style()
    assert updated.font_size == 27
    assert updated.font_family == other.font_family
    assert updated.width == other.width

    controls.bold.setChecked(True)
    updated = changed_style()
    assert updated.bold is True
    assert updated.font_size == other.font_size

    current = controls.family.currentFont().family()
    next_index = next(
        index
        for index in range(controls.family.count())
        if controls.family.itemText(index) != current
    )
    controls.family.setCurrentIndex(next_index)
    updated = changed_style()
    assert updated.font_family == controls.family.currentFont().family()
    assert updated.font_size == other.font_size
    assert updated.width == other.width


@pytest.mark.parametrize("points", [9, 18])
def test_reserved_height_fits_font_controls_when_they_are_created(
    qtbot: QtBot, points: int
) -> None:
    """Switching to a font tool must not move the canvas or clip its controls."""
    bar = StyleBar()
    qtbot.addWidget(bar)
    font = QFont(bar.font())
    font.setPointSize(points)
    bar.setFont(font)
    reserved = bar.tallest_height()
    bar.show_for(FIELD_LAYOUT[ToolId.TEXT], Style())
    controls = bar._font_controls
    assert controls is not None
    margins = bar.contentsMargins()
    layout = bar.layout()
    assert layout is not None
    needed = max(controls.family.sizeHint().height(), controls.size.sizeHint().height())
    needed += margins.top() + margins.bottom() + 2 * layout.spacing()
    assert reserved >= needed
