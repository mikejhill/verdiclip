"""Main VerdiClip application class."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication

from verdiclip import __app_name__, __version__
from verdiclip.config import Config

if TYPE_CHECKING:
    from collections.abc import Callable

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
        self._post_init_hooks: list = []

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
        self._qt_app.setProperty("verdiclip_controller", self)
        self._qt_app.aboutToQuit.connect(self._cleanup)

        for hook in self._post_init_hooks:
            hook()

        self._config = Config()

        logger.info("VerdiClip %s started successfully.", __version__)
        self._setup_tray()
        self._setup_hotkeys()

        return self._qt_app.exec()

    def _cleanup(self) -> None:
        """Gracefully shut down all components before the process exits."""
        logger.info("Shutting down VerdiClip…")
        if self._hotkey_manager:
            self._hotkey_manager.stop()
            self._hotkey_manager = None
        if self._tray_icon:
            self._tray_icon.hide()
            self._tray_icon = None
        if self._shared_memory.isAttached():
            self._shared_memory.detach()
        logger.info("Cleanup complete.")

    def _setup_tray(self) -> None:
        """Initialize the system tray icon."""
        from verdiclip.tray.icon import TrayIcon

        self._tray_icon = TrayIcon(self._qt_app, self._config)
        self._tray_icon.show()
        logger.info("System tray icon initialized.")

    def _setup_hotkeys(self) -> None:
        """Initialize global hotkey listener and register capture callbacks."""
        from verdiclip.hotkeys.manager import HotkeyManager

        self._hotkey_manager = HotkeyManager(self._config)
        self._register_hotkeys()
        self._hotkey_manager.start()
        logger.info("Global hotkey listener started.")

    def _register_hotkeys(self) -> None:
        """Register configured hotkeys with tray capture callbacks."""
        if self._hotkey_manager is None:
            raise RuntimeError("Hotkey manager not initialized")
        if self._tray_icon is None:
            raise RuntimeError("Tray icon not initialized")

        bindings = {
            "hotkeys.region": self._tray_icon.capture_region,
            "hotkeys.fullscreen": self._tray_icon.capture_screen,
            "hotkeys.window": self._tray_icon.capture_window,
            "hotkeys.repeat": self._tray_icon.capture_repeat,
        }
        for config_key, callback in bindings.items():
            hotkey_str = self._config.get(config_key, "")
            if hotkey_str:
                self._hotkey_manager.register(hotkey_str, callback)

    def reload_hotkeys(self) -> None:
        """Reload hotkey bindings from config (called after settings change)."""
        if self._hotkey_manager:
            self._hotkey_manager.reload_from_config()
            self._register_hotkeys()
            logger.info("Hotkey bindings reloaded.")

    def register_post_init_hook(self, hook: Callable[[], None]) -> None:
        """Register a callback to run after QApplication initialization."""
        self._post_init_hooks.append(hook)

    @property
    def config(self) -> Config:
        """Return the application configuration."""
        if self._config is None:
            raise RuntimeError("Config not initialized")
        return self._config
