"""Pure producer-to-reader admission and immutable raw evidence contracts."""

import hashlib
import struct
from typing import Any, cast

import pytest

from verdiclip.platform import moving_edit_protocol as wire

NS = 1_000_000_000
SESSION = "1" * 32
EPOCH = "2" * 32
DIGEST = "a" * 64


def identity() -> wire.EvidenceIdentity:
    """Provide already independently pinned identities, not name-only trust."""
    return wire.EvidenceIdentity(
        SESSION,
        EPOCH,
        NS,
        31 * NS,
        10,
        100,
        "C:\\python.exe",
        20,
        200,
        "C:\\python.exe",
    )


def setup() -> tuple[
    wire.EvidenceReaderState, wire.EvidenceProducer, wire.EvidenceProducer
]:
    """Authenticate both connections and the peer's immutable hello."""
    reader = wire.EvidenceReaderState(identity())
    reader.bind_peer(10, 100, "C:\\python.exe", NS)
    reader.bind_parent(20, 200, "C:\\python.exe", NS)
    producer = wire.EvidenceProducer(SESSION, EPOCH)
    parent = wire.EvidenceProducer(SESSION, EPOCH, parent=True)
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    send(reader, producer, wire.make_evidence_hello(launch, 10, 100), NS)
    return reader, producer, parent


def send(
    reader: wire.EvidenceReaderState,
    producer: wire.EvidenceProducer,
    event: dict[str, object],
    now: int,
) -> bytes:
    """Use the actual producer bytes and exact reader ACK, not validator doubles."""
    frame = producer.prepare(event, now, 31 * NS)
    reader.prepare_peer(frame, now)
    ack = reader.acknowledge_persisted(now)
    producer.acknowledge(ack, now)
    return frame


