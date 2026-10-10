"""The running app: hotkeys and tray → capture → after-capture action."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QImageReader
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from verdiclip import APP_NAME, VERSION
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.models import Capture
from verdiclip.capture.service import CaptureService, WindowSource
from verdiclip.document.document import Document
from verdiclip.editor.icons import IconFactory
from verdiclip.editor.session import EditorSession
from verdiclip.editor.style_memory import StyleMemory
from verdiclip.editor.window import IMAGE_FILTER, EditorOptions, EditorWindow
from verdiclip.exceptions import AppError, HotkeyError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.associations import DEFAULT_APPS_URI, FileAssociations
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.startup import StartupRegistration
from verdiclip.settings import IntegrationSettings, Settings, SettingsStore
from verdiclip.shell.settings_dialog import HOTKEY_FIELDS, SettingsDialog
from verdiclip.shell.theme import ThemeManager
from verdiclip.shell.tray import TrayCommands, TrayIcon

logger = logging.getLogger(__name__)

NOTIFY_MS: Final = 4000


@dataclass(frozen=True, slots=True)
class ShellIntegration:
    """Windows registrations kept in sync with the settings."""

    startup: StartupRegistration
    associations: FileAssociations


class AppController(QObject):
    """Owns the long-lived services and every open editor."""

    def __init__(
        self,
        store: SettingsStore,
        screens: ScreenSource,
        windows: WindowSource,
        hotkeys: HotkeyService,
        integration: ShellIntegration,
    ) -> None:
        super().__init__()
        self._store = store
        self._settings = store.load()
        self._styles = StyleMemory(store.path.with_name("styles.json"))
        self._hotkeys = hotkeys
        self._startup = integration.startup
        self._associations = integration.associations
        self._capture = CaptureService(
            screens, windows, show_magnifier=self._settings.capture.show_magnifier
        )
        self._editors: list[EditorWindow] = []
        self._icon_factory = IconFactory(QApplication.palette().windowText().color())
        self._icon = self._icon_factory.brand()
        self._tray = TrayIcon(self._icon, self._tray_commands())
        self._last_saved_folder: Path | None = None
        self._capture.captured.connect(self._on_captured)
        self._capture.failed.connect(
            lambda message: self._notify("Capture failed", message, warning=True)
        )
        self._hotkeys.triggered.connect(self._on_hotkey)
        self._tray.messageClicked.connect(self._open_last_folder)

    # Accessors

    @property
    def settings(self) -> Settings:
        """Return the active settings."""
        return self._settings

    @property
    def capture(self) -> CaptureService:
        """Return the capture service."""
        return self._capture

    @property
    def tray(self) -> TrayIcon:
        """Return the tray icon."""
        return self._tray

    @property
    def editors(self) -> tuple[EditorWindow, ...]:
        """Return the open editors."""
        return tuple(self._editors)

    # Lifecycle

    def start(self) -> None:
        """Apply the theme, show the tray, and register hotkeys (UX-TRY-01)."""
        ThemeManager.apply(self._settings.appearance.theme)
        self._tray.show()
        self._apply_hotkeys()
        # Re-assert Windows registrations so they survive moved installs and
        # registrations that were lost or written somewhere else
        if self._settings.startup.run_at_login:
            self._apply_startup()
        if self._settings.integration.open_with:
            self._apply_open_with()
        logger.info("%s %s ready", APP_NAME, VERSION)

    def shutdown(self) -> None:
        """Release global resources."""
        self._capture.cancel()
        self._hotkeys.close()
        self._tray.hide()

    def handle_forwarded(self, arguments: list[str]) -> None:
        """Act on arguments sent by a second launch."""
        files = [Path(a) for a in arguments if a and not a.startswith("-")]
        if files:
            for path in files:
                self.open_image(path)
            return
        self._notify(
            APP_NAME, "VerdiClip is already running — click the tray icon to capture."
        )

    # Capture entry points

    def _on_hotkey(self, action: str) -> None:
        """Dispatch a global hotkey."""
        handlers = {
            "region": self._capture.capture_region,
            "window": self._capture.capture_active_window,
            "fullscreen": self._capture.capture_fullscreen,
            "repeat": self._capture.repeat_last,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def _on_captured(self, capture: Capture) -> None:
        """Run every enabled after-capture action (UX-CAP-11..13)."""
        actions = self._settings.capture
        delivery = ImageDelivery(self._settings.output)
        saved: Path | None = None
        done: list[str] = []
        try:
            if actions.save_to_file:
                when = capture.taken_at
                saved = delivery.save(
                    capture.image, delivery.auto_path(title=capture.title, moment=when)
                )
                self._last_saved_folder = saved.parent
                done.append(f"Saved {saved}")
            if actions.copy_to_clipboard:
                delivery.copy(capture.image)
                done.append("Copied to clipboard")
        except AppError as err:
            self._notify("Could not deliver the capture", str(err), warning=True)
        if actions.open_editor or not actions.has_action:
            delivered = saved is not None or actions.copy_to_clipboard
            self.open_editor(
                capture.image, title=capture.title, source=saved, delivered=delivered
            )
        if done:
            hint = "\nClick to open the folder." if saved is not None else ""
            self._notify("Screenshot captured", "\n".join(done) + hint)

    # Editors

    def open_editor(
        self,
        image: QImage,
        *,
        title: str = "",
        source: Path | None = None,
        delivered: bool = False,
    ) -> EditorWindow:
        """Open an editor; ``delivered`` means it's already saved or copied."""
        session = EditorSession(
            Document(image),
            self._settings.editor,
            remembered=self._styles.load(),
            on_style_change=self._styles.remember,
            delivered=delivered,
        )
        options = EditorOptions(
            title=title,
            source_path=source,
            confirm_close=self._settings.editor.confirm_unsaved_close,
        )
        editor = EditorWindow(session, ImageDelivery(self._settings.output), options)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.open_image_requested.connect(self.open_image)
        editor.settings_requested.connect(lambda e=editor: self.show_settings(e))
        editor.destroyed.connect(lambda _=None, e=editor: self._forget(e))
        self._editors.append(editor)
        editor.show()
        editor.raise_()
        editor.activateWindow()
        return editor

    def open_image(self, path: Path) -> EditorWindow | None:
        """Open an image file in a new editor (UX-OUT-06)."""
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self._notify(
                "Could not open image",
                f"{path.name}: {reader.errorString()}",
                warning=True,
            )
            return None
        return self.open_editor(image, title=path.stem, source=path, delivered=True)

    def _forget(self, editor: EditorWindow) -> None:
        """Drop a closed editor."""
        if editor in self._editors:
            self._editors.remove(editor)

    # Tray commands

    def _tray_commands(self) -> TrayCommands:
        """Return the callbacks for the tray menu."""
        return TrayCommands(
            capture_region=self._capture.capture_region,
            capture_window=self._capture.capture_active_window,
            capture_fullscreen=self._capture.capture_fullscreen,
            repeat_last=self._capture.repeat_last,
            open_image=self._ask_open_image,
            settings=self.show_settings,
            about=self._show_about,
            exit=self.request_exit,
        )

    def _ask_open_image(self) -> None:
        """Ask for an image file to open."""
        chosen, _ = QFileDialog.getOpenFileName(
            None, "Open image", str(self._settings.output.directory), IMAGE_FILTER
        )
        if chosen:
            self.open_image(Path(chosen))

    def show_settings(self, parent: QWidget | None = None) -> None:
        """Edit and apply settings, from the tray or an editor (UX-TRY-04)."""
        dialog = SettingsDialog(self._settings, parent)
        dialog.setWindowIcon(self._icon)
        dialog.make_default_requested.connect(self.make_default_image_app)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.apply_settings(dialog.result_settings())

    def make_default_image_app(self) -> None:
        """Register for Open with, then open Windows' default-apps page.

        Windows only lets the user choose the default handler, so the page
        is where they confirm it (UX-TRY-07).
        """
        enabled = IntegrationSettings(open_with=True)
        if self._settings.integration != enabled:
            self.apply_settings(replace(self._settings, integration=enabled))
        else:
            self._apply_open_with()
        QDesktopServices.openUrl(QUrl(DEFAULT_APPS_URI))

    def apply_settings(self, settings: Settings) -> None:
        """Persist ``settings`` and apply them immediately."""
        try:
            self._store.save(settings)
        except AppError as err:
            self._notify("Settings not saved", str(err), warning=True)
        if settings.editor.style_defaults != self._settings.editor.style_defaults:
            # New defaults in Settings replace per-tool choices made in editors
            self._styles.forget()
        previous, self._settings = self._settings, settings
        for editor in self._editors:
            editor.set_confirm_close(confirm=settings.editor.confirm_unsaved_close)
        ThemeManager.apply(settings.appearance.theme)
        self._capture.set_show_magnifier(show=settings.capture.show_magnifier)
        self._apply_startup()
        if settings.integration != previous.integration:
            self._apply_open_with()
        self._apply_hotkeys()

    def _show_about(self) -> None:
        """Show version and attribution."""
        QMessageBox.about(
            None,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} {VERSION}</b>"
            "<p>Fast, faithful screenshots with annotation.</p>"
            "<p>Inspired by <a href='https://getgreenshot.org/'>Greenshot</a>; "
            "an independent, "
            "clean-room implementation. MIT licensed.</p>",
        )

    def request_exit(self) -> None:
        """Close editors (each may ask about unsaved work), then quit."""
        for editor in list(self._editors):
            if not editor.close():
                return
        QApplication.quit()

    # Internals

    def _apply_hotkeys(self) -> None:
        """Register configured hotkeys and report any that are taken (UX-CAP-10)."""
        self._hotkeys.unregister_all()
        labels: dict[str, str] = {}
        problems: list[str] = []
        for name, label in HOTKEY_FIELDS:
            text = getattr(self._settings.hotkeys, name)
            if not text:
                continue
            try:
                hotkey = Hotkey.parse(text)
            except HotkeyError as err:
                problems.append(f"{label}: {err}")
                continue
            if self._hotkeys.register(name, hotkey):
                labels[name] = hotkey.display_text
            else:
                problems.append(
                    f"{label}: {hotkey.display_text} is in use by another program"
                )
        self._tray.rebuild_menu(labels)
        if problems:
            self._notify(
                "Some hotkeys are unavailable", "\n".join(problems), warning=True
            )

    def _apply_startup(self) -> None:
        """Add or remove the run-at-login entry."""
        try:
            if self._settings.startup.run_at_login:
                self._startup.enable(self._launch_command())
            else:
                self._startup.disable()
        except AppError as err:
            self._notify("Could not change startup setting", str(err), warning=True)

    def _apply_open_with(self) -> None:
        """Add or remove VerdiClip from Explorer's Open with menu."""
        try:
            if self._settings.integration.open_with:
                icon = self._store.path.with_name("verdiclip.ico")
                if not self._icon_factory.brand(256).pixmap(256).save(str(icon)):
                    logger.warning("Could not write %s", icon)
                self._associations.register(
                    f'{self._launch_command()} open "%1"', str(icon)
                )
            else:
                self._associations.unregister()
        except AppError as err:
            self._notify("Could not change Open with", str(err), warning=True)

    @staticmethod
    def _launch_command() -> str:
        """Return the command line that starts VerdiClip in the tray."""
        executable = Path(sys.executable)
        gui = executable.with_name("pythonw.exe")
        runner = gui if gui.exists() else executable
        return f'"{runner}" -m verdiclip'

    def _notify(self, title: str, message: str, *, warning: bool = False) -> None:
        """Show a tray balloon."""
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        logger.log(
            logging.WARNING if warning else logging.INFO, "%s: %s", title, message
        )
        if self._tray.isVisible():
            self._tray.showMessage(title, message, icon, NOTIFY_MS)

    def _open_last_folder(self) -> None:
        """Open the folder of the last auto-saved capture."""
        if self._last_saved_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_saved_folder)))
