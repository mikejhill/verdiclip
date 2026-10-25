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
    QApplication,
    QFileDialog,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from verdiclip import APP_NAME, DOCS_URL, VERSION
from verdiclip.capture.grabber import ScreenSource
from verdiclip.capture.models import Capture
from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture
from verdiclip.capture.scroll.panels import ScrollReviewPanel
from verdiclip.capture.scroll.session import ScrollSession
from verdiclip.capture.service import CaptureService, WindowSource
from verdiclip.document.admission import CaptureProvenance
from verdiclip.document.document import Document
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.icons import IconFactory
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.editor.style_memory import StyleMemory
from verdiclip.editor.window import IMAGE_FILTER, EditorOptions, EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.exceptions import AppError, HotkeyError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.associations import DEFAULT_APPS_URI, FileAssociations
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.keyboard_hook import KeyCapture
from verdiclip.platform.launcher import LaunchCommand, tray_launch
from verdiclip.platform.shortcuts import Shortcut, ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.settings import (
    CaptureSettings,
    IntegrationSettings,
    OutputSettings,
    Settings,
    SettingsStore,
)
from verdiclip.shell.scroll_review import ScrollReviews
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


@dataclass(slots=True)
class CaptureDelivery:
    """Retain original pixels and successful receipts until delivery completes."""

    capture: ReviewedCapture
    actions: CaptureSettings
    output: OutputSettings
    target: EditorWindow | None
    activate: bool
    saved: Path | None = None
    copied: bool = False
    admitted: bool = False
    in_flight: bool = False


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
        self._editors: list[EditorWindow] = []
        self._workspace = Workspace()
        self._workspace.window_added.connect(self._wire_editor)
        self._workspace.window_removed.connect(self._forget)
        self._pending_deliveries: dict[str, CaptureDelivery] = {}
        self._completed_deliveries: set[str] = set()
        self._scroll_reviews = ScrollReviews(self)
        self._scroll_reviews.reviewed_capture.connect(self._deliver_scroll_review)
        self._scroll_reviews.cleanup_failed.connect(
            lambda message: self._notify(
                "Capture cleanup needs retry", message, warning=True
            )
        )
        self._scroll_reviews.work_changed.connect(self._resume_idle_if_ready)
        self._closing_editors = False
        self._icon_factory = IconFactory(QApplication.palette().windowText().color())
        self._icon = self._icon_factory.brand()
        self._tray = TrayIcon(self._icon, self._tray_commands())
        self._last_saved_folder: Path | None = None
        self._message_action: Callable[[], None] = self._open_last_folder
        self._manual_check = False
        self._update_status: QMessageBox | None = None
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

    @property
    def workspace(self) -> Workspace:
        """Return the shared owner for all editor documents and hosts."""
        return self._workspace

    @property
    def pending_deliveries(self) -> tuple[ReviewedCapture, ...]:
        """Retain failed capture delivery without forcing an editor to open."""
        return tuple(attempt.capture for attempt in self._pending_deliveries.values())

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
        """Dispatch a global hotkey."""
        handlers = {
            "region": self._capture.capture_region,
            "window": self._capture.capture_active_window,
            "fullscreen": self._capture.capture_fullscreen,
            "repeat": self._capture.repeat_last,
        }
        handler = handlers.get(action)
        if handler is not None:
            self._workspace.cancel_drag()
            handler()

    def review_scrolling_capture(self, session: ScrollSession) -> ScrollReviewPanel:
        """Own stopped-result review without enabling native scrolling acquisition.

        The future capture coordinator presents the returned nonmodal panel only
        after capture has stopped. Opening it does not activate or dismiss other UI.
        """
        self._workspace.cancel_drag()
        return self._scroll_reviews.open(session)

    def _deliver_scroll_review(self, result: ReviewedCapture) -> None:
        try:
            self.deliver_reviewed_capture(result)
        except (AppError, RuntimeError, ValueError, MemoryError) as error:
            self._scroll_reviews.delivery_failed(result.capture_id, str(error))
            self._notify("Capture retained for retry", str(error), warning=True)

    def _on_captured(self, capture: Capture) -> None:
        """Adapt an ordinary capture to the reviewed, producer-neutral seam."""
        self.deliver_reviewed_capture(
            ReviewedCapture(
                capture.image,
                capture.title,
                capture.taken_at,
                CaptureKind.SCREENSHOT,
                CaptureCompleteness.COMPLETE,
            )
        )

    def deliver_reviewed_capture(
        self,
        result: ReviewedCapture,
        *,
        target: EditorWindow | None = None,
        activate: bool = False,
    ) -> EditorWindow | None:
        """Apply chosen outputs once; preserve native popup/modal context by default.

        Partial images must already have passed producer-owned review. Capture
        plans, tiles, repeat targets and input backend objects never cross here.
        """
        if result.capture_id in self._completed_deliveries:
            self._scroll_reviews.acknowledge_delivery(result.capture_id)
            return self._capture_host(result.capture_id)
        attempt = self._pending_deliveries.setdefault(
            result.capture_id,
            CaptureDelivery(
                result, self._settings.capture, self._settings.output, target, activate
            ),
        )
        if attempt.in_flight:
            return self._capture_host(result.capture_id)
        attempt.in_flight = True
        try:
            return self._deliver_capture_attempt(attempt)
        finally:
            attempt.in_flight = False

    def _deliver_capture_attempt(self, attempt: CaptureDelivery) -> EditorWindow | None:
        """Settle one reserved attempt; reentrant duplicate callbacks coalesce."""
        errors: list[str] = []
        done = self._deliver_outputs(attempt, errors)
        if attempt.actions.open_editor or not attempt.actions.has_action:
            self._admit_capture(attempt, errors)
        if errors:
            self._scroll_reviews.delivery_failed(
                attempt.capture.capture_id, "\n".join(errors)
            )
            self._notify(
                "Could not deliver the capture",
                "\n".join(errors) + "\nCapture retained. Click to retry its delivery.",
                warning=True,
                on_click=lambda: self.retry_delivery(attempt.capture.capture_id),
            )
        else:
            self._pending_deliveries.pop(attempt.capture.capture_id)
            self._completed_deliveries.add(attempt.capture.capture_id)
            self._scroll_reviews.acknowledge_delivery(attempt.capture.capture_id)
        if done:
            hint = "\nClick to open the folder." if attempt.saved is not None else ""
            # Keep a failed attempt's retry action rather than replacing it.
            if errors:
                logger.info("Screenshot captured: %s", "\n".join(done))
            else:
                self._notify("Screenshot captured", "\n".join(done) + hint)
        self._resume_idle_if_ready()
        return self._capture_host(attempt.capture.capture_id)

    def retry_delivery(self, capture_id: str) -> None:
        """Retry failed sinks using current output settings without duplicating work."""
        attempt = self._pending_deliveries.get(capture_id)
        if attempt is not None and not attempt.in_flight:
            attempt.output = self._settings.output
            self.deliver_reviewed_capture(attempt.capture)

    def _deliver_outputs(
        self, attempt: CaptureDelivery, errors: list[str]
    ) -> list[str]:
        """Run independent outputs and count only confirmed successful delivery."""
        result, actions = attempt.capture, attempt.actions
        delivery = ImageDelivery(attempt.output)
        done: list[str] = []
        if actions.save_to_file and attempt.saved is None:
            try:
                attempt.saved = delivery.save(
                    result.image,
                    delivery.auto_path(title=result.title, moment=result.created_at),
                )
                self._last_saved_folder = attempt.saved.parent
                done.append(f"Saved {attempt.saved}")
            except AppError as error:
                errors.append(str(error))
        if actions.copy_to_clipboard and not attempt.copied:
            try:
                delivery.copy(result.image)
                attempt.copied = True
                done.append("Copied to clipboard")
            except AppError as error:
                errors.append(str(error))
        return done

    def _capture_record(self, capture_id: str) -> DocumentRecord | None:
        return next(
            (
                record
                for record in self._workspace.documents
                if record.provenance is not None
                and record.provenance.capture_id == capture_id
            ),
            None,
        )

    def _capture_host(self, capture_id: str) -> EditorWindow | None:
        record = self._capture_record(capture_id)
        return (
            self._workspace.host_of(record.identifier) if record is not None else None
        )

    def _admit_capture(self, attempt: CaptureDelivery, errors: list[str]) -> None:
        if attempt.admitted:
            self._refresh_capture_delivery(attempt)
            return
        result = attempt.capture
        options = EditorOptions(
            title=result.title,
            source_path=attempt.saved,
            confirm_close=self._settings.editor.confirm_unsaved_close,
            provenance=CaptureProvenance.from_capture(result),
        )
        try:
            self._open_document(
                result.image,
                options,
                delivered=attempt.saved is not None or attempt.copied,
                target=attempt.target,
                activate=attempt.activate,
            )
            attempt.admitted = True
            self._refresh_capture_delivery(attempt)
        except (AppError, RuntimeError, ValueError, MemoryError) as error:
            errors.append(f"Could not open capture editor: {error}")

    def _refresh_capture_delivery(self, attempt: CaptureDelivery) -> None:
        """A retried original-image output must never mark newer edits delivered."""
        record = self._capture_record(attempt.capture.capture_id)
        if record is not None:
            if record.saved_path is None:
                record.saved_path = attempt.saved
            record.copied = record.copied or attempt.copied
            if attempt.saved is not None or attempt.copied:
                record.session.history.mark_delivered(expected_state_id=0)
            self._workspace.refresh_document(record.identifier)

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
        activate: bool = True,
    ) -> EditorWindow:
        existing = (
            self._capture_record(options.provenance.capture_id)
            if options.provenance is not None
            else None
        )
        session = (
            existing.session
            if existing is not None
            else EditorSession(
                Document(image),
                self._settings.editor,
                remembered=self._styles.load(),
                on_style_change=self._styles.remember,
                delivered=delivered,
            )
        )
        return self._workspace.open_document(
            session,
            ImageDelivery(self._settings.output),
            options,
            target=target,
            activate=activate,
        )

    def _wire_editor(self, editor: EditorWindow) -> None:
        """Wire every host, including ones created by tab detachment."""
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
        self._resume_idle_if_ready()

    def _resume_idle_if_ready(self) -> None:
        if (
            not self._workspace.has_work
            and not self._pending_deliveries
            and not self._scroll_reviews.has_work
            and self._idle_timer.isActive()
        ):
            self._idle_timer.start(0)  # A deferred update can go ahead now

    # Tray commands

    def _tray_commands(self) -> TrayCommands:
        """Return the callbacks for the tray menu."""
        return TrayCommands(
            capture_region=lambda: self._on_hotkey("region"),
            capture_window=lambda: self._on_hotkey("window"),
            capture_fullscreen=lambda: self._on_hotkey("fullscreen"),
            repeat_last=lambda: self._on_hotkey("repeat"),
            open_image=self._ask_open_image,
            open_folder=self.open_screenshots_folder,
            settings=self.show_settings,
            about=self._show_about,
            exit=self.request_exit,
            update=self.check_for_updates if self._updater is not None else None,
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
        dialog = SettingsDialog(self._settings, parent, key_capture=self._key_capture)
        dialog.setWindowIcon(self._icon)
        dialog.make_default_requested.connect(self.make_default_image_app)
        dialog.hotkey_recording_changed.connect(self._pause_hotkeys)
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
    ) -> None:
        """Keep manual-check progress and its result visible until dismissed."""
        if self._update_status is None:
            self._update_status = QMessageBox()
            self._update_status.setObjectName("manual-update-status")
            self._update_status.setWindowIcon(self._icon)
            self._update_status.setTextFormat(Qt.TextFormat.PlainText)
        box = self._update_status
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(
            QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Close
            if checking
            else QMessageBox.StandardButton.Ok
        )
        box.show()
        box.raise_()
        box.activateWindow()

    def install_update(self, *, manual: bool) -> None:
        """Hand the available release to the updater and quit (UX-TRY-12).

        The updater installs it once VerdiClip has exited, then starts it
        again. A manual install first closes editors, each of which may ask
        about unsaved work; an automatic one only runs with none open.
        """
        updater = self._updater
        if updater is None:
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
        if not self._close_editors():
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
                "Use the tray's Install entry to install it.",
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

    def _install_when_idle(self) -> None:
        """Install now if nothing is open; otherwise try again later."""
        if (
            self._workspace.has_work
            or self._pending_deliveries
            or self._scroll_reviews.has_work
            or self._capture.is_selecting
        ):
            self._idle_timer.start(IDLE_RETRY_MS)
            return
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
        """Resolve retained captures and approve every document before disposal."""
        if self._closing_editors:
            return False
        self._closing_editors = True
        try:
            return self._close_retained_and_workspace()
        finally:
            self._closing_editors = False

    def _pending_plan_current(self, plan: tuple[CaptureDelivery, ...]) -> bool:
        return (
            not self._scroll_reviews.has_unaccepted
            and not any(item.in_flight for item in plan)
            and tuple(id(item) for item in self._pending_deliveries.values())
            == tuple(id(item) for item in plan)
        )

    def _close_retained_and_workspace(self) -> bool:
        if self._scroll_reviews.has_unaccepted:
            self._notify(
                "Scrolling review needs a decision",
                "Use or discard the scrolling capture in its review before closing.",
                warning=True,
            )
            return False
        plan = tuple(self._pending_deliveries.values())
        if any(item.in_flight for item in plan):
            self._notify(
                "Capture delivery in progress",
                "Please close again when the capture finishes.",
                warning=True,
            )
            return False
        for attempt in plan:
            prompt = ClosePrompt(
                f'The original capture "{attempt.capture.title or "Screenshot"}" '
                "still has unfinished automatic output. Save that original image "
                "to a file before closing? Don't save stops retrying it; "
                "editor changes are checked separately."
            )
            choice = prompt.ask(None)
            if choice is CloseChoice.CANCEL:
                return False
            if choice is CloseChoice.SAVE and not self._save_retained_capture(attempt):
                return False

        def guard() -> bool:
            return self._pending_plan_current(plan)

        if not self._workspace.close_all(can_commit=guard) or not guard():
            return False
        for attempt in plan:
            self._pending_deliveries.pop(attempt.capture.capture_id)
            self._completed_deliveries.add(attempt.capture.capture_id)
        return self._scroll_reviews.close_resolved()

    def _save_retained_capture(self, attempt: CaptureDelivery) -> bool:
        delivery = ImageDelivery(self._settings.output)
        try:
            start = attempt.saved or delivery.auto_path(
                title=attempt.capture.title, moment=attempt.capture.created_at
            )
        except AppError:
            start = self._settings.output.directory / "Screenshot.png"
        chosen, _filter = QFileDialog.getSaveFileName(
            None, "Save retained capture", str(start), IMAGE_FILTER
        )
        if not chosen:
            return False
        try:
            attempt.saved = delivery.save(attempt.capture.image, Path(chosen))
        except AppError as error:
            self._notify("Capture not saved", str(error), warning=True)
            return False
        self._refresh_capture_delivery(attempt)
        return True

    def _pause_hotkeys(self, paused: bool) -> None:  # noqa: FBT001 - Qt signal
        """Release hotkeys while Settings records one, so the keys reach it."""
        if paused:
            self._hotkeys.unregister_all()
        else:
            self._apply_hotkeys(report=False)

    def _apply_hotkeys(self, *, report: bool = True) -> None:
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