def context(
    reader: wire.EvidenceReaderState,
    parent: wire.EvidenceProducer,
    index: int,
    now: int,
    raw: list[bytes] | None = None,
) -> dict[str, object]:
    """Supply the independent parent record before its corresponding command."""
    operation, step = (
        ("begin", 0)
        if index == 1
        else (
            ("finish", 0)
            if index == 16
            else ("move" if index % 2 == 0 else "observe", index // 2)
        )
    )
    requested = now
    deadline = min(
        now + (3 if operation == "move" else 2) * NS,
        (31 if step == 7 or index == 16 else 23) * NS,
    )
    packet = {
        "schema": 1,
        "session": SESSION,
        "epoch": EPOCH,
        "context_id": index,
        "operation": operation,
        "request_id": index,
        "step_id": step,
        "requested_ns": requested,
        "deadline_ns": deadline,
        "command_sha256": DIGEST,
    }
    if operation == "finish":
        packet["deadline_ns"] = min(now + 5 * NS, 31 * NS)
        packet["response_deadline_ns"] = deadline
    frame = parent.prepare(packet, now, deadline)
    reader.prepare_parent(frame, now)
    if raw is not None:
        raw.append(frame)
    ack = reader.acknowledge_parent(now)
    if ack is not None:
        parent.acknowledge(ack, now)
    return packet


def phase(packet: dict[str, object], event: str, now: int) -> dict[str, object]:
    """Echo command identity while keeping independently sampled completion."""
    result = {
        key: packet[key]
        for key in (
            "context_id",
            "operation",
            "request_id",
            "step_id",
            "command_sha256",
        )
    }
    result["event"] = event
    if event == "phase_end":
        result["completed_ns"] = now
    return result


def complete(
    reader: wire.EvidenceReaderState,
    parent: wire.EvidenceProducer,
    packet: dict[str, object],
    now: int,
    raw: list[bytes] | None = None,
) -> None:
    """Finish the real parent acceptance/response commitment."""
    value = {
        key: value
        for key, value in packet.items()
        if key not in ("requested_ns", "deadline_ns", "response_deadline_ns")
    }
    value["completed_ns"] = now
    if packet["operation"] == "finish":
        value.update(
            retirement_started_ns=now, retirement_deadline_ns=min(now + 5 * NS, 31 * NS)
        )
    frame = parent.prepare(value, now, 31 * NS)
    reader.prepare_parent(frame, now)
    if raw is not None:
        raw.append(frame)
    ack = reader.acknowledge_parent(now)
    if ack is not None:
        parent.acknowledge(ack, now)


def test_launch_and_hello_round_trip_original_stamps() -> None:
    """Strict launch metadata cannot be omitted, expanded or restamped."""
    reader, _, _ = setup()
    assert reader.authenticated
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    assert wire.deserialize_launch(launch.serialize()) == launch
    launch.validate_time(NS)
    with pytest.raises(wire.ProtocolError):
        launch.validate_time(31 * NS)
    with pytest.raises(wire.ProtocolError):
        wire.MovingEditLaunch(
            "VerdiClip-moving-edit-" + SESSION,
            EPOCH,
            20,
            True,  # noqa: FBT003 - reject bool masquerading as timestamp
            31 * NS,
            SESSION,
            30,
            300,
        )
    assert len(launch.serialize()) <= 1024
    assert wire.EvidenceIdentity.from_value(identity().value) == identity()


def test_wire_actual_frame_ack_and_one_outstanding() -> None:
    """Use fixed16-byte header and fixed80-byte identity-bound digest ACK."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    send(reader, producer, phase(packet, "phase_enter", NS), NS)
    frame = producer.prepare(
        {"event": "call_start", "name": "OpenProcess", "effective_deadline_ns": 3 * NS},
        NS,
        3 * NS,
    )
    assert struct.unpack(">IIQ", frame[:16]) == (len(frame) - 16, 3, NS)
    reader.prepare_peer(frame, NS)
    assert reader.status["pending_calls"][0]["deadline_ns"] == 3 * NS
    assert producer.pending is not None
    with pytest.raises(wire.ProtocolError):
        producer.prepare({"event": "call_end", "seconds": 0.0}, NS, 3 * NS)
    # Independent producer proves that an attempted queue cannot be repaired.
    with pytest.raises(wire.ProtocolError):
        producer.acknowledge(reader.acknowledge_persisted(NS), NS)


def test_ack_exact_digest_and_late_admission() -> None:
    """A persisted start does not authorize execution after its original deadline."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    send(reader, producer, phase(packet, "phase_enter", NS), NS)
    frame = producer.prepare(
        {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
        NS,
        2 * NS,
    )
    reader.prepare_peer(frame, NS)
    ack = reader.acknowledge_persisted(NS)
    assert len(ack) == 80
    decoded = wire.EvidenceAck.decode(ack)
    assert decoded.record_sha256 == hashlib.sha256(frame).hexdigest()
    with pytest.raises(wire.ProtocolError):
        producer.acknowledge(ack, 2 * NS)
    assert producer.failed


@pytest.mark.parametrize("mutation", ["request_id", "step_id", "command_sha256"])
def test_parent_context_cannot_be_fabricated_by_peer(mutation: str) -> None:
    """Authenticated peer metadata alone does not establish phase authority."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    event = phase(packet, "phase_enter", NS)
    event[mutation] = "b" * 64 if mutation == "command_sha256" else 2
    with pytest.raises(wire.ProtocolError):
        send(reader, producer, event, NS)
    assert reader.failed or producer.failed


def test_peer_phase_end_does_not_wait_for_parent_but_next_phase_does() -> None:
    """Avoid response cycles while preventing frame acceptance bypass."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    send(reader, producer, phase(packet, "phase_enter", NS), NS)
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    assert reader.status["peer_phase_closed"]
    assert not reader.status["parent_completed"]
    with pytest.raises(wire.ProtocolError):
        context(reader, parent, 2, NS)


def test_finish_context_retains_native_cleanup_until_final_and_eof() -> None:
    """Parent response starts retirement, not a new or closed cleanup context."""
    reader, producer, parent = setup()
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    frames = [
        wire.encode_evidence_record(
            wire.EvidenceRecord(1, NS, wire.make_evidence_hello(launch, 10, 100))
        )
    ]
    parents: list[bytes] = []
    for index in range(1, 17):
        now = NS + index
        packet = context(reader, parent, index, now, parents)
        frames.append(send(reader, producer, phase(packet, "phase_enter", now), now))
        frames.extend(normal_seek(reader, producer, packet, now))
        if index == 16:
            frames.append(
                send(reader, producer, phase(packet, "finish_response_sent", now), now)
            )
            complete(reader, parent, packet, now, parents)
            frames.append(
                send(
                    reader,
                    producer,
                    {
                        "event": "call_start",
                        "name": "CloseHandle",
                        "effective_deadline_ns": now + 3 * NS,
                    },
                    now,
                )
            )
            frames.append(
                send(reader, producer, {"event": "call_end", "seconds": 0.0}, now)
            )
        frames.append(send(reader, producer, phase(packet, "phase_end", now), now))
        if index != 16:
            complete(reader, parent, packet, now, parents)
    final = producer.final_event()
    frames.append(send(reader, producer, final, NS + 17))
    assert reader.final_received
    assert not reader.complete
    reader.peer_eof(NS + 18)
    assert not reader.complete
    replay = wire.EvidenceReplay()
    data = b"".join(frames)
    for offset in range(0, len(data), 47):
        replay.feed(data[offset : offset + 47])
    replay.eof()
    reader.verify_replay(replay)
    assert not reader.complete
    reader.verify_correlated_replay(correlated_audit(parents, frames))
    assert reader.complete


@pytest.mark.parametrize(
    "fault", ["flush", "parent_closed", "timeout", "future", "backwards"]
)
def test_failure_is_latched_before_native_admission(fault: str) -> None:
    """No loss, clock error or unpersisted record can be repaired by later traffic."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    send(reader, producer, phase(packet, "phase_enter", NS), NS)
    frame = producer.prepare(
        {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
        NS,
        2 * NS,
    )
    reader.prepare_peer(frame, NS)
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - one selected failure injection
        if fault == "flush":
            reader.fail("flush-failed")
        elif fault == "parent_closed":
            reader.parent_closed()
        else:
            reader.acknowledge_persisted(
                {"timeout": 2 * NS, "future": True, "backwards": NS - 1}[fault]
            )
    assert reader.failed
    with pytest.raises(wire.ProtocolError):
        reader.check(NS)


@pytest.mark.parametrize("fault", ["truncated", "extra", "unknown", "gap", "depth"])
def test_frame_parser_rejects_malformed_bounded_input(fault: str) -> None:
    """One fixed receive buffer cannot hide a queue or unrecognized event."""
    frame = wire.encode_evidence_record(
        wire.EvidenceRecord(1, NS, {"event": "call_end", "seconds": 0.0})
    )
    decoder = wire.EvidenceDecoder()
    if fault == "truncated":
        assert decoder.feed(frame[:-1]) is None
        with pytest.raises(wire.ProtocolError):
            decoder.eof()
    elif fault == "extra":
        with pytest.raises(wire.ProtocolError):
            decoder.feed(frame + frame)
    elif fault == "unknown":
        with pytest.raises(wire.ProtocolError):
            wire.encode_evidence_record(
                wire.EvidenceRecord(1, NS, {"event": "arbitrary"})
            )
    elif fault == "gap":
        reader, _producer, _ = setup()
        data = wire.encode_evidence_record(
            wire.EvidenceRecord(8, NS, {"event": "call_end", "seconds": 0.0})
        )
        with pytest.raises(wire.ProtocolError):
            reader.prepare_peer(data, NS)
    else:
        value = b'{"event":' + b"[" * 33 + b"0" + b"]" * 33 + b"}"
        with pytest.raises(wire.ProtocolError):
            decoder.feed(struct.pack(">IIQ", len(value), 1, NS) + value)


def enter_context(
    index: int = 1,
) -> tuple[
    wire.EvidenceReaderState,
    wire.EvidenceProducer,
    wire.EvidenceProducer,
    dict[str, object],
]:
    """Advance real ordered contexts through the requested active phase."""
    reader, producer, parent = setup()
    packet: dict[str, object] = {}
    for current in range(1, index + 1):
        packet = context(reader, parent, current, NS)
        send(reader, producer, phase(packet, "phase_enter", NS), NS)
        if current != index:
            normal_seek(reader, producer, packet, NS)
            send(reader, producer, phase(packet, "phase_end", NS), NS)
            complete(reader, parent, packet, NS)
    return reader, producer, parent, packet


@pytest.mark.parametrize("role", ["peer", "parent"])
@pytest.mark.parametrize("fault", ["pid", "creation", "image", "duplicate"])
def test_exact_pinned_identity_and_duplicate_connection(role: str, fault: str) -> None:
    """Reused PIDs and matching executable basenames do not authenticate a client."""
    reader = wire.EvidenceReaderState(identity())
    bind = getattr(reader, "bind_" + role)
    pid, creation = (10, 100) if role == "peer" else (20, 200)
    executable = "C:\\python.exe"
    if fault == "duplicate":
        bind(pid, creation, executable, NS)
    elif fault == "pid":
        pid += 1
    elif fault == "creation":
        creation += 1
    else:
        executable = "D:\\python.exe"
    with pytest.raises(wire.ProtocolError):
        bind(pid, creation, executable, NS)
    assert reader.failed


@pytest.mark.parametrize(
    "field",
    [
        "sequence_started_ns",
        "sequence_deadline_ns",
        "epoch",
        "evidence_token",
        "created_100ns",
    ],
)
def test_hello_cannot_replace_outer_originals(field: str) -> None:
    """Launch and independently delivered expected identity must match byte values."""
    reader = wire.EvidenceReaderState(identity())
    reader.bind_peer(10, 100, "C:\\python.exe", NS)
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    hello = wire.make_evidence_hello(launch, 10, 100)
    hello[field] = (
        "3" * 32 if field in ("epoch", "evidence_token") else hello[field] - 1
    )
    # Re-encode untrusted canonical data directly, so decoder sees malformed stamps too.
    payload = wire.canonical_json(hello)
    frame = struct.pack(">IIQ", len(payload), 1, NS) + payload
    with pytest.raises(wire.ProtocolError):
        reader.prepare_peer(frame, NS)
    assert not reader.authenticated


@pytest.mark.parametrize("offset", [0, 4, 6, 8, 24, 40, 44, 79])
def test_ack_rejects_every_fixed_identity_and_digest_field(offset: int) -> None:
    """Magic, flags, schema, identity, record ID, digest and status are mandatory."""
    reader, producer, _parent, _ = enter_context()
    frame = producer.prepare(
        {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
        NS,
        2 * NS,
    )
    reader.prepare_peer(frame, NS)
    ack = bytearray(reader.acknowledge_persisted(NS))
    ack[offset] ^= 1
    with pytest.raises(wire.ProtocolError):
        producer.acknowledge(bytes(ack), NS)
    assert producer.failed


def test_observe_completion_before_peer_end_never_advances_early() -> None:
    """Frame acceptance and peer completion are independent, race-safe observations."""
    reader, producer, parent, packet = enter_context(3)
    complete(reader, parent, packet, NS)
    assert reader.parent_completed
    assert not reader.peer_phase_closed
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    ack = reader.acknowledge_parent(NS)
    assert ack is not None
    parent.acknowledge(ack, NS)
    context(reader, parent, 4, NS)
    assert reader.context_id == 4


@pytest.mark.parametrize("bound", ["records", "bytes", "depth"])
def test_fixed_caps_reject_before_another_ack(
    monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    """The live writer/reader cannot turn bounded raw evidence into a growing queue."""
    reader, producer, _, _ = enter_context()
    event: dict[str, object] = {
        "event": "call_start",
        "name": "COM",
        "effective_deadline_ns": 2 * NS,
    }
    if bound == "records":
        monkeypatch.setattr(wire, "MAX_EVIDENCE_RECORDS", producer.count)
    elif bound == "bytes":
        monkeypatch.setattr(wire, "MAX_EVIDENCE_BYTES", producer.byte_count)
    else:
        for _ in range(32):
            send(reader, producer, event, NS)
        assert len(reader.calls) == 32
    with pytest.raises(wire.ProtocolError):
        send(reader, producer, event, NS)
    assert reader.failed or producer.failed


def test_missing_native_end_expires_independently_of_stream_progress() -> None:
    """A live peer and idle pipe cannot suppress the pending native-call watchdog."""
    reader, producer, _, _ = enter_context()
    send(
        reader,
        producer,
        {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
        NS,
    )
    with pytest.raises(wire.ProtocolError, match="deadline"):
        reader.check(2 * NS)
    first = reader.failure
    with pytest.raises(wire.ProtocolError):
        reader.fail("later-cleanup-failure")
    assert reader.failure is first


def test_nested_call_pairing_and_three_second_ceiling() -> None:
    """All enclosing calls remain pending; a younger nested call cannot extend one."""
    reader, producer, _, _ = enter_context(2)
    for name in ("COM", "SendMessageTimeoutW"):
        send(
            reader,
            producer,
            {"event": "call_start", "name": name, "effective_deadline_ns": 4 * NS},
            NS,
        )
    send(reader, producer, {"event": "call_end", "seconds": 0.25}, NS + NS // 4)
    assert [call.name for call in reader.calls] == ["COM"]
    with pytest.raises(wire.ProtocolError):
        reader.check(4 * NS)


@pytest.mark.parametrize(
    "fault",
    [
        "unrecorded",
        "future_failure",
        "no_reserve",
        "wrong_order",
        "repeat",
        "parent_loss",
        "timeout_failure",
    ],
)
def test_restore_one_shot_admission_rejects_unsafe_paths(fault: str) -> None:
    """Require a recorded observational failure and the full original reserve."""
    reader, producer, _, _ = enter_context(3)
    if fault != "unrecorded":
        send(
            reader,
            producer,
            {
                "event": "failure",
                "context_id": 3,
                "reason": "timeout" if fault == "timeout_failure" else "observation",
            },
            NS,
        )
    entry: dict[str, object] = {
        "event": "failure_restore_enter",
        "context_id": 3,
        "sequence_started_ns": NS,
        "sequence_deadline_ns": 31 * NS,
        "failure_ns": NS + 1 if fault == "future_failure" else NS,
    }
    if fault in ("wrong_order", "repeat", "parent_loss"):
        send(reader, producer, entry, NS)
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - one selected fault injection
        if fault == "wrong_order":
            send(
                reader,
                producer,
                {"event": "failure_restore_phase", "phase_id": 2, "operation": "write"},
                NS,
            )
        elif fault == "parent_loss":
            reader.parent_closed()
        else:
            send(reader, producer, entry, 24 * NS if fault == "no_reserve" else NS)
    assert reader.failed or producer.failed


def test_restore_phase_starts_are_original_and_never_reset_by_ack() -> None:
    """Four ordered restore subphases share the one-shot failure+8s ceiling."""
    reader, producer, _, _ = enter_context(3)
    send(
        reader,
        producer,
        {"event": "failure", "context_id": 3, "reason": "observation"},
        NS,
    )
    send(
        reader,
        producer,
        {
            "event": "failure_restore_enter",
            "context_id": 3,
            "sequence_started_ns": NS,
            "sequence_deadline_ns": 31 * NS,
            "failure_ns": NS,
        },
        NS,
    )
    for phase_id, operation in enumerate(("guard", "write", "capture", "final"), 1):
        stamp = NS + phase_id
        send(
            reader,
            producer,
            {
                "event": "failure_restore_phase",
                "phase_id": phase_id,
                "operation": operation,
            },
            stamp,
        )
    assert reader.restore_deadline_ns == 9 * NS
    assert reader.restore_phase_deadline_ns == 2 * NS + 4
    with pytest.raises(wire.ProtocolError):
        reader.check(2 * NS + 4)


@pytest.mark.parametrize(
    "fault", ["missing_final", "truncated", "unpaired", "duplicate", "noncanonical"]
)
def test_replay_cannot_upgrade_bad_raw_prefix(fault: str) -> None:
    """The independent disk reread rejects incomplete or altered storage."""
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    frame = wire.encode_evidence_record(
        wire.EvidenceRecord(1, NS, wire.make_evidence_hello(launch, 10, 100))
    )
    replay = wire.EvidenceReplay()
    replay.feed(frame[:-1] if fault == "truncated" else frame)
    if fault in ("missing_final", "truncated"):
        with pytest.raises(wire.ProtocolError):
            replay.eof()
    elif fault == "duplicate":
        with pytest.raises(wire.ProtocolError):
            replay.feed(frame)
    elif fault == "noncanonical":
        payload = b'{"seconds": 0.0, "event": "call_end"}'
        with pytest.raises(wire.ProtocolError):
            replay.feed(struct.pack(">IIQ", len(payload), 2, NS) + payload)
    else:
        frame = wire.encode_evidence_record(
            wire.EvidenceRecord(2, NS, {"event": "call_end", "seconds": 0.0})
        )
        with pytest.raises(wire.ProtocolError):
            replay.feed(frame)
    assert replay.failed


def test_strict_reader_launch_and_serialized_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No caller-selected role/path or overlarge JSON can reach process creation."""
    launch = wire.EvidenceReaderLaunch(SESSION, 20, 200, 50 * NS)
    assert wire.deserialize_evidence_reader_launch(launch.serialize()) == launch
    payload = wire.parse_json(launch.serialize().encode(), 1024)
    payload["path"] = "C:\\arbitrary"
    with pytest.raises(wire.ProtocolError):
        wire.deserialize_evidence_reader_launch(wire.canonical_json(payload).decode())
    monkeypatch.setattr(wire, "MAX_LAUNCH_BYTES", 32)
    with pytest.raises(wire.ProtocolError):
        launch.serialize()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence_started_ns", True),
        ("sequence_deadline_ns", 32 * NS),
        ("evidence_reader_pid", False),
        ("evidence_reader_created_100ns", 0),
        ("evidence_schema", 2),
    ],
)
def test_launch_original_fields_reject_bool_overflow_and_widening(
    field: str, value: object
) -> None:
    """The launched new profile has no optional or backwards-compatible bypass."""
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    packet = wire.parse_json(launch.serialize().encode(), 1024)
    packet[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.deserialize_launch(wire.canonical_json(packet).decode())


def test_reader_retirement_deadline_survives_phase_end() -> None:
    """Final evidence and natural EOF remain inside the original cleanup allowance."""
    reader, producer, parent, packet = enter_context(16)
    send(reader, producer, phase(packet, "finish_response_sent", NS), NS)
    complete(reader, parent, packet, NS)
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    with pytest.raises(wire.ProtocolError):
        reader.check(6 * NS)


@pytest.mark.parametrize("fault", ["count", "bytes", "hash", "premature_eof"])
def test_final_summary_requires_the_entire_exact_raw_prefix(fault: str) -> None:
    """A clean frame terminal alone cannot replace complete raw audit closure."""
    reader, producer, parent, packet = enter_context(16)
    send(reader, producer, phase(packet, "finish_response_sent", NS), NS)
    complete(reader, parent, packet, NS)
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    event = producer.final_event()
    if fault == "premature_eof":
        with pytest.raises(wire.ProtocolError):
            reader.peer_eof(NS)
    else:
        key = {
            "count": "preceding_records",
            "bytes": "preceding_bytes",
            "hash": "preceding_sha256",
        }[fault]
        event[key] = "0" * 64 if fault == "hash" else event[key] - 1
        with pytest.raises(wire.ProtocolError):
            send(reader, producer, event, NS)
    assert reader.failed


def test_reader_rejects_untrusted_byte_overflow_without_using_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sides enforce the4MB cap independently, not just the friendly writer."""
    reader, _, _, _ = enter_context()
    frame = wire.encode_evidence_record(
        wire.EvidenceRecord(
            reader.count + 1,
            NS,
            {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
        )
    )
    monkeypatch.setattr(wire, "MAX_EVIDENCE_BYTES", reader.byte_count + len(frame) - 1)
    with pytest.raises(wire.ProtocolError, match="byte-bound"):
        reader.prepare_peer(frame, NS)
    assert reader.pending_peer is None


def test_restore_requires_full_reserve_at_actual_reader_admission() -> None:
    """Valid step7 failure cannot reserve8s after the full reserve has elapsed."""
    reader, producer, parent = setup()
    for index in range(1, 15):
        packet = context(reader, parent, index, NS)
        send(reader, producer, phase(packet, "phase_enter", NS), NS)
        normal_seek(reader, producer, packet, NS)
        send(reader, producer, phase(packet, "phase_end", NS), NS)
        complete(reader, parent, packet, NS)
    # Step7 may run in the reserved final8s. Its own observation deadline has
    # not expired, but a separate autonomous restore needs the full reserve.
    packet = context(reader, parent, 15, 22 * NS)
    send(reader, producer, phase(packet, "phase_enter", 22 * NS), 22 * NS)
    send(
        reader,
        producer,
        {"event": "failure", "context_id": 15, "reason": "observation"},
        22 * NS,
    )
    entry = {
        "event": "failure_restore_enter",
        "context_id": 15,
        "sequence_started_ns": NS,
        "sequence_deadline_ns": 31 * NS,
        "failure_ns": 22 * NS,
    }
    frame = producer.prepare(entry, 22 * NS, 31 * NS)
    with pytest.raises(wire.ProtocolError, match="restore-reserve"):
        reader.prepare_peer(frame, 23 * NS + 1)
    assert reader.failed


def test_exact_hard_limits_and_incremental_storage_inventory() -> None:
    """Keep raw, ACK, parent and stack caps distinct and explicit."""
    assert wire.EVIDENCE_HEADER_BYTES == 16
    assert wire.EVIDENCE_ACK_BYTES == 80
    assert wire.MAX_EVIDENCE_RECORDS == 32768
    assert wire.MAX_EVIDENCE_BYTES == 4_000_000
    assert wire.MAX_EVIDENCE_RECORD_BYTES == 4096
    assert wire.MAX_EVIDENCE_DEPTH == 32
    assert wire.MAX_EVIDENCE_ACK_BYTES == 2_621_440
    assert wire.MAX_PARENT_EVIDENCE_RECORDS == 32
    assert wire.MAX_PARENT_EVIDENCE_RECORD_BYTES == 1024


@pytest.mark.parametrize("name", ["<lambda>", "CloseHandle", "COM"])
def test_native_cleanup_preserves_original_bounded_callable_name(name: str) -> None:
    """The donor uses exact lambda closures for some mandatory resource releases."""
    reader, producer, _, _ = enter_context()
    frame = send(
        reader,
        producer,
        {"event": "call_start", "name": name, "effective_deadline_ns": 2 * NS},
        NS,
    )
    assert wire.decode_evidence_record(frame).event["name"] == name
    assert reader.calls[-1].name == name


@pytest.mark.parametrize("name", ["<arbitrary>", "<lambda >", "a" * 129, "\x00"])
def test_native_name_does_not_accept_arbitrary_closure_descriptions(name: str) -> None:
    """The one legacy closure literal does not permit unbounded arbitrary logging."""
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(
            wire.EvidenceRecord(
                1,
                NS,
                {"event": "call_start", "name": name, "effective_deadline_ns": 2 * NS},
            )
        )


def finish_packet(
    reader: wire.EvidenceReaderState,
    parent: wire.EvidenceProducer,
    packet: dict[str, object],
    completed_ns: int,
    delivered_ns: int,
) -> None:
    """Retain the response acceptance stamp through later packet delivery."""
    value = {
        key: value
        for key, value in packet.items()
        if key not in ("requested_ns", "deadline_ns", "response_deadline_ns")
    }
    value.update(
        completed_ns=completed_ns,
        retirement_started_ns=completed_ns,
        retirement_deadline_ns=min(completed_ns + 5 * NS, 31 * NS),
    )
    frame = parent.prepare(value, delivered_ns, 6 * NS)
    reader.prepare_parent(frame, delivered_ns)
    ack = reader.acknowledge_parent(delivered_ns)
    assert ack is not None
    parent.acknowledge(ack, delivered_ns)


def test_finish_response_marker_opens_only_the_original_cleanup_cap() -> None:
    """A timely marker permits cleanup inside its original5s allowance."""
    reader, producer, parent, packet = enter_context(16)
    send(reader, producer, phase(packet, "finish_response_sent", 2 * NS), 2 * NS)
    finish_packet(reader, parent, packet, 2 * NS, 4 * NS)
    assert reader.context is not None
    assert reader.context["deadline_ns"] == 6 * NS
    send(
        reader,
        producer,
        {"event": "call_start", "name": "CloseHandle", "effective_deadline_ns": 6 * NS},
        4 * NS,
    )
    with pytest.raises(wire.ProtocolError, match="deadline"):
        reader.check(6 * NS)


@pytest.mark.parametrize(
    "fault",
    [
        "no_marker",
        "early_cleanup",
        "late_marker",
        "late_marker_ack",
        "late_parent_transition",
        "duplicate_marker",
        "cleanup_before_marker_ack",
    ],
)
def test_finish_response_marker_is_mandatory_and_deadline_bound(fault: str) -> None:
    """No omitted, duplicate or slow marker can authorize native cleanup."""
    reader, producer, parent, packet = enter_context(16)
    marker = phase(packet, "finish_response_sent", NS)
    if fault in ("late_parent_transition", "duplicate_marker"):
        send(reader, producer, marker, NS)
    if fault in ("late_marker_ack", "cleanup_before_marker_ack"):
        frame = producer.prepare(marker, NS, 3 * NS)
        reader.prepare_peer(frame, NS)
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - one selected protocol fault
        if fault == "no_marker":
            reader.check(3 * NS)
        elif fault == "early_cleanup":
            send(
                reader,
                producer,
                {
                    "event": "call_start",
                    "name": "CloseHandle",
                    "effective_deadline_ns": 3 * NS,
                },
                NS,
            )
        elif fault == "late_marker":
            send(reader, producer, marker, 3 * NS)
        elif fault == "late_marker_ack":
            reader.acknowledge_persisted(3 * NS)
        elif fault == "late_parent_transition":
            finish_packet(reader, parent, packet, 3 * NS, 4 * NS)
        elif fault == "duplicate_marker":
            send(reader, producer, marker, NS)
        else:
            frame = wire.encode_evidence_record(
                wire.EvidenceRecord(
                    producer.count + 1,
                    NS,
                    {
                        "event": "call_start",
                        "name": "CloseHandle",
                        "effective_deadline_ns": 3 * NS,
                    },
                )
            )
            reader.prepare_peer(frame, NS)
    assert reader.failed or producer.failed


def test_parent_finish_completion_may_precede_peer_response_marker() -> None:
    """Independent endpoint scheduling cannot create a marker/completion cycle."""
    reader, producer, parent, packet = enter_context(16)
    finish_packet(reader, parent, packet, NS, NS + 1)
    assert reader.parent_completed
    send(reader, producer, phase(packet, "finish_response_sent", NS + 2), NS + 2)
    send(
        reader,
        producer,
        {"event": "call_start", "name": "CloseHandle", "effective_deadline_ns": 3 * NS},
        NS + 3,
    )
    assert len(reader.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deadline_ns", 6 * NS + 1),
        ("deadline_ns", 5 * NS),
        ("response_deadline_ns", 3 * NS + 1),
        ("response_deadline_ns", True),
        ("cleanup_deadline_ns", 6 * NS),
    ],
)
def test_finish_context_requires_exact_reviewed_dual_bounds(
    field: str, value: object
) -> None:
    """Neither duplicated names nor tightened/restamped substitutes alter the schema."""
    reader, producer, parent, packet = enter_context(15)
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    complete(reader, parent, packet, NS)
    finish = {
        "schema": 1,
        "session": SESSION,
        "epoch": EPOCH,
        "context_id": 16,
        "request_id": 16,
        "step_id": 0,
        "operation": "finish",
        "requested_ns": NS,
        "deadline_ns": 6 * NS,
        "response_deadline_ns": 3 * NS,
        "command_sha256": DIGEST,
    }
    finish[field] = value
    payload = wire.canonical_json(finish)
    frame = struct.pack(">IIQ", len(payload), 31, NS) + payload
    with pytest.raises(wire.ProtocolError):
        reader.prepare_parent(frame, NS)
    assert reader.failed


def test_finish_marker_cannot_be_emitted_in_an_unrelated_phase() -> None:
    """A valid correlation is still insufficient outside the exact finish context."""
    reader, producer, _, packet = enter_context(3)
    with pytest.raises(wire.ProtocolError, match="marker-state"):
        send(reader, producer, phase(packet, "finish_response_sent", NS), NS)


def test_nonfinish_completion_ack_waits_for_persisted_peer_phase_end() -> None:
    """The parent waits while the independent peer endpoint remains serviceable."""
    reader, producer, parent, packet = enter_context(3)
    value = {
        key: value
        for key, value in packet.items()
        if key not in ("requested_ns", "deadline_ns")
    }
    value["completed_ns"] = NS
    frame = parent.prepare(value, NS, 3 * NS)
    reader.prepare_parent(frame, NS)
    assert not reader.parent_ack_ready
    assert reader.acknowledge_parent(NS) is None
    peer_frame = producer.prepare(phase(packet, "phase_end", NS), NS, 3 * NS)
    reader.prepare_peer(peer_frame, NS)
    assert not reader.parent_ack_ready
    producer.acknowledge(reader.acknowledge_persisted(NS), NS)
    assert reader.parent_ack_ready
    ack = reader.acknowledge_parent(NS)
    assert ack is not None
    parent.acknowledge(ack, NS)
    assert reader.pending_parent is None


def test_held_parent_completion_ack_keeps_original_frame_deadline() -> None:
    """A timely peer end never grants an extra timeout for the withheld parent ACK."""
    reader, producer, parent, packet = enter_context(3)
    value = {
        key: value
        for key, value in packet.items()
        if key not in ("requested_ns", "deadline_ns")
    }
    value["completed_ns"] = NS
    frame = parent.prepare(value, NS, 3 * NS)
    reader.prepare_parent(frame, NS)
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    assert reader.ack_deadline_ns == 3 * NS
    with pytest.raises(wire.ProtocolError):
        reader.acknowledge_parent(3 * NS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_started_ns", True),
        ("command_started_ns", 0),
        ("command_deadline_ns", 3 * NS + 1),
        ("command_deadline_ns", 2 * NS),
    ],
)
def test_finish_command_stamps_reject_reset_and_ambiguous_values(
    field: str, value: object
) -> None:
    """Finish command bytes carry original dispatch2s, not a peer-created clock."""
    payload = {
        "operation": "moving_finish",
        "command_started_ns": NS,
        "command_deadline_ns": 3 * NS,
    }
    payload[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.validate_finish_request(
            payload, sequence_started_ns=NS, sequence_deadline_ns=31 * NS, now_ns=NS
        )


def test_finish_command_original_deadline_and_session_clamp() -> None:
    """Queueing, digest construction and peer admission all consume one2s budget."""
    payload = {
        "operation": "moving_finish",
        "command_started_ns": NS,
        "command_deadline_ns": 3 * NS,
    }
    assert (
        wire.validate_finish_request(
            payload,
            sequence_started_ns=NS,
            sequence_deadline_ns=31 * NS,
            now_ns=3 * NS - 1,
        )
        == payload
    )
    with pytest.raises(wire.ProtocolError):
        wire.validate_finish_request(
            payload, sequence_started_ns=NS, sequence_deadline_ns=31 * NS, now_ns=3 * NS
        )
    payload.update(command_started_ns=30 * NS, command_deadline_ns=31 * NS)
    assert (
        wire.validate_finish_request(
            payload,
            sequence_started_ns=NS,
            sequence_deadline_ns=31 * NS,
            now_ns=30 * NS,
        )
        == payload
    )


@pytest.mark.parametrize(
    "path", ["python.exe", "", "C:\\bad\x00.exe", "C:\\" + "a" * 2049]
)
def test_expected_identity_rejects_unpinned_or_ambiguous_image_path(path: str) -> None:
    """Authenticated client identity always names a bounded absolute executable."""
    packet = identity().value
    packet["peer_executable"] = path
    with pytest.raises(wire.ProtocolError):
        wire.EvidenceIdentity.from_value(packet)


def test_fixed_pipe_endpoints_and_fragmented_record_decoder() -> None:
    """Both endpoint names and byte-at-a-time delivery preserve one exact record."""
    assert (
        wire.evidence_pipe_name(SESSION) == "VerdiClip-moving-edit-evidence-" + SESSION
    )
    assert (
        wire.evidence_pipe_name(SESSION, parent=True)
        == "VerdiClip-moving-edit-evidence-parent-" + SESSION
    )
    record = wire.EvidenceRecord(1, NS, {"event": "call_end", "seconds": 0.0})
    frame = wire.encode_evidence_record(record)
    decoder = wire.EvidenceDecoder()
    for byte in frame[:-1]:
        assert decoder.feed(bytes([byte])) is None
    assert decoder.feed(frame[-1:]) == record
    decoder.eof()
    with pytest.raises(wire.ProtocolError):
        wire.evidence_pipe_name(SESSION, parent=cast("Any", 1))


@pytest.mark.parametrize("data", [b"", b"x" * 79, b"x" * 81])
def test_ack_cannot_be_truncated_or_extended(data: bytes) -> None:
    """An ACK is exactly80 bytes before any content validation."""
    with pytest.raises(wire.ProtocolError):
        wire.EvidenceAck.decode(data)


def test_producer_rejects_unsolicited_ack_and_parent_final() -> None:
    """Neither future native credit nor a parent raw-transcript final exists."""
    producer = wire.EvidenceProducer(SESSION, EPOCH)
    with pytest.raises(wire.ProtocolError):
        producer.acknowledge(wire.EvidenceAck(SESSION, EPOCH, 1, DIGEST).encode(), NS)
    parent = wire.EvidenceProducer(SESSION, EPOCH, parent=True)
    with pytest.raises(wire.ProtocolError):
        parent.final_event()


@pytest.mark.parametrize("value", [True, "0.1", -0.1, 3.1, float("inf")])
def test_native_duration_rejects_coercion_and_unbounded_values(value: object) -> None:
    """Malformed native times cannot enter the canonical raw transcript."""
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(
            wire.EvidenceRecord(1, NS, {"event": "call_end", "seconds": value})
        )


def seek_event(
    kind: str, *, step_id: int = 1, restoring: bool = False
) -> dict[str, object]:
    """Preserve the donor's exact native seek event fields."""
    result: dict[str, object] = {
        "event": kind,
        "vertical": 100.0 * wire.FRAME_LINES[step_id - 1] / 13,
        "horizontal": -1.0,
        "restoring": restoring,
    }
    if kind == "seek_end":
        result["status"] = 0
    return result


def normal_seek(
    reader: wire.EvidenceReaderState,
    producer: wire.EvidenceProducer,
    packet: dict[str, object],
    now: int,
) -> list[bytes]:
    """Emit one exact fixed write for a move, retaining both real framed records."""
    if packet["operation"] != "move":
        return []
    step_id = cast("int", packet["step_id"])
    return [
        send(
            reader,
            producer,
            seek_event(kind, step_id=step_id, restoring=step_id == 7),
            now,
        )
        for kind in ("seek_start", "seek_end")
    ]


def test_actual_native_read_seek_and_nested_call_stream() -> None:
    """Reader pairing covers raw target evidence, not only synthetic phase records."""
    reader, producer, _, _ = enter_context(2)
    send(reader, producer, seek_event("seek_start"), NS)
    send(
        reader,
        producer,
        {"event": "call_start", "name": "COM", "effective_deadline_ns": 4 * NS},
        NS,
    )
    send(reader, producer, {"event": "call_end", "seconds": 0.0}, NS)
    send(reader, producer, seek_event("seek_end"), NS)
    send(
        reader,
        producer,
        {
            "event": "edit_read",
            "message": 214,
            "index": 0,
            "result": 3,
            "timeout_ms": 500,
        },
        NS,
    )
    assert reader.seek is None
    assert not reader.calls


@pytest.mark.parametrize(
    "fault",
    [
        "unpaired",
        "overlap",
        "wrong_phase",
        "wrong_restoring",
        "nonbool",
        "phase_end_while_seeking",
    ],
)
def test_reader_rejects_unpaired_and_unscoped_native_seeks(fault: str) -> None:
    """A native write trace cannot be relabelled or cross an independent context."""
    reader, producer, _, packet = enter_context(1 if fault == "wrong_phase" else 2)
    if fault in ("overlap", "phase_end_while_seeking"):
        send(reader, producer, seek_event("seek_start"), NS)
    event = seek_event("seek_end" if fault == "unpaired" else "seek_start")
    if fault == "wrong_restoring":
        event["restoring"] = True
    elif fault == "nonbool":
        event["restoring"] = 1
    elif fault == "phase_end_while_seeking":
        event = phase(packet, "phase_end", NS)
    with pytest.raises(wire.ProtocolError):
        send(reader, producer, event, NS)
    assert reader.failed or producer.failed


@pytest.mark.parametrize(
    "fault",
    [
        "no_context",
        "not_entered",
        "duplicate_enter",
        "future_end",
        "unpaired_end",
        "no_pending_peer",
        "no_pending_parent",
    ],
)
def test_reader_phase_and_ack_admission_states(fault: str) -> None:
    """All phase transitions use the independent context and explicit pending state."""
    reader, producer, parent = setup()
    packet: dict[str, object] = {
        "context_id": 1,
        "operation": "begin",
        "request_id": 1,
        "step_id": 0,
        "command_sha256": DIGEST,
    }
    if fault != "no_context":
        packet = context(reader, parent, 1, NS)
    if fault in ("duplicate_enter", "future_end", "unpaired_end"):
        send(reader, producer, phase(packet, "phase_enter", NS), NS)
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - one selected illegal transition
        if fault == "no_pending_peer":
            reader.acknowledge_persisted(NS)
        elif fault == "no_pending_parent":
            reader.acknowledge_parent(NS)
        elif fault == "not_entered":
            send(reader, producer, phase(packet, "phase_end", NS), NS)
        elif fault == "future_end":
            send(reader, producer, phase(packet, "phase_end", NS + 1), NS)
        elif fault == "unpaired_end":
            send(reader, producer, {"event": "call_end", "seconds": 0.0}, NS)
        else:
            send(reader, producer, phase(packet, "phase_enter", NS), NS)
    assert reader.failed


def replay_prefix() -> tuple[wire.EvidenceReplay, dict[str, object]]:
    """Use exact raw framing for a live authenticated begin phase prefix."""
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    replay = wire.EvidenceReplay()
    replay.feed(
        wire.encode_evidence_record(
            wire.EvidenceRecord(1, NS, wire.make_evidence_hello(launch, 10, 100))
        )
    )
    packet: dict[str, object] = {
        "context_id": 1,
        "operation": "begin",
        "request_id": 1,
        "step_id": 0,
        "command_sha256": DIGEST,
    }
    replay.feed(
        wire.encode_evidence_record(
            wire.EvidenceRecord(2, NS, phase(packet, "phase_enter", NS))
        )
    )
    return replay, packet


@pytest.mark.parametrize(
    "fault",
    [
        "overlap",
        "future_end",
        "pending_call_end",
        "overlapping_seek",
        "unpaired_seek",
        "pending_seek_end",
        "out_of_phase_read",
        "invalid_marker",
        "expired_call",
        "expanded_call",
        "early_final",
        "huge_chunk",
        "bad_header",
    ],
)
def test_independent_replay_rejects_unsafe_raw_histories(fault: str) -> None:  # noqa: C901 - explicit independent replay corruption matrix
    """Stored-raw replay detects pairing, phase, bound and closure corruption itself."""
    replay, packet = replay_prefix()
    event = phase(packet, "phase_enter", NS)
    stamp = NS
    if fault in ("pending_call_end", "expired_call"):
        start = {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS}
        replay.feed(wire.encode_evidence_record(wire.EvidenceRecord(3, NS, start)))
        event = phase(packet, "phase_end", NS)
        if fault == "expired_call":
            event, stamp = {"event": "call_end", "seconds": 1.0}, 2 * NS
    elif fault in ("overlapping_seek", "pending_seek_end"):
        replay.feed(
            wire.encode_evidence_record(
                wire.EvidenceRecord(3, NS, seek_event("seek_start"))
            )
        )
        event = (
            seek_event("seek_start")
            if fault == "overlapping_seek"
            else phase(packet, "phase_end", NS)
        )
    elif fault == "future_end":
        event = phase(packet, "phase_end", NS + 1)
    elif fault == "unpaired_seek":
        event = seek_event("seek_end")
    elif fault == "out_of_phase_read":
        replay.feed(
            wire.encode_evidence_record(
                wire.EvidenceRecord(3, NS, phase(packet, "phase_end", NS))
            )
        )
        event = {
            "event": "edit_read",
            "message": 214,
            "index": 0,
            "result": 3,
            "timeout_ms": 500,
        }
    elif fault == "invalid_marker":
        event = phase(packet, "finish_response_sent", NS)
    elif fault == "expanded_call":
        event = {"event": "call_start", "name": "COM", "effective_deadline_ns": 5 * NS}
    elif fault == "early_final":
        event = {
            "event": "final",
            "preceding_records": replay.count,
            "preceding_bytes": replay.byte_count,
            "preceding_sha256": replay.hexdigest,
        }
    data = wire.encode_evidence_record(
        wire.EvidenceRecord(replay.count + 1, stamp, event)
    )
    if fault == "huge_chunk":
        data = b"x" * 4097
    elif fault == "bad_header":
        data = struct.pack(">IIQ", 4097, 3, NS) + b"x"
    with pytest.raises(wire.ProtocolError):
        replay.feed(data)
    assert replay.failed


def test_replay_valid_seek_pair_and_read_preserve_original_bytes() -> None:
    """The bounded audit accepts all fixed raw native event kinds in order."""
    replay, _ = replay_prefix()
    for event in (
        seek_event("seek_start"),
        seek_event("seek_end"),
        {
            "event": "edit_read",
            "message": 214,
            "index": 0,
            "result": 3,
            "timeout_ms": 500,
        },
    ):
        frame = wire.encode_evidence_record(
            wire.EvidenceRecord(replay.count + 1, NS, event)
        )
        replay.feed(frame)
    assert replay.count == 5
    assert replay.seek is None
    assert len(replay.buffer) == 0


def test_wire_entrypoints_reject_wrong_types_and_declared_lengths() -> None:
    """Public parse boundaries reject malformed input before state mutation."""
    with pytest.raises(wire.ProtocolError):
        wire.deserialize_evidence_reader_launch(cast("Any", b"{}"))
    with pytest.raises(wire.ProtocolError):
        wire.EvidenceReaderState(cast("Any", identity().value))
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(cast("Any", {}))
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(wire.EvidenceRecord(1, NS, cast("Any", [])))
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(
            wire.EvidenceRecord(1, NS, cast("Any", [])), parent=True
        )
    with pytest.raises(wire.ProtocolError):
        wire.decode_evidence_record(b"x")
    with pytest.raises(wire.ProtocolError):
        wire.decode_evidence_record(struct.pack(">IIQ", 5, 1, NS) + b"{}")
    decoder = wire.EvidenceDecoder()
    with pytest.raises(wire.ProtocolError):
        decoder.feed(b"x" * 4097)
    assert decoder.failed
    with pytest.raises(wire.ProtocolError):
        wire.frame_pipe_name("VerdiClip-foreign-" + SESSION, 1)


def test_finish_parser_independent_shape_and_session_lower_bound() -> None:
    """Standalone parsing stays bounded; admission also binds the original session."""
    payload = {
        "operation": "moving_finish",
        "command_started_ns": NS,
        "command_deadline_ns": 3 * NS,
    }
    assert wire.validate_finish_request(payload) == payload
    with pytest.raises(wire.ProtocolError, match="precedes"):
        wire.validate_finish_request(payload, sequence_started_ns=NS + 1)


def test_failure_reason_cannot_be_arbitrary_text() -> None:
    """Failure evidence uses the exact reviewed eligibility categories."""
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(
            wire.EvidenceRecord(
                1, NS, {"event": "failure", "context_id": 1, "reason": "trust-me"}
            )
        )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_hello",
        "duplicate_hello",
        "future_record",
        "native_after_end",
        "early_final",
        "duplicate_failure",
        "restore_without_entry",
        "restore_with_pending_call",
    ],
)
def test_reader_rejects_authentication_phase_and_restore_shortcuts(fault: str) -> None:
    """Actual framed records cannot bypass authenticated ordered live state."""
    reader, producer, _, packet = enter_context(3)
    event: dict[str, object] = {"event": "call_end", "seconds": 0.0}
    stamp = NS
    if fault == "missing_hello":
        reader = wire.EvidenceReaderState(identity())
        reader.bind_peer(10, 100, "C:\\python.exe", NS)
        reader.bind_parent(20, 200, "C:\\python.exe", NS)
        event = phase(packet, "phase_enter", NS)
    elif fault == "duplicate_hello":
        launch = wire.MovingEditLaunch(
            "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
        )
        event = cast("dict[str, object]", wire.make_evidence_hello(launch, 10, 100))
    elif fault == "future_record":
        stamp += 1
    elif fault == "native_after_end":
        send(reader, producer, phase(packet, "phase_end", NS), NS)
    elif fault == "early_final":
        event = cast("dict[str, object]", producer.final_event())
    elif fault == "duplicate_failure":
        event = {"event": "failure", "context_id": 3, "reason": "observation"}
        send(reader, producer, event, NS)
    elif fault == "restore_without_entry":
        event = {"event": "failure_restore_phase", "phase_id": 1, "operation": "guard"}
    else:
        send(
            reader,
            producer,
            {"event": "call_start", "name": "COM", "effective_deadline_ns": 2 * NS},
            NS,
        )
        event = {
            "event": "failure_restore_enter",
            "context_id": 3,
            "sequence_started_ns": NS,
            "sequence_deadline_ns": 31 * NS,
            "failure_ns": NS,
        }
    data = wire.encode_evidence_record(
        wire.EvidenceRecord(reader.count + 1, stamp, event)
    )
    with pytest.raises(wire.ProtocolError):
        reader.prepare_peer(data, NS)
    assert reader.failed


