"""Rendered tab hierarchy follows user palette and keyboard focus."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle
from pytestqt.qtbot import QtBot

from verdiclip.editor.tab_bar import DocumentTabBar


@pytest.mark.parametrize(
    ("surface", "text", "base", "accent"),
    [
        ("#eeeeee", "#161616", "#ffffff", "#0055aa"),
        ("#202020", "#eeeeee", "#303030", "#66bbff"),
        ("#000000", "#ffffff", "#000000", "#ffff00"),
    ],
)
def test_active_marker_and_focus_survive_palette_changes(
    qtbot: QtBot,
    surface: str,
    text: str,
    base: str,
    accent: str,
) -> None:
    """Light, dark and high-contrast palettes retain a visible selected marker."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Invoice capture with an editable title")
    bar.addTab("Second capture")
    bar.resize(600, 60)
    palette = QPalette(bar.palette())
    for role, color in (
        (QPalette.ColorRole.Window, surface),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Highlight, accent),
    ):
        palette.setColor(role, QColor(color))
    bar.setPalette(palette)
    bar.show()
    bar.activateWindow()
    qtbot.waitUntil(bar.isActiveWindow)
    bar.clearFocus()
    first = bar.tabRect(0)
    image = bar.grab().toImage()
    point = QPoint(first.center().x(), first.bottom() - 5)
    assert image.pixelColor(point) == QColor(accent)
    assert bar.tabRect(0).height() >= 36
    assert bar.elideMode() == Qt.TextElideMode.ElideMiddle
    bar.setCurrentIndex(1)
    second = bar.tabRect(1)
    assert bar.grab().toImage().pixelColor(
        QPoint(second.center().x(), second.bottom() - 5)
    ) == QColor(accent)
    bar.setFocus()
    assert bar.hasFocus()
    focused = bar.grab().toImage()
    bar.clearFocus()
    assert focused != bar.grab().toImage()
    assert bar.accessibleName() == "Capture tabs"
    QTest.mouseMove(bar, bar.tabRect(0).center())
    assert bar.grab().toImage().pixelColor(
        bar.tabRect(0).topLeft() + QPoint(10, 10)
    ) == QColor(base)
    side = bar.style().styleHint(QStyle.StyleHint.SH_TabBar_CloseButtonPosition)
    close = bar.tabButton(1, DocumentTabBar.ButtonPosition(side))
    assert close.isVisible()
    bar.setTabEnabled(0, False)  # noqa: FBT003  # Qt positional-only API
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#00ff00"))
    bar.setPalette(palette)
    before_disabled_tint = bar.grab(bar.tabRect(0)).toImage()
    disabled = QColor("#ff007f")
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled
    )
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    bar.setPalette(palette)
    pixels = bar.grab(bar.tabRect(0)).toImage()
    # Compare two controlled disabled brushes. Antialiasing and native close
    # glyphs can contain individual colored samples even before this change.
    # Green -> magenta must increase the integrated magenta/green opponent
    # channel; blank rendering or unchanged neutral text cannot satisfy this.
    # This verifies disabled-palette influence across the rendered header.
    before_chroma = sum(
        color.red() + color.blue() - 2 * color.green()
        for y in range(before_disabled_tint.height())
        for x in range(before_disabled_tint.width())
        for color in [before_disabled_tint.pixelColor(x, y)]
    )
    after_chroma = sum(
        color.red() + color.blue() - 2 * color.green()
        for y in range(pixels.height())
        for x in range(pixels.width())
        for color in [pixels.pixelColor(x, y)]
    )
    assert after_chroma > before_chroma, (before_chroma, after_chroma)
    bar.resize(180, 60)
    assert bar.usesScrollButtons()
    assert not bar.grab().isNull()
    bar.setEnabled(False)
    assert not bar.grab().isNull()
