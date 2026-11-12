"""Persistent scrolling failure feedback without tray or focus dependence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytestqt.qtbot import QtBot
from tests.update.test_service import FakeFeed, Spawns
from tests.ux.test_app_journeys import PYTHONW, App, AppFactory
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - pytest fixture
)
from tests.ux.test_capture_journeys import FakeDesktop
from tests.ux.test_scrolling_journeys import finish, prepare, select

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.settings import Settings, Theme
from verdiclip.update.install import Installation, InstallKind
from verdiclip.update.service import UpdateLauncher, Updater

pytestmark = [pytest.mark.ux, pytest.mark.usefixtures("scrolling_enabled")]


def statuses() -> list[QMessageBox]:
    """Find the capture status surfaces a user can see."""
    return [
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QMessageBox)
        and widget.objectName() == "scrolling-capture-status"
        and widget.isVisible()
    ]


def fail(qtbot: QtBot, app: App, helper: ScrollHelper) -> QMessageBox:
    """Reject the current panel through the owned helper's normal result path."""
    helper.finished.emit(
        ScrollResult(None, "Could not align the scrolling images", "restored", 2)
    )
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    qtbot.waitUntil(lambda: len(statuses()) == 1)
    return statuses()[0]


@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
def test_failed_scroll_stays_visible_without_tray_and_delivers_nothing(
    make_app: AppFactory, qtbot: QtBot, snapshot_dir: Path | None, theme: Theme
) -> None:
    """UX-CAP-19: a rejected capture has a durable reason, not a silent stop."""
    settings = Settings()
    app = make_app(
        replace(
            settings,
            appearance=replace(settings.appearance, theme=theme),
            capture=replace(
                settings.capture, save_to_file=True, copy_to_clipboard=True
            ),
        )
    )
    app.controller.tray.hide()
    before = QImage(30, 20, QImage.Format.Format_RGB32)
    before.fill(0xFF123456)
    QApplication.clipboard().setImage(before)
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    assert box.windowTitle() == "Scrolling capture stopped"
    assert "No screenshot was captured" in box.text()
    assert "Could not align the scrolling images" in box.text()
    assert "restored" in box.text()
    assert not box.isModal()
    assert box.icon() == QMessageBox.Icon.Warning
    assert not box.windowIcon().isNull()
    qtbot.wait(20)
    assert box.isVisible()
    assert not app.controller.editors
    assert QApplication.clipboard().image() == before
    assert not list(app.controller.settings.output.directory.glob("*.png"))
    if snapshot_dir is not None:
        assert box.grab().save(
            str(snapshot_dir / f"scrolling-failed-{theme.value}.png")
        )