@pytest.mark.parametrize(
    "fault",
    [
        "completion_before_enter",
        "completion_before_peer_end",
        "future_completion",
        "duplicate_completion",
        "future_packet",
        "duplicate_pending_packet",
        "prequeue_before_prior_commit",
        "expanded_move",
    ],
)
def test_parent_context_preserves_actual_commitment_and_queue_order(fault: str) -> None:
    """Independent parent authority is bounded and cannot replace pending work."""
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, NS)
    if fault != "completion_before_enter":
        send(reader, producer, phase(packet, "phase_enter", NS), NS)
    if fault in (
        "duplicate_completion",
        "future_packet",
        "prequeue_before_prior_commit",
        "expanded_move",
    ):
        send(reader, producer, phase(packet, "phase_end", NS), NS)
        complete(reader, parent, packet, NS + 1)
    completion = {
        key: value
        for key, value in packet.items()
        if key not in ("requested_ns", "deadline_ns")
    }
    completion["completed_ns"] = NS + 2 if fault == "future_completion" else NS
    now = NS + 1 if reader.last_ns > NS else NS
    stamp = now
    if fault == "future_packet":
        stamp = now + 1
    if fault in ("prequeue_before_prior_commit", "expanded_move"):
        completion = {
            "schema": 1,
            "session": SESSION,
            "epoch": EPOCH,
            "context_id": 2,
            "request_id": 2,
            "step_id": 1,
            "operation": "move",
            "requested_ns": NS if fault == "prequeue_before_prior_commit" else now,
            "deadline_ns": 4 * NS + 2 if fault == "expanded_move" else 4 * NS,
            "command_sha256": DIGEST,
        }
    if fault == "duplicate_pending_packet":
        # A valid observe completion may remain pending while peer closes.
        reader, producer, parent, packet = enter_context(3)
        completion = {
            key: value
            for key, value in packet.items()
            if key not in ("requested_ns", "deadline_ns")
        }
        completion["completed_ns"] = NS
        first = parent.prepare(completion, NS, 3 * NS)
        reader.prepare_parent(first, NS)
    data = wire.encode_evidence_record(
        wire.EvidenceRecord(reader.parent_count + 1, stamp, completion), parent=True
    )
    with pytest.raises(wire.ProtocolError):
        reader.prepare_parent(data, now)
    assert reader.failed


