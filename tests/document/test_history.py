"""Test undo, redo, branching, merging, and delivery state."""

from __future__ import annotations

import pytest

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations, ReplaceAnnotations
from verdiclip.document.document import Document
from verdiclip.document.history import History
from verdiclip.geometry import Point, Rect


class TestHistory:
    """Reversible history and delivered-state tracking."""

    def test_empty_and_round_trip(self, document: Document) -> None:
        """Empty undo and redo are harmless and commands expose action text."""
        history = History(document)
        events: list[str] = []
        history.subscribe(lambda: events.append("changed"))
        history.undo()
        history.redo()
        assert history.is_delivered
        assert history.undo_text == history.redo_text == ""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))

        history.execute(AddAnnotations([shape], "Draw"))
        assert history.can_undo
        assert not history.can_redo
        assert history.undo_text == "Draw"
        history.undo()
        assert history.is_delivered
        assert history.redo_text == "Draw"
        history.redo()
        history.mark_delivered()

        assert history.document is document
        assert history.is_delivered
        assert document.annotations == (shape,)
        assert len(events) == 4

    @pytest.mark.parametrize(
        "delivered",
        ["initial", "future", "current"],
        ids=["initial", "discarded-future", "current"],
    )
    def test_branch(self, document: Document, delivered: str) -> None:
        """Branching discards redo and cannot falsely restore discarded delivery."""
        history = History(document)
        first = RectangleShape(rect=Rect(0, 0, 10, 10))
        history.execute(AddAnnotations([first], "First"))
        if delivered == "future":
            history.mark_delivered()
        history.undo()
        if delivered == "current":
            history.mark_delivered()

        history.execute(AddAnnotations([first.with_new_id()], "Branch"))

        assert not history.can_redo
        assert not history.is_delivered
        history.undo()
        assert history.is_delivered is (delivered != "future")

    def test_merge_and_delivery_boundary(self, document: Document) -> None:
        """Merged edits undo together but a delivery mark separates later edits."""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))
        document.insert([(0, shape)])
        history = History(document)
        moved = shape.translated(Point(1, 0))
        final = shape.translated(Point(2, 0))

        history.execute(ReplaceAnnotations([shape], [moved], "Move", merge_key="nudge"))
        history.execute(ReplaceAnnotations([moved], [final], "Move", merge_key="nudge"))
        history.mark_delivered()
        history.execute(ReplaceAnnotations([final], [shape], "Move", merge_key="nudge"))
        history.undo()

        assert document.annotations == (final,)
        assert history.is_delivered
        history.undo()
        assert document.annotations == (shape,)
        assert not history.can_undo
