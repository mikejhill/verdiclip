"""Portable lifecycle-worker cancellation and notification boundary tests."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts.owned_pipe_smoke import LatencyMetrics, Message, ProtocolError, Session
from scripts.owned_worker_smoke import (
    CompletionGate,
    Diagnostics,
    LaunchControl,
    WorkerSession,
    case_observed,
    terminal_receipt,
)


def test_cancel_before_launch_never_calls_native() -> None:
    """Cancel before launch never calls native."""
    control, start = LaunchControl(), Mock()
    control.cancel()
    with pytest.raises(ProtocolError, match="cancelled"):
        control.launch(start, "cancel-before")
    start.assert_not_called()
    assert control.returned
    assert not control.pending
    assert case_observed("cancel-before", control, 0)


def test_cancel_pre_create_stall_skips_native() -> None:
    """Cancel pre create stall skips native."""
    control, start = LaunchControl(), Mock()

    def delay(_: float) -> None:
        control.witness("heartbeat")
        control.witness("paint")
        control.cancel()

    with pytest.raises(ProtocolError, match="cancelled"):
        control.launch(start, "cancel-pre-create", delay)
    start.assert_not_called()
    assert case_observed("cancel-pre-create", control, 4)


def test_cancel_post_create_stays_pending_until_return() -> None:
    """Cancel post create stays pending until return."""
    control, start = LaunchControl(), Mock()

    def delay(_: float) -> None:
        assert control.pending
        assert not control.returned
        start.assert_called_once()
        control.witness("heartbeat")
        control.witness("paint")
        control.cancel()
        assert not case_observed("cancel-post-create", control, 10)

    with pytest.raises(ProtocolError, match="cancelled"):
        control.launch(start, "cancel-post-create", delay)
    assert case_observed("cancel-post-create", control, 10)


def test_cancel_inside_native_launch_is_latched() -> None:
    """Cancel inside native launch is latched."""
    control = LaunchControl()

    def start() -> None:
        control.cancel()
        assert control.pending
        assert not control.returned

    with pytest.raises(ProtocolError, match="cancelled"):
        control.launch(start, "normal")
    assert control.returned


def test_native_exception_does_not_admit_early_terminal() -> None:
    """Native exception does not admit early terminal."""
    control = LaunchControl()
    with pytest.raises(OSError, match="native failure"):
        control.launch(Mock(side_effect=OSError("native failure")), "normal")
    assert control.returned
    assert not control.pending


def test_queued_ready_after_cancel_does_not_reach_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued ready after cancel does not reach base."""
    session = object.__new__(WorkerSession)
    session.control = LaunchControl()
    base = Mock()
    monkeypatch.setattr(Session, "_message", base)
    session.control.cancel()
    with pytest.raises(ProtocolError, match="cancelled"):
        session._message(Message("ready", 0, {}))
    base.assert_not_called()


def test_reentrant_cancel_before_send_blocks_write() -> None:
    """Reentrant cancel before send blocks write."""
    session = object.__new__(WorkerSession)
    session.control = LaunchControl()
    session.socket = Mock()
    session.control.cancel()
    with pytest.raises(ProtocolError, match="cancelled"):
        session._send({"operation": "ping"})
    session.socket.write.assert_not_called()


