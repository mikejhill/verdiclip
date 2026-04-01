"""System tray icon and context menu for VerdiClip."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
        self._menu = self._build_menu()
        self.setContextMenu(self._menu)
        self.setToolTip(f"{__app_name__} {__version__}")

        self.activated.connect(self._on_activated)

    def _build_menu(self) -> QMenu:
        """Build the tray context menu."""
        menu = QMenu()

        # Capture actions
        capture_region = QAction("Capture Region", menu)
        capture_region.setShortcut("Print")
        capture_region.triggered.connect(self._capture_region)
        menu.addAction(capture_region)

        capture_window = QAction("Capture Window", menu)
        capture_window.triggered.connect(self._capture_window)
        menu.addAction(capture_window)

        capture_screen = QAction("Capture Full Screen", menu)
        capture_screen.triggered.connect(self._capture_screen)
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

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation (e.g., left-click)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            logger.debug("Tray icon left-clicked.")
            self._capture_region()

    def _capture_region(self) -> None:
        """Trigger region capture."""
        logger.info("Tray: Capture region requested.")
        from verdiclip.capture.region import RegionCapture

        capture = RegionCapture()
        capture.start_selection(
            on_captured=self._open_editor,
            on_cancelled=lambda: logger.info("Region capture cancelled."),
        )
        # Keep reference to prevent garbage collection
        self._active_capture = capture

    def _capture_window(self) -> None:
        """Trigger active window capture."""
        logger.info("Tray: Capture window requested.")
        from verdiclip.capture.window import WindowCapture

        pixmap = WindowCapture.capture_active_window(
            include_decorations=self._config.get("capture.window_decorations", True)
        )
        self._open_editor(pixmap)

    def _capture_screen(self) -> None:
        """Trigger full-screen capture."""
        logger.info("Tray: Capture full screen requested.")
        from verdiclip.capture.screen import ScreenCapture

        pixmap = ScreenCapture.capture_all_monitors()
        self._open_editor(pixmap)

    def _open_editor(self, pixmap: QPixmap) -> None:
        """Open the editor with a captured image."""
        from verdiclip.editor.canvas import EditorWindow

        self._editor = EditorWindow(pixmap, self._config)
        self._editor.show()

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
                self._open_editor(pixmap)
            else:
                logger.error("Failed to load image: %s", file_path)

    def _show_settings(self) -> None:
        """Open the settings dialog."""
        from verdiclip.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._config)
        dialog.exec()

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
