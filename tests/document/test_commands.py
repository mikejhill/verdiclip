"""Test reversible commands and merge boundaries."""

from __future__ import annotations

import pytest

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import (
    AddAnnotations,
    Command,
    RemoveAnnotations,
    ReorderAnnotations,
    ReplaceAnnotations,
    SetCrop,
)
from verdiclip.document.document import Document
from verdiclip.geometry import Point, Rect


class TestAddAnnotations:
    """Append and undo annotations."""

    def test_round_trip(self, document: Document) -> None:
        """Add and revert preserve an initially empty document."""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))
        command = AddAnnotations([shape], "Draw")

        command.apply(document)
        assert document.annotations == (shape,)
        command.revert(document)

        assert document.annotations == ()
        assert command.description == "Draw"
        assert not command.merge(SetCrop(document.crop, document.crop))

    def test_empty(self) -> None:
        """Adding an empty sequence is rejected."""
        with pytest.raises(ValueError, match="at least one"):
            AddAnnotations([], "Draw")


class TestRemoveAnnotations:
    """Deletion preserves original stacking positions."""

    def test_round_trip(self, document: Document) -> None:
        """Revert restores removed annotations at their exact indices."""
        shapes = [RectangleShape(rect=Rect(i * 20, 0, 10, 10)) for i in range(3)]
        document.insert(list(enumerate(shapes)))
        command = RemoveAnnotations([shapes[0].id, shapes[2].id])

        command.apply(document)
        assert document.annotations == (shapes[1],)
        command.revert(document)

        assert document.annotations == tuple(shapes)


class TestReplaceAnnotations:
    """Snapshot swaps and compatible command merging."""

    def test_mismatched_ids(self) -> None:
        """Snapshots must retain annotation identity."""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))

        with pytest.raises(ValueError, match="matching ids"):
            ReplaceAnnotations([shape], [shape.with_new_id()], "Move")

    @pytest.mark.parametrize(
        "case",
        ["same", "different-key", "different-id", "different-command", "empty-key"],
        ids=["same", "different-key", "different-id", "different-command", "empty-key"],
    )
    def test_merge(self, document: Document, case: str) -> None:
        """Only the same non-empty merge key and ids coalesce."""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))
        moved = shape.translated(Point(5, 0))
        final = shape.translated(Point(10, 0))
        document.insert([(0, shape)])
        command = ReplaceAnnotations(
            [shape], [moved], "Move", merge_key="" if case == "empty-key" else "drag"
        )
        other: Command = ReplaceAnnotations(
            [moved],
            [final],
            "Move",
            merge_key="other" if case == "different-key" else "drag",
        )
        if case == "different-id":
            unrelated = shape.with_new_id()
            other = ReplaceAnnotations(
                [unrelated], [unrelated], "Move", merge_key="drag"
            )
        elif case == "different-command":
            other = SetCrop(document.crop, document.crop)

        merged = command.merge(other)
        command.apply(document)
        assert document.annotations == (final if merged else moved,)
        command.revert(document)

        assert merged is (case == "same")
        assert document.annotations == (shape,)


class TestReorderAnnotations:
    """Stack swaps are reversible."""

    def test_round_trip(self, document: Document) -> None:
        """Apply reverses stacking and revert restores it."""
        shapes = [RectangleShape(rect=Rect(i * 20, 0, 10, 10)) for i in range(3)]
        document.insert(list(enumerate(shapes)))
        command = ReorderAnnotations(
            [a.id for a in shapes], [a.id for a in reversed(shapes)], "Restack"
        )

        command.apply(document)
        assert document.annotations == tuple(reversed(shapes))
        command.revert(document)

        assert document.annotations == tuple(shapes)


class TestSetCrop:
    """Non-destructive crop commands."""

    def test_round_trip(self, document: Document) -> None:
        """Undo restores the original crop exactly."""
        original = document.crop
        command = SetCrop(original, Rect(20, 30, 40, 50))

        command.apply(document)
        assert document.crop == Rect(20, 30, 40, 50)
        command.revert(document)

        assert document.crop == original
