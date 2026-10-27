"""Prepared production-hook qualification. Never execute without a handoff."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QElapsedTimer, QPoint, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.menu_peer import MenuPeer
from tests.capture.preservation_harness import HarnessBudgets, PreservationSnapshot
from tests.capture.production_matrix import (
    CASES,
    PHYSICAL_CASES,
    DriverJournal,
    InputProfile,
    MatrixCase,
    MemoryCaptureSink,
    ProductionBrokerDriver,
    StrictFixtureSnapshot,
    cleanup_confirmed,
    fixture_foreground_errors,
    native_action_errors,
)
from tests.capture.test_capture_native import NativeRun, RealDesktop
from tests.ux.test_app_journeys import App, make_app

from verdiclip.geometry import Rect
from verdiclip.platform.windows import WindowLocator
from verdiclip.settings import CaptureSettings, Settings

pytestmark = pytest.mark.native_desktop


@pytest.fixture(autouse=True)
def require_broker_handoff(request: pytest.FixtureRequest) -> None:
    """Fail/skip before creating a fixture host, child or input worker."""
    if not request.config.getoption("--native-broker-handoff"):
        pytest.skip("Production broker matrix requires explicit exclusive handoff")
    head = os.environ.get("VERDICLIP_QUALIFICATION_HEAD", "")
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        pytest.fail("Run through the provenance-recording native matrix script")
    if sys.platform != "win32":
        pytest.fail("Production broker qualification requires Windows")
    session = DesktopSession.current()
    if session is None or session.state != 0 or session.flags != 1:
        pytest.fail(
            "Production broker requires a positively known active/unlocked session"
        )


class ExternalSnapshot(StrictFixtureSnapshot):
    """Check the actual foreign menu owner/state as well as Qt host identity."""

    def __init__(
        self, app: QApplication, windows: tuple[QWidget, ...], peer: MenuPeer
    ) -> None:
        super().__init__(app, windows)
        self.peer = peer

    @override
    def errors(self) -> list[str]:
        return super().errors() + self.peer.preservation_errors()


class NativeTransport:
    """Observe real cleanup and publish external instructions; no replay events."""

    def __init__(self, journal: DriverJournal, driver: ProductionBrokerDriver) -> None:
        self.journal, self.driver = journal, driver

    def send(self, outcome: str, area: Rect) -> None:
        """An external guarded driver must perform the declared action."""
        self.journal.request(outcome, area)

    def cleanup_errors(self) -> list[str]:
        """No output-file acknowledgement can substitute for native cleanup."""
        return self.driver.cleanup_errors()


class ProductionRun(NativeRun):
    """Reuse the common preservation coordinator with an actual native worker."""

    def __init__(
        self,
        app: App,
        qapp: QApplication,
        transport: NativeTransport,
        case: MatrixCase,
        peer: MenuPeer | None,
    ) -> None:
        super().__init__(app, qapp, transport, outcome=case.outcome)
        self.case, self.peer, self.driver = case, peer, transport.driver
        self.journal = transport.journal
        self._declared_popup: QMenu | None = None
        self._binding_action: Callable[[], None] | None = None
        self._binding_elapsed = QElapsedTimer()
        self._binding_poll = QTimer()
        self._binding_poll.setInterval(10)
        self._binding_poll.timeout.connect(self._await_binding)
        self._platform_start = len(self.driver.platforms)
        self._final_sequence = 0
        self._final_checked = False
        self._final_elapsed = QElapsedTimer()
        self._final_poll = QTimer()
        self._final_poll.setInterval(10)
        self._final_poll.timeout.connect(self._finish_hosts)
        self._peer_poll = QTimer()
        self._peer_poll.setInterval(100)
        if peer is not None:
            self._peer_poll.timeout.connect(peer.poll)
            self._peer_poll.start()

    @override
    def schedule(self, action: Callable[[], None]) -> None:
        """Let the guarded driver bind exposed owners before starting capture."""
        if self.done or self._binding_action is not None or self.harness is not None:
            raise ValueError("Native binding may be scheduled only once")
        self._binding_action = action
        self._binding_elapsed.start()
        self._binding_poll.start()

    def _await_binding(self) -> None:
        if self.done or self._binding_action is None:
            self._binding_poll.stop()
            return
        try:
            if self._binding_elapsed.elapsed() >= 20_000:
                raise TimeoutError(
                    "Driver target binding deadline expired before capture"
                )
            windows = tuple(w for w in self.qapp.topLevelWidgets() if w.isVisible())
            if not windows or any(
                w.windowHandle() is None or not w.windowHandle().isExposed()
                for w in windows
            ):
                return
            area = self.selection_area()
            if self.journal.binding is None:
                self.journal.prepare_binding(area)
            if not self.journal.binding_ready(area):
                return
            if self._binding_elapsed.elapsed() >= 20_000:
                raise TimeoutError(
                    "Driver target binding deadline expired before capture"
                )
            action, self._binding_action = self._binding_action, None
            self._binding_poll.stop()
            super().schedule(action)
        except (OSError, ValueError, RuntimeError, StopIteration) as error:
            self.errors.append(str(error))
            self._binding_action = None
            self._binding_poll.stop()
            self._finish_hosts()

    @override
    def budgets(self) -> HarnessBudgets:
        """Allow ten seconds for guarded/human input without extending the worker."""
        area = self.selection_area()
        if not self.journal.binding_ready(area):
            raise ValueError("Driver binding is no longer ready before capture")
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        allowed = tuple(
            int(window.winId())
            for window in self.qapp.topLevelWidgets()
            if window.isVisible()
        )
        if self.peer is not None:
            allowed += (self.peer.owner,)
        errors = fixture_foreground_errors(int(api.GetForegroundWindow() or 0), allowed)
        if errors:
            raise ValueError(errors[0])
        return HarnessBudgets(
            deadline_ms=10_000, selection_area=area, close_snapshot_on_finish=False
        )

    @override
    def _menu_scene(self, scenario: str) -> None:
        """Expose a normal owned anchor; popup-only processes can be undiscoverable."""
        image = QImage(640, 480, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        anchor = self.app.controller.open_editor(image, delivered=True)
        anchor.setWindowTitle("VerdiClip owned broker fixture")
        self.own_host(anchor)
        if self.case.profile is InputProfile.DEFAULT:
            menu = self.app.controller.tray.menu
            self._declared_popup = menu
            menu.setParent(anchor, menu.windowFlags())
            super()._menu_scene(scenario)
            return
        menu = QMenu("VerdiClip test-owned pointer fixture", anchor)
        self._declared_popup = menu
        menu.addAction("Inert fixture item")
        menu.addAction("Inert second item")
        self.own_host(menu)
        menu.popup(QPoint(80, 80))
        self.schedule(self.post_hotkey)

    @override
    def snapshot(self, windows: tuple[QWidget, ...]) -> PreservationSnapshot:
        if self.peer is None:
            return StrictFixtureSnapshot(self.qapp, windows)
        return ExternalSnapshot(self.qapp, windows, self.peer)

    @override
    def selection_area(self) -> Rect:
        """Publish only a small rectangle inside an explicitly owned native host."""
        if self.peer is not None:
            state = self.peer.state
            if state is None or self.peer.preservation_errors():
                raise ValueError("External native menu was not ready")
            bounds = state.bounds
            self.journal.owned_handles = (state.owner,)
            self.journal.binding_handle = state.owner
        else:
            if self.case.scene == "qt-menu" and (
                self._declared_popup is None
                or not self._declared_popup.isVisible()
                or QApplication.activePopupWidget() is not self._declared_popup
            ):
                raise ValueError("Declared fixture popup was lost before capture")
            visible = tuple(host for host in reversed(self.hosts) if host.isVisible())
            if not visible:
                raise ValueError("Declared fixture hosts are no longer visible")
            host = next(
                (host for host in visible if isinstance(host, QMenu)), visible[0]
            )
            api = ctypes.WinDLL("user32", use_last_error=True)
            api.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
            api.GetWindowRect.restype = w.BOOL
            rect = w.RECT()
            if not api.GetWindowRect(int(host.winId()), ctypes.byref(rect)):
                raise OSError("Could not verify fixture native rectangle")
            bounds = Rect(
                rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
            )
            anchor = host
            while (parent := anchor.parentWidget()) is not None:
                anchor = parent
            self.journal.owned_handles = tuple(
                dict.fromkeys((int(anchor.winId()), int(host.winId())))
            )
            self.journal.binding_handle = int(anchor.winId())
        area = Rect(
            bounds.x + bounds.width * 0.2,
            bounds.y + bounds.height * 0.5,
            min(40, bounds.width * 0.3),
            min(30, bounds.height * 0.25),
        )
        if area.is_empty:
            raise ValueError("Fixture is too small for a guarded input request")
        return area

    @override
    def _complete(self) -> None:
        self.errors.extend(
            native_action_errors(self.driver, self.outcome, start=self._platform_start)
        )
        super()._complete()

    @override
    def _finish_hosts(self) -> None:
        """Never dismiss even a fixture menu while a worker still owns input."""
        if self.done:
            return
        self._binding_poll.stop()
        self._binding_action = None
        try:
            self.journal.retire()
        except OSError as error:
            self.errors.append(f"Driver retirement receipt failed: {error}")
        if not cleanup_confirmed(
            self.driver, selecting=self.app.controller.capture.is_selecting
        ):
            self.errors.append("Fixture teardown deferred until native cleanup")
            return
        if self.peer is not None and not self._final_checked:
            if not self._final_sequence:
                self._peer_poll.stop()
                sequence = self.peer.poll()
                if sequence is None:
                    self.errors.extend(self.peer.preservation_errors())
                    self._final_checked = True
                    self._finish_hosts()
                    return
                self._final_sequence = sequence
                self._final_elapsed.start()
                self._final_poll.start()
                return
            if not self.peer.acknowledged(self._final_sequence):
                if self._final_elapsed.elapsed() < 2000:
                    return
                self.errors.append(
                    "No correlated post-terminal foreign-menu observation"
                )
            self._final_checked = True
            self._final_poll.stop()
            self.errors.extend(self.peer.preservation_errors())
        self._peer_poll.stop()
        if self.harness is not None:
            self.errors.extend(self.harness.snapshot.errors())
            self.harness.snapshot.close()
        super()._finish_hosts()

    @override
    def record(self, directory: Path, scenario: str, source: RealDesktop) -> None:
        """Label injected/native/raw evidence separately; omit desktop pixels."""
        receipt = self.driver.receipt()
        raw = sum(p.raw_packets for p in self.driver.platforms[self._platform_start :])
        receipt["physical_raw_input_qualified"] = (
            self.case.profile is InputProfile.PHYSICAL
            and not self.errors
            and isinstance(raw, int)
            and raw > 0
            and not self.driver.cleanup_errors()
        )
        receipt.update(
            {
                "output_lane": (
                    "RAM-only canonical observer; production delivery unqualified"
                ),
                "scene": scenario,
                "outcome": self.outcome,
                "errors": self.errors,
                "frames": source.frames,
                "foreign_owner": self.peer.owner if self.peer else None,
                "driver_bound_before_capture": self.journal.bound,
                "binding_nonce": self.journal.binding.get("nonce")
                if self.journal.binding is not None
                else None,
            }
        )
        (directory / "production-native-trace.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )


@pytest.mark.parametrize(
    "case",
    [*CASES, *PHYSICAL_CASES],
    ids=lambda c: f"{c.scene}-{c.profile}-{c.outcome}-x{c.repeat}",
)
def test_production_broker_preserves_declared_owners(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: MatrixCase,
) -> None:
    """Only real terminal/cleanup signals and unchanged hosts establish a pass."""
    if case.profile is InputProfile.PHYSICAL and not request.config.getoption(
        "--native-broker-physical"
    ):
        pytest.skip("Physical raw-pointer lane requires the human observation window")
    assert QGuiApplication.platformName() == "windows"
    qapp = QApplication.instance()
    assert isinstance(qapp, QApplication)
    driver = ProductionBrokerDriver(case.profile)
    assert driver.native
    monkeypatch.setattr("verdiclip.capture.service.make_input_broker", driver.broker)
    factory = request.getfixturevalue(make_app.__name__)
    assert callable(factory)
    source = RealDesktop()
    settings = replace(
        Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
    )
    app = factory(settings=settings, screens=source, windows=WindowLocator())
    assert isinstance(app, App)
    sink = MemoryCaptureSink()
    monkeypatch.setattr(app.controller, "deliver_reviewed_capture", sink.accept)
    peer = MenuPeer() if case.scene == "win32-menu" else None
    try:
        for index in range(case.repeat):
            journal = DriverJournal(tmp_path / f"request-{index}", case)
            run = ProductionRun(app, qapp, NativeTransport(journal, driver), case, peer)
            try:
                if peer is not None:
                    image = QImage(640, 480, QImage.Format.Format_RGB32)
                    image.fill(0xFFFFFFFF)
                    app.controller.open_editor(image, delivered=True)
                    peer.start(handoff=True)
                    qtbot.waitUntil(
                        lambda: bool(peer.state) and not peer.preservation_errors(),
                        timeout=8000,
                    )
                    run.schedule(run.post_hotkey)
                else:
                    run.enter_scene("modal" if case.scene == "owned-modal" else "tray")
                # Distinct 20s bind-only phase + unchanged 14s completion observer.
                qtbot.waitUntil(lambda active=run: active.done, timeout=34000)
                assert not run.errors
                assert not driver.cleanup_errors()
                assert journal.requests
                assert not native_action_errors(
                    driver, case.outcome, start=run._platform_start
                )
                if case.profile is InputProfile.PHYSICAL:
                    assert (
                        sum(
                            p.raw_packets
                            for p in driver.platforms[run._platform_start :]
                        )
                        > 0
                    )
            finally:
                app.controller.capture.cancel()
                qtbot.waitUntil(run.input_idle, timeout=14000)
                (tmp_path / f"request-{index}").mkdir(parents=True, exist_ok=True)
                run.record(tmp_path / f"request-{index}", case.scene, source)
                run.close()
    finally:
        if peer is not None and cleanup_confirmed(
            driver, selecting=app.controller.capture.is_selecting
        ):
            peer.close()
        source.close()
