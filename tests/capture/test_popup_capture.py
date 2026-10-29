"""Regression for a capture started while a Qt popup owns input."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QMenu,
    QMessageBox,
    QWidget,
)
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputBroker
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindow, FakeWindows

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import Capture
from verdiclip.capture.service import CaptureService, read_display_snapshot
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
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


class ExistingWindowTrace(QObject):
    """Observe only windows that existed before this explicit capture."""

    def __init__(self, windows: tuple[QWidget, ...]) -> None:
        super().__init__()
        self.windows = windows
        self.changes: list[str] = []

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if any(watched is window for window in self.windows) and event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.Close,
        }:
            self.changes.append(event.type().name)
        return False


class CaptureContext:
    """A real service and Qt overlays over explicit fake pixel/input boundaries."""

    def __init__(self, qapp: QApplication) -> None:
        self.desktop = FakeDesktop()
        self.broker = ReplayInputBroker(qapp)
        window = FakeWindow("Example document", Rect(80, 80, 300, 200))
        self.windows = FakeWindows([window], active=window)
        self.display_snapshot = read_display_snapshot()
        self.service = CaptureService(
            self.desktop,
            self.windows,
            input_broker=self.broker,
            display_snapshot=lambda: self.display_snapshot,
        )
        self.captures: list[object] = []
        self.cancellations: list[bool] = []
        self.failures: list[str] = []
        self.service.captured.connect(self.captures.append)
        self.service.cancelled.connect(lambda: self.cancellations.append(True))
        self.service.failed.connect(self.failures.append)

    def cancel(self) -> None:
        """Replay cancellation through the same broker/service boundary."""
        self.broker.feed(SelectionInput(InputKind.CANCEL, Point(120, 120), "escape"))

    def drag(self) -> None:
        """Replay a complete selection without native input or cursor changes."""
        for kind, point in (
            (InputKind.MOVE, Point(80, 80)),
            (InputKind.PRESS, Point(80, 80)),
            (InputKind.MOVE, Point(180, 150)),
            (InputKind.RELEASE, Point(180, 150)),
        ):
            self.broker.feed(SelectionInput(kind, point))


@pytest.fixture
def context(qapp: QApplication) -> Iterator[CaptureContext]:
    """Always retire only resources created by this fixture."""
    value = CaptureContext(qapp)
    yield value
    value.service.cancel()
    value.broker.acknowledge_cleanup()
    value.service.deleteLater()
    value.broker.deleteLater()


def test_unavailable_display_refuses_before_input_reservation(
    context: CaptureContext, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty topology cannot install input ownership or fabricate a screenshot."""
    context.display_snapshot = ()
    monkeypatch.setattr(QGuiApplication, "screens", list)
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.failures))
    assert "No display" in context.failures[0]
    assert not context.service.is_selecting
    assert not context.captures


def test_no_cursor_display_refuses_read_only_capture(
    context: CaptureContext, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disconnected last display is an error rather than a null screen access."""
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda _point: None)
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: None)
    context.service.capture_fullscreen()
    qtbot.waitUntil(lambda: bool(context.failures))
    assert "No display" in context.failures[0]
    assert not context.service.is_selecting


def test_pixel_acquisition_failure_releases_input_before_error(
    context: CaptureContext, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A display read error cannot leave the newly acquired input lease active."""

    def unavailable(_source: FakeDesktop) -> FrozenScreen:
        raise CaptureError("Fixture pixels unavailable")

    monkeypatch.setattr(FakeDesktop, "freeze", unavailable)
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.failures))
    assert context.failures == ["Fixture pixels unavailable"]
    assert not context.broker.busy
    assert not context.service.overlays
    assert not context.captures


def test_invalid_selected_pixels_cannot_deliver_or_stick(
    context: CaptureContext, qtbot: QtBot
) -> None:
    """A stale/outside selection is refused and the same service can retry."""
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.service.overlays[0].region_chosen.emit(Rect(1_000_000, 1_000_000, 20, 20))
    assert context.failures
    assert not context.service.is_selecting
    assert not context.captures
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.cancel()
    assert not context.service.is_selecting


