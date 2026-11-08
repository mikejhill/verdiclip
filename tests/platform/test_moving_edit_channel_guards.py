"""Actual moving-channel rejection and disposal paths on portable transport seams."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtNetwork import QLocalSocket
from tests.capture.moving_wire_fixtures import make_begin, make_observation
from tests.platform.test_moving_edit_channel_lifecycle import Bridge
from tests.platform.test_moving_edit_channel_lifecycle import bridge as _bridge_fixture
from tests.platform.test_sequence_channel import MemorySocket

from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.capture.moving_edit_frame import FrameSink, FrameState
from verdiclip.platform import moving_edit_channel as channel
from verdiclip.platform import moving_edit_protocol as wire

bridge = _bridge_fixture


def paired(bridge: Bridge, *, complete: bool = True) -> channel.SequencePairing:
    """Use the extracted real observation parser and header pairing, without RGB."""
    observation = make_observation(
        image_sha256=hashlib.sha256(bytes(610827)).hexdigest()
    )
    value = channel.SequencePairing(
        observation.target, bridge.worker.epoch, 3, 1, bridge.worker._admit
    )
    if complete:
        value.metadata(observation.canonical)
        value.header(
            FrameHeader(
                observation.frame_identity(),
                bytes.fromhex(observation.value["image"]["sha256"]),
            ).encode()
        )
    return value


def frame_socket(bridge: Bridge, raw: bytes = b"") -> MemorySocket:
    """Attach only a fake byte endpoint to the actual worker."""
    value = MemorySocket(raw)
    bridge.worker.frame_socket = cast("QLocalSocket", cast("object", value))
    return value


@pytest.mark.parametrize(
    "payload",
    [b"", bytearray(b"{}"), None, b"x" * (wire.MAX_MESSAGE_BYTES + 1)],
    ids=["empty", "mutable", "none", "oversized"],
)
def test_control_payload_requires_bounded_immutable_bytes(
    payload: object, request: pytest.FixtureRequest
) -> None:
    """Invalid control storage cannot reach a wire command."""
    assert len(request.node.nodeid) < 200
    with pytest.raises(ValueError, match="bounded immutable bytes"):
        channel._payload(cast("bytes", payload))


@pytest.mark.parametrize("completed", [0, wire.MAX_REQUESTS])
def test_flight_cannot_overlap_or_exceed_command_limit(completed: int) -> None:
    """A pending or exhausted flight never allocates another request ID."""
    flight = channel._Flight()
    flight.completed = completed
    if not completed:
        flight.begin({"operation": "moving_begin"})
    before = flight.pending
    with pytest.raises(wire.ProtocolError, match="single-flight"):
        flight.begin({"operation": "moving_begin"})
    assert flight.pending == before


@pytest.mark.parametrize("fault", ["target", "epoch", "request", "frame"])
def test_pairing_identity_rejects_before_any_payload(
    bridge: Bridge, fault: str
) -> None:
    """Bad immutable identities cannot create a stream."""
    observation = make_observation()
    args = [observation.target, bridge.worker.epoch, 3, 1]
    args[{"target": 0, "epoch": 1, "request": 2, "frame": 3}[fault]] = {
        "target": None,
        "epoch": "0" * 32,
        "request": True,
        "frame": 8,
    }[fault]
    with pytest.raises(wire.ProtocolError, match="invalid-frame-pairing-identity"):
        channel.SequencePairing(
            cast("channel.ObserveTarget", args[0]),
            cast("str", args[1]),
            cast("int", args[2]),
            cast("int", args[3]),
            lambda: None,
        )


@pytest.mark.parametrize(
    "operation",
    [
        "feed",
        "eof",
        "header-empty",
        "header-large",
        "header-mutable",
        "metadata-twice",
        "pair-twice",
    ],
)
def test_pairing_rejection_is_permanent(bridge: Bridge, operation: str) -> None:
    """A malformed or duplicate phase cannot be retried into a valid frame."""
    value = paired(bridge, complete=operation.endswith("twice"))

    def reject() -> None:
        if operation == "feed":
            value.feed(b"x")
        elif operation == "eof":
            value.eof()
        elif operation.startswith("header"):
            raw = {
                "header-empty": b"",
                "header-large": bytes(channel.FRAME_HEADER_BYTES + 1),
                "header-mutable": bytearray(b"x"),
            }[operation]
            value.header(cast("bytes", raw))
        elif operation == "metadata-twice":
            assert value.observation is not None
            value.metadata(value.observation.canonical)
        else:
            value.header(b"x")

    with pytest.raises(wire.ProtocolError):
        reject()
    assert value.terminal
    with pytest.raises(wire.ProtocolError, match="terminal-frame-pairing"):
        value.feed(b"x")


def test_duplicate_pair_is_rejected_without_replacing_stream(bridge: Bridge) -> None:
    """Already paired bytes cannot silently replace their retained stream."""
    value = paired(bridge)
    stream = value.stream
    with pytest.raises(wire.ProtocolError, match="duplicate-frame-pairing"):
        value._pair()
    assert value.stream is stream


@pytest.mark.parametrize(
    "fault", ["missing-clock", "nan", "infinite", "backward", "boolean"]
)
def test_worker_admission_rejects_invalid_clock(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Original clocks are required before any dispatch, even with valid bytes."""
    worker = bridge.worker
    if fault == "missing-clock":
        worker.sequence_clock = None
    else:
        worker.last_clock = 1.0
        monkeypatch.setattr(
            channel.time,
            "monotonic",
            lambda: {
                "nan": float("nan"),
                "infinite": float("inf"),
                "backward": 0.5,
                "boolean": True,
            }[fault],
        )
    with pytest.raises(
        wire.ProtocolError, match=r"missing-sequence-clock|invalid-monotonic-clock"
    ):
        worker._admit()
    assert not bridge.socket.writes


