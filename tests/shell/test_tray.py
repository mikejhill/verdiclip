"""Tests for the tray icon's menu, tooltip and click handling."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from pytestqt.qtbot import QtBot

from verdiclip import APP_NAME, VERSION
from verdiclip.shell.tray import (
    CHECK_UPDATES_LABEL,
    UNIVERSAL_TIP,
    TrayCommands,
    TrayIcon,
)

IDLE_TIP = f"{APP_NAME} {VERSION} — click to capture"


class Calls:
    """Record which tray command ran."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, name: str) -> Callable[[], None]:
        """Return a callback that records ``name``."""
        return lambda: self.names.append(name)


def _commands(calls: Calls, *, optional: bool = True) -> TrayCommands:
    return TrayCommands(
        capture_region=calls("region"),
        capture_window=calls("window"),
        capture_fullscreen=calls("fullscreen"),
        repeat_last=calls("repeat"),
        open_image=calls("open"),
        open_folder=calls("folder"),
        settings=calls("settings"),
        about=calls("about"),
        exit=calls("exit"),
        update=calls("update") if optional else None,
        capture_scrolling=calls("scrolling") if optional else None,
        copy_scroll_error=calls("copy_scroll_error") if optional else None,
    )


@pytest.fixture
def calls() -> Calls:
    """Return a fresh call recorder."""
    return Calls()


@pytest.fixture
def tray(qtbot: QtBot, calls: Calls) -> TrayIcon:
    """Return a tray icon with every optional command."""
    del qtbot
    return TrayIcon(QIcon(), _commands(calls))


def _texts(tray: TrayIcon) -> list[str]:
    return [
        "---" if action.isSeparator() else action.text()
        for action in tray.menu.actions()
    ]


class TestMenu:
    """UX-TRY-03: the menu's entries, order and hotkey labels."""

    def test_full_menu_in_order(self, tray: TrayIcon) -> None:
        """Every entry appears once, grouped by separators."""
        assert _texts(tray) == [
            "Universal capture",
            "Capture active window",
            "Capture full screen",
            "Repeat last capture",
            "Capture scrolling area",
            "---",
            "Open image…",
            "Open screenshots folder",
            "---",
            "Settings…",
            CHECK_UPDATES_LABEL,
            f"About {APP_NAME}",
            "Exit",
        ]

    def test_optional_entries_absent_without_commands(
        self, qtbot: QtBot, calls: Calls
    ) -> None:
        """No update or scrolling entry when the app offers neither."""
        del qtbot
        tray = TrayIcon(QIcon(), _commands(calls, optional=False))
        tray.set_scroll_error_available()
        texts = _texts(tray)
        assert CHECK_UPDATES_LABEL not in texts
        assert "Capture scrolling area" not in texts
        assert "Copy last scrolling error" not in texts
        with pytest.raises(KeyError):
            tray.action("update")

    def test_hotkey_labels_follow_a_tab(self, tray: TrayIcon) -> None:
        """A hotkey shows after a tab; entries without one show no tab."""
        tray.rebuild_menu({"region": "Print", "fullscreen": "Ctrl+Print"})
        assert tray.action("region").text() == "Universal capture\tPrint"
        assert tray.action("fullscreen").text() == "Capture full screen\tCtrl+Print"
        assert tray.action("window").text() == "Capture active window"

    def test_labels_survive_state_changes(self, tray: TrayIcon) -> None:
        """Later rebuilds keep the hotkey labels given last."""
        tray.rebuild_menu({"repeat": "Shift+Print"})
        tray.set_update_label("Install VerdiClip 9.9.9…")
        assert tray.action("repeat").text() == "Repeat last capture\tShift+Print"
        assert tray.action("update").text() == "Install VerdiClip 9.9.9…"

    def test_universal_capture_explains_itself(self, tray: TrayIcon) -> None:
        """Only the universal entry carries the modifier tooltip."""
        assert tray.action("region").toolTip() == UNIVERSAL_TIP
        assert tray.action("window").toolTip() != UNIVERSAL_TIP
        assert tray.menu.toolTipsVisible()

    @pytest.mark.parametrize(
        "name",
        [
            "region",
            "window",
            "fullscreen",
            "repeat",
            "scrolling",
            "open",
            "folder",
            "settings",
            "update",
            "about",
            "exit",
        ],
    )
    def test_each_entry_runs_its_command(
        self, tray: TrayIcon, calls: Calls, name: str
    ) -> None:
        """Triggering an entry runs exactly its own command."""
        tray.action(name).trigger()
        assert calls.names == [name]

    def test_rebuild_drops_stale_actions(self, tray: TrayIcon, calls: Calls) -> None:
        """A rebuilt menu has no duplicates and old actions no longer count."""
        before = len(tray.menu.actions())
        tray.rebuild_menu({})
        assert len(tray.menu.actions()) == before
        tray.action("exit").trigger()
        assert calls.names == ["exit"]


