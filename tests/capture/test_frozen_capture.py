"""Freeze-first targets, repeat history, and preparation failure boundaries."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor, QGuiApplication, QScreen
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindow, FakeWindows
from tests.ux.test_frozen_menu_journeys import MovingTarget, select_menu

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import Capture
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


@pytest.mark.parametrize("failure", ["freeze", "discovery", "foreground", "crop"])
def test_freeze_first_failure_returns_idle_and_retries(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A failed source/target/crop never delivers or retains its prepared frame."""
    source = FakeDesktop()
    windows = FakeWindows(active=FakeWindow("Window", Rect(50, 50, 200, 200)))
    service = CaptureService(source, windows)
    errors: list[str] = []
    outputs: list[Capture] = []
    service.failed.connect(errors.append)
    service.captured.connect(outputs.append)
    try:
        with monkeypatch.context() as patch:
            if failure == "freeze":
                source.fail = True
            elif failure == "discovery":
                patch.setattr(
                    FakeWindows,
                    "visible_windows",
                    Mock(side_effect=CaptureError("window lookup failed")),
                )
            elif failure == "foreground":
                windows.active = None
            else:
                patch.setattr(
                    FrozenScreen, "crop", Mock(side_effect=CaptureError("crop failed"))
                )
            if failure in {"foreground", "crop"}:
                service.capture_active_window(freeze_first=True)
            else:
                service.capture_region(freeze_first=True)
            qtbot.waitUntil(lambda: bool(errors))
            assert len(errors) == 1
            assert outputs == []
            assert not service.is_selecting
            assert service.overlays == ()
        source.fail = False
        service.capture_region(freeze_first=True)
        qtbot.waitUntil(lambda: bool(service.overlays))
        select_menu(service)
        assert len(outputs) == 1
    finally:
        service.cancel()


@pytest.mark.parametrize("repeat", [False, True])
def test_fullscreen_target_precedes_popup_cursor_movement(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, *, repeat: bool
) -> None:
    """Fullscreen and its repeat retain the monitor occupied at the hotkey."""
    first, second = Mock(spec=QScreen), Mock(spec=QScreen)
    for index, screen in enumerate((first, second)):
        screen.name.return_value = f"Screen {index}"
        screen.devicePixelRatio.return_value = 1
        screen.geometry.return_value = QRect(index * 400, 0, 400, 600)
    monkeypatch.setattr(
        QGuiApplication, "screenAt", lambda point: first if point.x() < 400 else second
    )
    source = FakeDesktop()
    service = CaptureService(source, FakeWindows())
    outputs: list[Capture] = []
    service.captured.connect(outputs.append)
    menu = QMenu()
    menu.addAction("Move cursor on dismissal")
    previous = QCursor.pos()
    try:
        if repeat:
            service.capture_fullscreen()
            qtbot.waitUntil(lambda: len(outputs) == 1)
            outputs.clear()
        menu.popup(QPoint(80, 80))
        QCursor.setPos(120, 100)
        menu.aboutToHide.connect(lambda: QCursor.setPos(600, 400))
        if repeat:
            service.repeat_last(freeze_first=True)
        else:
            service.capture_fullscreen(freeze_first=True)
        qtbot.waitUntil(lambda: bool(outputs))
        assert len(outputs) == 1
        assert outputs[0].area == Rect(0, 0, 400, 600)
        assert source.frames == 1 + int(repeat)
    finally:
        service.cancel()
        menu.close()
        QCursor.setPos(previous)


@pytest.mark.parametrize("mode", ["region", "window"])
def test_established_repeat_uses_one_pre_dismissal_frame(
    qtbot: QtBot, mode: str
) -> None:
    """Region repeat keeps its area; active-window repeat snapshots new foreground."""
    source, target = FakeDesktop(), MovingTarget()
    service = CaptureService(source, target)
    outputs: list[Capture] = []
    service.captured.connect(outputs.append)
    menu = QMenu()
    menu.addAction("A menu changes the foreground on dismissal")
    try:
        if mode == "region":
            service.capture_region()
            qtbot.waitUntil(lambda: bool(service.overlays))
            select_menu(service)
        else:
            service.capture_active_window()
            qtbot.waitUntil(lambda: bool(outputs))
        first_area = outputs[0].area
        target.bounds = Rect(60, 60, 200, 100)
        target.title = "At repeat hotkey"
        menu.popup(QPoint(80, 80))
        menu.aboutToHide.connect(target.move)
        service.repeat_last(freeze_first=True)
        qtbot.waitUntil(lambda: len(outputs) == 2)
        assert source.frames == 2
        assert not service.overlays
        assert outputs[1].image.pixelColor(0, 0).red() == 20
        if mode == "region":
            assert outputs[1].area == first_area
        else:
            assert outputs[1].area == Rect(60, 60, 200, 100)
            assert outputs[1].title == "At repeat hotkey"
    finally:
        service.cancel()
        menu.close()
