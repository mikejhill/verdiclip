"""Scrolling capture through the real controller, with the helper process faked."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import App, AppFactory
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - pytest fixture
)

from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point
from verdiclip.settings import Settings

pytestmark = pytest.mark.ux


def prepare(app: App) -> ScrollHelper:
    """Replace only the child process launch."""
    helper = ScrollHelper()
    helper.start = Mock()
    helper.cancel = Mock()
    helper.shutdown = Mock()
    app.controller._scrolling._helper_factory = lambda: helper
    return helper


def select(qtbot: QtBot, app: App, helper: ScrollHelper) -> None:
    """Click a point in the chooser and wait for the helper to start."""
    service = app.controller._scrolling
    qtbot.waitUntil(lambda: bool(service.overlays))
    service.overlays[0].chosen.emit(Point(30, 40))
    assert not service.overlays
    qtbot.waitUntil(lambda: helper is service._helper)
    assert isinstance(helper.start, Mock)
    helper.start.assert_called_once_with(Point(30, 40))


def tall_image() -> QImage:
    """Tall image."""
    image = QImage(80, 1200, QImage.Format.Format_RGB32)
    image.fill(0xFF345678)
    return image


@pytest.mark.parametrize("entry", ["tray", "hotkey"])
def test_tray_and_shortcut_deliver_a_tab(
    make_app: AppFactory, qtbot: QtBot, entry: str
) -> None:
    """UX-CAP-17: click a panel; the tall image opens like any capture."""
    settings = Settings()
    app = make_app(
        replace(settings, hotkeys=replace(settings.hotkeys, scrolling="ctrl+alt+f9"))
    )
    helper = prepare(app)
    assert app.controller.tray.action("scrolling").text().endswith("Ctrl+Alt+F9")
    if entry == "tray":
        app.controller.tray.action("scrolling").trigger()
    else:
        app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    assert "scroll_cancel" in app.hotkeys.registered
    image = tall_image()
    helper.finished.emit(ScrollResult(image, "", 5))
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)
    editor = app.controller.editors[0]
    assert editor.session.document.image == image.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    assert not app.controller._scrolling.busy
    assert "scroll_cancel" not in app.hotkeys.registered


def test_estimated_seams_are_mentioned(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estimated seams are mentioned."""
    app = make_app()
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app.controller, "_notify", lambda title, text, **_: notes.append((title, text))
    )
    helper = prepare(app)
    app.controller.capture_scrolling()
    select(qtbot, app, helper)
    helper.finished.emit(ScrollResult(tall_image(), "", 4, "1 seam(s) were estimated"))
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)
    assert ("Scrolling capture finished", "1 seam(s) were estimated") in notes


def test_failure_is_reported_and_can_be_copied(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """Failure is reported and can be copied."""
    app = make_app()
    helper = prepare(app)
    app.controller.capture_scrolling()
    select(qtbot, app, helper)
    helper.finished.emit(ScrollResult(None, "Nothing at that point scrolls", 0))
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    assert not app.controller.workspace.documents
    status = app.controller._scroll_status
    assert status is not None
    assert "Nothing at that point scrolls" in status.text()
    app.controller.tray.action("copy_scroll_error").trigger()
    assert "Nothing at that point scrolls" in QApplication.clipboard().text()


def test_escape_in_chooser_cancels_quietly(make_app: AppFactory, qtbot: QtBot) -> None:
    """Escape in chooser cancels quietly."""
    app = make_app()
    prepare(app)
    app.controller.capture_scrolling()
    service = app.controller._scrolling
    qtbot.waitUntil(lambda: bool(service.overlays))
    service.overlays[0].cancelled.emit()
    assert not service.busy
    assert not service.overlays
    assert app.controller._scroll_status is None


def test_escape_during_capture_cancels_and_drops_the_result(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """Escape during capture cancels and drops the result."""
    app = make_app()
    helper = prepare(app)
    app.controller.capture_scrolling()
    select(qtbot, app, helper)
    app.press_hotkey("scroll_cancel")
    assert isinstance(helper.cancel, Mock)
    helper.cancel.assert_called_once()
    helper.finished.emit(ScrollResult(tall_image(), "", 3))
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    assert not app.controller.workspace.documents
    assert app.controller._scroll_status is None


def test_second_request_cancels_and_ordinary_capture_waits(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """Second request cancels and ordinary capture waits."""
    app = make_app()
    helper = prepare(app)
    app.controller.capture_scrolling()
    select(qtbot, app, helper)
    app.press_hotkey("window")
    assert isinstance(helper.cancel, Mock)
    helper.cancel.assert_called_once()
    assert not app.controller.workspace.documents
    helper.finished.emit(ScrollResult(None, "Scrolling capture cancelled", 1))
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)


def test_shutdown_kills_a_running_helper(make_app: AppFactory, qtbot: QtBot) -> None:
    """Shutdown kills a running helper."""
    app = make_app()
    helper = prepare(app)
    app.controller.capture_scrolling()
    select(qtbot, app, helper)
    app.controller._scrolling.shutdown()
    assert isinstance(helper.shutdown, Mock)
    helper.shutdown.assert_called_once()
