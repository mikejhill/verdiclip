"""App journeys: hotkeys and tray through capture to the after-capture action."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_acquisition import Run
from tests.capture.test_scroll_panels import Controls
from tests.platform.test_associations import FakeAssociationBackend
from tests.platform.test_launcher import GUI, write_pe
from tests.platform.test_shortcuts import FakeShortcutBackend
from tests.update.test_service import FakeFeed, Spawns, write_result
from tests.ux.conftest import Stopwatch
from tests.ux.test_capture_journeys import (
    CaptureOf,
    FakeDesktop,
    FakeWindow,
    FakeWindows,
    Overlay,
)

from verdiclip import APP_NAME, DOCS_URL, VERSION
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.capture.service import WindowSource
from verdiclip.document.commands import SetCrop
from verdiclip.document.style import Color
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.session import ToolId
from verdiclip.editor.style_bar import ColorButton
from verdiclip.editor.window import EditorWindow
from verdiclip.exceptions import DeliveryError, UpdateError
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.associations import DEFAULT_APPS_URI, FileAssociations
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.launcher import LaunchCommand
from verdiclip.platform.shortcuts import Shortcut, ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.settings import (
    CaptureSettings,
    IntegrationSettings,
    Settings,
    SettingsStore,
    StartupSettings,
    Theme,
    UpdateSettings,
)
from verdiclip.shell import controller as controller_module
from verdiclip.shell.controller import AppController, ShellIntegration
from verdiclip.shell.instance import SingleInstance
from verdiclip.shell.settings_dialog import SettingsDialog
from verdiclip.shell.tray import TrayIcon
from verdiclip.update.install import (
    Installation,
    InstallKind,
    UpdatePlan,
    UpdatePlanner,
)
from verdiclip.update.service import UpdateLauncher, Updater

pytestmark = pytest.mark.ux


@dataclass(slots=True)
class FakeHotkeys:
    """RegisterHotKey stand-in; ``taken`` keys belong to another program."""

    taken: set[str] = field(default_factory=set)
    active: dict[int, tuple[int, int]] = field(default_factory=dict)

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
        """Register unless the combination is taken."""
        del hwnd
        for text in self.taken:
            hotkey = Hotkey.parse(text)
            if (hotkey.win32_modifiers, hotkey.virtual_key) == (modifiers, vk):
                return False
        self.active[hotkey_id] = (modifiers, vk)
        return True

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        """Release a registration."""
        del hwnd
        return self.active.pop(hotkey_id, None) is not None


@dataclass(slots=True)
class FakeRegistry:
    """The legacy Run key."""

    values: dict[str, str] = field(default_factory=dict)

    def read_value(self, name: str) -> str | None:
        """Return a value."""
        return self.values.get(name)

    def write_value(self, name: str, value: str) -> None:
        """Store a value."""
        self.values[name] = value

    def delete_value(self, name: str) -> None:
        """Remove a value."""
        self.values.pop(name, None)


@dataclass(frozen=True, slots=True)
class ShortcutFolders:
    """Stand-ins for the user's Startup and Start menu Programs folders."""

    startup: Path
    programs: Path


class BlockedWindows(QObject):
    """Record windows that a modal dialog blocks from input."""

    def __init__(self) -> None:
        super().__init__()
        self.overlays = 0
        self.editors: list[EditorWindow] = []

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Record WindowBlocked events sent to an overlay or an editor."""
        if event.type() == QEvent.Type.WindowBlocked:
            if isinstance(watched, SelectionOverlay):
                self.overlays += 1
            elif isinstance(watched, EditorWindow):
                self.editors.append(watched)
        return False


@dataclass(slots=True)
class App:
    """A controller over fakes, plus handles to the fakes."""

    controller: AppController
    hotkeys: HotkeyService
    os_hotkeys: FakeHotkeys
    registry: FakeRegistry
    store: SettingsStore
    associations: FakeAssociationBackend
    links: FakeShortcutBackend
    folders: ShortcutFolders

    @property
    def startup_shortcut(self) -> Path:
        """Return where the Startup-folder shortcut goes."""
        return self.folders.startup / "VerdiClip.lnk"

    @property
    def start_menu_shortcut(self) -> Path:
        """Return where the Start menu shortcut goes."""
        return self.folders.programs / "VerdiClip.lnk"

    def press_hotkey(self, action: str) -> None:
        """Simulate Windows delivering WM_HOTKEY for ``action``."""
        hotkey = self.hotkeys.registered[action]
        for hotkey_id, combo in self.os_hotkeys.active.items():
            if combo == (hotkey.win32_modifiers, hotkey.virtual_key):
                self.hotkeys.handle_hotkey_message(hotkey_id)
                QApplication.processEvents()  # Return from native dispatch first.
                return
        msg = f"{action} is not registered"
        raise AssertionError(msg)


PYTHONW = Path("C:/Python/pythonw.exe")

type AppFactory = Callable[..., App]


@pytest.fixture
def make_app(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[AppFactory]:
    """Return a factory for a started app with the given settings."""
    created: list[App] = []
    # Launcher choice depends on the host's Python; its own tests cover it
    monkeypatch.setattr(
        controller_module,
        "tray_launch",
        lambda *_args: LaunchCommand(PYTHONW, "-m verdiclip"),
    )

    def factory(  # noqa: PLR0913 - explicit screen/window OS boundary injection
        settings: Settings | None = None,
        taken: set[str] | None = None,
        links: FakeShortcutBackend | None = None,
        updater: Updater | None = None,
        screens: ScreenSource | None = None,
        *,
        windows: WindowSource | None = None,
    ) -> App:
        store = SettingsStore(tmp_path / "settings.json")
        base = settings or Settings()
        store.save(
            replace(base, output=replace(base.output, directory=tmp_path / "out"))
        )
        os_hotkeys = FakeHotkeys(taken=taken or set())
        hotkeys = HotkeyService(os_hotkeys)
        registry = FakeRegistry()
        default_window = FakeWindow("Notepad", Rect(10, 10, 300, 200), 1)
        window_source = (
            windows
            if windows is not None
            else FakeWindows([default_window], active=default_window)
        )
        associations = FakeAssociationBackend()
        links = links if links is not None else FakeShortcutBackend()
        folders = ShortcutFolders(tmp_path / "Startup", tmp_path / "Programs")
        controller = AppController(
            store,
            screens if screens is not None else FakeDesktop(),
            window_source,
            hotkeys,
            ShellIntegration(
                StartupRegistration(
                    ShortcutRegistration(folders.startup, links), registry
                ),
                FileAssociations(associations),
                ShortcutRegistration(folders.programs, links),
                updater=updater,
            ),
        )
        controller.start()
        app = App(
            controller,
            hotkeys,
            os_hotkeys,
            registry,
            store,
            associations,
            links,
            folders,
        )
        created.append(app)
        return app

    yield factory
    for app in created:
        for record in app.controller.workspace.documents:
            record.session.history.mark_delivered()
        for editor in app.controller.editors:
            editor.session.history.mark_delivered()
            editor.close()
        app.controller.shutdown()
    qtbot.wait(0)  # Let closed editors process their deferred deletes


class TestAfterCapture:
    """What happens when a capture finishes."""

    def test_hotkey_opens_focused_editor_with_select_tool(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """By default a capture opens an editor quickly, Select tool active.

        UX-CAP-11.
        """
        app = make_app()
        clock = Stopwatch()

        app.press_hotkey("fullscreen")

        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor: EditorWindow = app.controller.editors[0]
        qtbot.waitExposed(editor)
        assert clock.elapsed_ms < 300
        assert editor.action("tool_select").isChecked()

    def test_clipboard_action_copies_without_window(
        self, make_app: AppFactory, qtbot: QtBot, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Clipboard mode copies the image and opens nothing.

        UX-CAP-12.
        """
        caplog.set_level(logging.INFO)
        settings = Settings()
        app = make_app(
            replace(
                settings,
                capture=replace(
                    settings.capture, open_editor=False, copy_to_clipboard=True
                ),
            )
        )
        QGuiApplication.clipboard().clear()

        app.press_hotkey("window")

        qtbot.waitUntil(lambda: not QGuiApplication.clipboard().image().isNull())
        assert QGuiApplication.clipboard().image().width() == 300
        assert app.controller.editors == ()
        assert "Copied to clipboard" in caplog.text

    def test_file_action_saves_with_pattern(
        self, make_app: AppFactory, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """UX-CAP-13: after-capture "file" saves into the folder and says where."""
        caplog.set_level(logging.INFO)
        settings = Settings()
        app = make_app(
            replace(
                settings,
                capture=replace(settings.capture, open_editor=False, save_to_file=True),
            )
        )

        app.press_hotkey("window")

        saved = list((tmp_path / "out").glob("*.png"))
        assert len(saved) == 1
        assert saved[0].stem.endswith("_Notepad")
        assert str(saved[0]) in caplog.text
        assert app.controller.editors == ()

    def test_window_capture_saved_from_editor_is_named_after_window(
        self, make_app: AppFactory, tmp_path: Path, qtbot: QtBot
    ) -> None:
        """Ctrl+S right after a window capture names the file after the window.

        UX-OUT-02.
        """
        app = make_app()

        app.press_hotkey("window")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor: EditorWindow = app.controller.editors[0]
        editor.action("save").trigger()

        saved = list((tmp_path / "out").glob("*.png"))
        assert [p.stem.split("_", 2)[2] for p in saved] == ["Notepad"]

    def test_all_three_actions_run_together(
        self, make_app: AppFactory, tmp_path: Path, qtbot: QtBot
    ) -> None:
        """Save, copy, and edit all happen; the editor is linked to the saved file.

        UX-CAP-13, UX-CAP-14.
        """
        settings = Settings()
        everything = replace(
            settings.capture,
            open_editor=True,
            copy_to_clipboard=True,
            save_to_file=True,
        )
        app = make_app(replace(settings, capture=everything))
        QGuiApplication.clipboard().clear()

        app.press_hotkey("window")

        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        saved = list((tmp_path / "out").glob("*.png"))
        editor = app.controller.editors[0]
        assert len(saved) == 1
        assert editor.saved_path == saved[0]
        assert saved[0].name in editor.windowTitle()
        assert QGuiApplication.clipboard().image().width() == 300

    def test_tray_left_click_starts_region_capture(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-TRY-02: clicking the tray icon starts a region capture."""
        app = make_app()

        app.controller.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)

        qtbot.waitUntil(lambda: app.controller.capture.is_selecting)
        app.controller.capture.cancel()

    def test_tray_double_click_opens_folder_without_capturing(
        self,
        make_app: AppFactory,
        qtbot: QtBot,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """UX-TRY-02: double-clicking the tray icon opens the screenshots folder.

        The first click of the pair must not start a capture.
        """
        opened: list[QUrl] = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url) or True
        )
        app = make_app()
        tray = app.controller.tray

        tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        qtbot.wait(QApplication.doubleClickInterval() + 100)

        assert not app.controller.capture.is_selecting
        assert [Path(url.toLocalFile()) for url in opened] == [tmp_path / "out"]
        assert (tmp_path / "out").is_dir()

    def test_tray_menu_opens_screenshots_folder(
        self, make_app: AppFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UX-TRY-03: "Open screenshots folder" opens the save folder."""
        opened: list[QUrl] = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url) or True
        )
        app = make_app()

        app.controller.tray.action("folder").trigger()

        assert [Path(url.toLocalFile()) for url in opened] == [tmp_path / "out"]

    def test_unopenable_screenshots_folder_is_reported(
        self,
        make_app: AppFactory,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """UX-TRY-03: a folder that can't be created or opened becomes a warning."""
        opened: list[QUrl] = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url) and False
        )
        app = make_app()

        app.controller.open_screenshots_folder()
        (tmp_path / "out").rmdir()
        (tmp_path / "out").write_text("not a folder")
        app.controller.open_screenshots_folder()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        assert all("Can't open screenshots folder" in r.message for r in warnings)
        assert len(opened) == 1