@pytest.mark.parametrize("cleaned", [False, True])
def test_unexpected_input_termination_never_delivers_pending_pixels(
    context: CaptureContext, qtbot: QtBot, *, cleaned: bool
) -> None:
    """Lost ownership is explicit failure, including a negative cleanup receipt."""
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.broker.closed.emit(cleaned, "Fixture input interrupted")
    assert context.failures
    assert not context.captures
    assert not context.service.overlays
    if not cleaned:
        assert "Restart VerdiClip" in context.failures[0]


class ExistingScene:
    """Retain every pre-existing test window and compare its exact ownership state."""

    def __init__(self, kind: str, qapp: QApplication) -> None:
        self.parent = QWidget()
        self.parent.show()
        self.owner: QWidget = self.parent
        self.windows: list[QWidget] = [self.parent]
        self.qapp = qapp
        self._build(kind)
        self.trace = ExistingWindowTrace(tuple(self.windows))
        qapp.installEventFilter(self.trace)
        self.handles = tuple(window.windowHandle() for window in self.windows)
        self.modality = self.owner.windowModality()
        self.popup = QApplication.activePopupWidget()
        self.mouse, self.keyboard = QWidget.mouseGrabber(), QWidget.keyboardGrabber()

    def _build(self, kind: str) -> None:
        if kind == "main":
            return
        if kind in {"popup", "submenu"}:
            root = QMenu(self.parent)
            root.addAction("Example document")
            self.windows.append(root)
            menu = root.addMenu("Nested example") if kind == "submenu" else root
            self.owner = menu
            menu.addAction("Example item")
            root.popup(QPoint(60, 60))
            if kind == "submenu":
                self.windows.append(self.owner)
                menu.popup(QPoint(160, 60))
        elif kind == "combo":
            self._combo()
        else:
            self._dialog(kind)

    def _dialog(self, kind: str) -> None:
        if kind == "about":
            self.owner = QMessageBox(
                QMessageBox.Icon.Information, "About", "Example", parent=self.parent
            )
        elif kind == "settings":
            self.owner = SettingsDialog(Settings(), self.parent)
        else:
            self.owner = QDialog(self.parent)
        self.windows.append(self.owner)
        self.owner.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.owner.show()

    def _combo(self) -> None:
        combo = QComboBox()
        combo.addItems(["Example one", "Example two"])
        combo.show()
        combo.showPopup()
        popup = QApplication.activePopupWidget()
        assert popup is not None
        self.owner = popup
        self.windows.extend((combo, popup))

    def assert_preserved(self) -> None:
        """Assert no Hide/Close, reconstruction, modality change or grab transfer."""
        assert not self.trace.changes
        assert all(window.isVisible() for window in self.windows)
        assert tuple(window.windowHandle() for window in self.windows) == self.handles
        assert self.owner.windowModality() == self.modality
        assert QApplication.activePopupWidget() is self.popup
        assert QWidget.mouseGrabber() is self.mouse
        assert QWidget.keyboardGrabber() is self.keyboard

    def close(self) -> None:
        """Close only fixture windows after capture assertions are complete."""
        self.qapp.removeEventFilter(self.trace)
        for window in reversed(self.windows):
            window.close()


@pytest.fixture
def existing_scene(
    request: pytest.FixtureRequest, qtbot: QtBot, qapp: QApplication
) -> Iterator[ExistingScene]:
    """Window-specific setup is fixture data, never production capture policy."""
    scene = ExistingScene(str(request.param), qapp)
    qtbot.waitUntil(scene.owner.isVisible)
    yield scene
    scene.close()


