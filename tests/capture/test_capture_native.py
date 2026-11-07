"""Native Windows capture regression with the real tray menu and desktop freeze.

Run with QT_QPA_PLATFORM=windows and --require-interactive-capture. This posts
WM_HOTKEY to this test's own GUI thread; it reserves no global shortcut and
never interacts with the user's installed VerdiClip or its settings.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
import time
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.test_popup_capture import InputTrace
from tests.ux.test_app_journeys import (
    App,
    make_app,
)

from verdiclip.capture.grabber import FrozenScreen, MssScreenSource
from verdiclip.capture.models import Capture
from verdiclip.shell.settings_dialog import SettingsDialog


def native_window_exposed(window: QWindow | None) -> bool:
    """An owned widget without a created native window is not ready yet."""
    return window is not None and window.isExposed()


class RealDesktop(MssScreenSource):
    """Freeze real pixels and record the input owner at that exact boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, str | bool]] = []

    @override
    def freeze(self) -> FrozenScreen:
        """Record popup ownership and GUI-thread execution before native capture."""
        app = QApplication.instance()
        assert app is not None
        self.frames.append(
            {
                "popup": InputTrace.name(QApplication.activePopupWidget()),
                "gui_thread": QThread.currentThread() is app.thread(),
            }
        )
        return super().freeze()


