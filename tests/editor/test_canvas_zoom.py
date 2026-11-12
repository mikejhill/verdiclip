"""Image scaling at each zoom: sharp at whole scales, smooth at fractional ones."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from verdiclip.document.document import Document, DocumentState
from verdiclip.editor.canvas import PIXEL_GRID_SCALE, smooth_pixmap_scale
from verdiclip.editor.factory import create_editor_window
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings

SIZE = 96


@pytest.mark.parametrize(
    ("scale", "smooth"),
    [
        (0.5, True),
        (1.0, False),
        (1.25, True),
        (1.3, True),
        (1.5, True),
        (2.0, False),
        (2.0004, False),
        (2.5, True),
        (PIXEL_GRID_SCALE - 0.01, True),
        (PIXEL_GRID_SCALE, False),
        (3.5, False),
        (16.0, False),
    ],
)
def test_smooth_only_at_fractional_scales_below_the_pixel_grid(
    scale: float, *, smooth: bool
) -> None:
    """Hard edges at whole scales and from the pixel-grid level up."""
    assert smooth_pixmap_scale(scale) is smooth


def _stripes(period: int = 2) -> QImage:
    """One-pixel black columns on white: the worst case for resampling."""
    image = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
    for x in range(SIZE):
        color = QColor("black") if x % period == period - 1 else QColor("white")
        for y in range(SIZE):
            image.setPixelColor(x, y, color)
    return image


def _open(qtbot: QtBot, image: QImage) -> EditorWindow:
    session = EditorSession(Document(image), EditorSettings())
    host = create_editor_window(session, ImageDelivery(OutputSettings()))
    qtbot.addWidget(host)
    host.resize(900, 700)
    host.show()
    qtbot.waitExposed(host)
    return host


@pytest.fixture
def window(qapp: QApplication, qtbot: QtBot) -> EditorWindow:
    """Return a shown editor holding one-pixel stripes."""
    del qapp
    return _open(qtbot, _stripes())


def _image_row(window: EditorWindow, zoom: float) -> list[int]:
    """Return grey levels across one row of the image as shown on screen."""
    canvas = window.canvas
    canvas.set_zoom(zoom)
    canvas.horizontalScrollBar().setValue(0)
    canvas.verticalScrollBar().setValue(0)
    shot = canvas.viewport().grab().toImage()
    ratio = shot.devicePixelRatio()
    origin = canvas.origin()
    left = round(origin.x() * ratio) + 2
    y = round((origin.y() + SIZE * zoom / 2) * ratio)
    width = int(SIZE * zoom * ratio) - 4
    return [QColor(shot.pixel(QPoint(left + i, y))).lightness() for i in range(width)]


def _mid_tones(row: list[int]) -> int:
    return sum(1 for level in row if 40 < level < 215)


def test_fractional_zoom_blends_instead_of_doubling_some_pixels(
    window: EditorWindow,
) -> None:
    """At 130% the stripes blend into grey instead of uneven hard columns."""
    row = _image_row(window, 1.3)
    assert _mid_tones(row) > len(row) // 4


@pytest.mark.parametrize("zoom", [1.0, 2.0, 4.0, 3.3])
def test_whole_and_large_zooms_keep_hard_pixel_edges(
    window: EditorWindow, zoom: float
) -> None:
    """Whole and inspection-level zooms show only pure black and white."""
    if window.canvas.viewport().devicePixelRatioF() != 1.0:
        pytest.skip("whole zoom levels are only whole screen scales at 100% DPI")
    assert _mid_tones(_image_row(window, zoom)) == 0


def test_zooming_out_averages_detail_instead_of_skipping_it(
    qapp: QApplication, qtbot: QtBot
) -> None:
    """Zoomed out, thin lines fade evenly instead of vanishing or doubling."""
    del qapp
    # Every fourth column is a thin line: each shown pixel should average one
    # line and three white columns, not land on a line or miss them all
    row = _image_row(_open(qtbot, _stripes(period=4)), 0.3)
    assert row
    assert max(row) - min(row) < 80


def test_reduced_copy_is_rebuilt_after_the_image_changes(
    window: EditorWindow,
) -> None:
    """Replacing the pixels drops the cached zoomed-out copy."""
    _image_row(window, 0.25)
    white = QImage(SIZE * 2, SIZE * 2, QImage.Format.Format_RGB32)
    white.fill(QColor("white"))
    document = window.session.document
    document.restore(DocumentState(white, Rect(0, 0, SIZE * 2, SIZE * 2), ()))
    row = _image_row(window, 0.25)
    assert all(level > 245 for level in row)
