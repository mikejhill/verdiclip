"""In-place editors for text notes and counter labels on the canvas."""

from __future__ import annotations

from typing import Protocol, override

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QFocusEvent,
    QKeyEvent,
    QKeySequence,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import QFrame, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from verdiclip.document.annotations import (
    CounterMarker,
    LabeledBox,
    RectangleShape,
    TextNote,
)
from verdiclip.document.commands import (
    AddAnnotations,
    RemoveAnnotations,
    ReplaceAnnotations,
)
from verdiclip.document.style import Style
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.geometry import Point, Rect
from verdiclip.render.renderer import (
    ELLIPSE_LABEL_INSET,
    TEXT_PADDING,
    QtConvert,
    Renderer,
)


class CanvasHost(Protocol):
    """The canvas services the in-place editors need."""

    @property
    def zoom(self) -> float:
        """Current zoom factor."""

    def viewport(self) -> QWidget:
        """Widget the editors are placed on."""

    def to_view(self, point: Point) -> QPointF:
        """Map image coordinates to the viewport."""

    def rect_to_view(self, rect: Rect) -> QRectF:
        """Map an image rectangle to the viewport."""

    def refresh(self) -> None:
        """Repaint the canvas."""

    def setFocus(self) -> None:  # noqa: N802 — Qt method name
        """Give the canvas keyboard focus."""


class _TextBox(QPlainTextEdit):
    """A frameless multi-line editor that finishes on Esc or focus loss."""

    finished = Signal()

    @override
    def keyPressEvent(self, e: QKeyEvent) -> None:
        """Finish on Esc; otherwise edit normally."""
        if e.key() == Qt.Key.Key_Escape:
            self.finished.emit()
            return
        super().keyPressEvent(e)

    @override
    def focusOutEvent(self, e: QFocusEvent) -> None:
        """Finish when focus moves elsewhere."""
        super().focusOutEvent(e)
        self.finished.emit()


class _BoxTextEdit(QTextEdit):
    """Centered, wrapping editor for a box label; finishes on Esc or focus loss."""

    finished = Signal()

    @override
    def event(self, e: QEvent) -> bool:
        """Let Undo/Redo reach the document when the label has nothing to undo.

        The editor opens automatically after drawing a box, so Ctrl+Z must
        still undo the box itself rather than silently doing nothing.
        """
        if e.type() == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent):
            doc = self.document()
            passthrough = (
                e.matches(QKeySequence.StandardKey.Undo) and not doc.isUndoAvailable()
            ) or (
                e.matches(QKeySequence.StandardKey.Redo) and not doc.isRedoAvailable()
            )
            if passthrough:
                e.ignore()
                return False
        return super().event(e)

    @override
    def keyPressEvent(self, e: QKeyEvent) -> None:
        """Finish on Esc; otherwise edit normally (Enter adds a line)."""
        if e.key() == Qt.Key.Key_Escape:
            self.finished.emit()
            return
        super().keyPressEvent(e)

    @override
    def focusOutEvent(self, e: QFocusEvent) -> None:
        """Finish when focus moves elsewhere."""
        super().focusOutEvent(e)
        self.finished.emit()


class _LabelBox(QLineEdit):
    """A one-line editor that finishes on Enter, Esc, or focus loss."""

    finished = Signal()

    @override
    def keyPressEvent(self, arg__1: QKeyEvent) -> None:
        """Finish on Esc or Enter; otherwise edit normally."""
        if arg__1.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finished.emit()
            return
        super().keyPressEvent(arg__1)

    @override
    def focusOutEvent(self, arg__1: QFocusEvent) -> None:
        """Finish when focus moves elsewhere."""
        super().focusOutEvent(arg__1)
        self.finished.emit()


