"""Queued window deactivation must cancel arming before a late native release."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from tests.platform.test_tab_drag import Api

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions
from verdiclip.editor.workspace import Workspace
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.tab_drag import NativeTabDrag
from verdiclip.settings import EditorSettings, OutputSettings


def test_queued_deactivation_cancels_before_late_release_without_losing_work(
    qapp: QApplication,
    sample_image: QImage,
    tmp_path: Path,
) -> None:
    """Activation must settle before tests/real input start; cancellation is safe."""
    owner = Workspace()
    host = owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        ImageDelivery(OutputSettings(directory=tmp_path)),
        EditorOptions(title="Origin"),
    )
    page = host.pages[0]
    page.record.session.history.execute(
        AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Edit before drag")
    )
    original = page.record.session.history.state_id
    api = Api()
    driver = NativeTabDrag(host.tab_bar, api)
    host.tab_bar._driver = driver
    host.tab_bar._dragging = True
    released: list[QPoint] = []
    cancelled: list[bool] = []
    host.tab_bar.drag_released.connect(released.append)
    host.tab_bar.drag_cancelled.connect(lambda: cancelled.append(True))
    assert driver.begin(42)
    owner._start_drag(host, page.record.identifier)
    try:
        QTimer.singleShot(
            0,
            lambda: QApplication.sendEvent(host, QEvent(QEvent.Type.WindowDeactivate)),
        )
        QTimer.singleShot(
            0, lambda: driver.dispatch(42, 0x0202, position=QPoint(900, 600))
        )
        qapp.processEvents()
        assert cancelled == [True]
        assert released == []
        assert api.releases == [42]
        assert api.owner == 0
        assert not driver.active
        assert host.pages == (page,)
        assert owner.documents == (page.record,)
        assert page.record.session.history.state_id == original
        assert len(page.record.session.document.annotations) == 1
        assert not page.record.session.history.is_delivered
    finally:
        owner.cancel_drag()
        assert owner.close_all()
        owner.parking.deleteLater()
        qapp.processEvents()
