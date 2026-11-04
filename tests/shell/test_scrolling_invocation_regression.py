"""Real tray action/service/broker/worker interaction without OS input calls."""

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputPlatform
from tests.capture.test_input_worker import ReplayRun
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.geometry import Point, Rect
from verdiclip.preview_scrolling import compose_preview_scrolling
from verdiclip.verification import PreviewController, PreviewProfile


@pytest.mark.parametrize("recipient_allowed", [False, True])
def test_tray_scrolling_invocation_retires_worker_and_leaves_ui_usable(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    recipient_allowed: bool,
) -> None:
    """Invoke actual UI wiring; preserve admission refusal and prove retry/clicks."""
    caplog.set_level("INFO", logger="verdiclip.capture.input_worker")
    platforms: list[ReplayInputPlatform] = []
    workers: list[CaptureInputWorker] = []

    def worker_factory(_point: Point, _bounds: Rect) -> CaptureInputWorker:
        platform = ReplayInputPlatform()
        platform.recipient_supported = recipient_allowed
        worker = ReplayRun.make(platform)
        platforms.append(platform)
        workers.append(worker)
        return worker

    def broker_factory(owner: QObject) -> NativeInputBroker:
        return NativeInputBroker(owner, factory=worker_factory, require_windows=False)

    monkeypatch.setattr("verdiclip.capture.service.make_input_broker", broker_factory)
    controller = PreviewController(
        PreviewProfile(tmp_path, "a" * 40), FakeDesktop(), FakeWindows([])
    )
    adapter = UiaAdapter()
    compose_preview_scrolling(
        controller,
        qualified=True,
        bundle_root=tmp_path,
        adapter_factory=lambda: adapter,
    )
    failures: list[str] = []
    controller.capture.failed.connect(failures.append)
    button = QPushButton("Unrelated test control")
    qtbot.addWidget(button)
    clicks: list[bool] = []
    button.clicked.connect(lambda: clicks.append(True))
    button.show()
    try:
        for attempt in range(2):
            # Actual product QAction wiring, deferred QTimer, service and worker.
            controller.tray._actions["scrolling"].trigger()
            qtbot.waitUntil(lambda expected=attempt + 1: len(workers) == expected)
            if recipient_allowed:
                qtbot.waitUntil(lambda: bool(controller.capture.overlays))
                assert controller.capture.input_active
                platforms[-1].key(0x100, 0x1B)
                platforms[-1].key(0x101, 0x1B)
            qtbot.waitUntil(lambda: not controller.capture.is_selecting)
            assert workers[-1].stopped.is_set()
            assert workers[-1].cleaned
            assert platforms[-1].closed
            assert not controller.capture.overlays
            assert not controller.capture._broker.busy
            if not recipient_allowed:
                assert_retained_error(controller, failures[-1])
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
            assert len(clicks) == attempt + 1
        assert_failure_outcome(
            failures, platforms, caplog.messages, allowed=recipient_allowed
        )
        assert adapter._process.worker_pid is None
        assert not controller.workspace.documents
    finally:
        controller.capture.cancel()
        for worker in workers:
            worker.release()
            assert worker.stopped.wait(2)
        controller.shutdown()
        controller.workspace.parking.deleteLater()


def assert_retained_error(controller: PreviewController, failure: str) -> None:
    """The same menu retains a readable noninteractive status after rebuilding."""
    status = controller.tray.action("capture_error")
    assert status.isVisible()
    assert not status.isEnabled()
    assert failure in status.text()
    menu = controller.tray.menu
    controller.tray.rebuild_menu({})
    assert controller.tray.menu is menu
    assert failure in controller.tray.action("capture_error").text()


def assert_failure_outcome(
    failures: list[str],
    platforms: list[ReplayInputPlatform],
    messages: list[str],
    *,
    allowed: bool,
) -> None:
    """Denial never installs hooks; successful Escape is cancellation, not error."""
    assert_worker_log(messages, allowed=allowed)
    if allowed:
        assert not failures
    else:
        assert len(failures) == 2
        assert all("safely own input" in value for value in failures)
        assert all(not platform.installed for platform in platforms)


def assert_worker_log(messages: list[str], *, allowed: bool) -> None:
    """Logs distinguish admission failure from acquired input and verified cleanup."""
    assert messages.count("Capture input worker retired; cleanup_confirmed=True") == 2
    assert ("Capture input ready" in messages) is allowed
