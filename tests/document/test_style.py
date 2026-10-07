"""Test RGBA colors and annotation styles."""

from __future__ import annotations

import pytest

from verdiclip.document.style import Color, Style


class TestColor:
    """Color parsing and channel invariants."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("#abcdef", Color(171, 205, 239)),
            (" #ABCDEF80 ", Color(171, 205, 239, 128)),
            ("#00000000", Color(0, 0, 0, 0)),
        ],
        ids=["opaque", "translucent", "transparent"],
    )
    def test_hex_round_trip(self, text: str, expected: Color) -> None:
        """Hex parsing preserves every channel and canonicalizes case."""
        color = Color.from_hex(text)

        encoded = color.hex

        assert color == expected
        assert Color.from_hex(encoded) == expected
        assert color.is_transparent == (expected.alpha == 0)
        assert color.with_alpha(255) == Color(
            expected.red, expected.green, expected.blue
        )

    @pytest.mark.parametrize(
        "text", ["red", "#fff", "#gg0000"], ids=["name", "short", "invalid-digit"]
    )
    def test_invalid_hex(self, text: str) -> None:
        """Malformed colors fail with a usable format hint."""
        with pytest.raises(ValueError, match="Expected a color"):
            Color.from_hex(text)

    @pytest.mark.parametrize(
        "channel", [-1, 256, 999], ids=["negative", "overflow", "large"]
    )
    def test_invalid_channel(self, channel: int) -> None:
        """Out-of-range channels are rejected."""
        with pytest.raises(ValueError, match="channel out of range"):
            Color(0, 0, 0, channel)


class TestStyle:
    """Positive stroke and font sizes."""

    @pytest.mark.parametrize(
        ("width", "font_size"),
        [(0, 16), (-1, 16), (3, 0), (3, -1)],
        ids=["zero-width", "negative-width", "zero-font", "negative-font"],
    )
    def test_invalid_sizes(self, width: float, font_size: int) -> None:
        """Non-positive stroke or font sizes are rejected."""
        with pytest.raises(ValueError, match="must be positive"):
            Style(width=width, font_size=font_size)