class TestHotkeysAndSettings:
    """Hotkey registration, conflicts, and settings changes."""

    def test_taken_hotkey_is_reported_and_menu_still_works(
        self, make_app: AppFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A hotkey owned by another program is reported; the menu entry still captures.

        UX-CAP-10.
        """
        caplog.set_level(logging.INFO)
        app = make_app(taken={"print_screen"})

        assert "in use by another program" in caplog.text
        assert "region" not in app.hotkeys.registered
        app.controller.tray.action("region").trigger()
        assert app.controller.capture.is_selecting
        app.controller.capture.cancel()

    def test_menu_shows_each_hotkey(self, make_app: AppFactory) -> None:
        """UX-TRY-03: tray menu entries show their hotkeys."""
        app = make_app()

        text = app.controller.tray.action("fullscreen").text()

        assert text.endswith("Ctrl+PrtSc")

    def test_applying_settings_rebinds_immediately(self, make_app: AppFactory) -> None:
        """UX-TRY-04: new hotkeys work at once, old ones stop, and the menu updates."""
        app = make_app()
        current = app.controller.settings

        app.controller.apply_settings(
            replace(current, hotkeys=replace(current.hotkeys, fullscreen="ctrl+alt+f9"))
        )

        assert str(app.hotkeys.registered["fullscreen"]) == "ctrl+alt+f9"
        assert app.controller.tray.action("fullscreen").text().endswith("Ctrl+Alt+F9")
        assert app.store.load().hotkeys.fullscreen == "ctrl+alt+f9"

    def test_recording_a_hotkey_pauses_global_hotkeys(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-TRY-09: while Settings records, PrtSc reaches it, not a capture."""
        app = make_app()
        registered = len(app.os_hotkeys.active)
        seen: list[int] = []
        recorded: list[str] = []

        def record_then_close() -> None:
            dialog = next(
                w
                for w in QApplication.topLevelWidgets()
                if isinstance(w, SettingsDialog) and w.isVisible()
            )
            field = dialog.hotkey_field("fullscreen")
            field.start_recording()
            seen.append(len(app.os_hotkeys.active))
            QTest.keyRelease(
                field.edit, Qt.Key.Key_Print, Qt.KeyboardModifier.NoModifier
            )
            seen.append(len(app.os_hotkeys.active))
            recorded.append(field.edit.text())
            dialog.reject()

        QTimer.singleShot(0, record_then_close)
        app.controller.tray.action("settings").trigger()
        qtbot.wait(0)

        assert registered == 4
        assert seen == [0, 4]
        assert recorded == ["PrtSc"]
        assert not app.controller.editors
        assert not app.controller.capture.is_selecting

    def test_run_at_login_writes_and_removes_startup_entry(
        self, make_app: AppFactory, tmp_path: Path
    ) -> None:
        """The run-at-login checkbox really changes the Windows startup entry.

        UX-TRY-04: a "VerdiClip" shortcut with the app icon in the Startup
        folder, which Windows lists by that name; the pre-0.5 Run value goes.
        """
        app = make_app()
        app.registry.values["VerdiClip"] = '"pythonw.exe" -m verdiclip'
        current = app.controller.settings

        app.controller.apply_settings(
            replace(current, startup=replace(current.startup, run_at_login=True))
        )
        shortcut = app.links.files.get(app.startup_shortcut)
        app.controller.apply_settings(
            replace(current, startup=replace(current.startup, run_at_login=False))
        )

        assert shortcut is not None
        assert shortcut.arguments == "-m verdiclip"
        assert shortcut.target == PYTHONW
        assert shortcut.icon == tmp_path / "verdiclip.ico"
        assert not QImage(str(shortcut.icon)).isNull()
        assert app.startup_shortcut not in app.links.files
        assert app.registry.values == {}

    def test_open_with_registers_and_unregisters(
        self, make_app: AppFactory, tmp_path: Path
    ) -> None:
        """UX-TRY-07: the Open with checkbox really changes Explorer's registration."""
        app = make_app()
        current = app.controller.settings
        command_key = r"Software\Classes\VerdiClip.Image\shell\open\command"

        app.controller.apply_settings(
            replace(current, integration=IntegrationSettings(open_with=True))
        )
        command = app.associations.keys[command_key][""]
        icon = Path(
            app.associations.keys[r"Software\VerdiClip\Capabilities"]["ApplicationIcon"]
        )
        app.controller.apply_settings(
            replace(current, integration=IntegrationSettings(open_with=False))
        )

        assert command == f'"{PYTHONW}" -m verdiclip open "%1"'
        assert icon == tmp_path / "verdiclip.ico"
        assert not QImage(str(icon)).isNull()
        assert command_key not in app.associations.keys
        assert app.store.load().integration.open_with is False

    def test_run_at_login_is_refreshed_at_start(self, make_app: AppFactory) -> None:
        """UX-TRY-04: a lost or outdated startup entry is rewritten on launch."""
        settings = Settings(startup=StartupSettings(run_at_login=True))

        app = make_app(settings)

        assert app.links.files[app.startup_shortcut].arguments == "-m verdiclip"

    def test_registrations_are_left_alone_when_off(self, make_app: AppFactory) -> None:
        """UX-TRY-04, 07, 08: launching never adds entries the user turned off."""
        settings = Settings(integration=IntegrationSettings(start_menu=False))

        app = make_app(settings)

        assert app.registry.values == {}
        assert app.associations.keys == {}
        assert app.links.files == {}

    def test_start_menu_entry_by_default(self, make_app: AppFactory) -> None:
        """UX-TRY-08: VerdiClip is in the Start menu unless the user opts out."""
        app = make_app()

        shortcut = app.links.files[app.start_menu_shortcut]

        assert shortcut.arguments == "-m verdiclip"
        assert shortcut.description
        assert app.startup_shortcut not in app.links.files

    def test_start_menu_entry_follows_setting(self, make_app: AppFactory) -> None:
        """UX-TRY-08: unchecking removes the entry; rechecking restores it."""
        app = make_app()
        current = app.controller.settings
        off = replace(current, integration=IntegrationSettings(start_menu=False))

        app.controller.apply_settings(off)
        removed = app.start_menu_shortcut not in app.links.files
        app.controller.apply_settings(current)

        assert removed
        assert app.start_menu_shortcut in app.links.files
        assert app.store.load().integration.start_menu is True

    def test_launch_keeps_shortcuts_of_another_install(
        self, make_app: AppFactory, tmp_path: Path
    ) -> None:
        """UX-TRY-04, 08: a second checkout doesn't take over existing shortcuts."""
        main = tmp_path / "main" / "pythonw.exe"
        main.parent.mkdir()
        write_pe(main, GUI)
        installed = Shortcut(main, "-m verdiclip", tmp_path, main, "VerdiClip")
        links = FakeShortcutBackend()
        folders = ShortcutFolders(tmp_path / "Startup", tmp_path / "Programs")
        links.files[folders.programs / "VerdiClip.lnk"] = installed
        links.files[folders.startup / "VerdiClip.lnk"] = installed
        settings = Settings(startup=StartupSettings(run_at_login=True))

        app = make_app(settings, links=links)
        app.controller.apply_settings(
            replace(
                app.controller.settings, capture=CaptureSettings(copy_to_clipboard=True)
            )
        )

        assert app.links.files[app.start_menu_shortcut] == installed
        assert app.links.files[app.startup_shortcut] == installed

    def test_open_with_is_refreshed_at_start(self, make_app: AppFactory) -> None:
        """UX-TRY-07: a moved install re-registers its current command on launch."""
        app = make_app(Settings(integration=IntegrationSettings(open_with=True)))

        command_key = r"Software\Classes\VerdiClip.Image\shell\open\command"
        assert app.associations.keys[command_key][""].endswith('open "%1"')

    def test_make_default_registers_and_opens_windows_settings(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UX-TRY-07: "Make default" registers, saves, and opens Default apps."""
        opened: list[str] = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
        )
        app = make_app()

        app.controller.make_default_image_app()
        app.controller.make_default_image_app()

        assert opened == [DEFAULT_APPS_URI, DEFAULT_APPS_URI]
        assert QUrl(DEFAULT_APPS_URI).scheme() == "ms-settings"
        assert app.store.load().integration.open_with is True
        assert app.associations.notified == 2

    def test_open_with_failure_is_reported(
        self, make_app: AppFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        """UX-TRY-07: a registry failure becomes a warning, not a crash."""
        app = make_app()
        app.associations.error = PermissionError("denied")

        with caplog.at_level(logging.WARNING):
            app.controller.apply_settings(
                replace(
                    app.controller.settings,
                    integration=IntegrationSettings(open_with=True),
                )
            )

        assert "Could not change Open with" in caplog.text

    def test_editor_defaults_follow_settings(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-TRY-04: a new default color is used by the next editor."""
        app = make_app()
        current = app.controller.settings
        blue = Color(0, 0, 255)
        app.controller.apply_settings(
            replace(current, editor=replace(current.editor, stroke_color=blue))
        )

        app.press_hotkey("fullscreen")

        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        assert (
            app.controller.editors[0].session.styles.get(ToolId.RECTANGLE).stroke
            == blue
        )


class TestSettingsEverywhere:
    """Settings reachable and effective from anywhere."""

    @pytest.mark.parametrize("entry", ["hotkey", "menu"])
    def test_tray_capture_preserves_popup_until_user_action(
        self,
        make_app: AppFactory,
        qtbot: QtBot,
        entry: str,
    ) -> None:
        """Capture preserves a hotkey-opened menu; user activation may dismiss it."""
        app = make_app()
        menu = app.controller.tray.menu
        try:
            menu.popup(QPoint(80, 80))
            assert QApplication.activePopupWidget() is menu
            if entry == "hotkey":
                app.press_hotkey("region")
            else:
                menu.setActiveAction(app.controller.tray.action("region"))
                QTest.keyClick(menu.windowHandle(), Qt.Key.Key_Return)
            qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
            if entry == "hotkey":
                assert menu.isVisible()
                assert QApplication.activePopupWidget() is menu
            else:
                assert not menu.isVisible(), "The user activated the menu action"
            overlay = app.controller.capture.overlays[0]
            overlay.receive_input(
                SelectionInput(InputKind.CANCEL, Point(120, 120), "escape")
            )
            assert not app.controller.capture.is_selecting
            assert app.controller.editors == (), "Escape must not deliver an image"
        finally:
            menu.close()

    def test_editor_shortcut_opens_settings(
        self, make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UX-TRY-06: Ctrl+, in an editor opens the Settings dialog."""
        opened: list[QWidget | None] = []

        def fake_exec(dialog: SettingsDialog) -> int:
            opened.append(dialog.parentWidget())
            return SettingsDialog.DialogCode.Rejected

        monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        editor.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is editor)

        ctrl = Qt.KeyboardModifier.ControlModifier
        QTest.keyClick(editor.canvas, Qt.Key.Key_Comma, ctrl)

        assert opened == [editor]

    @pytest.mark.parametrize("dialog", [SettingsDialog, QMessageBox])
    def test_region_capture_works_while_a_tray_dialog_is_open(
        self, make_app: AppFactory, qtbot: QtBot, dialog: type[QDialog]
    ) -> None:
        """UX-G-10: no dialog from the tray blocks the capture overlay's input."""
        app = make_app()
        captured: list[Capture] = []
        app.controller.capture.captured.connect(captured.append)
        blocked = BlockedWindows()
        qt_app = QApplication.instance()
        assert qt_app is not None
        qt_app.installEventFilter(blocked)

        def capture_then_close() -> None:
            dialogs = [
                widget
                for widget in QApplication.topLevelWidgets()
                if isinstance(widget, dialog) and widget.isVisible()
            ]
            assert len(dialogs) == 1
            original_modality = dialogs[0].windowModality()
            app.press_hotkey("region")
            overlay = Overlay(app.controller.capture).widget
            for kind, point in (
                (InputKind.PRESS, Point(90, 90)),
                (InputKind.MOVE, Point(160, 140)),
                (InputKind.RELEASE, Point(160, 140)),
            ):
                overlay.receive_input(SelectionInput(kind, point))
            assert dialogs[0].isVisible()
            assert dialogs[0].windowModality() == original_modality
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, dialog) and widget.isVisible():
                    widget.reject()

        name = "settings" if dialog is SettingsDialog else "about"
        QTimer.singleShot(0, capture_then_close)
        try:
            app.controller.tray.action(name).trigger()
        finally:
            qt_app.removeEventFilter(blocked)
        qtbot.wait(0)

        assert [c.area for c in captured] == [Rect(90, 90, 70, 50)]

    def test_editor_dialog_blocks_only_its_editor(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-G-10: a dialog from an editor blocks that editor, not the overlay."""
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        qtbot.waitExposed(editor)
        captured: list[Capture] = []
        app.controller.capture.captured.connect(captured.append)
        blocked = BlockedWindows()
        qt_app = QApplication.instance()
        assert qt_app is not None
        qt_app.installEventFilter(blocked)
        box = QMessageBox(QMessageBox.Icon.Warning, "VerdiClip", "Oops", parent=editor)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        try:
            box.show()
            qtbot.waitExposed(box)
            original_modality = box.windowModality()
            app.press_hotkey("region")
            overlay = Overlay(app.controller.capture).widget
            for kind, point in (
                (InputKind.PRESS, Point(90, 90)),
                (InputKind.MOVE, Point(160, 140)),
                (InputKind.RELEASE, Point(160, 140)),
            ):
                overlay.receive_input(SelectionInput(kind, point))
            assert box.isVisible()
            assert box.windowModality() == original_modality
        finally:
            box.reject()
            qt_app.removeEventFilter(blocked)
        qtbot.wait(0)

        assert editor in blocked.editors
        assert [c.area for c in captured] == [Rect(90, 90, 70, 50)]

    def test_about_names_version_and_license(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-TRY-03: About shows the version and license, nothing else."""
        app = make_app()
        texts: list[str] = []

        def read_then_close() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    texts.append(widget.text())
                    widget.reject()

        QTimer.singleShot(0, read_then_close)
        app.controller.tray.action("about").trigger()
        qtbot.wait(0)

        assert len(texts) == 1
        assert VERSION in texts[0]
        assert "MIT licensed" in texts[0]
        assert "Greenshot" not in texts[0]

    @pytest.mark.parametrize(
        ("theme", "scheme"),
        [
            (Theme.DARK, Qt.ColorScheme.Dark),
            (Theme.LIGHT, Qt.ColorScheme.Light),
        ],
    )
    def test_theme_applies_immediately(
        self,
        make_app: AppFactory,
        monkeypatch: pytest.MonkeyPatch,
        theme: Theme,
        scheme: Qt.ColorScheme,
    ) -> None:
        """UX-G-08: choosing a theme asks Qt for that color scheme at once.

        The offscreen test platform cannot render themes, so this checks the
        request; the manual checklist covers the visible result.
        """
        requested: list[Qt.ColorScheme] = []
        hints = QGuiApplication.styleHints()
        monkeypatch.setattr(hints, "setColorScheme", requested.append)
        app = make_app()
        current = app.controller.settings

        app.controller.apply_settings(
            replace(current, appearance=replace(current.appearance, theme=theme))
        )

        assert requested[-1] == scheme


class TestHelpMenu:
    """The editor's Help menu: documentation and About."""

    def test_help_is_the_last_menu(self, make_app: AppFactory, qtbot: QtBot) -> None:
        """UX-TRY-10: Help sits last on the menu bar, after Tools."""
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        bar = app.controller.editors[0].menuBar()

        titles = [a.text().replace("&", "") for a in bar.actions()]
        help_menu = bar.actions()[-1].menu()

        assert titles[-2:] == ["Tools", "Help"]
        assert isinstance(help_menu, QMenu)
        labels = [a.text().replace("&", "") for a in help_menu.actions()]
        assert labels == ["Documentation", "", f"About {APP_NAME}"]

    def test_f1_opens_the_documentation(
        self, make_app: AppFactory, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UX-TRY-10: F1 in an editor opens the documentation in the browser."""
        opened: list[str] = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
        )
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        editor.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is editor)

        QTest.keyClick(editor.canvas, Qt.Key.Key_F1)

        assert opened == [DOCS_URL]

    def test_about_opens_over_the_editor(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-TRY-10: Help → About shows the tray's About box over the editor."""
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        shown: list[tuple[QWidget | None, str]] = []

        def read_then_close() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    shown.append((widget.parentWidget(), widget.text()))
                    widget.reject()

        QTimer.singleShot(0, read_then_close)
        editor.action("about").trigger()
        qtbot.wait(0)

        assert len(shown) == 1
        parent, text = shown[0]
        assert parent is editor
        assert VERSION in text
        assert DOCS_URL in text


class TestClosePrompt:
    """Closing never silently loses a screenshot that wasn't saved or copied."""

    @pytest.fixture
    def asked(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Record every close prompt and answer Cancel."""
        prompts: list[str] = []

        def cancel(prompt: ClosePrompt, _parent: object) -> CloseChoice:
            prompts.append(prompt.message)
            return CloseChoice.CANCEL

        monkeypatch.setattr(ClosePrompt, "ask", cancel)
        return prompts

    def _capture(self, app: App, qtbot: QtBot) -> EditorWindow:
        """Capture the full screen and return the new editor."""
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        return app.controller.editors[0]

    def test_fresh_capture_asks_before_closing(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str]
    ) -> None:
        """UX-G-06: closing an untouched capture asks; Cancel keeps it open."""
        editor = self._capture(make_app(), qtbot)

        editor.close()

        assert len(asked) == 1
        assert "saved or copied" in asked[0]
        assert editor.isVisible()

    @pytest.mark.parametrize("shortcut", ["Ctrl+Shift+C", "Ctrl+S"])
    def test_copied_or_saved_capture_closes_without_asking(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str], shortcut: str
    ) -> None:
        """UX-G-06: copying or saving counts as saved."""
        editor = self._capture(make_app(), qtbot)
        editor.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is editor)
        mods = Qt.KeyboardModifier.ControlModifier
        if "Shift" in shortcut:
            mods |= Qt.KeyboardModifier.ShiftModifier
        key = Qt.Key.Key_C if shortcut.endswith("C") else Qt.Key.Key_S

        QTest.keyClick(editor.canvas, key, mods)
        editor.close()

        assert asked == []
        assert not editor.isVisible()

    def test_capture_already_copied_by_after_capture_action(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str]
    ) -> None:
        """UX-G-06: if the capture was auto-copied, closing doesn't ask."""
        settings = Settings()
        both = replace(settings.capture, open_editor=True, copy_to_clipboard=True)
        editor = self._capture(make_app(replace(settings, capture=both)), qtbot)

        editor.close()

        assert asked == []

    def test_turning_the_prompt_off_applies_to_open_editors(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str]
    ) -> None:
        """UX-G-06: the setting switches the prompt off, even for open editors."""
        app = make_app()
        editor = self._capture(app, qtbot)
        current = app.controller.settings
        app.controller.apply_settings(
            replace(
                current, editor=replace(current.editor, confirm_unsaved_close=False)
            )
        )

        editor.close()

        assert asked == []
        assert not editor.isVisible()

    def test_edited_file_names_the_file(
        self, make_app: AppFactory, tmp_path: Path, asked: list[str]
    ) -> None:
        """UX-G-06: an opened file with new edits asks to save changes to it."""
        picture = tmp_path / "existing.png"
        QImage(20, 20, QImage.Format.Format_RGB32).save(str(picture))
        editor = make_app().controller.open_image(picture)
        assert editor is not None
        editor.session.history.execute(SetCrop(Rect(0, 0, 20, 20), Rect(0, 0, 9, 9)))

        editor.close()

        assert asked == ["Save changes to “existing.png” before closing?"]

    def test_saved_then_edited_names_the_saved_file(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str]
    ) -> None:
        """UX-G-06: after saving, later edits are described as changes to that file."""
        editor = self._capture(make_app(), qtbot)
        assert editor.save()
        saved = editor.saved_path
        assert saved is not None
        editor.session.history.execute(SetCrop(Rect(0, 0, 50, 50), Rect(0, 0, 9, 9)))

        editor.close()

        assert asked == [f"Save changes to “{saved.name}” before closing?"]

    def test_copied_then_edited_says_so(
        self, make_app: AppFactory, qtbot: QtBot, asked: list[str]
    ) -> None:
        """UX-G-06: after copying, later edits are described as such."""
        editor = self._capture(make_app(), qtbot)
        assert editor.copy_image()
        editor.session.history.execute(SetCrop(Rect(0, 0, 50, 50), Rect(0, 0, 9, 9)))

        editor.close()

        assert len(asked) == 1
        assert "changed since you copied it" in asked[0]

    def test_opened_file_closes_without_asking(
        self, make_app: AppFactory, tmp_path: Path, asked: list[str]
    ) -> None:
        """UX-G-06: an image opened from disk is already saved."""
        picture = tmp_path / "existing.png"
        QImage(20, 20, QImage.Format.Format_RGB32).save(str(picture))
        editor = make_app().controller.open_image(picture)
        assert editor is not None

        editor.close()

        assert asked == []


class TestRememberedStyles:
    """Style choices carry over to the next screenshot."""

    def _open(self, app: App, qtbot: QtBot) -> EditorWindow:
        """Capture and select its tab without assuming a new top-level window."""
        before = len(app.controller.workspace.documents)
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.workspace.documents) == before + 1)
        record = app.controller.workspace.documents[-1]
        host = app.controller.workspace.host_of(record.identifier)
        assert host is not None
        host.activate_page(next(page for page in host.pages if page.record is record))
        return host

    @staticmethod
    def _pick_fill(editor: EditorWindow, hex_color: str) -> None:
        """Choose a fill from the style bar's palette menu, as a user would."""
        button = next(
            b for b in editor.findChildren(ColorButton) if b.toolTip() == "Fill color"
        )
        menu = button.menu()
        assert menu is not None
        action = next(a for a in menu.actions() if a.text() == hex_color)
        action.trigger()

    def test_fill_chosen_once_is_used_next_time(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-SEL-11: a fill picked for rectangles is the default in the next editor."""
        app = make_app()
        first = self._open(app, qtbot)
        first.activate_tool(ToolId.RECTANGLE)
        self._pick_fill(first, "#ffeb3b")
        first.close()

        second = self._open(app, qtbot)

        fill = second.session.styles.get(ToolId.RECTANGLE).fill
        assert fill == Color(0xFF, 0xEB, 0x3B)

    def test_new_defaults_in_settings_replace_remembered_choices(
        self, make_app: AppFactory, qtbot: QtBot
    ) -> None:
        """UX-SEL-11: changing editor defaults in Settings resets remembered styles."""
        app = make_app()
        first = self._open(app, qtbot)
        first.activate_tool(ToolId.RECTANGLE)
        self._pick_fill(first, "#ffeb3b")
        current = app.controller.settings
        green = Color(0, 160, 0)
        app.controller.apply_settings(
            replace(current, editor=replace(current.editor, stroke_color=green))
        )

        second = self._open(app, qtbot)

        rectangle = second.session.styles.get(ToolId.RECTANGLE)
        assert rectangle.stroke == green
        assert rectangle.fill.is_transparent


class TestOpeningImages:
    """Opening files."""

    def test_open_image_file_and_reject_bad_file(
        self, make_app: AppFactory, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """UX-OUT-06: images open in an editor; unreadable files are reported."""
        caplog.set_level(logging.INFO)
        app = make_app()
        good = tmp_path / "pic.png"
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(0xFF00FF)
        image.save(str(good))
        bad = tmp_path / "notes.png"
        bad.write_text("not an image", encoding="utf-8")

        opened = app.controller.open_image(good)
        rejected = app.controller.open_image(bad)

        assert opened is not None
        assert opened.saved_path == good
        assert rejected is None
        assert "Could not open image" in caplog.text

    def test_second_launch_forwards_files_to_running_instance(
        self, make_app: AppFactory, tmp_path: Path, qtbot: QtBot
    ) -> None:
        """UX-TRY-01: a second launch hands its files to the running instance."""
        app = make_app()
        name = f"verdiclip-test-{uuid.uuid4().hex}"
        primary = SingleInstance(name)
        assert primary.listen()
        primary.message_received.connect(app.controller.handle_forwarded)
        picture = tmp_path / "forwarded.png"
        QImage(20, 20, QImage.Format.Format_RGB32).save(str(picture))

        sender = QProcess()
        script = (
            "import sys; from PySide6.QtCore import QCoreApplication;"
            "app = QCoreApplication(sys.argv);"
            "from verdiclip.shell.instance import SingleInstance;"
            "sys.exit(0 if SingleInstance(sys.argv[1]).forward(sys.argv[2:]) else 3)"
        )

        sender.start(sys.executable, ["-c", script, name, str(picture)])

        qtbot.waitUntil(lambda: len(app.controller.editors) == 1, timeout=10_000)
        sender.waitForFinished(5_000)
        assert sender.exitCode() == 0
        primary.close()

    def test_no_running_instance_means_no_forwarding(self) -> None:
        """When nothing is running, the launch becomes the primary instance.

        UX-TRY-01.
        """
        assert not SingleInstance(f"verdiclip-test-{uuid.uuid4().hex}").forward([])


class TestExit:
    """Leaving the app."""

    def test_exit_quits_with_nothing_open(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit with no editors quits at once.

        UX-TRY-05.
        """
        app = make_app()
        quits: list[bool] = []
        monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))

        app.controller.request_exit()

        assert quits == [True]

    def test_exit_stops_when_an_editor_refuses_to_close(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot
    ) -> None:
        """Exit asks about undelivered work and Cancel keeps everything open.

        UX-TRY-05.
        """
        app = make_app()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        editor.session.history.execute(
            SetCrop(editor.session.document.crop, Rect(0, 0, 10, 10))
        )
        monkeypatch.setattr(ClosePrompt, "ask", lambda *_: CloseChoice.CANCEL)
        quits: list[bool] = []
        monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))

        app.controller.request_exit()

        assert editor.isVisible()
        assert quits == []


