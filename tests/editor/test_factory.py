"""Standalone composition keeps owner lifetime separate from presentation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from verdiclip.document.document import Document
from verdiclip.editor.contracts import EditorOptions
from verdiclip.editor.factory import create_editor_window
from verdiclip.editor.session import EditorSession
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings


def test_standalone_factory_retains_one_document_without_showing(
    qtbot: QtBot, sample_image: QImage, tmp_path: Path
) -> None:
    """Construction never presents a window or weakens the document close policy."""
    session = EditorSession(Document(sample_image), EditorSettings())
    window = create_editor_window(
        session,
        ImageDelivery(OutputSettings(directory=tmp_path)),
        EditorOptions(title="Standalone capture", confirm_close=False),
    )
    qtbot.addWidget(window)
    owner = window.workspace
    try:
        assert not window.isVisible()
        assert window.session is session
        assert window.tab_bar.tabText(0) == "Standalone capture"
        assert not window.confirm_close
        assert owner.windows == (window,)
        assert owner.documents == (window.pages[0].record,)
        closed = window.close()
        assert closed
        assert not owner.windows
        assert not owner.documents
    finally:
        owner.close_all()
        owner.parking.deleteLater()
