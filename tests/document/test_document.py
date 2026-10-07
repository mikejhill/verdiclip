"""Test document queries, mutation invariants, and notifications."""

from __future__ import annotations

from functools import partial

import pytest
from PySide6.QtGui import QImage

from verdiclip.document.annotations import CounterMarker, RectangleShape
from verdiclip.document.document import Document
from verdiclip.document.style import RED, Style
from verdiclip.geometry import Point, Rect


class TestDocument:
    """Document state in original image coordinates."""

    def test_null_image(self) -> None:
        """Null image documents are rejected."""
        with pytest.raises(ValueError, match="must not be null"):
            Document(QImage())

    def test_queries(self, document: Document) -> None:
        """Queries return the topmost visible target and stacking positions."""
        lower = RectangleShape(rect=Rect(10, 10, 30, 30), style=Style(fill=RED))
        upper = lower.with_new_id()
        document.insert([(0, lower), (1, upper)])

        hit = document.annotation_at(Point(20, 20), 0)

        assert hit == upper
        assert document.find(lower.id) == lower
        assert document.find("missing") is None
        assert document.index_of(upper.id) == 1
        assert document.annotations_in(Rect(20, 20, 5, 5)) == [lower, upper]
        assert document.annotation_at(Point(200, 100), 0) is None
        assert document.image.size().width() == 400
        assert document.image_rect == Rect(0, 0, 400, 300)

    def test_crop_visibility(self, document: Document) -> None:
        """Crop hides outside annotations without changing their coordinates."""
        inside = RectangleShape(rect=Rect(20, 20, 10, 10))
        outside = RectangleShape(rect=Rect(200, 200, 10, 10))
        document.insert([(0, inside), (1, outside)])

        document.set_crop(Rect(-10, -10, 110, 110))

        assert document.crop == Rect(0, 0, 100, 100)
        assert document.visible_annotations == (inside,)
        assert document.annotations == (inside, outside)

    def test_notifications(self, document: Document) -> None:
        """Subscribed listeners receive changes until explicitly unsubscribed."""
        events: list[str] = []
        listener = partial(events.append, "changed")
        document.subscribe(listener)

        document.insert([])
        document.unsubscribe(listener)
        document.remove([])

        assert events == ["changed"]

    def test_invalid_queries_and_mutations(self, document: Document) -> None:
        """Unknown ids, invalid permutations, and outside crops fail explicitly."""
        shape = RectangleShape(rect=Rect(0, 0, 10, 10))

        with pytest.raises(KeyError, match="missing"):
            document.index_of("missing")
        with pytest.raises(KeyError, match="unknown annotations"):
            document.replace([shape])
        with pytest.raises(ValueError, match="exactly once"):
            document.reorder([shape.id])
        with pytest.raises(ValueError, match="does not overlap"):
            document.set_crop(Rect(500, 500, 20, 20))

    def test_remove_and_reinsert(self, document: Document) -> None:
        """Removing and reinserting indexed items restores stacking exactly."""
        shapes = [RectangleShape(rect=Rect(i * 20, 0, 10, 10)) for i in range(3)]
        document.insert(list(enumerate(shapes)))

        removed = document.remove([shapes[0].id, shapes[2].id])
        document.insert(removed[::-1])
        document.reorder([a.id for a in reversed(shapes)])

        assert removed == [(0, shapes[0]), (2, shapes[2])]
        assert document.annotations == tuple(reversed(shapes))

    def test_counter_labels(self, document: Document) -> None:
        """The last placed or edited label controls subsequent numbering."""
        marker = CounterMarker(center=Point(20, 20), radius=12, label="8")
        assert document.next_counter_label == "1"
        document.insert([(0, marker)])
        assert document.next_counter_label == "9"

        document.replace([marker.with_label("A")])

        assert document.next_counter_label == "1"
