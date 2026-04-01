"""Main VerdiClip application class."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication

from verdiclip import __app_name__, __version__
from verdiclip.config import Config

logger = logging.getLogger(__name__)

_SHARED_MEMORY_KEY = "VerdiClip_SingleInstance_Lock"


class VerdiClipApp:
    """Top-level application controller."""

    def __init__(self, argv: list[str]) -> None:
        self._argv = argv
        self._shared_memory = QSharedMemory(_SHARED_MEMORY_KEY)
        self._qt_app: QApplication | None = None
        self._config: Config | None = None
        self._tray_icon = None
        self._hotkey_manager = None

    def _is_already_running(self) -> bool:
        """Check if another instance is already running via shared memory."""
        if self._shared_memory.attach():
            self._shared_memory.detach()
            return True
        return False

    def _init_qt(self) -> QApplication:
        """Initialize the Qt application."""
        app = QApplication(self._argv)
        app.setApplicationName(__app_name__)
        app.setApplicationVersion(__version__)
        app.setQuitOnLastWindowClosed(False)
        return app

    def run(self) -> int:
        """Start the application. Returns exit code."""
        if self._is_already_running():
            logger.warning("VerdiClip is already running. Exiting.")
            print("VerdiClip is already running.", file=sys.stderr)
            return 1

        if not self._shared_memory.create(1):
            logger.warning(
                "Could not create shared memory lock: %s",
                self._shared_memory.errorString(),
            )

        self._qt_app = self._init_qt()
        self._config = Config()

        logger.info("VerdiClip %s started successfully.", __version__)
        self._setup_tray()
        self._setup_hotkeys()

        return self._qt_app.exec()

    def _setup_tray(self) -> None:
        """Initialize the system tray icon."""
        from verdiclip.tray.icon import TrayIcon

        self._tray_icon = TrayIcon(self._qt_app, self._config)
        self._tray_icon.show()
        logger.info("System tray icon initialized.")

    def _setup_hotkeys(self) -> None:
        """Initialize global hotkey listener."""
        from verdiclip.hotkeys.manager import HotkeyManager

        self._hotkey_manager = HotkeyManager(self._config)
        self._hotkey_manager.start()
        logger.info("Global hotkey listener started.")

    @property
    def config(self) -> Config:
        """Return the application configuration."""
        assert self._config is not None
        return self._config
