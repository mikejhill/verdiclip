"""Test tools using pointer gestures and a widget-free recording host."""

from __future__ import annotations

import pytest

from verdiclip.document.annotations import (
    CounterMarker,
    LabeledBox,
    LineShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.commands import AddAnnotations, ReplaceAnnotations
from verdiclip.document.style import RED, Style
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.tools import (
    ArrowTool,
    CounterTool,
    CropTool,
    CursorKind,
    DrawingTool,
    EllipseTool,
    FreehandTool,
    HandleDrag,
    HighlightTool,
    LineTool,
    ObfuscateTool,
    Pointer,
    RectangleTool,
    SelectTool,
    TextTool,
    Tool,
)
from verdiclip.geometry import Point, Rect


class FakeToolHost:
    """Record tool requests without creating widgets."""

    def __init__(self) -> None:
        self.text_calls: list[tuple[TextNote | None, Point]] = []
        self.counter_calls: list[CounterMarker] = []
        self.activations: list[ToolId] = []
        self.box_calls: list[LabeledBox] = []

    def edit_text(self, note: TextNote | None, at: Point) -> None:
        """Record a text editing request."""
        self.text_calls.append((note, at))

    def edit_counter(self, marker: CounterMarker) -> None:
        """Record a counter editing request."""
        self.counter_calls.append(marker)

    def activate_tool(self, tool: ToolId) -> None:
        """Record a tool activation request."""
        self.activations.append(tool)

    def edit_box_text(self, box: LabeledBox) -> None:
        """Record a box label editing request."""
        self.box_calls.append(box)


class TestDrawingTool:
    """Shape creation and selected handle manipulation."""

    @pytest.mark.parametrize(
        "tool_type",
        [
            RectangleTool,
            EllipseTool,
            LineTool,
            ArrowTool,
            HighlightTool,
            ObfuscateTool,
            FreehandTool,
        ],
        ids=[
            "rectangle",
            "ellipse",
            "line",
            "arrow",
            "highlight",
            "obfuscate",
            "freehand",
        ],
    )
    def test_create(self, session: EditorSession, tool_type: type[DrawingTool]) -> None:
        """A complete drawing gesture creates and selects one undoable annotation."""
        tool = tool_type(session, FakeToolHost())
        start, end = Pointer(Point(20, 100)), Pointer(Point(100, 180))

        tool.press(start)
        tool.move(end)
        assert len(tool.preview) == 1
        tool.release(end)

        assert len(session.document.annotations) == 1
        annotation = session.document.annotations[0]
        assert annotation.kind.value == tool.tool_id.value
        assert session.selection.ids == (annotation.id,)
        assert tool.preview == ()
        session.history.undo()
        assert session.document.annotations == ()
        assert not session.history.can_undo

    @pytest.mark.parametrize(
        "tool_type",
        [RectangleTool, EllipseTool, LineTool, ArrowTool, HighlightTool, ObfuscateTool],
        ids=["rectangle", "ellipse", "line", "arrow", "highlight", "obfuscate"],
    )
    def test_short_drag(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Shape drags shorter than three pixels create nothing."""
        tool = tool_type(session, FakeToolHost())

        tool.press(Pointer(Point(20, 100)))
        tool.move(Pointer(Point(21, 101)))
        tool.release(Pointer(Point(21, 101)))

        assert session.document.annotations == ()
        assert not session.history.can_undo

    @pytest.mark.parametrize(
        "tool_type",
        [RectangleTool, EllipseTool, HighlightTool, ObfuscateTool],
        ids=["rectangle", "ellipse", "highlight", "obfuscate"],
    )
    def test_shift_square(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Shift constrains box creation to a square."""
        tool = tool_type(session, FakeToolHost())

        tool.press(Pointer(Point(100, 100)))
        tool.release(Pointer(Point(30, 60), shift=True))

        annotation = session.document.annotations[0]
        assert isinstance(annotation, (RectangleShape,)) or annotation.kind.value in {
            "ellipse",
            "highlight",
            "obfuscate",
        }
        geometry = annotation.geometry_json()["rect"]
        assert geometry == [30, 30, 70, 70]

    @pytest.mark.parametrize(
        "tool_type",
        [LineTool, ArrowTool, RectangleTool],
        ids=["line", "arrow", "rectangle"],
    )
    def test_handle_drag(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Drawing tools resize selected handles as one undo step."""
        shape = LineShape(start=Point(20, 100), end=Point(100, 100))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        tool = tool_type(session, FakeToolHost())

        tool.press(Pointer(shape.end, tolerance=1))
        tool.move(Pointer(Point(100, 160), shift=True))
        tool.move(Pointer(Point(120, 180), shift=True))
        tool.release(Pointer(Point(120, 180), shift=True))

        changed = session.document.find(shape.id)
        assert isinstance(changed, LineShape)
        delta = changed.end - changed.start
        assert abs(delta.x) == pytest.approx(abs(delta.y))
        assert len(session.document.annotations) == 1
        session.history.undo()
        assert session.document.annotations == (shape,)
        assert session.history.undo_text == "Draw"

    @pytest.mark.parametrize(
        "tool_type",
        [LineTool, ArrowTool, FreehandTool],
        ids=["line", "arrow", "freehand"],
    )
    def test_cancel_and_idle(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Idle tools ignore motion and cancellation discards transient previews."""
        tool = tool_type(session, FakeToolHost())
        pointer = Pointer(Point(20, 100))
        tool.move(pointer)
        tool.release(pointer)
        assert not tool.cancel()

        tool.press(pointer)
        tool.move(Pointer(Point(40, 120)))
        cancelled = tool.cancel()
        tool.release(pointer)

        assert cancelled
        assert not tool.preview
        assert session.document.annotations == ()
        assert not tool.confirm()
        assert tool.frame is None
        assert not tool.dims_outside_frame
        tool.double_click(pointer)
        tool.deactivate()

    @pytest.mark.parametrize(
        "tool_type",
        [LineTool, ArrowTool, RectangleTool],
        ids=["line", "arrow", "rectangle"],
    )
    def test_cancel_selection(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Cancelling an idle drawing tool clears selection without deleting work."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        tool = tool_type(session, FakeToolHost())

        cancelled = tool.cancel()

        assert cancelled
        assert not session.selection.ids
        assert session.document.annotations == (shape,)

    @pytest.mark.parametrize("tool_type", [LineTool, ArrowTool], ids=["line", "arrow"])
    def test_shift_line(
        self, session: EditorSession, tool_type: type[DrawingTool]
    ) -> None:
        """Shift snaps line and arrow creation to a 45-degree direction."""
        tool = tool_type(session, FakeToolHost())
        origin = Point(20, 100)

        tool.press(Pointer(origin))
        tool.move(Pointer(Point(120, 180), shift=True))
        tool.release(Pointer(Point(120, 180), shift=True))

        annotation = session.selected()[0]
        assert isinstance(annotation, LineShape)
        delta = annotation.end - origin
        assert delta.x == pytest.approx(delta.y)

    def test_resize_cursor(self, session: EditorSession) -> None:
        """Drawing tools show resize cursors over selected handles."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        tool = RectangleTool(session, FakeToolHost())

        cursor = tool.cursor(Pointer(shape.rect.top_left, tolerance=1))

        assert cursor is CursorKind.RESIZE_FDIAG


class TestSelectTool:
    """Selection, rubber bands, movement, and handle resizing."""

    def test_click_and_toggle(self, session: EditorSession) -> None:
        """Click selects and Shift-click toggles membership."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50), style=Style(fill=RED))
        session.history.execute(AddAnnotations([shape], "Draw"))
        tool = SelectTool(session, FakeToolHost())
        pointer = Pointer(Point(35, 120), tolerance=1)

        tool.press(pointer)
        tool.release(pointer)
        assert session.selection.ids == (shape.id,)
        tool.press(Pointer(pointer.pos, shift=True, tolerance=1))
        assert not session.selection.ids
        tool.press(Pointer(pointer.pos, shift=True, tolerance=1))

        assert session.selection.ids == (shape.id,)

    @pytest.mark.parametrize(
        "mode", ["replace", "add", "empty"], ids=["replace", "shift-add", "empty-click"]
    )
    def test_rubber_band(self, session: EditorSession, mode: str) -> None:
        """Rubber bands select intersections and Shift preserves prior selection."""
        first = RectangleShape(rect=Rect(20, 100, 50, 50))
        second = RectangleShape(rect=Rect(200, 100, 50, 50))
        session.history.execute(AddAnnotations([first, second], "Draw"))
        session.selection.set([second.id])
        tool = SelectTool(session, FakeToolHost())
        start = Pointer(Point(0, 90), shift=mode == "add", tolerance=1)

        tool.press(start)
        if mode != "empty":
            tool.move(Pointer(Point(25, 120)))
            assert tool.frame == Rect(0, 90, 25, 30)
        tool.release(Pointer(Point(25, 120)))

        expected = (
            (second.id, first.id)
            if mode == "add"
            else (first.id,)
            if mode == "replace"
            else ()
        )
        assert session.selection.ids == expected
        assert tool.frame is None

    def test_move_selection(self, session: EditorSession) -> None:
        """Dragging moves all selected items as one undo step."""
        shapes = [
            RectangleShape(rect=Rect(x, 100, 50, 50), style=Style(fill=RED))
            for x in (20, 200)
        ]
        session.history.execute(AddAnnotations(shapes, "Draw"))
        session.select_all()
        tool = SelectTool(session, FakeToolHost())

        tool.press(Pointer(Point(35, 120), tolerance=1))
        tool.move(Pointer(Point(45, 130)))
        tool.move(Pointer(Point(55, 150)))
        tool.release(Pointer(Point(55, 150)))

        assert session.selected() == [
            shape.translated(Point(20, 30)) for shape in shapes
        ]
        session.history.undo()
        assert session.document.annotations == tuple(shapes)
        assert session.history.undo_text == "Draw"

    def test_resize(self, session: EditorSession) -> None:
        """Handle resizing forms one undo step across multiple move events."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        tool = SelectTool(session, FakeToolHost())

        tool.press(Pointer(Point(70, 150), tolerance=1))
        tool.move(Pointer(Point(80, 160)))
        tool.move(Pointer(Point(100, 180)))
        tool.release(Pointer(Point(100, 180)))

        changed = session.document.find(shape.id)
        assert isinstance(changed, RectangleShape)
        assert changed.rect == Rect(20, 100, 80, 80)
        session.history.undo()
        assert session.document.annotations == (shape,)
        assert session.history.undo_text == "Draw"

    def test_double_click(self, session: EditorSession) -> None:
        """Double-click requests text and counter editors only for matching items."""
        note = TextNote(rect=Rect(20, 100, 50, 50), text="Note")
        counter = CounterMarker(center=Point(200, 100), radius=20, label="1")
        session.history.execute(AddAnnotations([note, counter], "Draw"))
        host = FakeToolHost()
        tool = SelectTool(session, host)

        tool.double_click(Pointer(Point(30, 110)))
        tool.double_click(Pointer(counter.center))
        tool.double_click(Pointer(Point(390, 290)))

        assert host.text_calls == [(note, note.rect.top_left)]
        assert host.counter_calls == [counter]
        assert not tool.cancel()
        session.select_all()
        assert tool.cancel()


class TestHandleDrag:
    """Handle gesture lifecycle edge cases."""

    def test_missing_target(self, session: EditorSession) -> None:
        """Removing a dragged target ends the gesture safely."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        drag = HandleDrag(session)
        drag.update(Pointer(Point(0, 0)))
        assert drag.begin(Pointer(shape.rect.top_left, tolerance=1))
        session.delete_selected()

        drag.update(Pointer(Point(10, 10)))

        assert not drag.active

    def test_unchanged_handle(self, session: EditorSession) -> None:
        """Moving a handle to its current position creates no history step."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        drag = HandleDrag(session)
        pointer = Pointer(shape.rect.top_left, tolerance=1)

        assert drag.begin(pointer)
        drag.update(pointer)
        drag.end()

        assert session.history.undo_text == "Draw"


class TestTextTool:
    """Text editor requests."""

    def test_edit_existing_and_new(self, session: EditorSession) -> None:
        """Text clicks request the existing note or a new note at the pointer."""
        note = TextNote(rect=Rect(20, 100, 50, 50), text="Note")
        session.history.execute(AddAnnotations([note], "Draw"))
        host = FakeToolHost()
        tool = TextTool(session, host)

        tool.press(Pointer(Point(30, 110)))
        tool.press(Pointer(Point(200, 200)))
        tool.move(Pointer(Point(300, 200)))
        tool.release(Pointer(Point(300, 200)))

        assert host.text_calls == [(note, note.rect.top_left), (None, Point(200, 200))]
        assert session.document.annotations == (note,)
        assert tool.preview == ()
        assert not tool.cancel()


class TestCounterTool:
    """Counter numbering and editing requests."""

    def test_numbering(self, session: EditorSession) -> None:
        """Counters count upward and restart after a nonnumeric relabel."""
        host = FakeToolHost()
        tool = CounterTool(session, host)
        for x in (40, 100, 160):
            tool.press(Pointer(Point(x, 100)))
        markers = session.document.annotations
        assert [a.label for a in markers if isinstance(a, CounterMarker)] == [
            "1",
            "2",
            "3",
        ]
        last = markers[-1]
        assert isinstance(last, CounterMarker)
        session.history.execute(
            ReplaceAnnotations([last], [last.with_label("A")], "Relabel")
        )

        tool.press(Pointer(Point(220, 100)))
        tool.move(Pointer(Point(0, 0)))
        tool.release(Pointer(Point(0, 0)))
        tool.double_click(Pointer(Point(220, 100)))
        tool.double_click(Pointer(Point(390, 290)))

        pasted = session.selected()[0]
        assert isinstance(pasted, CounterMarker)
        assert pasted.label == "1"
        assert session.selection.ids == (pasted.id,)
        assert host.counter_calls == [pasted]


class TestCropTool:
    """Clamped, reversible crop frame gestures."""

    def test_confirm_and_undo(self, session: EditorSession) -> None:
        """Crop undo restores the exact crop and annotation coordinates."""
        shape = RectangleShape(rect=Rect(100, 100, 40, 50))
        session.history.execute(AddAnnotations([shape], "Draw"))
        before = session.document.crop
        host = FakeToolHost()
        tool = CropTool(session, host)

        tool.press(Pointer(Point(-20, -30)))
        tool.move(Pointer(Point(200, 150)))
        tool.release(Pointer(Point(200, 150)))
        assert tool.frame == Rect(0, 0, 200, 150)
        assert tool.dims_outside_frame
        assert tool.confirm()

        assert session.document.crop == Rect(0, 0, 200, 150)
        assert session.document.annotations == (shape,)
        assert host.activations == [ToolId.SELECT]
        assert tool.frame is None
        session.history.undo()
        assert session.document.crop == before
        assert session.document.annotations == (shape,)

    def test_cancel_and_tiny(self, session: EditorSession) -> None:
        """Tiny frames are discarded and cancellation clears a pending frame."""
        tool = CropTool(session, FakeToolHost())
        tool.move(Pointer(Point(0, 0)))
        assert not tool.confirm()
        assert not tool.cancel()
        tool.press(Pointer(Point(10, 10)))
        tool.release(Pointer(Point(11, 11)))
        assert tool.frame is None

        tool.press(Pointer(Point(10, 10)))
        tool.release(Pointer(Point(100, 100)))
        cancelled = tool.cancel()

        assert cancelled
        assert tool.frame is None
        assert not session.history.can_undo

    def test_double_click(self, session: EditorSession) -> None:
        """Double-click confirms the pending crop frame."""
        host = FakeToolHost()
        tool = CropTool(session, host)
        tool.press(Pointer(Point(10, 10)))
        tool.release(Pointer(Point(500, 500)))

        tool.double_click(Pointer(Point(100, 100)))

        assert session.document.crop == Rect(10, 10, 390, 290)
        assert host.activations == [ToolId.SELECT]

    def test_stale_frame(self, session: EditorSession) -> None:
        """Frames outside a changed document crop cannot be confirmed."""
        tool = CropTool(session, FakeToolHost())
        tool.press(Pointer(Point(10, 10)))
        tool.release(Pointer(Point(50, 50)))
        session.document.set_crop(Rect(200, 200, 50, 50))

        confirmed = tool.confirm()

        assert not confirmed
        assert tool.frame is None
        assert not session.history.can_undo


class TestFreehandTool:
    """Freehand preview construction and jitter filtering."""

    def test_idle_and_jitter(self, session: EditorSession) -> None:
        """Idle construction yields nothing and subpixel jitter adds no point."""
        tool = FreehandTool(session, FakeToolHost())
        origin = Point(10, 100)
        assert tool.build(origin, Pointer(origin)) is None
        tool.press(Pointer(origin))

        annotation = tool.build(origin, Pointer(Point(10.1, 100.1)))

        assert annotation is not None
        assert annotation.geometry_json() == {"points": [[10, 100]]}


class TestTool:
    """Cursor choices over blank space and selected targets."""

    @pytest.mark.parametrize(
        ("tool_type", "expected"),
        [
            (SelectTool, CursorKind.ARROW),
            (RectangleTool, CursorKind.CROSS),
            (CropTool, CursorKind.CROSS),
            (TextTool, CursorKind.IBEAM),
            (CounterTool, CursorKind.POINT),
        ],
        ids=["select", "drawing", "crop", "text", "counter"],
    )
    def test_idle_cursor(
        self, session: EditorSession, tool_type: type[Tool], expected: CursorKind
    ) -> None:
        """Each tool exposes the cursor appropriate to its action."""
        tool = tool_type(session, FakeToolHost())

        cursor = tool.cursor(Pointer(Point(0, 0)))

        assert cursor is expected

    @pytest.mark.parametrize(
        ("position", "expected"),
        [
            (Point(20, 100), CursorKind.RESIZE_FDIAG),
            (Point(45, 100), CursorKind.RESIZE_V),
            (Point(70, 125), CursorKind.RESIZE_H),
            (Point(70, 100), CursorKind.RESIZE_BDIAG),
            (Point(35, 120), CursorKind.MOVE),
        ],
        ids=["corner", "top", "right", "opposite-diagonal", "interior"],
    )
    def test_select_cursor(
        self, session: EditorSession, position: Point, expected: CursorKind
    ) -> None:
        """Select cursors distinguish handles from move targets."""
        shape = RectangleShape(rect=Rect(20, 100, 50, 50), style=Style(fill=RED))
        session.history.execute(AddAnnotations([shape], "Draw"))
        session.select_all()
        tool = SelectTool(session, FakeToolHost())

        cursor = tool.cursor(Pointer(position, tolerance=1))

        assert cursor is expected


class TestEditInPlace:
    """Double-click opens the right in-place editor."""

    @pytest.mark.parametrize(
        "tool_type", [SelectTool, RectangleTool], ids=["select", "rectangle"]
    )
    def test_double_click_inside_hollow_box_edits_its_label(
        self, session: EditorSession, tool_type: type[Tool]
    ) -> None:
        """UX-TL-13: the inside of an unfilled rectangle counts for double-click."""
        host = FakeToolHost()
        box = RectangleShape(rect=Rect(100, 100, 200, 100))
        session.history.execute(AddAnnotations([box], "Add"))
        tool = tool_type(session, host)

        tool.double_click(Pointer(Point(200, 150)))

        assert host.box_calls == [box]
        assert session.selection.ids == (box.id,)

    def test_double_click_on_empty_canvas_does_nothing(
        self, session: EditorSession
    ) -> None:
        """Double-clicking empty space opens no editor."""
        host = FakeToolHost()

        SelectTool(session, host).double_click(Pointer(Point(5, 5)))

        assert (host.box_calls, host.text_calls, host.counter_calls) == ([], [], [])

    def test_labeled_hollow_box_is_selectable_inside(
        self, session: EditorSession
    ) -> None:
        """A hollow box with a label can be clicked anywhere inside to select it."""
        box = RectangleShape(rect=Rect(100, 100, 200, 100), text="Login")
        session.history.execute(AddAnnotations([box], "Add"))

        SelectTool(session, FakeToolHost()).press(Pointer(Point(200, 150)))

        assert session.selection.ids == (box.id,)
