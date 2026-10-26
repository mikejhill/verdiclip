"""Portable proof that the native-presentation harness cannot require activation."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot
from tests.capture import test_capture_native as native_module
from tests.capture.desktop import DesktopSession
from tests.capture.harness_transport import ReplayWorkerTransport
from tests.capture.preservation_harness import (
    CaptureHarness,
    HarnessBudgets,
    PreservationSnapshot,
)
from tests.capture.test_capture_native import NativeRun
from tests.capture.test_popup_capture import CaptureContext, ExistingScene
from tests.ux.test_app_journeys import App, make_app

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.geometry import Rect
from verdiclip.settings import CaptureSettings, Settings


class SemanticDriver:
    """Only explicit replay broker events enter the real capture service."""

    def __init__(self, context: CaptureContext) -> None:
        self.context = context
        self.sent: list[str] = []

    def send(self, outcome: str, area: Rect) -> None:
        """No QWidget/native input or foreground changes are synthesized."""
        self.sent.append(outcome)
        if outcome == "escape":
            self.context.broker.feed(
                SelectionInput(InputKind.CANCEL, area.top_left, "escape")
            )
        else:
            for kind, point in (
                (InputKind.MOVE, area.top_left),
                (InputKind.PRESS, area.top_left),
                (InputKind.MOVE, area.bottom_right),
                (InputKind.RELEASE, area.bottom_right),
            ):
                self.context.broker.feed(SelectionInput(kind, point))


@pytest.mark.parametrize(
    "kind", ["main", "about", "settings", "modal", "popup", "submenu", "combo"]
)
@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_shared_driver_waits_for_broker_not_focus_and_preserves_every_owner(
    qtbot: QtBot,
    qapp: QApplication,
    kind: str,
    outcome: str,
    request: pytest.FixtureRequest,
) -> None:
    """The native harness's actual driver works with existing popup/modal grabs."""
    scene, context = ExistingScene(kind, qapp), CaptureContext(qapp)
    driver = SemanticDriver(context)
    snapshot = PreservationSnapshot(qapp, tuple(scene.windows))
    harness = CaptureHarness(context.service, snapshot, driver.send, outcome=outcome)
    context.broker.defer_ready = True
    try:
        snapshot.phase = "before_service_request"
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: context.broker.starts == 1)
        snapshot.phase = "broker_reserved_without_overlays"
        harness.advance()
        assert not driver.sent
        assert not harness.done
        snapshot.phase = "dispatching_broker_ready"
        context.broker.ready.emit()
        snapshot.phase = "broker_ready_overlays_present"
        qtbot.waitUntil(lambda: harness.done)
        request.node.user_properties.append(
            ("preservation_trace", json.dumps(snapshot.transitions))
        )
        assert not harness.errors, (
            harness.errors,
            snapshot.transitions,
            context.desktop.frames,
        )
        assert driver.sent == [outcome]
        scene.assert_preserved()
    finally:
        harness.close()
        scene.close()
        context.service.deleteLater()


@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_harness_completion_waits_for_the_real_cleanup_receipt(
    qtbot: QtBot,
    qapp: QApplication,
    outcome: str,
) -> None:
    """Disappearing overlays are insufficient evidence of released input."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    context.broker.defer_cleanup = True
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        SemanticDriver(context).send,
        outcome=outcome,
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: harness.issued)
        assert not context.service.overlays
        assert not harness.done
        assert context.service.is_selecting
        scene.assert_preserved()
        context.broker.acknowledge_cleanup()
        qtbot.waitUntil(lambda: harness.done)
        assert not harness.errors
    finally:
        context.broker.defer_cleanup = False
        harness.close()
        scene.close()
        context.service.deleteLater()


def test_expiry_never_sends_late_input_or_closes_unrelated_dialogs(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    """A missing readiness receipt fails without driving or rejecting any host."""
    scene, context = ExistingScene("modal", qapp), CaptureContext(qapp)
    context.broker.defer_ready = True
    driver = SemanticDriver(context)
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        driver.send,
        outcome="escape",
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: context.broker.starts == 1)
        harness.expire()
        context.broker.ready.emit()
        qtbot.waitUntil(lambda: harness.done)
        assert harness.errors == ["Capture readiness/completion deadline expired"]
        assert not driver.sent
        scene.assert_preserved()
    finally:
        harness.close()
        scene.close()
        context.service.deleteLater()


def test_negative_cleanup_cannot_complete_a_native_test_successfully(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    """The harness keeps waiting and retains the service failure as evidence."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    context.broker.defer_ready = context.broker.defer_cleanup = True
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        SemanticDriver(context).send,
        outcome="escape",
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: context.broker.starts == 1)
        context.broker.interrupt("Fixture cleanup failed", cleaned=False)
        harness.advance()
        assert not harness.done
        assert harness.errors
        context.broker.acknowledge_cleanup()
        qtbot.waitUntil(lambda: harness.done)
        assert any("cleanup" in error for error in harness.errors)
        scene.assert_preserved()
    finally:
        context.broker.defer_cleanup = False
        harness.close()
        scene.close()
        context.service.deleteLater()


