"""Compose a standalone editor without a reverse window-to-workspace dependency."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from verdiclip.editor.contracts import EditorOptions
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.output.delivery import ImageDelivery


def create_editor_window(
    session: EditorSession | None,
    delivery: ImageDelivery,
    options: EditorOptions | None = None,
    parent: QWidget | None = None,
) -> EditorWindow:
    """Construct an owned standalone host, leaving presentation to the caller."""
    return EditorWindow(session, delivery, options, parent, workspace=Workspace())
