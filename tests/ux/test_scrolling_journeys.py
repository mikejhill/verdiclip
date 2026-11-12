"""Integrated scrolling ownership and ordinary capture delivery journeys."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.test_scrolling import NormalizingListTarget, Target, transaction
from tests.capture.test_scrolling_containment import ContainmentTarget
from tests.ux.test_app_journeys import App, AppFactory
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - pytest fixture
)

from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.editor.window import EditorWindow
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo
from verdiclip.settings import Settings, Theme

pytestmark = [pytest.mark.ux, pytest.mark.usefixtures("scrolling_enabled")]


def prepare(app: App) -> ScrollHelper:
    """Substitute only the native boundary and child launch, not app ownership."""
    service = app.controller._scrolling
    state = Mock()
    state.windows.return_value = [
        WindowInfo(1, "Scrolling document", Rect(0, 0, 1000, 1000))
    ]
    state.tick.return_value = 456
    state.foreground.return_value = 1
    state.observe.return_value = 1
    helper = ScrollHelper()
    helper.start = Mock()
    helper.cancel = Mock()
    service._state = state
    service._helper_factory = lambda: helper
    return helper


def select(qtbot: QtBot, app: App, helper: ScrollHelper) -> None:
    """Complete the actual chooser's queued handoff."""
    service = app.controller._scrolling
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    qtbot.waitUntil(lambda: helper is service._helper)
    assert isinstance(helper.start, Mock)
    helper.start.assert_called_once_with(Point(30, 40), window=1, input_tick=456)


def finish(
    qtbot: QtBot, app: App, helper: ScrollHelper, result: ScrollResult | None = None
) -> QImage:
    """Publish one distinct tall image through the normal configured delivery."""
    if result is None:
        image = QImage(80, 1200, QImage.Format.Format_RGB32)
        image.fill(0xFF345678)
        result = ScrollResult(image, "", "restored", 5)
    image = result.image
    assert image is not None
    helper.finished.emit(result)
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    return image


@pytest.mark.parametrize("entry", ["tray", "hotkey"])
def test_tray_and_configured_shortcut_add_tab_to_existing_editor(
    make_app: AppFactory, qtbot: QtBot, entry: str
) -> None:
    """UX-CAP-17: one point chooser delivers a focused tab, without changing Repeat."""
    settings = Settings()
    app = make_app(
        replace(settings, hotkeys=replace(settings.hotkeys, scrolling="ctrl+alt+f9"))
    )
    app.press_hotkey("window")
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)
    editor = app.controller.editors[0]
    original = app.controller.workspace.documents[0]
    repeat_area = app.controller.capture._last_area
    helper = prepare(app)
    assert app.controller.tray.action("scrolling").text().endswith("Ctrl+Alt+F9")
    if entry == "tray":
        app.controller.tray.action("scrolling").trigger()
    else:
        app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    image = finish(qtbot, app, helper)
    assert app.controller.editors == (editor,)
    assert len(app.controller.workspace.documents) == 2
    assert app.controller.workspace.documents[0] is original
    assert editor.session.document.image == image.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    assert editor.isVisible()
    assert app.controller.capture._last_area == repeat_area
    app.press_hotkey("repeat")
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 3)
    assert (
        editor.session.document.image.size() == original.session.document.image.size()
    )
    assert app.controller.capture._last_area == repeat_area


