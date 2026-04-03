"""System tray icon and context menu for VerdiClip."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from verdiclip import __app_name__, __version__

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from verdiclip.config import Config

logger = logging.getLogger(__name__)


def _create_default_icon() -> QIcon:
    """Create a simple default icon (green 'V' on dark background)."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(34, 139, 34))

    painter = QPainter(pixmap)
    painter.setPen(QColor(255, 255, 255))
    font = QFont("Segoe UI", 36, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), 0x0084, "V")  # AlignCenter
    painter.end()

    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    """System tray icon with context menu for VerdiClip."""

    def __init__(self, app: QApplication, config: Config) -> None:
        super().__init__(_create_default_icon(), app)
        self._app = app
        self._config = config
        self._editors: list = []
        self._active_capture = None
        self._last_capture_type: str | None = None
        self._last_region: QRect | None = None
        self._menu = self._build_menu()
        self.setContextMenu(self._menu)
        self.setToolTip(f"{__app_name__} {__version__}")

        self.activated.connect(self._on_activated)

    def _build_menu(self) -> QMenu:
        """Build the tray context menu with current hotkey labels."""
        menu = QMenu()

        def _hotkey_label(config_key: str) -> str:
            """Format a config hotkey string for display in menu."""
            hotkey = self._config.get(config_key, "")
            if hotkey:
                return hotkey.replace("+", " + ").replace("_", " ").title()
            return ""

        # Capture actions
        capture_region = QAction("Capture Region", menu)
        shortcut = _hotkey_label("hotkeys.region")
        if shortcut:
            capture_region.setText(f"Capture Region\t{shortcut}")
        capture_region.triggered.connect(self.capture_region)
        menu.addAction(capture_region)

        capture_window = QAction("Capture Window", menu)
        shortcut = _hotkey_label("hotkeys.window")
        if shortcut:
            capture_window.setText(f"Capture Window\t{shortcut}")
        capture_window.triggered.connect(self.capture_window)
        menu.addAction(capture_window)

        capture_window_pick = QAction("Capture Window (Select)...", menu)
        capture_window_pick.triggered.connect(self.capture_window_interactive)
        menu.addAction(capture_window_pick)

        capture_screen = QAction("Capture Full Screen", menu)
        shortcut = _hotkey_label("hotkeys.fullscreen")
        if shortcut:
            capture_screen.setText(f"Capture Full Screen\t{shortcut}")
        capture_screen.triggered.connect(self.capture_screen)
        menu.addAction(capture_screen)

        menu.addSeparator()

        # Open image
        open_image = QAction("Open Image...", menu)
        open_image.triggered.connect(self._open_image)
        menu.addAction(open_image)

        menu.addSeparator()

        # Settings
        settings = QAction("Settings...", menu)
        settings.triggered.connect(self._show_settings)
        menu.addAction(settings)

        # About
        about = QAction("About", menu)
        about.triggered.connect(self._show_about)
        menu.addAction(about)

        menu.addSeparator()

        # Exit
        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(self._exit_app)
        menu.addAction(exit_action)

        return menu

    def rebuild_menu(self) -> None:
        """Rebuild the tray menu to reflect updated hotkey labels."""
        self._menu = self._build_menu()
        self.setContextMenu(self._menu)
        logger.info("Tray menu rebuilt with updated hotkey labels.")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation (e.g., left-click)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            logger.debug("Tray icon left-clicked.")
            self.capture_region()

    def capture_region(self) -> None:
        """Trigger region capture."""
        logger.info("Tray: Capture region requested.")
        from verdiclip.capture.region import RegionCapture

        capture = RegionCapture()

        def _on_region_captured(pixmap: QPixmap) -> None:
            if capture.last_region:
                self._last_region = capture.last_region
            self._handle_capture(pixmap)

        capture.start_selection(
            on_captured=_on_region_captured,
            on_cancelled=lambda: logger.info("Region capture cancelled."),
        )
        self._active_capture = capture
        self._last_capture_type = "region"

    def capture_window(self) -> None:
        """Trigger active window capture."""
        logger.info("Tray: Capture window requested.")
        from verdiclip.capture.window import WindowCapture

        pixmap = WindowCapture.capture_active_window(
            include_decorations=self._config.get("capture.window_decorations", True)
        )
        self._last_capture_type = "window"
        self._handle_capture(pixmap)

    def capture_screen(self) -> None:
        """Trigger full-screen capture."""
        logger.info("Tray: Capture full screen requested.")
        from verdiclip.capture.screen import ScreenCapture

        pixmap = ScreenCapture.capture_all_monitors()
        self._last_capture_type = "screen"
        self._handle_capture(pixmap)

    def capture_window_interactive(self) -> None:
        """Trigger interactive window capture — user selects a window by clicking."""
        logger.info("Tray: Interactive window capture requested.")
        from verdiclip.capture.window_picker import WindowPicker

        picker = WindowPicker()

        def _on_window_captured(pixmap: QPixmap) -> None:
            self._last_capture_type = "window"
            self._handle_capture(pixmap)

        picker.window_captured.connect(_on_window_captured)
        picker.start()
        self._active_capture = picker

    def capture_repeat(self) -> None:
        """Repeat the last capture action."""
        logger.info("Tray: Repeat last capture requested.")
        if self._last_capture_type == "region" and self._last_region:
            from verdiclip.capture.screen import ScreenCapture
            pixmap = ScreenCapture.capture_region(self._last_region)
            self._handle_capture(pixmap)
        elif self._last_capture_type == "window":
            self.capture_window()
        elif self._last_capture_type == "screen":
            self.capture_screen()
        elif self._last_capture_type == "region":
            self.capture_region()
        else:
            logger.info("No previous capture to repeat.")

    def _handle_capture(self, pixmap: QPixmap) -> None:
        """Handle a completed capture — autosave and/or open editor."""
        if pixmap.isNull():
            logger.warning("Capture returned a null pixmap.")
            return

        # Auto-save if enabled
        if self._config.get("save.auto_save_enabled", False):
            from verdiclip.export.file_export import FileExporter
            path = FileExporter.auto_save(pixmap, self._config)
            if path:
                logger.info("Auto-saved capture to %s", path)

        self._open_editor(pixmap)

    def _open_editor(self, pixmap: QPixmap, file_path: str = "") -> None:
        """Open the editor with a captured image."""
        from verdiclip.editor.window import EditorWindow

        editor = EditorWindow(pixmap, self._config, file_path=file_path)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.destroyed.connect(
            lambda: self._editors.remove(editor) if editor in self._editors else None
        )
        self._editors.append(editor)
        editor.show()

    def _open_image(self) -> None:
        """Open an existing image file for editing."""
        from PySide6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Open Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp);;All Files (*)",
        )
        if file_path:
            logger.info("Opening image: %s", file_path)
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._open_editor(pixmap, file_path=file_path)
            else:
                logger.error("Failed to load image: %s", file_path)

    def _show_settings(self) -> None:
        """Open the settings dialog (non-modal so editors remain accessible)."""
        from verdiclip.ui.settings_dialog import SettingsDialog

        # Reuse existing dialog if still open
        if hasattr(self, "_settings_dialog") and self._settings_dialog is not None:
            try:
                self._settings_dialog.raise_()
                self._settings_dialog.activateWindow()
                return
            except RuntimeError:
                self._settings_dialog = None

        dialog = SettingsDialog(self._config)
        dialog.settings_saved.connect(self._on_settings_saved)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, "_settings_dialog", None))
        dialog.show()
        self._settings_dialog = dialog

    def _on_settings_saved(self) -> None:
        """Handle settings saved — rebuild menu and notify app to reload hotkeys."""
        self.rebuild_menu()
        # Reload hotkeys if app controller is connected
        from verdiclip.app import VerdiClipApp
        app = self._app.property("verdiclip_controller")
        if isinstance(app, VerdiClipApp):
            app.reload_hotkeys()

    def _show_about(self) -> None:
        """Open the about dialog."""
        from verdiclip.ui.about_dialog import AboutDialog

        dialog = AboutDialog()
        dialog.exec()

    def _exit_app(self) -> None:
        """Exit the application."""
        logger.info("Exit requested from tray menu.")
        self.hide()
        self._app.quit()
