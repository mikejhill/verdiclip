"""Pending native transparent-window qualification; never implicit UI permission."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession

from verdiclip.editor.tab_bar import DocumentTabBar
from verdiclip.editor.tab_preview import TabDragPreview

pytestmark = pytest.mark.native_desktop


def test_native_preview_does_not_occlude_tab_hit_testing(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
) -> None:
    """Real window-mask/hit testing is unavailable in Qt's offscreen plugin."""
    if not request.config.getoption("--require-interactive-capture"):
        pytest.skip("Native tab preview requires a separately granted UI handoff")
    if QGuiApplication.platformName() != "windows":
        pytest.fail("Native tab preview requires Windows QPA")
    session = DesktopSession.current()
    if session is None or session.state != 0 or session.flags != 1:
        pytest.fail("Native tab preview requires a positively active/unlocked session")
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Destination")
    bar.resize(250, 40)
    bar.move(100, 150)
    bar.show()
    qtbot.waitExposed(bar)
    point = bar.mapToGlobal(bar.tabRect(0).center())
    preview = TabDragPreview(bar, bar.grab(bar.tabRect(0)), QPoint(10, 10))
    qtbot.addWidget(preview)
    preview.follow(point)
    qtbot.waitExposed(preview)
    widget = QApplication.widgetAt(point)
    assert widget is bar or (widget is not None and bar.isAncestorOf(widget))
    assert widget is not preview
