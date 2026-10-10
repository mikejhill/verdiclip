"""Rotate, flip, and resize: pixels, crop, and annotations move together."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    CounterMarker,
    FreehandShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.commands import TransformImage
from verdiclip.document.document import Document, DocumentState
from verdiclip.document.history import History
from verdiclip.document.style import Style
from verdiclip.document.transform import (
    MAX_DIMENSION,
    Flip,
    FlipAxis,
    ImageTransform,
    Resize,
    Rotate,
)
from verdiclip.geometry import Point, Rect

BLACK_SQUARE_CENTER = (325, 225)  # In the 400x300 sample image


def pixel(image: QImage, x: float, y: float) -> QColor:
    """Return the color at image pixel (x, y)."""
    return image.pixelColor(round(x), round(y))


def annotations() -> list[Annotation]:
    """One annotation of each geometry family."""
    return [
        RectangleShape(id="box", rect=Rect(10, 20, 100, 50), text="label"),
        ArrowShape(id="arrow", start=Point(0, 0), end=Point(40, 10)),
        FreehandShape(id="pen", points=(Point(5, 5), Point(15, 25))),
        TextNote(id="note", rect=Rect(200, 100, 60, 20), text="hi"),
        CounterMarker(id="one", center=Point(350, 50), radius=12, label="1"),
    ]


def transformed(document: Document, transform: ImageTransform) -> DocumentState:
    """Apply ``transform`` to ``document``'s state."""
    return transform.apply(document.state)


class TestRotate:
    """Quarter turns map every pixel and shape exactly."""

    def test_rotate_right_moves_pixels_and_swaps_size(self, document: Document) -> None:
        """The black square at the bottom-right ends up at the bottom-left."""
        after = transformed(document, Rotate(clockwise=True))

        assert (after.image.width(), after.image.height()) == (300, 400)
        assert pixel(after.image, 75, 325) == QColor("black")
        assert pixel(after.image, 300 - 50, 120) == QColor(30, 30, 30)

    def test_rotate_left_moves_pixels(self, document: Document) -> None:
        """Counter-clockwise: (x, y) goes to (y, width - x)."""
        after = transformed(document, Rotate(clockwise=False))

        x, y = BLACK_SQUARE_CENTER
        assert (after.image.width(), after.image.height()) == (300, 400)
        assert pixel(after.image, y, 400 - x) == QColor("black")

    def test_annotations_and_crop_follow(self, document: Document) -> None:
        """Box corners, line ends, and the crop rotate with the pixels."""
        document.insert(list(enumerate(annotations())))
        document.set_crop(Rect(0, 0, 200, 100))

        after = transformed(document, Rotate(clockwise=True))
        by_id = {a.id: a for a in after.annotations}

        assert after.crop == Rect(200, 0, 100, 200)
        box = by_id["box"]
        assert isinstance(box, RectangleShape)
        assert box.rect == Rect(230, 10, 50, 100)
        assert box.text == "label"
        arrow = by_id["arrow"]
        assert isinstance(arrow, ArrowShape)
        assert (arrow.start, arrow.end) == (Point(300, 0), Point(290, 40))

    def test_text_stays_upright_around_its_mapped_center(
        self, document: Document
    ) -> None:
        """Text keeps its size; only its center moves."""
        note = TextNote(id="note", rect=Rect(200, 100, 60, 20), text="hi")
        document.insert([(0, note)])

        after = transformed(document, Rotate(clockwise=True)).annotations[0]

        assert isinstance(after, TextNote)
        assert (after.rect.width, after.rect.height) == (60, 20)
        assert after.rect.center == Point(300 - 110, 230)

    def test_four_turns_return_to_the_start(self, document: Document) -> None:
        """Rotating right four times is the identity for pixels and shapes."""
        document.insert(list(enumerate(annotations())))
        state = document.state
        for _ in range(4):
            state = Rotate(clockwise=True).apply(state)

        assert state.image == document.image
        assert state.annotations == document.state.annotations
        assert state.crop == document.crop

    def test_rotation_keeps_styles(self, document: Document) -> None:
        """No scaling means stroke widths and fonts are untouched."""
        style = Style(width=5.0, font_size=13)
        document.insert([(0, RectangleShape(rect=Rect(1, 1, 5, 5), style=style))])

        after = transformed(document, Rotate(clockwise=False))

        assert after.annotations[0].style == style


