"""Tests for theme-aware editor chrome."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from verdiclip.editor.chrome import ACCENT, ChromeStyle


def palette(window: str, text: str) -> QPalette:
    """Return a palette with the given window and text colors."""
    result = QPalette()
    result.setColor(QPalette.ColorRole.Window, QColor(window))
    result.setColor(QPalette.ColorRole.WindowText, QColor(text))
    return result


class TestChromeStyle:
    """Tests for ChromeStyle."""

    def test_light_theme_gets_light_backdrop(self) -> None:
        """UX-G-08: light windows put the image on a light-gray backdrop."""
        chrome = ChromeStyle(palette("#f3f3f3", "#000000"))

        assert not chrome.is_dark
        assert chrome.backdrop.lightness() > 180

    def test_dark_theme_gets_dark_backdrop(self) -> None:
        """UX-G-08: dark windows keep a dark backdrop distinct from the window."""
        chrome = ChromeStyle(palette("#1e1e1e", "#ffffff"))

        assert chrome.is_dark
        assert chrome.backdrop.lightness() < 80
        assert chrome.backdrop != QColor("#1e1e1e")

    def test_checked_tool_uses_soft_accent_not_dark_block(self) -> None:
        """The selected tool is tinted with the accent in both themes."""
        accent = f"rgba({ACCENT.red()}, {ACCENT.green()}, {ACCENT.blue()}"

        light = ChromeStyle(palette("#f3f3f3", "#000000")).toolbar_sheet()
        dark = ChromeStyle(palette("#1e1e1e", "#ffffff")).toolbar_sheet(26)

        assert f"QToolButton:checked {{ background: {accent}, 0.16)" in light
        assert f"{accent}, 0.4)" in dark
        assert "min-width: 26px" in dark
