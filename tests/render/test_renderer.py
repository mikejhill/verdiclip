"""Test flattened annotation pixels and text measurements."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    BoxAnnotation,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    HighlightShape,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.document import Document
from verdiclip.document.style import HIGHLIGHT_YELLOW, RED, Color, Style
from verdiclip.geometry import Point, Rect
from verdiclip.render.renderer import QtConvert, Renderer


class TestQtConvert:
    """Model and Qt value conversion."""

    def test_values(self, qapp: QApplication) -> None:
        """Conversions retain coordinates, color channels, and font properties."""
        assert qapp is not None
        style = Style(font_family="Arial", font_size=24, bold=True, italic=True)

        font = QtConvert.font(style)

        assert font.pixelSize() == 24
        assert font.bold()
        assert font.italic()
        assert QtConvert.color(Color(1, 2, 3, 4)) == QColor(1, 2, 3, 4)
        assert QtConvert.to_point(QPointF(1, 2)) == Point(1, 2)
        assert QtConvert.point(Point(1, 2)) == QPointF(1, 2)
        assert QtConvert.rect(Rect(1, 2, 3, 4)).width() == 3


class TestRenderer:
    """Pixel-level export behavior shared with the canvas."""

    def test_rectangle(self, qapp: QApplication, document: Document) -> None:
        """An unfilled rectangle paints its stroke while preserving its interior."""
        assert qapp is not None
        document.insert(
            [(0, RectangleShape(rect=Rect(50, 100, 100, 80), style=Style(width=6)))]
        )

        image = Renderer().flatten(document)

        assert image.pixelColor(50, 140) == QtConvert.color(RED)
        assert image.pixelColor(100, 140) == QColor("white")
        assert document.image.pixelColor(50, 140) == QColor("white")

    @pytest.mark.parametrize(
        "length", [8, 50, 100], ids=["short-head-only", "medium", "long"]
    )
    def test_arrow(self, qapp: QApplication, document: Document, length: int) -> None:
        """Arrow tips taper to a point without a shaft beyond the endpoint."""
        assert qapp is not None
        tip = 100 + length
        document.insert(
            [
                (
                    0,
                    ArrowShape(
                        start=Point(100, 120), end=Point(tip, 120), style=Style(width=6)
                    ),
                )
            ]
        )

        image = Renderer().flatten(document)

        assert image.pixelColor(tip - 2, 120) != QColor("white")
        assert image.pixelColor(tip + 1, 120) == QColor("white")
        assert image.pixelColor(tip - 1, 124) == QColor("white")

    def test_highlight(self, qapp: QApplication, document: Document) -> None:
        """Multiplication makes white yellow while keeping dark text dark."""
        assert qapp is not None
        document.insert(
            [
                (
                    0,
                    HighlightShape(
                        rect=Rect(20, 20, 200, 60), style=Style(fill=HIGHLIGHT_YELLOW)
                    ),
                )
            ]
        )

        image = Renderer().flatten(document)

        white = image.pixelColor(25, 25)
        dark = image.pixelColor(50, 50)
        assert white.red() == 255
        assert white.blue() < white.green() < 255
        assert max(dark.red(), dark.green(), dark.blue()) <= 30
        assert dark.blue() < dark.red()

    def test_obfuscation(self, qapp: QApplication) -> None:
        """A boundary inside one pixelation block becomes an averaged uniform color."""
        assert qapp is not None
        base = QImage(20, 20, QImage.Format.Format_RGB32)
        base.fill(QColor("white"))
        for y in range(20):
            for x in range(10):
                base.setPixelColor(x, y, QColor("black"))
        document = Document(base)
        document.insert(
            [(0, ObfuscateShape(rect=Rect(0, 0, 20, 20), style=Style(width=20)))]
        )
        renderer = Renderer()

        image = renderer.flatten(document)
        repeated = renderer.flatten(document)

        colors = {image.pixelColor(x, y).rgba() for y in range(20) for x in range(20)}
        assert len(colors) == 1
        assert QColor("black").rgba() not in colors
        assert QColor("white").rgba() not in colors
        assert repeated == image

    def test_pixelation_eviction(self, qapp: QApplication, document: Document) -> None:
        """Many pixelated regions remain stable after cache eviction."""
        assert qapp is not None
        shapes = [ObfuscateShape(rect=Rect(i, 100, 10, 10)) for i in range(65)]
        document.insert(list(enumerate(shapes)))
        renderer = Renderer()

        first = renderer.flatten(document)
        second = renderer.flatten(document)

        assert first == second
        assert first.pixelColor(1, 105) == QColor("white")

    def test_crop(self, qapp: QApplication, document: Document) -> None:
        """Cropping changes output size and offsets both pixels and annotations."""
        assert qapp is not None
        document.insert(
            [(0, RectangleShape(rect=Rect(50, 80, 50, 50), style=Style(fill=RED)))]
        )
        document.set_crop(Rect(40, 40, 100, 100))

        image = Renderer().flatten(document)

        assert (image.width(), image.height()) == (100, 100)
        assert image.pixelColor(5, 5) == QColor(30, 30, 30)
        assert image.pixelColor(20, 50) == QtConvert.color(RED)

    @pytest.mark.parametrize(
        "annotation",
        [
            EllipseShape(rect=Rect(50, 100, 80, 60), style=Style(fill=RED)),
            LineShape(start=Point(50, 120), end=Point(100, 120)),
            FreehandShape(points=(Point(50, 100),)),
            FreehandShape(points=(Point(50, 100), Point(80, 130))),
            FreehandShape(points=(Point(50, 100), Point(80, 130), Point(110, 110))),
            TextNote(rect=Rect(50, 100, 100, 50), text="Note"),
            TextNote(rect=Rect(50, 100, 100, 50), text="Note", style=Style(fill=RED)),
            CounterMarker(
                center=Point(100, 140), radius=20, label="1", style=Style(fill=RED)
            ),
            CounterMarker(
                center=Point(100, 140), radius=20, label="ABC", style=Style(fill=RED)
            ),
        ],
        ids=[
            "ellipse",
            "line",
            "freehand-dot",
            "freehand-line",
            "freehand-curve",
            "text",
            "filled-text",
            "counter",
            "long-counter",
        ],
    )
    def test_visible_pixels(
        self, qapp: QApplication, document: Document, annotation: Annotation
    ) -> None:
        """Each annotation paints non-white pixels within its visual bounds."""
        assert qapp is not None
        document.insert([(0, annotation)])

        image = Renderer().flatten(document)

        bounds = annotation.bounds.rounded()
        assert any(
            image.pixelColor(x, y) != QColor("white")
            for y in range(int(bounds.top), int(bounds.bottom))
            for x in range(int(bounds.left), int(bounds.right))
        )

    def test_measure_text(self, qapp: QApplication) -> None:
        """Text width grows with length and height grows with line count."""
        assert qapp is not None
        style = Style()

        short = Renderer.measure_text("a", style)
        long = Renderer.measure_text("aaaa", style)
        multiline = Renderer.measure_text("a\na", style)
        empty = Renderer.measure_text("", style)

        assert long[0] > short[0] > empty[0] > 0
        assert multiline[1] > short[1] == long[1]

    def test_outside_obfuscation(
        self, qapp: QApplication, sample_image: QImage
    ) -> None:
        """Painting pixelation outside the base image leaves the target unchanged."""
        assert qapp is not None
        result = sample_image.copy()
        painter = QPainter(result)
        shape = ObfuscateShape(rect=Rect(500, 500, 10, 10))

        try:
            Renderer().paint(painter, sample_image, shape)
        finally:
            painter.end()

        assert result == sample_image

    def test_unknown_shape(
        self, qapp: QApplication, document: Document, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unsupported annotation subclasses are logged and preserve base pixels."""
        assert qapp is not None
        document.insert([(0, BoxAnnotation(rect=Rect(20, 100, 50, 50)))])

        image = Renderer().flatten(document)

        assert image == document.image
        assert "No painter for BoxAnnotation" in caplog.text