def test_activating_overlay_is_a_harness_failure_not_a_fallback(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression back to focus-taking UI cannot make this harness pass."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    driver = SemanticDriver(context)
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        driver.send,
        outcome="escape",
    )
    monkeypatch.setattr(
        SelectionOverlay, "windowFlags", lambda _self: Qt.WindowType.Window
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: harness.done)
        assert any("nonactivating" in error for error in harness.errors)
        assert not driver.sent
        scene.assert_preserved()
    finally:
        harness.close()
        scene.close()
        context.service.deleteLater()


@pytest.mark.parametrize("outcome", ["escape", "drag"])
def test_native_run_uses_replay_worker_and_leaves_foreign_dialog_untouched(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    outcome: str,
) -> None:
    """The same native-run coordinator is portable without native calls or focus."""
    transport = ReplayWorkerTransport()
    monkeypatch.setattr("verdiclip.capture.service.make_input_broker", transport.broker)
    settings = replace(
        Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
    )
    factory = request.getfixturevalue(make_app.__name__)
    assert callable(factory)
    app = factory(settings=settings)
    assert isinstance(app, App)
    unrelated = QDialog()
    qtbot.addWidget(unrelated)
    unrelated.setWindowModality(Qt.WindowModality.ApplicationModal)
    unrelated.show()
    run = NativeRun(app, qapp, transport, outcome=outcome)
    menu = app.controller.tray.menu
    menu.popup(QPoint(60, 60))
    try:
        run.schedule(lambda: app.press_hotkey("region"))
        with pytest.raises(ValueError, match="only once"):
            run.schedule(lambda: app.press_hotkey("region"))
        qtbot.waitUntil(lambda: run.done, timeout=6000)
        assert not run.errors
        assert not transport.cleanup_errors()
        run._exposed()
        run.close()
        assert unrelated.isVisible()
    finally:
        run.close()
        qtbot.waitUntil(run.input_idle)


def test_transport_refuses_unready_and_unknown_input(qapp: QApplication) -> None:
    """No OS call or synthetic input is attempted without an active fixture lease."""
    transport = ReplayWorkerTransport()
    with pytest.raises(ValueError, match="not ready"):
        transport.send("drag", Rect(0, 0, 70, 50))
    assert transport.cleanup_errors() == ["No broker worker was started"]
    assert isinstance(qapp, QApplication)


def test_missing_cleanup_has_a_bounded_failed_completion_without_closing_hosts(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    """Nested fixture loops can unwind on failure without claiming clean input."""
    scene, context = ExistingScene("modal", qapp), CaptureContext(qapp)
    context.broker.defer_ready = context.broker.defer_cleanup = True
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        SemanticDriver(context).send,
        outcome="escape",
        budgets=HarnessBudgets(cleanup_ms=10),
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: context.broker.starts == 1)
        harness.expire()
        qtbot.waitUntil(lambda: harness.done)
        assert not harness.cleanup_confirmed
        assert context.service.is_selecting
        assert any("cleanup was not confirmed" in error for error in harness.errors)
        scene.assert_preserved()
        context.broker.acknowledge_cleanup()
    finally:
        context.broker.defer_cleanup = False
        harness.close()
        scene.close()
        context.service.deleteLater()