@pytest.mark.parametrize(
    "fault",
    [
        "premature_audit",
        "duplicate_hello",
        "early_hello",
        "backwards",
        "failure_record",
    ],
)
def test_stored_replay_does_not_supply_missing_live_authority(fault: str) -> None:
    """Audit cannot replace live completion or the original hello chronology."""
    if fault == "premature_audit":
        reader, _, _ = setup()
        with pytest.raises(wire.ProtocolError):
            reader.verify_replay(wire.EvidenceReplay())
        return
    replay, packet = replay_prefix()
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    if fault == "failure_record":
        event = {"event": "failure", "context_id": 1, "reason": "observation"}
        replay.feed(wire.encode_evidence_record(wire.EvidenceRecord(3, NS, event)))
        assert replay.failed_context
        return
    stamp = NS
    event = wire.make_evidence_hello(launch, 10, 100)
    if fault == "early_hello":
        replay = wire.EvidenceReplay()
        stamp -= 1
    elif fault == "backwards":
        stamp -= 1
        event = phase(packet, "phase_end", stamp)
    data = wire.encode_evidence_record(
        wire.EvidenceRecord(replay.count + 1, stamp, event)
    )
    with pytest.raises(wire.ProtocolError):
        replay.feed(data)
    assert replay.failed


def unauthenticated_route() -> tuple[
    wire.EvidenceReaderState, wire.EvidenceProducer, dict[str, object]
]:
    """Pin preconnected endpoints without treating runtime-ready as authentication."""
    reader = wire.EvidenceReaderState(identity())
    reader.bind_parent(20, 200, "C:\\python.exe", NS)
    reader.bind_peer(10, 100, "C:\\python.exe", NS)
    producer = wire.EvidenceProducer(SESSION, EPOCH)
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    return (
        reader,
        producer,
        cast("dict[str, object]", wire.make_evidence_hello(launch, 10, 100)),
    )


