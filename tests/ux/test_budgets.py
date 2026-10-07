"""Latency budgets from docs/design/philosophy.md §6, measured on real widgets."""

from __future__ import annotations

import statistics
from collections.abc import Callable

import pytest
from PySide6.QtGui import QColor, QImage
from tests.ux.conftest import Stopwatch, User

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    CounterMarker,
    HighlightShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.commands import AddAnnotations
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import OutputSettings

pytestmark = [pytest.mark.ux, pytest.mark.ux_budget]

UHD = (3840, 2160)


@pytest.fixture
def uhd_image() -> QImage:
    """A 4K screenshot-sized image with some structure."""
    image = QImage(*UHD, QImage.Format.Format_RGB32)
    image.fill(QColor(240, 240, 240))
    for y in range(0, UHD[1], 40):
        image.setPixelColor(10, y, QColor("black"))
    return image


def fifty_annotations() -> list[Annotation]:
    """A heavy mix: shapes, arrows, text, counters, highlights, obfuscations."""
    made: list[Annotation] = []
    for i in range(10):
        x, y = 100 + i * 300, 100 + i * 150
        made += [
            RectangleShape(rect=Rect(x, y, 250, 120)),
            ArrowShape(start=Point(x, y + 300), end=Point(x + 200, y + 200)),
            TextNote(rect=Rect(x, y + 400, 220, 40), text=f"Step {i}"),
            CounterMarker(center=Point(x + 50, y + 600), radius=18, label=str(i)),
            ObfuscateShape(rect=Rect(x, y + 700, 200, 60))
            if i % 2
            else HighlightShape(rect=Rect(x, y + 700, 200, 60)),
        ]
    return made


class TestBudgets:
    """Each interaction stays within its budget."""

    def test_paint_with_fifty_annotations_on_4k_under_16ms(
        self, open_editor: Callable[[QImage], User], uhd_image: QImage
    ) -> None:
        """A full repaint while drawing stays under one 60 Hz frame (16 ms)."""
        user = open_editor(uhd_image)
        session = user.window.session
        session.history.execute(AddAnnotations(fifty_annotations(), "Fill"))
        viewport = user.window.canvas.viewport()
        user.shortcut("Ctrl+0")  # Drawing happens at 100%, the costly case
        viewport.repaint()  # Warm caches (pixelation, glyphs)

        timings = []
        for _ in range(10):
            clock = Stopwatch()
            viewport.repaint()
            timings.append(clock.elapsed_ms)

        assert statistics.median(timings) < 16, timings

    def test_flatten_and_copy_4k_under_250ms(
        self, uhd_image: QImage, open_editor: Callable[[QImage], User]
    ) -> None:
        """Copying an annotated 4K image to the clipboard takes under 250 ms."""
        user = open_editor(uhd_image)
        user.window.session.history.execute(AddAnnotations(fifty_annotations(), "Fill"))
        delivery = ImageDelivery(OutputSettings())

        clock = Stopwatch()
        delivery.copy(user.window.flattened())

        assert clock.elapsed_ms < 250

    def test_open_editor_on_4k_under_300ms(
        self, open_editor: Callable[[QImage], User], uhd_image: QImage
    ) -> None:
        """UX-CAP-11: a 4K capture's editor is visible within 300 ms."""
        clock = Stopwatch()

        user = open_editor(uhd_image)

        assert user.window.isVisible()
        assert clock.elapsed_ms < 300
