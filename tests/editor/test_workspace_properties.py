"""Generated document transitions preserve ownership and delivered edits."""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions
from verdiclip.editor.workspace import Workspace
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(st.tuples(st.integers(0, 2), st.integers(0, 2)), min_size=1, max_size=25)
)
def test_generated_moves_never_duplicate_or_lose_sessions(
    qapp: QApplication, tmp_path: Path, operations: list[tuple[int, int]]
) -> None:
    """Every operation leaves one visible host per stable document identity."""
    owner = Workspace()
    image = QImage(60, 40, QImage.Format.Format_ARGB32)
    image.fill(0xFF123456)
    delivery = ImageDelivery(OutputSettings(directory=tmp_path))
    first = owner.open_document(
        EditorSession(Document(image), EditorSettings()),
        delivery,
        EditorOptions(title="A"),
    )
    for name in ("B", "C"):
        owner.open_document(
            EditorSession(Document(image), EditorSettings()),
            delivery,
            EditorOptions(title=name),
        )
    pages = first.pages
    identities = {page.record.identifier for page in pages}
    try:
        for item, destination in operations:
            page = pages[item]
            source = next(window for window in owner.windows if page in window.pages)
            if destination == 2:
                assert owner.move(source, page)
            else:
                target = owner.windows[destination % len(owner.windows)]
                assert owner.move(source, page, target, destination)
            attached = [page for window in owner.windows for page in window.pages]
            assert len(attached) == 3
            assert {page.record.identifier for page in attached} == identities
            assert len({id(page.record.session) for page in attached}) == 3
            assert {record.identifier for record in owner.documents} == identities
            qapp.processEvents()
    finally:
        assert owner.close_all()
        owner.parking.deleteLater()
        qapp.processEvents()
