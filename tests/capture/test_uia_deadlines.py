"""Deterministic process/Qt-delivery schedules without a helper or desktop."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, override

import pytest
from PySide6.QtCore import QByteArray, QObject, QProcess, QSignalBlocker
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pytestqt.qtbot import QtBot

from verdiclip.capture.scroll import uia_worker
from verdiclip.capture.scroll.uia_pipe import UiaPipe
from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess
from verdiclip.exceptions import CaptureError


class ControlledProcess(QProcess):
    """Own no OS child; expose bytes independently of readyRead delivery."""

    created: ClassVar[list[ControlledProcess]] = []

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.current_state = QProcess.ProcessState.NotRunning
        self.output = bytearray()
        self.errors = bytearray()
        self.sent: list[bytes] = []
        self.finish_on_kill = True
        self.kill_count = 0
        self.created.append(self)

    @override
    def start(self, *_args: object, **_kwargs: object) -> None:
        """Simulate OS launch only when the real queued dispatch runs."""
        self.current_state = QProcess.ProcessState.Running
        self.started.emit()

    @override
    def state(self) -> QProcess.ProcessState:
        return self.current_state

    @override
    def processId(self) -> int:
        return 123 if self.current_state is QProcess.ProcessState.Running else 0

    @override
    def write(self, *args: object, **_kwargs: object) -> int:
        data = args[0]
        assert isinstance(data, bytes)
        self.sent.append(data)
        return len(data)

    @override
    def closeWriteChannel(self) -> None:
        """There is no native stream to close."""

    @override
    def readAllStandardOutput(self) -> QByteArray:
        data = QByteArray(bytes(self.output))
        self.output.clear()
        return data

    @override
    def readAllStandardError(self) -> QByteArray:
        data = QByteArray(bytes(self.errors))
        self.errors.clear()
        return data

    @override
    def kill(self) -> None:
        self.kill_count += 1
        if self.finish_on_kill:
            self.finish()

    def finish(self) -> None:
        """Deliver the independent terminal receipt."""
        self.current_state = QProcess.ProcessState.NotRunning
        self.finished.emit(0, QProcess.ExitStatus.NormalExit)

    def feed(self, data: bytes, *, notify: bool = True) -> None:
        """Buffering and event delivery are separate scheduling boundaries."""
        self.output.extend(data)
        if notify:
            self.readyReadStandardOutput.emit()


@dataclass
class Clock:
    """Advance policy time without sleeping or changing the machine clock."""

    seconds: float = 0

    def now(self) -> float:
        """Return the controlled policy clock."""
        return self.seconds


@dataclass
class Rig:
    """Exercise the production lease through a real facade and Qt signal graph."""

    client: UiaProcess
    clock: Clock
    process: ControlledProcess
    failures: list[str]
    replies: list[object]

    def ready(self) -> None:
        """Deliver only the protocol readiness receipt."""
        self.process.feed(b'{"ready":true,"protocol":1}\n')

    def response(self, *, notify: bool = True) -> None:
        """Correlate one response to the actually transmitted operation."""
        request = json.loads(self.process.sent[-1])
        self.process.feed(
            json.dumps({"request_id": request["request_id"], "ok": True}).encode()
            + b"\n",
            notify=notify,
        )


@pytest.fixture
def rig(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    """The fixed fixture command never reaches QProcess or Windows execution."""
    ControlledProcess.created.clear()
    clock = Clock()
    monkeypatch.setattr(uia_worker, "QProcess", ControlledProcess)
    monkeypatch.setattr(uia_worker, "monotonic", clock.now, raising=False)
    client = UiaProcess(helper_command=("fixture-only",), qualified=True)
    failures: list[str] = []
    replies: list[object] = []
    client.failed.connect(lambda _request, message: failures.append(message))
    client.completed.connect(lambda _request, reply: replies.append(reply))
    client.start(UiaAction.OBSERVE, {})
    qtbot.waitUntil(lambda: bool(ControlledProcess.created))
    process = ControlledProcess.created[-1]
    qtbot.waitUntil(lambda: process.state() is QProcess.ProcessState.Running)
    try:
        yield Rig(client, clock, process, failures, replies)
    finally:
        process.finish_on_kill = True
        client.cancel()
        if client.busy:
            process.finish()
        qtbot.waitUntil(lambda: not client.busy)
        client.deleteLater()


def test_buffered_ready_is_observed_before_startup_timeout(rig: Rig) -> None:
    """The due timer can overtake readyRead, without losing a valid handshake."""
    rig.process.feed(b'{"ready":true,"protocol":1}\n', notify=False)
    rig.clock.seconds = UiaProcess.STARTUP_MS / 1000
    rig.client._get_lease()._timeout()
    assert not rig.failures
    assert len(rig.process.sent) == 1
    rig.response()
    assert len(rig.replies) == 1


def test_buffered_reply_completes_before_operation_timeout(rig: Rig) -> None:
    """An already buffered result cannot be discarded due to event ordering."""
    rig.ready()
    rig.response(notify=False)
    rig.clock.seconds = UiaProcess.DEADLINE_MS / 1000
    rig.client._get_lease()._timeout()
    assert not rig.failures
    assert len(rig.replies) == 1
    assert not rig.client.busy


def test_early_timeout_cannot_shorten_the_cold_start_budget(rig: Rig) -> None:
    """A coarse or stale Qt timeout must respect the monotonic deadline."""
    rig.clock.seconds = UiaProcess.STARTUP_MS / 1000 - 0.1
    rig.client._get_lease()._timeout()
    assert not rig.failures
    assert not rig.process.sent
    rig.ready()
    rig.response()
    assert len(rig.replies) == 1


def test_cold_start_cap_does_not_reset_on_process_or_partial_progress(rig: Rig) -> None:
    """No child phase or fragmented response can grant an extra startup allowance."""
    rig.clock.seconds = UiaProcess.STARTUP_MS / 1000 - 0.1
    rig.process.started.emit()
    rig.process.feed(b'{"ready":')
    rig.clock.seconds += 0.1
    rig.client._get_lease()._timeout()
    assert len(rig.failures) == 1
    assert "before a command was sent" in rig.failures[0]
    assert not rig.process.sent
    assert not rig.client.busy


def test_old_startup_timeout_cannot_expire_the_new_operation(rig: Rig) -> None:
    """A queued timeout from the previous phase cannot shorten the new budget."""
    rig.clock.seconds = UiaProcess.STARTUP_MS / 1000 - 0.1
    rig.ready()
    rig.clock.seconds += 0.1
    rig.client._get_lease()._timeout()
    assert not rig.failures
    rig.response()
    assert len(rig.replies) == 1


def test_drained_reply_callback_can_submit_the_next_request(
    rig: Rig, qtbot: QtBot
) -> None:
    """The old timeout must not fail a request created by direct completion."""
    rig.ready()

    def next_request(_request: object, _reply: object) -> None:
        rig.client.start(UiaAction.OBSERVE, {})

    rig.client.completed.connect(next_request)
    rig.response(notify=False)
    rig.clock.seconds += UiaProcess.DEADLINE_MS / 1000
    rig.client._get_lease()._timeout()
    rig.client.completed.disconnect(next_request)
    assert not rig.failures
    assert rig.client.busy
    qtbot.waitUntil(lambda: len(rig.process.sent) == 2)
    rig.response()
    assert len(rig.replies) == 2
    assert not rig.client.busy


def test_cleanup_watchdog_retains_the_slot_until_exit_receipt(rig: Rig) -> None:
    """A kill request is not evidence that helper-owned resources are gone."""
    rig.process.finish_on_kill = False
    cleanup_failures: list[str] = []
    cleaned: list[bool] = []
    rig.client.cleanup_failed.connect(cleanup_failures.append)
    rig.client.register_scope_cleanup(lambda: cleaned.append(True))
    rig.client.cancel()
    lease = rig.client._get_lease()
    rig.clock.seconds += lease.CLEANUP_MS / 1000
    lease._cleanup_timeout()
    assert rig.client.busy
    assert not cleaned
    assert len(cleanup_failures) == 1
    assert rig.client.diagnostics is not None
    assert rig.client.diagnostics.cleanup == "unconfirmed"
    rig.process.finish()
    assert not rig.client.busy
    assert cleaned == [True]
    assert rig.client.diagnostics.cleanup == "confirmed"


def test_due_startup_drains_the_actual_private_pipe(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real local IPC plus a controlled child reproduces the handshake race."""
    clock = Clock()
    monkeypatch.setattr(uia_worker, "QProcess", ControlledProcess)
    monkeypatch.setattr(uia_worker, "monotonic", clock.now)
    client = UiaProcess(
        helper_command=("fixture-only",), qualified=True, use_local_pipe=True
    )
    failures: list[str] = []
    client.failed.connect(lambda _request, message: failures.append(message))
    request = client.start(UiaAction.OBSERVE, {})
    qtbot.waitUntil(
        lambda: ControlledProcess.created[-1].state() is QProcess.ProcessState.Running
    )
    lease = client._get_lease()
    pipe = lease.findChild(UiaPipe)
    assert pipe is not None
    peer = QLocalSocket()
    peer.connectToServer(pipe.name)
    try:
        qtbot.waitUntil(lambda: pipe.findChild(QLocalSocket) is not None)
        accepted = pipe.findChild(QLocalSocket)
        assert accepted is not None
        with QSignalBlocker(accepted):
            peer.write(b'{"ready":true,"protocol":1}\n')
            peer.flush()
            qtbot.waitUntil(lambda: accepted.bytesAvailable() > 0)
            clock.seconds = UiaProcess.STARTUP_MS / 1000
            lease._timeout()
            assert not failures
            qtbot.waitUntil(lambda: peer.bytesAvailable() > 0)
            assert peer.readAll().data() == request.encoded + b"\n"
        diagnostics = client.diagnostics
        assert diagnostics is not None
        assert "pipe_accepted" in {stamp.stage for stamp in diagnostics.events}
    finally:
        client.cancel()
        qtbot.waitUntil(lambda: not client.busy)
        peer.abort()
        peer.deleteLater()
        client.deleteLater()


