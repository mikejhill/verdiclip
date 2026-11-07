"""Independent small synthetic-pixel oracle, never a product viewport crop."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


RGB_CHANNELS = 3
ROW_COUNT = 47
ROW_HEIGHT = 31
ROW_INTERIOR_START = 3
ROW_INTERIOR_END = 28


@dataclass(frozen=True)
class Pixels:
    """Physical RGB bytes with known shape."""

    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        """Validate the physical dimensions and packed RGB buffer."""
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Invalid image dimensions")
        if len(self.rgb) != self.width * self.height * 3:
            raise ValueError("Invalid RGB buffer length")

    def crop(self, rect: Sequence[int]) -> Pixels:
        """Extract only an independently verified rectangle without clipping."""
        left, top, right, bottom = rect
        if not (0 <= left < right <= self.width and 0 <= top < bottom <= self.height):
            raise ValueError("Oracle rectangle outside captured pixels")
        stride = self.width * 3
        return Pixels(
            right - left,
            bottom - top,
            b"".join(
                self.rgb[y * stride + left * 3 : y * stride + right * 3]
                for y in range(top, bottom)
            ),
        )


def color_bounds(pixels: Pixels, color: bytes) -> tuple[int, int, int, int]:
    """Find a known fixture-only border color in actual native pixels."""
    if len(color) != RGB_CHANNELS:
        raise ValueError("A single RGB color is required")
    locations = [
        index // 3
        for index in range(0, len(pixels.rgb), 3)
        if pixels.rgb[index : index + 3] == color
    ]
    if not locations:
        raise RuntimeError("Authored fixture border not visible")
    xs = [index % pixels.width for index in locations]
    ys = [index // pixels.width for index in locations]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def overlap_displacements(first: Pixels, second: Pixels) -> list[int]:
    """Measure all exact full-width overlaps; percent is not pixel distance."""
    if (first.width, first.height) != (second.width, second.height):
        raise ValueError("Frame dimensions changed")
    stride = first.width * 3
    minimum = max(24, first.height // 4)
    return [
        shift
        for shift in range(1, first.height - minimum + 1)
        if first.rgb[shift * stride :] == second.rgb[: (first.height - shift) * stride]
    ]


def row_color(index: int) -> bytes:
    """Return the independently known color for one authored synthetic row."""
    return bytes(32 + index * multiplier % 190 for multiplier in (53, 29, 71))


def inspect_rows(pixels: Pixels, offset: float, scale: float) -> dict[str, object]:
    """Check row interiors, excluding fractional boundaries and rendered text."""
    x = pixels.width - 12
    checked = []
    failed = []
    for y in range(pixels.height):
        logical = (y + 0.5) / scale + offset
        row = int(logical // ROW_HEIGHT)
        within = logical % ROW_HEIGHT
        if (
            not 0 <= row < ROW_COUNT
            or not ROW_INTERIOR_START <= within <= ROW_INTERIOR_END
        ):
            continue
        actual = pixels.rgb[(y * pixels.width + x) * 3 : (y * pixels.width + x) * 3 + 3]
        checked.append(row)
        if actual != row_color(row):
            failed.append(y)
    return {
        "checked_scanlines": len(checked),
        "visible_row_ids": sorted(set(checked)),
        "mismatching_scanlines": failed,
        "matches": bool(checked) and not failed,
        "rgb_sha256": hashlib.sha256(pixels.rgb).hexdigest(),
    }