@pytest.mark.parametrize(
    "existing_scene",
    ["main", "about", "settings", "modal", "popup", "submenu", "combo"],
    indirect=True,
)
@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_capture_preserves_existing_windows_popups_and_modal_state(
    qtbot: QtBot, context: CaptureContext, existing_scene: ExistingScene, outcome: str
) -> None:
    """The same preservation invariant holds across every pre-existing owner."""
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    overlay = context.service.overlays[0]
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    if outcome == "escape":
        context.cancel()
    else:
        context.drag()
    assert not context.service.is_selecting
    existing_scene.assert_preserved()
    if outcome == "drag":
        assert len(context.captures) == 1
        capture = context.captures[0]
        assert isinstance(capture, Capture)
        assert capture.preserve_foreground
        assert capture.area == Rect(80, 80, 100, 70)
    else:
        assert context.cancellations == [True]
    assert not context.failures


@pytest.mark.parametrize(
    "entry",
    ["capture_region", "capture_fullscreen", "capture_active_window", "repeat_last"],
)
def test_all_capture_entries_leave_visible_menu_unchanged(
    qtbot: QtBot, qapp: QApplication, context: CaptureContext, entry: str
) -> None:
    """Queued capture reads preserve the menu instead of using it as a handoff."""
    menu = QMenu()
    menu.addAction("Example")
    menu.popup(QPoint(80, 80))
    trace = ExistingWindowTrace((menu,))
    qapp.installEventFilter(trace)
    try:
        getattr(context.service, entry)()
        assert context.desktop.frames == 0
        assert menu.isVisible()
        qtbot.waitUntil(lambda: context.desktop.frames == 1)
        assert not trace.changes
        assert menu.isVisible()
        if entry in {"capture_region", "repeat_last"}:
            context.cancel()
        else:
            assert len(context.captures) == 1
            assert context.broker.starts == 0
    finally:
        context.service.cancel()
        qapp.removeEventFilter(trace)
        menu.close()


@pytest.mark.parametrize("grab", ["mouse", "keyboard"])
def test_existing_explicit_grab_is_not_released_or_reassigned(
    qtbot: QtBot, context: CaptureContext, grab: str
) -> None:
    """Selection uses an independent stream and leaves the original grab intact."""
    owner = QWidget()
    owner.show()
    try:
        if grab == "mouse":
            owner.grabMouse()
        else:
            owner.grabKeyboard()
        mouse, keyboard = QWidget.mouseGrabber(), QWidget.keyboardGrabber()
        context.service.capture_region()
        qtbot.waitUntil(lambda: bool(context.service.overlays))
        context.drag()
        assert len(context.captures) == 1
        assert QWidget.mouseGrabber() is mouse
        assert QWidget.keyboardGrabber() is keyboard
        assert owner.isVisible()
    finally:
        owner.releaseMouse()
        owner.releaseKeyboard()
        owner.close()


def test_delivery_waits_for_cleanup_receipt_and_coalesces_repeated_requests(
    qtbot: QtBot, context: CaptureContext
) -> None:
    """No output or second lease can run while the old input is being released."""
    context.broker.defer_cleanup = True
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.drag()
    assert not context.captures
    assert context.service.is_selecting
    context.service.capture_region()
    context.service.capture_fullscreen()
    assert context.broker.starts == 1
    context.broker.acknowledge_cleanup()
    assert len(context.captures) == 1
    assert not context.service.is_selecting
    context.service.capture_region()
    qtbot.waitUntil(lambda: len(context.service.overlays) > 0)
    context.cancel()
    context.broker.acknowledge_cleanup()
    assert context.broker.starts == 2


