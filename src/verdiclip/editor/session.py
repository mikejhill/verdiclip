"""Widget-free editor state: document, history, selection, and tool styles."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from enum import StrEnum
from typing import cast

from verdiclip.document.annotations import Annotation, JsonValue
from verdiclip.document.codec import AnnotationCodec
from verdiclip.document.commands import (
    AddAnnotations,
    RemoveAnnotations,
    ReorderAnnotations,
    ReplaceAnnotations,
)
from verdiclip.document.document import Document
from verdiclip.document.history import History
from verdiclip.document.style import HIGHLIGHT_YELLOW, TRANSPARENT, WHITE, Style
from verdiclip.exceptions import CodecError
from verdiclip.geometry import Point
from verdiclip.settings import EditorSettings

PASTE_OFFSET = 10.0

type SelectionListener = Callable[[], None]
type StyleListener = Callable[["ToolId", Style], None]


class ToolId(StrEnum):
    """Every editor tool."""

    SELECT = "select"
    CROP = "crop"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    TEXT = "text"
    COUNTER = "counter"
    HIGHLIGHT = "highlight"
    OBFUSCATE = "obfuscate"
    FREEHAND = "freehand"


class Selection:
    """The set of selected annotation ids, in selection order."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._listeners: list[SelectionListener] = []

    @property
    def ids(self) -> tuple[str, ...]:
        """Return the selected ids."""
        return tuple(self._ids)

    def __contains__(self, annotation_id: object) -> bool:
        """Return True if ``annotation_id`` is selected."""
        return annotation_id in self._ids

    def __len__(self) -> int:
        """Return the number of selected annotations."""
        return len(self._ids)

    def subscribe(self, listener: SelectionListener) -> None:
        """Call ``listener`` whenever the selection changes."""
        self._listeners.append(listener)

    def set(self, ids: Iterable[str]) -> None:
        """Replace the selection."""
        new = list(dict.fromkeys(ids))
        if new != self._ids:
            self._ids = new
            self._notify()

    def toggle(self, annotation_id: str) -> None:
        """Add or remove one id."""
        if annotation_id in self._ids:
            self.set(i for i in self._ids if i != annotation_id)
        else:
            self.set([*self._ids, annotation_id])

    def clear(self) -> None:
        """Select nothing."""
        self.set([])

    def _notify(self) -> None:
        """Inform listeners."""
        for listener in list(self._listeners):
            listener()


class ToolStyles:
    """The style each tool uses for new annotations."""

    def __init__(
        self,
        settings: EditorSettings,
        remembered: Mapping[ToolId, Style] | None = None,
        on_change: StyleListener | None = None,
    ) -> None:
        base = Style(
            stroke=settings.stroke_color,
            fill=TRANSPARENT,
            width=settings.stroke_width,
            font_family=settings.font_family,
            font_size=settings.font_size,
        )
        self._styles: dict[ToolId, Style] = dict.fromkeys(ToolId, base)
        self._styles[ToolId.COUNTER] = replace(
            base, fill=settings.stroke_color, stroke=WHITE
        )
        self._styles[ToolId.HIGHLIGHT] = replace(base, fill=HIGHLIGHT_YELLOW)
        self._styles[ToolId.OBFUSCATE] = replace(base, width=12)
        self._styles.update(remembered or {})
        self._on_change = on_change

    def get(self, tool: ToolId) -> Style:
        """Return the style for ``tool``."""
        return self._styles[tool]

    def set(self, tool: ToolId, style: Style) -> None:
        """Set the style for ``tool`` and report it so it can be remembered."""
        if self._styles[tool] == style:
            return
        self._styles[tool] = style
        if self._on_change is not None:
            self._on_change(tool, style)


