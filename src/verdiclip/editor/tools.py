"""Editor tools: turn pointer input into commands on the session."""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Protocol, override

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
    BoxAnnotation,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    Geometry,
    HandleRole,
    HighlightShape,
    LabeledBox,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.commands import AddAnnotations, ReplaceAnnotations, SetCrop
from verdiclip.document.style import Style
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.geometry import Point, Rect

MIN_DRAG: Final = 3.0
HANDLE_REACH: Final = 2.0  # Handles are grabbable at this multiple of the tolerance


class GestureKeys:
    """Unique merge keys so one drag gesture becomes one undo step."""

    _counter: ClassVar[itertools.count[int]] = itertools.count(1)

    @classmethod
    def next(cls, prefix: str) -> str:
        """Return a fresh key such as ``move:7``."""
        return f"{prefix}:{next(cls._counter)}"


class CursorKind(StrEnum):
    """Cursor shapes a tool can ask for."""

    ARROW = "arrow"
    CROSS = "cross"
    MOVE = "move"
    IBEAM = "ibeam"
    POINT = "point"
    RESIZE_H = "resize_h"
    RESIZE_V = "resize_v"
    RESIZE_FDIAG = "resize_fdiag"
    RESIZE_BDIAG = "resize_bdiag"


HANDLE_CURSORS: Final[dict[HandleRole, CursorKind]] = {
    HandleRole.TOP_LEFT: CursorKind.RESIZE_FDIAG,
    HandleRole.BOTTOM_RIGHT: CursorKind.RESIZE_FDIAG,
    HandleRole.TOP_RIGHT: CursorKind.RESIZE_BDIAG,
    HandleRole.BOTTOM_LEFT: CursorKind.RESIZE_BDIAG,
    HandleRole.TOP: CursorKind.RESIZE_V,
    HandleRole.BOTTOM: CursorKind.RESIZE_V,
    HandleRole.LEFT: CursorKind.RESIZE_H,
    HandleRole.RIGHT: CursorKind.RESIZE_H,
    HandleRole.START: CursorKind.CROSS,
    HandleRole.END: CursorKind.CROSS,
}


@dataclass(frozen=True, slots=True)
class Pointer:
    """A pointer event in image coordinates.

    ``tolerance`` is the hit radius in image pixels (screen pixels / zoom).
    """

    pos: Point
    shift: bool = False
    ctrl: bool = False
    tolerance: float = 4.0


class ToolHost(Protocol):
    """UI services a tool may request from the editor."""

    def edit_text(self, note: TextNote | None, at: Point) -> None:
        """Open the inline text editor for ``note`` (or a new note at ``at``)."""

    def edit_counter(self, marker: CounterMarker) -> None:
        """Open the inline label editor for ``marker``."""

    def edit_box_text(self, box: LabeledBox) -> None:
        """Open the inline editor for the text inside ``box``."""

    def activate_tool(self, tool: ToolId) -> None:
        """Switch to ``tool``."""


class HandleDrag:
    """Shared gesture: drag one handle of the single selected annotation."""

    def __init__(self, session: EditorSession) -> None:
        self._session = session
        self._target: Annotation | None = None
        self._role: HandleRole | None = None
        self._key = ""

    @property
    def active(self) -> bool:
        """True while a handle is being dragged."""
        return self._target is not None

    def handle_at(self, pointer: Pointer) -> tuple[Annotation, HandleRole] | None:
        """Return the selected annotation and handle under the pointer."""
        selected = self._session.selected()
        if len(selected) != 1:
            return None
        reach = pointer.tolerance * HANDLE_REACH
        for role, pos in selected[0].handles().items():
            if pos.distance_to(pointer.pos) <= reach:
                return selected[0], role
        return None

    def begin(self, pointer: Pointer) -> bool:
        """Start dragging if a handle is under the pointer."""
        hit = self.handle_at(pointer)
        if hit is None:
            return False
        self._target, self._role = hit
        self._key = GestureKeys.next("handle")
        return True

    def update(self, pointer: Pointer) -> None:
        """Move the handle to the pointer."""
        if self._target is None or self._role is None:
            return
        current = self._session.document.find(self._target.id)
        if current is None:
            self.end()
            return
        moved = self._target.with_handle_moved(
            self._role, pointer.pos, constrain=pointer.shift
        )
        if moved != current:
            self._session.history.execute(
                ReplaceAnnotations([current], [moved], "Resize", merge_key=self._key)
            )

    def end(self) -> None:
        """Finish the gesture."""
        self._target = None
        self._role = None


