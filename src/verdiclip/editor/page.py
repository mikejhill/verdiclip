"""A movable document view whose tools never retain its former host window."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPointF, QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from verdiclip.document.admission import CaptureProvenance
from verdiclip.document.annotations import CounterMarker, LabeledBox, TextNote
from verdiclip.editor.canvas import CanvasView
from verdiclip.editor.jobs import DocumentJobs
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.tools import TOOL_TYPES, Tool
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.render.renderer import Renderer


@dataclass(slots=True)
class DocumentRecord:
    """Application-owned editing and delivery state, independent of a window."""

    session: EditorSession
    delivery: ImageDelivery
    title: str = "Screenshot"
    saved_path: Path | None = None
    copied: bool = False
    identifier: str = field(default_factory=lambda: uuid4().hex)
    operations: int = 0
    closing: bool = False
    provenance: CaptureProvenance | None = None
    jobs: DocumentJobs = field(init=False)

    def __post_init__(self) -> None:
        """Give every document its own generation-checked job registry."""
        self.jobs = DocumentJobs(self.identifier, self.session.history)

    @property
    def label(self) -> str:
        """Return a full tab title that distinguishes delivery from saving."""
        name = self.saved_path.name if self.saved_path is not None else self.title
        return name if self.session.history.is_delivered else f"{name} •"


class DocumentPage(QWidget):
    """Keep the canvas, viewport, gestures and ToolHost attached to one session."""

    changed = Signal()
    tool_changed = Signal()

    def __init__(self, record: DocumentRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record = record
        self.renderer = Renderer()
        self.canvas = CanvasView(record.session, self.renderer, self)
        self.tools: dict[ToolId, Tool] = {
            tool.tool_id: tool(record.session, self) for tool in TOOL_TYPES
        }
        self.initialized = False
        self._center: Point | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.setFocusProxy(self.canvas)
        record.session.history.subscribe(self._notify)
        record.session.selection.subscribe(self._notify)
        self.activate_tool(ToolId.SELECT)

    def _notify(self) -> None:
        """Notify the current host without capturing any particular window."""
        self.changed.emit()

    def present(self) -> None:
        """Fit a new page only once, preserving zoom on subsequent moves."""
        if not self.initialized:
            self.initialized = True
            self.canvas.show_initial()
        if self._center is not None:
            center, self._center = self._center, None
            QTimer.singleShot(0, self, lambda: self.canvas.center_on(center))

    def remember_viewport(self) -> None:
        """Retain image coordinates instead of host-specific scrollbar values."""
        self._center = self.canvas.to_image(
            QPointF(self.canvas.viewport().rect().center())
        )

    def suspend(self) -> None:
        """Commit typing and stop previews while keeping committed work."""
        self.canvas.suspend()

    def dispose(self) -> None:
        """Disconnect model listeners before the final view is destroyed."""
        self.record.session.history.unsubscribe(self._notify)
        self.record.session.selection.unsubscribe(self._notify)
        self.canvas.dispose()

    def activate_tool(self, tool: ToolId) -> None:
        """Implement ToolHost without a reference to an editor window."""
        self.canvas.set_tool(self.tools[tool])
        self.tool_changed.emit()

    def edit_text(
        self, note: TextNote | None, at: Point, box: Rect | None = None
    ) -> None:
        """Open inline text in this document even after a window transfer."""
        self.canvas.editors.edit_text(note, at, box)

    def edit_counter(self, marker: CounterMarker) -> None:
        """Edit this document's counter label."""
        self.canvas.editors.edit_counter(marker)

    def edit_box_text(self, box: LabeledBox) -> None:
        """Edit this document's box label."""
        self.canvas.editors.edit_box_text(box)
