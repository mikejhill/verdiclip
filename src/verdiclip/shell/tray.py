"""System tray icon and menu."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from verdiclip import APP_NAME, VERSION

CHECK_UPDATES_LABEL: Final = "Check for updates"


@dataclass(frozen=True, slots=True)
class TrayCommands:
    """Callbacks the tray menu triggers."""

    capture_region: Callable[[], None]
    capture_window: Callable[[], None]
    capture_fullscreen: Callable[[], None]
    repeat_last: Callable[[], None]
    open_image: Callable[[], None]
    open_folder: Callable[[], None]
    settings: Callable[[], None]
    about: Callable[[], None]
    exit: Callable[[], None]
    update: Callable[[], None] | None = None  # None: no update entry
    capture_scrolling: Callable[[], None] | None = None


class TrayIcon(QSystemTrayIcon):
    """Tray icon: click captures a region, double-click opens the screenshots folder.

    A click waits out the double-click interval before capturing, so the
    capture overlay doesn't swallow the second click of a double-click.
    """

    def __init__(
        self, icon: QIcon, commands: TrayCommands, parent: QWidget | None = None
    ) -> None:
        super().__init__(icon, parent)
        self._commands = commands
        self._menu = QMenu()
        self._actions: dict[str, QAction] = {}
        self._hotkey_labels: Mapping[str, str] = {}
        self._update_label = CHECK_UPDATES_LABEL
        self._update_checking = False
        self._scrolling_available = False
        self._click = QTimer(self)
        self._click.setSingleShot(True)
        self._click.timeout.connect(self._on_click)
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

    def set_update_label(self, text: str) -> None:
        """Name the update entry: check, or install a release found (UX-TRY-11)."""
        self._update_label = text
        self.rebuild_menu(self._hotkey_labels)

    def set_update_checking(self, *, checking: bool) -> None:
        """Disable duplicate manual checks and expose progress in the menu."""
        self._update_checking = checking
        self.rebuild_menu(self._hotkey_labels)

    def set_scrolling_available(self, *, available: bool) -> None:
        """Expose configured capability without rebuilding an open popup."""
        self._scrolling_available = available
        action = self._actions.get("scrolling")
        if action is not None:
            action.setEnabled(available)

    def rebuild_menu(self, hotkey_labels: Mapping[str, str]) -> None:
        """Rebuild the menu, showing each capture's hotkey (UX-TRY-03)."""
        c = self._commands
        self._hotkey_labels = dict(hotkey_labels)
        self._menu.clear()
        self._actions.clear()
        check = c.update
        label = (
            "Checking for updates..." if self._update_checking else self._update_label
        )
        update = [("update", label, check)] if check is not None else []
        scrolling = (
            [("scrolling", "Capture scrolling area…", c.capture_scrolling)]
            if c.capture_scrolling is not None
            else []
        )
        entries: list[tuple[str, str, Callable[[], None]] | None] = [
            ("region", "Capture region or window", c.capture_region),
            ("window", "Capture active window", c.capture_window),
            ("fullscreen", "Capture full screen", c.capture_fullscreen),
            *scrolling,
            ("repeat", "Repeat last capture", c.repeat_last),
            None,
            ("open", "Open image…", c.open_image),
            ("folder", "Open screenshots folder", c.open_folder),
            None,
            ("settings", "Settings…", c.settings),
            *update,
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
            if name == "update":
                action.setEnabled(not self._update_checking)
            elif name == "scrolling":
                action.setEnabled(self._scrolling_available)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Capture a region on click, or open the folder on double-click (UX-TRY-02)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._click.start(QApplication.doubleClickInterval())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._click.stop()
            self._commands.open_folder()

    def _on_click(self) -> None:
        """Capture a region once a click turned out not to be a double-click."""
        self._commands.capture_region()
