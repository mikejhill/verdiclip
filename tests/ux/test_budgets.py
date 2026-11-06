"""Latency budgets from docs/design/philosophy.md §6, measured on real widgets."""

from __future__ import annotations

import gc
import json
import statistics
import sys
import threading
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import qVersion
from PySide6.QtGui import QColor, QGuiApplication, QImage
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


def gui_cpu_time() -> float | None:
    """Keep unavailable diagnostic CPU clocks from changing the operation."""
    try:
        return time.thread_time()
    except (AttributeError, OSError):
        return None


def cpu_elapsed_ms(start: float | None, end: float | None) -> float | None:
    """Report an unavailable phase rather than inventing a CPU duration."""
    return None if start is None or end is None else (end - start) * 1000


@pytest.fixture(autouse=True)
def warm_and_settled(open_editor: Callable[[QImage], User]) -> None:
    """Pay one-time costs first, so a budget never depends on test order.

    The first editor in a process loads Qt's fonts and styles (seconds on
    Windows), and earlier tests' leftover images can be collected mid-timing.
    A running VerdiClip has paid the first already; the budgets are per
    capture, so a small editor opens and closes before each, then garbage
    is collected.
    """
    warm = open_editor(QImage(64, 48, QImage.Format.Format_RGB32))
    warm.window.close()
    gc.collect()


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
        self,
        uhd_image: QImage,
        open_editor: Callable[[QImage], User],
        request: pytest.FixtureRequest,
        record_property: Callable[[str, object], None],
    ) -> None:
        """Copying an annotated 4K image to the clipboard takes under 250 ms."""
        user = open_editor(uhd_image)
        user.window.session.history.execute(AddAnnotations(fifty_annotations(), "Fill"))
        delivery = ImageDelivery(OutputSettings())

        clock = Stopwatch()
        started_cpu = gui_cpu_time()
        flattened = user.window.flattened()
        flattened_ms = clock.elapsed_ms
        flattened_cpu = gui_cpu_time()
        delivery.copy(flattened)
        del flattened  # Match the original expression's temporary-image lifetime.
        copied_ms = clock.elapsed_ms
        copied_cpu = gui_cpu_time()
        threads = threading.enumerate()
        seed = request.config.getoption("randomly_seed", default=None)
        receipt = {
            "python": ".".join(str(part) for part in sys.version_info[:3]),
            "qt": qVersion(),
            "os": sys.platform,
            "qpa": QGuiApplication.platformName(),
            "seed": seed if type(seed) is int else None,
            "python_threads": len(threads),
            "capture_storage_workers": sum(
                thread.name == "VerdiClip capture storage" for thread in threads
            ),
            "flatten_wall_ms": flattened_ms,
            "flatten_gui_cpu_ms": cpu_elapsed_ms(started_cpu, flattened_cpu),
            "copy_wall_ms": copied_ms - flattened_ms,
            "copy_gui_cpu_ms": cpu_elapsed_ms(flattened_cpu, copied_cpu),
        }
        # Keep every timing sample and metadata operation inside the total;
        # report serialization happens afterwards and never changes the result.
        elapsed_ms = clock.elapsed_ms
        receipt["total_wall_ms"] = elapsed_ms
        record_property("flatten_copy_budget", json.dumps(receipt, sort_keys=True))

        assert elapsed_ms < 250, receipt

    def test_open_editor_on_4k_under_300ms(
        self, open_editor: Callable[[QImage], User], uhd_image: QImage
    ) -> None:
        """UX-CAP-11: a 4K capture's editor is visible within 300 ms."""
        clock = Stopwatch()

        user = open_editor(uhd_image)

        assert user.window.isVisible()
        assert clock.elapsed_ms < 300
