"""Synthetic-only real delivery scenarios, shared by portable/native tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon, QWidget
from pytestqt.qtbot import QtBot
from tests.capture.production_matrix import StrictFixtureSnapshot
from tests.shell.test_passive_capture_delivery import PreservedUi
from tests.ux.test_app_journeys import App, AppFactory

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Rect
from verdiclip.settings import CaptureSettings, Settings

CASES = ("popup-save-present", "modal-provider-error", "popup-write-retry")


def forbid_devices(patch: pytest.MonkeyPatch) -> None:
    """Delivery must not acquire input/screens, global keys or shared clipboard."""

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        pytest.fail("Synthetic delivery attempted a forbidden device/shell boundary")

    for target in (
        "verdiclip.capture.input_broker.NativeInputBroker.start",
        "verdiclip.capture.grabber.MssScreenSource.__init__",
        "verdiclip.platform.hotkeys.Win32HotkeyApi.__init__",
        "verdiclip.output.delivery.ImageDelivery.copy",
    ):
        patch.setattr(target, forbidden)
    patch.setattr(QDesktopServices, "openUrl", forbidden)


def synthetic_capture() -> Capture:
    """These pixels are generated, never a screenshot or user clipboard data."""
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0xFF27835A)
    return Capture(
        image,
        CaptureMode.REGION,
        Rect(0, 0, 40, 30),
        "Synthetic delivery fixture",
        datetime(2026, 10, 26, tzinfo=UTC),
        preserve_foreground=True,
    )


def event_boundary(qtbot: QtBot) -> None:
    """Observe a queued Qt event boundary, without inventing a settling sleep."""
    reached: list[bool] = []
    QTimer.singleShot(0, lambda: reached.append(True))
    qtbot.waitUntil(lambda: bool(reached), timeout=3000)


class Scene:
    """Observe only declared hosts, excluding their intentional fixture teardown."""

    def __init__(
        self,
        anchor: QWidget,
        qtbot: QtBot,
        *,
        modal: bool,
        admit: Callable[[QWidget, QWidget], tuple[int, ...] | None] | None = None,
    ) -> None:
        self.anchor, self.qtbot = anchor, qtbot
        self.original_page = (
            anchor.pages[anchor.tab_bar.currentIndex()]
            if isinstance(anchor, EditorWindow)
            else None
        )
        qtbot.addWidget(anchor)
        anchor.show()
        if modal:
            dialog = QDialog(anchor)
            dialog.setWindowTitle("VerdiClip owned delivery modal")
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
            dialog.show()
            self.surface: QWidget = dialog
        else:
            menu = QMenu("VerdiClip owned delivery popup", anchor)
            menu.addAction("Owned inert item")
            action = menu.addAction("Keep this active action")
            menu.setActiveAction(action)
            menu.popup(anchor.mapToGlobal(anchor.rect().center()))
            self.surface = menu
        qtbot.waitUntil(
            lambda: all(
                w.isVisible()
                and w.windowHandle() is not None
                and w.windowHandle().isExposed()
                for w in (anchor, self.surface)
            ),
            timeout=3000,
        )
        qtbot.waitUntil(
            lambda: (
                (
                    QApplication.activeModalWidget()
                    if modal
                    else QApplication.activePopupWidget()
                )
                is self.surface
            ),
            timeout=3000,
        )
        self.allowed_foreground = admit(anchor, self.surface) if admit else None
        event_boundary(qtbot)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self.snapshot = StrictFixtureSnapshot(app, (anchor, self.surface))
        self.events = PreservedUi((anchor, self.surface))
        self.states = tuple(w.windowState() for w in (anchor, self.surface))
        self.geometries = tuple(w.geometry() for w in (anchor, self.surface))

    def check(self) -> None:
        """Require all protected state after a bounded Qt event boundary."""
        event_boundary(self.qtbot)
        assert not self.snapshot.errors()
        assert not self.events.events
        assert self.states == tuple(
            w.windowState() for w in (self.anchor, self.surface)
        )

        assert self.geometries == tuple(
            w.geometry() for w in (self.anchor, self.surface)
        )

    def stop(self) -> None:
        """Stop observations before explicit fixture teardown."""
        self.snapshot.close()
        self.events.stop()

    def close(self) -> None:
        """Close only this explicitly created popup/modal fixture."""
        self.stop()
        self.surface.close()


def run_scenario(  # noqa: PLR0913 - explicit fixture/observation boundaries
    case: str,
    factory: AppFactory,
    qtbot: QtBot,
    patch: pytest.MonkeyPatch,
    private: Path,
    *,
    foreground: Callable[[], int],
    admit: Callable[[QWidget, QWidget], tuple[int, ...] | None] | None = None,
) -> dict[str, object]:
    """Keep real canonical delivery, QSaveFile, presentation and notifications."""
    assert case in CASES
    forbid_devices(patch)
    app: App = factory(
        Settings(
            capture=CaptureSettings(
                open_editor=case != "popup-write-retry",
                save_to_file=True,
                copy_to_clipboard=False,
            )
        )
    )
    image = synthetic_capture()
    notifications = _observe_notifications(app, patch)
    anchor: QWidget = QWidget()
    anchor.setWindowTitle("VerdiClip owned delivery anchor")
    if case == "modal-provider-error":
        anchor = app.controller.open_editor(image.image, delivered=True)
    scene = Scene(anchor, qtbot, modal=case == "modal-provider-error", admit=admit)
    baseline = foreground()
    assert baseline > 0
    if scene.allowed_foreground is not None:
        assert baseline in scene.allowed_foreground
    shown = _observe_presentations(app, patch, foreground)
    directory = app.controller.settings.output.directory
    assert directory.resolve().is_relative_to(private.resolve())
    try:
        if case == "popup-write-retry":
            directory.write_text("Owned fixture forces a real folder failure")
        app.controller.capture.captured.emit(image)
        scene.check()
        assert foreground() == baseline
        _settle_protected(case, app, scene, image, notifications)
        scene.check()
        assert foreground() == baseline
        _assert_png(directory, image.image)
        if case == "popup-save-present":
            scene.close()
            host = app.controller.editors[0]
            qtbot.waitUntil(host.isVisible, timeout=3000)
            event_boundary(qtbot)
            assert shown
            assert all(a == b and not activate for a, b, activate, _ in shown)
            assert any(transition for _, _, _, transition in shown)
            assert foreground() == shown[-1][0]
        assert not app.controller.capture.is_selecting
        assert not app.controller.capture.input_active
        return {
            "case": case,
            "pixels": "synthetic only",
            "input": False,
            "clipboard": False,
            "real_private_png": True,
            "foreground_samples_unchanged": True,
            "native_balloon_visibility_certified": False,
            "notification_requests_delegated": len(notifications),
            "retry_click": "Qt signal routing; no physical notification click",
        }
    finally:
        scene.close()


def _settle_protected(
    case: str,
    app: App,
    scene: Scene,
    image: Capture,
    notifications: list[str],
) -> None:
    """Assert real output/recovery while the original surface stays protected."""
    directory = app.controller.settings.output.directory
    anchor = scene.anchor
    if case == "popup-write-retry":
        pending = app.controller.pending_deliveries
        assert len(pending) == 1
        original = pending[0]
        assert original.image == image.image
        assert not app.controller.editors
        assert notifications[-1] == "Could not deliver the capture"
        assert directory.is_file()
        directory.unlink()
        directory.mkdir()
        app.controller.tray.messageClicked.emit()
        assert not app.controller.pending_deliveries
        app.controller.deliver_reviewed_capture(original)
        assert not app.controller.editors
    elif case == "modal-provider-error":
        assert isinstance(anchor, EditorWindow)
        assert app.controller.editors == (anchor,)
        assert len(anchor.pages) == 2
        assert anchor.pages[anchor.tab_bar.currentIndex()] is scene.original_page
        app.controller.capture.failed.emit("Synthetic provider failure")
        assert notifications[-1] == "Capture failed"
    else:
        assert len(app.controller.editors) == 1
        assert not app.controller.editors[0].isVisible()


def _assert_png(directory: Path, image: QImage) -> None:
    """Decode the one real private save rather than trusting a success receipt."""
    files = list(directory.glob("*.png"))
    assert len(files) == 1
    assert QImage(str(files[0])) == image


def _observe_notifications(app: App, patch: pytest.MonkeyPatch) -> list[str]:
    """Record requested titles while delegating to the real tray presentation."""
    notifications: list[str] = []
    notify = app.controller.tray.showMessage

    def observed_notify(
        title: str, message: str, icon: QSystemTrayIcon.MessageIcon, duration: int
    ) -> None:
        notifications.append(title)
        notify(title, message, icon, duration)

    patch.setattr(app.controller.tray, "showMessage", observed_notify)
    return notifications


def _observe_presentations(
    app: App,
    patch: pytest.MonkeyPatch,
    foreground: Callable[[], int],
) -> list[tuple[int, int, bool, bool]]:
    """Delegate real presentation, recording positive foreground and visibility."""
    shown: list[tuple[int, int, bool, bool]] = []
    present = app.controller.workspace._present_host

    def observed_present(host: EditorWindow, *, activate: bool) -> None:
        before = foreground()
        assert before > 0
        was_visible = host.isVisible()
        present(host, activate=activate)
        after = foreground()
        assert after > 0
        shown.append((before, after, activate, not was_visible and host.isVisible()))

    patch.setattr(app.controller.workspace, "_present_host", observed_present)
    return shown