def test_closed_harness_ignores_queued_deadlines_and_readiness(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    """Retiring observers never resurrects a run or reports a false success."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    driver = SemanticDriver(context)
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        driver.send,
        outcome="escape",
    )
    completed: list[bool] = []
    harness.completed.connect(lambda: completed.append(True))
    harness.start()
    harness.close()
    harness.advance()
    harness.expire()
    harness.cleanup_expired()
    qtbot.waitUntil(lambda: not context.service.is_selecting)
    assert not harness.done
    assert not completed
    assert not driver.sent
    scene.assert_preserved()
    scene.close()
    context.service.deleteLater()


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
def test_native_scene_coordinator_has_portable_nested_loop_regressions(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    outcome: str,
) -> None:
    """Native fixture setup and cleanup are tested without native message/input APIs."""
    qapp = request.getfixturevalue("qapp")
    assert isinstance(qapp, QApplication)
    transport = ReplayWorkerTransport()
    monkeypatch.setattr("verdiclip.capture.service.make_input_broker", transport.broker)
    monkeypatch.setattr(
        NativeRun, "post_hotkey", lambda self: self.app.press_hotkey("region")
    )
    factory = request.getfixturevalue(make_app.__name__)
    assert callable(factory)
    app = factory(
        settings=replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )
    assert isinstance(app, App)
    run = NativeRun(app, qapp, transport, outcome=outcome)
    try:
        run.enter_scene(scenario)
        qtbot.waitUntil(lambda: run.done, timeout=8000)
        assert not run.errors
        assert run.harness is not None
        assert run.harness.cleanup_confirmed
        assert not transport.cleanup_errors()
    finally:
        run.close()
        qtbot.waitUntil(run.input_idle)


def test_destroyed_observed_host_is_a_bounded_failure_not_a_qt_exception(
    qtbot: QtBot,
    qapp: QApplication,
) -> None:
    """A deleted native wrapper cannot break both cleanup and its watchdog."""
    host = QDialog()
    host.show()
    context = CaptureContext(qapp)
    context.broker.defer_ready = True
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, (host,)),
        SemanticDriver(context).send,
        outcome="escape",
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: context.broker.starts == 1)
        shiboken6.delete(host)
        context.broker.ready.emit()
        qtbot.waitUntil(lambda: harness.done)
        assert any("destroyed" in error for error in harness.errors)
        assert harness.cleanup_confirmed
    finally:
        harness.close()
        if shiboken6.isValid(host):
            host.close()
        context.service.deleteLater()


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("unavailable", ["platform", "locked"])
def test_native_requirement_never_silently_skips_requested_qualification(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    *,
    required: bool,
    unavailable: str,
) -> None:
    """Simulated prerequisites test only the guard; no desktop query/input runs."""
    monkeypatch.setattr(native_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(request.config.option, "require_interactive_capture", required)
    monkeypatch.setattr(
        QGuiApplication,
        "platformName",
        staticmethod(lambda: "offscreen" if unavailable == "platform" else "windows"),
    )
    monkeypatch.setattr(
        DesktopSession, "current", classmethod(lambda _cls: DesktopSession(0, 0))
    )
    exception = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(exception):
        NativeRun.require_desktop(request)


@pytest.mark.parametrize("kind", ["popup", "submenu", "combo"])
def test_popup_fixture_remains_stable_without_any_capture_request(
    qtbot: QtBot,
    qapp: QApplication,
    kind: str,
) -> None:
    """A capture-free event-loop control distinguishes setup from capture effects."""
    scene = ExistingScene(kind, qapp)
    snapshot = PreservationSnapshot(qapp, tuple(scene.windows))
    snapshot.phase = "control_without_capture"
    turns: list[bool] = []
    try:
        QTimer.singleShot(0, lambda: turns.append(True))
        qtbot.waitUntil(lambda: bool(turns))
        errors = snapshot.errors()
        assert not errors, (errors, snapshot.transitions, {"capture_requests": 0})
        scene.assert_preserved()
    finally:
        snapshot.close()
        scene.close()