class TestUpdateEntry:
    """UX-TRY-11: the update entry's label and availability."""

    def test_checking_disables_and_renames(self, tray: TrayIcon) -> None:
        """A check in progress can't be started twice."""
        tray.set_update_checking(checking=True)
        action = tray.action("update")
        assert action.text() == "Checking for updates..."
        assert not action.isEnabled()

    def test_finished_check_restores_the_label(self, tray: TrayIcon) -> None:
        """After a check the entry is enabled with its own label again."""
        tray.set_update_label("Install VerdiClip 9.9.9…")
        tray.set_update_checking(checking=True)
        tray.set_update_checking(checking=False)
        action = tray.action("update")
        assert action.text() == "Install VerdiClip 9.9.9…"
        assert action.isEnabled()

    def test_other_entries_stay_enabled_while_checking(self, tray: TrayIcon) -> None:
        """Only the update entry is disabled during a check."""
        tray.set_update_checking(checking=True)
        assert tray.action("settings").isEnabled()


class TestScrollingState:
    """UX-CAP-21: scrolling capture stays visible and cancellable."""

    def test_idle_tooltip(self, tray: TrayIcon) -> None:
        """At rest the tooltip names the app, version and the click."""
        assert tray.toolTip() == IDLE_TIP

    def test_running(self, tray: TrayIcon) -> None:
        """While running, the entry cancels and the tooltip says Esc stops."""
        tray.set_scrolling_state(True, False)  # noqa: FBT003 - Qt slot signature
        assert tray.toolTip() == "Scrolling capture… Esc stops"
        assert tray.action("scrolling").text() == "Cancel scrolling capture"

    def test_stopping(self, tray: TrayIcon) -> None:
        """While stopping, the tooltip says so and cancel stays available."""
        tray.set_scrolling_state(True, True)  # noqa: FBT003 - Qt slot signature
        assert tray.toolTip() == "Stopping scrolling capture…"
        assert tray.action("scrolling").text() == "Cancel scrolling capture"

    def test_cancelling_flag_alone_is_idle(self, tray: TrayIcon) -> None:
        """A finished capture resets everything, whatever the cancel flag."""
        tray.set_scrolling_state(True, True)  # noqa: FBT003 - Qt slot signature
        tray.set_scrolling_state(False, True)  # noqa: FBT003 - Qt slot signature
        assert tray.toolTip() == IDLE_TIP
        assert tray.action("scrolling").text() == "Capture scrolling area"

    def test_copy_error_appears_after_a_failure(
        self, tray: TrayIcon, calls: Calls
    ) -> None:
        """UX-CAP-19: the last error stays copyable from the tray."""
        assert "Copy last scrolling error" not in _texts(tray)
        tray.set_scroll_error_available()
        texts = _texts(tray)
        assert texts.index("Copy last scrolling error") == (
            texts.index("Capture scrolling area") + 1
        )
        tray.action("copy_scroll_error").trigger()
        assert calls.names == ["copy_scroll_error"]


class TestClicks:
    """UX-TRY-02: click captures, double-click opens the folder."""

    def test_click_waits_out_the_double_click_interval(
        self, tray: TrayIcon, calls: Calls, qtbot: QtBot
    ) -> None:
        """A single click captures, but only after the interval passes."""
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        assert calls.names == []
        qtbot.waitUntil(lambda: calls.names == ["region"], timeout=2000)

    def test_double_click_opens_the_folder_only(
        self, tray: TrayIcon, calls: Calls, qtbot: QtBot
    ) -> None:
        """The pending capture is cancelled when the second click arrives."""
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert calls.names == ["folder"]
        qtbot.wait(QApplication.doubleClickInterval() + 100)
        assert calls.names == ["folder"]

    def test_other_activations_do_nothing(
        self, tray: TrayIcon, calls: Calls, qtbot: QtBot
    ) -> None:
        """A context-menu or middle click runs no command."""
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.MiddleClick)
        qtbot.wait(QApplication.doubleClickInterval() + 100)
        assert calls.names == []

    def test_the_menu_is_the_context_menu(self, tray: TrayIcon) -> None:
        """Right-click shows the same menu the tray exposes."""
        assert tray.contextMenu() is tray.menu