def test_buffered_invalid_handshake_never_dispatches_an_operation(rig: Rig) -> None:
    """A deadline drain does not relax protocol validation."""
    rig.process.feed(b'{"ready":true,"protocol":99}\n', notify=False)
    rig.clock.seconds = UiaProcess.STARTUP_MS / 1000
    rig.client._get_lease()._timeout()
    assert len(rig.failures) == 1
    assert "protocol" in rig.failures[0]
    assert not rig.process.sent
    assert not rig.client.busy


def test_due_startup_accepts_an_already_pending_pipe_connection(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline may also overtake QLocalServer's newConnection signal."""
    clock = Clock()
    monkeypatch.setattr(uia_worker, "QProcess", ControlledProcess)
    monkeypatch.setattr(uia_worker, "monotonic", clock.now)
    client = UiaProcess(
        helper_command=("fixture-only",), qualified=True, use_local_pipe=True
    )
    failures: list[str] = []
    client.failed.connect(lambda _request, message: failures.append(message))
    request = client.start(UiaAction.OBSERVE, {})
    qtbot.waitUntil(
        lambda: ControlledProcess.created[-1].state() is QProcess.ProcessState.Running
    )
    lease = client._get_lease()
    pipe = lease.findChild(UiaPipe)
    assert pipe is not None
    server = pipe.findChild(QLocalServer)
    assert server is not None
    peer = QLocalSocket()
    try:
        with QSignalBlocker(server):
            peer.connectToServer(pipe.name)
            qtbot.waitUntil(server.hasPendingConnections)
            pending = server.findChild(QLocalSocket)
            assert pending is not None
            peer.write(b'{"ready":true,"protocol":1}\n')
            peer.flush()
            qtbot.waitUntil(lambda: pending.bytesAvailable() > 0)
            # findChild is recursive: a pending server child is not accepted yet.
            assert pending.parent() is server
            clock.seconds = UiaProcess.STARTUP_MS / 1000
            lease._timeout()
            assert not failures
            assert pending.parent() is pipe
            pipe._connected()  # A delayed newConnection notification sees no peer.
            diagnostics = client.diagnostics
            assert diagnostics is not None
            assert [stamp.stage for stamp in diagnostics.events].count(
                "pipe_accepted"
            ) == 1
            qtbot.waitUntil(lambda: peer.bytesAvailable() > 0)
            assert peer.readAll().data() == request.encoded + b"\n"
    finally:
        client.cancel()
        qtbot.waitUntil(lambda: not client.busy)
        peer.abort()
        peer.deleteLater()
        client.deleteLater()


def test_cancelled_request_cannot_consume_late_buffered_ready(rig: Rig) -> None:
    """Retired readiness and timeout delivery cannot revive an operation."""
    rig.process.feed(b'{"ready":true,"protocol":1}\n', notify=False)
    rig.client.cancel()
    rig.client._get_lease()._timeout()
    assert len(rig.failures) == 1
    assert not rig.process.sent
    assert not rig.client.busy


def test_unconfirmed_cleanup_blocks_retry_without_running_a_second_helper(
    rig: Rig,
) -> None:
    """The watchdog's error receipt does not grant permission to overlap workers."""
    rig.process.finish_on_kill = False
    rig.client.cancel()
    rig.clock.seconds += uia_worker.UiaWorkerLease.CLEANUP_MS / 1000
    rig.client._get_lease()._cleanup_timeout()
    with pytest.raises(CaptureError, match="busy"):
        rig.client.start(UiaAction.STEP, {})
    assert rig.process.kill_count == 1
    rig.process.finish()


def test_stale_cleanup_timeout_cannot_report_a_new_cancellation_early(rig: Rig) -> None:
    """A queued old watchdog respects the active cleanup's own deadline."""
    rig.process.finish_on_kill = False
    errors: list[str] = []
    rig.client.cleanup_failed.connect(errors.append)
    rig.client.cancel()
    rig.client._get_lease()._cleanup_timeout()
    assert not errors
    assert rig.client.busy
    rig.process.finish()


def test_cleanup_watchdog_can_confirm_exit_without_a_finished_signal(rig: Rig) -> None:
    """QProcess NotRunning is a terminal receipt even if its signal is delayed."""
    rig.process.finish_on_kill = False
    cleaned: list[bool] = []
    rig.client.register_scope_cleanup(lambda: cleaned.append(True))
    rig.client.cancel()
    rig.process.current_state = QProcess.ProcessState.NotRunning
    rig.clock.seconds += uia_worker.UiaWorkerLease.CLEANUP_MS / 1000
    rig.client._get_lease()._cleanup_timeout()
    assert not rig.client.busy
    assert cleaned == [True]


def test_repeated_dispatch_cannot_send_the_same_operation_twice(rig: Rig) -> None:
    """Reentrant scheduling may revisit dispatch, but movement is never replayed."""
    lease = rig.client._get_lease()
    request = lease._pending
    assert request is not None
    rig.ready()
    lease._dispatch(rig.process, request, ("fixture-only",))
    assert len(rig.process.sent) == 1
    rig.response()
    assert len(rig.replies) == 1


def test_cleanup_watchdog_retains_ownership_in_its_final_half_second(rig: Rig) -> None:
    """An early callback cannot report timeout or release a still-live helper."""
    rig.process.finish_on_kill = False
    errors: list[str] = []
    cleaned: list[bool] = []
    rig.client.cleanup_failed.connect(errors.append)
    rig.client.register_scope_cleanup(lambda: cleaned.append(True))
    rig.client.cancel()
    lease = rig.client._get_lease()
    rig.clock.seconds = lease.CLEANUP_MS / 1000 - 0.5
    lease._cleanup_timeout()
    assert not errors
    assert not cleaned
    assert rig.client.busy
    assert lease._cleanup_deadline.isActive()
    rig.clock.seconds += 0.5
    lease._cleanup_timeout()
    assert len(errors) == 1
    assert not cleaned
    assert rig.client.busy
    rig.process.finish()
    assert cleaned == [True]
    assert not rig.client.busy


def test_stale_timeout_after_reply_preserves_idle_worker_and_next_request(
    rig: Rig, qtbot: QtBot
) -> None:
    """A completed operation's queued callback cannot kill its warm worker."""
    rig.ready()
    rig.response()
    rig.clock.seconds = 100
    rig.client._get_lease()._timeout()
    assert not rig.failures
    assert rig.process.kill_count == 0
    assert rig.process.state() is QProcess.ProcessState.Running
    assert not rig.client.busy
    assert len(rig.replies) == 1
    rig.client.start(UiaAction.OBSERVE, {})
    qtbot.waitUntil(lambda: len(rig.process.sent) == 2)
    rig.response()
    assert len(rig.replies) == 2
    assert not rig.failures


def test_operation_timeout_records_positive_lateness_in_milliseconds(rig: Rig) -> None:
    """Timing evidence distinguishes the due time from Qt delivery lateness."""
    rig.ready()
    rig.clock.seconds = UiaProcess.DEADLINE_MS / 1000 + 0.125
    rig.client._get_lease()._timeout()
    assert len(rig.failures) == 1
    diagnostics = rig.client.diagnostics
    assert diagnostics is not None
    assert diagnostics.max_deadline_lateness_ms == 125
    assert diagnostics.request_sent
    assert not rig.client.busy
