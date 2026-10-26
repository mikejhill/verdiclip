"""Native Qt/MSS preservation with production broker scheduling and replay input.

This supplemental suite posts WM_HOTKEY to its own fixture thread. It does not
install OS hooks, send OS input, reserve global keys or qualify physical input.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, override

import pytest
from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.desktop import DesktopSession
from tests.capture.harness_transport import ReplayWorkerTransport
from tests.capture.preservation_harness import (
    CaptureHarness,
    HarnessBudgets,
    PreservationSnapshot,
)
from tests.capture.production_matrix import MemoryCaptureSink
from tests.capture.test_popup_capture import InputTrace
from tests.ux.test_app_journeys import App, make_app

from verdiclip.capture.grabber import FrozenScreen, MssScreenSource
from verdiclip.geometry import Rect
from verdiclip.settings import CaptureSettings, Settings
from verdiclip.shell.settings_dialog import SettingsDialog

pytestmark = pytest.mark.native_desktop


class HarnessInput(Protocol):
    """The coordinator does not infer real input from a driver's receipt."""

    @abstractmethod
    def send(self, outcome: str, area: Rect) -> None:
        """Issue a replay event or an external-driver instruction explicitly."""

    @abstractmethod
    def cleanup_errors(self) -> list[str]:
        """Report actual worker cleanup, never merely hidden capture surfaces."""


