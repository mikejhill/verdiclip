"""Independent worker admission seams; no native sockets or desktop operations."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import PureWindowsPath
from typing import Any, cast
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QByteArray
from tests.capture.test_sequence_retention import frame

from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.capture.sequence_credit import CreditToken, ParentCredit
from verdiclip.capture.sequence_retention import SequenceCollector
from verdiclip.platform import sequence_channel as channel
from verdiclip.platform.owned_helper import HelperStatus, OwnedHelper
from verdiclip.platform.sequence_protocol import Message, ProtocolError, encode
from verdiclip.platform.sequence_runtime import SequenceRuntimeManifest


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[channel._Worker]:
    """Create only the inert QObject and fake helper/control endpoints."""
    monkeypatch.setattr(channel.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    value = channel._Worker(1, Mock(spec=SequenceRuntimeManifest), threading.Event())
    value.epoch = "a" * 32
    value.peer = 42
    value.session_deadline = value.deadline = 31.0
    value.sequence_deadline_ns = 31_000_000_000
    value.sequence_clock = channel.SequenceDeadline(value.sequence_deadline_ns)
    value.collector = SequenceCollector(value.epoch, value._admit)
    value.pipe_name = "VerdiClip-sequence-" + "b" * 32
    value.helper = Mock(spec=OwnedHelper)
    value.helper.status = live_status()
    value.helper.poll.return_value = live_status()
    value.helper.verify_ready.return_value = live_status()
    monkeypatch.setattr(channel, "QThread", Mock())
    yield value
    if shiboken6.isValid(value):
        shiboken6.delete(value)


def live_status() -> HelperStatus:
    """Exact direct helper, live retained job and verified kernel image."""
    return HelperStatus(
        pid=42,
        producer_verified=True,
        exit_code=None,
        remaining_pids=(42,),
        termination_requested=False,
        cleanup_confirmed=False,
        closed=False,
        failure=None,
        cleanup_errors=(),
    )


def ready(worker: channel._Worker) -> Message:
    """Build the exact separately versioned ready schema."""
    return Message(
        "ready",
        0,
        {
            "pid": 42,
            "executable": "/verified/python",
            "epoch": worker.epoch,
            "protocol_version": 3,
            "peer_schema": 4,
            "credit_schema": 2,
            "sequence_schema": 1,
            "runtime_schema": 5,
            "max_frames": 8,
            "max_requests": 32,
        },
    )


def socket_with(raw: bytes = b"") -> Mock:
    """Model bounded Qt reads with real QByteArray values and no endpoint."""
    socket = Mock()
    data = bytearray(raw)
    socket.bytesAvailable.side_effect = lambda: len(data)
    socket.bytesToWrite.return_value = 0
    socket.socketDescriptor.return_value = 7
    socket.write.side_effect = len

    def read(size: int) -> QByteArray:
        block = bytes(data[:size])
        del data[:size]
        return QByteArray(block)

    socket.read.side_effect = read
    return socket


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", True),
        ("pid", 43),
        ("executable", 7),
        ("epoch", "c" * 32),
        ("protocol_version", 2),
        ("peer_schema", 3),
        ("credit_schema", 1),
        ("sequence_schema", True),
        ("runtime_schema", 4),
        ("max_frames", 9),
        ("max_requests", 33),
        ("extra", 1),
    ],
)
def test_ready_profile_rejects_before_publication(
    worker: channel._Worker, field: str, value: object
) -> None:
    """Unsupported profile and malformed ready identity never authorize readiness."""
    message = ready(worker)
    message.payload[field] = value
    with pytest.raises(ProtocolError):
        worker._message(message)
    assert worker.verified_identity is None
    assert not worker.ready_notified


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("producer_verified", False),
        ("pid", 43),
        ("failure", "identity failed"),
        ("cleanup_errors", ("close failed",)),
        ("closed", True),
        ("termination_requested", True),
        ("cleanup_confirmed", True),
        ("exit_code", 0),
        ("remaining_pids", (43,)),
    ],
)
def test_ready_requires_live_retained_identity(
    worker: channel._Worker, field: str, value: object
) -> None:
    """Ready self-report cannot substitute for every retained kernel/job condition."""
    assert isinstance(worker.helper, Mock)
    worker.helper.verify_ready.return_value = replace(live_status(), **{field: value})
    with pytest.raises(ProtocolError, match="unverified-producer"):
        worker._message(ready(worker))
    assert worker.verified_identity is None


def test_duplicate_ready_and_partial_tail_fail_before_notification(
    worker: channel._Worker,
) -> None:
    """Validate a complete received batch before emitting ready."""
    worker.socket = socket_with(encode(ready(worker)) + b"\x00")
    emitted = []
    worker.ready.connect(emitted.append)
    worker._readable()
    assert worker.failure == "unsolicited-trailing-bytes"
    assert emitted == []
    assert worker.phase == "retiring"
    assert isinstance(worker.helper, Mock)
    worker.helper.terminate.assert_called_once()


@pytest.mark.parametrize(
    "raw", [b"\x00\x00\x40\x01", b"\x00\x00\x00\x02{}", b"\x00\x00\x00\x01\xff"]
)
def test_malformed_control_permanently_retires(
    worker: channel._Worker, raw: bytes
) -> None:
    """Malformed length, envelope and UTF8 cannot recover into a later ready."""
    socket = socket_with(raw)
    worker.socket = socket
    worker._readable()
    failure = worker.failure
    assert failure is not None
    worker._readable()
    assert worker.failure == failure
    assert worker.phase == "retiring"
    socket.abort.assert_called_once()


@pytest.mark.parametrize("pid", [0, 43])
def test_control_connection_requires_kernel_pid(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, pid: int
) -> None:
    """Reject the actual connected PID before any self-reported ready bytes."""
    worker.server = Mock()
    socket = socket_with()
    worker.server.nextPendingConnection.return_value = socket
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: pid)
    worker._connected()
    assert worker.failure == "wrong-peer-pid"
    socket.read.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("pid", 43), ("remaining_pids", ()), ("closed", True), ("failure", "failed")],
)
def test_frame_connection_authenticates_same_live_helper(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Each fresh frame pipe repeats retained direct-peer membership validation."""
    worker.phase = "busy"
    worker.observe_pairing = Mock(spec=channel.SequencePairing)
    worker.frame_server = Mock()
    socket = socket_with()
    worker.frame_server.nextPendingConnection.return_value = socket
    assert isinstance(worker.helper, Mock)
    worker.helper.poll.return_value = replace(live_status(), **{field: value})
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 42)
    worker._frame_connected()
    assert worker.failure == "wrong-frame-peer-pid"
    socket.read.assert_not_called()