def test_hello_authentication_cap_uses_original_worker_entry_and_exact_ack() -> None:
    """Runtime-ready and hello delivery never replace the original startup3s clock."""
    reader, producer, hello = unauthenticated_route()
    frame = producer.prepare(hello, 2 * NS, 31 * NS)
    reader.prepare_peer(frame, 3 * NS)
    assert not reader.authenticated
    assert reader.ack_deadline_ns == 4 * NS
    assert producer.pending is not None
    assert producer.pending[1] == 4 * NS
    ack = reader.acknowledge_persisted(4 * NS - 1)
    producer.acknowledge(ack, 4 * NS - 1)
    assert reader.authenticated
    assert wire.evidence_auth_deadline_ns(NS, 31 * NS, now_ns=4 * NS - 1) == 4 * NS
    with pytest.raises(wire.ProtocolError):
        wire.evidence_auth_deadline_ns(NS, 31 * NS, now_ns=4 * NS)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_hello",
        "late_hello",
        "late_reader_ack",
        "late_producer_ack",
        "late_bind",
    ],
)
def test_startup_authentication_refuses_exact_boundary_or_missing_ack(
    fault: str,
) -> None:
    """A slow authentication handoff fails without granting native admission."""
    reader, producer, hello = unauthenticated_route()
    if fault in ("late_reader_ack", "late_producer_ack"):
        frame = producer.prepare(hello, NS, 31 * NS)
        reader.prepare_peer(frame, NS)
    ack = b""
    if fault == "late_producer_ack":
        ack = reader.acknowledge_persisted(4 * NS - 1)
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - one exact boundary injection
        if fault == "missing_hello":
            reader.check(4 * NS)
        elif fault == "late_hello":
            producer.prepare(hello, 4 * NS, 31 * NS)
        elif fault == "late_reader_ack":
            reader.acknowledge_persisted(4 * NS)
        elif fault == "late_producer_ack":
            producer.acknowledge(ack, 4 * NS)
        else:
            late = wire.EvidenceReaderState(identity())
            late.bind_parent(20, 200, "C:\\python.exe", 4 * NS)
    assert reader.failed or producer.failed or fault == "late_bind"


