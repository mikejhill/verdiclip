"""Apply the light, dark, or system color scheme."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from verdiclip.settings import Theme


class ThemeManager:
    """Switch the whole app between light, dark, and the Windows setting."""

    SCHEMES: ClassVar[dict[Theme, Qt.ColorScheme]] = {
        Theme.SYSTEM: Qt.ColorScheme.Unknown,
        Theme.LIGHT: Qt.ColorScheme.Light,
        Theme.DARK: Qt.ColorScheme.Dark,
    }

    @classmethod
    def apply(cls, theme: Theme) -> None:
        """Apply ``theme`` to every window immediately."""
        QGuiApplication.styleHints().setColorScheme(cls.SCHEMES[theme])
