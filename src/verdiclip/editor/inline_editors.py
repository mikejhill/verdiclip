"""In-place editors for text notes and counter labels on the canvas."""

from __future__ import annotations

import json
from bisect import bisect_left
from collections.abc import Callable
from typing import Final, Protocol, override

from PySide6.QtCore import QEvent, QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QKeyEvent,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextOption,
)
from PySide6.QtWidgets import QFrame, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from verdiclip.document.annotations import (
    Annotation,
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
from verdiclip.document.rich_text import (
    ATTRS,
    RichText,
    TextAttr,
    TextFormat,
    TextSpan,
    utf16_offsets,
)
from verdiclip.document.style import Style, VAlign
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.geometry import Point, Rect
from verdiclip.render.renderer import (
    ELLIPSE_LABEL_INSET,
    H_ALIGN,
    V_SHARE,
    QtConvert,
    Renderer,
)

EDITOR_FRAME: Final = 1  # Editor border width in screen pixels
FORMAT_KEYS: Final = {
    QKeySequence.StandardKey.Bold: TextAttr.BOLD,
    QKeySequence.StandardKey.Italic: TextAttr.ITALIC,
    QKeySequence.StandardKey.Underline: TextAttr.UNDERLINE,
}

# Words copied from an inline editor, with their bold/italic/underline
RICH_TEXT_MIME: Final = "application/x-verdiclip-rich-text"

type FormatEditor = _TextBox | _BoxTextEdit
type FormatListener = Callable[[TextFormat | None], None]


class RichEditing:
    """Move bold, italic, and underline between spans and an editor's document."""

    @staticmethod
    def char_format(attr: TextAttr, *, on: bool) -> QTextCharFormat:
        """Return a format that sets only ``attr``."""
        char = QTextCharFormat()
        if attr is TextAttr.BOLD:
            weight = QFont.Weight.Bold if on else QFont.Weight.Normal
            char.setFontWeight(int(weight.value))
        elif attr is TextAttr.ITALIC:
            char.setFontItalic(on)
        else:
            char.setFontUnderline(on)
        return char

    @staticmethod
    def format_of(char: QTextCharFormat, base: TextFormat) -> TextFormat:
        """Return the format ``char`` gives text whose default is ``base``."""
        prop = QTextFormat.Property
        bold = base.bold
        if char.hasProperty(prop.FontWeight):
            bold = char.fontWeight() >= QFont.Weight.Bold.value
        italic = char.fontItalic() if char.hasProperty(prop.FontItalic) else base.italic
        underlined = char.hasProperty(prop.TextUnderlineStyle) or char.hasProperty(
            prop.FontUnderline
        )
        underline = char.fontUnderline() if underlined else base.underline
        return TextFormat(bold=bold, italic=italic, underline=underline)

    @staticmethod
    def load(
        document: QTextDocument, spans: tuple[TextSpan, ...], style: Style
    ) -> None:
        """Apply ``spans`` to the plain text already in ``document``."""
        text = document.toPlainText()
        runs = RichText.runs(text, spans, style)
        RichEditing.apply_runs(document, 0, runs, text, TextFormat.of(style))

    @staticmethod
    def apply_runs(
        document: QTextDocument,
        at: int,
        runs: list[tuple[int, int, TextFormat]],
        text: str,
        base: TextFormat | None = None,
    ) -> None:
        """Format ``text``, found at UTF-16 position ``at``, run by run.

        Runs equal to ``base`` are left alone; with no ``base`` every run is
        set explicitly (for pasted text, which must not take on the caret's).
        """
        offsets = utf16_offsets(text)
        for start, end, fmt in runs:
            if fmt == base:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(at + offsets[start])
            cursor.setPosition(at + offsets[end], QTextCursor.MoveMode.KeepAnchor)
            for attr in ATTRS:
                cursor.mergeCharFormat(RichEditing.char_format(attr, on=fmt.get(attr)))

    @staticmethod
    def unit_formats(document: QTextDocument, base: TextFormat) -> list[TextFormat]:
        """Return the format of each UTF-16 unit of the document's text."""
        result: list[TextFormat] = []
        block = document.begin()
        first = True
        while block.isValid():
            if not first:
                # The line break before this block takes the format before it
                result.append(result[-1] if result else base)
            first = False
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = RichEditing.format_of(fragment.charFormat(), base)
                result.extend([fmt] * fragment.length())
                iterator += 1
            block = block.next()
        return result

    @staticmethod
    def formats(document: QTextDocument, style: Style) -> list[TextFormat]:
        """Return the format of each character of ``document.toPlainText()``."""
        units = RichEditing.unit_formats(document, TextFormat.of(style))
        offsets = utf16_offsets(document.toPlainText())
        return [units[offsets[i]] for i in range(len(offsets) - 1)]

    @staticmethod
    def selected(editor: FormatEditor) -> tuple[int, int]:
        """Return the selection as Python character indexes into the text."""
        cursor = editor.textCursor()
        offsets = utf16_offsets(editor.toPlainText())
        return (
            bisect_left(offsets, cursor.selectionStart()),
            bisect_left(offsets, cursor.selectionEnd()),
        )

    @staticmethod
    def at_cursor(editor: FormatEditor, base: TextFormat) -> TextFormat:
        """Return the selection's format (on only if on throughout) or the caret's.

        Line breaks don't count: they show nothing to format.
        """
        cursor = editor.textCursor()
        if not cursor.hasSelection():
            return RichEditing.format_of(editor.currentCharFormat(), base)
        text = editor.toPlainText()
        formats = RichEditing.formats(editor.document(), editor.style_of_text)
        start, end = RichEditing.selected(editor)
        shown = [i for i in range(start, end) if text[i] != "\n"]
        chosen = [formats[i] for i in shown] or [base]
        return TextFormat(**{a.value: all(f.get(a) for f in chosen) for a in ATTRS})

    @staticmethod
    def copy(editor: FormatEditor, mime: QMimeData) -> QMimeData:
        """Return ``mime`` plus the selected words' formats.

        Qt's own selection data lists only its built-in formats, so its
        contents move to a plain QMimeData that can list one more.
        """
        copied = QMimeData()
        for kind in mime.formats():
            copied.setData(kind, mime.data(kind))
        mime = copied
        start, end = RichEditing.selected(editor)
        text = editor.toPlainText()[start:end]
        formats = RichEditing.formats(editor.document(), editor.style_of_text)
        runs = [
            [s, e, f.bold, f.italic, f.underline]
            for s, e, f in RichText.runs(
                text, RichText.spans(formats[start:end], Style()), Style()
            )
        ]
        payload = json.dumps({"text": text, "runs": runs})
        mime.setData(RICH_TEXT_MIME, payload.encode("utf-8"))
        return mime

    @staticmethod
    def paste(editor: FormatEditor, source: QMimeData) -> bool:
        """Insert words copied from an inline editor with their formats.

        Returns False when ``source`` holds no such words.
        """
        if not source.hasFormat(RICH_TEXT_MIME):
            return False
        try:
            payload = json.loads(bytes(source.data(RICH_TEXT_MIME).data()))
            text = str(payload["text"])
            runs = [
                (int(s), int(e), TextFormat(bool(b), bool(i), bool(u)))
                for s, e, b, i, u in payload["runs"]
            ]
        except (ValueError, KeyError, TypeError):
            return False
        if any(not 0 <= s < e <= len(text) for s, e, _ in runs):
            return False
        cursor = editor.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        at = cursor.position()
        cursor.insertText(text)
        RichEditing.apply_runs(editor.document(), at, runs, text)
        cursor.endEditBlock()
        editor.setTextCursor(cursor)
        return True

    @staticmethod
    def toggle(editor: FormatEditor, attr: TextAttr, *, on: bool | None = None) -> None:
        """Switch ``attr`` for the selection, or for what is typed next.

        ``on`` None flips it: on unless the selection already has it throughout.
        """
        base = TextFormat.of(editor.style_of_text)
        if on is None:
            on = not RichEditing.at_cursor(editor, base).get(attr)
        editor.mergeCurrentCharFormat(RichEditing.char_format(attr, on=on))

    @staticmethod
    def format_key(editor: FormatEditor, e: QKeyEvent) -> bool:
        """Handle Ctrl+B, Ctrl+I, or Ctrl+U; return True if ``e`` was one."""
        for key, attr in FORMAT_KEYS.items():
            if e.matches(key):
                RichEditing.toggle(editor, attr)
                return True
        return False

    @staticmethod
    def is_format_key(e: QEvent) -> bool:
        """True for a shortcut override of a format key, which editors keep."""
        return (
            e.type() == QEvent.Type.ShortcutOverride
            and isinstance(e, QKeyEvent)
            and any(e.matches(key) for key in FORMAT_KEYS)
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
    formatted = Signal()

    def __init__(self, parent: QWidget, style: Style) -> None:
        super().__init__(parent)
        self.style_of_text = style

    @override
    def createMimeDataFromSelection(self) -> QMimeData:
        """Copy the selected words with their formats."""
        return RichEditing.copy(self, super().createMimeDataFromSelection())

    @override
    def insertFromMimeData(self, source: QMimeData) -> None:
        """Paste copied words with their formats; anything else as plain text."""
        if not RichEditing.paste(self, source):
            super().insertFromMimeData(source)

    @override
    def event(self, e: QEvent) -> bool:
        """Keep Ctrl+B, Ctrl+I, and Ctrl+U for formatting the text."""
        if RichEditing.is_format_key(e):
            e.accept()
            return True
        return super().event(e)

    @override
    def keyPressEvent(self, e: QKeyEvent) -> None:
        """Finish on Esc; format on Ctrl+B/I/U; otherwise edit normally."""
        if e.key() == Qt.Key.Key_Escape:
            self.finished.emit()
            return
        if RichEditing.format_key(self, e):
            self.formatted.emit()
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
    formatted = Signal()

    def __init__(self, parent: QWidget, style: Style) -> None:
        super().__init__(parent)
        self.style_of_text = style

    @override
    def createMimeDataFromSelection(self) -> QMimeData:
        """Copy the selected words with their formats."""
        return RichEditing.copy(self, super().createMimeDataFromSelection())

    @override
    def insertFromMimeData(self, source: QMimeData) -> None:
        """Paste copied words with their formats; anything else as plain text."""
        if not RichEditing.paste(self, source):
            super().insertFromMimeData(source)

    @override
    def event(self, e: QEvent) -> bool:
        """Let Undo/Redo reach the document when the label has nothing to undo.

        The editor opens automatically after drawing a box, so Ctrl+Z must
        still undo the box itself rather than silently doing nothing. Ctrl+B,
        Ctrl+I, and Ctrl+U stay here to format the text.
        """
        if RichEditing.is_format_key(e):
            e.accept()
            return True
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
        """Finish on Esc; format on Ctrl+B/I/U; else edit (Enter adds a line)."""
        if e.key() == Qt.Key.Key_Escape:
            self.finished.emit()
            return
        if RichEditing.format_key(self, e):
            self.formatted.emit()
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
        self._fixed: Rect | None = None
        self._anchor = Point(0, 0)
        self._style = Style()
        self._listener: FormatListener | None = None

    def set_format_listener(self, listener: FormatListener | None) -> None:
        """Report the format at the caret while typing, and None when done."""
        self._listener = listener

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
    def new_box(self) -> TextNote | None:
        """Return the empty box being typed into for a new fixed-size note."""
        if self._widget is None or self._note is not None or self._fixed is None:
            return None
        return TextNote(rect=self._fixed, text="", style=self._style, auto_fit=False)

    @property
    def is_open(self) -> bool:
        """True while an editor is showing."""
        return self._widget is not None

    @property
    def widget(self) -> QWidget | None:
        """Return the open editor widget, if any."""
        return self._widget

    def edit_text(
        self, note: TextNote | None, at: Point, box: Rect | None = None
    ) -> None:
        """Open a text editor for ``note``, or for a new note at ``at``.

        A new note with ``box`` (or a fixed-size ``note``) is edited inside its
        box, wrapped and aligned as it will be drawn.
        """
        self.commit()
        if note is not None:
            # The style bar acts on the selection, so it must be this note
            self._session.selection.set([note.id])
        self._note = note
        self._anchor = note.rect.top_left if note is not None else at
        self._style = (
            note.style if note is not None else self._session.styles.get(ToolId.TEXT)
        )
        if note is not None:
            self._fixed = None if note.auto_fit else note.rect
        else:
            self._fixed = box
        if self._fixed is not None:
            # No placeholder: the dashed box already shows where text goes,
            # and a hint would clip in a small box
            editor = self._wrapping_editor(
                note.text if note is not None else "",
                note.spans if note is not None else (),
                H_ALIGN[self._style.h_align],
            )
            self._open(editor, transparent=True)
            editor.moveCursor(QTextCursor.MoveOperation.End)
            editor.document().clearUndoRedoStacks()
            return
        text_box = _TextBox(self._canvas.viewport(), self._style)
        text_box.setFrameShape(QFrame.Shape.NoFrame)
        text_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if note is not None:
            text, spans = RichText.plain_lines(note.text, note.spans, self._style)
            text_box.setPlainText(text)
            RichEditing.load(text_box.document(), spans, self._style)
        text_box.document().clearUndoRedoStacks()
        text_box.textChanged.connect(self.reposition)
        text_box.cursorPositionChanged.connect(self._report_format)
        text_box.selectionChanged.connect(self._report_format)
        text_box.formatted.connect(self._report_format)
        text_box.document().contentsChanged.connect(self._report_format)
        text_box.finished.connect(self.commit)
        self._open(text_box)
        text_box.moveCursor(text_box.textCursor().MoveOperation.End)

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
        editor = self._wrapping_editor(
            box.text,
            box.spans,
            Qt.AlignmentFlag.AlignHCenter,
            "Type a label (Esc to skip)",
        )
        editor.selectAll()
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
        if isinstance(widget, _BoxTextEdit) and self._box is not None:
            self._commit_box(*self._typed(widget, strip_start=True))
        elif isinstance(widget, (_TextBox, _BoxTextEdit)):
            self._commit_text(*self._typed(widget, strip_start=False))
        elif isinstance(widget, _LabelBox):
            self._commit_label(widget.text().strip())
        self._note = None
        self._marker = None
        self._box = None
        self._fixed = None
        widget.hide()
        widget.deleteLater()
        if self._listener is not None:
            self._listener(None)
        self._canvas.setFocus()
        self._canvas.refresh()

    def apply_format(self, attr: TextAttr, *, on: bool) -> bool:
        """Format the selected words in the open editor; False if none are selected.

        Without a selection the style bar formats the whole annotation instead.
        """
        widget = self._widget
        if not isinstance(widget, (_TextBox, _BoxTextEdit)):
            return False
        if not widget.textCursor().hasSelection():
            return False
        RichEditing.toggle(widget, attr, on=on)
        self._report_format()
        return True

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
            text, spans = self._typed(widget, strip_start=False, strip_end=False)
            w, h = Renderer.measure_text(text + "  ", self._style, spans)
            top_left = self._canvas.to_view(self._anchor)
            inset = Renderer.text_inset(self._style)
            widget.document().setDocumentMargin(inset * zoom)
            widget.setGeometry(
                round(top_left.x()),
                round(top_left.y()),
                round(w * zoom) + 8,
                round(h * zoom) + 8,
            )
        elif isinstance(widget, _BoxTextEdit):
            self._place_box_editor()
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

    def _wrapping_editor(
        self,
        text: str,
        spans: tuple[TextSpan, ...],
        align: Qt.AlignmentFlag,
        placeholder: str = "",
    ) -> _BoxTextEdit:
        """Return a frameless editor that wraps ``text`` inside a box."""
        editor = _BoxTextEdit(self._canvas.viewport(), self._style)
        editor.setFrameShape(QFrame.Shape.NoFrame)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText(placeholder)
        text, spans = RichText.plain_lines(text, spans, self._style)
        editor.setPlainText(text)
        RichEditing.load(editor.document(), spans, self._style)
        option = editor.document().defaultTextOption()
        option.setAlignment(align)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        editor.document().setDefaultTextOption(option)
        editor.textChanged.connect(self._place_box_editor)
        editor.cursorPositionChanged.connect(self._report_format)
        editor.selectionChanged.connect(self._report_format)
        editor.formatted.connect(self._report_format)
        editor.document().contentsChanged.connect(self._report_format)
        editor.finished.connect(self.commit)
        return editor

    def _typed(
        self, widget: FormatEditor, *, strip_start: bool, strip_end: bool = True
    ) -> tuple[str, tuple[TextSpan, ...]]:
        """Return the editor's text, trimmed as it is committed, and its spans."""
        text = widget.toPlainText()
        start = len(text) - len(text.lstrip()) if strip_start else 0
        end = len(text.rstrip()) if strip_end else len(text)
        end = max(start, end)
        formats = RichEditing.formats(widget.document(), self._style)
        return text[start:end], RichText.spans(formats[start:end], self._style)

    def _report_format(self) -> None:
        """Tell the listener the format at the caret or of the selection."""
        widget = self._widget
        if self._listener is None or not isinstance(widget, (_TextBox, _BoxTextEdit)):
            return
        self._listener(RichEditing.at_cursor(widget, TextFormat.of(self._style)))

    def _place_box_editor(self) -> None:
        """Size the box editor to its wrapped text and align it in the box."""
        widget = self._widget
        if not isinstance(widget, _BoxTextEdit):
            return
        if self._box is not None:
            box = self._box
            inset = 0.0 if isinstance(box, RectangleShape) else ELLIPSE_LABEL_INSET
            area = self._canvas.rect_to_view(Renderer.label_area(box, inset=inset))
            share = V_SHARE[VAlign.MIDDLE]
        elif self._fixed is not None:
            inner = self._fixed.adjusted(-Renderer.text_inset(self._style))
            area = self._canvas.rect_to_view(inner)
            share = V_SHARE[self._style.v_align]
        else:  # pragma: no cover - a wrapping editor always has a box
            return
        widget.document().setDocumentMargin(0)
        widget.document().setTextWidth(max(1.0, area.width()))
        line = widget.fontMetrics().lineSpacing()
        height = min(area.height(), max(line, widget.document().size().height()))
        top = area.top() + (area.height() - height) * share
        # The dashed frame sits outside the area so text wraps at the same
        # width it is drawn with
        widget.setGeometry(
            round(area.left()) - EDITOR_FRAME,
            round(top) - EDITOR_FRAME,
            round(area.width()) + 2 * EDITOR_FRAME,
            round(height) + 2 + 2 * EDITOR_FRAME,
        )

    def _open(self, widget: QWidget, *, transparent: bool = False) -> None:
        """Show ``widget`` styled for the current annotation and focus it."""
        color = QtConvert.color(self._style.stroke).name()
        background = "transparent" if transparent else "rgba(255,255,255,0.85)"
        widget.setStyleSheet(
            f"background: {background}; color: {color};"
            f" border: {EDITOR_FRAME}px dashed #0078d7;"
        )
        self._widget = widget
        self.reposition()
        widget.show()
        widget.setFocus()
        self._canvas.refresh()

    def _current[T: Annotation](self, edited: T | None) -> T | None:
        """Return the document's latest version of ``edited``.

        The style bar can change an annotation while its editor is open (for
        example Auto-fit), so commits build on that, not the opening snapshot.
        """
        if edited is None:
            return None
        current = self._session.document.find(edited.id)
        return current if isinstance(current, type(edited)) else None

    def _commit_text(self, text: str, spans: tuple[TextSpan, ...]) -> None:
        """Add, change, or remove the note.

        A box drawn by dragging is kept even when empty, so finishing without
        typing leaves the box; only text that fits itself vanishes when empty.
        """
        history = self._session.history
        note = self._current(self._note)
        if note is None and self._note is not None:
            return  # Deleted (e.g. undone) while being edited
        if note is None:
            if not text and self._fixed is None:
                return
            if self._fixed is not None:
                created = TextNote(
                    rect=self._fixed,
                    text=text,
                    style=self._style,
                    auto_fit=False,
                    spans=spans,
                )
            else:
                w, h = Renderer.measure_text(text, self._style, spans)
                created = TextNote(
                    rect=Rect(self._anchor.x, self._anchor.y, w, h),
                    text=text,
                    style=self._style,
                    spans=spans,
                )
            history.execute(AddAnnotations([created], "Add text"))
            self._session.selection.set([created.id])
            return
        if not text and note.auto_fit:
            history.execute(RemoveAnnotations([note.id], "Delete text"))
            return
        edited = note.with_text(text, note.rect, spans)
        if (edited.text, edited.spans) != (note.text, note.spans):
            history.execute(
                ReplaceAnnotations([note], [Renderer.fit(edited)], "Edit text")
            )

    def _commit_box(self, text: str, spans: tuple[TextSpan, ...]) -> None:
        """Change the box's label if it changed (empty text clears it)."""
        box = self._current(self._box)
        if box is None:
            return
        edited = box.with_text(text, spans)
        if (edited.text, edited.spans) == (box.text, box.spans):
            return
        self._session.history.execute(ReplaceAnnotations([box], [edited], "Edit label"))

    def _commit_label(self, label: str) -> None:
        """Relabel the counter if the label changed and is not empty."""
        marker = self._current(self._marker)
        if marker is None or not label or label == marker.label:
            return
        self._session.history.execute(
            ReplaceAnnotations([marker], [marker.with_label(label)], "Edit counter")
        )
