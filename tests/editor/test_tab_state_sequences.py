"""Independent content-state oracle across generated cross-window transitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.jobs import JobKind, JobOutcome
from verdiclip.editor.page import DocumentPage
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions
from verdiclip.editor.workspace import Workspace
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings


@dataclass
class Oracle:
    """Track semantic states/counts without relying on production history tokens."""

    states: list[tuple[int, int]] = field(default_factory=lambda: [(0, 0)])
    cursor: int = 0
    delivered: int = 0
    serial: int = 0

    def edit(self, session: EditorSession) -> None:
        """Create a unique branch token and independent expected annotation count."""
        self.serial += 1
        count = self.states[self.cursor][1] + 1
        self.states[self.cursor + 1 :] = [(self.serial, count)]
        self.cursor += 1
        session.history.execute(
            AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Generated edit")
        )

    def check(self, session: EditorSession) -> None:
        """Delivery follows content through undo, redo and branch replacement."""
        token, count = self.states[self.cursor]
        assert len(session.document.annotations) == count
        assert session.history.is_delivered is (token == self.delivered)
        assert session.history.can_undo is (self.cursor > 0)
        assert session.history.can_redo is (self.cursor < len(self.states) - 1)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 2),
            st.sampled_from(
                [
                    "edit",
                    "undo",
                    "redo",
                    "delivered",
                    "detach",
                    "move",
                    "fail_move",
                    "cancel_close",
                    "job_move",
                    "job_stale",
                    "select_zoom",
                ]
            ),
        ),
        min_size=1,
        max_size=35,
    )
)
def test_generated_content_transfer_failure_and_job_sequences(  # noqa: C901, PLR0912, PLR0915 - generated transition oracle
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operations: list[tuple[int, str]],
) -> None:
    """Moves and cancellations preserve model state and exactly one view per record."""
    owner = Workspace()
    image = QImage(60, 40, QImage.Format.Format_ARGB32)
    image.fill(0xFF123456)
    delivery = ImageDelivery(OutputSettings(directory=tmp_path))
    for name in ("A", "B", "C"):
        owner.open_document(
            EditorSession(Document(image), EditorSettings()),
            delivery,
            EditorOptions(title=name),
        )
    pages = owner.windows[0].pages
    models = [Oracle() for _ in pages]
    identities = {page.record.identifier for page in pages}
    for page in pages:
        page.record.session.history.mark_delivered()
    try:
        for item, action in operations:
            page, model = pages[item], models[item]
            session = page.record.session
            selection, zoom = session.selection.ids, page.canvas.zoom
            host = owner.host_of(page.record.identifier)
            assert host is not None
            if action == "edit":
                model.edit(session)
            elif action == "undo" and model.cursor > 0:
                session.history.undo()
                model.cursor -= 1
            elif action == "redo" and model.cursor < len(model.states) - 1:
                session.history.redo()
                model.cursor += 1
            elif action == "delivered":
                session.history.mark_delivered()
                model.delivered = model.states[model.cursor][0]
            elif action == "select_zoom":
                if session.document.annotations:
                    session.selection.set([session.document.annotations[0].id])
                page.canvas.set_zoom(0.5 + 0.25 * (model.serial % 6))
            elif action in {"detach", "move", "job_move"}:
                ticket = page.record.jobs.begin(JobKind.ANALYSIS)
                target = None if action == "detach" else owner.windows[-1]
                assert owner.move(host, page, target, 0)
                assert page.record.jobs.finish(ticket) is JobOutcome.CURRENT
            elif action == "job_stale":
                ticket = page.record.jobs.begin(JobKind.ANALYSIS)
                model.edit(session)
                assert page.record.jobs.finish(ticket) is JobOutcome.STALE
            elif action == "fail_move":
                before, active = host.pages, host.session
                original = host.take_page

                def fail(
                    selected: DocumentPage,
                    serial: int = model.serial,
                    take: Callable[[DocumentPage], int] = original,
                ) -> int:
                    if serial % 2:
                        take(selected)
                    raise MemoryError("Generated attachment failure")

                with monkeypatch.context() as patch:
                    patch.setattr(host, "take_page", fail)
                    assert not owner.move(host, page)
                assert host.pages == before
                assert host.session is active
            elif action == "cancel_close" and not session.history.is_delivered:
                with monkeypatch.context() as patch:
                    patch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.CANCEL)
                    assert not owner.close_all()
            attached = [page for window in owner.windows for page in window.pages]
            if action in {"detach", "move", "job_move", "fail_move"}:
                assert session.selection.ids == selection
                assert page.canvas.zoom == zoom
            assert len(attached) == len(pages)
            assert {page.record.identifier for page in attached} == identities
            assert {record.identifier for record in owner.documents} == identities
            assert len({id(page.record.session) for page in attached}) == len(pages)
            for expected, retained in zip(models, pages, strict=True):
                expected.check(retained.record.session)
            qapp.processEvents()
    finally:
        assert owner.close_all()
        owner.parking.deleteLater()
        qapp.processEvents()
