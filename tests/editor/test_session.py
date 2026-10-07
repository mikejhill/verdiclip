"""Test widget-free selection, tool defaults, and editing operations."""

from __future__ import annotations

from dataclasses import replace

import pytest

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.style import HIGHLIGHT_YELLOW, RED, WHITE, Style
from verdiclip.editor.session import EditorSession, Selection, ToolId, ToolStyles
from verdiclip.exceptions import CodecError
from verdiclip.geometry import Point, Rect
from verdiclip.settings import EditorSettings


class TestSelection:
    """Ordered unique selection and notifications."""

    def test_membership(self) -> None:
        """Selection deduplicates, toggles, and notifies only on changes."""
        selection = Selection()
        events: list[str] = []
        selection.subscribe(lambda: events.append("changed"))

        selection.set(["a", "b", "a"])
        selection.set(["a", "b"])
        selection.toggle("a")
        selection.toggle("c")

        assert selection.ids == ("b", "c")
        assert "b" in selection
        assert "a" not in selection
        assert len(selection) == 2
        assert len(events) == 3
        selection.clear()
        assert not selection.ids


class TestToolStyles:
    """Tool-specific style defaults."""

    def test_defaults_and_updates(self) -> None:
        """Counter, highlight, and obfuscation have specialized defaults."""
        styles = ToolStyles(EditorSettings())

        counter = styles.get(ToolId.COUNTER)

        assert counter.stroke == WHITE
        assert counter.fill == RED
        assert styles.get(ToolId.HIGHLIGHT).fill == HIGHLIGHT_YELLOW
        assert styles.get(ToolId.OBFUSCATE).width == 12
        changed = Style(width=8)
        styles.set(ToolId.RECTANGLE, changed)
        assert styles.get(ToolId.RECTANGLE) == changed
        assert styles.get(ToolId.ELLIPSE).width == 3


class TestEditorSession:
    """Selection-wide edits and element clipboard round trips."""

    def test_empty_operations(self, session: EditorSession) -> None:
        """Operations on an empty selection create no history."""
        assert session.copy_selected() is None

        deleted = session.delete_selected()
        nudged = session.nudge_selected(Point(1, 0))
        restyled = session.restyle_selected(lambda style: replace(style, width=8))
        restacked = session.restack_selected(forward=True)
        pasted = session.paste('{"version":1,"annotations":[]}')

        assert not any((deleted, nudged, restyled, restacked, pasted))
        assert not session.history.can_undo

    def test_delete_and_prune(self, session: EditorSession) -> None:
        """Deletion and undoing insertion prune removed ids from selection."""
        shape = RectangleShape(rect=Rect(20, 20, 40, 40))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()

        assert session.delete_selected()
        assert not session.selection.ids
        session.history.undo()
        session.select_all()
        session.history.undo()

        assert session.document.annotations == ()
        assert not session.selection.ids

    def test_nudge_coalesces(self, session: EditorSession) -> None:
        """Consecutive nudges of the same selection form one undo step."""
        shape = RectangleShape(rect=Rect(20, 20, 40, 40))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.selection.set([shape.id])

        session.nudge_selected(Point(1, 0))
        session.nudge_selected(Point(0, 10))
        assert session.selected() == [shape.translated(Point(1, 10))]
        session.history.undo()

        assert session.document.annotations == (shape,)
        assert session.history.undo_text == "Draw"

    def test_restyle(self, session: EditorSession) -> None:
        """Style changes are undoable and identical changes are ignored."""
        shape = RectangleShape(rect=Rect(20, 20, 40, 40))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()

        changed = session.restyle_selected(lambda style: replace(style, width=8))

        assert changed
        assert session.selected()[0].style.width == 8
        assert not session.restyle_selected(lambda style: style)
        session.history.undo()
        assert session.selected() == [shape]

    @pytest.mark.parametrize(
        "direction",
        ["forward", "backward", "boundary"],
        ids=["forward", "backward", "boundary"],
    )
    def test_restack(self, session: EditorSession, direction: str) -> None:
        """Restacking moves a selection one step and ignores boundaries."""
        shapes = [RectangleShape(rect=Rect(i * 30, 0, 20, 20)) for i in range(3)]
        session.history.execute(AddAnnotations(shapes, "Draw"))
        selected = shapes[2] if direction == "boundary" else shapes[1]
        session.selection.set([selected.id])

        changed = session.restack_selected(forward=direction != "backward")

        expected = (
            [shapes[0], shapes[2], shapes[1]]
            if direction == "forward"
            else [shapes[1], shapes[0], shapes[2]]
            if direction == "backward"
            else shapes
        )
        assert changed is (direction != "boundary")
        assert session.document.annotations == tuple(expected)

    def test_copy_paste(self, session: EditorSession) -> None:
        """Pasted elements gain new identities, ten-pixel offsets, and selection."""
        shape = RectangleShape(rect=Rect(20, 20, 40, 40))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        payload = session.copy_selected()
        assert payload is not None

        pasted = session.paste(payload)

        assert pasted
        copy = session.selected()[0]
        assert copy.id != shape.id
        assert copy.bounds == shape.bounds.translated(Point(10, 10))
        assert copy.style == shape.style
        session.history.undo()
        assert session.document.annotations == (shape,)
        assert not session.selection.ids

    @pytest.mark.parametrize(
        "text",
        ["{", "[]", '{"version":2,"annotations":[]}'],
        ids=["bad-json", "wrong-root", "wrong-version"],
    )
    def test_invalid_paste(self, session: EditorSession, text: str) -> None:
        """Malformed clipboard input raises CodecError without editing."""
        with pytest.raises(CodecError, match=r"Clipboard|object|Unsupported"):
            session.paste(text)

        assert not session.history.can_undo