class Tool(ABC):
    """Base class for editor tools."""

    tool_id: ClassVar[ToolId]
    label: ClassVar[str]
    shortcut: ClassVar[str]
    hint: ClassVar[str]

    def __init__(self, session: EditorSession, host: ToolHost) -> None:
        self._session = session
        self._host = host

    @property
    def style(self) -> Style:
        """Return the style this tool draws with."""
        return self._session.styles.get(self.tool_id)

    @property
    def preview(self) -> tuple[Annotation, ...]:
        """Return in-progress annotations drawn above the document."""
        return ()

    @property
    def frame(self) -> Rect | None:
        """Return a rubber band or crop frame to draw, if any."""
        return None

    @property
    def dims_outside_frame(self) -> bool:
        """True if the canvas should dim everything outside ``frame``."""
        return False

    @abstractmethod
    def press(self, pointer: Pointer) -> None:
        """Handle a left-button press."""

    @abstractmethod
    def move(self, pointer: Pointer) -> None:
        """Handle pointer motion while the button is down."""

    @abstractmethod
    def release(self, pointer: Pointer) -> None:
        """Handle the left-button release."""

    def double_click(self, pointer: Pointer) -> None:  # noqa: B027 — optional hook
        """Handle a double-click (default: ignore)."""

    def edit_in_place(self, pointer: Pointer) -> bool:
        """Open the in-place editor for what is under the pointer, if editable.

        Rectangles and ellipses count anywhere inside, even when empty, so a
        double-click in a hollow box starts typing a label.
        """
        doc = self._session.document
        hit = doc.annotation_at(pointer.pos, pointer.tolerance)
        if hit is None:
            hit = next(
                (
                    a
                    for a in reversed(doc.visible_annotations)
                    if isinstance(a, LabeledBox) and a.rect.contains(pointer.pos)
                ),
                None,
            )
        if isinstance(hit, TextNote):
            self._host.edit_text(hit, hit.rect.top_left)
        elif isinstance(hit, CounterMarker):
            self._host.edit_counter(hit)
        elif isinstance(hit, LabeledBox):
            self._session.selection.set([hit.id])
            self._host.edit_box_text(hit)
        else:
            return False
        return True

    def cursor(self, pointer: Pointer) -> CursorKind:  # noqa: ARG002 — default ignores it
        """Return the cursor to show while hovering (default: crosshair)."""
        return CursorKind.CROSS

    def cancel(self) -> bool:
        """Abort in-progress work for ``Esc``; return True if anything was aborted."""
        return False

    def confirm(self) -> bool:
        """Commit pending work for ``Enter``; return True if anything was committed."""
        return False

    def deactivate(self) -> None:
        """Drop transient state when another tool is chosen."""
        self.cancel()