def test_selection_start_failure_is_visible_without_tray(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-19: failure before the chooser also leaves an actionable reason."""
    screens = Mock()
    screens.freeze.side_effect = CaptureError("The desktop could not be captured")
    app = make_app(screens=screens)
    app.controller.tray.hide()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    qtbot.waitUntil(lambda: bool(statuses()))
    assert "The desktop could not be captured" in statuses()[0].text()
    assert not app.controller._scrolling.overlays
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    assert not app.controller.editors


@pytest.mark.parametrize("dismiss", ["ok", "escape"])
def test_dismiss_failure_then_retry_can_deliver(
    make_app: AppFactory, qtbot: QtBot, dismiss: str
) -> None:
    """UX-CAP-19: dismissal closes only the message, and a fresh attempt works."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    if dismiss == "ok":
        button = box.button(QMessageBox.StandardButton.Ok)
        assert button is not None
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(box, Qt.Key.Key_Escape)
    assert not box.isVisible()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    finish(qtbot, app, helper)
    assert len(app.controller.editors) == 1
    assert app.controller.editors[0].isVisible()
    assert not statuses()


def test_retry_hides_old_failure_and_reuses_one_plain_text_message(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-19: retries never accumulate stale dialogs or interpret error HTML."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    first = fail(qtbot, app, helper)
    helper = prepare(app)
    app.press_hotkey("scrolling")
    assert not first.isVisible()
    select(qtbot, app, helper)
    helper.finished.emit(ScrollResult(None, "<b>Panel unavailable</b>", "", 0))
    qtbot.waitUntil(lambda: bool(statuses()))
    assert statuses() == [first]
    assert first.text() == "No screenshot was captured.\n\n<b>Panel unavailable</b>. "
    assert first.textFormat() == Qt.TextFormat.PlainText


def test_ordinary_capture_hides_old_failure_before_freezing(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18: a new normal capture cannot include the old error surface."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    screens = app.controller.capture._screens
    freeze = screens.freeze

    def check_hidden(_self: FakeDesktop) -> FrozenScreen:
        assert not box.isVisible()
        return freeze()

    monkeypatch.setattr(FakeDesktop, "freeze", check_hidden)
    app.press_hotkey("window")
    qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
    assert app.controller.editors[0].isVisible()
    assert not statuses()


@pytest.mark.parametrize("action", ["scroll_cancel", "scrolling"])
@pytest.mark.parametrize("queued", [False, True])
def test_intentional_cancel_never_shows_a_failure(
    make_app: AppFactory, qtbot: QtBot, action: str, *, queued: bool
) -> None:
    """UX-CAP-18: Escape and a second scrolling request end quietly."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    result = ScrollResult(None, "Scrolling capture cancelled", "restored", 0)
    if queued:
        helper.finished.emit(result)
    app.press_hotkey(action)
    if not queued:
        helper.finished.emit(result)
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    assert not statuses()
    assert not app.controller.editors


def test_pending_capture_suppresses_late_failure_feedback(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-18: a superseded helper cannot put an error over a newer capture."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    app.press_hotkey("region")
    app.controller._scrolling.failed.emit("The previous helper could not stop yet")
    assert not statuses()
    helper.finished.emit(
        ScrollResult(None, "Scrolling capture cancelled", "restored", 0)
    )
    qtbot.waitUntil(lambda: app.controller.capture.is_selecting)
    assert not statuses()
    app.controller.capture.cancel()


def test_interruption_feedback_does_not_request_focus_back(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18/19: report interruption without explicitly raising or activating."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    other = QWidget()
    qtbot.addWidget(other)
    other.setWindowTitle("Other work")
    other.show()
    other.activateWindow()
    qtbot.waitUntil(other.isActiveWindow)
    activate, raise_window = Mock(), Mock()
    monkeypatch.setattr(QMessageBox, "activateWindow", activate)
    monkeypatch.setattr(QMessageBox, "raise_", raise_window)
    helper.finished.emit(ScrollResult(None, "User input interrupted capture", "", 1))
    qtbot.waitUntil(lambda: bool(statuses()))
    assert "User input interrupted capture" in statuses()[0].text()
    assert statuses()[0].testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    activate.assert_not_called()
    raise_window.assert_not_called()


@pytest.mark.native_desktop
def test_interruption_feedback_preserves_native_focus(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-18/19: native Qt honors the nonactivating status window policy."""
    if QApplication.platformName() == "offscreen":
        pytest.skip("Qt offscreen does not implement show-without-activation")
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    other.activateWindow()
    qtbot.waitUntil(other.isActiveWindow)
    helper.finished.emit(ScrollResult(None, "User input interrupted capture", "", 1))
    qtbot.waitUntil(lambda: bool(statuses()))
    assert other.isActiveWindow()
    assert not statuses()[0].isActiveWindow()


def test_capture_failure_and_manual_update_keep_separate_surfaces(
    make_app: AppFactory, qtbot: QtBot, tmp_path: Path
) -> None:
    """UX-CAP-19: a capture error cannot overwrite a manual update result."""
    updater = Updater(
        Installation(InstallKind.CHECKOUT, "0.14.0", PYTHONW, tmp_path),
        FakeFeed("0.14.0"),
        UpdateLauncher(tmp_path / "update", Spawns()),
    )
    app = make_app(updater=updater)
    app.controller.check_for_updates()
    updates = [
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QMessageBox)
        and widget.objectName() == "manual-update-status"
        and widget.isVisible()
    ]
    assert len(updates) == 1
    update = updates[0]
    title, message = update.windowTitle(), update.text()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    assert box is not update
    assert update.isVisible()
    assert update.windowTitle() == title
    assert update.text() == message


def test_shutdown_disposes_failure_and_ignores_late_signals(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-18/19: no failure surface or late capture outlives app shutdown."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    with qtbot.waitSignal(box.destroyed):
        app.controller.shutdown()
        assert not box.isVisible()
        QCoreApplication.sendPostedEvents(box, QEvent.Type.DeferredDelete)
    app.controller._scrolling.failed.emit("Late failure")
    app.controller.capture_scrolling()
    qtbot.wait(0)
    assert not statuses()
    assert not app.controller._scrolling.overlays


@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
def test_error_can_be_copied_before_and_after_another_capture(
    make_app: AppFactory, qtbot: QtBot, snapshot_dir: Path | None, theme: Theme
) -> None:
    """UX-CAP-19: error reporting does not require photographing a vanishing popup."""
    settings = Settings()
    app = make_app(
        replace(settings, appearance=replace(settings.appearance, theme=theme))
    )
    assert not any(
        "Copy last scrolling error" in action.text()
        for action in app.controller.tray.menu.actions()
    )
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    box = fail(qtbot, app, helper)
    expected = box.text()
    copy = next(button for button in box.buttons() if button.text() == "&Copy error")
    qtbot.mouseClick(copy, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == expected
    assert "restored" in expected
    app.press_hotkey("window")
    qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
    assert not box.isVisible()
    QApplication.clipboard().setText("New clipboard content")
    menu = app.controller.tray.menu
    menu.popup(app.controller.editors[0].pos())
    action = next(
        action
        for action in menu.actions()
        if action.text() == "Copy last scrolling error"
    )
    if snapshot_dir is not None:
        assert menu.grab().save(
            str(snapshot_dir / f"scrolling-copy-error-menu-{theme.value}.png")
        )
    qtbot.mouseClick(
        menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center()
    )
    assert QApplication.clipboard().text() == expected
    assert not statuses()
    # A subsequent rejection replaces the retained report instead of appending history.
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    helper.finished.emit(ScrollResult(None, "Another panel is unavailable", "", 0))
    qtbot.waitUntil(lambda: bool(statuses()))
    copy = next(
        button for button in statuses()[0].buttons() if button.text() == "&Copy error"
    )
    qtbot.mouseClick(copy, Qt.MouseButton.LeftButton)
    assert "Another panel is unavailable" in QApplication.clipboard().text()
    assert "Could not align" not in QApplication.clipboard().text()
