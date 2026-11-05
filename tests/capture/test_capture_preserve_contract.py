"""The same public capture contract fails on the former popup-closing service."""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QEvent, QObject, QPoint
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip.capture.service import CaptureService


class PreservationEvents(QObject):
    """Observe only the pre-existing fixture, excluding newly owned overlays."""

    def __init__(self) -> None:
        super().__init__()
        self.dismissals: list[QEvent.Type] = []

    @override
    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            self.dismissals.append(event.type())
        return False


def test_public_capture_never_dismisses_existing_popup(qtbot: QtBot) -> None:
    """A completed screenshot must preserve the visible popup and its state."""
    service = CaptureService(FakeDesktop(), FakeWindows())
    menu = QMenu()
    menu.addAction("Keep this menu and its selection")
    trace = PreservationEvents()
    menu.installEventFilter(trace)
    captures: list[object] = []
    service.captured.connect(captures.append)
    try:
        menu.popup(QPoint(80, 80))
        qtbot.waitUntil(menu.isVisible)
        service.capture_fullscreen()
        qtbot.waitUntil(lambda: bool(captures))
        assert menu.isVisible(), trace.dismissals
        assert not trace.dismissals
    finally:
        menu.removeEventFilter(trace)
        service.cancel()
        menu.close()