@pytest.mark.parametrize(
    "fault",
    [
        "missing-launch",
        "pending-context",
        "missing-completion",
        "missing-client",
        "missing-retirement",
    ],
)
def test_worker_evidence_state_cannot_grant_invalid_transition(
    bridge: Bridge, fault: str
) -> None:
    """Invalid contexts fail before either evidence or command bytes are sent."""
    worker = bridge.worker
    packet = wire.Message("request", 1, make_begin())
    if fault == "missing-launch":
        worker.evidence_launch = None
    elif fault in ("pending-context", "missing-retirement"):
        bridge.facade.begin(
            1, make_observation().target, make_begin()["profile_evidence"]
        )
    elif fault == "missing-client":
        worker._close_evidence()
    before = (len(bridge.socket.writes), len(bridge.evidence.parent_packets))

    def reject() -> None:
        if fault in ("missing-launch", "pending-context"):
            worker._evidence_begin(packet, wire.encode(packet))
        elif fault == "missing-retirement":
            worker._evidence_complete(retirement_started_ns=bridge.now[0])
        else:
            worker._evidence_complete()

    with pytest.raises(wire.ProtocolError, match="evidence"):
        reject()
    assert (len(bridge.socket.writes), len(bridge.evidence.parent_packets)) == before


@pytest.mark.parametrize("prior", [None, "original-close-error"])
def test_evidence_close_failure_preserves_prior_cleanup_reason(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, prior: str | None
) -> None:
    """The actual endpoint closes before its injected OS-close failure is reported."""
    worker = bridge.worker
    assert worker.evidence_client is not None
    close = worker.evidence_client.close

    def failing_close() -> None:
        close()
        raise OSError("new-close-error")

    monkeypatch.setattr(worker.evidence_client, "close", failing_close)
    worker.cleanup_failure = prior
    worker._close_evidence()
    assert worker.evidence_client is None
    assert worker.cleanup_failure == (prior or "new-close-error")
    assert all(
        endpoint.closed for endpoint in bridge.evidence.endpoints if endpoint.parent
    )