def test_pending_launch_cannot_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending launch cannot finish."""
    session = object.__new__(WorkerSession)
    session.control = LaunchControl()
    session.control.pending = True
    base = Mock()
    monkeypatch.setattr(Session, "_finish", base)
    with pytest.raises(RuntimeError, match="pending"):
        session._finish()
    base.assert_not_called()


@pytest.mark.parametrize("first", ["receipt", "thread"])
def test_finish_requires_both_notifications_and_is_reentrant_safe(first: str) -> None:
    """Finish requires both notifications and is reentrant safe."""
    gate, control, events = CompletionGate(7), LaunchControl(), []

    def notify() -> None:
        events.append("done")
        control.cancel()
        gate.complete(notify)
        assert not gate.accept(7, "duplicate")

    if first == "receipt":
        assert gate.accept(7, "immutable-json")
    else:
        gate.thread_finished = True
    gate.complete(notify)
    assert not events
    gate.thread_finished = True
    if gate.receipt is None:
        gate.accept(7, "immutable-json")
    gate.complete(notify)
    assert events == ["done"]


def test_stale_terminal_cannot_retire_current_worker() -> None:
    """Stale terminal cannot retire current worker."""
    gate, notify = CompletionGate(2), Mock()
    gate.thread_finished = True
    assert not gate.accept(1, "stale")
    gate.complete(notify)
    notify.assert_not_called()
    assert gate.accept(2, "current")
    gate.complete(notify)
    notify.assert_called_once()


def test_cleanup_unconfirmed_never_passes_terminal_receipt() -> None:
    """An expected cancellation cannot conceal unresolved native cleanup."""
    session = Mock()
    session.result.return_value = {"passed": True}
    session.failure = "cancelled"
    session.helper.status.cleanup_confirmed = False
    session.helper.status.cleanup_errors = ()
    session.helper.status.failure = None
    session.helper.status.producer_verified = False
    session.cleanup_failure = None
    session.flight.pending = None
    control = LaunchControl()
    control.returned = True
    assert not terminal_receipt(session, control, "cancel-post-create")["passed"]
    session.helper.status.cleanup_confirmed = True
    control.pending = True
    assert not terminal_receipt(session, control, "cancel-post-create")["passed"]


def test_timeout_during_launch_cannot_finish_until_native_returns() -> None:
    """An elapsed clock cannot establish containment or retirement prematurely."""
    control = LaunchControl()
    session = object.__new__(WorkerSession)
    session.control = control

    def delayed_native() -> None:
        with pytest.raises(RuntimeError, match="pending"):
            session._finish()

    control.launch(delayed_native, "timeout-post-create", lambda _: None)
    assert control.returned


def test_gui_witnesses_outside_stall_cannot_pass() -> None:
    """Paints before and after the stall do not prove painting inside it."""
    control = LaunchControl()
    control.witness("heartbeat")
    control.witness("paint")
    control.launch(Mock(), "timeout-post-create", lambda _: None)
    control.witness("heartbeat")
    control.witness("paint")
    assert control.witnesses() == {"heartbeat": 0, "paint": 0}
    assert not case_observed("timeout-post-create", control, 100)


def test_delayed_cancel_observation_preserves_gui_request_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel-to-retirement includes time the lifecycle worker was blocked."""
    session = object.__new__(WorkerSession)
    session.control = LaunchControl()
    session.control.marks["cancel_requested"] = 10
    session.latency = LatencyMetrics()
    monkeypatch.setattr(Session, "_fail", Mock())
    session._fail("cancelled")
    session.latency.marks["termination_requested"] = 510
    session.latency.marks["cleanup_confirmed"] = 600
    report = session.latency.report()
    assert report["cancel_to_termination_request_ns"] == 500
    assert report["cancel_to_cleanup_confirmed_ns"] == 590


def test_real_qt_completion_exits_despite_active_close_veto(tmp_path: Path) -> None:
    """Real queued completion retires the GUI despite its live closeEvent veto."""
    source = r"""
import json, sys, threading
from pathlib import Path
from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication, QWidget
from scripts.owned_worker_smoke import (
    CompletionGate, LaunchControl, exit_after_retirement,
)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
gate, control, events = CompletionGate(1), LaunchControl(), []
release = threading.Event()
class Window(QWidget):
    def closeEvent(self, event):
        control.cancel()
        events.append("close-vetoed")
        event.ignore()
class Thread(QThread):
    def run(self):
        release.wait(1)
class Receiver(QObject):
    @Slot()
    def terminal(self):
        events.append("terminal")
        gate.accept(1, "receipt")
        exit_after_retirement(gate)
    @Slot()
    def finished(self):
        events.append("thread-finished")
        gate.thread_finished = True
        exit_after_retirement(gate)
window, thread, receiver = Window(), Thread(), Receiver()
window.show()
thread.finished.connect(receiver.finished)
QTimer.singleShot(5, window.close)
QTimer.singleShot(15, receiver.terminal)
def release_worker():
    events.append("still-running")
    release.set()
QTimer.singleShot(30, release_worker)
thread.start()
app.exec()
assert gate.delivered and gate.thread_finished
assert control.cancelled.is_set()
expected = ["close-vetoed", "terminal", "still-running", "thread-finished"]
assert events == expected, events
Path(sys.argv[1]).write_text(json.dumps({"passed": True, "events": events}))
window.hide()
"""
    receipt = tmp_path / "qt-retired.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(  # noqa: S603 - fixed test source in owned subprocess
        [sys.executable, "-c", source, str(receipt)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text())["passed"] is True


def test_diagnostics_bound_phases_and_exceptions(tmp_path: Path) -> None:
    """Failure evidence survives separately without an unbounded log stream."""
    diagnostics = Diagnostics(tmp_path)
    for _ in range(100):
        diagnostics.record("phase" * 100, "exception" * 1000)
    value = json.loads((tmp_path / "diagnostic.json").read_text())
    assert len(value["phases"]) == 64
    assert len(value["errors"]) == 8
    assert all(len(item) <= 128 for item in value["phases"])
    assert all(len(item) <= 4096 for item in value["errors"])
    assert not (tmp_path / "diagnostic.tmp").exists()