class EditorSession:
    """Everything an editor window edits, independent of widgets."""

    def __init__(
        self,
        document: Document,
        settings: EditorSettings,
        *,
        remembered: Mapping[ToolId, Style] | None = None,
        on_style_change: StyleListener | None = None,
    ) -> None:
        self._document = document
        self._history = History(document)
        self._selection = Selection()
        self._styles = ToolStyles(settings, remembered, on_style_change)
        self._codec = AnnotationCodec()
        document.subscribe(self._prune_selection)

    @property
    def document(self) -> Document:
        """Return the document."""
        return self._document

    @property
    def history(self) -> History:
        """Return the undo history."""
        return self._history

    @property
    def selection(self) -> Selection:
        """Return the selection."""
        return self._selection

    @property
    def styles(self) -> ToolStyles:
        """Return per-tool styles."""
        return self._styles

    def selected(self) -> list[Annotation]:
        """Return selected annotations in stacking order."""
        return [a for a in self._document.annotations if a.id in self._selection]

    # Selection-wide operations

    def select_all(self) -> None:
        """Select every visible annotation."""
        self._selection.set(a.id for a in self._document.visible_annotations)

    def delete_selected(self) -> bool:
        """Remove the selection; return True if anything was removed."""
        ids = self._selection.ids
        if not ids:
            return False
        self._history.execute(RemoveAnnotations(ids))
        return True

    def nudge_selected(self, delta: Point) -> bool:
        """Move the selection by ``delta``; consecutive nudges are one undo step."""
        before = self.selected()
        if not before:
            return False
        after = [a.translated(delta) for a in before]
        self._history.execute(
            ReplaceAnnotations(before, after, "Move", merge_key="nudge")
        )
        return True

    def restyle_selected(self, change: Callable[[Style], Style]) -> bool:
        """Apply ``change`` to every selected annotation's style."""
        before = self.selected()
        after = [a.with_style(change(a.style)) for a in before]
        changed = [(b, a) for b, a in zip(before, after, strict=True) if b != a]
        if not changed:
            return False
        self._history.execute(
            ReplaceAnnotations(
                [b for b, _ in changed], [a for _, a in changed], "Change style"
            )
        )
        return True

    def restack_selected(self, *, forward: bool) -> bool:
        """Move the selection one step up (``forward``) or down the stack."""
        order = [a.id for a in self._document.annotations]
        new = order[::-1] if forward else list(order)
        selected = set(self._selection.ids)
        for i in range(1, len(new)):
            if new[i] in selected and new[i - 1] not in selected:
                new[i - 1], new[i] = new[i], new[i - 1]
        if forward:
            new.reverse()
        if new == order:
            return False
        label = "Bring forward" if forward else "Send backward"
        self._history.execute(ReorderAnnotations(order, new, label))
        return True

    # Element clipboard

    def copy_selected(self) -> str | None:
        """Return the selection as JSON text, or None if nothing is selected."""
        selected = self.selected()
        if not selected:
            return None
        return json.dumps(self._codec.encode_many(selected))

    def paste(self, text: str) -> bool:
        """Add annotations from ``copy_selected`` text, offset and selected.

        Raises:
            CodecError: If ``text`` is not valid annotation JSON.
        """
        try:
            # json.loads only ever produces JSON-compatible values
            data = cast("JsonValue", json.loads(text))
        except json.JSONDecodeError as err:
            msg = f"Clipboard does not hold VerdiClip annotations: {err}"
            raise CodecError(msg) from err
        decoded = self._codec.decode_many(data)
        if not decoded:
            return False
        offset = Point(PASTE_OFFSET, PASTE_OFFSET)
        pasted = [a.with_new_id().translated(offset) for a in decoded]
        self._history.execute(AddAnnotations(pasted, "Paste"))
        self._selection.set(a.id for a in pasted)
        return True

    # Internals

    def _prune_selection(self) -> None:
        """Drop selected ids that are no longer in the document."""
        present = {a.id for a in self._document.annotations}
        if any(i not in present for i in self._selection.ids):
            self._selection.set(i for i in self._selection.ids if i in present)