class InlineEditors:
    """Owns at most one open in-place editor and commits it as a command."""

    def __init__(self, canvas: CanvasHost, session: EditorSession) -> None:
        self._canvas = canvas
        self._session = session
        self._widget: QWidget | None = None
        self._note: TextNote | None = None
        self._marker: CounterMarker | None = None
        self._box: LabeledBox | None = None
        self._anchor = Point(0, 0)
        self._style = Style()

    @property
    def editing_id(self) -> str | None:
        """Return the id of the annotation being edited (hidden on canvas)."""
        if self._note is not None:
            return self._note.id
        if self._marker is not None:
            return self._marker.id
        if self._box is not None:
            return self._box.id
        return None

    @property
    def is_open(self) -> bool:
        """True while an editor is showing."""
        return self._widget is not None

    @property
    def widget(self) -> QWidget | None:
        """Return the open editor widget, if any."""
        return self._widget

    def edit_text(self, note: TextNote | None, at: Point) -> None:
        """Open a text editor for ``note``, or for a new note at ``at``."""
        self.commit()
        self._note = note
        self._anchor = note.rect.top_left if note is not None else at
        self._style = (
            note.style if note is not None else self._session.styles.get(ToolId.TEXT)
        )
        box = _TextBox(self._canvas.viewport())
        box.setFrameShape(QFrame.Shape.NoFrame)
        box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.setPlainText(note.text if note is not None else "")
        box.textChanged.connect(self.reposition)
        box.finished.connect(self.commit)
        self._open(box)
        box.moveCursor(box.textCursor().MoveOperation.End)

    def edit_counter(self, marker: CounterMarker) -> None:
        """Open a label editor centered on ``marker``."""
        self.commit()
        self._marker = marker
        self._style = marker.style
        box = _LabelBox(self._canvas.viewport())
        box.setText(marker.label)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.selectAll()
        box.finished.connect(self.commit)
        self._open(box)

    def edit_box_text(self, box: LabeledBox) -> None:
        """Open a centered editor inside ``box`` for its label."""
        self.commit()
        self._box = box
        self._style = box.style
        editor = _BoxTextEdit(self._canvas.viewport())
        editor.setFrameShape(QFrame.Shape.NoFrame)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText("Type a label (Esc to skip)")
        editor.setPlainText(box.text)
        option = editor.document().defaultTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        editor.document().setDefaultTextOption(option)
        editor.selectAll()
        editor.textChanged.connect(self._center_box_editor)
        editor.finished.connect(self.commit)
        self._open(editor, transparent=True)
        editor.moveCursor(QTextCursor.MoveOperation.End)
        # Setup is not the user's typing; keep it out of the editor's undo history
        editor.document().clearUndoRedoStacks()

    def commit(self) -> None:
        """Close the open editor and record its change, if any."""
        widget = self._widget
        if widget is None:
            return
        self._widget = None
        if isinstance(widget, _TextBox):
            self._commit_text(widget.toPlainText().rstrip())
        elif isinstance(widget, _BoxTextEdit):
            self._commit_box(widget.toPlainText().strip())
        elif isinstance(widget, _LabelBox):
            self._commit_label(widget.text().strip())
        self._note = None
        self._marker = None
        self._box = None
        widget.hide()
        widget.deleteLater()
        self._canvas.setFocus()
        self._canvas.refresh()

    def reposition(self) -> None:
        """Move and size the editor to match the zoomed annotation."""
        widget = self._widget
        if widget is None:
            return
        zoom = self._canvas.zoom
        font = QtConvert.font(self._style)
        font.setPixelSize(max(1, round(self._style.font_size * zoom)))
        widget.setFont(font)
        if isinstance(widget, _TextBox):
            w, h = Renderer.measure_text(widget.toPlainText() + "  ", self._style)
            top_left = self._canvas.to_view(self._anchor)
            widget.document().setDocumentMargin(TEXT_PADDING * zoom)
            widget.setGeometry(
                round(top_left.x()),
                round(top_left.y()),
                round(w * zoom) + 8,
                round(h * zoom) + 8,
            )
        elif isinstance(widget, _BoxTextEdit):
            self._center_box_editor()
        elif self._marker is not None:
            center = self._canvas.to_view(self._marker.center)
            width = max(48.0, self._marker.radius * 3 * zoom)
            height = max(24.0, self._marker.radius * 1.4 * zoom)
            widget.setGeometry(
                round(center.x() - width / 2),
                round(center.y() - height / 2),
                round(width),
                round(height),
            )

    # Internals

    def _center_box_editor(self) -> None:
        """Size the box editor to its wrapped text and center it in the box."""
        widget, box = self._widget, self._box
        if not isinstance(widget, _BoxTextEdit) or box is None:
            return
        inset = 0.0 if isinstance(box, RectangleShape) else ELLIPSE_LABEL_INSET
        area = self._canvas.rect_to_view(Renderer.label_area(box, inset=inset))
        widget.document().setDocumentMargin(0)
        widget.document().setTextWidth(area.width())
        line = widget.fontMetrics().lineSpacing()
        height = min(area.height(), max(line, widget.document().size().height()))
        top = area.top() + (area.height() - height) / 2
        widget.setGeometry(
            round(area.left()), round(top), round(area.width()), round(height) + 2
        )

    def _open(self, widget: QWidget, *, transparent: bool = False) -> None:
        """Show ``widget`` styled for the current annotation and focus it."""
        color = QtConvert.color(self._style.stroke).name()
        background = "transparent" if transparent else "rgba(255,255,255,0.85)"
        widget.setStyleSheet(
            f"background: {background}; color: {color}; border: 1px dashed #0078d7;"
        )
        self._widget = widget
        self.reposition()
        widget.show()
        widget.setFocus()
        self._canvas.refresh()

    def _commit_text(self, text: str) -> None:
        """Add, change, or remove the note."""
        history = self._session.history
        note = self._note
        if note is None:
            if not text:
                return
            w, h = Renderer.measure_text(text, self._style)
            created = TextNote(
                rect=Rect(self._anchor.x, self._anchor.y, w, h),
                text=text,
                style=self._style,
            )
            history.execute(AddAnnotations([created], "Add text"))
            self._session.selection.set([created.id])
            return
        if not text:
            history.execute(RemoveAnnotations([note.id], "Delete text"))
            return
        if text != note.text:
            w, h = Renderer.measure_text(text, note.style)
            edited = note.with_text(text, Rect(note.rect.x, note.rect.y, w, h))
            history.execute(ReplaceAnnotations([note], [edited], "Edit text"))

    def _commit_box(self, text: str) -> None:
        """Change the box's label if it changed (empty text clears it)."""
        box = self._box
        if box is None or text == box.text:
            return
        self._session.history.execute(
            ReplaceAnnotations([box], [box.with_text(text)], "Edit label")
        )

    def _commit_label(self, label: str) -> None:
        """Relabel the counter if the label changed and is not empty."""
        marker = self._marker
        if marker is None or not label or label == marker.label:
            return
        self._session.history.execute(
            ReplaceAnnotations([marker], [marker.with_label(label)], "Edit counter")
        )
