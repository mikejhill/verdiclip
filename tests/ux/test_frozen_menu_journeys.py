"""Hotkey pixels and target geometry survive the normal popup input handoff."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import AppFactory
from tests.ux.test_app_journeys import make_app as _make_app
from tests.ux.test_capture_journeys import FakeDesktop

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import Capture
from verdiclip.capture.service import CaptureService
from verdiclip.geometry import Rect

pytestmark = pytest.mark.ux
make_app = _make_app


class MenuDesktop(FakeDesktop):
    """A controlled desktop containing the actual rendered Qt menu when visible."""

    def __init__(self, menu: QMenu) -> None:
        super().__init__()
        self.menu = menu
        self.observed: list[bool] = []
        self.last: FrozenScreen | None = None

    @override
    def freeze(self) -> FrozenScreen:
        """Render only visible menu pixels into the simulated desktop frame."""
        frozen = super().freeze()
        self.observed.append(self.menu.isVisible())
        if self.menu.isVisible():
            painter = QPainter(frozen.image)
            painter.drawPixmap(self.menu.pos(), self.menu.grab())
            painter.end()
        self.last = frozen
        return frozen


@dataclass(slots=True, weakref_slot=True)
class MovingTarget:
    """A window provider whose values can change during menu dismissal."""

    title: str = "Before dismissal"
    bounds: Rect = field(default_factory=lambda: Rect(70, 70, 300, 200))

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[MovingTarget]:
        """Return the same live provider, not a fresh immutable target."""
        del exclude
        return [self]

    def foreground(self) -> MovingTarget:
        """Return current foreground metadata."""
        return self

    def move(self) -> None:
        """Change geometry and title as a real popup dismissal might."""
        self.title = "After dismissal"
        self.bounds = Rect(500, 400, 100, 100)


def select_menu(service: CaptureService) -> None:
    """Select menu pixels through the overlay's ordinary Qt window input."""
    window = service.overlays[0].windowHandle()
    assert window is not None
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(90, 90))
    QTest.mouseMove(window, QPoint(220, 145))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(220, 145))


@pytest.mark.parametrize("action", ["region", "window", "fullscreen", "repeat"])
def test_hotkey_retains_menu_pixels_and_original_target(
    qtbot: QtBot, make_app: AppFactory, action: str
) -> None:
    """UX-CAP-16: every hotkey keeps visible menu pixels, then gives selection input."""
    menu = QMenu()
    menu.addAction("Menu pixels to retain")
    menu.addAction("Another visible choice")
    menu.setStyleSheet("QMenu { background: #e020a0; color: white; }")
    source, target = MenuDesktop(menu), MovingTarget()
    app = make_app(screens=source, windows=target)
    service = app.controller.capture
    outputs: list[Capture] = []
    service.captured.connect(outputs.append)
    menu.aboutToHide.connect(target.move)
    try:
        menu.popup(QPoint(80, 80))
        qtbot.waitUntil(menu.isVisible)
        app.press_hotkey(action)
        assert source.observed == [True]
        assert not menu.isVisible()
        assert QApplication.activePopupWidget() is None
        expected = source.last
        assert expected is not None
        # A definite menu-only color proves this is not merely a blank frame.
        assert expected.image.pixelColor(85, 85) == QColor("#e020a0")
        if action in {"region", "repeat"}:
            assert service.overlays
            select_menu(service)
        qtbot.waitUntil(lambda: bool(outputs))
        assert len(outputs) == 1
        assert outputs[0].image == expected.crop(outputs[0].area)
        if action == "window":
            assert outputs[0].title == "Before dismissal"
            assert outputs[0].area == Rect(70, 70, 300, 200)
        assert source.frames == 1
        assert not service.is_selecting
    finally:
        service.cancel()
        menu.close()


def test_window_selection_uses_copied_pre_dismissal_geometry(qtbot: QtBot) -> None:
    """UX-CAP-04/16: clicking selects the frozen target even if its source moves."""
    menu = QMenu()
    menu.addAction("Freeze this menu")
    source, target = MenuDesktop(menu), MovingTarget()
    service = CaptureService(source, target)
    outputs: list[Capture] = []
    service.captured.connect(outputs.append)
    menu.aboutToHide.connect(target.move)
    try:
        menu.popup(QPoint(80, 80))
        service.capture_region(freeze_first=True)
        qtbot.waitUntil(lambda: bool(service.overlays))
        window = service.overlays[0].windowHandle()
        assert window is not None
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        assert len(outputs) == 1
        assert outputs[0].title == "Before dismissal"
        assert outputs[0].area == Rect(70, 70, 300, 200)
    finally:
        service.cancel()
        menu.close()


@pytest.mark.parametrize("action", ["region", "window", "fullscreen", "repeat"])
def test_tray_capture_command_excludes_its_own_menu(
    qtbot: QtBot, make_app: AppFactory, action: str
) -> None:
    """UX-CAP-16: the initiating Capture command remains absent from tray captures."""
    placeholder = QMenu()
    source = MenuDesktop(placeholder)
    app = make_app(screens=source, windows=MovingTarget())
    source.menu = app.controller.tray.menu
    source.menu.popup(QPoint(80, 80))
    qtbot.waitUntil(source.menu.isVisible)
    app.controller.tray.action(action).trigger()
    assert source.frames == 0
    qtbot.waitUntil(lambda: source.frames == 1)
    assert source.observed == [False]
    app.controller.capture.cancel()
    placeholder.close()


def test_repeated_hotkey_and_cancel_discard_saved_frame(qtbot: QtBot) -> None:
    """UX-CAP-05/16: pending/visible requests share one frame; retry is fresh."""
    menu = QMenu()
    menu.addAction("Visible menu")
    source = MenuDesktop(menu)
    service = CaptureService(source, MovingTarget())
    outputs: list[Capture] = []
    service.captured.connect(outputs.append)
    try:
        menu.popup(QPoint(80, 80))
        service.capture_region(freeze_first=True)
        service.capture_fullscreen(freeze_first=True)
        service.repeat_last(freeze_first=True)
        assert source.frames == 1
        service.cancel()
        qtbot.wait(0)
        assert not service.is_selecting
        assert outputs == []
        service.capture_region(freeze_first=True)
        qtbot.waitUntil(lambda: bool(service.overlays))
        service.capture_region(freeze_first=True)
        service.capture_fullscreen(freeze_first=True)
        assert source.frames == 2
        assert source.observed == [True, False]
        select_menu(service)
        assert len(outputs) == 1
        expected = source.last
        assert expected is not None
        assert outputs[0].image == expected.crop(outputs[0].area)
    finally:
        service.cancel()
        menu.close()
