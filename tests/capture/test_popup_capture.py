"""Regression for a capture started while a Qt popup owns input."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QMenu,
    QMessageBox,
    QWidget,
)
from pytestqt.qtbot import QtBot
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindow, FakeWindows

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.capture.service import CaptureService
from verdiclip.geometry import Rect
from verdiclip.settings import Settings
from verdiclip.shell.settings_dialog import SettingsDialog


class InputTrace(QObject):
    """Record Qt's input ownership and event target, rather than inferred focus."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, str]] = []

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Observe relevant events without consuming or redirecting any input."""
        if event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.KeyPress,
            QEvent.Type.GrabMouse,
            QEvent.Type.UngrabMouse,
            QEvent.Type.GrabKeyboard,
            QEvent.Type.UngrabKeyboard,
        }:
            self.events.append(
                {
                    "target": watched.metaObject().className(),
                    "event": event.type().name,
                    "popup": self.name(QApplication.activePopupWidget()),
                    "focus": self.name(QApplication.focusWidget()),
                    "mouse_grabber": self.name(QWidget.mouseGrabber()),
                    "keyboard_grabber": self.name(QWidget.keyboardGrabber()),
                }
            )
        return False

    @staticmethod
    def name(obj: QObject | None) -> str:
        """Return a stable diagnostic identity for a Qt object."""
        return "none" if obj is None else obj.metaObject().className()


def test_capture_takes_input_ownership_from_popup(
    qtbot: QtBot,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    """UX-G-10: a visible selection cannot leave another popup owning input."""
    service = CaptureService(FakeDesktop(), FakeWindows())
    menu = QMenu()
    menu.addAction("Capture region", service.capture_region)
    trace = InputTrace()
    qapp.installEventFilter(trace)
    try:
        menu.popup(QPoint(80, 80))
        qtbot.waitUntil(menu.isVisible)
        assert QApplication.activePopupWidget() is menu
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        overlay = service.overlays[0]
        state = {
            "overlay_visible": overlay.isVisible(),
            "popup": InputTrace.name(QApplication.activePopupWidget()),
            "focus_window": InputTrace.name(QGuiApplication.focusWindow()),
            "events": trace.events,
        }
        # QWindow input follows Qt's native event routing, unlike QWidget.sendEvent.
        QTest.keyClick(overlay.windowHandle(), Qt.Key.Key_Escape)
        qapp.processEvents()
        state["selecting_after_escape"] = service.is_selecting
        (tmp_path / "popup-input-trace.json").write_text(json.dumps(state, indent=2))
        assert not service.is_selecting, json.dumps(state, indent=2)
    finally:
        service.cancel()
        menu.close()
        qapp.removeEventFilter(trace)


def test_closing_selection_ends_session_and_allows_retry(qtbot: QtBot) -> None:
    """UX-G-10: closing a capture window must terminate its owner session."""
    desktop = FakeDesktop()
    service = CaptureService(desktop, FakeWindows())
    cancelled: list[bool] = []
    service.cancelled.connect(lambda: cancelled.append(True))
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        overlay = service.overlays[0]
        overlay.close()
        assert not service.is_selecting, "A closed overlay cannot leave capture active"
        assert cancelled == [True], "Closing a selection reports cancellation once"
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert service.overlays[0] is not overlay, (
            "The next capture must have fresh input"
        )
        assert desktop.frames == 2
    finally:
        service.cancel()
        qtbot.wait(0)


@pytest.fixture
def capturing(qtbot: QtBot) -> Iterator[tuple[CaptureService, FakeDesktop]]:
    """A session whose capture, cancellation and recovery have no shell side effects."""
    desktop = FakeDesktop()
    window = FakeWindow("Document", Rect(80, 80, 300, 200))
    service = CaptureService(desktop, FakeWindows([window], active=window))
    yield service, desktop
    service.cancel()
    qtbot.wait(0)


@pytest.mark.parametrize(
    "entry",
    ["capture_region", "capture_fullscreen", "capture_active_window", "repeat_last"],
)
def test_every_entry_waits_for_popup_handoff(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    entry: str,
) -> None:
    """UX-G-10: all capture modes close popup ownership before freezing pixels."""
    service, desktop = capturing
    menu = QMenu()
    menu.addAction("An ordinary popup")
    captured: list[object] = []
    service.captured.connect(captured.append)
    try:
        menu.popup(QPoint(80, 80))
        assert QApplication.activePopupWidget() is menu
        getattr(service, entry)()
        assert desktop.frames == 0, "Capture cannot run inside popup/native dispatch"
        assert not menu.isVisible(), "The transient menu must dismiss through Qt"
        assert service.is_selecting, (
            "The pending handoff is part of the capture session"
        )
        qtbot.waitUntil(lambda: desktop.frames == 1)
        assert QApplication.activePopupWidget() is None
        if entry in {"capture_region", "repeat_last"}:
            assert service.overlays, (
                "A region request must retain its selection session"
            )
        else:
            assert len(captured) == 1, "Immediate modes deliver once after the handoff"
            assert not service.is_selecting
    finally:
        menu.close()


@pytest.mark.parametrize("kind", ["popup", "submenu", "combo"])
def test_popup_stack_is_released_without_special_casing_windows(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    kind: str,
) -> None:
    """UX-G-10: input handoff follows Qt popup ownership, not a tray-window list."""
    service, _ = capturing
    parent = QMenu()
    combo: QComboBox | None = None
    popup: QWidget
    if kind == "submenu":
        popup = parent.addMenu("Submenu")
        parent.addAction("Root")
        popup.addAction("Leaf")
        parent.popup(QPoint(80, 80))
        popup.popup(QPoint(150, 80))
    elif kind == "combo":
        combo = QComboBox()
        combo.addItems(["One", "Two"])
        combo.show()
        combo.showPopup()
        active_popup = QApplication.activePopupWidget()
        assert active_popup is not None
        popup = active_popup
    else:
        popup = QWidget(None, Qt.WindowType.Popup)
        popup.show()
    try:
        assert QApplication.activePopupWidget() is popup
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert QApplication.activePopupWidget() is None
        assert not popup.isVisible()
        assert not parent.isVisible()
        QTest.keyClick(service.overlays[0].windowHandle(), Qt.Key.Key_Escape)
        assert not service.is_selecting
    finally:
        popup.close()
        parent.close()
        if combo is not None:
            combo.close()


def test_pending_requests_coalesce_and_cancel_without_freezing(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
) -> None:
    """UX-CAP-05: cancelling a queued capture leaves no delayed selection behind."""
    service, desktop = capturing
    cancelled: list[bool] = []
    service.cancelled.connect(lambda: cancelled.append(True))
    service.capture_region()
    service.capture_fullscreen()
    service.capture_active_window()
    service.repeat_last()
    assert desktop.frames == 0
    service.cancel()
    qtbot.wait(0)
    assert not service.is_selecting
    assert service.overlays == ()
    assert desktop.frames == 0, "Cancelled work must never freeze on a later turn"
    assert cancelled == [True]
    service.capture_region()
    service.capture_region()
    service.capture_fullscreen()
    qtbot.waitUntil(lambda: bool(service.overlays))
    assert desktop.frames == 1, "Repeated requests must share the first selection"
    original = service.overlays
    service.capture_region()
    assert service.overlays == original
    assert desktop.frames == 1


def test_popup_hide_handler_can_cancel_the_pending_capture(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
) -> None:
    """A reentrant dismissal callback cannot resurrect a cancelled request."""
    service, desktop = capturing
    menu = QMenu()
    menu.addAction("Popup")
    menu.aboutToHide.connect(service.cancel)
    try:
        menu.popup(QPoint(80, 80))
        service.capture_region()
        qtbot.wait(0)
        assert desktop.frames == 0
        assert not service.is_selecting
    finally:
        menu.close()


@pytest.mark.parametrize("owner", ["keyboard", "mouse"])
def test_explicit_grab_is_released_at_capture_boundary(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    owner: str,
) -> None:
    """UX-G-10: an existing widget grab must not retain selection input."""
    service, _ = capturing
    widget = QWidget()
    widget.show()
    try:
        if owner == "keyboard":
            widget.grabKeyboard()
            assert QWidget.keyboardGrabber() is widget
        else:
            widget.grabMouse()
            assert QWidget.mouseGrabber() is widget
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert QWidget.mouseGrabber() is not widget
        assert QWidget.keyboardGrabber() is not widget
        assert widget.isVisible(), "Releasing a grab must not close the owning window"
    finally:
        widget.releaseMouse()
        widget.releaseKeyboard()
        widget.close()


@pytest.mark.parametrize("kind", ["main", "about", "settings", "modal"])
def test_modal_and_main_windows_keep_work_while_capture_owns_input(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    kind: str,
) -> None:
    """UX-G-10: normal/modal windows survive capture and do not block native routing."""
    service, _ = capturing
    parent = QWidget()
    parent.show()
    if kind == "main":
        owner = parent
    elif kind == "about":
        owner = QMessageBox(
            QMessageBox.Icon.Information, "About", "Version", parent=parent
        )
    elif kind == "settings":
        owner = SettingsDialog(Settings(), parent)
    else:
        owner = QDialog(parent)
    if isinstance(owner, QDialog):
        owner.setWindowModality(Qt.WindowModality.ApplicationModal)
    owner.show()
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        overlay = service.overlays[0]
        QTest.keyClick(overlay.windowHandle(), Qt.Key.Key_Escape)
        assert not service.is_selecting
        assert owner.isVisible(), "Capture must not dismiss documents or modal dialogs"
    finally:
        owner.close()
        parent.close()


@pytest.mark.parametrize("kind", ["window", "popup", "keyboard", "mouse"])
def test_losing_input_cancels_and_next_capture_recovers(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    kind: str,
) -> None:
    """UX-CAP-05: a selection losing its input lease cannot remain frozen."""
    service, desktop = capturing
    cancelled: list[bool] = []
    service.cancelled.connect(lambda: cancelled.append(True))
    other = QWidget(
        None, Qt.WindowType.Popup if kind == "popup" else Qt.WindowType.Window
    )
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        QApplication.processEvents()
        other.show()
        if kind == "window":
            other.activateWindow()
        elif kind == "keyboard":
            other.grabKeyboard()
        elif kind == "mouse":
            other.grabMouse()
        qtbot.waitUntil(lambda: not service.is_selecting)
        assert cancelled == [True]
        assert service.overlays == ()
        assert other.isVisible(), "Capture cleanup must preserve the new input owner"
        other.releaseMouse()
        other.releaseKeyboard()
        other.close()
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert desktop.frames == 2
        QTest.keyClick(service.overlays[0].windowHandle(), Qt.Key.Key_Escape)
        assert cancelled == [True, True]
    finally:
        other.releaseMouse()
        other.releaseKeyboard()
        other.close()


def test_capture_failure_drops_input_lease_and_allows_retry(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
) -> None:
    """UX-G-05: a failing freeze ends pending capture and permits a fresh attempt."""
    service, desktop = capturing
    failures: list[str] = []
    service.failed.connect(failures.append)
    desktop.fail = True
    service.capture_region()
    qtbot.waitUntil(lambda: bool(failures))
    assert failures == ["screen is locked"]
    assert not service.is_selecting
    desktop.fail = False
    service.capture_region()
    qtbot.waitUntil(lambda: bool(service.overlays))
    assert desktop.frames == 1


class VetoPopup(QWidget):
    """A popup whose owner can refuse dismissal until its operation finishes."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Popup)
        self.allow_close = False

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Model a transient interaction that explicitly declines to close."""
        if self.allow_close:
            super().closeEvent(event)
        else:
            event.ignore()


def test_popup_refusing_close_reports_failure_and_allows_retry(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
) -> None:
    """An input owner veto cannot strand capture or be silently forced closed."""
    service, desktop = capturing
    failures: list[str] = []
    service.failed.connect(failures.append)
    popup = VetoPopup()
    popup.show()
    try:
        service.capture_region()
        assert failures == ["Close the open popup before capturing"]
        assert popup.isVisible()
        assert not service.is_selecting
        assert desktop.frames == 0
        popup.allow_close = True
        popup.close()
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert desktop.frames == 1
    finally:
        popup.allow_close = True
        popup.close()


@pytest.mark.parametrize("kind", ["popup", "keyboard", "mouse"])
def test_input_reacquired_before_queued_start_fails_safely(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    kind: str,
) -> None:
    """A new input owner appearing during handoff must not be captured or closed."""
    service, desktop = capturing
    failures: list[str] = []
    service.failed.connect(failures.append)
    owner = QWidget(
        None, Qt.WindowType.Popup if kind == "popup" else Qt.WindowType.Window
    )
    try:
        service.capture_region()
        owner.show()
        if kind == "keyboard":
            owner.grabKeyboard()
        elif kind == "mouse":
            owner.grabMouse()
        qtbot.waitUntil(lambda: bool(failures))
        assert failures == ["Another window is holding input; try capturing again"]
        assert desktop.frames == 0
        assert not service.is_selecting
        assert owner.isVisible()
        owner.releaseKeyboard()
        owner.releaseMouse()
        owner.close()
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert desktop.frames == 1
    finally:
        owner.releaseKeyboard()
        owner.releaseMouse()
        owner.close()


def test_application_deactivation_cancels_selection(
    qtbot: QtBot,
    qapp: QApplication,
    capturing: tuple[CaptureService, FakeDesktop],
) -> None:
    """The platform's application-deactivation event cannot leave a frozen lease."""
    service, _ = capturing
    service.capture_region()
    qtbot.waitUntil(lambda: bool(service.overlays))
    QApplication.processEvents()
    try:
        QApplication.sendEvent(qapp, QEvent(QEvent.Type.ApplicationDeactivate))
        qtbot.waitUntil(lambda: not service.is_selecting)
    finally:
        QApplication.sendEvent(qapp, QEvent(QEvent.Type.ApplicationActivate))


