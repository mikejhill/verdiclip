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
    disabled = QColor("#ff007f")
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled
    )
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    bar.setPalette(palette)
    bar.setTabEnabled(0, False)  # noqa: FBT003  # Qt positional-only API
    pixels = bar.grab(bar.tabRect(0)).toImage()
    assert any(
        pixels.pixelColor(x, y) == disabled
        for y in range(pixels.height())
        for x in range(pixels.width())
    )
    bar.resize(180, 60)
    assert bar.usesScrollButtons()
    assert not bar.grab().isNull()
    bar.setEnabled(False)
    assert not bar.grab().isNull()
    assert "palette(disabled" not in bar.styleSheet()
