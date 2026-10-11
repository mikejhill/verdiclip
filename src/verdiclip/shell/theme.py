"""Apply the light, dark, or system color scheme."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QStyleFactory

from verdiclip.settings import Theme


class ThemeManager:
    """Switch the whole app between light, dark, and the Windows setting."""

    SCHEMES: ClassVar[dict[Theme, Qt.ColorScheme]] = {
        Theme.SYSTEM: Qt.ColorScheme.Unknown,
        Theme.LIGHT: Qt.ColorScheme.Light,
        Theme.DARK: Qt.ColorScheme.Dark,
    }
    _watching: ClassVar[bool] = False

    @classmethod
    def apply(cls, theme: Theme) -> None:
        """Apply ``theme`` to every window immediately."""
        hints = QGuiApplication.styleHints()
        if not cls._watching:
            # Also catches Windows switching modes under "Match Windows"
            hints.colorSchemeChanged.connect(cls.refresh_style)
            cls._watching = True
        hints.setColorScheme(cls.SCHEMES[theme])

    @classmethod
    def refresh_style(cls) -> None:
        """Replace the app style with a fresh instance of the same style.

        Qt's windows11 style keeps colors from the previous scheme after a
        switch, which paints checked checkboxes in the window color (invisible
        in dark mode). A new instance picks up the current scheme.
        """
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        app.setStyle(QStyleFactory.create(app.style().name()))
