"""Hosted Qt raster contracts for fixed symbols, not physical-display DPI proof."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPainter
from PySide6.QtWidgets import QApplication

from verdiclip.editor.icons import SIZE, IconFactory
from verdiclip.editor.session import ToolId


def test_factory_glyphs_do_not_resolve_text_fonts(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even the fixed T/1 symbols must avoid explicit painter font/text calls."""
    del qapp  # The fixture owns the required application; do not create another.

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Toolbar glyph rendering attempted a font/text operation")

    monkeypatch.setattr(QPainter, "setFont", forbidden)
    monkeypatch.setattr(QPainter, "drawText", forbidden)
    factory = IconFactory(QColor("#202020"))
    for tool in ToolId:
        assert not factory.tool(tool).isNull()
    for action in ("copy_image", "save", "undo", "redo", "settings", "image"):
        assert not factory.action(action).isNull()
    for style in (
        "align_left",
        "align_center",
        "align_right",
        "align_top",
        "align_middle",
        "align_bottom",
        "shadow",
        "auto_fit",
    ):
        assert not factory.style(style).isNull()


def glyph_icon(factory: IconFactory, glyph: str) -> QIcon:
    """Use the same public factory entry points as the actual toolbars."""
    if glyph == "auto_fit":
        return factory.style(glyph)
    return factory.tool(ToolId(glyph))


def has_color(image: QImage, region: tuple[int, int, int, int], color: QColor) -> bool:
    """Require visible interior ink in a semantic region, tolerating edge AA."""
    left, top, right, bottom = region
    for y in range(
        math.floor(top * image.height() / SIZE),
        math.ceil(bottom * image.height() / SIZE),
    ):
        for x in range(
            math.floor(left * image.width() / SIZE),
            math.ceil(right * image.width() / SIZE),
        ):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() >= 180 and all(
                abs(actual - expected) <= 24
                for actual, expected in zip(
                    (pixel.red(), pixel.green(), pixel.blue()),
                    (color.red(), color.green(), color.blue()),
                    strict=True,
                )
            ):
                return True
    return False


@pytest.mark.parametrize("glyph", ["text", "counter", "auto_fit"])
@pytest.mark.parametrize("size", [16, 18])
@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("tint", ["#202020", "#eeeeee", "#2579ad"])
def test_fixed_glyphs_keep_shape_and_contrast_at_requested_raster_scales(
    qapp: QApplication, glyph: str, size: int, dpr: float, tint: str
) -> None:
    """Simulated raster requests preserve legibility; no monitor is configured."""
    del qapp
    ink = QColor(tint)
    image = glyph_icon(IconFactory(ink), glyph).pixmap(QSize(size, size), dpr).toImage()
    assert not image.isNull()
    assert abs(image.width() - size * dpr) <= 1  # Allow fractional-pixel rounding.
    assert image.height() == image.width()
    # The glyph must remain inset rather than clipping at any canvas edge.
    assert all(
        image.pixelColor(x, y).alpha() == 0
        for x, y in (
            *((x, 0) for x in range(image.width())),
            *((x, image.height() - 1) for x in range(image.width())),
            *((0, y) for y in range(image.height())),
            *((image.width() - 1, y) for y in range(image.height())),
        )
    )
    if glyph == "counter":
        paper = QColor(30, 30, 30) if ink.lightness() > 128 else QColor(255, 255, 255)
        assert has_color(image, (22, 7, 26, 12), ink)  # Circle's upper rim.
        assert has_color(image, (11, 21, 17, 28), ink)  # Disc beside the numeral.
        assert has_color(image, (23, 21, 27, 29), paper)  # Contrasting stem.
        assert has_color(image, (19, 30, 30, 34), paper)  # Readable numeral foot.
    elif glyph == "text":
        assert has_color(image, (13, 11, 35, 17), ink)  # T cap.
        assert has_color(image, (21, 20, 27, 37), ink)  # T stem.
        assert image.pixelColor(image.width() // 4, image.height() // 2).alpha() == 0
    else:
        assert has_color(image, (17, 15, 31, 20), ink)  # Smaller T cap.
        assert has_color(image, (22, 21, 26, 33), ink)  # Smaller T stem.
        for corner in (
            (5, 5, 12, 12),
            (36, 5, 43, 12),
            (5, 36, 12, 43),
            (36, 36, 43, 43),
        ):
            assert has_color(image, corner, ink)
