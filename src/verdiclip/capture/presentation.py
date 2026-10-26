"""Window policy for surfaces driven exclusively by the capture input broker."""

from typing import Final

from PySide6.QtCore import Qt

__all__ = ["PASSIVE_WINDOW_FLAGS"]

# A Tool window can still acquire Qt platform focus (notably in offscreen QPA).
# ToolTip denotes passive presentation, not a QToolTip-managed expiring widget.
# No input or focus is transferred here; owned controls use broker commands.
PASSIVE_WINDOW_FLAGS: Final = (
    Qt.WindowType.ToolTip
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.WindowDoesNotAcceptFocus
    | Qt.WindowType.WindowTransparentForInput
)
