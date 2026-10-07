"""Colors and drawing styles for annotations."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final, Self

_HEX_PATTERN: Final = re.compile(r"^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


@dataclass(frozen=True, slots=True)
class Color:
    """An 8-bit RGBA color."""

    red: int
    green: int
    blue: int
    alpha: int = 255

    def __post_init__(self) -> None:
        """Reject channels outside 0-255."""
        for channel in (self.red, self.green, self.blue, self.alpha):
            if not 0 <= channel <= 255:  # noqa: PLR2004 — channel range
                msg = f"Color channel out of range 0-255: {channel}"
                raise ValueError(msg)

    @classmethod
    def from_hex(cls, text: str) -> Self:
        """Parse ``#rrggbb`` or ``#rrggbbaa``."""
        match = _HEX_PATTERN.match(text.strip())
        if match is None:
            msg = f"Expected a color like '#ff0000' or '#ff000080', got {text!r}"
            raise ValueError(msg)
        rgb = int(match.group(1), 16)
        alpha = int(match.group(2), 16) if match.group(2) else 255
        return cls((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF, alpha)

    @property
    def hex(self) -> str:
        """Return ``#rrggbb`` when opaque, otherwise ``#rrggbbaa``."""
        rgb = f"#{self.red:02x}{self.green:02x}{self.blue:02x}"
        return rgb if self.alpha == 255 else f"{rgb}{self.alpha:02x}"  # noqa: PLR2004

    @property
    def is_transparent(self) -> bool:
        """True when fully transparent."""
        return self.alpha == 0

    def with_alpha(self, alpha: int) -> Color:
        """Return the same color with a different alpha."""
        return replace(self, alpha=alpha)


TRANSPARENT: Final = Color(0, 0, 0, 0)
RED: Final = Color(0xE5, 0x1C, 0x23)
WHITE: Final = Color(255, 255, 255)
BLACK: Final = Color(0, 0, 0)
HIGHLIGHT_YELLOW: Final = Color(0xFF, 0xEB, 0x3B, 0x80)


@dataclass(frozen=True, slots=True)
class Style:
    """How an annotation is drawn.

    ``width`` is the stroke width in image pixels; for obfuscation it is the
    pixelation block size.
    """

    stroke: Color = RED
    fill: Color = TRANSPARENT
    width: float = 3.0
    font_family: str = "Segoe UI"
    font_size: int = 16
    bold: bool = False
    italic: bool = False

    def __post_init__(self) -> None:
        """Reject non-positive sizes."""
        if self.width <= 0:
            msg = f"Style width must be positive, got {self.width}"
            raise ValueError(msg)
        if self.font_size <= 0:
            msg = f"Style font size must be positive, got {self.font_size}"
            raise ValueError(msg)
