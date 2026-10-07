"""System tray icon and menu."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from verdiclip import APP_NAME, VERSION


@dataclass(frozen=True, slots=True)
class TrayCommands:
    """Callbacks the tray menu triggers."""

    capture_region: Callable[[], None]
    capture_window: Callable[[], None]
    capture_fullscreen: Callable[[], None]
    repeat_last: Callable[[], None]
    open_image: Callable[[], None]
    settings: Callable[[], None]
    about: Callable[[], None]
    exit: Callable[[], None]


class TrayIcon(QSystemTrayIcon):
    """Tray icon: left-click captures a region, right-click shows the menu."""

    def __init__(
        self, icon: QIcon, commands: TrayCommands, parent: QWidget | None = None
    ) -> None:
        super().__init__(icon, parent)
        self._commands = commands
        self._menu = QMenu()
        self._actions: dict[str, QAction] = {}
        self.setToolTip(f"{APP_NAME} {VERSION} — click to capture")
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self.rebuild_menu({})

    @property
    def menu(self) -> QMenu:
        """Return the context menu."""
        return self._menu

    def action(self, name: str) -> QAction:
        """Return the named menu action."""
        return self._actions[name]

    def rebuild_menu(self, hotkey_labels: Mapping[str, str]) -> None:
        """Rebuild the menu, showing each capture's hotkey (UX-TRY-03)."""
        c = self._commands
        self._menu.clear()
        self._actions.clear()
        entries: list[tuple[str, str, Callable[[], None]] | None] = [
            ("region", "Capture region or window", c.capture_region),
            ("window", "Capture active window", c.capture_window),
            ("fullscreen", "Capture full screen", c.capture_fullscreen),
            ("repeat", "Repeat last capture", c.repeat_last),
            None,
            ("open", "Open image…", c.open_image),
            None,
            ("settings", "Settings…", c.settings),
            ("about", f"About {APP_NAME}", c.about),
            ("exit", "Exit", c.exit),
        ]
        for entry in entries:
            if entry is None:
                self._menu.addSeparator()
                continue
            name, text, callback = entry
            label = hotkey_labels.get(name, "")
            action = self._menu.addAction(f"{text}\t{label}" if label else text)
            action.triggered.connect(lambda _=False, cb=callback: cb())
            self._actions[name] = action

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Start a region capture on left-click (UX-TRY-02)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._commands.capture_region()
