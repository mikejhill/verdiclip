"""Test flattened annotation pixels and text measurements."""

from __future__ import annotations

from dataclasses import replace

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
from verdiclip.document.style import (
    HIGHLIGHT_YELLOW,
    RED,
    Color,
    HAlign,
    Style,
    VAlign,
)
from verdiclip.geometry import Point, Rect
from verdiclip.render.renderer import TEXT_PADDING, QtConvert, Renderer


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


class TestTextBoxesAndShadows:
    """Outlines, alignment, fitting, and drop shadows."""

    @staticmethod
    def _dark_columns(image: QImage, rect: Rect, color: QColor) -> list[int]:
        """Return the x positions in ``rect`` holding pixels near ``color``."""
        found = []
        for x in range(int(rect.left), int(rect.right)):
            for y in range(int(rect.top), int(rect.bottom)):
                pixel = image.pixelColor(x, y)
                distance = abs(pixel.red() - color.red()) + abs(
                    pixel.green() - color.green()
                )
                if distance < 60:
                    found.append(x)
                    break
        return found

    def test_outline_and_fill(self, qapp: QApplication, document: Document) -> None:
        """An outlined note paints its border color and its background."""
        assert qapp is not None
        style = Style(fill=Color(0, 0, 255), outline=Color(0, 255, 0), width=6)
        document.insert(
            [(0, TextNote(rect=Rect(40, 120, 120, 60), text="", style=style))]
        )

        image = Renderer().flatten(document)

        assert image.pixelColor(40, 150) == QColor(0, 255, 0)
        assert image.pixelColor(100, 150) == QColor(0, 0, 255)

    @pytest.mark.parametrize(
        ("h_align", "v_align", "side"),
        [
            (HAlign.LEFT, VAlign.TOP, "left"),
            (HAlign.RIGHT, VAlign.BOTTOM, "right"),
            (HAlign.CENTER, VAlign.MIDDLE, "center"),
        ],
    )
    def test_alignment(
        self,
        qapp: QApplication,
        document: Document,
        h_align: HAlign,
        v_align: VAlign,
        side: str,
    ) -> None:
        """Fixed-size text sits where its alignment puts it."""
        assert qapp is not None
        box = Rect(20, 120, 360, 120)
        style = Style(fill=Color(255, 255, 255), h_align=h_align, v_align=v_align)
        note = TextNote(rect=box, text="ab", style=style, auto_fit=False)
        document.insert([(0, note)])

        image = Renderer().flatten(document)

        columns = self._dark_columns(image, box, QtConvert.color(RED))
        assert columns
        middle = (columns[0] + columns[-1]) / 2
        expected = {"left": 40, "center": 200, "right": 360}[side]
        assert abs(middle - expected) < 30

    def test_fixed_text_wraps_and_clips(
        self, qapp: QApplication, document: Document
    ) -> None:
        """Long text in a fixed box never paints outside the box."""
        assert qapp is not None
        box = Rect(100, 120, 60, 30)
        note = TextNote(rect=box, text="word " * 40, auto_fit=False)
        document.insert([(0, note)])

        image = Renderer().flatten(document)

        outside = Rect(100, 152, 60, 40)
        assert not self._dark_columns(image, outside, QtConvert.color(RED))

    def test_fit(self, qapp: QApplication) -> None:
        """Fitting sizes auto-fit text, leaves fixed text and other shapes alone."""
        assert qapp is not None
        style = Style(outline=Color(0, 0, 0), width=8)
        note = TextNote(rect=Rect(5, 5, 1, 1), text="Hi", style=style)
        fixed = TextNote(rect=Rect(5, 5, 1, 1), text="Hi", auto_fit=False)
        shape = RectangleShape(rect=Rect(0, 0, 1, 1))

        fitted = Renderer.fit(note)

        assert isinstance(fitted, TextNote)
        assert (fitted.rect.width, fitted.rect.height) == pytest.approx(
            Renderer.measure_text("Hi", style)
        )
        assert Renderer.fit(fixed) is fixed
        assert Renderer.fit(shape) is shape
        assert Renderer.text_inset(style) == TEXT_PADDING + 4
        assert Renderer.text_inset(Style(width=8)) == TEXT_PADDING

    @pytest.mark.parametrize(
        "annotation",
        [
            RectangleShape(rect=Rect(60, 120, 100, 60), style=Style(shadow=True)),
            RectangleShape(
                rect=Rect(60, 120, 100, 60),
                style=Style(shadow=True, fill=Color(0, 0, 255)),
            ),
            TextNote(
                rect=Rect(60, 120, 100, 60),
                text="",
                style=Style(shadow=True, fill=Color(0, 0, 255)),
            ),
            TextNote(
                rect=Rect(60, 120, 100, 60),
                text="",
                style=Style(shadow=True, outline=Color(0, 0, 255)),
            ),
        ],
        ids=["hollow-rectangle", "filled-rectangle", "filled-text", "outlined-text"],
    )
    def test_shadow_below_right(
        self, qapp: QApplication, document: Document, annotation: Annotation
    ) -> None:
        """A shadow darkens the image just past the bottom-right edges only."""
        assert qapp is not None
        plain = Document(document.image)
        plain.insert(
            [(0, replace(annotation, style=replace(annotation.style, shadow=False)))]
        )
        document.insert([(0, annotation)])

        shadowed = Renderer().flatten(document)
        flat = Renderer().flatten(plain)

        below = (110, 186)
        above = (110, 114)
        assert (
            shadowed.pixelColor(*below).lightness()
            < flat.pixelColor(*below).lightness()
        )
        assert shadowed.pixelColor(*above) == flat.pixelColor(*above)

    def test_caption_shadow(self, qapp: QApplication, document: Document) -> None:
        """Text with no box casts a shadow of its letters."""
        assert qapp is not None
        style = Style(shadow=True, font_size=40)
        note = Renderer.fit(TextNote(rect=Rect(40, 120, 1, 1), text="WW", style=style))
        plain = Document(document.image)
        plain.insert([(0, replace(note, style=replace(style, shadow=False)))])
        document.insert([(0, note)])

        shadowed = Renderer().flatten(document)
        flat = Renderer().flatten(plain)

        bounds = note.bounds.adjusted(4).rounded()
        assert any(
            shadowed.pixelColor(x, y) != flat.pixelColor(x, y)
            for y in range(int(bounds.top), int(bounds.bottom))
            for x in range(int(bounds.left), int(bounds.right))
        )

    def test_long_words_wrap_like_the_editor(
        self, qapp: QApplication, document: Document
    ) -> None:
        """A word wider than a fixed box breaks across lines instead of clipping."""
        assert qapp is not None
        box = Rect(100, 100, 80, 150)
        note = TextNote(rect=box, text="abcdefghijklmnopqrstuvwxyz", auto_fit=False)
        document.insert([(0, note)])

        image = Renderer().flatten(document)

        rows = [
            y
            for y in range(int(box.top), int(box.bottom))
            if self._dark_columns(
                image, Rect(box.x, y, box.width, 1), QtConvert.color(RED)
            )
        ]
        assert rows[-1] - rows[0] > 3 * note.style.font_size

    def test_crop_keeps_a_visible_shadow(
        self, qapp: QApplication, document: Document
    ) -> None:
        """A crop showing only an annotation's shadow still exports that shadow."""
        assert qapp is not None
        shape = RectangleShape(rect=Rect(20, 20, 40, 40), style=Style(shadow=True))
        document.insert([(0, shape)])
        document.set_crop(Rect(64, 30, 20, 20))

        image = Renderer().flatten(document)

        assert document.visible_annotations == (shape,)
        assert image.pixelColor(0, 10) != QColor("white")

    @pytest.mark.parametrize("kind", ["rectangle", "text"])
    def test_thick_outline_still_casts_a_shadow(
        self, qapp: QApplication, document: Document, kind: str
    ) -> None:
        """The shadow follows the outer edge of a thick outline, not just the fill."""
        assert qapp is not None
        fill = Color(0, 0, 255)
        rect = Rect(60, 120, 100, 60)
        annotation: Annotation = (
            RectangleShape(rect=rect, style=Style(width=20, fill=fill, shadow=True))
            if kind == "rectangle"
            else TextNote(
                rect=rect,
                text="",
                style=Style(width=20, fill=fill, outline=RED, shadow=True),
            )
        )
        plain = Document(document.image)
        plain.insert(
            [(0, replace(annotation, style=replace(annotation.style, shadow=False)))]
        )
        document.insert([(0, annotation)])

        shadowed = Renderer().flatten(document)
        flat = Renderer().flatten(plain)

        past_outline = (110, 193)
        assert (
            shadowed.pixelColor(*past_outline).lightness()
            < flat.pixelColor(*past_outline).lightness()
        )

    @pytest.mark.parametrize("kind", ["text", "label"])
    def test_box_clipping_keeps_the_callers_clip(
        self, qapp: QApplication, kind: str
    ) -> None:
        """Clipping text to its box never paints outside the canvas's crop clip."""
        assert qapp is not None
        image = QImage(200, 100, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        rect = Rect(0, 0, 200, 100)
        style = Style(font_size=40, h_align=HAlign.RIGHT)
        annotation: Annotation = (
            TextNote(rect=rect, text="WWW", style=style, auto_fit=False)
            if kind == "text"
            else RectangleShape(rect=rect, text="WWWWWW", style=style)
        )
        painter = QPainter(image)
        painter.setClipRect(0, 0, 100, 100)
        Renderer().paint(painter, image, annotation)
        painter.end()

        assert not self._dark_columns(
            image, Rect(110, 0, 80, 100), QtConvert.color(RED)
        )