def test_hello_auth_deadline_respects_tighter_sequence_and_caller_limit() -> None:
    """The startup3s allowance cannot extend the original sequence or caller cap."""
    assert wire.evidence_auth_deadline_ns(NS, 2 * NS, now_ns=2 * NS - 1) == 2 * NS
    with pytest.raises(wire.ProtocolError):
        wire.evidence_auth_deadline_ns(NS, 2 * NS, now_ns=2 * NS)
    _, producer, hello = unauthenticated_route()
    producer.prepare(hello, NS, 2 * NS)
    assert producer.pending is not None
    assert producer.pending[1] == 2 * NS
    with pytest.raises(wire.ProtocolError):
        producer.check(2 * NS)


def test_authentication_completes_without_renewing_deadline_on_parent_pin() -> None:
    """Connecting parent metadata never creates a fresh startup clock."""
    reader = wire.EvidenceReaderState(identity())
    reader.bind_parent(20, 200, "C:\\python.exe", NS)
    reader.bind_peer(10, 100, "C:\\python.exe", 4 * NS - 1)
    assert reader.ack_deadline_ns == 4 * NS
    assert not reader.authenticated
    with pytest.raises(wire.ProtocolError):
        reader.check(4 * NS)


def test_raw_replay_rejects_hello_after_original_authentication_cap() -> None:
    """Stored raw evidence cannot conceal authentication after the startup ceiling."""
    _, _, hello = unauthenticated_route()
    frame = wire.encode_evidence_record(wire.EvidenceRecord(1, 4 * NS, hello))
    replay = wire.EvidenceReplay()
    with pytest.raises(wire.ProtocolError):
        replay.feed(frame)


def completed_transcripts() -> tuple[
    wire.EvidenceReaderState, list[bytes], list[bytes]
]:
    """Produce both complete streams through real producer/reader/ACK transitions."""
    reader, producer, parent = setup()
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + SESSION, EPOCH, 20, NS, 31 * NS, SESSION, 30, 300
    )
    peers = [
        wire.encode_evidence_record(
            wire.EvidenceRecord(1, NS, wire.make_evidence_hello(launch, 10, 100))
        )
    ]
    parents: list[bytes] = []
    for index in range(1, 17):
        now = NS + index * 100
        packet = context(reader, parent, index, now, parents)
        peers.append(
            send(reader, producer, phase(packet, "phase_enter", now + 1), now + 1)
        )
        if index == 16:
            peers.append(
                send(
                    reader,
                    producer,
                    phase(packet, "finish_response_sent", now + 2),
                    now + 2,
                )
            )
            complete(reader, parent, packet, now + 3, parents)
        if packet["operation"] == "move":
            step_id = cast("int", packet["step_id"])
            peers.append(
                send(
                    reader,
                    producer,
                    seek_event("seek_start", step_id=step_id, restoring=step_id == 7),
                    now + 3,
                )
            )
        peers.append(
            send(
                reader,
                producer,
                {
                    "event": "call_start",
                    "name": "CloseHandle" if index == 16 else "COM",
                    "effective_deadline_ns": now + NS,
                },
                now + 4,
            )
        )
        peers.append(
            send(reader, producer, {"event": "call_end", "seconds": 0.0}, now + 5)
        )
        if packet["operation"] == "move":
            step_id = cast("int", packet["step_id"])
            peers.append(
                send(
                    reader,
                    producer,
                    seek_event("seek_end", step_id=step_id, restoring=step_id == 7),
                    now + 6,
                )
            )
        if index != 16 and packet["operation"] == "observe":
            complete(reader, parent, packet, now + 6, parents)
        peers.append(
            send(reader, producer, phase(packet, "phase_end", now + 7), now + 7)
        )
        if index != 16:
            if packet["operation"] == "observe":
                ack = reader.acknowledge_parent(now + 8)
                assert ack is not None
                parent.acknowledge(ack, now + 8)
            else:
                complete(reader, parent, packet, now + 8, parents)
    peers.append(send(reader, producer, producer.final_event(), NS + 1700))
    reader.peer_eof(NS + 1701)
    return reader, parents, peers


def correlated_audit(
    parents: list[bytes], peers: list[bytes]
) -> wire.EvidenceCorrelatedReplay:
    """Stream bounded chunks; never materialize an unbounded peer transcript."""
    replay = wire.EvidenceCorrelatedReplay(identity())
    for record in parents:
        for offset in range(0, len(record), 37):
            replay.feed_parent(record[offset : offset + 37])
    replay.parent_eof()
    for record in peers:
        for offset in range(0, len(record), 53):
            replay.feed_peer(record[offset : offset + 53])
    replay.peer_eof()
    return replay


@pytest.mark.parametrize("step_id", range(1, 8))
def test_each_fixed_move_requires_its_exact_successful_seek(step_id: int) -> None:
    """The repeated bottom position still requires two distinct writes."""
    reader, producer, parent, packet = enter_context(2 * step_id)
    frames = normal_seek(reader, producer, packet, NS)
    assert len(frames) == 2
    start, end = (wire.decode_evidence_record(frame).event for frame in frames)
    assert type(start["vertical"]) is float
    assert start["vertical"] == 100.0 * wire.FRAME_LINES[step_id - 1] / 13
    assert start["restoring"] is (step_id == 7)
    assert type(end["status"]) is int
    assert end["status"] == 0
    send(reader, producer, phase(packet, "phase_end", NS), NS)
    complete(reader, parent, packet, NS)
    assert reader.peer_phase_closed
    assert reader.parent_completed


@pytest.mark.parametrize("step_id", range(1, 8))
def test_fixed_move_cannot_close_without_its_write(step_id: int) -> None:
    """Earlier writes and an equal preceding bottom position cannot stand in."""
    reader, producer, _, packet = enter_context(2 * step_id)
    with pytest.raises(wire.ProtocolError, match="fixed-seek-missing"):
        send(reader, producer, phase(packet, "phase_end", NS), NS)
    assert reader.failed
    assert not reader.peer_phase_closed