def test_capture_objects_are_complete(make_app: AppFactory, qtbot: QtBot) -> None:
    """Captures carry a title and timestamp for naming files."""
    app = make_app()

    with qtbot.waitSignal(app.controller.capture.captured) as signal:
        app.press_hotkey("window")

    capture = CaptureOf.signal(signal)
    assert capture.title == "Notepad"
    assert capture.taken_at.tzinfo is not None


@dataclass(slots=True)
class Balloons:
    """Tray balloons shown, as (title, message, warning)."""

    shown: list[tuple[str, str, bool]] = field(default_factory=list)

    def titles(self) -> list[str]:
        """Return the titles in order."""
        return [title for title, _, _ in self.shown]


@pytest.fixture
def balloons(monkeypatch: pytest.MonkeyPatch) -> Balloons:
    """Show the tray (there's none offscreen) and record its balloons."""
    recorded = Balloons()

    def show(
        _tray: TrayIcon,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon,
        _ms: int,
    ) -> None:
        warning = icon == QSystemTrayIcon.MessageIcon.Warning
        recorded.shown.append((title, message, warning))

    monkeypatch.setattr(TrayIcon, "isVisible", lambda _tray: True)
    monkeypatch.setattr(TrayIcon, "showMessage", show)
    return recorded


@dataclass(slots=True)
class Updates:
    """An updater over a fake PyPI and helper launch, for a 0.10.0 uv tool."""

    feed: FakeFeed
    spawns: Spawns
    folder: Path
    updater: Updater
    quits: list[bool]


