"""The running app: hotkeys and tray → capture → after-capture action."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QImageReader
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFileDialog,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from verdiclip import APP_NAME, DOCS_URL, VERSION
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.models import Capture
from verdiclip.capture.scroll_selection import ScrollingCapture, ScrollSelection
from verdiclip.capture.service import CaptureService, WindowSource
from verdiclip.document.admission import CaptureProvenance
from verdiclip.document.document import Document
from verdiclip.editor.icons import IconFactory
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.editor.style_memory import StyleMemory
from verdiclip.editor.window import IMAGE_FILTER, EditorOptions, EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.exceptions import AppError, HotkeyError
from verdiclip.geometry import Point
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.associations import (
    DEFAULT_APPS_URI,
    EXTENSIONS,
    FileAssociations,
)
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.keyboard_hook import KeyCapture
from verdiclip.platform.launcher import LaunchCommand, tray_launch
from verdiclip.platform.shortcuts import Shortcut, ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.settings import (
    CaptureDestination,
    Settings,
    SettingsStore,
)
from verdiclip.shell.default_app_dialog import DefaultAppDialog
from verdiclip.shell.settings_dialog import HOTKEY_FIELDS, SettingsDialog
from verdiclip.shell.theme import ThemeManager
from verdiclip.shell.tray import CHECK_UPDATES_LABEL, TrayCommands, TrayIcon
from verdiclip.update.service import Updater

logger = logging.getLogger(__name__)

NOTIFY_MS: Final = 4000
FIRST_CHECK_MS: Final = 60_000  # Let sign-in settle before the first check
CHECK_INTERVAL_MS: Final = 6 * 60 * 60 * 1000
IDLE_RETRY_MS: Final = 5 * 60 * 1000


@dataclass(frozen=True, slots=True)
class ShellIntegration:
    """Windows registrations kept in sync with the settings, and the shell's keys."""

    startup: StartupRegistration
    associations: FileAssociations
    start_menu: ShortcutRegistration
    # Lets Settings record keys Windows would otherwise take (PrtSc)
    key_capture: KeyCapture | None = None
    # Finds and installs newer releases; None leaves updates out of the tray
    updater: Updater | None = None


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
        self._key_capture = integration.key_capture
        self._updater = updater = integration.updater
        self._store = store
        self._settings = store.load()
        self._styles = StyleMemory(store.path.with_name("styles.json"))
        self._hotkeys = hotkeys
        self._startup = integration.startup
        self._associations = integration.associations
        self._start_menu = integration.start_menu
        self._capture = CaptureService(
            screens, windows, show_magnifier=self._settings.capture.show_magnifier
        )
        self._scrolling = ScrollSelection(screens, windows, hotkeys)
        self._pending_capture: tuple[str, bool] | None = None
        self._capture_handoff = QTimer(self)
        self._capture_handoff.setSingleShot(True)
        self._capture_handoff.timeout.connect(self._resume_capture)
        self._closing = self._hotkeys_paused = self._hotkey_refresh_pending = False
        self._editors: list[EditorWindow] = []
        self._workspace = Workspace()
        self._workspace.window_added.connect(self._wire_editor)
        self._workspace.window_removed.connect(self._forget)
        self._workspace.presentation_failed.connect(
            lambda message: self._notify(
                "Editor work retained",
                message,
                warning=True,
                on_click=self._recover_editor_work,
            )
        )
        self._icon_factory = IconFactory(QApplication.palette().windowText().color())
        self._icon = self._icon_factory.brand()
        self._tray = TrayIcon(self._icon, self._tray_commands())
        self._wire_scrolling()
        self._last_saved_folder: Path | None = None
        self._message_action: Callable[[], None] = self._open_last_folder
        self._default_app_dialog: DefaultAppDialog | None = None
        self._manual_check = False
        self._install_button: QAbstractButton | None = None
        self._update_status: QMessageBox | None = None
        self._scroll_status: QMessageBox | None = None
        self._last_scroll_error = ""
        self._announced: str | None = None
        self._check_timer = QTimer(self)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._install_when_idle)
        self._check_timer.timeout.connect(self._scheduled_check)
        if updater is not None:
            updater.checked.connect(self._on_update_checked)
            updater.check_failed.connect(self._on_update_check_failed)
        self._capture.captured.connect(self._on_captured)
        self._capture.failed.connect(
            lambda message: self._notify("Capture failed", message, warning=True)
        )
        self._hotkeys.triggered.connect(self._on_hotkey)
        self._tray.messageClicked.connect(self._on_message_clicked)

    def _wire_scrolling(self) -> None:
        scrolling = self._scrolling
        scrolling.captured.connect(self._on_scrolled)
        scrolling.failed.connect(self._on_scroll_failed)
        scrolling.finished.connect(self._after_scrolling)
        scrolling.state_changed.connect(self._tray.set_scrolling_state)
        self._capture.scroll_requested.connect(self._scroll_at)

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
    def automation_enabled(self) -> bool:
        """Return whether local tools may query open documents."""
        return self._settings.integration.automation

    @property
    def tray(self) -> TrayIcon:
        """Return the tray icon."""
        return self._tray

    @property
    def default_app_dialog(self) -> DefaultAppDialog | None:
        """Return the open default-app guide, if any (for tests)."""
        return self._default_app_dialog

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
        # registrations that were lost; entries that start another existing
        # install (the user's main checkout, seen from a dev copy) are kept
        if self._settings.startup.run_at_login:
            self._apply_startup(keep_others=True)
        if self._settings.integration.open_with:
            self._apply_open_with(keep_others=True)
        if self._settings.integration.start_menu:
            self._apply_start_menu(keep_others=True)
        self._report_update()
        self._schedule_update_checks()
        logger.info("%s %s ready", APP_NAME, VERSION)

    def shutdown(self) -> None:
        """Release global resources."""
        self._check_timer.stop()
        self._idle_timer.stop()
        if self._update_status is not None:
            self._update_status.hide()
            self._update_status.deleteLater()
            self._update_status = None
        if self._scroll_status is not None:
            self._scroll_status.hide()
            self._scroll_status.deleteLater()
            self._scroll_status = None
        self._closing = True
        self._pending_capture = None
        self._capture_handoff.stop()
        self._scrolling.shutdown()
        self._capture.cancel()
        self._workspace.cancel_drag()
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
        """Dispatch configured shortcuts through one capture owner."""
        if action == "scrolling":
            self.capture_scrolling()
        else:
            self._request_capture(action, freeze_first=True)

    def _request_capture(self, action: str, *, freeze_first: bool = False) -> None:
        if self._closing:
            return
        handlers = {
            "region": self._capture.capture_region,
            "window": self._capture.capture_active_window,
            "fullscreen": self._capture.capture_fullscreen,
            "repeat": self._capture.repeat_last,
        }
        handler = handlers.get(action)
        if handler is None:
            return
        self._dismiss_scroll_status()
        self._capture_handoff.stop()
        if self._scrolling.busy:
            self._pending_capture = action, freeze_first
            self._scrolling.cancel()
            return
        self._pending_capture = None
        self._workspace.cancel_drag()
        handler(freeze_first=freeze_first)

    def capture_scrolling(self) -> None:
        """Choose one scrolling area; a second request cancels the active one."""
        if self._closing:
            return
        self._dismiss_scroll_status()
        if self._scrolling.busy:
            self._scrolling.cancel()
            return
        self._pending_capture = None
        self._capture_handoff.stop()
        self._capture.cancel()
        self._workspace.cancel_drag()
        self._scrolling.capture()

    def _scroll_at(self, point: Point, title: str) -> None:
        """Start a scrolling capture of the panel Ctrl+clicked in region selection."""
        if self._closing or self._scrolling.busy:
            return
        self._dismiss_scroll_status()
        self._workspace.cancel_drag()
        self._scrolling.start_at(point, title)

    def _after_scrolling(self) -> None:
        if self._closing:
            return
        if self._hotkeys_paused:
            self._hotkeys.unregister_all()
        elif self._hotkey_refresh_pending:
            self._hotkey_refresh_pending = False
            self._apply_hotkeys(report=False)
        if self._pending_capture is not None:
            self._capture_handoff.start(0)

    def _resume_capture(self) -> None:
        pending, self._pending_capture = self._pending_capture, None
        if pending is None or self._closing:
            return
        action, freeze_first = pending
        if action == "settings":
            self.show_settings()
        elif action == "update":
            self.install_update(manual=True)
        else:
            self._request_capture(action, freeze_first=freeze_first)

    def _on_scroll_failed(self, message: str) -> None:
        if not self._closing and self._pending_capture is None:
            self._last_scroll_error = f"No screenshot was captured.\n\n{message}"
            self._tray.set_scroll_error_available()
            self._notify("Scrolling capture stopped", message, warning=True)
            if self._scroll_status is None:
                self._scroll_status = QMessageBox()
                self._scroll_status.setObjectName("scrolling-capture-status")
                self._scroll_status.setWindowIcon(self._icon)
                self._scroll_status.setTextFormat(Qt.TextFormat.PlainText)
                self._scroll_status.setWindowModality(Qt.WindowModality.NonModal)
                # Input/focus changes may be why capture stopped. Report the
                # failure without taking focus back from the user's new work.
                self._scroll_status.setAttribute(
                    Qt.WidgetAttribute.WA_ShowWithoutActivating
                )
                self._scroll_status.setIcon(QMessageBox.Icon.Warning)
                self._scroll_status.setStandardButtons(QMessageBox.StandardButton.Ok)
                self._scroll_status.setWindowTitle("Scrolling capture stopped")
                copy = self._scroll_status.addButton(
                    "&Copy error", QMessageBox.ButtonRole.ActionRole
                )
                copy.clicked.connect(self._copy_scroll_error)
                self._scroll_status.setEscapeButton(QMessageBox.StandardButton.Ok)
                self._scroll_status.setDefaultButton(QMessageBox.StandardButton.Ok)
                self._scroll_status.setInformativeText(
                    "You can also copy this error later from the tray menu."
                )
            self._scroll_status.setText(self._last_scroll_error)
            self._scroll_status.show()

    def _copy_scroll_error(self) -> None:
        """Copy only on request, never overwrite a screenshot on failure."""
        if self._last_scroll_error:
            QApplication.clipboard().setText(self._last_scroll_error)

    def _dismiss_scroll_status(self) -> None:
        """Keep an old failure out of the pixels and input path of a new capture."""
        if self._scroll_status is not None:
            self._scroll_status.hide()

    def _on_scrolled(self, capture: ScrollingCapture) -> None:
        delivered = self._deliver_capture(capture)
        if delivered and capture.warning:
            self._notify("Scrolling capture finished", capture.warning, warning=True)

    def _on_captured(self, capture: Capture) -> None:
        """Deliver an ordinary capture without changing its Repeat semantics."""
        self._deliver_capture(capture)

    def _deliver_capture(self, capture: Capture | ScrollingCapture) -> bool:
        """Deliver once; retain error/recovery precedence over cleanup warnings."""
        warning = (
            f"\n{capture.warning}"
            if isinstance(capture, ScrollingCapture) and capture.warning
            else ""
        )
        delivery_ok = True
        actions = self._settings.capture
        delivery = ImageDelivery(self._settings.output)
        saved: Path | None = None
        copied = False
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
                copied = True
                done.append("Copied to clipboard")
        except AppError as err:
            delivery_ok = False
            self._notify(
                "Could not deliver the capture", str(err) + warning, warning=True
            )
        if actions.open_editor or not actions.has_action:
            options = EditorOptions(
                title=capture.title,
                source_path=saved,
                confirm_close=self._settings.editor.confirm_unsaved_close,
                provenance=CaptureProvenance.create(capture.title, capture.taken_at),
            )
            try:
                self._open_document(
                    capture.image,
                    options,
                    delivered=saved is not None or copied,
                    new_window=actions.destination is CaptureDestination.WINDOWS,
                )
            except (RuntimeError, ValueError, MemoryError):
                retained = any(
                    record.provenance == options.provenance
                    for record in self._workspace.documents
                )
                if retained:
                    self._notify(
                        "Editor work retained",
                        "The screenshot could not be shown. "
                        "Click to recover retained tabs." + warning,
                        warning=True,
                        on_click=self._recover_editor_work,
                    )
                else:
                    self._notify(
                        "Could not open the capture",
                        "The editor could not accept the screenshot." + warning,
                        warning=True,
                    )
                return False  # Keep recovery; never repeat save/copy delivery.
        if done and delivery_ok:
            hint = "\nClick to open the folder." if saved is not None else ""
            self._notify("Screenshot captured", "\n".join(done) + hint)
        return delivery_ok

    def _recover_editor_work(self) -> None:
        """Retry retained views only, never repeat capture or output delivery."""
        self._workspace.recover_retained(activate=True)

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
        options = EditorOptions(
            title=title,
            source_path=source,
            confirm_close=self._settings.editor.confirm_unsaved_close,
        )
        return self._open_document(image, options, delivered=delivered)

    def _open_document(
        self,
        image: QImage,
        options: EditorOptions,
        *,
        delivered: bool,
        target: EditorWindow | None = None,
        new_window: bool = False,
    ) -> EditorWindow:
        session = EditorSession(
            Document(image),
            self._settings.editor,
            remembered=self._styles.load(),
            on_style_change=self._styles.remember,
            delivered=delivered,
        )
        return self._workspace.open_document(
            session,
            ImageDelivery(self._settings.output),
            options,
            target=target,
            activate=True,
            new_window=new_window,
            select_new=options.provenance is not None,
        )

    @property
    def workspace(self) -> Workspace:
        """Return document ownership across all tab hosts."""
        return self._workspace

    def _wire_editor(self, editor: EditorWindow) -> None:
        """Wire new and detached tab hosts once."""
        editor.open_image_requested.connect(
            lambda path, host=editor: self.open_image(path, target=host)
        )
        editor.settings_requested.connect(lambda e=editor: self.show_settings(e))
        editor.about_requested.connect(lambda e=editor: self._show_about(e))
        self._editors.append(editor)

    def open_image(
        self, path: Path, *, target: EditorWindow | None = None
    ) -> EditorWindow | None:
        """Open an image file in a new editor (UX-OUT-06)."""
        try:
            return self._open_file(path, target)
        except ValueError as err:
            self._notify("Could not open image", str(err), warning=True)
            return None

    def open_file(self, path: Path) -> DocumentRecord:
        """Open an image file in a tab and return its document (``ctl open``).

        Raises:
            ValueError: If the file can't be read as an image.
        """
        before = {record.identifier for record in self._workspace.documents}
        self._open_file(path, None)
        # The new tab isn't selected while a menu or dialog is open, so find
        # the admitted document rather than reading the current tab.
        added = [
            record
            for record in self._workspace.documents
            if record.identifier not in before
        ]
        if len(added) != 1:
            msg = f"{path.name} did not open in a tab"
            raise ValueError(msg)
        return added[0]

    def _open_file(self, path: Path, target: EditorWindow | None) -> EditorWindow:
        """Read ``path`` and open it; raise ValueError if it isn't an image."""
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            msg = f"{path.name}: {reader.errorString()}"
            raise ValueError(msg)
        options = EditorOptions(
            title=path.stem,
            source_path=path,
            confirm_close=self._settings.editor.confirm_unsaved_close,
        )
        return self._open_document(image, options, delivered=True, target=target)

    def _forget(self, editor: EditorWindow) -> None:
        """Drop a closed editor."""
        if editor in self._editors:
            self._editors.remove(editor)
        if not self._workspace.has_work and self._idle_timer.isActive():
            self._idle_timer.start(0)  # A deferred update can go ahead now

    # Tray commands

    def _tray_commands(self) -> TrayCommands:
        """Return the callbacks for the tray menu."""
        return TrayCommands(
            capture_region=lambda: self._request_capture("region"),
            capture_window=lambda: self._request_capture("window"),
            capture_fullscreen=lambda: self._request_capture("fullscreen"),
            repeat_last=lambda: self._request_capture("repeat"),
            open_image=self._ask_open_image,
            open_folder=self.open_screenshots_folder,
            settings=self.show_settings,
            about=self._show_about,
            exit=self.request_exit,
            update=self.check_for_updates if self._updater is not None else None,
            capture_scrolling=self.capture_scrolling,
            copy_scroll_error=self._copy_scroll_error,
        )

    def _ask_open_image(self) -> None:
        """Ask for an image file to open."""
        chosen, _ = QFileDialog.getOpenFileName(
            None, "Open image", str(self._settings.output.directory), IMAGE_FILTER
        )
        if chosen:
            self.open_image(Path(chosen))

    def open_screenshots_folder(self) -> None:
        """Open the save folder in Explorer, creating it if needed."""
        folder = self._settings.output.directory
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._notify(
                "Can't open screenshots folder", f"{folder}: {error}", warning=True
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._notify("Can't open screenshots folder", str(folder), warning=True)

    def show_settings(self, parent: QWidget | None = None) -> None:
        """Edit and apply settings, from the tray or an editor (UX-TRY-04)."""
        if self._scrolling.busy:
            self._pending_capture = "settings", False
            self._scrolling.cancel()
            return
        dialog = SettingsDialog(self._settings, parent, key_capture=self._key_capture)
        dialog.setWindowIcon(self._icon)
        dialog.make_default_requested.connect(
            lambda: self.make_default_image_app(dialog)
        )
        dialog.hotkey_recording_changed.connect(self._pause_hotkeys)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.apply_settings(dialog.result_settings())

    def make_default_image_app(self, parent: QWidget | None = None) -> None:
        """Register for Open with, open Settings on VerdiClip, and guide the user.

        Windows only lets the user choose the default handler, so Settings
        is where they pick it, one file type at a time; the guide says how
        and ticks off each type as they go (UX-TRY-07).
        """
        integration = replace(self._settings.integration, open_with=True)
        if self._settings.integration != integration:
            self.apply_settings(replace(self._settings, integration=integration))
        else:
            self._apply_open_with()
        self._open_default_apps()
        if self._default_app_dialog is not None:
            self._default_app_dialog.raise_()
            self._default_app_dialog.activateWindow()
            return
        guide = DefaultAppDialog(EXTENSIONS, self._associations.defaults, parent)
        guide.setWindowIcon(self._icon)
        guide.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        guide.open_settings_requested.connect(self._open_default_apps)
        guide.destroyed.connect(self._forget_default_app_dialog)
        self._default_app_dialog = guide
        guide.open()

    def _open_default_apps(self) -> None:
        """Open VerdiClip's page under Settings > Default apps."""
        QDesktopServices.openUrl(QUrl(DEFAULT_APPS_URI))

    def _forget_default_app_dialog(self) -> None:
        """Drop the guide once it is closed and deleted."""
        self._default_app_dialog = None

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
        # Only turning it on (or off) moves it; saving other settings keeps it
        self._apply_startup(keep_others=settings.startup == previous.startup)
        if settings.integration.open_with != previous.integration.open_with:
            self._apply_open_with()
        if settings.integration.start_menu != previous.integration.start_menu:
            self._apply_start_menu()
        if settings.updates.check != previous.updates.check:
            self._schedule_update_checks()
        if not (settings.updates.check and settings.updates.install):
            self._idle_timer.stop()  # Drop an automatic install waiting for idle
        self._apply_hotkeys()

    def _show_about(self, parent: QWidget | None = None) -> None:
        """Show version, license, and where the documentation lives."""
        QMessageBox.about(
            parent,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} {VERSION}</b>"
            "<p>Fast, faithful screenshots with annotation.</p>"
            "<p>MIT licensed.</p>"
            f'<p><a href="{DOCS_URL}">Documentation</a></p>',
        )

    def request_exit(self) -> None:
        """Close editors (each may ask about unsaved work), then quit."""
        if self._close_editors():
            try:
                self._scrolling.shutdown()
            except AppError as error:
                self._notify(
                    "Could not stop scrolling capture", str(error), warning=True
                )
                return
            QApplication.quit()

    # Updates

    def check_for_updates(self) -> None:
        """Check now and say what was found, or install a release already found.

        UX-TRY-11.
        """
        if self._updater is None:
            return
        if self._manual_check:
            if self._update_status is not None:
                self._update_status.show()
                self._update_status.raise_()
            return
        if reason := self._updater.unsupported_reason:
            self._show_update_status("Update check unavailable", reason)
            return
        if self._updater.available is not None:
            self.install_update(manual=True)
            return
        self._manual_check = True
        self._tray.set_update_checking(checking=True)
        self._show_update_status(
            "Checking for updates",
            "Checking PyPI for the latest stable release...",
            checking=True,
        )
        try:
            self._updater.check()
        except AppError as err:
            self._on_update_check_failed(str(err))

    def _show_update_status(
        self,
        title: str,
        message: str,
        *,
        checking: bool = False,
        warning: bool = False,
        offer_install: bool = False,
    ) -> None:
        """Keep manual-check progress and its result visible until dismissed.

        A found release is offered right here: Install now or Later (#124).
        """
        if self._update_status is None:
            self._update_status = QMessageBox()
            self._update_status.setObjectName("manual-update-status")
            self._update_status.setWindowIcon(self._icon)
            self._update_status.setTextFormat(Qt.TextFormat.PlainText)
            self._update_status.buttonClicked.connect(self._on_update_status_button)
        box = self._update_status
        for button in box.buttons():
            # removeButton() unparents it: hide first or it shows as a window.
            button.hide()
            box.removeButton(button)
            button.deleteLater()
        self._install_button = None
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(
            QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information
        )
        if offer_install:
            self._install_button = box.addButton(
                "Install now", QMessageBox.ButtonRole.AcceptRole
            )
            box.setDefaultButton(self._install_button)
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        else:
            box.setStandardButtons(
                QMessageBox.StandardButton.Close
                if checking
                else QMessageBox.StandardButton.Ok
            )
        box.show()
        box.raise_()
        box.activateWindow()

    def _on_update_status_button(self, button: QAbstractButton) -> None:
        """Install from the result window; Later only dismisses it."""
        if button is self._install_button:
            self._install_button = None
            self.install_update(manual=True)

    def install_update(self, *, manual: bool) -> None:
        """Hand the available release to the updater and quit (UX-TRY-12).

        The updater installs it once VerdiClip has exited, then starts it
        again. A manual install first closes editors, each of which may ask
        about unsaved work; an automatic one only runs with none open.
        """
        updater = self._updater
        if updater is None or self._defer_scroll_update(manual=manual):
            return
        if not manual and self._defer_automatic_install():
            return
        try:
            plan = updater.prepare(self._launch().command_line)
        except AppError as err:
            version = updater.available
            if manual or version != self._announced:
                self._announced = version
                self._notify(
                    f"{APP_NAME} {version} is available",
                    f"It can't install itself: {err}.",
                    warning=True,
                )
            return
        if manual:
            # Preparation or editor-close prompts can admit a new capture.
            if not self._close_editors() or self._defer_scroll_update(manual=True):
                return
        elif self._defer_automatic_install():
            # Preparation can synchronously admit new work or show application UI.
            # Automatic installation never asks to close newly arrived content.
            return
        try:
            updater.start(plan)
        except AppError as err:
            self._notify("Could not update VerdiClip", str(err), warning=True)
            return
        QApplication.quit()

    def _schedule_update_checks(self) -> None:
        """Check soon after starting, then periodically, while the setting is on."""
        if self._updater is None or not self._settings.updates.check:
            self._check_timer.stop()
            return
        self._check_timer.start(FIRST_CHECK_MS)

    def _scheduled_check(self) -> None:
        """Run a periodic check, unannounced unless it finds something."""
        self._check_timer.setInterval(CHECK_INTERVAL_MS)
        if (
            self._updater is not None
            and not self._manual_check
            and not self._updater.unsupported_reason
        ):
            self._updater.check()

    def _on_update_checked(self, latest: str) -> None:
        """Offer, or install, a newer release; a manual check reports either way."""
        manual, self._manual_check = self._manual_check, False
        self._tray.set_update_checking(checking=False)
        updater = self._updater
        if updater is None:  # pragma: no cover - only an updater emits this
            return
        available = updater.available
        self._tray.set_update_label(
            f"Install {APP_NAME} {available}…"
            if available is not None
            else CHECK_UPDATES_LABEL
        )
        if available is None:
            if manual:
                self._show_update_status(
                    f"{APP_NAME} is up to date",
                    f"You have {updater.installation.version}; "
                    f"the latest stable release is {latest}.",
                )
                self._notify(
                    f"{APP_NAME} is up to date",
                    f"You have {updater.installation.version}; "
                    f"the latest release is {latest}.",
                )
            return
        if manual:
            self._show_update_status(
                "Update available",
                f"{APP_NAME} {available} is available. "
                f"Install it now? {APP_NAME} restarts when it is done.",
                offer_install=True,
            )
        automatic = self._settings.updates.install and not updater.failed_before(
            available
        )
        if not manual and automatic:
            self._install_when_idle()
            return
        if manual or available != self._announced:
            self._announced = available
            self._notify(
                "Update available",
                f"{APP_NAME} {available} is available. Click to install it.",
                on_click=lambda: self.install_update(manual=True),
            )

    def _on_update_check_failed(self, reason: str) -> None:
        """Report a failed check the user asked for; periodic ones only log."""
        manual, self._manual_check = self._manual_check, False
        self._tray.set_update_checking(checking=False)
        if manual:
            self._show_update_status(
                "Could not check for updates",
                f"{reason}\nPlease try again.",
                warning=True,
            )
            self._notify("Could not check for updates", reason, warning=True)

    def _defer_scroll_update(self, *, manual: bool) -> bool:
        if not manual or not self._scrolling.busy:
            return False
        self._pending_capture = "update", False
        self._scrolling.cancel()
        return True

    def _defer_automatic_install(self) -> bool:
        """Keep background updates away from retained work and visible UI."""
        busy = (
            self._workspace.has_work
            or self._capture.is_selecting
            or self._scrolling.busy
            or any(widget.isVisible() for widget in QApplication.topLevelWidgets())
        )
        if busy:
            self._idle_timer.start(IDLE_RETRY_MS)
        return busy

    def _install_when_idle(self) -> None:
        """Install only after work and existing application UI have settled."""
        self.install_update(manual=False)

    def _report_update(self) -> None:
        """Say how the update that restarted VerdiClip went."""
        report = self._updater.take_report() if self._updater is not None else None
        if report is None:
            return
        title, message = report.describe()
        self._notify(title, message, warning=not report.reached_target)

    # Internals

    def _close_editors(self) -> bool:
        """Close every editor; False if one stayed open to keep unsaved work."""
        return self._workspace.close_all()

    def _pause_hotkeys(self, paused: bool) -> None:  # noqa: FBT001 - Qt signal
        """Release hotkeys while Settings records one, so the keys reach it."""
        self._hotkeys_paused = paused
        if self._scrolling.busy:
            self._hotkey_refresh_pending = True
            self._scrolling.cancel()
        elif paused:
            self._hotkeys.unregister_all()
        else:
            self._apply_hotkeys(report=False)

    def _apply_hotkeys(self, *, report: bool = True) -> None:
        """Register configured hotkeys and report any that are taken (UX-CAP-10)."""
        if self._scrolling.busy:
            self._hotkey_refresh_pending = True
            self._scrolling.cancel()
            return
        self._hotkey_refresh_pending = False
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
        if problems and report:
            self._notify(
                "Some hotkeys are unavailable", "\n".join(problems), warning=True
            )

    def _apply_startup(self, *, keep_others: bool = False) -> None:
        """Add or remove the run-at-login shortcut.

        ``keep_others`` leaves a shortcut that starts another existing install.
        """
        try:
            if self._settings.startup.run_at_login and keep_others:
                self._startup.refresh(self._shortcut())
            elif self._settings.startup.run_at_login:
                self._startup.enable(self._shortcut())
            else:
                self._startup.disable()
        except AppError as err:
            self._notify("Could not change startup setting", str(err), warning=True)

    def _apply_open_with(self, *, keep_others: bool = False) -> None:
        """Add or remove VerdiClip from Explorer's Open with menu.

        ``keep_others`` leaves a registration that runs another existing install.
        """
        try:
            if self._settings.integration.open_with:
                command = f'{self._launch().command_line} open "%1"'
                icon = str(self._brand_icon())
                if keep_others:
                    self._associations.refresh(command, icon)
                else:
                    self._associations.register(command, icon)
            else:
                self._associations.unregister()
        except AppError as err:
            self._notify("Could not change Open with", str(err), warning=True)

    def _apply_start_menu(self, *, keep_others: bool = False) -> None:
        """Add or remove VerdiClip's Start menu shortcut.

        ``keep_others`` leaves a shortcut that starts another existing install.
        """
        try:
            if self._settings.integration.start_menu and keep_others:
                self._start_menu.refresh(self._shortcut())
            elif self._settings.integration.start_menu:
                self._start_menu.enable(self._shortcut())
            else:
                self._start_menu.disable()
        except AppError as err:
            self._notify("Could not change Start menu entry", str(err), warning=True)

    def _brand_icon(self) -> Path:
        """Write the app icon next to the settings file and return its path."""
        icon = self._store.path.with_name("verdiclip.ico")
        if not self._icon_factory.brand(256).pixmap(256).save(str(icon)):
            logger.warning("Could not write %s", icon)
        return icon

    def _shortcut(self) -> Shortcut:
        """Return a shortcut that starts VerdiClip in the tray."""
        launch = self._launch()
        return Shortcut(
            target=launch.program,
            arguments=launch.arguments,
            working_directory=Path.home(),
            icon=self._brand_icon(),
            description="Fast, faithful screenshot capture and annotation",
        )

    @staticmethod
    def _launch() -> LaunchCommand:
        """Return the console-less command that starts VerdiClip in the tray."""
        return tray_launch(Path(sys.executable), Path(sys.base_prefix))

    def _notify(
        self,
        title: str,
        message: str,
        *,
        warning: bool = False,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        """Show a tray balloon; clicking it runs ``on_click``.

        Without ``on_click``, a click opens the last auto-save folder.
        """
        self._message_action = on_click or self._open_last_folder
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

    def _on_message_clicked(self) -> None:
        """Run the action of the balloon that was clicked."""
        self._message_action()

    def _open_last_folder(self) -> None:
        """Open the folder of the last auto-saved capture."""
        if self._last_saved_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_saved_folder)))