class TestFlip:
    """Mirrors in either direction."""

    @pytest.mark.parametrize(
        ("axis", "expected"),
        [
            (FlipAxis.HORIZONTAL, (400 - 325, 225)),
            (FlipAxis.VERTICAL, (325, 300 - 225)),
        ],
    )
    def test_flip_mirrors_pixels(
        self, document: Document, axis: FlipAxis, expected: tuple[int, int]
    ) -> None:
        """The black square lands on the mirrored side."""
        after = transformed(document, Flip(axis))

        assert (after.image.width(), after.image.height()) == (400, 300)
        assert pixel(after.image, *expected) == QColor("black")

    def test_flip_twice_is_identity(self, document: Document) -> None:
        """Flipping the same way twice restores everything."""
        document.insert(list(enumerate(annotations())))
        document.set_crop(Rect(10, 20, 100, 80))
        flip = Flip(FlipAxis.HORIZONTAL)

        state = flip.apply(flip.apply(document.state))

        assert state.image == document.image
        assert state.annotations == document.state.annotations
        assert state.crop == document.crop

    def test_arrow_head_follows_the_mirror(self, document: Document) -> None:
        """The head stays at the arrow's end, now on the other side."""
        document.insert([(0, ArrowShape(start=Point(0, 0), end=Point(40, 10)))])

        arrow = transformed(document, Flip(FlipAxis.HORIZONTAL)).annotations[0]

        assert isinstance(arrow, ArrowShape)
        assert (arrow.start, arrow.end) == (Point(400, 0), Point(360, 10))

    @pytest.mark.parametrize(
        ("transform", "description"),
        [
            (Rotate(clockwise=True), "Rotate right"),
            (Rotate(clockwise=False), "Rotate left"),
            (Flip(FlipAxis.HORIZONTAL), "Flip horizontally"),
            (Flip(FlipAxis.VERTICAL), "Flip vertically"),
            (Resize(10, 10), "Resize"),
        ],
    )
    def test_descriptions(self, transform: ImageTransform, description: str) -> None:
        """Undo menu entries read naturally."""
        assert transform.description == description


class TestResize:
    """Scaling what the user sees to an exact size."""

    def test_resize_scales_the_visible_area_to_the_exact_size(
        self, document: Document
    ) -> None:
        """Half size: 400x300 becomes 200x150 and the square moves to half."""
        after = transformed(document, Resize(200, 150))

        assert (after.image.width(), after.image.height()) == (200, 150)
        assert after.crop == Rect(0, 0, 200, 150)
        assert pixel(after.image, 325 / 2, 225 / 2) == QColor("black")

    def test_resize_bakes_the_crop(self, document: Document) -> None:
        """Only the cropped area is kept; annotations shift and scale with it."""
        document.set_crop(Rect(300, 200, 50, 50))
        document.insert([(0, RectangleShape(id="box", rect=Rect(310, 210, 10, 10)))])

        after = transformed(document, Resize(100, 100))
        box = after.annotations[0]

        assert (after.image.width(), after.image.height()) == (100, 100)
        assert pixel(after.image, 50, 50) == QColor("black")
        assert isinstance(box, RectangleShape)
        assert box.rect == Rect(20, 20, 20, 20)

    def test_uniform_resize_scales_strokes_fonts_and_counters(
        self, document: Document
    ) -> None:
        """Doubling the size doubles widths, fonts, and counter radii."""
        style = Style(width=3.0, font_size=16)
        document.insert(
            [
                (0, RectangleShape(id="box", rect=Rect(0, 0, 10, 10), style=style)),
                (1, CounterMarker(id="c", center=Point(10, 10), radius=12, label="1")),
                (2, TextNote(id="t", rect=Rect(100, 100, 40, 20), text="x")),
            ]
        )

        after = {a.id: a for a in transformed(document, Resize(800, 600)).annotations}

        assert after["box"].style.width == 6.0
        assert after["box"].style.font_size == 32
        counter = after["c"]
        assert isinstance(counter, CounterMarker)
        assert (counter.center, counter.radius) == (Point(20, 20), 24)
        note = after["t"]
        assert isinstance(note, TextNote)
        assert note.rect == Rect(200, 200, 80, 40)

    def test_obfuscation_block_size_scales(self, document: Document) -> None:
        """Pixelation stays as coarse relative to the image."""
        shape = ObfuscateShape(rect=Rect(0, 0, 40, 40), style=Style(width=10))
        document.insert([(0, shape)])

        after = transformed(document, Resize(200, 150)).annotations[0]

        assert isinstance(after, ObfuscateShape)
        assert after.block_size == 5

    @pytest.mark.parametrize("size", [(0, 10), (10, 0), (MAX_DIMENSION + 1, 10)])
    def test_rejects_impossible_sizes(self, size: tuple[int, int]) -> None:
        """Zero, negative, or huge sizes are refused up front."""
        with pytest.raises(ValueError, match="Size must be"):
            Resize(*size)


class TestTransformCommand:
    """Whole-image changes are single, exact undo steps."""

    def test_undo_and_redo_restore_exact_states(self, document: Document) -> None:
        """Undo returns the original pixels, crop, and shapes; redo reapplies."""
        document.insert(list(enumerate(annotations())))
        document.set_crop(Rect(10, 10, 300, 200))
        before = document.state
        history = History(document)

        history.execute(TransformImage(Resize(150, 100)))
        after = document.state
        history.undo()
        undone = document.state
        history.redo()

        assert history.undo_text == "Resize"
        assert (undone.image, undone.crop, undone.annotations) == (
            before.image,
            before.crop,
            before.annotations,
        )
        assert document.state.image == after.image
        assert document.state.annotations == after.annotations

    def test_transforms_never_merge(self, document: Document) -> None:
        """Two rotations are two undo steps."""
        history = History(document)

        history.execute(TransformImage(Rotate(clockwise=True)))
        history.execute(TransformImage(Rotate(clockwise=True)))
        history.undo()

        assert (document.image.width(), document.image.height()) == (300, 400)

    def test_revert_without_apply_is_harmless(self, document: Document) -> None:
        """A command that never ran leaves the document alone."""
        TransformImage(Rotate(clockwise=True)).revert(document)

        assert document.image.width() == 400