@pytest.fixture
def updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Updates:
    """Return an updater whose latest release is 0.11.0; quitting is recorded."""
    feed, spawns = FakeFeed("0.11.0"), Spawns()
    folder = tmp_path / "update"
    kind = InstallKind.UV_TOOL
    updater = Updater(
        Installation(kind, "0.10.0", Path("C:/tools/verdiclip/python.exe")),
        feed,
        UpdateLauncher(folder, spawns),
        UpdatePlanner({"uv": "C:/uv/uv.exe"}.get, home=tmp_path),
    )
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))
    return Updates(feed, spawns, folder, updater, quits)


@pytest.fixture
def check_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the automatic check right after start instead of a minute later."""
    monkeypatch.setattr(controller_module, "FIRST_CHECK_MS", 0)


@pytest.fixture
def check_often(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check right after start and every millisecond after that."""
    monkeypatch.setattr(controller_module, "FIRST_CHECK_MS", 0)
    monkeypatch.setattr(controller_module, "CHECK_INTERVAL_MS", 1)


class TestUpdates:
    """Finding, installing, and reporting newer releases."""

    @staticmethod
    def status() -> QMessageBox:
        """Return the manual-check window a user can see."""
        return next(
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, QMessageBox)
            and widget.objectName() == "manual-update-status"
            and widget.isVisible()
        )

    @pytest.mark.parametrize("latest", ["0.10.0", "0.11.0"])
    @pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
    def test_manual_progress_then_persistent_result(
        self,
        make_app: AppFactory,
        updates: Updates,
        latest: str,
        theme: Theme,
        snapshot_dir: Path | None,
    ) -> None:
        """UX-TRY-11: a slow check stays visible, coalesces clicks, and finishes."""
        updates.feed.deferred = True
        settings = Settings()
        settings = replace(
            settings, appearance=replace(settings.appearance, theme=theme)
        )
        app = make_app(settings, updater=updates.updater)
        app.controller.tray.action("update").trigger()
        box = self.status()
        assert box.windowTitle() == "Checking for updates", "Progress must appear now"
        assert "PyPI" in box.text(), "Progress must explain what is being checked"
        action = app.controller.tray.action("update")
        assert action.text() == "Checking for updates..."
        assert not action.isEnabled(), "Repeated clicks must not start parallel checks"
        app.controller.check_for_updates()
        assert updates.feed.fetches == 1, "Direct duplicate requests must also coalesce"
        app.controller.tray.rebuild_menu({})
        assert not app.controller.tray.action("update").isEnabled()
        if snapshot_dir is not None:
            box.grab().save(str(snapshot_dir / f"{theme}-update-{latest}-checking.png"))
        box.close()
        updates.feed.fetched.emit(latest)
        assert self.status() is box, (
            "Completion must remain visible even if progress closed"
        )
        expected = (
            "VerdiClip is up to date" if latest == "0.10.0" else "Update available"
        )
        assert box.windowTitle() == expected
        assert latest in box.text(), "The result must identify the release"
        assert app.controller.tray.action("update").isEnabled()
        assert updates.spawns.calls == [], "Checking must not install anything"
        assert updates.quits == [], "Checking must keep the app running"
        if snapshot_dir is not None:
            box.grab().save(str(snapshot_dir / f"{theme}-update-{latest}-result.png"))

    def test_manual_failure_releases_progress_and_allows_retry(
        self,
        make_app: AppFactory,
        updates: Updates,
    ) -> None:
        """UX-TRY-11: failure stays visible and the next click can succeed."""
        updates.feed.deferred = True
        app = make_app(updater=updates.updater)
        app.controller.tray.action("update").trigger()
        updates.feed.failed.emit("Network unavailable")
        box = self.status()
        assert box.windowTitle() == "Could not check for updates"
        assert "Network unavailable" in box.text()
        assert "try again" in box.text()
        assert app.controller.tray.action("update").isEnabled()
        updates.feed.deferred = False
        updates.feed.latest = "0.10.0"
        app.controller.tray.action("update").trigger()
        assert updates.feed.fetches == 2
        assert box.windowTitle() == "VerdiClip is up to date"

    def test_synchronous_check_error_restores_retry(
        self,
        make_app: AppFactory,
        updates: Updates,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """UX-TRY-11: a request that cannot start cannot leave progress stuck."""

        def fail() -> None:
            """Simulate failure before a network reply exists."""
            raise UpdateError("Cannot start update check")

        monkeypatch.setattr(updates.updater, "check", fail)
        app = make_app(updater=updates.updater)
        app.controller.tray.action("update").trigger()
        assert self.status().windowTitle() == "Could not check for updates"
        assert "Cannot start" in self.status().text()
        assert app.controller.tray.action("update").isEnabled()

    @pytest.mark.parametrize(
        ("kind", "version"),
        [(InstallKind.UNKNOWN, "0.0.0"), (InstallKind.CHECKOUT, "0.12.0b1")],
    )
    def test_development_build_is_explicitly_unsupported(
        self,
        make_app: AppFactory,
        tmp_path: Path,
        kind: InstallKind,
        version: str,
    ) -> None:
        """UX-TRY-11: unidentifiable/beta builds must never claim to be current."""
        feed, spawns = FakeFeed(), Spawns()
        updater = Updater(
            Installation(kind, version, PYTHONW),
            feed,
            UpdateLauncher(tmp_path / "update", spawns),
        )
        app = make_app(updater=updater)
        app.controller.tray.action("update").trigger()
        assert self.status().windowTitle() == "Update check unavailable"
        assert "development" in self.status().text()
        assert feed.fetches == 0, "An incomparable build must not check the stable feed"
        assert app.controller.tray.action("update").isEnabled()
        assert spawns.calls == [], "An unsupported build must not launch an updater"

    def test_numeric_checkout_checks_the_stable_release(
        self,
        make_app: AppFactory,
        tmp_path: Path,
    ) -> None:
        """UX-TRY-11: a supported repository build still checks PyPI normally."""
        feed = FakeFeed("0.11.0")
        updater = Updater(
            Installation(InstallKind.CHECKOUT, "0.11.0", PYTHONW, tmp_path),
            feed,
            UpdateLauncher(tmp_path / "update", Spawns()),
        )
        app = make_app(updater=updater)
        app.controller.tray.action("update").trigger()
        assert self.status().windowTitle() == "VerdiClip is up to date"
        assert "latest stable release is 0.11.0" in self.status().text()
        assert feed.fetches == 1

    def test_no_updater_no_menu_entry(self, make_app: AppFactory) -> None:
        """Without an updater the tray offers no update entry."""
        app = make_app()

        with pytest.raises(KeyError):
            app.controller.tray.action("update")
        app.controller.check_for_updates()  # Nothing to do, nothing breaks
        app.controller.install_update(manual=True)

    def test_menu_check_says_up_to_date(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """Check for updates reports when nothing newer exists.

        UX-TRY-11.
        """
        updates.feed.latest = "0.10.0"
        app = make_app(updater=updates.updater)
        action = app.controller.tray.action("update")
        assert action.text() == "Check for updates"

        action.trigger()

        assert balloons.shown == [
            (
                "VerdiClip is up to date",
                "You have 0.10.0; the latest release is 0.10.0.",
                False,
            )
        ]
        assert app.controller.tray.action("update").text() == "Check for updates"

    def test_menu_check_offers_and_click_installs(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """A newer release is offered; clicking the balloon installs it.

        UX-TRY-11, UX-TRY-12.
        """
        app = make_app(updater=updates.updater)

        app.controller.tray.action("update").trigger()

        assert balloons.titles() == ["Update available"]
        assert "0.11.0 is available" in balloons.shown[0][1]
        assert (
            app.controller.tray.action("update").text() == "Install VerdiClip 0.11.0…"
        )
        assert updates.spawns.calls == []
        app.controller.tray.messageClicked.emit()
        assert len(updates.spawns.calls) == 1
        assert updates.quits == [True]
        plan = json.loads((updates.folder / "plan.json").read_text(encoding="utf-8"))
        assert plan["target"] == "0.11.0"
        assert plan["steps"] == [["C:/uv/uv.exe", "tool", "upgrade", "verdiclip"]]
        assert plan["relaunch"] == LaunchCommand(PYTHONW, "-m verdiclip").command_line

    def test_install_entry_installs_a_found_release(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """Once found, the menu entry installs without checking again.

        UX-TRY-12.
        """
        app = make_app(updater=updates.updater)
        app.controller.tray.action("update").trigger()
        fetches = updates.feed.fetches

        app.controller.tray.action("update").trigger()

        assert updates.feed.fetches == fetches
        assert updates.quits == [True]
        assert balloons.titles() == ["Update available"]

    def test_failed_menu_check_says_why(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """A check the user asked for reports why it failed.

        UX-TRY-11.
        """
        updates.feed.error = "Host pypi.org not found"
        app = make_app(updater=updates.updater)

        app.controller.tray.action("update").trigger()

        assert balloons.shown == [
            ("Could not check for updates", "Host pypi.org not found", True)
        ]

    @pytest.mark.usefixtures("check_at_once")
    def test_failed_automatic_check_is_quiet(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, qtbot: QtBot
    ) -> None:
        """A periodic check that fails only logs.

        UX-TRY-11.
        """
        updates.feed.error = "offline"
        make_app(updater=updates.updater)

        qtbot.waitUntil(lambda: updates.feed.fetches == 1)

        assert balloons.shown == []

    @pytest.mark.usefixtures("check_at_once")
    def test_automatic_check_finding_nothing_is_quiet(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, qtbot: QtBot
    ) -> None:
        """A periodic check that finds no newer release says nothing.

        UX-TRY-11.
        """
        updates.feed.latest = "0.10.0"
        app = make_app(updater=updates.updater)

        qtbot.waitUntil(lambda: updates.feed.fetches == 1)

        assert balloons.shown == []
        assert updates.quits == []
        assert app.controller.tray.action("update").text() == "Check for updates"

    @staticmethod
    def protected_context(kind: str) -> QWidget:
        """Show existing app UI without running a nested modal loop."""
        if kind == "popup":
            menu = QMenu()
            menu.addAction("Keep this menu open")
            menu.popup(QPoint(40, 40))
            return menu
        dialog = QDialog()
        if kind == "modal":
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        return dialog

    @pytest.mark.parametrize("context", ["popup", "modal", "modeless"])
    def test_tray_capture_keeps_pending_update_behind_existing_ui(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        context: str,
    ) -> None:
        """Completing capture cannot accelerate an update across preserved UI."""
        app = make_app(
            Settings(
                capture=CaptureSettings(open_editor=False, copy_to_clipboard=True)
            ),
            updater=updates.updater,
        )
        app.press_hotkey("region")
        overlay = Overlay(app.controller.capture).widget
        updates.updater.check()
        assert app.controller._idle_timer.isActive()
        existing = self.protected_context(context)
        qtbot.addWidget(existing)
        try:
            qtbot.waitExposed(existing)
            for kind, point in (
                (InputKind.PRESS, Point(90, 90)),
                (InputKind.MOVE, Point(160, 140)),
                (InputKind.RELEASE, Point(160, 140)),
            ):
                overlay.receive_input(SelectionInput(kind, point))
            qtbot.wait(10)
            assert not app.controller.capture.is_selecting
            assert app.controller.editors == ()
            assert existing.isVisible()
            assert updates.spawns.calls == []
            assert updates.quits == []
            assert app.controller._idle_timer.isActive()
        finally:
            existing.close()
        app.controller._install_when_idle()
        assert len(updates.spawns.calls) == 1
        assert updates.quits == [True]

    @pytest.mark.parametrize("context", ["popup", "modal", "modeless"])
    def test_direct_automatic_install_preserves_existing_ui(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        context: str,
    ) -> None:
        """The automatic-install API itself protects UI, independently of timers."""
        updates.updater.check()
        app = make_app(updater=updates.updater)
        existing = self.protected_context(context)
        qtbot.addWidget(existing)
        try:
            qtbot.waitExposed(existing)
            app.controller.install_update(manual=False)
            assert existing.isVisible()
            assert updates.spawns.calls == []
            assert updates.quits == []
            assert app.controller._idle_timer.isActive()
        finally:
            existing.close()
        app.controller.install_update(manual=False)
        assert len(updates.spawns.calls) == 1
        assert updates.quits == [True]

    @pytest.mark.parametrize(
        "late", ["popup", "modal", "modeless", "capture", "document"]
    )
    def test_automatic_install_rechecks_work_created_during_prepare(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
        late: str,
    ) -> None:
        """Late capture, document or dialog must veto launch without close prompts."""
        updates.updater.check()
        app = make_app(updater=updates.updater)
        prepare = updates.updater.prepare
        shown: list[QWidget] = []

        def prepare_with_arrival(relaunch: str) -> UpdatePlan:
            plan = prepare(relaunch)
            if late == "capture":
                app.press_hotkey("region")
            elif late == "document":
                image = QImage(10, 10, QImage.Format.Format_RGB32)
                image.fill(Qt.GlobalColor.white)
                app.controller._on_captured(
                    Capture(
                        image,
                        CaptureMode.FULLSCREEN,
                        Rect(0, 0, 10, 10),
                        "Arrived during update preparation",
                        datetime.now().astimezone(),
                    )
                )
            else:
                widget = self.protected_context(late)
                qtbot.addWidget(widget)
                shown.append(widget)
            return plan

        def must_not_close() -> bool:
            pytest.fail(
                "Automatic update must not close content admitted during prepare"
            )

        monkeypatch.setattr(updates.updater, "prepare", prepare_with_arrival)
        monkeypatch.setattr(app.controller, "_close_editors", must_not_close)
        try:
            app.controller.install_update(manual=False)
            assert updates.spawns.calls == []
            assert updates.quits == []
            assert app.controller._idle_timer.isActive()
            if late == "document":
                assert len(app.controller.workspace.documents) == 1
            elif late == "capture":
                assert app.controller.capture.is_selecting
            else:
                assert shown[0].isVisible()
        finally:
            app.controller.capture.cancel()
            for widget in shown:
                widget.close()

    @pytest.mark.parametrize("arrival", ["before", "during_prepare"])
    def test_automatic_install_retains_failed_tray_output(
        self,
        make_app: AppFactory,
        updates: Updates,
        monkeypatch: pytest.MonkeyPatch,
        sample_image: QImage,
        arrival: str,
    ) -> None:
        """An image awaiting retry must survive even when it has no editor or UI."""
        updates.updater.check()
        app = make_app(
            Settings(
                capture=CaptureSettings(open_editor=False, copy_to_clipboard=True)
            ),
            updater=updates.updater,
        )
        prepare = updates.updater.prepare

        def unavailable(_delivery: ImageDelivery, _image: QImage) -> None:
            raise DeliveryError("Clipboard temporarily unavailable")

        monkeypatch.setattr(ImageDelivery, "copy", unavailable)
        monkeypatch.setattr(
            ClosePrompt,
            "ask",
            lambda *_args: pytest.fail(
                "Automatic installation cannot discard captures"
            ),
        )

        def retain() -> None:
            app.controller._on_captured(
                Capture(
                    sample_image,
                    CaptureMode.FULLSCREEN,
                    Rect(0, 0, sample_image.width(), sample_image.height()),
                    "Retained without an editor",
                    datetime.now().astimezone(),
                )
            )

        def prepare_with_arrival(relaunch: str) -> UpdatePlan:
            plan = prepare(relaunch)
            retain()
            return plan

        if arrival == "before":
            retain()
        else:
            monkeypatch.setattr(updates.updater, "prepare", prepare_with_arrival)
        app.controller.install_update(manual=False)
        assert updates.spawns.calls == []
        assert updates.quits == []
        assert app.controller.editors == ()
        retained = app.controller.pending_deliveries
        assert len(retained) == 1
        assert retained[0].image == sample_image
        assert app.controller._idle_timer.isActive()
        monkeypatch.setattr(updates.updater, "prepare", prepare)
        monkeypatch.setattr(ImageDelivery, "copy", lambda _self, _image: None)
        app.controller.retry_delivery(retained[0].capture_id)
        assert app.controller.pending_deliveries == ()
        app.controller._install_when_idle()
        assert len(updates.spawns.calls) == 1
        assert updates.quits == [True]

    @pytest.mark.parametrize("arrival", ["before", "during_prepare"])
    def test_automatic_install_retains_hidden_unaccepted_review(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
        arrival: str,
    ) -> None:
        """Hiding review presentation cannot turn producer-owned pixels into idle."""
        updates.updater.check()
        app = make_app(updater=updates.updater)
        prepare = updates.updater.prepare
        panels = []

        def retain() -> None:
            run = Run(updates.folder.parent / "review-spool")
            run.start()
            run.settle()
            run.driver.stop()
            panel = app.controller.review_scrolling_capture(run.session)
            qtbot.addWidget(panel)
            panels.append(panel)

        def prepare_with_arrival(relaunch: str) -> UpdatePlan:
            plan = prepare(relaunch)
            retain()
            return plan

        if arrival == "before":
            retain()
        else:
            monkeypatch.setattr(updates.updater, "prepare", prepare_with_arrival)
        app.controller.install_update(manual=False)
        assert updates.spawns.calls == []
        assert updates.quits == []
        assert app.controller.workspace.documents == ()
        assert app.controller._scroll_reviews.has_unaccepted
        assert not panels[0].isVisible()
        assert app.controller._idle_timer.isActive()
        monkeypatch.setattr(updates.updater, "prepare", prepare)
        Controls.button(panels[0], "Discard").click()
        assert not app.controller._scroll_reviews.has_work
        app.controller._install_when_idle()
        assert len(updates.spawns.calls) == 1
        assert updates.quits == [True]

    @pytest.mark.usefixtures("check_at_once")
    def test_automatic_install_when_idle(
        self, make_app: AppFactory, updates: Updates, qtbot: QtBot
    ) -> None:
        """With nothing open, a found release installs by itself.

        UX-TRY-12.
        """
        make_app(updater=updates.updater)

        qtbot.waitUntil(lambda: updates.quits == [True])

        assert len(updates.spawns.calls) == 1

    @pytest.mark.usefixtures("check_at_once")
    def test_automatic_install_waits_for_the_last_editor(
        self, make_app: AppFactory, updates: Updates, qtbot: QtBot
    ) -> None:
        """An open editor holds the update back until it closes.

        UX-TRY-12.
        """
        app = make_app(updater=updates.updater)
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        qtbot.waitUntil(lambda: updates.feed.fetches == 1)
        qtbot.wait(20)
        assert updates.quits == []

        editor = app.controller.editors[0]
        editor.session.history.mark_delivered()
        editor.close()

        qtbot.waitUntil(lambda: updates.quits == [True])
        assert len(updates.spawns.calls) == 1

    @pytest.mark.usefixtures("check_at_once")
    @pytest.mark.parametrize(
        "turned_off", [UpdateSettings(install=False), UpdateSettings(check=False)]
    )
    def test_deferred_install_is_dropped_when_turned_off(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        turned_off: UpdateSettings,
    ) -> None:
        """Turning updates off while one waits for idle cancels it.

        UX-TRY-12.
        """
        app = make_app(updater=updates.updater)
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        qtbot.waitUntil(lambda: updates.feed.fetches == 1)

        app.controller.apply_settings(replace(Settings(), updates=turned_off))
        editor = app.controller.editors[0]
        editor.session.history.mark_delivered()
        editor.close()
        qtbot.waitUntil(lambda: not app.controller.editors)
        qtbot.wait(50)

        assert updates.quits == []
        assert updates.spawns.calls == []

    @pytest.mark.usefixtures("check_at_once")
    def test_automatic_install_waits_for_a_capture(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A selection in progress holds the update back too.

        UX-TRY-12.
        """
        monkeypatch.setattr(controller_module, "IDLE_RETRY_MS", 10)
        app = make_app(updater=updates.updater)
        app.controller.capture.capture_region()
        assert app.controller.capture.is_selecting

        qtbot.waitUntil(lambda: updates.feed.fetches == 1)
        qtbot.wait(50)
        assert updates.quits == []

        app.controller.capture.cancel()
        qtbot.waitUntil(lambda: updates.quits == [True])

    @pytest.mark.usefixtures("check_often")
    def test_automatic_check_with_install_off_offers_once(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, qtbot: QtBot
    ) -> None:
        """Without auto-install, each release is offered once, not every check.

        UX-TRY-11.
        """
        settings = replace(Settings(), updates=UpdateSettings(install=False))
        make_app(settings, updater=updates.updater)

        qtbot.waitUntil(lambda: updates.feed.fetches >= 3)

        assert balloons.titles() == ["Update available"]
        assert updates.quits == []

    @pytest.mark.usefixtures("check_at_once")
    def test_checking_off_never_checks(
        self, make_app: AppFactory, updates: Updates, qtbot: QtBot
    ) -> None:
        """With checking off, nothing is fetched; turning it on starts checks.

        UX-TRY-11.
        """
        off = replace(Settings(), updates=UpdateSettings(check=False))
        app = make_app(off, updater=updates.updater)
        qtbot.wait(50)
        assert updates.feed.fetches == 0

        app.controller.apply_settings(
            replace(off, updates=UpdateSettings(install=False))
        )

        qtbot.waitUntil(lambda: updates.feed.fetches == 1)
        app.controller.apply_settings(off)
        fetches = updates.feed.fetches
        qtbot.wait(50)
        assert updates.feed.fetches == fetches

    def test_install_that_cannot_run_says_why(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, tmp_path: Path
    ) -> None:
        """An install VerdiClip can't update explains and keeps running.

        UX-TRY-12.
        """
        updater = Updater(
            Installation(InstallKind.UNKNOWN, "0.10.0", Path("python.exe")),
            updates.feed,
            UpdateLauncher(tmp_path / "update", updates.spawns),
        )
        app = make_app(updater=updater)
        app.controller.tray.action("update").trigger()

        app.controller.tray.messageClicked.emit()

        assert balloons.titles() == [
            "Update available",
            "VerdiClip 0.11.0 is available",
        ]
        _title, message, warning = balloons.shown[1]
        assert "update it the way you installed it" in message
        assert warning
        assert updates.spawns.calls == []
        assert updates.quits == []

    @pytest.mark.usefixtures("check_often")
    def test_automatic_refusal_is_reported_once(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, qtbot: QtBot
    ) -> None:
        """An install that can't update itself says why once per release.

        UX-TRY-12.
        """
        updater = Updater(
            Installation(InstallKind.UNKNOWN, "0.10.0", Path("python.exe")),
            updates.feed,
            UpdateLauncher(updates.folder, updates.spawns),
        )
        make_app(updater=updater)

        qtbot.waitUntil(lambda: updates.feed.fetches >= 3)

        assert balloons.titles() == ["VerdiClip 0.11.0 is available"]
        assert updates.quits == []

    def test_manual_install_keeps_unsaved_work(
        self,
        make_app: AppFactory,
        updates: Updates,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An editor that keeps its unsaved work cancels the update.

        UX-TRY-12, UX-G-06.
        """
        app = make_app(updater=updates.updater)
        app.controller.tray.action("update").trigger()
        status = self.status()
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == 1)
        editor = app.controller.editors[0]
        editor.session.history.execute(
            SetCrop(editor.session.document.crop, Rect(0, 0, 10, 10))
        )
        monkeypatch.setattr(ClosePrompt, "ask", lambda *_: CloseChoice.CANCEL)

        app.controller.tray.action("update").trigger()

        assert editor in app.controller.editors
        assert not editor.session.history.is_delivered
        assert status.isVisible(), (
            "Capture/update cancellation must preserve the dialog"
        )
        assert updates.spawns.calls == []
        assert updates.quits == []
        status.close()  # Explicit user dismissal permits passive editor presentation.
        qtbot.waitUntil(editor.isVisible)
        assert not editor.session.history.is_delivered

    def test_helper_that_cannot_start_keeps_running(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """If the helper can't start, VerdiClip says so and doesn't quit.

        UX-TRY-12.
        """
        updates.spawns.error = OSError("blocked")
        app = make_app(updater=updates.updater)
        app.controller.tray.action("update").trigger()

        app.controller.tray.action("update").trigger()

        assert balloons.shown[-1] == (
            "Could not update VerdiClip",
            "Could not start the updater: blocked",
            True,
        )
        assert updates.quits == []

    def test_restart_reports_the_update(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons
    ) -> None:
        """The restarted app says the update worked, once.

        UX-TRY-12.
        """
        write_result(
            updates.folder,
            ok=True,
            previous="0.9.0",
            target="0.10.0",
            installed="0.10.0",
        )

        make_app(updater=updates.updater)
        make_app(updater=updates.updater)

        assert balloons.shown == [
            ("VerdiClip updated", "Updated from 0.9.0 to 0.10.0.", False)
        ]

    @pytest.mark.usefixtures("check_at_once")
    def test_failed_update_is_reported_and_not_retried(
        self, make_app: AppFactory, updates: Updates, balloons: Balloons, qtbot: QtBot
    ) -> None:
        """A failed update says why; the same release is offered, not retried.

        UX-TRY-12.
        """
        write_result(
            updates.folder,
            ok=False,
            previous="0.10.0",
            target="0.11.0",
            message="uv failed with exit code 2",
            log="update.log",
        )

        make_app(updater=updates.updater)
        qtbot.waitUntil(lambda: updates.feed.fetches == 1)

        assert balloons.shown[0] == (
            "VerdiClip update failed",
            "uv failed with exit code 2. VerdiClip is still 0.10.0. "
            "Details: update.log",
            True,
        )
        assert balloons.titles()[1:] == ["Update available"]
        assert updates.quits == []