def test_scrolling_uses_configured_save_and_clipboard_without_editor(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-17: tall pixels use existing output options, with no extra dialog."""
    settings = Settings()
    app = make_app(
        replace(
            settings,
            capture=replace(
                settings.capture,
                open_editor=False,
                save_to_file=True,
                copy_to_clipboard=True,
            ),
        )
    )
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    image = finish(qtbot, app, helper)
    saved = list(app.controller.settings.output.directory.glob("*.png"))
    assert len(saved) == 1
    assert QImage(str(saved[0])) == image
    assert QApplication.clipboard().image() == image
    assert not app.controller.editors
    assert app.controller.capture._last_area is None


@pytest.mark.parametrize("collapse", [False, True])
def test_fixed_header_delivers_full_width_content_once(
    make_app: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    collapse: bool,
) -> None:
    """UX-CAP-19: a stable visible header needs no configuration or manual crop."""
    target = Target()
    original_frame = target.frame

    def frame() -> QImage:
        image = original_frame()
        painter = QPainter(image)
        expanded = collapse and target.offset == 0
        painter.fillRect(
            0,
            0,
            image.width(),
            24 if expanded else 16,
            QColor("blue" if expanded else "lightgray"),
        )
        painter.end()
        return image

    monkeypatch.setattr(target, "frame", frame)
    expected = target.document.copy()
    painter = QPainter(expected)
    painter.fillRect(
        0,
        0,
        expected.width(),
        24 if collapse else 16,
        QColor("blue" if collapse else "lightgray"),
    )
    painter.end()
    initial = target.position()
    result = transaction(target).run()
    assert result.image == expected
    assert target.position() == initial
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    finish(qtbot, app, helper, result)
    assert len(app.controller.editors) == 1
    assert app.controller.editors[0].session.document.image == expected.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    assert app.controller.capture._last_area is None


def test_normal_capture_waits_for_cancelled_child_then_proceeds(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-18: another capture cannot overlap an owned scrolling child."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    app.press_hotkey("region")
    assert app.controller._scrolling.busy
    assert not app.controller.capture.is_selecting
    assert "scroll_cancel" in app.hotkeys.registered
    finish(qtbot, app, helper)
    qtbot.waitUntil(lambda: app.controller.capture.is_selecting)
    assert not app.controller.workspace.documents
    assert "scroll_cancel" not in app.hotkeys.registered
    app.controller.capture.cancel()


@pytest.mark.parametrize("change", ["pause", "apply"])
def test_hotkey_changes_keep_escape_until_owned_child_exits(
    make_app: AppFactory, qtbot: QtBot, change: str
) -> None:
    """UX-CAP-18: settings changes cannot silently remove active cancellation."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    if change == "pause":
        app.controller._pause_hotkeys(paused=True)
    else:
        app.controller.apply_settings(
            replace(
                app.controller.settings,
                hotkeys=replace(
                    app.controller.settings.hotkeys, scrolling="ctrl+alt+f9"
                ),
            )
        )
    assert "scroll_cancel" in app.hotkeys.registered
    assert app.controller._scrolling.busy
    finish(qtbot, app, helper)
    assert not app.controller.workspace.documents
    assert "scroll_cancel" not in app.hotkeys.registered
    if change == "pause":
        assert not app.hotkeys.registered
        app.controller._pause_hotkeys(paused=False)
    assert len(app.hotkeys.registered) == 5
    if change == "apply":
        assert str(app.hotkeys.registered["scrolling"]) == "ctrl+alt+f9"


def test_exit_refusal_retires_later_child_and_resumes_ordinary_request(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18: failed synchronous exit never strands the running app."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    shutdown = helper.shutdown
    monkeypatch.setattr(
        helper, "shutdown", Mock(side_effect=CaptureError("Still running"))
    )
    quit_app = Mock()
    monkeypatch.setattr(QApplication, "quit", quit_app)
    app.controller.request_exit()
    quit_app.assert_not_called()
    assert app.controller._scrolling.busy
    app.press_hotkey("fullscreen")
    assert not app.controller.workspace.documents
    monkeypatch.setattr(helper, "shutdown", shutdown)
    finish(qtbot, app, helper)
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)
    assert "scroll_cancel" not in app.hotkeys.registered
    assert app.controller.editors[0].session.document.image.height() != 1200


def test_settings_request_waits_for_retirement(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18: settings cannot activate over an unfinished helper."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    app.controller.show_settings()
    shown = Mock()
    monkeypatch.setattr(app.controller, "show_settings", shown)
    shown.assert_not_called()
    finish(qtbot, app, helper)
    qtbot.waitUntil(lambda: shown.called)
    assert not app.controller.workspace.documents


def test_background_update_is_busy_and_manual_update_waits(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CAP-18: the child remains an update blocker through cancellation."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    assert app.controller._defer_automatic_install()
    assert app.controller._defer_scroll_update(manual=True)
    install = Mock()
    monkeypatch.setattr(app.controller, "install_update", install)
    install.assert_not_called()
    finish(qtbot, app, helper)
    qtbot.waitUntil(lambda: install.called)
    install.assert_called_once_with(manual=True)


@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
@pytest.mark.parametrize(
    "gesture", ["click", "enter", "no-hbar-click", "no-hbar-enter"]
)
def test_visible_point_chooser_uses_completed_widget_input(
    make_app: AppFactory,
    qtbot: QtBot,
    snapshot_dir: Path | None,
    theme: Theme,
    gesture: str,
) -> None:
    """UX-CAP-17/19: no-hbar List pixels keep the same one-click/Enter journey."""
    settings = Settings()
    app = make_app(
        replace(settings, appearance=replace(settings.appearance, theme=theme))
    )
    helper = prepare(app)
    app.controller.tray.action("scrolling").trigger()
    service = app.controller._scrolling
    qtbot.waitUntil(lambda: bool(service.overlays))
    overlay = service.overlays[0]
    QCursor.setPos(overlay.mapToGlobal(QPoint(30, 40)))
    qtbot.mouseMove(overlay, QPoint(30, 40))
    if snapshot_dir is not None:
        assert overlay.grab().save(
            str(snapshot_dir / f"scrolling-chooser-{theme.value}-{gesture}.png")
        )
    if gesture.endswith("click"):
        qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 40))
        assert service._helper is None
        qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 40))
    else:
        qtbot.keyPress(overlay, Qt.Key.Key_Return)
        assert service._helper is None
        qtbot.keyRelease(overlay, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: service._helper is helper)
    result = (
        transaction(NormalizingListTarget(start=2400, no_hbar=True)).run()
        if gesture.startswith("no-hbar-")
        else None
    )
    image = finish(qtbot, app, helper, result)
    assert len(app.controller.workspace.documents) == 1
    assert app.controller.editors[0].isVisible()
    assert app.controller.editors[0].session.document.image == image.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