def test_stale_frame_notifications_never_touch_next_socket(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old readable/disconnect/resume events are ignored; observed connections fail."""
    worker.frame_serial = 8
    readable = Mock()
    monkeypatch.setattr(worker, "_frame_readable", readable)
    worker._frame_event(7, disconnected=True)
    worker._resume_current_frame(7)
    readable.assert_not_called()
    assert not worker.frame_disconnected
    worker._accept_frame_connection(7)
    assert worker.failure == "connection-on-retired-frame"


def test_frame_listener_is_fresh_and_fixed(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next listener has only the derived current frame name and one slot."""
    worker.phase = "ready"
    worker.sequence_begun = True
    worker.sequence_target = ObserveTarget(7, 1234, 99, (11, 21))
    factory = Mock()
    server = factory.return_value
    server.listen.return_value = True
    server.hasPendingConnections.return_value = False
    monkeypatch.setattr(channel, "QLocalServer", factory)
    payload: dict[str, object] = {
        "operation": "observe",
        "frame_id": 1,
        "requested_ns": 1_000_000_000,
        "deadline_ns": 3_000_000_000,
    }
    worker._begin_observe(payload)
    server.listen.assert_called_once_with(worker.pipe_name + "-frame-1")
    server.setMaxPendingConnections.assert_called_once_with(1)
    worker._close_frame()
    server.close.assert_called_once()
    server.deleteLater.assert_called_once()
    assert worker.frame_server is None
    assert worker.frame_serial == 2


def test_expired_original_stamp_never_creates_listener(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue delay consumes the original 2s, not a new worker receive budget."""
    worker.phase = "ready"
    worker.sequence_begun = True
    worker.sequence_target = ObserveTarget(7, 1234, 99, (11, 21))
    factory = Mock()
    monkeypatch.setattr(channel, "QLocalServer", factory)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 3_000_000_000)
    with pytest.raises(ProtocolError):
        worker._begin_observe(
            {
                "operation": "observe",
                "frame_id": 1,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 3_000_000_000,
            }
        )
    factory.assert_not_called()
    assert worker.frame_id == 0


def test_global_budget_rejects_before_dispatch(worker: channel._Worker) -> None:
    """Exhausted global records never reach ready identity verification."""
    worker.budget.records = 512
    with pytest.raises(ProtocolError, match="sequence-record-bound"):
        worker._message(ready(worker))
    assert isinstance(worker.helper, Mock)
    worker.helper.verify_ready.assert_not_called()
    assert worker.budget.records == 512


@pytest.mark.parametrize(
    ("field", "value"), [("sequence_schema", True), ("max_frames", 8.0)]
)
def test_begin_response_requires_strict_literal_types(
    worker: channel._Worker, field: str, value: object
) -> None:
    """Wire-valid bool/float equality cannot satisfy the literal begin schema."""
    worker.phase = "busy"
    worker.flight.pending = 1
    payload: dict[str, object] = {
        "operation": "sequence_begin",
        "sequence_schema": 1,
        "epoch": worker.epoch,
        "max_frames": 8,
        "deadline_ns": worker.sequence_deadline_ns,
    }
    payload[field] = value
    with pytest.raises(ProtocolError):
        worker._message(Message("response", 1, payload))
    assert not worker.sequence_begun


def test_exhausted_budget_prevents_socket_read(worker: channel._Worker) -> None:
    """The global cap is checked before accepting another bounded control chunk."""
    socket = socket_with(encode(ready(worker)))
    worker.socket = socket
    worker.budget.records = 512
    worker._readable()
    assert worker.failure is not None
    socket.read.assert_not_called()
    assert worker.budget.records == 512


@pytest.mark.parametrize(("count", "retained"), [(8, 0), (0, 11_010_049)])
def test_capacity_preflight_before_frame_listener(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, count: int, retained: int
) -> None:
    """Reserve room for a worst-case source before knowing its observed identity."""
    worker.sequence_begun = True
    worker.sequence_target = ObserveTarget(7, 1234, 99, (11, 21))
    collector = Mock(spec=SequenceCollector)
    collector.count = count
    collector.retained_bytes = retained
    worker.collector = collector
    factory = Mock()
    monkeypatch.setattr(channel, "QLocalServer", factory)
    with pytest.raises(ProtocolError, match="sequence-capacity-before-request"):
        worker._begin_observe(
            {
                "operation": "observe",
                "frame_id": 1,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 3_000_000_000,
            }
        )
    factory.assert_not_called()
    assert worker.frame_id == 0
    collector.reserve.assert_not_called()


def test_duplicate_connection_rejected_without_consuming_another(
    worker: channel._Worker,
) -> None:
    """A second connection cannot replace the authenticated current binary socket."""
    worker.phase = "busy"
    worker.observe_pairing = Mock(spec=channel.SequencePairing)
    server = Mock()
    worker.frame_server = server
    original = socket_with()
    worker.frame_socket = original
    worker._frame_connected()
    assert worker.failure == "unexpected-frame-connection"
    server.nextPendingConnection.assert_not_called()
    assert worker.frame_socket is original


@pytest.mark.parametrize(
    ("field", "value"), [("sequence_schema", True), ("frames", True)]
)
def test_finish_response_requires_strict_literal_types(
    worker: channel._Worker, field: str, value: object
) -> None:
    """A malformed finish cannot seal even a single otherwise accepted frame."""
    worker.phase = "busy"
    worker.flight.pending = 3
    worker.finish_requested = True
    worker.proofs = ["0" * 64]
    collector = Mock(spec=SequenceCollector)
    collector.chain = bytes(32)
    worker.collector = collector
    payload: dict[str, object] = {
        "operation": "sequence_finish",
        "sequence_schema": 1,
        "epoch": worker.epoch,
        "frames": 1,
        "proof_sha256": worker.proofs,
        "sequence_sha256": collector.chain.hex(),
    }
    payload[field] = value
    with pytest.raises(ProtocolError, match="invalid-sequence-finish"):
        worker._message(Message("response", 3, payload))
    collector.finish.assert_not_called()


@pytest.mark.parametrize("failure", [None, "command", "listen", "launch", "cancel"])
def test_actual_startup_boundaries(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Run actual startup, mocking only fixed launch and native resource adapters."""
    worker.helper = None
    command = Mock()
    build = Mock(return_value=command)
    servers = Mock()
    servers.return_value.listen.return_value = failure != "listen"
    timers = Mock()
    helper = Mock(spec=OwnedHelper)
    helpers = Mock(return_value=helper)
    monkeypatch.setattr(channel, "_build_command", build)
    monkeypatch.setattr(channel, "QLocalServer", servers)
    monkeypatch.setattr(channel, "QTimer", timers)
    monkeypatch.setattr(channel, "OwnedHelper", helpers)
    if failure == "command":
        build.side_effect = ValueError("manifest mismatch")
    elif failure == "launch":
        helper.start.side_effect = OSError("launch failure")
    elif failure == "cancel":
        helper.start.side_effect = lambda _command: worker.cancelled.set()
    worker.start()
    assert worker.launch_returned
    assert worker.sequence_deadline_ns == 31_000_000_000
    timers.return_value.start.assert_called_once()
    timers.return_value.timeout.connect.assert_called_once_with(worker._poll)
    if failure is None:
        assert worker.failure is None
        assert worker.phase == "starting"
        helper.start.assert_called_once_with(command)
        servers.return_value.setMaxPendingConnections.assert_called_once_with(1)
        assert servers.return_value.listen.call_args.args[0].startswith(
            "VerdiClip-sequence-"
        )
    else:
        assert worker.failure is not None
        assert worker.phase == "retiring"
        assert worker.deadline == 6.0
        if failure in {"command", "listen"}:
            helpers.assert_not_called()
        else:
            helper.terminate.assert_called_once()


def test_actual_connection_reads_verified_ready(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live control connection validates its ready batch before either signal."""
    server = Mock()
    socket = socket_with(encode(ready(worker)))
    server.nextPendingConnection.return_value = socket
    worker.server = server
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 42)
    identities = []
    notifications = []
    worker.identity.connect(
        lambda generation, raw: identities.append((generation, json.loads(raw)))
    )
    worker.ready.connect(notifications.append)
    worker._connected()
    assert worker.failure is None
    assert worker.phase == "ready"
    assert notifications == [1]
    assert identities == [(1, ready(worker).payload)]
    socket.setReadBufferSize.assert_called_once_with(16388)
    worker._notify_ready()
    assert notifications == [1]
    worker._connected()
    assert worker.failure == "unexpected-connection"
    server.nextPendingConnection.assert_called_once()


@pytest.mark.parametrize("missing", ["socket", "helper"])
def test_connection_without_owned_peer(worker: channel._Worker, missing: str) -> None:
    """Neither a missing accepted endpoint nor missing helper is a recoverable ready."""
    server = Mock()
    server.nextPendingConnection.return_value = (
        None if missing == "socket" else socket_with()
    )
    worker.server = server
    if missing == "helper":
        worker.helper = None
    worker._connected()
    assert worker.failure == "missing-owned-peer"
    assert worker.phase == "retiring"


@pytest.mark.parametrize("trigger", ["cancel", "operation", "session", "frame"])
def test_poll_latches_first_failure_without_releasing_live_helper(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    """Each expiry starts the one cleanup phase while retaining a live worker."""
    worker.launch_returned = True
    worker.phase = "busy"
    if trigger == "cancel":
        worker.cancelled.set()
    elif trigger == "operation":
        worker.deadline = 1.0
    elif trigger == "session":
        worker.session_deadline = 1.0
    else:
        worker.observe_deadline_ns = 1_000_000_000
    worker._poll()
    assert worker.failure == (
        "cancelled" if trigger == "cancel" else "operation-timeout"
    )
    assert worker.phase == "retiring"
    assert worker.deadline == 6.0
    assert not worker.done
    first = worker.failure
    worker._fail("later failure")
    assert worker.failure == first
    assert worker.deadline == 6.0
    assert isinstance(worker.helper, Mock)
    worker.helper.release_unconfirmed.assert_not_called()
    monkeypatch.setattr(channel.time, "monotonic", lambda: 6.0)
    emitted = []
    worker.terminal.connect(
        lambda generation, raw: emitted.append((generation, json.loads(raw)))
    )
    worker._poll()
    assert worker.done
    assert emitted[0][1]["failure"] == first
    assert emitted[0][1]["cleanup_failure"] == "cleanup-timeout"
    assert not emitted[0][1]["passed"]
    worker.helper.release_unconfirmed.assert_called_once()
    worker._poll()
    assert len(emitted) == 1


def test_poll_without_launched_helper_reports_failed_receipt(
    worker: channel._Worker,
) -> None:
    """No helper cannot be misreported as natural successful retirement."""
    worker.helper = None
    worker.launch_returned = True
    worker.failure = "launch failed"
    receipts = []
    worker.terminal.connect(lambda _generation, raw: receipts.append(json.loads(raw)))
    worker._poll()
    assert worker.done
    assert receipts[0]["not_launched"]
    assert not receipts[0]["passed"]
    assert receipts[0]["failure"] == "launch failed"


@pytest.mark.parametrize("exit_code", [None, 7])
def test_poll_producer_failure_retires_only_with_confirmed_cleanup(
    worker: channel._Worker, exit_code: int | None
) -> None:
    """A failed process or native poll never becomes success after resource cleanup."""
    worker.launch_returned = True
    assert isinstance(worker.helper, Mock)
    status = replace(
        live_status(),
        failure="native failure" if exit_code is None else None,
        exit_code=exit_code,
        cleanup_confirmed=True,
        closed=True,
        remaining_pids=(),
    )
    worker.helper.poll.return_value = worker.helper.status = status
    receipts = []
    worker.terminal.connect(lambda _generation, raw: receipts.append(json.loads(raw)))
    worker._poll()
    assert worker.done
    assert receipts[0]["failure"] == "producer-failed"
    assert not receipts[0]["passed"]


def test_poll_exception_keeps_live_owner_and_failure(worker: channel._Worker) -> None:
    """An unexpected adapter exception invalidates payload but retains ownership."""
    assert isinstance(worker.helper, Mock)
    worker.helper.poll.side_effect = OSError("query failed")
    worker._poll()
    assert worker.failure == "query failed"
    assert not worker.done
    worker.helper.terminate.assert_called_once()


@pytest.mark.parametrize("scenario", ["unsolicited", "early_eof", "late_body"])
def test_frame_callbacks_reject_unrequested_evidence(
    worker: channel._Worker, scenario: str
) -> None:
    """Observed data/EOF outside the current transfer fails the whole session."""
    worker.frame_socket = socket_with(b"x" if scenario != "early_eof" else b"")
    if scenario == "early_eof":
        worker._frame_disconnected()
    elif scenario == "late_body":
        worker.observe_pairing = Mock(spec=channel.SequencePairing)
        worker.frame_eof = True
        worker._frame_readable()
    else:
        worker._frame_readable()
    assert (
        worker.failure
        == {
            "unsolicited": "unsolicited-frame-bytes",
            "early_eof": "unexpected-frame-eof",
            "late_body": "frame-bytes-after-eof",
        }[scenario]
    )
    assert worker.phase == "retiring"


def test_frame_resume_and_queue_are_generation_scoped(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most one continuation is queued and it cannot operate on a retired frame."""
    timer = Mock()
    monkeypatch.setattr(channel, "QTimer", timer)
    worker.frame_socket = socket_with()
    worker.frame_serial = 5
    worker._queue_frame()
    worker._queue_frame()
    timer.singleShot.assert_called_once()
    callback = timer.singleShot.call_args.args[2]
    worker.frame_serial = 6
    callback()
    assert worker.frame_drain_queued
    worker._resume_frame()
    assert not worker.frame_drain_queued
    assert worker.failure is None
    worker.finish_requested = True
    worker._frame_event(6, disconnected=True)
    assert worker.frame_eof


def test_close_frame_rejects_surplus_pending_connection(
    worker: channel._Worker,
) -> None:
    """Unexpected delivered connections cannot be hidden by server retirement."""
    server = Mock()
    server.hasPendingConnections.return_value = True
    worker.frame_server = server
    with pytest.raises(ProtocolError, match="surplus-frame-connection"):
        worker._close_frame()
    server.close.assert_not_called()
    assert worker.frame_serial == 0


def test_sequence_clock_history_survives_ready_gap(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer ready/frame transition cannot replace the original sequence clock."""
    worker.phase = "ready"
    worker._admit()
    original = worker.sequence_clock
    assert original is not None
    original.frame(1_000_000_000, 3_000_000_000)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 2_000_000_000)
    worker._admit()
    original.frame_deadline_ns = None
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_999_999_999)
    with pytest.raises(ProtocolError):
        worker._admit()
    assert original.failed
    assert worker.sequence_clock is original
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 2_000_000_001)
    with pytest.raises(ProtocolError):
        worker._admit()


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), 0.5])
def test_float_clock_invalid_or_regressed_retires(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, bad: float
) -> None:
    """Float deadline admission cannot accept booleans, nonfinite or regressed time."""
    worker._admit()
    monkeypatch.setattr(channel.time, "monotonic", lambda: bad)
    with pytest.raises(ProtocolError, match="invalid-monotonic-clock"):
        worker._admit()


def test_frame_connection_positive_and_eof_is_not_complete_header(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh authenticated endpoint still requires a complete header and EOF."""
    worker.phase = "busy"
    worker.observe_pairing = channel.SequencePairing(
        ObserveTarget(7, 1234, 99, (11, 21)), worker.epoch, 2, 1, worker._admit
    )
    server = Mock()
    socket = socket_with()
    worker.frame_server = server
    server.nextPendingConnection.return_value = socket
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 42)
    worker._frame_connected()
    assert worker.failure is None
    socket.setReadBufferSize.assert_called_once_with(65536)
    socket.readyRead.connect.call_args.args[0]()
    assert worker.failure is None
    socket.disconnected.connect.call_args.args[0]()
    assert worker.failure == "truncated-frame-header-or-metadata"


@pytest.mark.parametrize("missing", ["socket", "helper"])
def test_frame_connection_requires_owned_endpoint(
    worker: channel._Worker, missing: str
) -> None:
    """A connection callback without an owned peer cannot leave a retryable frame."""
    worker.phase = "busy"
    worker.observe_pairing = Mock(spec=channel.SequencePairing)
    worker.frame_server = Mock()
    worker.frame_server.nextPendingConnection.return_value = (
        None if missing == "socket" else socket_with()
    )
    if missing == "helper":
        worker.helper = None
    worker._frame_connected()
    assert worker.failure == "missing-owned-frame-peer"


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_socket",
        "missing_pairing",
        "no_credit",
        "body_without_credit",
        "header_overflow",
    ],
)
def test_binary_drain_rejects_invalid_state(
    worker: channel._Worker, scenario: str
) -> None:
    """Draining uses actual header pairing and rejects unreserved body data."""
    target = ObserveTarget(7, 1234, 99, (11, 21))
    worker.phase = "busy"
    worker.observe_pairing = channel.SequencePairing(
        target, worker.epoch, 2, 1, worker._admit
    )
    worker.frame_socket = socket_with(b"x")
    if scenario == "missing_socket":
        worker.frame_socket = None
        with pytest.raises(ProtocolError, match="missing-frame-state"):
            worker._drain_frame()
        return
    if scenario == "missing_pairing":
        worker.observe_pairing = None
        with pytest.raises(ProtocolError, match="missing-frame-state"):
            worker._drain_frame()
        return
    if scenario == "header_overflow":
        worker.observe_pairing.raw_header = b"x" * 128
        worker.frame_header_read = 127
        worker._frame_readable()
        assert worker.failure is not None
        return
    candidate, proof = frame(1)
    worker.observe_pairing = channel.SequencePairing(
        proof.observation.target, worker.epoch, 2, 1, worker._admit
    )
    worker.observe_pairing.metadata(candidate.observation)
    worker.observe_pairing.header(candidate.header.encode())
    worker.frame_header_read = 128
    if scenario == "body_without_credit":
        worker.credits = ParentCredit(
            worker.epoch, 2, 1, len(candidate.rgb), worker.credit_gate
        )
    worker._frame_readable()
    assert worker.failure == (
        "missing-frame-credit" if scenario == "no_credit" else "body-without-credit"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00",
        encode(Message("response", 2, {})),
        encode(Message("frame_credit", 2, {})),
    ],
)
def test_grant_rejects_malformed_queued_control(
    worker: channel._Worker, raw: bytes
) -> None:
    """Queued credit requires exactly one complete matching credit2 envelope."""
    worker.phase = "busy"
    worker.frame_id = 1
    worker.socket = socket_with()
    worker.credits = ParentCredit(worker.epoch, 2, 1, 3, worker.credit_gate)
    worker.grant_credit(1, raw)
    assert worker.failure is not None
    assert isinstance(worker.socket, Mock)
    worker.socket.write.assert_not_called()