class SelectTool(Tool):
    """Select, move, and resize annotations."""

    tool_id = ToolId.SELECT
    label = "Select"
    shortcut = "V"
    hint = "Click to select, drag to move, Shift+click to add, drag to box-select"

    def __init__(self, session: EditorSession, host: ToolHost) -> None:
        super().__init__(session, host)
        self._handles = HandleDrag(session)
        self._move_origin: Point | None = None
        self._move_start: list[Annotation] = []
        self._move_key = ""
        self._band_origin: Point | None = None
        self._band: Rect | None = None
        self._band_additive = False

    @property
    @override
    def frame(self) -> Rect | None:
        """Return the rubber band while box-selecting."""
        return self._band

    @override
    def press(self, pointer: Pointer) -> None:
        """Start a handle drag, a move, a toggle, or a rubber band."""
        if self._handles.begin(pointer):
            return
        doc = self._session.document
        selection = self._session.selection
        hit = doc.annotation_at(pointer.pos, pointer.tolerance)
        if hit is None:
            self._band_origin = pointer.pos
            self._band_additive = pointer.shift
            if not pointer.shift:
                selection.clear()
            return
        if pointer.shift:
            selection.toggle(hit.id)
            return
        if hit.id not in selection:
            selection.set([hit.id])
        self._move_origin = pointer.pos
        self._move_start = self._session.selected()
        self._move_key = GestureKeys.next("move")

    @override
    def move(self, pointer: Pointer) -> None:
        """Continue the active gesture."""
        if self._handles.active:
            self._handles.update(pointer)
        elif self._move_origin is not None:
            self._drag_selection(pointer.pos - self._move_origin)
        elif self._band_origin is not None:
            self._band = Rect.from_points(self._band_origin, pointer.pos)

    @override
    def release(self, pointer: Pointer) -> None:
        """Finish the active gesture."""
        self._handles.end()
        if self._band is not None:
            found = [a.id for a in self._session.document.annotations_in(self._band)]
            selection = self._session.selection
            selection.set([*selection.ids, *found] if self._band_additive else found)
        self._move_origin = None
        self._move_start = []
        self._band_origin = None
        self._band = None

    @override
    def double_click(self, pointer: Pointer) -> None:
        """Edit text, a counter label, or a box label in place."""
        self.edit_in_place(pointer)

    @override
    def cursor(self, pointer: Pointer) -> CursorKind:
        """Show resize cursors over handles and a move cursor over annotations."""
        handle = self._handles.handle_at(pointer)
        if handle is not None:
            return HANDLE_CURSORS[handle[1]]
        hit = self._session.document.annotation_at(pointer.pos, pointer.tolerance)
        return CursorKind.MOVE if hit is not None else CursorKind.ARROW

    @override
    def cancel(self) -> bool:
        """Clear the selection."""
        if len(self._session.selection) == 0:
            return False
        self._session.selection.clear()
        return True

    def _drag_selection(self, delta: Point) -> None:
        """Move the selection to its start position plus ``delta``."""
        doc = self._session.document
        current = [doc.find(a.id) for a in self._move_start]
        before = [a for a in current if a is not None]
        start_by_id = {a.id: a for a in self._move_start}
        after = [start_by_id[a.id].translated(delta) for a in before]
        if before and after != before:
            self._session.history.execute(
                ReplaceAnnotations(before, after, "Move", merge_key=self._move_key)
            )


class DrawingTool(Tool):
    """Base for tools that drag out a new annotation; handles stay draggable."""

    def __init__(self, session: EditorSession, host: ToolHost) -> None:
        super().__init__(session, host)
        self._handles = HandleDrag(session)
        self._origin: Point | None = None
        self._preview: Annotation | None = None

    @property
    @override
    def preview(self) -> tuple[Annotation, ...]:
        """Return the annotation being drawn."""
        return (self._preview,) if self._preview is not None else ()

    @abstractmethod
    def build(self, origin: Point, pointer: Pointer) -> Annotation | None:
        """Return the annotation for a drag from ``origin`` to the pointer."""

    @override
    def press(self, pointer: Pointer) -> None:
        """Start a handle drag on the selection, or a new annotation."""
        if self._handles.begin(pointer):
            return
        self._origin = pointer.pos
        self._preview = None

    @override
    def move(self, pointer: Pointer) -> None:
        """Update the handle drag or the preview."""
        if self._handles.active:
            self._handles.update(pointer)
        elif self._origin is not None:
            self._preview = self.build(self._origin, pointer)

    @override
    def release(self, pointer: Pointer) -> None:
        """Commit the new annotation if the drag was long enough, and select it."""
        if self._handles.active:
            self._handles.end()
            return
        if self._origin is not None:
            self._preview = self.build(self._origin, pointer)
        created = self._preview
        self._origin = None
        self._preview = None
        if created is None or not self.big_enough(created):
            return
        self._session.history.execute(
            AddAnnotations([created], f"Draw {self.label.lower()}")
        )
        self._session.selection.set([created.id])
        if isinstance(created, LabeledBox):
            # Boxes are mostly used as labels: start typing straight away
            self._host.edit_box_text(created)

    @override
    def double_click(self, pointer: Pointer) -> None:
        """Type into a rectangle or ellipse (or edit text) without switching tools."""
        self.edit_in_place(pointer)

    @override
    def cursor(self, pointer: Pointer) -> CursorKind:
        """Show resize cursors over handles of the selection, else a crosshair."""
        handle = self._handles.handle_at(pointer)
        return HANDLE_CURSORS[handle[1]] if handle is not None else CursorKind.CROSS

    @override
    def cancel(self) -> bool:
        """Abandon the annotation being drawn, or clear the selection."""
        if self._preview is not None or self._origin is not None:
            self._origin = None
            self._preview = None
            return True
        if len(self._session.selection):
            self._session.selection.clear()
            return True
        return False

    @staticmethod
    def big_enough(annotation: Annotation) -> bool:
        """Return True if the annotation is large enough to keep."""
        match annotation:
            case LineShape():
                return annotation.start.distance_to(annotation.end) >= MIN_DRAG
            case FreehandShape():
                return True
            case BoxAnnotation():
                # Measure the drawn rectangle, not its stroke-grown bounds
                return (
                    annotation.rect.width >= MIN_DRAG
                    and annotation.rect.height >= MIN_DRAG
                )
            case _:
                bounds = annotation.bounds
                return bounds.width >= MIN_DRAG and bounds.height >= MIN_DRAG