@pytest.mark.parametrize("arrival", ["prepare", "close"])
def test_manual_update_rechecks_capture_after_reentrant_preparation(
    make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, arrival: str
) -> None:
    """UX-CAP-18: modal callbacks cannot sneak a new capture past the final gate."""
    app = make_app()
    helper = prepare(app)
    updater = Mock()
    app.controller._updater = updater
    plan = Mock()

    def arrive() -> None:
        app.controller.capture_scrolling()
        select(qtbot, app, helper)

    def prepare_update(_relaunch: str) -> Mock:
        if arrival == "prepare":
            arrive()
        return plan

    def close_editors() -> bool:
        if arrival == "close":
            arrive()
        return True

    updater.prepare.side_effect = prepare_update
    monkeypatch.setattr(app.controller, "_close_editors", close_editors)
    app.controller.install_update(manual=True)
    updater.start.assert_not_called()
    assert app.controller._scrolling.busy
    assert "scroll_cancel" in app.hotkeys.registered
    resume = Mock()
    monkeypatch.setattr(app.controller, "install_update", resume)
    finish(qtbot, app, helper)
    qtbot.waitUntil(lambda: resume.called)
    resume.assert_called_once_with(manual=True)
    app.controller._updater = None


def test_successful_capture_surfaces_temporary_file_cleanup_warning(
    make_app: AppFactory, qtbot: QtBot, caplog: pytest.LogCaptureFixture
) -> None:
    """UX-CAP-19: a complete image does not hide a temporary-file cleanup failure."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    helper.finished.emit(
        ScrollResult(
            QImage(20, 50, QImage.Format.Format_RGB32),
            "Temporary scrolling files could not be removed",
            "restored",
            2,
        )
    )
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    assert len(app.controller.workspace.documents) == 1
    assert "Temporary scrolling files could not be removed" in caplog.text


def test_cleanup_warning_preserves_retained_editor_recovery_action(
    make_app: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UX-CAP-19: a cleanup warning must never replace the retained-tab recovery."""
    app = make_app()
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    show = EditorWindow.show

    def fail_show(_self: EditorWindow) -> None:
        raise RuntimeError("Presentation failed")

    monkeypatch.setattr(EditorWindow, "show", fail_show)
    helper.finished.emit(
        ScrollResult(
            QImage(20, 50, QImage.Format.Format_RGB32),
            "Temporary scrolling files could not be removed",
            "restored",
            2,
        )
    )
    qtbot.waitUntil(lambda: not app.controller._scrolling.busy)
    assert "Editor work retained" in caplog.text
    assert "Temporary scrolling files could not be removed" in caplog.text
    assert "Scrolling capture finished" not in caplog.text
    records = app.controller.workspace.documents
    assert len(records) == 1
    monkeypatch.setattr(EditorWindow, "show", show)
    app.controller._on_message_clicked()
    assert app.controller.workspace.documents == records
    assert len(app.controller.editors) == 1
    assert app.controller.editors[0].isVisible()


def test_contained_repeat_evidence_delivers_complete_image_without_changing_repeat(
    make_app: AppFactory, qtbot: QtBot
) -> None:
    """UX-CAP-19: supported repeated content uses ordinary delivery and restoration."""
    target = ContainmentTarget()
    original_position, original_pixels = target.position(), target.frame()
    result = transaction(target).run()
    expected = target.document.copy()
    expected.setPixelColor(1, 230, QColor("magenta"))
    assert result.image == expected
    assert result.restoration == "restored"
    assert target.position() == original_position
    assert target.frame() == original_pixels

    app = make_app()
    app.press_hotkey("window")
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 1)
    ordinary = app.controller.workspace.documents[0]
    repeat_area = app.controller.capture._last_area
    helper = prepare(app)
    app.press_hotkey("scrolling")
    select(qtbot, app, helper)
    finish(qtbot, app, helper, result)
    assert len(app.controller.workspace.documents) == 2
    assert app.controller.editors[0].session.document.image == expected.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    assert app.controller.capture._last_area == repeat_area
    app.press_hotkey("repeat")
    qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == 3)
    assert app.controller.editors[0].session.document.image.size() == (
        ordinary.session.document.image.size()
    )
    assert app.controller.capture._last_area == repeat_area
