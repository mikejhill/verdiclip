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

    def test_backdrop_is_one_step_off_the_window(self) -> None:
        """The backdrop is the window color lightened (dark) or darkened (light)."""
        light, dark = QColor("#f3f3f3"), QColor("#1e1e1e")

        assert ChromeStyle(palette("#f3f3f3", "#000")).backdrop == light.darker(112)
        assert ChromeStyle(palette("#1e1e1e", "#fff")).backdrop == dark.lighter(125)

    def test_dark_means_lightness_below_the_midpoint(self) -> None:
        """Lightness 127 counts as dark and 128 as light."""
        below = QColor.fromHsl(0, 0, 127).name()
        at = QColor.fromHsl(0, 0, 128).name()

        assert ChromeStyle(palette(below, "#fff")).is_dark
        assert not ChromeStyle(palette(at, "#000")).is_dark

    def test_sheet_states_derive_from_text_and_accent(self) -> None:
        """Hover and press tint the text color; checked tools get an accent outline."""
        accent = f"rgba({ACCENT.red()}, {ACCENT.green()}, {ACCENT.blue()}"

        light = ChromeStyle(palette("#f3f3f3", "#102030")).toolbar_sheet()
        dark = ChromeStyle(palette("#1e1e1e", "#ffffff")).toolbar_sheet()

        assert "QToolButton:hover { background: rgba(16, 32, 48, 0.08); }" in light
        assert "QToolButton:pressed { background: rgba(16, 32, 48, 0.14); }" in light
        assert f"border-color: {accent}, 0.75); }}" in light
        assert f"border-color: {accent}, 1.0); }}" in dark
        assert "min-width: 28px; min-height: 28px;" in light
        assert "QToolButton:disabled { background: transparent; }" in light
        assert (
            "QToolButton#qt_toolbar_ext_button { min-width: 0; min-height: 0;"
            " padding: 0; }"
        ) in light
        assert light.startswith("QToolBar { spacing: 2px; padding: 2px; }")
