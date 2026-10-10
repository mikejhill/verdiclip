"""App journeys: hotkeys and tray through capture to the after-capture action."""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QWidget
from pytestqt.qtbot import QtBot
from tests.platform.test_associations import FakeAssociationBackend
from tests.ux.conftest import Stopwatch
from tests.ux.test_capture_journeys import (
    CaptureOf,
    FakeDesktop,
    FakeWindow,
    FakeWindows,
)

from verdiclip.document.commands import SetCrop
from verdiclip.document.style import Color
from verdiclip.editor.session import ToolId
from verdiclip.editor.style_bar import ColorButton
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Rect
from verdiclip.platform.associations import DEFAULT_APPS_URI, FileAssociations
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.startup import StartupRegistration
from verdiclip.settings import (
    IntegrationSettings,
    Settings,
    SettingsStore,
    StartupSettings,
    Theme,
)
from verdiclip.shell.controller import AppController, ShellIntegration
from verdiclip.shell.instance import SingleInstance
from verdiclip.shell.settings_dialog import SettingsDialog

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
    """The Run key."""

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


@dataclass(slots=True)
class App:
    """A controller over fakes, plus handles to the fakes."""

    controller: AppController
    hotkeys: HotkeyService
    os_hotkeys: FakeHotkeys
    registry: FakeRegistry
    store: SettingsStore
    associations: FakeAssociationBackend

    def press_hotkey(self, action: str) -> None:
        """Simulate Windows delivering WM_HOTKEY for ``action``."""
        hotkey = self.hotkeys.registered[action]
        for hotkey_id, combo in self.os_hotkeys.active.items():
            if combo == (hotkey.win32_modifiers, hotkey.virtual_key):
                self.hotkeys.handle_hotkey_message(hotkey_id)
                return
        msg = f"{action} is not registered"
        raise AssertionError(msg)


type AppFactory = Callable[..., App]


@pytest.fixture
def make_app(qtbot: QtBot, tmp_path: Path) -> Iterator[AppFactory]:
    """Return a factory for a started app with the given settings."""
    created: list[App] = []

    def factory(settings: Settings | None = None, taken: set[str] | None = None) -> App:
        store = SettingsStore(tmp_path / "settings.json")
        base = settings or Settings()
        store.save(
            replace(base, output=replace(base.output, directory=tmp_path / "out"))
        )
        os_hotkeys = FakeHotkeys(taken=taken or set())
        hotkeys = HotkeyService(os_hotkeys)
        registry = FakeRegistry()
        windows = FakeWindows([FakeWindow("Notepad", Rect(10, 10, 300, 200), 1)])
        windows.active = windows.windows[0]
        associations = FakeAssociationBackend()
        controller = AppController(
            store,
            FakeDesktop(),
            windows,
            hotkeys,
            ShellIntegration(
                StartupRegistration(registry), FileAssociations(associations)
            ),
        )
        controller.start()
        app = App(controller, hotkeys, os_hotkeys, registry, store, associations)
        created.append(app)
        return app

    yield factory
    for app in created:
        for editor in app.controller.editors:
            editor.session.history.mark_delivered()
            editor.close()
        app.controller.shutdown()
    del qtbot


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
        assert str(saved[0]) in caplog.text
        assert app.controller.editors == ()

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

    def test_tray_left_click_starts_region_capture(self, make_app: AppFactory) -> None:
        """UX-TRY-02: clicking the tray icon starts a region capture."""
        app = make_app()

        app.controller.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)

        assert app.controller.capture.is_selecting
        app.controller.capture.cancel()


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

    def test_run_at_login_writes_and_removes_startup_entry(
        self, make_app: AppFactory
    ) -> None:
        """The run-at-login checkbox really changes the Windows startup entry.

        UX-TRY-04.
        """
        app = make_app()
        current = app.controller.settings

        app.controller.apply_settings(
            replace(current, startup=replace(current.startup, run_at_login=True))
        )
        enabled = "VerdiClip" in app.registry.values
        app.controller.apply_settings(
            replace(current, startup=replace(current.startup, run_at_login=False))
        )

        assert enabled
        assert "VerdiClip" not in app.registry.values

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

        assert command.endswith('-m verdiclip open "%1"')
        assert icon == tmp_path / "verdiclip.ico"
        assert not QImage(str(icon)).isNull()
        assert command_key not in app.associations.keys
        assert app.store.load().integration.open_with is False

    def test_run_at_login_is_refreshed_at_start(self, make_app: AppFactory) -> None:
        """UX-TRY-04: a lost or outdated startup entry is rewritten on launch."""
        settings = Settings(startup=StartupSettings(run_at_login=True))

        app = make_app(settings)

        assert app.registry.values["VerdiClip"].endswith("-m verdiclip")

    def test_registrations_are_left_alone_when_off(self, make_app: AppFactory) -> None:
        """UX-TRY-04, UX-TRY-07: launching never adds entries the user turned off."""
        app = make_app()

        assert app.registry.values == {}
        assert app.associations.keys == {}

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


class TestClosePrompt:
    """Closing never silently loses a screenshot that wasn't saved or copied."""

    @pytest.fixture
    def asked(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Record every close prompt and answer Cancel."""
        prompts: list[str] = []

        def cancel(*args: object) -> QMessageBox.StandardButton:
            prompts.append(str(args[2]))
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "question", cancel)
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
        """Capture and return the new editor."""
        before = len(app.controller.editors)
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(lambda: len(app.controller.editors) == before + 1)
        return app.controller.editors[-1]

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
        monkeypatch.setattr(
            QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel
        )
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