def test_queued_cancel_and_reentrant_freeze_cancel_do_not_resurrect_selection(
    qtbot: QtBot, context: CaptureContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation reserves a generation before work or cleanup callbacks run."""
    context.service.capture_region()
    context.service.cancel()
    QApplication.processEvents()
    assert context.desktop.frames == 0
    assert not context.service.overlays
    original = FakeDesktop.freeze

    def cancel_then_freeze(desktop: FakeDesktop) -> FrozenScreen:
        if desktop is context.desktop:
            context.service.cancel()
        return original(desktop)

    monkeypatch.setattr(FakeDesktop, "freeze", cancel_then_freeze)
    context.service.capture_region()
    qtbot.waitUntil(lambda: context.desktop.frames == 1)
    assert not context.service.is_selecting
    assert not context.service.overlays
    assert not context.captures
    assert context.cancellations == [True, True]


def test_failed_freeze_releases_owned_input_and_allows_retry(
    qtbot: QtBot, context: CaptureContext
) -> None:
    """A capture error never dismisses unrelated content or leaves an active lease."""
    context.desktop.fail = True
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.failures))
    assert not context.service.is_selecting
    context.desktop.fail = False
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.cancel()
    assert not context.service.is_selecting


def test_unexpected_lease_loss_ends_overlays_without_closing_other_windows(
    qtbot: QtBot, context: CaptureContext
) -> None:
    """The native boundary's terminal receipt, not Qt window special cases, cancels."""
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.broker.release()
    assert not context.service.is_selecting
    assert context.failures == ["Capture input was interrupted"]
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    context.cancel()


def test_repeat_keeps_clicked_window_when_original_foreground_is_different(
    qtbot: QtBot, context: CaptureContext
) -> None:
    """Selecting B leaves A foreground, but Repeat retains B's explicit area."""
    selected = FakeWindow("Selected B", Rect(400, 200, 200, 150))
    context.windows.windows.insert(0, selected)
    context.service.capture_region()
    qtbot.waitUntil(lambda: bool(context.service.overlays))
    for kind in (InputKind.MOVE, InputKind.PRESS, InputKind.RELEASE):
        context.broker.feed(SelectionInput(kind, Point(450, 250)))
    context.service.repeat_last()
    qtbot.waitUntil(lambda: len(context.captures) == 2)
    for capture in context.captures:
        assert isinstance(capture, Capture)
        assert capture.title == selected.title
        assert capture.area == selected.bounds


def test_repeat_active_window_follows_current_foreground(
    qtbot: QtBot, context: CaptureContext
) -> None:
    """Active-window repeat remains distinct from an explicitly clicked rectangle."""
    context.service.capture_active_window()
    qtbot.waitUntil(lambda: len(context.captures) == 1)
    other = FakeWindow("New foreground", Rect(400, 200, 200, 150))
    context.windows.active = other
    context.service.repeat_last()
    qtbot.waitUntil(lambda: len(context.captures) == 2)
    actual = context.captures[-1]
    assert isinstance(actual, Capture)
    assert actual.area == other.bounds
    assert actual.title == other.title
    assert context.broker.starts == 0


@pytest.mark.parametrize("stage", ["preparation", "freeze", "selection"])
@pytest.mark.parametrize("change", ["identity", "dpi", "removed"])
def test_topology_change_invalidates_capture_without_mixing_coordinates(
    qtbot: QtBot,
    context: CaptureContext,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    change: str,
) -> None:
    """Synthetic topology changes use the native display-event contract."""

    def alter() -> None:
        identity, geometry = context.display_snapshot[0]
        if change == "identity":
            context.display_snapshot = ((identity + 1, geometry),)
        elif change == "dpi":
            context.display_snapshot = (
                (identity, replace(geometry, scale=geometry.scale + 0.25)),
            )
        else:
            context.display_snapshot = ()

    if stage == "preparation":
        context.broker.defer_ready = True
        context.service.capture_region()
        QApplication.processEvents()
        alter()
        context.broker.ready.emit()
    elif stage == "freeze":
        original = FakeDesktop.freeze

        def altered_freeze(desktop: FakeDesktop) -> FrozenScreen:
            image = original(desktop)
            alter()
            return image

        monkeypatch.setattr(FakeDesktop, "freeze", altered_freeze)
        context.service.capture_region()
    else:
        context.service.capture_region()
        qtbot.waitUntil(lambda: bool(context.service.overlays))
        alter()
        context.broker.feed(SelectionInput(InputKind.MOVE, Point(120, 120)))
    qtbot.waitUntil(lambda: bool(context.failures))
    assert "display layout changed" in context.failures[0]
    assert not context.captures
    assert not context.service.overlays
    assert not context.service.is_selecting