@pytest.mark.parametrize(
    ("step_id", "vertical"),
    [(1, 0.0), (1, 100.0 * 3 / 13 + 1e-12), (3, 100.0), (2, 0), (4, 100)],
)
def test_fixed_move_rejects_wrong_target_and_numeric_coercion(
    step_id: int, vertical: object
) -> None:
    """Exact donor floating-point targets are not rounded or type-normalized."""
    reader, producer, _, _ = enter_context(2 * step_id)
    event = seek_event("seek_start", step_id=step_id)
    event["vertical"] = vertical
    count, byte_count, digest = reader.count, reader.byte_count, reader.hexdigest
    with pytest.raises(wire.ProtocolError):
        send(reader, producer, event, NS)
    assert reader.failed
    assert (reader.count, reader.byte_count, reader.hexdigest) == (
        count,
        byte_count,
        digest,
    )
    assert reader.pending_peer is None


def test_fixed_move_rejects_a_second_successful_seek_pair() -> None:
    """Pairing alone cannot authorize a second native write in the same move."""
    reader, producer, _, packet = enter_context(2)
    normal_seek(reader, producer, packet, NS)
    with pytest.raises(wire.ProtocolError, match="fixed-seek-duplicate"):
        send(reader, producer, seek_event("seek_start"), NS)
    assert reader.failed


@pytest.mark.parametrize("status", [-2147467259, -1, 1, 2147483647])
def test_nonzero_seek_result_retains_only_accepted_prefix(status: int) -> None:
    """A rejected result remains in bounded failure metadata, never accepted raw."""
    reader, producer, _, _ = enter_context(2)
    send(reader, producer, seek_event("seek_start"), NS)
    prefix = reader.count, reader.byte_count, reader.hexdigest
    end = seek_event("seek_end")
    end["status"] = status
    with pytest.raises(
        wire.ProtocolError, match=f"fixed-seek-status:step=1,status={status}"
    ):
        send(reader, producer, end, NS)
    assert (reader.count, reader.byte_count, reader.hexdigest) == prefix
    assert reader.pending_peer is None
    assert f"step=1,status={status}" in reader.status["failure"]
    assert reader.failed
    assert not reader.complete


@pytest.mark.parametrize("status", [False, True, 0.0, "0"])
def test_seek_result_requires_exact_integer_status(status: object) -> None:
    """False and floating-point zero cannot masquerade as native integer success."""
    event = seek_event("seek_end")
    event["status"] = status
    with pytest.raises(wire.ProtocolError):
        wire.encode_evidence_record(wire.EvidenceRecord(1, NS, event))


@pytest.mark.parametrize("context_id", [1, 3, 16])
def test_only_normal_move_contexts_can_admit_fixed_writes(context_id: int) -> None:
    """An exact numeric target does not authorize a begin, observe or finish write."""
    reader, producer, _, packet = enter_context(context_id)
    if context_id == 16:
        send(reader, producer, phase(packet, "finish_response_sent", NS), NS)
    with pytest.raises(wire.ProtocolError, match="seek-state"):
        send(reader, producer, seek_event("seek_start"), NS)
    assert reader.failed


def test_deferred_restore_primitive_never_counts_as_a_normal_write() -> None:
    """Primitive restore admission cannot upgrade a failed capture to success."""
    reader, producer, _, packet = enter_context(3)
    completed_normal = reader._fixed_seeks.completed
    assert completed_normal == 1
    send(
        reader,
        producer,
        {"event": "failure", "context_id": 3, "reason": "observation"},
        NS,
    )
    send(
        reader,
        producer,
        {
            "event": "failure_restore_enter",
            "context_id": 3,
            "sequence_started_ns": NS,
            "sequence_deadline_ns": 31 * NS,
            "failure_ns": NS,
        },
        NS,
    )
    for phase_id, operation in ((1, "guard"), (2, "write")):
        send(
            reader,
            producer,
            {
                "event": "failure_restore_phase",
                "phase_id": phase_id,
                "operation": operation,
            },
            NS,
        )
    for kind in ("seek_start", "seek_end"):
        send(reader, producer, seek_event(kind, restoring=True), NS)
    assert reader._fixed_seeks.completed == completed_normal
    assert not reader.complete
    with pytest.raises(wire.ProtocolError, match="phase-not-clean"):
        send(reader, producer, phase(packet, "phase_end", NS), NS)


def rehash_peer_records(records: list[wire.EvidenceRecord]) -> list[bytes]:
    """Rebuild genuine framing IDs and final hash to isolate semantic audit failures."""
    frames: list[bytes] = []
    digest = hashlib.sha256()
    byte_count = 0
    for record_id, record in enumerate(records, 1):
        event = record.event
        if event["event"] == "final":
            event = {
                "event": "final",
                "preceding_records": record_id - 1,
                "preceding_bytes": byte_count,
                "preceding_sha256": digest.hexdigest(),
            }
        frame = wire.encode_evidence_record(
            wire.EvidenceRecord(record_id, record.monotonic_ns, event)
        )
        frames.append(frame)
        digest.update(frame)
        byte_count += len(frame)
    return frames


@pytest.mark.parametrize(
    "fault",
    [
        "missing_first",
        "missing_repeat",
        "missing_last",
        "duplicate",
        "wrong_step_target",
        "integer_target",
        "nonzero_status",
        "wrong_restoring",
        "begin_write",
        "observe_write",
        "finish_write",
    ],
)
def test_rehashed_fixed_seek_forgery_fails_independent_correlated_audit(  # noqa: C901 - explicit rehashed adversarial transcript matrix
    fault: str,
) -> None:
    """Valid framing, pairing and final hashes cannot hide an incorrect write set."""
    _, parents, peers = completed_transcripts()
    mutated: list[wire.EvidenceRecord] = []
    active_step, active_operation = 0, ""
    missing_step = {"missing_first": 1, "missing_repeat": 5, "missing_last": 7}.get(
        fault
    )
    for frame in peers:
        record = wire.decode_evidence_record(frame)
        event = dict(record.event)
        kind = event["event"]
        if kind == "phase_enter":
            active_step, active_operation = event["step_id"], event["operation"]
        if kind in ("seek_start", "seek_end"):
            if active_step == missing_step:
                continue
            if fault == "wrong_step_target" and active_step == 1:
                event["vertical"] = 100.0 * wire.FRAME_LINES[2] / 13
            elif fault == "integer_target" and active_step == 2:
                event["vertical"] = 0
            elif fault == "nonzero_status" and active_step == 1 and kind == "seek_end":
                event["status"] = 1
            elif fault == "wrong_restoring" and active_step == 7:
                event["restoring"] = False
        if kind == "phase_end" and (
            (fault == "begin_write" and active_operation == "begin")
            or (
                fault == "observe_write"
                and active_operation == "observe"
                and active_step == 1
            )
            or (fault == "finish_write" and active_operation == "finish")
        ):
            mutated.extend(
                wire.EvidenceRecord(1, record.monotonic_ns, seek_event(seek_kind))
                for seek_kind in ("seek_start", "seek_end")
            )
        mutated.append(wire.EvidenceRecord(1, record.monotonic_ns, event))
        if fault == "duplicate" and active_step == 1 and kind == "seek_end":
            mutated.extend(
                wire.EvidenceRecord(1, record.monotonic_ns, seek_event(seek_kind))
                for seek_kind in ("seek_start", "seek_end")
            )
    forged = rehash_peer_records(mutated)
    diagnostic = wire.EvidenceReplay()
    for frame in forged:
        diagnostic.feed(frame)
    diagnostic.eof()
    assert diagnostic.complete  # Proves this is not a hash, framing or pairing failure.
    with pytest.raises(wire.ProtocolError):
        correlated_audit(parents, forged)


@pytest.mark.parametrize("fault", ["missing_start", "duplicate_end"])
def test_correlated_fixed_seek_end_requires_its_own_unfinished_start(
    fault: str,
) -> None:
    """Stored replay rejects an unpaired result without relying on live admission."""
    _, parents, peers = completed_transcripts()
    replay = wire.EvidenceCorrelatedReplay(identity())
    for frame in parents:
        replay.feed_parent(frame)
    replay.parent_eof()
    for frame in peers:
        record = wire.decode_evidence_record(frame)
        kind = record.event["event"]
        if kind == "seek_start" and fault == "missing_start":
            continue
        if kind == "seek_end":
            if fault == "duplicate_end":
                replay.feed_peer(frame)
            rejected = wire.encode_evidence_record(
                wire.EvidenceRecord(
                    replay.peer.count + 1, record.monotonic_ns, record.event
                )
            )
            prefix = replay.peer.count, replay.peer.byte_count, replay.peer.hexdigest
            with pytest.raises(wire.ProtocolError, match="fixed-seek-unpaired"):
                replay.feed_peer(rejected)
            assert (
                replay.peer.count,
                replay.peer.byte_count,
                replay.peer.hexdigest,
            ) == prefix
            assert replay.failed
            assert not replay.complete
            break
        # Removing a start must not cause an earlier record-ID failure.
        replay.feed_peer(
            wire.encode_evidence_record(
                wire.EvidenceRecord(
                    replay.peer.count + 1, record.monotonic_ns, record.event
                )
            )
        )
    else:
        pytest.fail("The actual producer transcript did not contain a seek result")


def test_two_actual_transcripts_are_required_for_final_qualification() -> None:
    """Peer-only hashing cannot substitute for independently recorded parent context."""
    reader, parents, peers = completed_transcripts()
    single = wire.EvidenceReplay()
    for record in peers:
        single.feed(record)
    single.eof()
    reader.verify_replay(single)
    assert not reader.complete
    replay = correlated_audit(parents, peers)
    assert replay.complete
    assert replay.parent.count == 32
    assert replay.parent.byte_count <= 32768
    assert replay.status["audit_scope"] == "correlated-partial-order"
    assert replay.status["arrival_order_reconstructed"] is False
    assert replay.status["acknowledgments_reconstructed"] is False
    reader.verify_correlated_replay(replay)
    assert reader.complete
    assert reader.status["parent_sha256"] == replay.parent.hexdigest


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "swapped",
        "duplicate",
        "truncated",
        "digest",
        "session",
        "epoch",
        "bool",
        "expanded_deadline",
        "future_completion",
        "next_before_prior",
    ],
)
def test_parent_raw_replay_rejects_omissions_and_authority_mutations(
    fault: str,
) -> None:
    """Raw parent records retain exact context, timing and identity authority."""
    _, parents, peers = completed_transcripts()
    if fault == "missing":
        parents = parents[:-1]
    elif fault == "swapped":
        parents[0], parents[1] = parents[1], parents[0]
    elif fault == "duplicate":
        parents.insert(1, parents[0])
    elif fault == "truncated":
        parents[-1] = parents[-1][:-1]
    else:
        index = (
            1
            if fault == "future_completion"
            else 2
            if fault == "next_before_prior"
            else 0
        )
        original = wire.decode_evidence_record(parents[index], parent=True)
        event = dict(original.event)
        field, value = {
            "digest": ("command_sha256", "b" * 64),
            "session": ("session", "3" * 32),
            "epoch": ("epoch", "3" * 32),
            "bool": ("requested_ns", True),
            "expanded_deadline": ("deadline_ns", 30 * NS),
            "future_completion": ("completed_ns", original.monotonic_ns + 1),
            "next_before_prior": ("requested_ns", NS),
        }[fault]
        event[field] = value
        payload = wire.canonical_json(event)
        parents[index] = (
            struct.pack(">IIQ", len(payload), original.record_id, original.monotonic_ns)
            + payload
        )
    with pytest.raises(wire.ProtocolError):
        correlated_audit(parents, peers)


