"""Theme-aware colors and styling for the editor's toolbars and canvas."""

from __future__ import annotations

from typing import Final

from PySide6.QtGui import QColor, QPalette

BUTTON_SIZE: Final = 34
COMPACT_BUTTON_SIZE: Final = 26
ICON_SIZE: Final = 22
LIGHTNESS_MIDPOINT: Final = 128
# VerdiClip's accent, shared with selection outlines on the canvas
ACCENT: Final = QColor(0, 120, 215)


class ChromeStyle:
    """Derive toolbar and canvas colors from the active palette."""

    def __init__(self, palette: QPalette) -> None:
        self._window = palette.color(QPalette.ColorRole.Window)
        self._text = palette.color(QPalette.ColorRole.WindowText)
        self._accent = ACCENT

    @property
    def is_dark(self) -> bool:
        """True when the window background is dark."""
        return self._window.lightness() < LIGHTNESS_MIDPOINT

    @property
    def backdrop(self) -> QColor:
        """Return the canvas color around the image: a step off the window color."""
        if self.is_dark:
            return self._window.lighter(125)
        return self._window.darker(112)

    def toolbar_sheet(self, button: int = BUTTON_SIZE) -> str:
        """Return a style sheet for tool and action buttons.

        The selected tool gets a soft accent tint with an accent outline, so
        its icon stays readable in both themes.
        """
        hover = self._rgba(self._text, 0.08)
        pressed = self._rgba(self._text, 0.14)
        checked = self._rgba(self._accent, 0.40 if self.is_dark else 0.16)
        outline = self._rgba(self._accent, 1.0 if self.is_dark else 0.75)
        return (
            "QToolBar { spacing: 2px; padding: 2px; }"
            f"QToolButton {{ min-width: {button}px; min-height: {button}px;"
            " border: 1px solid transparent; border-radius: 5px; padding: 3px; }"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:pressed {{ background: {pressed}; }}"
            f"QToolButton:checked {{ background: {checked}; border-color: {outline}; }}"
            "QToolButton:disabled { background: transparent; }"
        )

    @staticmethod
    def _rgba(color: QColor, alpha: float) -> str:
        """Return a CSS rgba() for ``color`` at ``alpha`` (0-1)."""
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
