"""UX-CAP-18: persistent tray state with mocked capture/process boundaries."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import AppFactory
from tests.ux.test_app_journeys import make_app as make_app  # noqa: PLC0414
from tests.ux.test_scrolling_journeys import finish, prepare, select


@pytest.mark.parametrize("cancel", ["scroll_cancel", "scrolling", "region"])
def test_busy_cancel_retire_and_rebuild(
    make_app: AppFactory, qtbot: QtBot, cancel: str
) -> None:
    """UX-CAP-18: Escape/menu cancellation remains visible until actual retirement."""
    app = make_app()
    helper = prepare(app)
    tray = app.controller.tray
    original = tray.toolTip()
    tray.action("scrolling").trigger()
    select(qtbot, app, helper)
    assert tray.toolTip() == "Scrolling capture… Esc cancels"
    assert tray.action("scrolling").text().startswith("Cancel scrolling capture")
    tray.set_update_checking(checking=True)
    assert tray.toolTip() == "Scrolling capture… Esc cancels"
    assert tray.action("scrolling").text().startswith("Cancel scrolling capture")
    app.press_hotkey(cancel)
    assert tray.toolTip() == "Cancelling scrolling capture…"
    tray.set_update_checking(checking=True)
    assert tray.toolTip() == "Cancelling scrolling capture…"
    assert tray.action("scrolling").text().startswith("Cancel scrolling capture")
    tray.action("scrolling").trigger()
    assert "scroll_cancel" in app.hotkeys.registered
    finish(qtbot, app, helper)
    assert tray.toolTip() == original
    assert tray.action("scrolling").text().startswith("Capture scrolling area")
    assert "scroll_cancel" not in app.hotkeys.registered
    if cancel == "region":
        qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    else:
        assert not app.controller.workspace.documents


@pytest.mark.parametrize(
    "terminal", ["success", "failure", "shutdown", "failed_start", "timeout"]
)
def test_terminal_resets_busy(
    make_app: AppFactory, qtbot: QtBot, terminal: str
) -> None:
    """UX-CAP-18: no persistent busy tooltip outlives any terminal outcome."""
    from verdiclip.capture.scrolling import ScrollResult  # noqa: PLC0415

    app = make_app()
    helper = prepare(app)
    original = app.controller.tray.toolTip()
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    if terminal == "success":
        finish(qtbot, app, helper)
    elif terminal == "failure":
        helper.finished.emit(ScrollResult(None, "failed", "unchanged", 0))
        qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    elif terminal == "failed_start":
        from PySide6.QtCore import QProcess  # noqa: PLC0415

        helper._error(QProcess.ProcessError.FailedToStart)
        qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    elif terminal == "timeout":
        from PySide6.QtCore import QProcess  # noqa: PLC0415

        helper._timeout()
        assert app.controller.tray.toolTip() == "Scrolling capture… Esc cancels"
        helper._exited(1, QProcess.ExitStatus.CrashExit)
        qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    else:
        app.controller.shutdown()
    assert app.controller.tray.toolTip() == original


@pytest.mark.parametrize("stage", ["queued", "validation", "retirement"])
def test_queued_success_deadline(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """UX-CAP-18: delayed delivery and cleanup cannot publish expired success."""
    from unittest.mock import Mock  # noqa: PLC0415

    from PySide6.QtGui import QImage  # noqa: PLC0415

    from verdiclip.capture import scroll_helper as module  # noqa: PLC0415
    from verdiclip.capture.scrolling import ScrollResult  # noqa: PLC0415

    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    clock = [99.0]
    helper._deadline = 100.0
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    service = app.controller._scrolling
    if stage == "validation":
        assert isinstance(service._state, Mock)
        service._state.check.side_effect = lambda *_args: clock.__setitem__(0, 100.0)
    elif stage == "retirement":
        service.state_changed.connect(
            lambda active, _cancelled: (
                clock.__setitem__(0, 100.0) if not active else None
            )
        )
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    helper.finished.emit(ScrollResult(image, "", "restored", 2))
    if stage == "queued":
        clock[0] = 100.0
    service._deliver()
    assert not app.controller.workspace.documents
    assert app.controller._scroll_status is not None
    assert "timed out" in app.controller._scroll_status.text()


def test_selector_cancel_resets_without_launch(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-18: cancelling the chooser resets busy without starting a child."""
    from unittest.mock import Mock  # noqa: PLC0415

    from PySide6.QtCore import Qt  # noqa: PLC0415

    app = make_app()
    helper = prepare(app)
    original = app.controller.tray.toolTip()
    app.press_hotkey("scrolling")
    qtbot.waitUntil(lambda: bool(app.controller._scrolling.overlays))
    assert app.controller.tray.toolTip() == "Scrolling capture… Esc cancels"
    qtbot.keyClick(app.controller._scrolling.overlays[0], Qt.Key.Key_Escape)
    assert app.controller.tray.toolTip() == original
    assert not app.controller._scrolling.overlays
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


def test_shutdown_refusal_remains_cancelling_until_retirement(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18: failed shutdown cannot reset busy or release Escape early."""
    from unittest.mock import Mock  # noqa: PLC0415

    from verdiclip.exceptions import CaptureError  # noqa: PLC0415

    app = make_app()
    helper = prepare(app)
    original = app.controller.tray.toolTip()
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    monkeypatch.setattr(
        helper, "shutdown", Mock(side_effect=CaptureError("Still running"))
    )
    app.controller.request_exit()
    assert app.controller.tray.toolTip() == "Cancelling scrolling capture…"
    assert "scroll_cancel" in app.hotkeys.registered
    finish(qtbot, app, helper)
    assert app.controller.tray.toolTip() == original
    assert "scroll_cancel" not in app.hotkeys.registered
    assert not app.controller.workspace.documents