def test_reentrant_request_during_freeze_coalesces(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A callback during preparation cannot queue a second capture behind the first."""
    service, desktop = capturing
    freeze = desktop.freeze
    captured: list[object] = []
    service.captured.connect(captured.append)

    def prepare(_desktop: FakeDesktop) -> FrozenScreen:
        """A screen provider that reenters capture while preparing the first frame."""
        service.capture_fullscreen()
        return freeze()

    monkeypatch.setattr(FakeDesktop, "freeze", prepare)
    service.capture_region()
    qtbot.waitUntil(lambda: bool(service.overlays))
    QApplication.processEvents()
    assert desktop.frames == 1, "Preparation is part of the same exclusive session"
    assert captured == [], "A repeated request cannot capture the visible overlays"


def test_closing_one_overlay_finishes_every_overlay(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-window selection has one terminal state regardless of which closes."""
    service, _ = capturing
    screen = QGuiApplication.primaryScreen()
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [screen, screen])
    cancelled: list[bool] = []
    service.cancelled.connect(lambda: cancelled.append(True))
    service.capture_region()
    qtbot.waitUntil(lambda: len(service.overlays) == 2)
    windows = service.overlays
    windows[1].close()
    assert not service.is_selecting
    assert all(not window.isVisible() for window in windows)
    assert cancelled == [True], (
        "Sibling close cleanup must not emit duplicate cancellation"
    )


