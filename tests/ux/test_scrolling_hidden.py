"""Scrolling capture is hidden until its rewrite lands; nothing reaches it."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import AppFactory
from tests.ux.test_app_journeys import make_app as make_app  # noqa: PLC0414

from verdiclip.settings import OutputSettings, Settings
from verdiclip.shell.settings_dialog import SettingsDialog

pytestmark = pytest.mark.ux


def test_tray_and_shortcut_do_not_offer_scrolling(make_app: AppFactory) -> None:
    """The tray has no scrolling entry and Ctrl+Alt+PrtSc stays unregistered."""
    app = make_app()

    with pytest.raises(KeyError):
        app.controller.tray.action("scrolling")
    with pytest.raises(KeyError):
        app.controller.tray.action("copy_scroll_error")
    assert "scrolling" not in app.hotkeys.registered
    assert "region" in app.hotkeys.registered


def test_settings_hides_scrolling_but_keeps_its_saved_shortcut(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Accepting Settings keeps a custom scrolling shortcut for the rewrite."""
    settings = Settings(output=OutputSettings(directory=tmp_path))
    settings = replace(settings, hotkeys=replace(settings.hotkeys, scrolling="ctrl+f9"))
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)

    with pytest.raises(KeyError):
        dialog.hotkey_field("scrolling")
    assert dialog.result_settings().hotkeys.scrolling == "ctrl+f9"