class BoxTool(DrawingTool):
    """Base for tools that drag out a rectangle; Shift makes it square."""

    @abstractmethod
    def make(self, rect: Rect) -> Annotation:
        """Return the annotation occupying ``rect``."""

    @override
    def build(self, origin: Point, pointer: Pointer) -> Annotation | None:
        """Return the annotation spanning the drag."""
        rect = Rect.from_points(origin, pointer.pos)
        if pointer.shift:
            rect = Geometry.square_from(origin, pointer.pos)
        return self.make(rect)


class RectangleTool(BoxTool):
    """Draw rectangles."""

    tool_id = ToolId.RECTANGLE
    label = "Rectangle"
    shortcut = "R"
    hint = "Drag to draw a rectangle; Shift for a square"

    @override
    def make(self, rect: Rect) -> Annotation:
        """Return a rectangle."""
        return RectangleShape(rect=rect, style=self.style)


class EllipseTool(BoxTool):
    """Draw ellipses."""

    tool_id = ToolId.ELLIPSE
    label = "Ellipse"
    shortcut = "E"
    hint = "Drag to draw an ellipse; Shift for a circle"

    @override
    def make(self, rect: Rect) -> Annotation:
        """Return an ellipse."""
        return EllipseShape(rect=rect, style=self.style)


class HighlightTool(BoxTool):
    """Draw highlighter boxes."""

    tool_id = ToolId.HIGHLIGHT
    label = "Highlight"
    shortcut = "H"
    hint = "Drag over text to highlight it"

    @override
    def make(self, rect: Rect) -> Annotation:
        """Return a highlight."""
        return HighlightShape(rect=rect, style=self.style)


class ObfuscateTool(BoxTool):
    """Pixelate regions."""

    tool_id = ToolId.OBFUSCATE
    label = "Obfuscate"
    shortcut = "O"
    hint = "Drag over sensitive information to pixelate it"

    @override
    def make(self, rect: Rect) -> Annotation:
        """Return an obfuscation region."""
        return ObfuscateShape(rect=rect, style=self.style)


class LineTool(DrawingTool):
    """Draw straight lines."""

    tool_id = ToolId.LINE
    label = "Line"
    shortcut = "L"
    hint = "Drag to draw a line; Shift snaps to 45°"

    @override
    def build(self, origin: Point, pointer: Pointer) -> Annotation | None:
        """Return a line, snapped when Shift is held."""
        end = pointer.pos.snapped_to_45(origin) if pointer.shift else pointer.pos
        return LineShape(start=origin, end=end, style=self.style)


class ArrowTool(DrawingTool):
    """Draw arrows."""

    tool_id = ToolId.ARROW
    label = "Arrow"
    shortcut = "A"
    hint = "Drag from the tail to the point; Shift snaps to 45°"

    @override
    def build(self, origin: Point, pointer: Pointer) -> Annotation | None:
        """Return an arrow, snapped when Shift is held."""
        end = pointer.pos.snapped_to_45(origin) if pointer.shift else pointer.pos
        return ArrowShape(start=origin, end=end, style=self.style)


class FreehandTool(DrawingTool):
    """Draw freehand strokes."""

    tool_id = ToolId.FREEHAND
    label = "Freehand"
    shortcut = "F"
    hint = "Drag to draw freely"

    def __init__(self, session: EditorSession, host: ToolHost) -> None:
        super().__init__(session, host)
        self._points: list[Point] = []

    @override
    def press(self, pointer: Pointer) -> None:
        """Start a new stroke."""
        super().press(pointer)
        self._points = [pointer.pos]

    @override
    def build(self, origin: Point, pointer: Pointer) -> Annotation | None:
        """Extend the stroke, skipping sub-pixel jitter."""
        if self._points and self._points[-1].distance_to(pointer.pos) >= 1:
            self._points.append(pointer.pos)
        if not self._points:
            return None
        return FreehandShape(points=tuple(self._points), style=self.style)