@pytest.mark.parametrize(
    "fault", ["missing-client", "open-context", "missing-deadline"]
)
def test_incomplete_evidence_retirement_cannot_publish(
    bridge: Bridge, fault: str
) -> None:
    """Retirement requires the exact existing endpoint, context and original stamp."""
    worker = bridge.worker
    if fault == "open-context":
        bridge.facade.begin(
            1, make_observation().target, make_begin()["profile_evidence"]
        )
    elif fault == "missing-client":
        worker._close_evidence()
    worker.finish_requested = True
    worker.evidence_retirement_deadline_ns = (
        None if fault == "missing-deadline" else bridge.now[0] + 5_000_000_000
    )
    worker._retire_evidence()
    assert worker.failure == "incomplete-evidence-retirement"
    assert worker.evidence_client is None


def test_discard_attempts_every_owner_and_preserves_first_failure(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sink, collector and vault disposal continue after independent failures."""
    worker = bridge.worker
    observation = make_observation()
    assert worker.collector is not None
    sink = FrameSink(
        observation.frame_identity(), worker._admit, slots=worker.collector.slots
    )
    worker.pixel_sink = sink
    calls: list[str] = []
    with monkeypatch.context() as failures:
        for owner, name in (
            (sink, "discard"),
            (worker.collector, "discard"),
            (worker.vault, "invalidate"),
        ):
            original = getattr(owner, name)
            label = type(owner).__name__

            def dispose(
                original: Callable[[], None] = original, label: str = label
            ) -> None:
                calls.append(label)
                original()
                raise OSError(label)

            failures.setattr(owner, name, dispose)
        with pytest.raises(OSError, match="FrameSink"):
            worker._discard_pixels()
    assert calls == ["FrameSink", "SequenceCollector", "SequenceVault"]
    assert worker.pixel_sink is worker.collector is worker.sequence_candidate is None
    assert worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize(
    "fault",
    ["none", "bytes", "unexpected-eof", "finish-eof", "after-eof", "empty-after-eof"],
)
def test_inert_frame_requires_quiet_socket_and_permitted_eof(
    bridge: Bridge, fault: str
) -> None:
    """No pairing means no unsolicited bytes or ordinary EOF can be accepted."""
    worker = bridge.worker
    if fault != "none":
        frame_socket(bridge, b"x" if fault in ("bytes", "after-eof") else b"")
    worker.frame_disconnected = fault in ("unexpected-eof", "finish-eof")
    worker.finish_requested = fault == "finish-eof"
    if fault in ("after-eof", "empty-after-eof"):
        worker.observe_pairing = paired(bridge)
        worker.frame_eof = True
    if fault in ("finish-eof", "empty-after-eof"):
        assert worker._inert_frame()
        assert worker.frame_eof
        worker._frame_readable()
        assert worker.failure is None
    else:
        with pytest.raises(wire.ProtocolError):
            worker._inert_frame()
    assert not bridge.socket.writes


def test_frame_queue_is_single_and_stale_notifications_are_inert(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued drain keeps the original serial and cannot drain a later frame."""
    worker = bridge.worker
    frame_socket(bridge)
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(
        channel.QTimer,
        "singleShot",
        lambda _delay, _owner, callback: callbacks.append(callback),
    )
    worker._queue_frame()
    worker._queue_frame()
    assert len(callbacks) == 1
    worker.frame_serial += 1
    callbacks[0]()
    assert worker.frame_drain_queued
    worker._resume_frame()
    assert not worker.frame_drain_queued
    worker._queue_frame()
    callbacks[1]()
    assert not worker.frame_drain_queued
    worker._frame_event(worker.frame_serial, disconnected=False)
    worker.finish_requested = True
    worker._frame_event(worker.frame_serial, disconnected=True)
    assert worker.frame_eof


def test_retired_frame_connection_permanently_rejects(bridge: Bridge) -> None:
    """An old server's delayed connection cannot attach to the new frame."""
    bridge.worker._accept_frame_connection(bridge.worker.frame_serial - 1)
    assert bridge.worker.failure == "connection-on-retired-frame"
    assert bridge.worker.vault.state is FrameState.INVALID


def test_close_frame_rejects_surplus_before_releasing_server(bridge: Bridge) -> None:
    """An extra pending connection is evidence of a protocol failure, not cleanup."""
    server = Mock()
    server.hasPendingConnections.return_value = True
    bridge.worker.frame_server = server
    with pytest.raises(wire.ProtocolError, match="surplus-frame-connection"):
        bridge.worker._close_frame()
    server.close.assert_not_called()
    server.hasPendingConnections.return_value = False
    bridge.worker._close_frame()
    server.close.assert_called_once()
    bridge.worker._close_frame()


@pytest.mark.parametrize("operation", ["drain", "finish", "reserve"])
def test_missing_owned_frame_state_rejects(bridge: Bridge, operation: str) -> None:
    """Absent native-frame owners never silently complete a transfer."""
    worker = bridge.worker
    if operation == "reserve":
        worker.collector = None

    def reject() -> None:
        if operation == "reserve":
            worker._reserve_pixels(make_observation().frame_identity())
        else:
            getattr(worker, "_" + operation + "_frame")()

    with pytest.raises(wire.ProtocolError, match="missing"):
        reject()


@pytest.mark.parametrize(
    "fault", ["queued-body", "unpaired-body", "truncated", "missing-sink"]
)
def test_frame_finish_never_accepts_unproven_bytes(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Actual frame state distinguishes waiting, truncation and missing owners."""
    worker = bridge.worker
    frame_socket(bridge, b"x" if fault.endswith("body") else b"")
    worker.observe_pairing = paired(
        bridge, complete=fault in ("queued-body", "missing-sink")
    )
    worker.frame_disconnected = not fault.endswith("body")
    queued = Mock()
    monkeypatch.setattr(channel.QTimer, "singleShot", queued)
    if fault.endswith("body"):
        worker._finish_frame()
        assert worker.frame_drain_queued is (fault == "queued-body")
    else:
        with pytest.raises(wire.ProtocolError, match=r"truncated|missing-retained"):
            worker._finish_frame()
    assert not worker.proofs
    assert not bridge.socket.writes


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "other"},
        {"operation": "moving_finish"},
        {"operation": "moving_begin"},
        {"operation": "move_next"},
    ],
)
def test_invalid_worker_commands_dispatch_nothing(
    bridge: Bridge, payload: dict[str, object]
) -> None:
    """Missing setup and unsupported commands cannot create evidence or native work."""
    with pytest.raises(wire.ProtocolError):
        bridge.worker._send(payload)
    assert not bridge.socket.writes
    assert not bridge.evidence.parent_packets


@pytest.mark.parametrize(
    "fault",
    ["socket", "busy", "exhausted", "finish-busy", "ready-identity", "duplicate-ready"],
)
def test_worker_transition_guards_fail_closed(bridge: Bridge, fault: str) -> None:
    """State transitions require the actual prerequisite owner and original flight."""
    worker = bridge.worker
    if fault == "socket":
        worker.socket = None
        with pytest.raises(wire.ProtocolError, match="no-connection"):
            worker._send(make_begin())
    elif fault in ("busy", "exhausted"):
        worker.phase = "busy" if fault == "busy" else "ready"
        worker.flight.completed = wire.MAX_REQUESTS - 1 if fault == "exhausted" else 0
        worker.submit(1, wire.canonical_json(make_begin()))
        assert worker.failure == "single-flight"
    elif fault == "finish-busy":
        worker.phase = "busy"
        worker.finish()
        assert worker.failure == "finish-while-busy"
    elif fault == "ready-identity":
        with pytest.raises(wire.ProtocolError, match="missing-verified-identity"):
            worker._notify_ready()
    else:
        worker.verified_identity = b"already-verified"
        with pytest.raises(wire.ProtocolError, match="duplicate-ready"):
            worker._ready_message(wire.Message("ready", 0, {}))
    assert not bridge.socket.writes


@pytest.mark.parametrize("raw", [b"", b"x"])
def test_control_reader_empty_read_and_record_cap_do_not_loop(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    """Malformed endpoint progress or an exhausted record cap stops before dispatch."""
    bridge.socket.raw = b"x"
    if raw:
        bridge.worker.budget.records = wire.MAX_SEQUENCE_CONTROL_RECORDS
    else:
        monkeypatch.setattr(bridge.socket, "read", lambda _size: QByteArray())
    bridge.worker._readable()
    assert bridge.worker.failure == (
        "sequence-record-bound-before-read" if raw else "buffer-limit"
    )
    assert not bridge.socket.writes


@pytest.mark.parametrize(
    "fault", ["no-helper", "poll-error", "observe-expired", "cancelled", "done"]
)
def test_poll_retains_failure_and_terminal_ownership(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Timer polling cannot admit a missing helper or conceal a failed deadline."""
    worker = bridge.worker
    monkeypatch.setattr(worker, "_terminal", worker._terminal_impl)
    if fault == "no-helper":
        worker.helper = None
    elif fault == "poll-error":
        cast("Mock", worker.helper).poll.side_effect = OSError("native-poll-error")
    elif fault == "observe-expired":
        worker.observe_deadline_ns = bridge.now[0]
    elif fault == "cancelled":
        worker.phase = "retiring"
        worker.cancelled.set()
    else:
        worker.done = True
    worker._poll()
    if fault == "no-helper":
        assert worker.done
        assert json.loads(bridge.receipts[0])["not_launched"]
    elif fault == "done":
        assert not bridge.receipts
    else:
        assert (
            worker.failure
            == {
                "poll-error": "native-poll-error",
                "observe-expired": "operation-timeout",
                "cancelled": "cancelled",
            }[fault]
        )
        assert worker.phase == "retiring"


@pytest.mark.parametrize("primary", [False, True])
@pytest.mark.parametrize("cleanup", ["delete", "quit"])
@pytest.mark.parametrize("invalidation", [False, True])
def test_terminal_attempts_final_cleanup_and_keeps_primary_error(
    bridge: Bridge,
    monkeypatch: pytest.MonkeyPatch,
    *,
    primary: bool,
    cleanup: str,
    invalidation: bool,
) -> None:
    """Real terminal processing owns nested cleanup failures without false success."""
    worker = bridge.worker
    thread = Mock()
    with monkeypatch.context() as failures:
        failures.setattr(channel.QThread, "currentThread", lambda: thread)
        failures.setattr(
            worker,
            "deleteLater",
            Mock(side_effect=OSError("delete-error") if cleanup == "delete" else None),
        )
        thread.quit.side_effect = OSError("quit-error") if cleanup == "quit" else None
        if primary:
            failures.setattr(
                channel, "_serialized", Mock(side_effect=ValueError("receipt-error"))
            )
        if invalidation:
            invalidate = worker.vault.invalidate
            calls = [0]

            def fail_invalidation() -> None:
                calls[0] += 1
                invalidate()
                # Let normal terminal disposal run; fail the cleanup boundary.
                if calls[0] >= (1 if primary else 2):
                    raise OSError("invalidation-error")

            failures.setattr(worker.vault, "invalidate", fail_invalidation)
        with pytest.raises(
            (OSError, ValueError),
            match="receipt-error" if primary else cleanup + "-error",
        ) as error:
            worker._terminal()
        if primary:
            assert any(
                "deferred deletion" in note or "thread quit" in note
                for note in error.value.__notes__
            )
    thread.quit.assert_called_once()
    assert worker.vault.state is FrameState.INVALID


def test_terminal_closes_all_present_resources_without_qualified_result(
    bridge: Bridge,
) -> None:
    """Even an unfinished sequence releases sink, timers and both transport servers."""
    worker = bridge.worker
    assert worker.collector is not None
    worker.pixel_sink = FrameSink(
        make_observation().frame_identity(), worker._admit, slots=worker.collector.slots
    )
    worker.timer = Mock()
    worker.server = Mock()
    worker.frame_server = Mock()
    endpoint = frame_socket(bridge)
    worker._terminal_impl()
    assert worker.pixel_sink is None
    worker.timer.stop.assert_called_once()
    worker.server.close.assert_called_once()
    worker.frame_server.close.assert_called_once()
    assert endpoint.writes == []
    assert not json.loads(bridge.receipts[0])["passed"]
    worker._terminal_impl()
    worker._terminal()
    assert len(bridge.receipts) == 1


@pytest.mark.parametrize("fault", ["state", "repeated", "target", "caller", "profile"])
def test_public_begin_rejection_cancels_before_queue(
    bridge: Bridge, fault: str
) -> None:
    """Public immutable target/profile/caller errors are permanent, never queued."""
    facade = bridge.facade
    if fault == "state":
        facade._state = "busy"
    if fault == "repeated":
        facade._sequence_begun = True
    target = None if fault == "target" else make_observation().target
    with pytest.raises((ValueError, wire.ProtocolError)):
        facade.begin(
            True if fault == "caller" else 1,
            cast("channel.ObserveTarget", target),
            {} if fault == "profile" else make_begin()["profile_evidence"],
        )
    assert facade._cancelled.is_set()
    assert not bridge.socket.writes


@pytest.mark.parametrize("operation", ["move", "observe"])
@pytest.mark.parametrize(
    "fault", ["invalid-time", "missing-stamp", "wrong-phase", "missing-deadline"]
)
def test_public_clock_rejection_never_queues_next_operation(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, operation: str, fault: str
) -> None:
    """Successful real begin/move does not excuse a broken next public clock."""
    bridge.begin()
    if operation == "observe":
        bridge.move(1)
    before = len(bridge.socket.writes)
    if fault == "invalid-time":
        monkeypatch.setattr(channel.time, "monotonic_ns", lambda: True)
    elif fault == "missing-stamp":
        monkeypatch.setattr(bridge.worker.vault, "stamp", lambda: None)
    elif fault == "wrong-phase":
        bridge.worker.vault.publish_stamp("busy", 30.0)
    else:
        bridge.facade._sequence_deadline_ns = None

    def reject() -> None:
        if operation == "move":
            bridge.facade.move_next(2)
        else:
            bridge.facade.observe(3)

    with pytest.raises(wire.ProtocolError):
        reject()
    assert len(bridge.socket.writes) == before
    assert bridge.facade._cancelled.is_set()


def test_public_finish_and_unrelated_reply_are_inert(bridge: Bridge) -> None:
    """Finishing early or a stale callback cannot mutate the active operation."""
    with pytest.raises(RuntimeError, match="not ready to finish"):
        bridge.facade.finish()
    before = bridge.facade._state
    bridge.facade._reply(99, 99, b"invalid-json")
    assert bridge.facade._state == before
    assert not bridge.socket.writes


def test_missing_public_vault_cannot_accept_claimed_success(bridge: Bridge) -> None:
    """A foreign success claim is rejected against actual missing retained ownership."""
    facade = bridge.facade
    facade._vault = None
    facade._observe_consumed = True
    facade._retired(1, b'{"passed":true}')
    assert facade._terminal_receipt is not None
    assert json.loads(facade._terminal_receipt) == {
        "passed": False,
        "failure": "retained-frame-unavailable",
    }
    with pytest.raises(RuntimeError, match="No qualified retained frame"):
        facade.take_sequence_result(1)
    facade._retired(99, b"invalid-json")


@pytest.mark.parametrize(
    "fault", ["not-begun", "payload", "capacity", "move", "step", "clock", "listen"]
)
def test_actual_move_does_not_authorize_invalid_frame_request(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A real begin/move retains exact frame identity, capacity and server guards."""
    bridge.begin()
    bridge.move(1)
    pending: list[bytes] = []
    bridge.facade._request.disconnect(bridge.worker.submit)
    bridge.facade._request.connect(lambda _caller, raw: pending.append(raw))
    bridge.facade.observe(3)
    assert len(pending) == 1
    payload = json.loads(pending[0])
    worker = bridge.worker
    if fault == "not-begun":
        worker.sequence_begun = False
    elif fault == "payload":
        payload["extra"] = True
    elif fault == "capacity":
        worker.collector = None
    elif fault == "move":
        worker.move_ack = None
    elif fault == "step":
        payload["step_id"] = 2
    elif fault == "clock":
        worker.sequence_clock = None
    else:
        server = Mock()
        server.listen.return_value = False
        monkeypatch.setattr(channel, "QLocalServer", Mock(return_value=server))
    before = len(bridge.socket.writes)
    with pytest.raises(
        wire.ProtocolError,
        match={
            "not-begun": "sequence-not-ready",
            "payload": "invalid-frame-request",
            "capacity": "sequence-capacity",
            "move": "missing-matching-move",
            "step": "frame-before-move",
            "clock": "missing-sequence-clock",
            "listen": "frame-listen-failed",
        }[fault],
    ):
        worker._begin_observe(payload)
    assert len(bridge.socket.writes) == before
    assert worker.frame_socket is None
    assert worker.sequence_candidate is None


def active_credit(bridge: Bridge) -> channel.CreditToken:
    """Announce a real first token without granting it or constructing a result."""
    worker = bridge.worker
    worker.phase = "busy"
    worker.credits = channel.ParentCredit(
        worker.epoch, 3, 1, 610827, worker.credit_gate
    )
    token = channel.CreditToken.for_chunk(worker.epoch, 3, 1, 610827, 0)
    worker.credits.receive_wait(token)
    worker.credit_notified = token
    return token


@pytest.mark.parametrize("fault", ["phase", "command", "unannounced", "write"])
def test_credit_guard_failure_cancels_actual_grant_state(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No invalid or partially written grant can be retried as body permission."""
    worker = bridge.worker
    token = active_credit(bridge)
    message = wire.Message("frame_credit", token.request_id, token.payload())
    if fault == "phase":
        worker.phase = "ready"
    elif fault == "command":
        message = wire.Message("response", 3, {})
    elif fault == "unannounced":
        worker.credit_notified = None
    else:
        monkeypatch.setattr(bridge.socket, "write", lambda _raw: 0)
    worker.grant_credit(worker.generation, wire.encode(message))
    assert (
        worker.failure
        == {
            "phase": "invalid-credit-phase",
            "command": "invalid-credit-command",
            "unannounced": "unannounced-credit",
            "write": "credit-write-failed",
        }[fault]
    )
    assert worker.cancelled.is_set()
    assert worker.credits is not None
    assert worker.credits.snapshot().accepted_bytes == 0
    assert not bridge.socket.writes


def test_credit_notice_requires_actual_pixel_metadata(bridge: Bridge) -> None:
    """A valid wait and paired header cannot allocate a sink without metadata."""
    active_credit(bridge)
    worker = bridge.worker
    worker.credit_notified = None
    worker.observe_pairing = paired(bridge)
    with pytest.raises(wire.ProtocolError, match="missing-pixel-metadata"):
        worker._notify_credit()
    assert worker.pixel_sink is None


@pytest.mark.parametrize(
    "fault",
    [
        "header-empty",
        "await-metadata",
        "missing-credit",
        "body-empty",
        "missing-metadata",
        "allocate-sink",
    ],
)
def test_frame_drain_uses_actual_pair_credit_and_sink(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """One real credited byte distinguishes waiting, invalid state and owned storage."""
    worker = bridge.worker
    socket = frame_socket(bridge, b"\x00")
    worker.observe_pairing = paired(
        bridge, complete=fault not in ("header-empty", "await-metadata")
    )
    if fault != "header-empty":
        worker.frame_header_read = channel.FRAME_HEADER_BYTES
    if fault in ("header-empty", "body-empty"):
        monkeypatch.setattr(socket, "read", lambda _size: QByteArray())
    if fault in ("body-empty", "missing-metadata", "allocate-sink"):
        token = active_credit(bridge)
        assert worker.credits is not None
        worker.credits.reserve_grant(token)
        worker.credit_notified = None
    if fault == "allocate-sink":
        worker.observe_metadata = worker.observe_pairing.observation
    if fault in ("missing-credit", "missing-metadata"):
        with pytest.raises(
            wire.ProtocolError,
            match="missing-frame-credit"
            if fault == "missing-credit"
            else "missing-pixel-metadata",
        ):
            worker._drain_frame()
        assert worker.pixel_sink is None
    else:
        worker._drain_frame()
        assert worker.frame_bytes == (1 if fault == "allocate-sink" else 0)
        if fault == "allocate-sink":
            assert worker.pixel_sink is not None
            assert worker.credits is not None
            assert worker.credits.snapshot().accepted_bytes == 1
            assert worker.frame_first_byte_ns == bridge.now[0]
        else:
            assert worker.pixel_sink is None
    assert worker.sequence_candidate is None
    assert not worker.proofs


@pytest.mark.parametrize(
    "fault", ["unexpected-control", "missing-control-peer", "missing-frame-peer"]
)
def test_connection_notifications_require_pending_owned_endpoint(
    bridge: Bridge, fault: str
) -> None:
    """A notification cannot substitute for the actual pending socket."""
    worker = bridge.worker
    if fault == "unexpected-control":
        worker._connected()
    elif fault == "missing-control-peer":
        worker.phase = "starting"
        worker.socket = None
        worker.server = Mock()
        worker.server.nextPendingConnection.return_value = None
        worker._connected()
    else:
        worker.phase = "busy"
        worker.observe_pairing = paired(bridge)
        worker.frame_server = Mock()
        worker.frame_server.nextPendingConnection.return_value = None
        worker._frame_connected()
    assert (
        worker.failure
        == {
            "unexpected-control": "unexpected-connection",
            "missing-control-peer": "missing-owned-peer",
            "missing-frame-peer": "missing-owned-frame-peer",
        }[fault]
    )
    assert worker.vault.state is FrameState.INVALID


def test_actual_begin_partial_control_write_closes_evidence(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An acknowledged begin context cannot excuse a failed command write."""
    monkeypatch.setattr(bridge.socket, "write", lambda _raw: 0)
    bridge.facade.begin(1, make_observation().target, make_begin()["profile_evidence"])
    assert bridge.worker.failure == "write-failed"
    assert len(bridge.evidence.parent_packets) == 1
    assert bridge.worker.evidence_client is None
    assert not bridge.boundary.writes


def test_completed_move_cannot_be_repeated_without_frame_acceptance(
    bridge: Bridge,
) -> None:
    """An actual move reply does not authorize a second target write before pixels."""
    bridge.begin()
    bridge.move(1)
    request = dict(bridge.requests[-1].payload)
    with pytest.raises(wire.ProtocolError, match="move-before-accepted-frame"):
        bridge.worker._send(request)
    assert len(bridge.boundary.writes) == 1


def test_false_deadline_stamp_does_not_publish(bridge: Bridge) -> None:
    """An uninitialized stamp has no deadline authority."""
    worker = bridge.worker
    worker.deadline = 0
    worker._publish_stamp()
    worker._maybe_ready()
    assert worker.verified_identity is None