class NativeRun:
    """Drive real QWindow input after overlay activation and bound test cleanup."""

    def __init__(
        self,
        app: App,
        qt_app: QApplication,
        *,
        outcome: str,
        settings_setup: bool = False,
    ) -> None:
        self.app = app
        self.qt_app = qt_app
        self.outcome = outcome
        self.done = False
        self.scheduled = False
        self.errors: list[str] = []
        self.captures: list[Capture] = []
        self.phase = "settings-setup" if settings_setup else "capture-action"
        self.expired_overlay_count: int | None = None
        self.expired_selecting: bool | None = None
        self.posted = False
        self.triggered = False
        self.setup_started = time.perf_counter() if settings_setup else None
        self.setup_elapsed_ms: float | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.existing_settings = tuple(
            w for w in QApplication.topLevelWidgets() if isinstance(w, SettingsDialog)
        )
        self.readiness = QTimer()
        self.readiness.setInterval(10)
        self.readiness.timeout.connect(self._settings_ready)
        self.trace = InputTrace()
        self.qt_app.installEventFilter(self.trace)
        self.qt_app.focusWindowChanged.connect(self._focus_changed)
        self.app.controller.capture.captured.connect(self.captures.append)
        self.deadline = QTimer()
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._expired)
        self.deadline.start(5000)
        self.app.hotkeys.triggered.connect(self._hotkey_seen)

    @staticmethod
    def require_desktop(request: pytest.FixtureRequest) -> QApplication:
        """Enforce real desktop prerequisites without hiding capture failures."""
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            pytest.skip("Requires native Windows Qt; run with QT_QPA_PLATFORM=windows")
        session = DesktopSession.current()
        reason = session.unavailable_reason() if session is not None else None
        if reason is not None:
            if request.config.getoption("--require-interactive-capture"):
                pytest.fail(reason)
            pytest.skip(reason)
        qapp = QApplication.instance()
        assert isinstance(qapp, QApplication)
        return qapp

    @staticmethod
    def make(request: pytest.FixtureRequest, source: RealDesktop) -> App:
        """Obtain the registered app fixture without changing any user services."""
        factory = request.getfixturevalue(make_app.__name__)
        assert callable(factory)
        app = factory(screens=source)
        assert isinstance(app, App)
        return app

    def record(
        self,
        directory: Path,
        scenario: str,
        frames: list[dict[str, str | bool]],
    ) -> None:
        """Retain diagnostic events without saving captured desktop pixels."""
        if self.errors:
            sys.stdout.write(
                "NATIVE_CAPTURE_STAGE="
                + json.dumps(
                    {
                        "scenario": scenario,
                        "outcome": self.outcome,
                        "phase": self.phase,
                        "hotkey_posted": self.posted,
                        "hotkey_triggered": self.triggered,
                        "freeze_count": len(frames),
                        "overlay_count_at_timeout": self.expired_overlay_count,
                        "selecting_at_timeout": self.expired_selecting,
                        "setup_elapsed_ms": self.setup_elapsed_ms,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        (directory / "native-capture-trace.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "outcome": self.outcome,
                    "errors": self.errors,
                    "frames": frames,
                    "events": self.trace.events,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def open_dialog(self, scenario: str) -> None:
        """Keep actual tray dialog entry, separating only settings setup."""
        if scenario == "settings":
            self.await_settings()
        else:
            QTimer.singleShot(0, self.post_hotkey)
        self.app.controller.tray.action(scenario).trigger()

    def save_and_close(
        self, directory: Path, scenario: str, source: RealDesktop
    ) -> None:
        """Retire observers and source independently of diagnostic writing."""
        try:
            self.record(directory, scenario, source.frames)
        finally:
            try:
                self.close()
            finally:
                source.close()

    def await_settings(self) -> None:
        """Observe the actual new owned dialog, without warming or opening another."""
        self.readiness.start()

    def _settings_ready(self) -> None:
        """Dispatch once only after bounded, actual modal-window readiness."""
        try:
            self._probe_settings_ready()
        except (RuntimeError, TypeError, ValueError):
            self.errors.append("Owned settings readiness observation failed")
            self.finish()

    def _probe_settings_ready(self) -> None:
        """Require the exact new modal, then recheck the setup deadline."""
        if self.done or self.phase != "settings-setup":
            return
        assert self.setup_started is not None
        self.setup_elapsed_ms = (time.perf_counter() - self.setup_started) * 1000
        if self.setup_elapsed_ms >= 5000:
            self._expired()
            return
        if self.settings_dialog is None:
            candidates = [
                w
                for w in QApplication.topLevelWidgets()
                if isinstance(w, SettingsDialog) and w not in self.existing_settings
            ]
            if len(candidates) != 1:
                return
            self.settings_dialog = candidates[0]
        dialog = self.settings_dialog
        window = dialog.windowHandle()
        if not (
            dialog.isVisible()
            and QApplication.activeModalWidget() is dialog
            and dialog.isModal()
            and native_window_exposed(window)
        ):
            return
        self.setup_elapsed_ms = (time.perf_counter() - self.setup_started) * 1000
        if self.setup_elapsed_ms >= 5000:
            self._expired()
            return
        self.readiness.stop()
        self.phase = "capture-action"
        self.deadline.start(5000)
        self.post_hotkey()

    def _hotkey_seen(self, action: str) -> None:
        """Retain dispatch evidence without replacing the production handler."""
        if action == "region":
            self.triggered = True

    def post_hotkey(self) -> None:
        """Post the actual Windows message to only this test's native event filter."""
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
        posted = api.PostThreadMessageW(
            ctypes.windll.kernel32.GetCurrentThreadId(),
            0x0312,
            identifier,
            0,
        )
        self.posted = bool(posted)
        if not posted:
            self.errors.append(f"PostThreadMessage failed: {ctypes.get_last_error()}")
            self.finish()

    def _focus_changed(self, _window: object) -> None:
        """Wait for a genuine native overlay activation, then leave that dispatch."""
        service = self.app.controller.capture
        focus = QGuiApplication.focusWindow()
        if not self.scheduled and any(
            focus is o.windowHandle() for o in service.overlays
        ):
            self.scheduled = True
            QTimer.singleShot(0, self._complete)

    def _complete(self) -> None:
        """Exercise native Qt key/mouse routing rather than QWidget.sendEvent."""
        service = self.app.controller.capture
        try:
            assert service.overlays, "Selection disappeared before receiving input"
            window = service.overlays[0].windowHandle()
            assert window is not None
            popup = QApplication.activePopupWidget()
            if popup is not None:
                QTest.keyClick(window, Qt.Key.Key_Escape)
                self.errors.append(
                    "Popup retained input; "
                    f"selecting after Escape={service.is_selecting}"
                )
            elif self.outcome == "escape":
                QTest.keyClick(window, Qt.Key.Key_Escape)
                assert not service.is_selecting, "Native Escape must finish capture"
                assert self.captures == [], "Escape must not deliver an image"
            else:
                QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(90, 90))
                QTest.mouseMove(window, QPoint(160, 140))
                QTest.mouseRelease(
                    window, Qt.MouseButton.LeftButton, pos=QPoint(160, 140)
                )
                assert len(self.captures) == 1, "Native drag must deliver exactly once"
                assert self.captures[0].image.size().width() == 70
                assert self.captures[0].image.size().height() == 50
                assert not service.is_selecting
        except (AssertionError, RuntimeError) as error:
            self.errors.append(str(error))
        finally:
            self.finish()

    def _expired(self) -> None:
        """Fail and clean up even if Qt never transfers focus to the overlay."""
        if self.phase == "settings-setup" and self.setup_started is not None:
            self.setup_elapsed_ms = (time.perf_counter() - self.setup_started) * 1000
        self.expired_overlay_count = len(self.app.controller.capture.overlays)
        self.expired_selecting = self.app.controller.capture.is_selecting
        self.errors.append(
            "Owned settings setup never became ready before the deadline"
            if self.phase == "settings-setup"
            else "Native selection never acquired input before the deadline"
        )
        self.finish()

    def finish(self) -> None:
        """End the test-owned selection and nested dialogs, preserving other apps."""
        if self.done:
            return
        self.done = True
        self.deadline.stop()
        self.readiness.stop()
        self.app.controller.capture.cancel()
        self.app.controller.tray.menu.close()
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, (QMessageBox, SettingsDialog, QDialog)):
                widget.reject()

    def close(self) -> None:
        """Remove observers after the test's owned event loops have exited."""
        try:
            self.finish()
        finally:
            try:
                self.qt_app.focusWindowChanged.disconnect(self._focus_changed)
            finally:
                try:
                    self.qt_app.removeEventFilter(self.trace)
                finally:
                    self.app.hotkeys.triggered.disconnect(self._hotkey_seen)


@pytest.mark.ux
@pytest.mark.parametrize(
    "scenario",
    ["tray", "tray-exec", "menu-action", "main", "about", "settings", "modal"],
)
@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_native_capture_input_ownership(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
    scenario: str,
    outcome: str,
) -> None:
    """UX-G-10: real Windows capture works through tray/modal native input routing."""
    qapp = NativeRun.require_desktop(request)
    tmp_path = request.getfixturevalue("tmp_path")
    assert isinstance(tmp_path, Path)
    source = RealDesktop()
    app = NativeRun.make(request, source)
    run = NativeRun(app, qapp, outcome=outcome, settings_setup=scenario == "settings")
    menu = app.controller.tray.menu
    try:
        if scenario in {"tray", "tray-exec", "menu-action"}:
            if scenario == "menu-action":
                menu.setActiveAction(app.controller.tray.action("region"))
                QTimer.singleShot(
                    0, lambda: QTest.keyClick(menu.windowHandle(), Qt.Key.Key_Return)
                )
                menu.exec(QPoint(80, 80))
            elif scenario == "tray-exec":
                QTimer.singleShot(0, run.post_hotkey)
                menu.exec(QPoint(80, 80))
            else:
                menu.popup(QPoint(80, 80))
                assert QApplication.activePopupWidget() is menu
                run.post_hotkey()
        elif scenario in {"about", "settings"}:
            run.open_dialog(scenario)
        else:
            image = QImage(160, 100, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            editor = app.controller.open_editor(image, delivered=True)
            if scenario == "modal":
                dialog = QDialog(editor)
                dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
                QTimer.singleShot(0, run.post_hotkey)
                dialog.exec()
            else:
                run.post_hotkey()
        qtbot.waitUntil(lambda: run.done, timeout=6000)
        assert run.errors == [], (
            f"{scenario}/{outcome}: {run.errors}; frames={source.frames}"
        )
        assert source.frames == [{"popup": "none", "gui_thread": True}]
        assert QApplication.activePopupWidget() is None
    finally:
        run.save_and_close(tmp_path, scenario, source)