@pytest.mark.parametrize("entry", ["capture_region", "capture_fullscreen"])
def test_cancel_during_preparation_cannot_deliver_or_create_overlays(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    """A provider callback cancelling preparation must invalidate that result."""
    service, desktop = capturing
    freeze = desktop.freeze
    captured: list[object] = []
    cancelled: list[bool] = []
    service.captured.connect(captured.append)
    service.cancelled.connect(lambda: cancelled.append(True))

    def prepare(_desktop: FakeDesktop) -> FrozenScreen:
        """Cancel inside preparation, before the frame can be committed."""
        service.cancel()
        return freeze()

    monkeypatch.setattr(FakeDesktop, "freeze", prepare)
    getattr(service, entry)()
    qtbot.waitUntil(lambda: bool(cancelled))
    assert not service.is_selecting
    assert service.overlays == ()
    assert captured == []
    assert cancelled == [True]
    monkeypatch.undo()
    service.capture_region()
    qtbot.waitUntil(lambda: bool(service.overlays))
    assert desktop.frames == 2


def test_cancelled_preparation_preserves_repeat_history(
    qtbot: QtBot,
    capturing: tuple[CaptureService, FakeDesktop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a delivered capture can replace the region remembered for repeat."""
    service, desktop = capturing
    delivered: list[Capture] = []
    service.captured.connect(delivered.append)
    service.capture_region()
    qtbot.waitUntil(lambda: bool(service.overlays))
    window = service.overlays[0].windowHandle()
    assert window is not None
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(90, 90))
    QTest.mouseMove(window, QPoint(160, 140))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(160, 140))
    assert delivered[0].area == Rect(90, 90, 70, 50)
    freeze = desktop.freeze

    def cancelled_frame(_desktop: FakeDesktop) -> FrozenScreen:
        """Cancel a different capture kind before it can change repeat history."""
        service.cancel()
        return freeze()

    monkeypatch.setattr(FakeDesktop, "freeze", cancelled_frame)
    service.capture_fullscreen()
    qtbot.waitUntil(lambda: desktop.frames == 2)
    assert len(delivered) == 1
    monkeypatch.undo()
    service.repeat_last()
    qtbot.waitUntil(lambda: len(delivered) == 2)
    assert delivered[1].mode == CaptureMode.REGION
    assert delivered[1].area == Rect(90, 90, 70, 50)