class TextTool(Tool):
    """Place and edit text."""

    tool_id = ToolId.TEXT
    label = "Text"
    shortcut = "T"
    hint = "Click to type; click existing text to edit it; Esc to finish"

    @override
    def press(self, pointer: Pointer) -> None:
        """Edit the text under the pointer or start a new note."""
        hit = self._session.document.annotation_at(pointer.pos, pointer.tolerance)
        if isinstance(hit, TextNote):
            self._host.edit_text(hit, hit.rect.top_left)
        else:
            self._host.edit_text(None, pointer.pos)

    @override
    def move(self, pointer: Pointer) -> None:
        """Ignore motion."""

    @override
    def release(self, pointer: Pointer) -> None:
        """Ignore release."""

    @override
    def cursor(self, pointer: Pointer) -> CursorKind:
        """Show a text cursor."""
        return CursorKind.IBEAM


class CounterTool(Tool):
    """Place numbered markers."""

    tool_id = ToolId.COUNTER
    label = "Counter"
    shortcut = "N"
    hint = "Click to place the next number; double-click a number to change it"

    @override
    def press(self, pointer: Pointer) -> None:
        """Place a counter with the next label and select it."""
        doc = self._session.document
        marker = CounterMarker(
            center=pointer.pos,
            radius=float(self.style.font_size),
            label=doc.next_counter_label,
            style=self.style,
        )
        self._session.history.execute(AddAnnotations([marker], "Add counter"))
        self._session.selection.set([marker.id])

    @override
    def move(self, pointer: Pointer) -> None:
        """Ignore motion."""

    @override
    def release(self, pointer: Pointer) -> None:
        """Ignore release."""

    @override
    def double_click(self, pointer: Pointer) -> None:
        """Edit the counter under the pointer."""
        hit = self._session.document.annotation_at(pointer.pos, pointer.tolerance)
        if isinstance(hit, CounterMarker):
            self._host.edit_counter(hit)

    @override
    def cursor(self, pointer: Pointer) -> CursorKind:
        """Show a pointing cursor."""
        return CursorKind.POINT


class CropTool(Tool):
    """Drag a crop frame; Enter or double-click applies it."""

    tool_id = ToolId.CROP
    label = "Crop"
    shortcut = "C"
    hint = "Drag the area to keep, then press Enter (Esc cancels)"

    def __init__(self, session: EditorSession, host: ToolHost) -> None:
        super().__init__(session, host)
        self._origin: Point | None = None
        self._frame: Rect | None = None

    @property
    @override
    def frame(self) -> Rect | None:
        """Return the crop frame."""
        return self._frame

    @property
    @override
    def dims_outside_frame(self) -> bool:
        """Dim what will be cropped away."""
        return self._frame is not None

    @override
    def press(self, pointer: Pointer) -> None:
        """Start a new frame."""
        self._origin = self._clamp(pointer.pos)
        self._frame = None

    @override
    def move(self, pointer: Pointer) -> None:
        """Resize the frame, clamped to the visible image."""
        if self._origin is None:
            return
        self._frame = Rect.from_points(self._origin, self._clamp(pointer.pos))

    @override
    def release(self, pointer: Pointer) -> None:
        """Keep the frame for confirmation, discarding tiny frames."""
        self.move(pointer)
        self._origin = None
        if self._frame is not None and (
            self._frame.width < MIN_DRAG or self._frame.height < MIN_DRAG
        ):
            self._frame = None

    @override
    def double_click(self, pointer: Pointer) -> None:
        """Apply the frame."""
        self.confirm()

    @override
    def confirm(self) -> bool:
        """Apply the crop and return to the Select tool."""
        if self._frame is None:
            return False
        frame = self._frame.rounded().intersected(self._session.document.crop)
        self._frame = None
        if frame.is_empty:
            return False
        self._session.history.execute(SetCrop(self._session.document.crop, frame))
        self._host.activate_tool(ToolId.SELECT)
        return True

    @override
    def cancel(self) -> bool:
        """Discard the frame."""
        had_frame = self._frame is not None
        self._frame = None
        self._origin = None
        return had_frame

    def _clamp(self, point: Point) -> Point:
        """Clamp ``point`` to the current visible area."""
        crop = self._session.document.crop
        return Point(
            min(max(point.x, crop.left), crop.right),
            min(max(point.y, crop.top), crop.bottom),
        )


TOOL_TYPES: Final[tuple[type[Tool], ...]] = (
    SelectTool,
    CropTool,
    RectangleTool,
    EllipseTool,
    LineTool,
    ArrowTool,
    TextTool,
    CounterTool,
    HighlightTool,
    ObfuscateTool,
    FreehandTool,
)