@pytest.mark.parametrize("scenario", ["generation", "unannounced", "write_failed"])
def test_grant_rejects_stale_or_failed_delivery(
    worker: channel._Worker, scenario: str
) -> None:
    """Invalid grants never reserve; a failed committed write is never retried."""
    worker.phase = "busy"
    worker.frame_id = 1
    worker.socket = socket_with()
    worker.credits = ParentCredit(worker.epoch, 2, 1, 3, worker.credit_gate)
    token = CreditToken.for_chunk(worker.epoch, 2, 1, 3, 0)
    worker.credits.receive_wait(token)
    worker.credit_notified = token if scenario != "unannounced" else None
    if scenario == "write_failed":
        worker.socket.write.side_effect = lambda _raw: -1
    worker.grant_credit(
        2 if scenario == "generation" else 1,
        encode(Message("frame_credit", 2, token.payload())),
    )
    assert worker.failure is not None
    assert worker.credits.snapshot().granted_bytes == (
        3 if scenario == "write_failed" else 0
    )
    if scenario == "write_failed":
        worker.socket.write.assert_called_once()
    else:
        worker.socket.write.assert_not_called()


@pytest.mark.parametrize("caller", [True, 0, 32])
def test_submit_rejects_invalid_caller_without_write(
    worker: channel._Worker, caller: int
) -> None:
    """Caller identifiers cannot consume wire IDs through coercion or overflow."""
    worker.phase = "ready"
    worker.socket = socket_with()
    worker.submit(caller, b'{"operation":"sequence_begin","target":{}}')
    assert worker.failure == "caller-id"
    assert isinstance(worker.socket, Mock)
    worker.socket.write.assert_not_called()