class RealDesktop(MssScreenSource):
    """Record freeze ownership without persisting desktop pixels."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, str | bool]] = []

    @override
    def freeze(self) -> FrozenScreen:
        """Keep the original popup present at the native freeze boundary."""
        app = QApplication.instance()
        self.frames.append(
            {
                "popup": InputTrace.name(QApplication.activePopupWidget()),
                "gui_thread": isinstance(app, QApplication)
                and QThread.currentThread() is app.thread(),
            }
        )
        return super().freeze()


class NativeRun:
    """Wait for host exposure and broker readiness, never overlay activation."""

    def __init__(
        self,
        app: App,
        qapp: QApplication,
        transport: HarnessInput,
        *,
        outcome: str,
    ) -> None:
        self.app, self.qapp, self.transport = app, qapp, transport
        self.outcome = outcome
        self.done = False
        self.errors: list[str] = []
        self.harness: CaptureHarness | None = None
        self.expected_popup = "none"
        self.hosts: list[QWidget] = [app.controller.tray.menu]
        self._action: Callable[[], None] | None = None
        self._elapsed = QElapsedTimer()
        self._exposure = QTimer()
        self._exposure.setInterval(5)
        self._exposure.timeout.connect(self._exposed)

    @staticmethod
    def require_desktop(request: pytest.FixtureRequest) -> QApplication:
        """Refuse known unavailable sessions before creating any native fixture."""
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            reason = "Requires native Windows Qt; set QT_QPA_PLATFORM=windows"
            if request.config.getoption("--require-interactive-capture"):
                pytest.fail(reason)
            pytest.skip(reason)
        session = DesktopSession.current()
        reason = session.unavailable_reason() if session is not None else None
        if reason is not None:
            if request.config.getoption("--require-interactive-capture"):
                pytest.fail(reason)
            pytest.skip(reason)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        return app

    @staticmethod
    def make(request: pytest.FixtureRequest, source: RealDesktop) -> App:
        """Use temporary files and fake registrations, never the user's clipboard."""
        factory = request.getfixturevalue(make_app.__name__)
        assert callable(factory)
        settings = replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
        app = factory(settings=settings, screens=source)
        assert isinstance(app, App)
        patch = request.getfixturevalue("monkeypatch")
        assert isinstance(patch, pytest.MonkeyPatch)
        sink = MemoryCaptureSink()
        patch.setattr(app.controller, "deliver_reviewed_capture", sink.accept)
        return app

    def schedule(self, action: Callable[[], None]) -> None:
        """Queue dispatch after the pre-existing native host has been exposed."""
        if self.done or self.harness is not None or self._action is not None:
            raise ValueError("A native harness run may be scheduled only once")
        self._action = action
        self._elapsed.start()
        self._exposure.start()

    def own_host(self, host: QWidget) -> None:
        """Declare a fixture-created host explicitly, never infer modal ownership."""
        if host not in self.hosts:
            self.hosts.append(host)

    def _exposed(self) -> None:
        """Refuse stale setup callbacks and dispatch only after actual exposure."""
        if self.done or self.harness is not None:
            return
        windows = tuple(w for w in QApplication.topLevelWidgets() if w.isVisible())
        if self._elapsed.elapsed() >= 5000:
            self.errors.append("Fixture hosts did not become exposed")
            self._finish_hosts()
            return
        if not windows or any(
            w.windowHandle() is None or not w.windowHandle().isExposed()
            for w in windows
        ):
            return
        self._exposure.stop()
        self.expected_popup = InputTrace.name(QApplication.activePopupWidget())
        try:
            budgets = self.budgets()
        except (OSError, ValueError) as error:
            self.errors.append(str(error))
            self._finish_hosts()
            return
        self.harness = CaptureHarness(
            self.app.controller.capture,
            self.snapshot(windows),
            self.transport.send,
            outcome=self.outcome,
            budgets=budgets,
        )
        self.harness.completed.connect(self._complete)
        self.harness.start()
        action, self._action = self._action, None
        if action is not None:
            action()

    def post_hotkey(self) -> None:
        """Post WM_HOTKEY only to the test filter, without global registration."""
        key = self.app.hotkeys.registered["region"]
        identifier = next(
            identifier
            for identifier, combo in self.app.os_hotkeys.active.items()
            if combo == (key.win32_modifiers, key.virtual_key)
        )
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.PostThreadMessageW.argtypes = (
            ctypes.wintypes.DWORD,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        api.PostThreadMessageW.restype = ctypes.wintypes.BOOL
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.argtypes = ()
        kernel.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
        if not api.PostThreadMessageW(
            kernel.GetCurrentThreadId(), 0x0312, identifier, 0
        ):
            self.errors.append(f"PostThreadMessage failed: {ctypes.get_last_error()}")
            self._finish_hosts()

    def selection_area(self) -> Rect | None:
        """Replay defaults retain their original geometry policy."""
        return None

    def budgets(self) -> HarnessBudgets:
        """Keep replay defaults; production subclasses may choose bounded geometry."""
        return HarnessBudgets(selection_area=self.selection_area())

    def snapshot(self, windows: tuple[QWidget, ...]) -> PreservationSnapshot:
        """Permit an external-owner observer without replacing capture input."""
        return PreservationSnapshot(self.qapp, windows)

    def _complete(self) -> None:
        """Record cleanup/preservation before unwinding any owned modal loop."""
        if self.harness is not None:
            self.errors.extend(self.harness.errors)
        self.errors.extend(self.transport.cleanup_errors())
        self._finish_hosts()

    def _finish_hosts(self) -> None:
        """Unwind only explicitly owned host loops, after preservation assertions."""
        self._exposure.stop()
        self.done = True
        for host in reversed(self.hosts):
            if not isValid(host):
                continue
            if isinstance(host, QDialog):
                host.reject()
            elif isinstance(host, QMenu):
                host.close()

    def record(self, directory: Path, scenario: str, source: RealDesktop) -> None:
        """Label the evidence boundary and omit desktop pixels."""
        (directory / "native-capture-trace.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "outcome": self.outcome,
                    "native_gui": True,
                    "input_transport": "replay",
                    "output_lane": "RAM-only observer; production delivery unqualified",
                    "native_hooks": False,
                    "errors": self.errors,
                    "expected_popup": self.expected_popup,
                    "frames": source.frames,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def close(self) -> None:
        """Retire capture observers and this fixture's hosts, never other dialogs."""
        if self.harness is not None:
            self.harness.close()
        self.app.controller.capture.cancel()
        self._finish_hosts()

    def input_idle(self) -> bool:
        """Expose cleanup completion without a deferred lambda inside finally."""
        return not self.app.controller.capture.is_selecting

    def enter_scene(self, scenario: str) -> None:
        """Construct only explicit fixture hosts, sharing portable setup coverage."""
        if scenario in {"tray", "tray-exec", "nested-menu", "menu-action"}:
            self._menu_scene(scenario)
        elif scenario in {"about", "settings"}:
            dialog = (
                SettingsDialog(Settings())
                if scenario == "settings"
                else QMessageBox(QMessageBox.Icon.Information, "About", "Fixture")
            )
            self._modal_scene(dialog)
        else:
            image = QImage(160, 100, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            editor = self.app.controller.open_editor(image, delivered=True)
            if scenario == "modal":
                self._modal_scene(QDialog(editor))
            elif scenario == "main":
                self.schedule(self.post_hotkey)
            else:
                raise ValueError("Unknown fixture scene")

    def _modal_scene(self, dialog: QDialog) -> None:
        """Only the caller-created dialog is eligible for fixture teardown."""
        self.own_host(dialog)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        QTimer.singleShot(0, lambda: self.schedule(self.post_hotkey))
        dialog.exec()

    def _menu_scene(self, scenario: str) -> None:
        """Exercise nested loops or action callbacks without OS input synthesis."""
        menu = self.app.controller.tray.menu
        if scenario == "tray-exec":
            QTimer.singleShot(0, lambda: self.schedule(self.post_hotkey))
            menu.exec(QPoint(80, 80))
            return
        menu.popup(QPoint(80, 80))
        if scenario == "nested-menu":
            child = menu.addMenu("Fixture nested menu")
            child.addAction("Fixture item")
            child.popup(QPoint(200, 80))
            self.own_host(child)
        action = (
            self.app.controller.tray.action("region").trigger
            if scenario == "menu-action"
            else self.post_hotkey
        )
        self.schedule(action)


@pytest.mark.ux
@pytest.mark.parametrize(
    "scenario",
    [
        "tray",
        "tray-exec",
        "nested-menu",
        "menu-action",
        "main",
        "about",
        "settings",
        "modal",
    ],
)
@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_native_capture_presentation_preserves_existing_owners(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    outcome: str,
) -> None:
    """Native Qt/MSS plus replay input; OS-hook qualification remains separate."""
    qapp = NativeRun.require_desktop(request)
    transport = ReplayWorkerTransport()
    monkeypatch.setattr("verdiclip.capture.service.make_input_broker", transport.broker)
    source = RealDesktop()
    app = NativeRun.make(request, source)
    run = NativeRun(
        app,
        qapp,
        transport,
        outcome=outcome,
    )
    try:
        run.enter_scene(scenario)
        qtbot.waitUntil(lambda: run.done, timeout=8000)
        assert not run.errors
        assert source.frames == [{"popup": run.expected_popup, "gui_thread": True}]
        assert not app.controller.capture.is_selecting
    finally:
        directory = request.getfixturevalue("tmp_path")
        assert isinstance(directory, Path)
        run.record(directory, scenario, source)
        run.close()
        qtbot.waitUntil(run.input_idle, timeout=2000)
        source.close()
