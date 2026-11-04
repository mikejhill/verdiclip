"""One native product case using the existing driver journal and preservation checks."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import cast, override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.hosted_fixture_input import HostedFixtureClick
from tests.capture.production_matrix import (
    DriverJournal,
    InputProfile,
    MatrixCase,
    ProductionBrokerDriver,
    StrictFixtureSnapshot,
    cleanup_confirmed,
    fixture_foreground_errors,
    native_action_errors,
)
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_capture_native import RealDesktop
from tests.ux.test_app_journeys import App, AppFactory, make_app

from verdiclip.capture.presentation import PASSIVE_WINDOW_FLAGS
from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind
from verdiclip.capture.scroll.native_backend import (
    NativeBackendOptions,
    UiaFrameBackend,
)
from verdiclip.capture.scroll.targets import SemanticTargets
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery, UiaReference
from verdiclip.geometry import Rect
from verdiclip.platform.windows import WindowLocator
from verdiclip.settings import CaptureSettings, Settings


class PopupFailureTrace(QObject):
    """Retain only the first owned popup loss, never user content or stack locals."""

    def __init__(self, menu: QMenu, phase: Callable[[], str]) -> None:
        super().__init__()
        self.menu, self.phase = menu, phase
        self.first: dict[str, object] = {}
        menu.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.menu
            and not self.first
            and event.type() in {QEvent.Type.Close, QEvent.Type.Hide}
        ):
            self.first = {
                "event": event.type().name,
                "phase": self.phase(),
                "time": time.monotonic(),
                "stack": [
                    {
                        "file": Path(frame.filename).name,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_stack(limit=16)
                ],
            }
        return False


class ScrollbarCase:
    """External driver locally; explicit owned-input driver on disposable CI only."""

    def __init__(
        self,
        qtbot: QtBot,
        request: pytest.FixtureRequest,
        patch: pytest.MonkeyPatch,
        directory: Path,
    ) -> None:
        self.qtbot, self.directory = qtbot, directory
        self.input_deadline = time.monotonic() + 50
        self.driver = ProductionBrokerDriver(InputProfile.INJECTED, max_seconds=40)
        patch.setattr("verdiclip.capture.service.make_input_broker", self.driver.broker)
        self.source = RealDesktop()
        request.addfinalizer(self.source.close)
        factory = cast(AppFactory, request.getfixturevalue(make_app.__name__))
        self.app: App = factory(
            Settings(capture=CaptureSettings(open_editor=True)),
            screens=self.source,
            windows=WindowLocator(),
        )
        self.peer = ScrollPeer(directory / "provider")
        self.menu = QMenu("Owned scrollbar test popup")
        request.addfinalizer(self.close_owned_windows)
        self.process = UiaProcess(qualified=True, use_local_pipe=True)
        self.adapter = UiaAdapter(process=self.process)
        self.discoveries: list[UiaDiscovery] = []
        self.adapter.discovered.connect(
            lambda _request, discovery: self.discoveries.append(discovery)
        )
        self.passed = False
        self.snapshot: StrictFixtureSnapshot | None = None
        self.journal = DriverJournal(
            directory,
            MatrixCase("qt-scrollbar-popup", "scrollbar-click", InputProfile.INJECTED),
            input_generated_by_harness=os.environ.get("VERDICLIP_HOSTED_SCROLLBAR")
            == "1",
        )
        self.hosted_input: HostedFixtureClick | None = None
        if os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") == "1":
            self.hosted_input = HostedFixtureClick(self.peer, self.journal)
        self.retirement_probe: dict[str, object] = {}
        self.popup_trace: PopupFailureTrace | None = None
        self.image = QImage()
        self.errors: list[str] = []
        self.app.controller.capture.failed.connect(self.errors.append)
        self.workflow = self.app.controller.configure_scrolling(
            self.adapter, self.backend, directory / "spool"
        )

    def backend(self, reference: UiaReference, adapter: UiaAdapter) -> UiaFrameBackend:
        """Use production provider and pixels; fail if popup ownership changes."""
        assert reference.window_handle == self.peer.handle, "UIA target is not owned"
        return UiaFrameBackend(
            reference,
            adapter,
            options=NativeBackendOptions(
                preservation_qualified=True, input_unchanged=self.safe
            ),
        )

    def safe(self) -> bool:
        """Native movement never outlives its lease or unchanged fixture owners."""
        return (
            self.app.controller.capture.input_active
            and self.snapshot is not None
            and not self.snapshot.errors()
        )

    def prepare(self) -> None:
        """Expose owned content and popup before asking the existing driver to bind."""
        # Retirement itself needs only an owned Qt popup, not WPF or UIA startup.
        self.menu.addAction("Inert owned item")
        self.menu.popup(QPoint(64, 64))
        self.qtbot.waitUntil(self.menu.isVisible)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self.snapshot = StrictFixtureSnapshot(app, (self.menu,))
        self.popup_trace = PopupFailureTrace(self.menu, lambda: "retirement_probe")
        self.journal.owned_handles = (int(self.menu.winId()),)
        self.check_passive_retirement()
        self.snapshot.close()
        self.menu.removeEventFilter(self.popup_trace)
        self.menu.close()
        self.snapshot = None
        self.popup_trace = None
        self.peer.start()
        self.qtbot.waitUntil(self.peer.poll, timeout=10000)
        rectangle = self.peer.state["physical_rectangle"]
        assert isinstance(rectangle, dict)
        width, height = int(rectangle["width"]), self.peer.integer("content_rows")
        assert int(rectangle["height"]) == self.peer.state["viewport_height"], (
            "100% scale required"
        )
        assert 0 < width <= 384
        assert height == 1120
        self.image = fixture_image(width, height)
        self.menu.popup(QPoint(int(rectangle["x"]) + width + 80, int(rectangle["y"])))
        self.qtbot.waitUntil(self.menu.isVisible)
        assert self.menu.devicePixelRatioF() == 1, "100% scale required"
        menu_rect = self.menu.frameGeometry()
        viewport = Rect(**rectangle)
        assert not viewport.intersects(
            Rect(menu_rect.x(), menu_rect.y(), menu_rect.width(), menu_rect.height())
        )
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self.snapshot = StrictFixtureSnapshot(app, (self.menu,))
        self.popup_trace = PopupFailureTrace(
            self.menu, lambda: str(self.workflow.state)
        )
        self.journal.owned_handles = (self.peer.handle, int(self.menu.winId()))
        self.journal.binding_handle = self.peer.handle
        self.journal.prepare_binding(self.target())

    def check_passive_retirement(self) -> None:
        """Isolate ordinary owned tooltip retirement before hooks, input or UIA."""
        assert self.snapshot is not None
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        before = int(api.GetForegroundWindow() or 0)
        self.retirement_probe = {"stage": "before_show", "foreground_before": before}
        assert not fixture_foreground_errors(before, self.journal.owned_handles)
        panel = QWidget(None, PASSIVE_WINDOW_FLAGS)
        panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        panel.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        panel.setGeometry(self.menu.x() + self.menu.width() + 20, self.menu.y(), 40, 40)
        try:
            panel.show()
            self.qtbot.waitUntil(lambda: panel.windowHandle().isExposed(), timeout=2000)
            shown = int(api.GetForegroundWindow() or 0)
            self.retirement_probe.update(stage="shown", foreground_shown=shown)
            assert shown == before, "Passive show changed native foreground"
            assert not self.snapshot.errors(), "Passive show changed the popup"
            self.retirement_probe["stage"] = "closing"
            panel.close()
            self.qtbot.waitUntil(lambda: not isValid(panel), timeout=2000)
            QApplication.processEvents()
            errors = self.snapshot.errors()
            after = int(api.GetForegroundWindow() or 0)
            self.retirement_probe.update(
                stage="retired",
                retired=not isValid(panel),
                errors=errors,
                foreground_after=after,
            )
            assert after == before, "Passive retirement changed native foreground"
            assert not errors, "Passive retirement changed the popup"
        finally:
            if isValid(panel):
                panel.close()
                self.qtbot.waitUntil(lambda: not isValid(panel), timeout=2000)

    def target(self) -> Rect:
        """Restrict the actual click to the owned native scrollbar center."""
        self.peer.poll()
        return self.peer.target()

    def run(self) -> None:
        """No replay events or direct Start/Use calls may satisfy this case."""
        target = self.target()
        if self.hosted_input is not None:
            self.hosted_input.bind(target)
        self.qtbot.waitUntil(lambda: self.journal.binding_ready(target), timeout=20000)
        assert self.snapshot is not None
        assert not self.snapshot.errors()
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        assert not fixture_foreground_errors(
            int(api.GetForegroundWindow() or 0), self.journal.owned_handles
        )
        self.app.controller.capture_scrolling()
        self.qtbot.waitUntil(
            lambda: self.app.controller.capture.input_active or bool(self.errors),
            timeout=5000,
        )
        assert not self.errors
        self.journal.request("scrollbar-click", target)
        if self.hosted_input is not None:
            assert self.safe()
            assert len(self.driver.workers) == 1
            worker = self.driver.workers[0]
            self.hosted_input.click(
                target,
                expires_at=self.input_deadline,
                lease_valid=lambda: (
                    self.safe() and self.driver.workers == [worker] and worker.active
                ),
            )
        self.qtbot.waitUntil(
            lambda: bool(self.app.controller.workspace.documents) or bool(self.errors),
            timeout=30000,
        )
        assert not self.errors
        self.qtbot.waitUntil(self.retired, timeout=5000)
        records = self.app.controller.workspace.documents
        assert len(records) == 1
        record = records[0]
        assert record.provenance is not None
        assert record.provenance.source_kind is CaptureKind.SCROLLING
        assert record.provenance.completeness is CaptureCompleteness.COMPLETE
        assert self.discoveries, "No native UIA discovery received"
        discovery = self.discoveries[0]
        assert (
            SemanticTargets(discovery.nodes).scrollbar_at(
                discovery.hit_identity, target.center, discovery.window_identity
            )
            is not None
        ), "Native hit is not a verified scrollbar or its child"
        assert self.discoveries[0].references, "Provider has no verified ScrollPattern"
        verify_pixels(record.session.document.image, self.image)
        assert not native_action_errors(self.driver, "scrollbar-click")
        assert not self.snapshot.errors()
        assert cleanup_confirmed(
            self.driver, selecting=self.app.controller.capture.is_selecting
        )
        assert self.peer.poll()
        assert self.peer.state["end_seen"] is True
        assert self.peer.state["offset"] == 0
        self.passed = True

    def close(self) -> None:
        """Retire input before touching only declared owned test windows."""
        failures: list[str] = []
        popup_loss: dict[str, object] = {}
        transitions: list[dict[str, str]] = []
        try:
            popup_loss = dict(self.popup_trace.first) if self.popup_trace else {}
            transitions = list(self.snapshot.transitions) if self.snapshot else []
        except RuntimeError as error:
            failures.append(f"Popup observation unavailable: {error}")
        try:
            if self.popup_trace is not None:
                self.menu.removeEventFilter(self.popup_trace)
        except RuntimeError as error:
            failures.append(f"Popup observer detach failed: {error}")
        visible_owned = []
        try:
            visible_owned = [
                {"handle": int(widget.effectiveWinId()), "type": type(widget).__name__}
                for widget in QApplication.topLevelWidgets()
                if widget.isVisible()
            ]
        except RuntimeError as error:
            failures.append(str(error))
        for operation in (
            self.journal.retire,
            self.app.controller.capture.cancel,
            self.adapter.cancel,
        ):
            try:
                operation()
            except (OSError, RuntimeError, AssertionError) as error:
                failures.append(str(error))
        try:
            self.qtbot.waitUntil(
                self.retired,
                timeout=10000,
            )
        except AssertionError as error:
            failures.append(str(error))
        for operation in (
            self.snapshot.close if self.snapshot is not None else lambda: None,
            self.close_owned_windows,
            self.source.close,
        ):
            try:
                operation()
            except (OSError, RuntimeError, AssertionError) as error:
                failures.append(str(error))
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "scrollbar-native.json").write_text(
            json.dumps(
                {
                    "passed": self.passed and not failures and self.retired(),
                    "errors": self.errors,
                    "cleanup_errors": failures,
                    "visible_test_windows_before_cleanup": visible_owned,
                    "first_popup_loss": popup_loss,
                    "passive_retirement_probe": self.retirement_probe,
                    "popup_transitions_before_cleanup": transitions,
                    "declared_owned_handles": self.journal.owned_handles,
                    "retired": self.retired(),
                    "input": dict(
                        self.driver.receipt(),
                        input_generated_by_harness=self.hosted_input is not None,
                    ),
                    "entry": "AppController.capture_scrolling; no global hotkey claim",
                    "semantic_hits": [
                        [node.kind.value for node in item.nodes]
                        for item in self.discoveries
                    ],
                    "scope": "native scrollbar interaction; no physical RawInput claim",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert not failures, failures
        assert self.retired(), "Native resources did not retire"

    def retired(self) -> bool:
        """Never hide a fixture while capture or provider resources remain active."""
        return cleanup_confirmed(
            self.driver, selecting=self.app.controller.capture.is_selecting
        ) and (
            not hasattr(self, "adapter")
            or (not self.adapter.busy and self.process.worker_pid is None)
        )

    def close_owned_windows(self) -> None:
        """Also cover partial construction; unsafe retirement remains a failure."""
        if self.retired():
            try:
                self.menu.close()
            finally:
                self.peer.close()


def verify_pixels(actual: QImage, expected: QImage) -> None:
    """Reject cropping, repeated rows, or any changed channel, including alpha."""
    assert actual.size() == expected.size(), "Captured full-panel dimensions differ"
    assert actual.convertToFormat(
        QImage.Format.Format_RGBA8888
    ) == expected.convertToFormat(QImage.Format.Format_RGBA8888), (
        "Captured full-panel pixels differ"
    )


def fixture_image(width: int, height: int) -> QImage:
    """Match the existing WPF synthetic bitmap, including first and last rows."""
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            pixels[offset : offset + 4] = bytes(
                (
                    (y * 43 + x * 19 + y // 127) % 255,
                    (y * 31 + x * 7 + y // 251) % 253,
                    (y * 17 + x * 11) % 251,
                    255,
                )
            )
    return QImage(pixels, width, height, QImage.Format.Format_RGBA8888).copy()
