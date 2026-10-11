"""Tests for switching the app's color scheme."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory
from pytestqt.qtbot import QtBot

from verdiclip.settings import Theme
from verdiclip.shell import theme
from verdiclip.shell.theme import ThemeManager


class TestThemeManager:
    """Tests for ThemeManager."""

    def test_scheme_change_rebuilds_the_style(self, qtbot: QtBot) -> None:
        """UX-G-08: a scheme switch gets a fresh style so controls aren't stale.

        Qt's windows11 style keeps the old scheme's colors, so checked boxes
        vanish in dark mode; the offscreen platform can't render that, so this
        checks the style is replaced with a new instance of the same style.
        """
        del qtbot
        ThemeManager.apply(Theme.DARK)
        before = QApplication.style()
        name = before.name()

        QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)

        after = QApplication.style()
        assert after is not before
        assert after.name() == name

    def test_without_a_widget_app_does_nothing(
        self, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a widget application has a style to refresh."""
        del qtbot
        created: list[QStyle] = []
        monkeypatch.setattr(theme.QApplication, "instance", lambda: None)
        monkeypatch.setattr(QStyleFactory, "create", created.append)

        ThemeManager.refresh_style()

        assert created == []