@pytest.mark.parametrize(
    "fault",
    [
        "digest",
        "context",
        "expanded_call",
        "early_enter",
        "late_end",
        "completion_order",
        "hello_identity",
    ],
)
def test_correlated_raw_replay_rejects_peer_context_and_native_ceiling_forgery(
    fault: str,
) -> None:
    """Self-consistent peer records still need the independently recorded context."""
    _, parents, peers = completed_transcripts()
    index = (
        0
        if fault == "hello_identity"
        else 2
        if fault == "expanded_call"
        else 4
        if fault in ("late_end", "completion_order")
        else 1
    )
    original = wire.decode_evidence_record(peers[index])
    event = dict(original.event)
    stamp = original.monotonic_ns
    if fault == "digest":
        event["command_sha256"] = "b" * 64
    elif fault == "context":
        event.update(context_id=2, request_id=2, operation="move", step_id=1)
    elif fault == "expanded_call":
        # Within native start+3s and the session, but outside begin's true2s cap.
        event["effective_deadline_ns"] = stamp + 3 * NS
    elif fault == "early_enter":
        stamp = NS
    elif fault == "late_end":
        stamp = 3 * NS + 100
        event["completed_ns"] = stamp
    elif fault == "completion_order":
        stamp = NS + 109
        event["completed_ns"] = stamp
    else:
        event["created_100ns"] = 101
    peers[index] = wire.encode_evidence_record(
        wire.EvidenceRecord(original.record_id, stamp, event)
    )
    with pytest.raises(wire.ProtocolError):
        correlated_audit(parents, peers)


def test_parent_record_cap_is_independent_of_the_peer_raw_budget() -> None:
    """Parent evidence has its own fixed32-packet/32KiB cap and accepted prefix."""
    _, parents, _ = completed_transcripts()
    replay = wire.ParentEvidenceReplay(identity())
    for record in parents:
        replay.feed(record)
    assert replay.count == 32
    digest = replay.hexdigest
    with pytest.raises(wire.ProtocolError):
        replay.feed(parents[0])
    assert replay.count == 32
    assert replay.hexdigest == digest
    assert wire.MAX_PARENT_EVIDENCE_BYTES == 32768


def test_parent_oversized_chunk_and_premature_peer_stream_fail_closed() -> None:
    """Bound parsing and require parent authority before peer audit."""
    replay = wire.ParentEvidenceReplay(identity())
    with pytest.raises(wire.ProtocolError):
        replay.feed(b"x" * 1025)
    combined = wire.EvidenceCorrelatedReplay(identity())
    with pytest.raises(wire.ProtocolError):
        combined.feed_peer(b"x")


@pytest.mark.parametrize("fault", ["consistent_parent_digest", "peer_native_ceiling"])
def test_valid_individual_transcripts_still_require_independent_correlation(
    fault: str,
) -> None:
    """Neither valid framing nor two complete individual hashes prove the context."""
    _, parents, peers = completed_transcripts()
    if fault == "consistent_parent_digest":
        for index in (0, 1):
            record = wire.decode_evidence_record(parents[index], parent=True)
            value = dict(record.event, command_sha256="b" * 64)
            parents[index] = wire.encode_evidence_record(
                wire.EvidenceRecord(record.record_id, record.monotonic_ns, value),
                parent=True,
            )
    else:
        record = wire.decode_evidence_record(peers[2])
        value = dict(record.event, effective_deadline_ns=record.monotonic_ns + 3 * NS)
        peers[2] = wire.encode_evidence_record(
            wire.EvidenceRecord(record.record_id, record.monotonic_ns, value)
        )
        final = wire.decode_evidence_record(peers[-1])
        value = dict(
            final.event,
            preceding_bytes=sum(map(len, peers[:-1])),
            preceding_sha256=hashlib.sha256(b"".join(peers[:-1])).hexdigest(),
        )
        peers[-1] = wire.encode_evidence_record(
            wire.EvidenceRecord(final.record_id, final.monotonic_ns, value)
        )
    parent_audit = wire.ParentEvidenceReplay(identity())
    for frame in parents:
        parent_audit.feed(frame)
    parent_audit.eof()
    peer_audit = wire.EvidenceReplay()
    for frame in peers:
        peer_audit.feed(frame)
    peer_audit.eof()
    assert parent_audit.complete
    assert peer_audit.complete
    with pytest.raises(wire.ProtocolError):
        correlated_audit(parents, peers)


@pytest.mark.parametrize(
    "fault",
    [
        "context_is_completion",
        "completion_is_context",
        "expanded_move",
        "finish_dispatch_expired",
        "retirement_start",
        "backwards_stamp",
        "wrong_framing",
    ],
)
def test_parent_audit_guards_packet_roles_phase_caps_and_original_time(
    fault: str,
) -> None:
    """Strict parent replay rejects well-shaped packets at invalid causal positions."""
    _, parents, _ = completed_transcripts()
    index = {
        "context_is_completion": 0,
        "completion_is_context": 1,
        "expanded_move": 2,
        "finish_dispatch_expired": 30,
        "retirement_start": 31,
        "backwards_stamp": 0,
        "wrong_framing": 0,
    }[fault]
    record = wire.decode_evidence_record(parents[index], parent=True)
    event, stamp = dict(record.event), record.monotonic_ns
    if fault == "context_is_completion":
        event = wire.decode_evidence_record(parents[1], parent=True).event
    elif fault == "completion_is_context":
        event = wire.decode_evidence_record(parents[0], parent=True).event
    elif fault == "expanded_move":
        event["deadline_ns"] = 5 * NS
    elif fault == "finish_dispatch_expired":
        stamp = event["response_deadline_ns"]
    elif fault == "retirement_start":
        event["retirement_started_ns"] = 4 * NS
    elif fault == "backwards_stamp":
        stamp = NS - 1
    parents[index] = wire.encode_evidence_record(
        wire.EvidenceRecord(record.record_id, stamp, event), parent=True
    )
    if fault == "wrong_framing":
        parents[0] = struct.pack(">IIQ", 1024, 1, NS) + b"x"
    replay = wire.ParentEvidenceReplay(identity())
    with pytest.raises(wire.ProtocolError):  # noqa: PT012 - stream until the corrupt packet
        for frame in parents:
            replay.feed(frame)
    assert replay.count == index


def test_parent_audit_type_prefix_lookup_and_cross_stream_order_guards() -> None:
    """No incomplete authority object or stream reordering can become a full audit."""
    with pytest.raises(wire.ProtocolError):
        wire.ParentEvidenceReplay(cast("Any", identity().value))
    parent = wire.ParentEvidenceReplay(identity())
    with pytest.raises(wire.ProtocolError):
        parent.context_pair(1)
    _, parents, peers = completed_transcripts()
    audit = wire.EvidenceCorrelatedReplay(identity())
    for frame in parents:
        audit.feed_parent(frame)
    audit.parent_eof()
    audit.feed_peer(peers[0])
    with pytest.raises(wire.ProtocolError):
        audit.feed_parent(b"")
    incomplete = wire.EvidenceCorrelatedReplay(identity())
    with pytest.raises(wire.ProtocolError):
        incomplete.peer_eof()


def test_parent_byte_overflow_preserves_the_last_complete_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check the parent byte budget before accepting more raw data."""
    _, parents, _ = completed_transcripts()
    replay = wire.ParentEvidenceReplay(identity())
    replay.feed(parents[0])
    before = replay.status
    monkeypatch.setattr(wire, "MAX_PARENT_EVIDENCE_BYTES", replay.byte_count)
    with pytest.raises(wire.ProtocolError):
        replay.feed(parents[1])
    assert replay.count == before["count"]
    assert replay.byte_count == before["byte_count"]
    assert replay.hexdigest == before["sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "failed_prefix",
        "phase_completed_before_start",
        "observe_accepted_after_end",
        "late_finish_marker",
    ],
)
def test_correlated_replay_checks_completion_order_and_finish_response_cap(
    fault: str,
) -> None:
    """Enforce known command, response and acceptance order."""
    _, parents, peers = completed_transcripts()
    if fault == "observe_accepted_after_end":
        record = wire.decode_evidence_record(parents[5], parent=True)
        event = dict(record.event, completed_ns=NS + 308)
        parents[5] = wire.encode_evidence_record(
            wire.EvidenceRecord(record.record_id, NS + 308, event), parent=True
        )
    else:
        index = 4
        if fault == "late_finish_marker":
            index = next(
                index
                for index, data in enumerate(peers)
                if wire.decode_evidence_record(data).event["event"]
                == "finish_response_sent"
            )
        record = wire.decode_evidence_record(peers[index])
        event, stamp = dict(record.event), record.monotonic_ns
        if fault == "failed_prefix":
            event = {"event": "failure", "context_id": 1, "reason": "observation"}
        elif fault == "phase_completed_before_start":
            event["completed_ns"] = NS + 100
        else:
            stamp = NS + 1600 + 2 * NS
        peers[index] = wire.encode_evidence_record(
            wire.EvidenceRecord(record.record_id, stamp, event)
        )
    with pytest.raises(wire.ProtocolError, match="correlated-replay"):
        correlated_audit(parents, peers)


def test_live_verification_rejects_a_different_valid_parent_transcript() -> None:
    """Offline consistency cannot replace the live accepted parent byte digest."""
    reader, parents, peers = completed_transcripts()
    original = wire.decode_evidence_record(parents[0], parent=True)
    value = dict(original.event, requested_ns=NS + 99, deadline_ns=3 * NS + 99)
    parents[0] = wire.encode_evidence_record(
        wire.EvidenceRecord(original.record_id, original.monotonic_ns, value),
        parent=True,
    )
    audit = correlated_audit(parents, peers)
    assert audit.complete
    with pytest.raises(wire.ProtocolError, match="correlated-replay-mismatch"):
        reader.verify_correlated_replay(audit)
    assert not reader.complete