@pytest.mark.parametrize("operation", ["scroll", "sequence_finish"])
def test_unsupported_or_premature_operation_is_terminal(
    worker: channel._Worker, operation: str
) -> None:
    """No write/finish is permitted before the fixed sequence has accepted sources."""
    worker.phase = "ready"
    worker.socket = socket_with()
    worker.submit(1, json.dumps({"operation": operation}).encode())
    assert worker.failure == "unsupported-sequence-operation"
    assert isinstance(worker.socket, Mock)
    worker.socket.write.assert_not_called()


def test_disconnected_control_requires_retirement(worker: channel._Worker) -> None:
    """Clean OS EOF alone cannot qualify an unfinished sequence."""
    worker.phase = "ready"
    worker.socket = socket_with()
    worker._disconnected()
    assert worker.eof
    assert worker.failure == "unexpected-eof"


def test_clean_retiring_control_eof_is_not_failure(worker: channel._Worker) -> None:
    """After final receipt, clean control EOF remains separate retirement evidence."""
    worker.phase = "retiring"
    worker.socket = socket_with()
    worker._disconnected()
    assert worker.eof
    assert worker.failure is None


def test_fixed_command_uses_only_attested_target(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime drift prevents launch; the matching runtime uses only fixed argv."""
    runtime = worker.runtime
    assert isinstance(runtime, Mock)
    runtime.executable = r"C:\qualified\python.exe"
    runtime.serialize.return_value = "fixed-runtime"
    launch = channel.SequenceLaunch(worker.pipe_name, worker.epoch, 7)
    monkeypatch.setattr(channel, "Path", PureWindowsPath)
    inspector = Mock(return_value=runtime)
    arguments = Mock(return_value=("-I", "-S", "-c", "fixed-bootstrap"))
    monkeypatch.setattr(channel, "inspect_runtime", inspector)
    monkeypatch.setattr(channel, "sequence_arguments", arguments)
    command = channel._build_command(runtime, launch)
    assert command.executable == runtime.executable
    assert command.directory == r"C:\qualified"
    assert command.arguments == arguments.return_value
    arguments.assert_called_once_with("fixed-runtime", launch.serialize())
    inspector.return_value = Mock()
    with pytest.raises(ValueError, match="differs"):
        channel._build_command(runtime, launch)
    arguments.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_kernel_pid_adapter_uses_actual_out_parameter(
    monkeypatch: pytest.MonkeyPatch, *, success: bool
) -> None:
    """The production kernel adapter reads ctypes PID output or propagates failure."""
    kernel = Mock()

    def identify(descriptor: int, out: object) -> bool:
        assert descriptor == 77
        cast(Any, out)._obj.value = 42
        return success

    kernel.GetNamedPipeClientProcessId.side_effect = identify
    monkeypatch.setattr(
        channel.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False
    )
    monkeypatch.setattr(channel.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        channel.ctypes,
        "WinError",
        lambda code: OSError(code, "kernel failure"),
        raising=False,
    )
    if success:
        assert channel._peer_pid(77) == 42
    else:
        with pytest.raises(OSError, match="kernel failure"):
            channel._peer_pid(77)
    kernel.GetNamedPipeClientProcessId.assert_called_once()


@pytest.mark.parametrize("raw", [b"", b"x" * 16385])
def test_payload_rejects_before_wire_allocation(raw: bytes) -> None:
    """Caller byte admission rejects empty and oversized payloads before framing."""
    with pytest.raises(ValueError, match="bounded immutable"):
        channel._payload(raw)


@pytest.mark.parametrize("scenario", ["pending", "exhausted", "wrong_id", "wrong_kind"])
def test_request_flight_rejects_invalid_transition(scenario: str) -> None:
    """One outstanding request and reserved finish IDs cannot be bypassed."""
    flight = channel._Flight()
    if scenario == "exhausted":
        flight.completed = 32
    else:
        flight.begin({"operation": "sequence_begin"})
    if scenario in {"pending", "exhausted"}:
        with pytest.raises(ProtocolError):
            flight.begin({})
    else:
        message = Message("request" if scenario == "wrong_kind" else "response", 2, {})
        with pytest.raises(ProtocolError):
            flight.accept(message)


@pytest.mark.parametrize(
    "scenario",
    [
        "invalid_epoch",
        "invalid_frame",
        "duplicate_metadata",
        "unpaired_body",
        "unpaired_eof",
    ],
)
def test_pairing_invalid_evidence_is_irreversible(scenario: str) -> None:
    """Missing or duplicate observation evidence never yields a reusable pairing."""
    candidate, proof = frame(1)
    if scenario in {"invalid_epoch", "invalid_frame"}:
        with pytest.raises(ProtocolError, match="invalid-frame-pairing-identity"):
            channel.SequencePairing(
                proof.observation.target,
                "0" * 32 if scenario == "invalid_epoch" else "a" * 32,
                2,
                9 if scenario == "invalid_frame" else 1,
                lambda: None,
            )
        return
    pairing = channel.SequencePairing(
        proof.observation.target, "a" * 32, 2, 1, lambda: None
    )
    if scenario == "duplicate_metadata":
        pairing.metadata(candidate.observation)
        with pytest.raises(ProtocolError):
            pairing.metadata(candidate.observation)
    elif scenario == "unpaired_body":
        with pytest.raises(ProtocolError):
            pairing.feed(candidate.rgb)
    else:
        with pytest.raises(ProtocolError):
            pairing.eof()
    assert pairing.terminal
    with pytest.raises(ProtocolError):
        pairing.metadata(candidate.observation)


@pytest.mark.parametrize(
    "scenario", ["not_begun", "frame_id", "missing_clock", "listen"]
)
def test_frame_request_admission_fails_before_send(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Invalid sequence state cannot emit an observe request after partial setup."""
    worker.sequence_begun = scenario != "not_begun"
    worker.sequence_target = ObserveTarget(7, 1234, 99, (11, 21))
    worker.phase = "ready"
    worker.socket = socket_with()
    payload: dict[str, object] = {
        "operation": "observe",
        "frame_id": 2 if scenario == "frame_id" else 1,
        "requested_ns": 1_000_000_000,
        "deadline_ns": 3_000_000_000,
    }
    if scenario == "missing_clock":
        worker.sequence_clock = None
    server = Mock()
    server.return_value.listen.return_value = False
    monkeypatch.setattr(channel, "QLocalServer", server)
    worker.submit(1, json.dumps(payload).encode())
    assert worker.failure is not None
    worker.socket.write.assert_not_called()
    if scenario != "listen":
        server.assert_not_called()


@pytest.mark.parametrize(
    "scenario", ["busy", "no_connection", "duplicate_begin", "write_failed"]
)
def test_request_send_failures_never_retry(
    worker: channel._Worker, scenario: str
) -> None:
    """Partial setup or a failed command write invalidates instead of replaying."""
    worker.phase = "busy" if scenario == "busy" else "ready"
    socket = socket_with()
    worker.socket = None if scenario == "no_connection" else socket
    worker.sequence_begun = scenario == "duplicate_begin"
    if scenario == "write_failed":
        socket.write.side_effect = lambda _data: -1
    target = ObserveTarget(7, 1234, 99, (11, 21))
    raw = json.dumps(
        {"operation": "sequence_begin", "target": json.loads(target.serialize())}
    ).encode()
    worker.submit(1, raw)
    assert worker.failure is not None
    if scenario == "write_failed":
        socket.write.assert_called_once()
    else:
        socket.write.assert_not_called()
    worker.submit(2, raw)
    assert socket.write.call_count <= 1


def test_finish_while_busy_fails_without_request(worker: channel._Worker) -> None:
    """Finish cannot steal the outstanding observation's reserved response slot."""
    worker.phase = "busy"
    worker.socket = socket_with()
    worker.finish()
    assert worker.failure == "finish-while-busy"
    worker.socket.write.assert_not_called()


def test_actual_begin_readable_delivers_only_validated_reply(
    worker: channel._Worker,
) -> None:
    """A wire response produces the caller reply after the full batch is accepted."""
    worker.phase = "busy"
    worker.flight.pending = 1
    worker.caller_id = 5
    payload: dict[str, object] = {
        "operation": "sequence_begin",
        "sequence_schema": 1,
        "epoch": worker.epoch,
        "max_frames": 8,
        "deadline_ns": worker.sequence_deadline_ns,
    }
    worker.socket = socket_with(encode(Message("response", 1, payload)))
    replies = []
    worker.reply.connect(
        lambda generation, caller, raw: replies.append(
            (generation, caller, json.loads(raw))
        )
    )
    worker._readable()
    assert replies == [(1, 5, payload)]
    assert worker.pending_reply is None
    assert worker.sequence_begun


@pytest.mark.parametrize(
    "scenario", ["unsolicited", "wait", "duplicate_ready", "no_helper"]
)
def test_control_semantics_reject_unexpected_records(
    worker: channel._Worker, scenario: str
) -> None:
    """Valid framing cannot bypass phase, outstanding transfer or identity gates."""
    if scenario == "unsolicited":
        worker.phase = "ready"
        message = Message("response", 1, {})
    elif scenario == "wait":
        worker.phase = "busy"
        message = Message("frame_wait", 2, {})
    elif scenario == "duplicate_ready":
        worker.verified_identity = b"verified"
        message = ready(worker)
    else:
        worker.helper = None
        message = ready(worker)
    with pytest.raises(ProtocolError):
        worker._message(message)


def test_header_first_metadata_batch_announces_credit_once(
    worker: channel._Worker,
) -> None:
    """Actual metadata parsing resumes a header-paused pipe without premature pixels."""
    candidate, proof = frame(1)
    worker.phase = "busy"
    worker.frame_id = 1
    worker.flight.pending = 2
    worker.observe_pairing = channel.SequencePairing(
        proof.observation.target, worker.epoch, 2, 1, worker._admit
    )
    worker.frame_socket = socket_with(candidate.header.encode())
    notices = []
    worker.credit_required.connect(
        lambda generation, raw: notices.append((generation, raw))
    )
    worker._frame_readable()
    assert worker.frame_header_read == 128
    assert worker.pixel_sink is None
    assert notices == []
    observation = Message(
        "response",
        2,
        {
            "operation": "observe",
            "sequence_schema": 1,
            "epoch": worker.epoch,
            "frame_id": 1,
            "observation": json.loads(candidate.observation),
        },
    )
    token = CreditToken.for_chunk(worker.epoch, 2, 1, len(candidate.rgb), 0)
    wait = Message("frame_wait", 2, token.payload())
    worker.socket = socket_with(encode(observation) + encode(wait))
    worker._readable()
    assert worker.failure is None
    assert worker.observe_metadata == proof.observation
    assert notices == [(1, encode(wait))]
    assert worker.pixel_sink is not None
    assert worker.credits is not None
    assert worker.credits.snapshot().granted_bytes == 0
    worker._notify_credit()
    assert len(notices) == 1


@pytest.mark.parametrize("body", [False, True])
def test_short_binary_read_does_not_spin(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, *, body: bool
) -> None:
    """A zero-progress Qt read yields rather than reading forever in one turn."""
    candidate, proof = frame(1)
    worker.phase = "busy"
    worker.frame_id = 1
    worker.observe_pairing = channel.SequencePairing(
        proof.observation.target, worker.epoch, 2, 1, worker._admit
    )
    worker.observe_pairing.metadata(candidate.observation)
    socket = socket_with(candidate.rgb if body else candidate.header.encode())
    socket.read.side_effect = lambda _size: QByteArray()
    worker.frame_socket = socket
    timer = Mock()
    monkeypatch.setattr(channel, "QTimer", timer)
    if body:
        worker.observe_pairing.header(candidate.header.encode())
        worker.frame_header_read = 128
        worker.credits = ParentCredit(
            worker.epoch, 2, 1, len(candidate.rgb), worker.credit_gate
        )
        token = CreditToken.for_chunk(worker.epoch, 2, 1, len(candidate.rgb), 0)
        worker.credits.receive_wait(token)
        worker.credits.reserve_grant(token)
    worker._frame_readable()
    assert worker.failure is None
    socket.read.assert_called_once()
    assert worker.frame_bytes == 0
    assert worker.pixel_sink is None
    if body:
        timer.singleShot.assert_called_once()
    else:
        timer.singleShot.assert_not_called()


def test_empty_control_read_fails_buffer_progress(worker: channel._Worker) -> None:
    """A nonempty buffer returning zero bytes does not busy-loop or authorize ready."""
    socket = socket_with(b"x")
    socket.read.side_effect = lambda _size: QByteArray()
    worker.socket = socket
    worker._readable()
    assert worker.failure == "buffer-limit"
    socket.read.assert_called_once()


def test_current_generation_connection_and_resume_callbacks(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only current serial callbacks operate on the retained binary endpoint."""
    worker.phase = "busy"
    worker.observe_pairing = channel.SequencePairing(
        ObserveTarget(7, 1234, 99, (11, 21)), worker.epoch, 2, 1, worker._admit
    )
    worker.frame_server = Mock()
    worker.frame_server.nextPendingConnection.return_value = socket_with()
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 42)
    worker._accept_frame_connection(worker.frame_serial)
    assert worker.frame_socket is not None
    assert worker.failure is None
    worker.frame_drain_queued = True
    worker._resume_current_frame(worker.frame_serial)
    assert not worker.frame_drain_queued
    assert worker.failure is None
