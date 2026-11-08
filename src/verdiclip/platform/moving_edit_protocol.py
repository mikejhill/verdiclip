"""Separate bounded protocol4 for the fixed owned EDIT seven-position bridge.

The framing and metadata types do not authorize native writes or prove process
identity. Their lifecycle owner supplies pinned peers, original deadlines, real
EOF, and completed-write evidence. Old sequence/WPF contracts remain untouched.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, cast, override

from verdiclip.capture.edit_observation import (
    IMAGE_BYTES,
    MAX_PID,
    MAX_STAMP,
    EditObservation,
    array,
    canonical_json,
    constant,
    digest_hex,
    epoch,
    fields,
    integer,
    parse_json,
    validate_native_state,
    validate_profile_evidence,
    validate_target,
)
from verdiclip.capture.edit_scroll_contract import FRAME_LINES
from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.platform.helper_protocol import ProtocolError

VERSION = 4
PEER_SCHEMA = 5
RUNTIME_SCHEMA = 6
MOVING_SCHEMA = 1
CREDIT_SCHEMA = 2
MAX_MESSAGE_BYTES = 65_536
MAX_OBSERVATION_BYTES = 32_768
HEADER_BYTES = 4
MAX_READ_BUFFER_BYTES = MAX_MESSAGE_BYTES + HEADER_BYTES
MAX_REQUESTS = 16
MAX_SEQUENCE_FRAMES = 7
MAX_SEQUENCE_CONTROL_RECORDS = 512
MAX_FRAME_CONTROL_RECORDS = 64
MAX_CREDIT_RECORD_BYTES = 512
MAX_MOVE_ACK_BYTES = 20_480
MAX_FRAME_PROOF_BYTES = 49_152
MAX_FINAL_RECEIPT_BYTES = 16_384
MAX_LAUNCH_BYTES = 1024
MAX_MANIFEST_BYTES = 16_384
MAX_PATH_CHARS = 2048
DIGEST_BYTES = 32
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4096
MOVE_NS = 3_000_000_000
OBSERVE_NS = 2_000_000_000
SEQUENCE_NS = 30_000_000_000
RESTORE_RESERVE_NS = 8_000_000_000
SequenceObservation = EditObservation
MovingEditObservation = EditObservation


@dataclass(frozen=True)
class Message:
    """One bounded envelope; transfer thread boundaries as serialized bytes."""

    kind: str
    request_id: int
    payload: dict[str, object]
    version: int = VERSION


def decode(payload: bytes) -> Message:
    """Reject noncanonical UTF-8 and unsupported envelope fields or IDs."""
    value = parse_json(payload, MAX_MESSAGE_BYTES)
    fields(value, "version kind request_id payload")
    constant(value["version"], VERSION)
    request_id = integer(value["request_id"], 0, MAX_REQUESTS)
    if (
        type(value["kind"]) is not str
        or value["kind"]
        not in (
            "ready",
            "request",
            "response",
            "frame_wait",
            "frame_credit",
            "frame_accept",
        )
        or type(value["payload"]) is not dict
    ):
        raise ProtocolError("invalid-envelope")
    if (value["kind"] == "ready") != (request_id == 0):
        raise ProtocolError("invalid-envelope-id")
    if (
        value["kind"] in ("frame_wait", "frame_credit")
        and len(payload) + HEADER_BYTES > MAX_CREDIT_RECORD_BYTES
    ):
        raise ProtocolError("oversized-credit-record")
    return Message(value["kind"], request_id, value["payload"], VERSION)


def encode(message: Message) -> bytes:
    """Length-prefix validated canonical JSON under the moving-only cap."""
    if type(message) is not Message:
        raise ProtocolError("invalid-message-type")
    payload = canonical_json(
        {
            "kind": message.kind,
            "request_id": message.request_id,
            "payload": message.payload,
            "version": message.version,
        },
        MAX_MESSAGE_BYTES,
    )
    decode(payload)
    return struct.pack(">I", len(payload)) + payload


encode_message = encode
decode_message = decode


class Decoder:
    """Bound each read/batch to 65,540 bytes and latch malformed input forever."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> list[Message]:
        """Admit fragmentation or bounded coalescing before extending the buffer."""
        if self.failed:
            raise ProtocolError("decoder-failed")
        try:
            if (
                type(data) is not bytes
                or len(data) + len(self.buffer) > MAX_READ_BUFFER_BYTES
            ):
                raise ProtocolError("buffer-limit")  # noqa: TRY301 - latch before buffer extension
            self.buffer.extend(data)
            messages = []
            while len(self.buffer) >= HEADER_BYTES:
                size = struct.unpack(">I", self.buffer[:HEADER_BYTES])[0]
                if not 0 < size <= MAX_MESSAGE_BYTES:
                    raise ProtocolError("oversized")  # noqa: TRY301 - latch malformed framing
                total = HEADER_BYTES + size
                if len(self.buffer) < total:
                    break
                messages.append(decode(bytes(self.buffer[HEADER_BYTES:total])))
                del self.buffer[:total]
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise
        return messages


class SequenceBudget:
    """Count both directions before queueing, never replenish across frames."""

    def __init__(self) -> None:
        self.records = 0
        self.frames = [0] * MAX_SEQUENCE_FRAMES
        self.failed = False

    def _frame(self, frame_id: int) -> int:
        if (
            type(frame_id) is not int
            or not 1 <= frame_id <= MAX_SEQUENCE_FRAMES
            or self.frames[frame_id - 1] >= MAX_FRAME_CONTROL_RECORDS
        ):
            self.failed = True
            raise ProtocolError("sequence-frame-record-bound")
        return frame_id - 1

    def record(self, frame_id: int | None = None) -> None:
        """Reserve exactly one globally bounded inbound or outbound record."""
        if self.failed or self.records >= MAX_SEQUENCE_CONTROL_RECORDS:
            self.failed = True
            raise ProtocolError("sequence-record-bound")
        index = None if frame_id is None else self._frame(frame_id)
        self.records += 1
        if index is not None:
            self.frames[index] += 1

    def attribute_frame(self, frame_id: int) -> None:
        """Classify an already-reserved record after its frame is decoded."""
        if self.failed:
            raise ProtocolError("sequence-budget-failed")
        self.frames[self._frame(frame_id)] += 1


@dataclass(frozen=True, slots=True)
class MovingEditLaunch:
    """Only fixed packaged moving profiles and canonical session data are allowed."""

    pipe_name: str
    epoch: str
    parent_pid: int
    sequence_started_ns: int
    sequence_deadline_ns: int
    evidence_token: str
    evidence_reader_pid: int
    evidence_reader_created_100ns: int
    evidence_schema: int = 1
    protocol_version: int = VERSION
    peer_schema: int = PEER_SCHEMA
    credit_schema: int = CREDIT_SCHEMA
    moving_schema: int = MOVING_SCHEMA
    runtime_schema: int = RUNTIME_SCHEMA

    def __post_init__(self) -> None:
        """Validate the separate namespace, nonzero nonces and exact versions."""
        if (
            type(self.pipe_name) is not str
            or re.fullmatch(r"VerdiClip-moving-edit-[0-9a-f]{32}", self.pipe_name)
            is None
        ):
            raise ProtocolError("invalid-moving-pipe-name")
        epoch(self.pipe_name.removeprefix("VerdiClip-moving-edit-"))
        epoch(self.epoch)
        integer(self.parent_pid, 1, MAX_PID)
        _evidence_stamps(self.sequence_started_ns, self.sequence_deadline_ns)
        EvidenceBinding(
            self.evidence_token,
            self.evidence_reader_pid,
            self.evidence_reader_created_100ns,
        )
        constant(self.evidence_schema, 1)
        for actual, expected in (
            (self.protocol_version, VERSION),
            (self.peer_schema, PEER_SCHEMA),
            (self.credit_schema, CREDIT_SCHEMA),
            (self.moving_schema, MOVING_SCHEMA),
            (self.runtime_schema, RUNTIME_SCHEMA),
        ):
            constant(actual, expected)

    def validate_time(self, now_ns: int) -> None:
        """Recheck original worker stamps without replacing or extending them."""
        _evidence_stamps(self.sequence_started_ns, self.sequence_deadline_ns, now_ns)

    def serialize(self) -> str:
        """Encode exactly the checked launch schema with its independent cap."""
        return canonical_json(asdict(self), MAX_LAUNCH_BYTES).decode("utf-8")


SequenceLaunch = MovingEditLaunch


def deserialize_launch(payload: str) -> MovingEditLaunch:
    """Reject alternate profiles and ambiguous launch JSON before native I/O."""
    if type(payload) is not str:
        raise ProtocolError("launch-type")
    value = parse_json(payload.encode("utf-8"), MAX_LAUNCH_BYTES)
    fields(
        value,
        "pipe_name epoch parent_pid protocol_version peer_schema "
        "credit_schema moving_schema runtime_schema sequence_started_ns "
        "sequence_deadline_ns evidence_schema evidence_token evidence_reader_pid "
        "evidence_reader_created_100ns",
    )
    return MovingEditLaunch(**value)


def frame_pipe_name(pipe_name: str, frame_id: int) -> str:
    """Derive fixed frame paths from the control nonce, never a response path."""
    integer(frame_id, 1, 7)
    if (
        type(pipe_name) is not str
        or re.fullmatch(r"VerdiClip-moving-edit-[0-9a-f]{32}", pipe_name) is None
    ):
        raise ProtocolError("invalid-moving-pipe-name")
    epoch(pipe_name.removeprefix("VerdiClip-moving-edit-"))
    return f"{pipe_name}-frame-{frame_id}"


def validate_ready(value: object) -> dict[str, Any]:
    """Check the complete ready record before trusting the helper profile."""
    ready = fields(
        value,
        "pid executable epoch protocol_version peer_schema credit_schema "
        "moving_schema runtime_schema max_frames max_requests",
    )
    integer(ready["pid"], 1, MAX_PID)
    epoch(ready["epoch"])
    if (
        type(ready["executable"]) is not str
        or not 0 < len(ready["executable"]) <= MAX_PATH_CHARS
    ):
        raise ProtocolError("ready-executable-bound")
    for name, expected in {
        "protocol_version": VERSION,
        "peer_schema": PEER_SCHEMA,
        "credit_schema": CREDIT_SCHEMA,
        "moving_schema": MOVING_SCHEMA,
        "runtime_schema": RUNTIME_SCHEMA,
        "max_frames": 7,
        "max_requests": 16,
    }.items():
        constant(ready[name], expected)
    return parse_json(canonical_json(ready), MAX_MESSAGE_BYTES)


def _phase_limit(session_deadline_ns: int, step_id: int) -> int:
    integer(session_deadline_ns, 1, MAX_STAMP)
    integer(step_id, 1, 7)
    return (
        session_deadline_ns
        if step_id == MAX_SEQUENCE_FRAMES
        else session_deadline_ns - RESTORE_RESERVE_NS
    )


def _stamps(
    value: dict[str, Any],
    duration: int,
    session_deadline_ns: int | None,
    now_ns: int | None,
) -> None:
    requested = integer(value["requested_ns"], 0, MAX_STAMP)
    deadline = integer(value["deadline_ns"], 1, MAX_STAMP)
    if not requested < deadline <= min(requested + duration, MAX_STAMP):
        raise ProtocolError("invalid-original-operation-stamp")
    if session_deadline_ns is not None and deadline != min(
        requested + duration, _phase_limit(session_deadline_ns, value["step_id"])
    ):
        raise ProtocolError("operation-deadline-reset")
    if now_ns is not None and not requested <= integer(now_ns, 0, MAX_STAMP) < deadline:
        raise ProtocolError("expired-operation-admission")


def _command_stamps(
    value: dict[str, Any],
    sequence_started_ns: int | None,
    sequence_deadline_ns: int | None,
    now_ns: int | None,
    *,
    reserve: bool = False,
) -> None:
    started = integer(value["command_started_ns"], 0, MAX_STAMP)
    deadline = integer(value["command_deadline_ns"], 1, MAX_STAMP)
    if not started < deadline <= min(started + OBSERVE_NS, MAX_STAMP):
        raise ProtocolError("invalid-original-command-stamps")
    if sequence_started_ns is not None and started < integer(
        sequence_started_ns, 0, MAX_STAMP
    ):
        raise ProtocolError("command-precedes-sequence")
    if sequence_deadline_ns is not None:
        ceiling = integer(sequence_deadline_ns, 1, MAX_STAMP)
        if reserve:
            ceiling -= RESTORE_RESERVE_NS
        constant(deadline, min(started + OBSERVE_NS, ceiling))
    if now_ns is not None and not started <= integer(now_ns, 0, MAX_STAMP) < deadline:
        raise ProtocolError("expired-original-command")


def validate_finish_request(
    value: object,
    *,
    sequence_started_ns: int | None = None,
    sequence_deadline_ns: int | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Validate the parent's original finish dispatch2s without a peer restamp."""
    finish = fields(value, "operation command_started_ns command_deadline_ns")
    constant(finish["operation"], "moving_finish")
    _command_stamps(finish, sequence_started_ns, sequence_deadline_ns, now_ns)
    return parse_json(canonical_json(finish), MAX_MESSAGE_BYTES)


def validate_begin_payload(
    value: object, *, now_ns: int | None = None
) -> dict[str, Any]:
    """Validate original worker-entry stamps, not a fresh provider session clock."""
    begin = fields(
        value,
        "operation target profile_evidence sequence_started_ns sequence_deadline_ns "
        "command_started_ns command_deadline_ns",
    )
    constant(begin["operation"], "moving_begin")
    target = validate_target(begin["target"])
    validate_profile_evidence(begin["profile_evidence"], target)
    started = integer(begin["sequence_started_ns"], 0, MAX_STAMP)
    deadline = integer(begin["sequence_deadline_ns"], 1, MAX_STAMP)
    if not started < deadline <= min(started + SEQUENCE_NS, MAX_STAMP):
        raise ProtocolError("invalid-original-session-stamp")
    if now_ns is not None and not started <= integer(now_ns, 0, MAX_STAMP) < deadline:
        raise ProtocolError("expired-session-admission")
    _command_stamps(begin, started, deadline, now_ns, reserve=True)
    return parse_json(canonical_json(begin), MAX_MESSAGE_BYTES)


def validate_begin_reply(
    value: object,
    *,
    sequence_started_ns: int | None = None,
    sequence_deadline_ns: int | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Check the echoed immutable stamps; caller also compares its exact originals."""
    reply = fields(
        value,
        "operation epoch sequence_started_ns sequence_deadline_ns "
        "baseline_identity_sha256",
    )
    constant(reply["operation"], "moving_begun")
    epoch(reply["epoch"])
    digest_hex(reply["baseline_identity_sha256"])
    started = integer(reply["sequence_started_ns"], 0, MAX_STAMP)
    deadline = integer(reply["sequence_deadline_ns"], 1, MAX_STAMP)
    if not started < deadline <= min(started + SEQUENCE_NS, MAX_STAMP):
        raise ProtocolError("invalid-original-session-stamp")
    if sequence_started_ns is not None:
        constant(started, integer(sequence_started_ns, 0, MAX_STAMP))
    if sequence_deadline_ns is not None:
        constant(deadline, integer(sequence_deadline_ns, 1, MAX_STAMP))
    if now_ns is not None and not started <= integer(now_ns, 0, MAX_STAMP) < deadline:
        raise ProtocolError("expired-session-acknowledgment")
    return parse_json(canonical_json(reply), MAX_MESSAGE_BYTES)


def validate_move_request(
    value: object, *, session_deadline_ns: int | None = None, now_ns: int | None = None
) -> dict[str, Any]:
    """Only a fixed next step is representable; caller verifies state-machine order."""
    move = fields(value, "operation step_id requested_ns deadline_ns")
    constant(move["operation"], "move_next")
    integer(move["step_id"], 1, 7)
    _stamps(move, MOVE_NS, session_deadline_ns, now_ns)
    return parse_json(canonical_json(move), MAX_MESSAGE_BYTES)


def validate_move_ack(
    value: object, *, session_deadline_ns: int | None = None, now_ns: int | None = None
) -> dict[str, Any]:
    """Bind actual completed write/readback stamps and full native state."""
    ack = parse_json(canonical_json(value, MAX_MOVE_ACK_BYTES), MAX_MOVE_ACK_BYTES)
    fields(
        ack,
        "operation epoch request_id step_id expected_line requested_ns "
        "deadline_ns write_started_ns write_returned_ns readback_ns "
        "completed_ns restoring native_state native_state_sha256 "
        "baseline_identity_sha256",
    )
    constant(ack["operation"], "move_completed")
    epoch(ack["epoch"])
    step = integer(ack["step_id"], 1, 7)
    constant(ack["request_id"], 2 * step)
    constant(ack["expected_line"], FRAME_LINES[step - 1])
    constant(ack["restoring"], step == MAX_SEQUENCE_FRAMES)
    _stamps(ack, MOVE_NS, session_deadline_ns, now_ns)
    stamps = [
        integer(ack[name], 0, MAX_STAMP)
        for name in (
            "requested_ns",
            "write_started_ns",
            "write_returned_ns",
            "readback_ns",
            "completed_ns",
        )
    ]
    if (
        stamps != sorted(stamps)
        or stamps[-1] >= ack["deadline_ns"]
        or (now_ns is not None and stamps[-1] > now_ns)
    ):
        raise ProtocolError("move-write-readback-order")
    state = validate_native_state(ack["native_state"], ack["expected_line"])
    digest_hex(ack["baseline_identity_sha256"])
    if (
        digest_hex(ack["native_state_sha256"])
        != hashlib.sha256(canonical_json(state)).hexdigest()
    ):
        raise ProtocolError("move-native-state-digest")
    return ack


def move_digest(value: object) -> str:
    """Hash only a checked acknowledgment, binding the immediately following frame."""
    return hashlib.sha256(
        canonical_json(validate_move_ack(value), MAX_MOVE_ACK_BYTES)
    ).hexdigest()


def validate_frame_request(
    value: object, *, session_deadline_ns: int | None = None, now_ns: int | None = None
) -> dict[str, Any]:
    """Retain the original two-second frame cap through acceptance commitment."""
    request = fields(
        value, "operation frame_id step_id move_sha256 requested_ns deadline_ns"
    )
    constant(request["operation"], "observe")
    step = integer(request["step_id"], 1, 7)
    constant(request["frame_id"], step)
    digest_hex(request["move_sha256"])
    _stamps(request, OBSERVE_NS, session_deadline_ns, now_ns)
    return parse_json(canonical_json(request), MAX_MESSAGE_BYTES)


@dataclass
class SequenceDeadline:
    """One session clock, with a prequeue move/frame stamp and permanent failure."""

    deadline_ns: int
    frame_deadline_ns: int | None = None
    last_ns: int = 0
    failed: bool = False
    failure: Exception | None = None

    def check(self) -> int:
        """Enforce the original ceiling even when a phase deadline is supplied."""
        if self.failure is not None:
            raise self.failure
        try:
            now = time.monotonic_ns()
            integer(self.deadline_ns, 1, MAX_STAMP)
            integer(now, 0, MAX_STAMP)
            cap = self.deadline_ns
            if self.frame_deadline_ns is not None:
                cap = min(cap, integer(self.frame_deadline_ns, 1, MAX_STAMP))
            if self.failed or not self.last_ns <= now < cap:
                raise ProtocolError("expired-sequence-deadline")  # noqa: TRY301 - permanently preserve clock failure
            self.last_ns = now
        except Exception as error:
            self.failed, self.failure = True, error
            raise
        return now

    def phase_limit(self, step_id: int) -> int:
        """Leave the final eight seconds for restore, acceptance and retirement."""
        return _phase_limit(self.deadline_ns, step_id)

    def _phase(
        self, requested_ns: object, deadline_ns: object, step_id: int, duration: int
    ) -> None:
        try:
            now = self.check()
            _stamps(
                {
                    "requested_ns": requested_ns,
                    "deadline_ns": deadline_ns,
                    "step_id": step_id,
                },
                duration,
                self.deadline_ns,
                now,
            )
            self.frame_deadline_ns = integer(deadline_ns, 1, MAX_STAMP)
            self.check()
        except Exception as error:
            self.failed, self.failure = True, self.failure or error
            raise self.failure from error

    def frame(
        self, requested_ns: object, deadline_ns: object, *, step_id: int = 1
    ) -> None:
        """Admit only the original frame stamp; no reset after stream EOF."""
        self._phase(requested_ns, deadline_ns, step_id, OBSERVE_NS)

    def move(
        self, requested_ns: object, deadline_ns: object, *, step_id: int = 1
    ) -> None:
        """Include queueing, seek and readback in the original three seconds."""
        self._phase(requested_ns, deadline_ns, step_id, MOVE_NS)


@dataclass(frozen=True, slots=True)
class MovingFrameProof:
    """Bounded full acceptance proof, with the exact moving observation inside."""

    canonical: bytes

    def __post_init__(self) -> None:
        """Reject mismatched headers, stamps, credits and move correlations."""
        value = self.value
        fields(
            value,
            "proof_schema epoch request_id frame_id step_id move_sha256 "
            "baseline_identity_sha256 requested_ns deadline_ns verified_ns "
            "header_sha256 observation_sha256 image_sha256 bytes credit_chunks "
            "granted_bytes accepted_bytes credit_events observation",
        )
        constant(value["proof_schema"], 1)
        step = integer(value["step_id"], 1, 7)
        constant(value["frame_id"], step)
        constant(value["request_id"], 2 * step + 1)
        epoch(value["epoch"])
        _stamps(value, OBSERVE_NS, None, None)
        verified = integer(value["verified_ns"], 0, MAX_STAMP)
        if not value["requested_ns"] <= verified < value["deadline_ns"]:
            raise ProtocolError("frame-proof-deadline")
        for name in (
            "header_sha256",
            "observation_sha256",
            "image_sha256",
            "move_sha256",
            "baseline_identity_sha256",
        ):
            digest_hex(value[name])
        for name in ("bytes", "granted_bytes", "accepted_bytes"):
            constant(value[name], IMAGE_BYTES)
        constant(value["credit_chunks"], 10)
        observation = self.observation
        observed = observation.value
        identity = observation.frame_identity()
        header = FrameHeader(identity, bytes.fromhex(value["image_sha256"]))
        for name in (
            "epoch",
            "request_id",
            "frame_id",
            "step_id",
            "move_sha256",
            "baseline_identity_sha256",
        ):
            constant(value[name], observed[name])
        if (
            value["observation_sha256"] != observation.digest.hex()
            or value["image_sha256"] != observed["image"]["sha256"]
            or value["header_sha256"] != hashlib.sha256(header.encode()).hexdigest()
        ):
            raise ProtocolError("frame-proof-correlation")
        acquisition = observed["acquisition"]
        if (
            not value["requested_ns"]
            <= acquisition["started_ns"]
            <= acquisition["finished_ns"]
            <= verified
        ):
            raise ProtocolError("frame-proof-acquisition-order")
        self._validate_events(value)

    @staticmethod
    def _validate_events(value: dict[str, Any]) -> None:
        events = array(value["credit_events"], 20, 20)
        last = value["observation"]["acquisition"]["finished_ns"]
        for index, raw_event in enumerate(events):
            event = array(raw_event, 4, 4)
            constant(event[0], index + 1)
            stamp = integer(event[1], 0, MAX_STAMP)
            constant(event[2], "wait" if index % 2 == 0 else "grant")
            constant(event[3], index // 2)
            if not last <= stamp <= value["verified_ns"]:
                raise ProtocolError("frame-proof-event-order")
            last = stamp

    @property
    def value(self) -> dict[str, Any]:
        """Return detached metadata only after canonical size/depth checks."""
        return parse_json(self.canonical, MAX_FRAME_PROOF_BYTES)

    @property
    def observation(self) -> EditObservation:
        """Validate the complete EDIT-specific schema, not a relabeled WPF frame."""
        return EditObservation.from_value(self.value["observation"])

    @classmethod
    def parse(cls, data: bytes) -> MovingFrameProof:
        """Validate untrusted immutable proof bytes."""
        return cls(data)

    @property
    def digest(self) -> bytes:
        """Bind exact proof bytes, including actual observe request ID."""
        return hashlib.sha256(self.canonical).digest()


SequenceFrameProof = MovingFrameProof


def parse_frame_proof(data: bytes) -> dict[str, object]:
    """Validate full private acceptance evidence before returning its metadata."""
    return cast("dict[str, object]", MovingFrameProof(data).value)


def encode_frame_proof(value: dict[str, object]) -> bytes:
    """Canonicalize and validate locally constructed proof metadata."""
    return MovingFrameProof(canonical_json(value, MAX_FRAME_PROOF_BYTES)).canonical


def proof_digest(data: bytes) -> bytes:
    """Hash only a validated canonical full proof."""
    return MovingFrameProof(data).digest


def validate_finish_reply(value: object) -> dict[str, Any]:
    """Check bounded provisional completion; parent recomputes from its own bytes."""
    result = parse_json(
        canonical_json(value, MAX_FINAL_RECEIPT_BYTES), MAX_FINAL_RECEIPT_BYTES
    )
    fields(
        result, "operation epoch accepted_frames sequence_sha256 restoration assembly"
    )
    constant(result["operation"], "moving_finished")
    epoch(result["epoch"])
    constant(result["accepted_frames"], 7)
    digest_hex(result["sequence_sha256"])
    restore = fields(
        result["restoration"],
        "status step_id initial_frame_id restored_frame_id state_equal "
        "root_equal client_equal",
    )
    for name, expected in {
        "status": "verified",
        "step_id": 7,
        "initial_frame_id": 1,
        "restored_frame_id": 7,
        "state_equal": True,
        "root_equal": True,
        "client_equal": True,
    }.items():
        constant(restore[name], expected)
    assembly = fields(
        result["assembly"],
        "width height byte_count sha256 source_frame_ids row_provenance_sha256",
    )
    for name, expected in (("width", 493), ("height", 621), ("byte_count", 918459)):
        constant(assembly[name], expected)
    for actual, expected in zip(
        array(assembly["source_frame_ids"], 3, 3), (2, 3, 4), strict=True
    ):
        constant(actual, expected)
    digest_hex(assembly["sha256"])
    digest_hex(assembly["row_provenance_sha256"])
    return result


def validate_frame_accept(
    value: object,
    *,
    previous_chain: bytes | None = None,
    expected_observation: EditObservation | None = None,
    expected_header: FrameHeader | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Validate full proof, summary and optional retained peer correlations.

    The lifecycle owner supplies the previous chain and its retained observation,
    header and actual credit/EOF state. Summary-only acceptance cannot be parsed.
    """
    accepted = parse_json(canonical_json(value), MAX_MESSAGE_BYTES)
    fields(accepted, "epoch frame_id step_id proof_sha256 sequence_sha256 proof")
    proof = MovingFrameProof(canonical_json(accepted["proof"], MAX_FRAME_PROOF_BYTES))
    observed = proof.value
    for name in ("epoch", "frame_id", "step_id"):
        constant(accepted[name], observed[name])
    if digest_hex(accepted["proof_sha256"]) != proof.digest.hex():
        raise ProtocolError("frame-accept-proof-digest")
    digest_hex(accepted["sequence_sha256"])
    if previous_chain is not None:
        if type(previous_chain) is not bytes or len(previous_chain) != DIGEST_BYTES:
            raise ProtocolError("invalid-previous-chain")
        if (
            hashlib.sha256(previous_chain + proof.digest).hexdigest()
            != accepted["sequence_sha256"]
        ):
            raise ProtocolError("frame-accept-chain")
    if expected_observation is not None and proof.observation != expected_observation:
        raise ProtocolError("frame-accept-observation")
    if (
        expected_header is not None
        and hashlib.sha256(expected_header.encode()).hexdigest()
        != observed["header_sha256"]
    ):
        raise ProtocolError("frame-accept-header")
    if (
        now_ns is not None
        and not observed["verified_ns"]
        <= integer(now_ns, 0, MAX_STAMP)
        < observed["deadline_ns"]
    ):
        raise ProtocolError("frame-accept-expired")
    # Include all wrapping bytes in the same isolated control cap.
    encode(Message("frame_accept", observed["request_id"], accepted))
    return accepted


# These bounds are independent of the 512-record control/credit protocol.
EVIDENCE_SCHEMA = 1
EVIDENCE_HEADER_BYTES = 16
EVIDENCE_ACK_BYTES = 80
MAX_EVIDENCE_RECORD_BYTES = 4096
MAX_EVIDENCE_RECORDS = 32_768
MAX_EVIDENCE_BYTES = 4_000_000
MAX_EVIDENCE_DEPTH = 32
MAX_PARENT_EVIDENCE_RECORD_BYTES = 1024
MAX_PARENT_EVIDENCE_RECORDS = 32
MAX_PARENT_EVIDENCE_BYTES = 32_768
MAX_EVIDENCE_ACK_BYTES = MAX_EVIDENCE_RECORDS * EVIDENCE_ACK_BYTES
_EVIDENCE_ACK = struct.Struct(">4sHH16s16sI32sI")
_EVIDENCE_HEADER = struct.Struct(">IIQ")
_EVIDENCE_IDENTITY_FIELDS = (
    "schema session epoch sequence_started_ns sequence_deadline_ns peer_pid "
    "peer_created_100ns peer_executable parent_pid parent_created_100ns "
    "parent_executable"
)
_CONTEXT_CORRELATION = "context_id operation request_id step_id command_sha256"
_PARENT_CORRELATION = "schema session epoch " + _CONTEXT_CORRELATION


def _evidence_stamps(start: object, deadline: object, now: object = None) -> None:
    start = integer(start, 0, MAX_STAMP)
    deadline = integer(deadline, 1, MAX_STAMP)
    if not start < deadline <= min(start + SEQUENCE_NS, MAX_STAMP):
        raise ProtocolError("evidence-original-sequence-stamps")
    if now is not None and not start <= integer(now, 0, MAX_STAMP) < deadline:
        raise ProtocolError("evidence-expired-sequence")


def evidence_auth_deadline_ns(
    sequence_started_ns: int,
    sequence_deadline_ns: int,
    *,
    now_ns: int | None = None,
) -> int:
    """Keep hello authentication inside the worker's original startup3s ceiling."""
    _evidence_stamps(sequence_started_ns, sequence_deadline_ns, now_ns)
    deadline = min(sequence_started_ns + MOVE_NS, sequence_deadline_ns)
    if now_ns is not None and now_ns >= deadline:
        raise ProtocolError("evidence-authentication-expired")
    return deadline


def _evidence_path(value: object) -> str:
    if (
        type(value) is not str
        or not 0 < len(value) <= MAX_PATH_CHARS
        or "\x00" in value
    ):
        raise ProtocolError("evidence-image-path")
    # The runtime additionally checks the full pinned kernel image; never basename.
    if re.match(r"^[A-Za-z]:[\\/]", value) is None:
        raise ProtocolError("evidence-image-not-absolute")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """Outer-owned reader identity supplied before the worker launches a peer."""

    token: str
    reader_pid: int
    reader_created_100ns: int

    def __post_init__(self) -> None:
        """Reject ambiguous tokens, identities and original deadline values."""
        epoch(self.token)
        integer(self.reader_pid, 1, MAX_PID)
        integer(self.reader_created_100ns, 1, 2**64 - 1)


def evidence_pipe_name(token: str, *, parent: bool = False) -> str:
    """Derive the only two local endpoints from an authenticated fixed token."""
    epoch(token)
    if type(parent) is not bool:
        raise ProtocolError("evidence-parent-role-type")
    return f"VerdiClip-moving-edit-evidence-{'parent-' if parent else ''}{token}"


@dataclass(frozen=True, slots=True)
class EvidenceReaderLaunch:
    """Fixed reader role data; its artifact directory is an outer-assigned cwd."""

    token: str
    outer_pid: int
    outer_created_100ns: int
    absolute_deadline_ns: int
    schema: int = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        """Reject ambiguous tokens, identities and original deadline values."""
        epoch(self.token)
        integer(self.outer_pid, 1, MAX_PID)
        integer(self.outer_created_100ns, 1, 2**64 - 1)
        integer(self.absolute_deadline_ns, 1, MAX_STAMP)
        constant(self.schema, EVIDENCE_SCHEMA)

    def serialize(self) -> str:
        """Enforce the unchanged launch cap before process creation."""
        return canonical_json(asdict(self), MAX_LAUNCH_BYTES).decode("utf-8")


def deserialize_evidence_reader_launch(payload: str) -> EvidenceReaderLaunch:
    """Reject paths, role dispatch selectors and unreviewed launch fields."""
    if type(payload) is not str:
        raise ProtocolError("evidence-reader-launch-type")
    value = parse_json(payload.encode("utf-8"), MAX_LAUNCH_BYTES)
    fields(value, "token outer_pid outer_created_100ns absolute_deadline_ns schema")
    return EvidenceReaderLaunch(**value)


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    """Exact expected identity supplied independently by the surviving outer."""

    session: str
    epoch: str
    sequence_started_ns: int
    sequence_deadline_ns: int
    peer_pid: int
    peer_created_100ns: int
    peer_executable: str
    parent_pid: int
    parent_created_100ns: int
    parent_executable: str
    schema: int = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        """Validate the full expected packet without weakening process identity."""
        epoch(self.session)
        epoch(self.epoch)
        constant(self.schema, EVIDENCE_SCHEMA)
        _evidence_stamps(self.sequence_started_ns, self.sequence_deadline_ns)
        for pid, created, executable in (
            (self.peer_pid, self.peer_created_100ns, self.peer_executable),
            (self.parent_pid, self.parent_created_100ns, self.parent_executable),
        ):
            integer(pid, 1, MAX_PID)
            integer(created, 1, 2**64 - 1)
            _evidence_path(executable)

    @property
    def value(self) -> dict[str, Any]:
        """Return a detached exact bounded one-shot outer packet."""
        return parse_json(
            canonical_json(asdict(self), MAX_FINAL_RECEIPT_BYTES),
            MAX_FINAL_RECEIPT_BYTES,
        )

    @classmethod
    def from_value(cls, value: object) -> EvidenceIdentity:
        """Validate an independently delivered expected-identity packet."""
        checked = parse_json(
            canonical_json(value, MAX_FINAL_RECEIPT_BYTES), MAX_FINAL_RECEIPT_BYTES
        )
        return cls(**fields(checked, _EVIDENCE_IDENTITY_FIELDS))


def make_evidence_hello(
    launch: MovingEditLaunch, pid: int, created_100ns: int
) -> dict[str, Any]:
    """Copy the authoritative launch stamps into the exact peer hello."""
    value = {
        "event": "hello",
        "schema": EVIDENCE_SCHEMA,
        "role": "peer",
        "evidence_token": launch.evidence_token,
        "epoch": launch.epoch,
        "pid": pid,
        "created_100ns": created_100ns,
        "sequence_started_ns": launch.sequence_started_ns,
        "sequence_deadline_ns": launch.sequence_deadline_ns,
    }
    return _evidence_event(value)


def _evidence_number(value: object, low: float, high: float) -> None:
    if type(value) not in (int, float):
        raise ProtocolError("evidence-number")
    number = cast("int | float", value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ProtocolError("evidence-number")


def _context_correlation(value: dict[str, Any]) -> None:
    context_id = integer(value["context_id"], 1, MAX_REQUESTS)
    constant(value["request_id"], context_id)
    expected = (
        ("begin", 0)
        if context_id == 1
        else (
            ("finish", 0)
            if context_id == MAX_REQUESTS
            else ("move" if context_id % 2 == 0 else "observe", context_id // 2)
        )
    )
    constant(value["operation"], expected[0])
    constant(value["step_id"], expected[1])
    digest_hex(value["command_sha256"])


def _parent_event(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProtocolError("evidence-parent-fields")
    completion = "completed_ns" in value
    names = _PARENT_CORRELATION + (
        " completed_ns" if completion else " requested_ns deadline_ns"
    )
    if value.get("operation") == "finish":
        names += (
            " retirement_started_ns retirement_deadline_ns"
            if completion
            else " response_deadline_ns"
        )
    packet = fields(value, names)
    constant(packet["schema"], EVIDENCE_SCHEMA)
    epoch(packet["session"])
    epoch(packet["epoch"])
    _context_correlation(packet)
    for name in (
        "completed_ns",
        "requested_ns",
        "deadline_ns",
        "retirement_started_ns",
        "retirement_deadline_ns",
        "response_deadline_ns",
    ):
        if name in packet:
            integer(
                packet[name],
                0
                if name.endswith("started_ns")
                or name in ("completed_ns", "requested_ns")
                else 1,
                MAX_STAMP,
            )
    return packet


def _evidence_event(value: object) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0915 - fixed exhaustive raw event schema
    if type(value) is not dict or type(value.get("event")) is not str:
        raise ProtocolError("evidence-event-type")
    kind = cast("str", cast("dict[str, Any]", value)["event"])
    schemas = {
        "hello": (
            "schema role evidence_token epoch pid created_100ns "
            "sequence_started_ns sequence_deadline_ns"
        ),
        "phase_enter": _CONTEXT_CORRELATION,
        "phase_end": _CONTEXT_CORRELATION + " completed_ns",
        "finish_response_sent": _CONTEXT_CORRELATION,
        "call_start": "name effective_deadline_ns",
        "call_end": "seconds",
        "edit_read": "message index result timeout_ms",
        "seek_start": "vertical horizontal restoring",
        "seek_end": "vertical horizontal restoring status",
        "failure": "context_id reason",
        "failure_restore_enter": (
            "context_id sequence_started_ns sequence_deadline_ns failure_ns"
        ),
        "failure_restore_phase": "phase_id operation",
        "final": "preceding_records preceding_bytes preceding_sha256",
    }
    if kind not in schemas:
        raise ProtocolError("evidence-unknown-event")
    event = fields(value, "event " + schemas[kind])
    if kind == "hello":
        constant(event["schema"], EVIDENCE_SCHEMA)
        constant(event["role"], "peer")
        epoch(event["evidence_token"])
        epoch(event["epoch"])
        integer(event["pid"], 1, MAX_PID)
        integer(event["created_100ns"], 1, 2**64 - 1)
        _evidence_stamps(event["sequence_started_ns"], event["sequence_deadline_ns"])
    elif kind in ("phase_enter", "phase_end", "finish_response_sent"):
        _context_correlation(event)
        if kind == "phase_end":
            integer(event["completed_ns"], 0, MAX_STAMP)
    elif kind == "call_start":
        if type(event["name"]) is not str or (
            event["name"] != "<lambda>"
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", event["name"]) is None
        ):
            raise ProtocolError("evidence-call-name")
        integer(event["effective_deadline_ns"], 1, MAX_STAMP)
    elif kind == "call_end":
        _evidence_number(event["seconds"], 0, 3)
    elif kind == "edit_read":
        integer(event["message"], 0, MAX_PID)
        integer(event["index"], -(2**63), 2**64 - 1)
        integer(event["result"], -(2**63), 2**64 - 1)
        integer(event["timeout_ms"], 1, 3000)
    elif kind in ("seek_start", "seek_end"):
        _evidence_number(event["vertical"], 0, 100)
        constant(event["horizontal"], -1.0)
        if type(event["restoring"]) is not bool:
            raise ProtocolError("evidence-restoring-bool")
        if kind == "seek_end":
            integer(event["status"], -(2**31), MAX_PID)
    elif kind == "failure":
        integer(event["context_id"], 1, MAX_REQUESTS)
        if type(event["reason"]) is not str or event["reason"] not in (
            "observation",
            "timeout",
            "uncertain_write",
            "unsafe_guard",
            "cancelled",
            "protocol",
        ):
            raise ProtocolError("evidence-failure-reason")
    elif kind == "failure_restore_enter":
        integer(event["context_id"], 1, MAX_REQUESTS)
        _evidence_stamps(event["sequence_started_ns"], event["sequence_deadline_ns"])
        integer(event["failure_ns"], 0, MAX_STAMP)
    elif kind == "failure_restore_phase":
        phase_id = integer(event["phase_id"], 1, 4)
        constant(
            event["operation"], ("guard", "write", "capture", "final")[phase_id - 1]
        )
    elif kind == "final":
        integer(event["preceding_records"], 1, MAX_EVIDENCE_RECORDS - 1)
        integer(event["preceding_bytes"], EVIDENCE_HEADER_BYTES + 1, MAX_EVIDENCE_BYTES)
        digest_hex(event["preceding_sha256"])
    return event


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One raw canonical event with its original producer monotonic timestamp."""

    record_id: int
    monotonic_ns: int
    event: dict[str, Any]


def encode_evidence_record(record: EvidenceRecord, *, parent: bool = False) -> bytes:
    """Create a fixed16-byte header without adding a replacement event timestamp."""
    if type(record) is not EvidenceRecord:
        raise ProtocolError("evidence-record-type")
    integer(
        record.record_id,
        1,
        MAX_PARENT_EVIDENCE_RECORDS if parent else MAX_EVIDENCE_RECORDS,
    )
    integer(record.monotonic_ns, 0, MAX_STAMP)
    value = _parent_event(record.event) if parent else _evidence_event(record.event)
    maximum = MAX_PARENT_EVIDENCE_RECORD_BYTES if parent else MAX_EVIDENCE_RECORD_BYTES
    payload = canonical_json(value, maximum - EVIDENCE_HEADER_BYTES)
    return (
        _EVIDENCE_HEADER.pack(len(payload), record.record_id, record.monotonic_ns)
        + payload
    )


def decode_evidence_record(data: bytes, *, parent: bool = False) -> EvidenceRecord:
    """Reject trailing bytes, noncanonical JSON and mismatched framing."""
    maximum = MAX_PARENT_EVIDENCE_RECORD_BYTES if parent else MAX_EVIDENCE_RECORD_BYTES
    if type(data) is not bytes or not EVIDENCE_HEADER_BYTES < len(data) <= maximum:
        raise ProtocolError("evidence-record-bound")
    size, record_id, stamp = _EVIDENCE_HEADER.unpack(data[:EVIDENCE_HEADER_BYTES])
    if size != len(data) - EVIDENCE_HEADER_BYTES:
        raise ProtocolError("evidence-record-length")
    record = EvidenceRecord(
        record_id,
        stamp,
        parse_json(data[EVIDENCE_HEADER_BYTES:], maximum - EVIDENCE_HEADER_BYTES),
    )
    constant(encode_evidence_record(record, parent=parent), data)
    return record


class EvidenceDecoder:
    """One bounded fragmented record, with no coalesced unchecked record queue."""

    def __init__(self, *, parent: bool = False) -> None:
        self.parent = parent
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> EvidenceRecord | None:
        """Admit fragments only until the single pending record is complete."""
        try:
            maximum = (
                MAX_PARENT_EVIDENCE_RECORD_BYTES
                if self.parent
                else MAX_EVIDENCE_RECORD_BYTES
            )
            if (
                self.failed
                or type(data) is not bytes
                or len(self.buffer) + len(data) > maximum
            ):
                raise ProtocolError("evidence-decoder-bound")  # noqa: TRY301
            self.buffer.extend(data)
            if len(self.buffer) < EVIDENCE_HEADER_BYTES:
                return None
            size = _EVIDENCE_HEADER.unpack(self.buffer[:EVIDENCE_HEADER_BYTES])[0]
            if (
                not 0 < size <= maximum - EVIDENCE_HEADER_BYTES
                or len(self.buffer) > size + EVIDENCE_HEADER_BYTES
            ):
                raise ProtocolError("evidence-decoder-framing")  # noqa: TRY301
            if len(self.buffer) < size + EVIDENCE_HEADER_BYTES:
                return None
            record = decode_evidence_record(bytes(self.buffer), parent=self.parent)
            self.buffer.clear()
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise
        else:
            return record

    def eof(self) -> None:
        """Reject a partial last record; semantic EOF remains the reader's job."""
        if self.failed or self.buffer:
            self.failed = True
            self.buffer.clear()
            raise ProtocolError("evidence-truncated-eof")


@dataclass(frozen=True, slots=True)
class EvidenceAck:
    """Fixed80-byte acceptance of exactly one persisted, identity-bound record."""

    session: str
    epoch: str
    record_id: int
    record_sha256: str

    def encode(self) -> bytes:
        """Encode only the sole accepted status, schema and zero flags."""
        epoch(self.session)
        epoch(self.epoch)
        integer(self.record_id, 1, MAX_EVIDENCE_RECORDS)
        digest_hex(self.record_sha256)
        return _EVIDENCE_ACK.pack(
            b"VMEA",
            EVIDENCE_SCHEMA,
            0,
            bytes.fromhex(self.session),
            bytes.fromhex(self.epoch),
            self.record_id,
            bytes.fromhex(self.record_sha256),
            1,
        )

    @classmethod
    def decode(cls, data: bytes) -> EvidenceAck:
        """Check all fixed fields; no ACK can grant a future record."""
        if type(data) is not bytes or len(data) != EVIDENCE_ACK_BYTES:
            raise ProtocolError("evidence-ack-length")
        magic, schema, flags, session, transport_epoch, record_id, digest, accepted = (
            _EVIDENCE_ACK.unpack(data)
        )
        for actual, expected in (
            (magic, b"VMEA"),
            (schema, EVIDENCE_SCHEMA),
            (flags, 0),
            (accepted, 1),
        ):
            constant(actual, expected)
        result = cls(session.hex(), transport_epoch.hex(), record_id, digest.hex())
        constant(result.encode(), data)
        return result


class _EvidenceFailure:
    """Preserve the first protocol failure across every later API call."""

    def __init__(self) -> None:
        self.failure: ProtocolError | None = None

    @property
    def failed(self) -> bool:
        """Report the permanent evidence failure latch."""
        return self.failure is not None

    def fail(self, reason: str) -> None:
        """Latch native I/O/persistence/identity loss without granting another ACK."""
        if self.failure is None:
            self.failure = ProtocolError(reason)
        raise self.failure

    @contextmanager
    def _guard(self) -> Generator[None]:
        if self.failure is not None:
            raise self.failure
        try:
            yield
        except ProtocolError as error:
            self.failure = self.failure or error
            raise self.failure from error


class EvidenceProducer(_EvidenceFailure):
    """One outstanding event and original ACK deadline, with incremental raw hash."""

    def __init__(
        self, session: str, transport_epoch: str, *, parent: bool = False
    ) -> None:
        super().__init__()
        self.session, self.epoch = epoch(session), epoch(transport_epoch)
        self.parent = parent
        self.count = self.byte_count = self.last_ns = 0
        self.pending: tuple[bytes, int] | None = None
        self._hash = hashlib.sha256()
        self.final_sent = False

    @property
    def hexdigest(self) -> str:
        """Return the digest of exact framed producer bytes, including final."""
        return self._hash.hexdigest()

    def prepare(
        self, event: dict[str, Any], monotonic_ns: int, deadline_ns: int
    ) -> bytes:
        """Reserve one record; its timing never creates a new native allowance."""
        with self._guard():
            integer(monotonic_ns, 0, MAX_STAMP)
            integer(deadline_ns, 1, MAX_STAMP)
            if (
                self.pending is not None
                or self.final_sent
                or not self.last_ns <= monotonic_ns < deadline_ns
            ):
                raise ProtocolError("evidence-producer-pending-or-time")
            frame = encode_evidence_record(
                EvidenceRecord(self.count + 1, monotonic_ns, event), parent=self.parent
            )
            if event.get("event") == "hello":
                deadline_ns = min(
                    deadline_ns,
                    evidence_auth_deadline_ns(
                        event["sequence_started_ns"],
                        event["sequence_deadline_ns"],
                        now_ns=monotonic_ns,
                    ),
                )
            maximum_bytes = (
                MAX_PARENT_EVIDENCE_BYTES if self.parent else MAX_EVIDENCE_BYTES
            )
            if self.byte_count + len(frame) > maximum_bytes:
                raise ProtocolError("evidence-producer-byte-bound")
            self.count += 1
            self.byte_count += len(frame)
            self._hash.update(frame)
            self.pending = frame, deadline_ns
            self.last_ns = monotonic_ns
            self.final_sent = event.get("event") == "final"
            return frame

    def check(self, now_ns: int) -> None:
        """Include queueing and ACK delay in the caller's retained operation ceiling."""
        with self._guard():
            integer(now_ns, 0, MAX_STAMP)
            if now_ns < self.last_ns or (
                self.pending is not None and now_ns >= self.pending[1]
            ):
                raise ProtocolError("evidence-producer-expired")
            self.last_ns = now_ns

    def acknowledge(self, data: bytes, now_ns: int) -> None:
        """Admit only the exact pending record before its original deadline."""
        with self._guard():
            self.check(now_ns)
            if self.pending is None:
                raise ProtocolError("evidence-unsolicited-ack")
            ack = EvidenceAck.decode(data)
            expected = EvidenceAck(
                self.session,
                self.epoch,
                self.count,
                hashlib.sha256(self.pending[0]).hexdigest(),
            )
            constant(ack.encode(), expected.encode())
            self.pending = None

    def final_event(self) -> dict[str, Any]:
        """Describe the complete preceding transcript without including itself."""
        with self._guard():
            if self.pending is not None or self.parent or self.final_sent:
                raise ProtocolError("evidence-premature-final")
            return {
                "event": "final",
                "preceding_records": self.count,
                "preceding_bytes": self.byte_count,
                "preceding_sha256": self.hexdigest,
            }


@dataclass(frozen=True, slots=True)
class _PendingEvidenceCall:
    record_id: int
    name: str
    started_ns: int
    deadline_ns: int


class _FixedSeekAudit:
    """Require seven declared fixed writes, independently in live and stored audit.

    These event checks do not prove that a native COM call occurred or that no
    unreported write occurred. Deferred failure-restore primitives never enter
    this normal successful-capture ledger.
    """

    def __init__(self) -> None:
        self.completed = 0
        self.started = self.finished = False

    def enter(self) -> None:
        """Start the next authenticated phase without discarding completed writes."""
        self.started = self.finished = False

    def _target(self, event: dict[str, Any], context: dict[str, Any]) -> None:
        if context["operation"] != "move":
            raise ProtocolError("evidence-fixed-seek-operation")
        step = context["step_id"]
        constant(step, self.completed + 1)
        constant(event["vertical"], 100.0 * FRAME_LINES[step - 1] / 13)
        constant(event["restoring"], step == MAX_SEQUENCE_FRAMES)

    def start(self, event: dict[str, Any], context: dict[str, Any]) -> None:
        """Admit one exact floating-point target for this normal move only."""
        if self.started:
            raise ProtocolError("evidence-fixed-seek-duplicate")
        self._target(event, context)
        self.started = True

    def end(self, event: dict[str, Any], context: dict[str, Any]) -> None:
        """Count only the successfully paired exact integer-zero native result."""
        if not self.started or self.finished:
            raise ProtocolError("evidence-fixed-seek-unpaired")
        self._target(event, context)
        # The raw schema already rejects bool, float and string status values.
        if event["status"] != 0:
            raise ProtocolError(
                f"evidence-fixed-seek-status:step={context['step_id']},"
                f"status={event['status']}"
            )
        self.finished = True
        self.completed += 1

    def close(self, context: dict[str, Any]) -> None:
        """Neither a missing write nor the repeated bottom target can be skipped."""
        if context["operation"] == "move" and not self.finished:
            raise ProtocolError("evidence-fixed-seek-missing")

    def final(self) -> None:
        """Require all seven successful normal writes before qualifying capture."""
        constant(self.completed, MAX_SEQUENCE_FRAMES)


class EvidenceReaderState(_EvidenceFailure):
    """Independent pure admission state; runtime owns pinned handles and storage.

    ``prepare_peer`` publishes pending-call state but does not acknowledge it.
    Only the I/O owner may call ``acknowledge_persisted`` after successful append
    and flush. This object performs no I/O and cannot itself attest kernel PIDs,
    durable storage, real EOF, or job retirement.
    """

    def __init__(self, expected: EvidenceIdentity) -> None:
        super().__init__()
        if type(expected) is not EvidenceIdentity:
            raise ProtocolError("evidence-expected-identity-type")
        self.expected = expected
        self.peer_bound = self.parent_bound = self.authenticated = False
        self.hello_received = False
        self.auth_deadline_ns = evidence_auth_deadline_ns(
            expected.sequence_started_ns, expected.sequence_deadline_ns
        )
        self.count = self.byte_count = self.parent_count = 0
        self.parent_byte_count = 0
        self._parent_hash = hashlib.sha256()
        self.correlated_replay_verified = False
        self.last_ns = expected.sequence_started_ns
        self.peer_last_ns = self.parent_last_ns = expected.sequence_started_ns
        self._hash = hashlib.sha256()
        self.pending_peer: bytes | None = None
        self.pending_parent: bytes | None = None
        self.calls: list[_PendingEvidenceCall] = []
        self.context: dict[str, Any] | None = None
        self.context_id = 0
        self.peer_phase_entered = self.peer_phase_closed = self.parent_completed = False
        self.seek: dict[str, Any] | None = None
        self._fixed_seeks = _FixedSeekAudit()
        self.context_failure: tuple[str, int] | None = None
        self.restore_deadline_ns: int | None = None
        self.restore_phase = 0
        self.restore_phase_deadline_ns: int | None = None
        self.final_received = self.eof_received = self.replay_verified = False
        self.finish_response_seen = self.finish_response_acknowledged = False

    @property
    def hexdigest(self) -> str:
        """Return the bounded incremental digest of accepted raw framed bytes."""
        return self._hash.hexdigest()

    @property
    def complete(self) -> bool:
        """Require successful live admission, actual EOF and stored raw replay."""
        return (
            self.final_received
            and self.eof_received
            and self.replay_verified
            and self.correlated_replay_verified
            and not self.failed
        )

    @property
    def status(self) -> dict[str, Any]:
        """Expose bounded watchdog metadata; never copy a raw record queue."""
        return {
            "schema": EVIDENCE_SCHEMA,
            "session": self.expected.session,
            "epoch": self.expected.epoch,
            "count": self.count,
            "byte_count": self.byte_count,
            "sha256": self.hexdigest,
            "failed": self.failed,
            "failure": None if self.failure is None else str(self.failure),
            "authenticated": self.authenticated,
            "hello_received": self.hello_received,
            "auth_deadline_ns": self.auth_deadline_ns,
            "final_received": self.final_received,
            "eof_received": self.eof_received,
            "replay_verified": self.replay_verified,
            "correlated_replay_verified": self.correlated_replay_verified,
            "parent_count": self.parent_count,
            "parent_byte_count": self.parent_byte_count,
            "parent_sha256": self.parent_hexdigest,
            "complete": self.complete,
            "context_id": self.context_id,
            "peer_phase_closed": self.peer_phase_closed,
            "parent_completed": self.parent_completed,
            "finish_response_seen": self.finish_response_seen,
            "finish_response_acknowledged": self.finish_response_acknowledged,
            "pending_calls": [asdict(call) for call in self.calls],
        }

    @property
    def parent_hexdigest(self) -> str:
        """Bind every exact accepted parent packet independently of peer raw bytes."""
        return self._parent_hash.hexdigest()

    def _now(self, now_ns: int) -> None:
        integer(now_ns, 0, MAX_STAMP)
        if not self.last_ns <= now_ns < self.expected.sequence_deadline_ns:
            raise ProtocolError("evidence-reader-clock")
        self.last_ns = now_ns

    def _ceiling(self) -> int:
        ceiling = self.expected.sequence_deadline_ns
        if not self.authenticated:
            ceiling = min(ceiling, self.auth_deadline_ns)
        if self.restore_deadline_ns is not None:
            ceiling = min(ceiling, self.restore_deadline_ns)
            if self.restore_phase_deadline_ns is not None:
                ceiling = min(ceiling, self.restore_phase_deadline_ns)
        elif self.context is not None and (
            self.context_id == MAX_REQUESTS
            or self.pending_parent is not None
            or not (self.parent_completed and self.peer_phase_closed)
        ):
            ceiling = min(ceiling, self.context["deadline_ns"])
            if (
                self.context_id == MAX_REQUESTS
                and not self.finish_response_acknowledged
            ):
                ceiling = min(ceiling, self.context["response_deadline_ns"])
        return integer(ceiling, 1, MAX_STAMP)

    @property
    def ack_deadline_ns(self) -> int:
        """Retain this pretransition ceiling through actual ACK write completion."""
        with self._guard():
            return min([self._ceiling(), *(call.deadline_ns for call in self.calls)])

    def check(self, now_ns: int) -> None:
        """Independently expire pending native calls even without incoming bytes."""
        with self._guard():
            self._now(now_ns)
            if now_ns >= self._ceiling() or any(
                now_ns >= call.deadline_ns for call in self.calls
            ):
                raise ProtocolError("evidence-reader-deadline")

    def _bind(
        self, role: str, pid: int, created_100ns: int, executable: str, now_ns: int
    ) -> None:
        with self._guard():
            self.check(now_ns)
            if getattr(self, role + "_bound"):
                raise ProtocolError("evidence-duplicate-connection")
            constant(pid, getattr(self.expected, role + "_pid"))
            constant(created_100ns, getattr(self.expected, role + "_created_100ns"))
            constant(executable, getattr(self.expected, role + "_executable"))
            setattr(self, role + "_bound", True)

    def bind_peer(
        self, pid: int, created_100ns: int, executable: str, now_ns: int
    ) -> None:
        """Bind only independently kernel-verified retained peer identity."""
        self._bind("peer", pid, created_100ns, executable, now_ns)

    def bind_parent(
        self, pid: int, created_100ns: int, executable: str, now_ns: int
    ) -> None:
        """Bind the independently verified executor, never the first arrival."""
        self._bind("parent", pid, created_100ns, executable, now_ns)

    def _match_context(self, event: dict[str, Any]) -> None:
        if self.context is None:
            raise ProtocolError("evidence-no-parent-context")
        for name in _CONTEXT_CORRELATION.split():
            constant(event[name], self.context[name])

    def _parent_context(self, packet: dict[str, Any], stamp: int, now_ns: int) -> None:
        if (
            self.pending_peer is not None
            or self.calls
            or (
                self.context is not None
                and not (self.peer_phase_closed and self.parent_completed)
            )
            or self.context_failure is not None
        ):
            raise ProtocolError("evidence-context-still-active")
        constant(packet["context_id"], self.context_id + 1)
        requested, deadline = packet["requested_ns"], packet["deadline_ns"]
        if (
            not self.expected.sequence_started_ns
            <= requested
            <= stamp
            <= now_ns
            < deadline
            <= self.expected.sequence_deadline_ns
        ):
            raise ProtocolError("evidence-parent-context-stamps")
        operation, step = packet["operation"], packet["step_id"]
        if operation == "begin":
            constant(
                deadline,
                min(
                    requested + OBSERVE_NS,
                    self.expected.sequence_deadline_ns - RESTORE_RESERVE_NS,
                ),
            )
        elif operation in ("move", "observe"):
            limit = min(
                requested + (MOVE_NS if operation == "move" else OBSERVE_NS),
                _phase_limit(self.expected.sequence_deadline_ns, step),
            )
            if deadline > limit:
                raise ProtocolError("evidence-parent-expanded-deadline")
        elif operation == "finish":
            constant(
                deadline,
                min(requested + 5_000_000_000, self.expected.sequence_deadline_ns),
            )
            constant(
                packet["response_deadline_ns"],
                min(requested + OBSERVE_NS, self.expected.sequence_deadline_ns),
            )
            if now_ns >= packet["response_deadline_ns"]:
                raise ProtocolError("evidence-finish-context-response-expired")
        if self.context is not None and requested < self.context["completed_ns"]:
            raise ProtocolError("evidence-context-precedes-prior-completion")
        self.context = dict(packet)
        self.context_id = packet["context_id"]
        self.peer_phase_entered = self.peer_phase_closed = self.parent_completed = False

    def _parent_completion(
        self, packet: dict[str, Any], stamp: int, now_ns: int
    ) -> None:
        self._match_context(packet)
        if self.context is None or self.parent_completed or not self.peer_phase_entered:
            raise ProtocolError("evidence-parent-completion-state")
        if (
            not self.context["requested_ns"]
            <= packet["completed_ns"]
            <= stamp
            <= now_ns
            < self.context["deadline_ns"]
        ):
            raise ProtocolError("evidence-parent-completion-stamps")
        if packet["operation"] == "finish":
            started, deadline = (
                packet["retirement_started_ns"],
                packet["retirement_deadline_ns"],
            )
            if (
                not packet["completed_ns"] <= started <= stamp
                or started >= self.context["response_deadline_ns"]
            ):
                raise ProtocolError("evidence-retirement-start")
            constant(
                deadline,
                min(started + 5_000_000_000, self.expected.sequence_deadline_ns),
            )
            if now_ns >= deadline:
                raise ProtocolError("evidence-retirement-expired")
            self.context["deadline_ns"] = min(self.context["deadline_ns"], deadline)
        elif packet["operation"] != "observe" and (
            self.calls or self.seek is not None or not self.peer_phase_closed
        ):
            raise ProtocolError("evidence-parent-completion-before-peer")
        self.context["completed_ns"] = packet["completed_ns"]
        self.parent_completed = True

    def prepare_parent(self, frame: bytes, now_ns: int) -> None:
        """Validate independent context before the worker may dispatch its command."""
        with self._guard():
            self.check(now_ns)
            if (
                not self.parent_bound
                or self.pending_parent is not None
                or self.final_received
            ):
                raise ProtocolError("evidence-parent-admission-state")
            record = decode_evidence_record(frame, parent=True)
            if self.parent_byte_count + len(frame) > MAX_PARENT_EVIDENCE_BYTES:
                raise ProtocolError("evidence-parent-byte-bound")
            constant(record.record_id, self.parent_count + 1)
            if not self.parent_last_ns <= record.monotonic_ns <= now_ns:
                raise ProtocolError("evidence-parent-record-time")
            packet = record.event
            constant(packet["session"], self.expected.session)
            constant(packet["epoch"], self.expected.epoch)
            if "completed_ns" in packet:
                self._parent_completion(packet, record.monotonic_ns, now_ns)
            else:
                self._parent_context(packet, record.monotonic_ns, now_ns)
            self.parent_count += 1
            self.parent_byte_count += len(frame)
            self._parent_hash.update(frame)
            self.parent_last_ns = record.monotonic_ns
            self.pending_parent = frame

    @property
    def parent_ack_ready(self) -> bool:
        """Hold nonfinish completion until the peer end is persisted."""
        if self.pending_parent is None or self.failed:
            return False
        packet = decode_evidence_record(self.pending_parent, parent=True).event
        return bool(
            "completed_ns" not in packet
            or packet["operation"] == "finish"
            or (self.peer_phase_closed and self.pending_peer is None)
        )

    def acknowledge_parent(self, now_ns: int) -> bytes | None:
        """ACK context immediately, or completion without waiting for peer cleanup."""
        with self._guard():
            self.check(now_ns)
            if self.pending_parent is None:
                raise ProtocolError("evidence-no-pending-parent")
            if not self.parent_ack_ready:
                return None
            ack = EvidenceAck(
                self.expected.session,
                self.expected.epoch,
                self.parent_count,
                hashlib.sha256(self.pending_parent).hexdigest(),
            ).encode()
            self.pending_parent = None
            return ack

    def _hello(self, event: dict[str, Any]) -> None:
        if self.hello_received or self.count != 0 or not self.peer_bound:
            raise ProtocolError("evidence-hello-state")
        for name, expected in (
            ("evidence_token", self.expected.session),
            ("epoch", self.expected.epoch),
            ("pid", self.expected.peer_pid),
            ("created_100ns", self.expected.peer_created_100ns),
            ("sequence_started_ns", self.expected.sequence_started_ns),
            ("sequence_deadline_ns", self.expected.sequence_deadline_ns),
        ):
            constant(event[name], expected)
        self.hello_received = True

    def _finish_response(self, record: EvidenceRecord) -> None:
        """Correlate the post-write marker while retaining the original response cap."""
        self._match_context(record.event)
        if (
            self.context is None
            or self.context_id != MAX_REQUESTS
            or not self.peer_phase_entered
            or self.peer_phase_closed
            or self.finish_response_seen
            or self.calls
            or self.seek is not None
            or self.context_failure is not None
        ):
            raise ProtocolError("evidence-finish-response-marker-state")
        if (
            not self.context["requested_ns"]
            <= record.monotonic_ns
            < self.context["response_deadline_ns"]
        ):
            raise ProtocolError("evidence-finish-response-marker-time")
        self.finish_response_seen = True

    def _phase(self, record: EvidenceRecord) -> None:
        event = record.event
        self._match_context(event)
        if self.context_failure is not None or self.calls or self.seek is not None:
            raise ProtocolError("evidence-phase-not-clean")
        if event["event"] == "phase_enter":
            if self.peer_phase_entered or self.pending_parent is not None:
                raise ProtocolError("evidence-phase-enter-state")
            if (
                self.context is None
                or record.monotonic_ns < self.context["requested_ns"]
            ):
                raise ProtocolError("evidence-phase-enter-time")
            self.peer_phase_entered = True
            self._fixed_seeks.enter()
        else:
            if (
                not self.peer_phase_entered
                or self.peer_phase_closed
                or self.context is None
            ):
                raise ProtocolError("evidence-phase-end-state")
            if (
                not self.context["requested_ns"]
                <= event["completed_ns"]
                <= record.monotonic_ns
                < self.context["deadline_ns"]
            ):
                raise ProtocolError("evidence-phase-end-time")
            if (
                self.context_id == MAX_REQUESTS
                and not self.finish_response_acknowledged
            ):
                raise ProtocolError("evidence-finish-end-before-response-marker")
            self._fixed_seeks.close(self.context)
            self.peer_phase_closed = True

    def _native(self, record: EvidenceRecord) -> None:  # noqa: C901, PLR0912 - paired native events under one active independently authenticated phase
        if (
            not self.peer_phase_entered
            or self.peer_phase_closed
            or (self.context_failure is not None and self.restore_phase == 0)
        ):
            raise ProtocolError("evidence-native-outside-phase")
        if self.context_id == MAX_REQUESTS and not self.finish_response_acknowledged:
            raise ProtocolError("evidence-finish-cleanup-before-response-marker")
        event, stamp = record.event, record.monotonic_ns
        kind = event["event"]
        if kind == "call_start":
            deadline = event["effective_deadline_ns"]
            if len(self.calls) >= MAX_EVIDENCE_DEPTH or not stamp < deadline <= min(
                stamp + MOVE_NS, self._ceiling()
            ):
                raise ProtocolError("evidence-native-call-ceiling")
            self.calls.append(
                _PendingEvidenceCall(record.record_id, event["name"], stamp, deadline)
            )
        elif kind == "call_end":
            if not self.calls:
                raise ProtocolError("evidence-unpaired-call-end")
            call = self.calls.pop()
            if not call.started_ns <= stamp < call.deadline_ns:
                raise ProtocolError("evidence-native-call-end-time")
        elif kind == "seek_start":
            if (
                self.seek is not None
                or self.context is None
                or (self.restore_deadline_ns is not None and self.restore_phase != 2)  # noqa: PLR2004 - fixed restore write phase
                or (
                    self.restore_deadline_ns is None
                    and self.context["operation"] != "move"
                )
            ):
                raise ProtocolError("evidence-seek-state")
            constant(
                event["restoring"],
                self.restore_deadline_ns is not None
                or self.context["step_id"] == MAX_SEQUENCE_FRAMES,
            )
            if self.restore_deadline_ns is None:
                self._fixed_seeks.start(event, self.context)
            self.seek = dict(event)
        elif kind == "seek_end":
            if self.seek is None:
                raise ProtocolError("evidence-unpaired-seek-end")
            for name in ("vertical", "horizontal", "restoring"):
                constant(event[name], self.seek[name])
            if self.restore_deadline_ns is None:
                self._fixed_seeks.end(event, cast("dict[str, Any]", self.context))
            self.seek = None

    def _restore(self, record: EvidenceRecord, now_ns: int) -> None:
        event, stamp = record.event, record.monotonic_ns
        if self.calls or self.seek is not None or self.context is None:
            raise ProtocolError("evidence-restore-pending-native")
        if event["event"] == "failure_restore_enter":
            constant(event["context_id"], self.context_id)
            constant(event["sequence_started_ns"], self.expected.sequence_started_ns)
            constant(event["sequence_deadline_ns"], self.expected.sequence_deadline_ns)
            if (
                self.context_failure != ("observation", event["failure_ns"])
                or self.context["operation"] != "observe"
                or self.restore_deadline_ns is not None
                or not self.parent_bound
            ):
                raise ProtocolError("evidence-restore-ineligible")
            if (
                not event["failure_ns"] <= stamp <= now_ns
                or now_ns + RESTORE_RESERVE_NS > self.expected.sequence_deadline_ns
            ):
                raise ProtocolError("evidence-restore-reserve")
            ceiling = min(
                self.expected.sequence_deadline_ns,
                event["failure_ns"] + RESTORE_RESERVE_NS,
            )
            if now_ns >= ceiling:
                raise ProtocolError("evidence-restore-expired")
            self.restore_deadline_ns = ceiling
        else:
            if self.restore_deadline_ns is None:
                raise ProtocolError("evidence-restore-not-entered")
            constant(event["phase_id"], self.restore_phase + 1)
            duration = (OBSERVE_NS, MOVE_NS, OBSERVE_NS, 1_000_000_000)[
                self.restore_phase
            ]
            deadline = min(stamp + duration, self.restore_deadline_ns)
            if now_ns >= deadline:
                raise ProtocolError("evidence-restore-phase-expired")
            self.restore_phase += 1
            self.restore_phase_deadline_ns = deadline

    def _peer_event(self, record: EvidenceRecord, now_ns: int) -> None:
        event, kind = record.event, record.event["event"]
        if kind == "hello":
            self._hello(event)
            return
        if not self.authenticated or not self.parent_bound:
            raise ProtocolError("evidence-peer-not-authenticated")
        if kind in ("phase_enter", "phase_end"):
            self._phase(record)
        elif kind == "finish_response_sent":
            self._finish_response(record)
        elif kind in ("failure_restore_enter", "failure_restore_phase"):
            self._restore(record, now_ns)
        elif kind == "failure":
            constant(event["context_id"], self.context_id)
            if (
                not self.peer_phase_entered
                or self.peer_phase_closed
                or self.context_failure is not None
            ):
                raise ProtocolError("evidence-failure-context")
            self.context_failure = event["reason"], record.monotonic_ns
        elif kind == "final":
            if (
                self.context_id != MAX_REQUESTS
                or not self.peer_phase_closed
                or not self.parent_completed
                or self.calls
                or self.seek is not None
                or self.context_failure is not None
                or self.pending_parent is not None
            ):
                raise ProtocolError("evidence-final-context-not-complete")
            self._fixed_seeks.final()
            constant(event["preceding_records"], self.count)
            constant(event["preceding_bytes"], self.byte_count)
            constant(event["preceding_sha256"], self.hexdigest)
            self.final_received = True
        else:
            self._native(record)

    def prepare_peer(self, frame: bytes, now_ns: int) -> None:
        """Publish validated pairing state before the caller persists and ACKs."""
        with self._guard():
            self.check(now_ns)
            if (
                not self.peer_bound
                or self.pending_peer is not None
                or self.final_received
                or self.eof_received
            ):
                raise ProtocolError("evidence-peer-admission-state")
            record = decode_evidence_record(frame)
            constant(record.record_id, self.count + 1)
            if self.byte_count + len(frame) > MAX_EVIDENCE_BYTES:
                raise ProtocolError("evidence-reader-byte-bound")
            if not self.peer_last_ns <= record.monotonic_ns <= now_ns:
                raise ProtocolError("evidence-peer-record-time")
            self._peer_event(record, now_ns)
            self.count += 1
            self.byte_count += len(frame)
            self._hash.update(frame)
            self.peer_last_ns = record.monotonic_ns
            self.pending_peer = frame

    def acknowledge_persisted(self, now_ns: int) -> bytes:
        """Grant one record only after successful append/flush and a fresh watchdog."""
        with self._guard():
            self.check(now_ns)
            if self.pending_peer is None:
                raise ProtocolError("evidence-no-persisted-record")
            ack = EvidenceAck(
                self.expected.session,
                self.expected.epoch,
                self.count,
                hashlib.sha256(self.pending_peer).hexdigest(),
            ).encode()
            kind = decode_evidence_record(self.pending_peer).event["event"]
            if kind == "hello":
                self.authenticated = True
            elif kind == "finish_response_sent":
                self.finish_response_acknowledged = True
            self.pending_peer = None
            return ack

    def parent_closed(self) -> None:
        """Observed parent closure permanently forbids the next native dispatch."""
        self.parent_bound = False
        self.fail("evidence-parent-closed")

    def peer_eof(self, now_ns: int) -> None:
        """Require actual EOF after the final ACK, never an application terminal."""
        with self._guard():
            self.check(now_ns)
            if (
                self.eof_received
                or not self.final_received
                or self.pending_peer is not None
                or self.pending_parent is not None
            ):
                raise ProtocolError("evidence-premature-or-duplicate-eof")
            self.eof_received = True

    def verify_replay(self, replay: EvidenceReplay) -> None:
        """Compare bounded independent stored-raw audit before reporting completion."""
        with self._guard():
            if (
                not self.eof_received
                or self.replay_verified
                or type(replay) is not EvidenceReplay
                or not replay.complete
                or (replay.count, replay.byte_count, replay.hexdigest)
                != (self.count, self.byte_count, self.hexdigest)
            ):
                raise ProtocolError("evidence-final-replay-mismatch")
            self.replay_verified = True

    def verify_correlated_replay(self, replay: EvidenceCorrelatedReplay) -> None:
        """Require stored parent authority and the correlated peer audit."""
        with self._guard():
            if (
                not self.eof_received
                or self.correlated_replay_verified
                or type(replay) is not EvidenceCorrelatedReplay
                or not replay.complete
                or replay.expected != self.expected
                or (replay.peer.count, replay.peer.byte_count, replay.peer.hexdigest)
                != (self.count, self.byte_count, self.hexdigest)
                or (
                    replay.parent.count,
                    replay.parent.byte_count,
                    replay.parent.hexdigest,
                )
                != (self.parent_count, self.parent_byte_count, self.parent_hexdigest)
            ):
                raise ProtocolError("evidence-correlated-replay-mismatch")
            self.replay_verified = self.correlated_replay_verified = True


class EvidenceReplay(_EvidenceFailure):
    """Independently reread stored raw bytes with one4096-byte parser buffer."""

    def __init__(self) -> None:
        super().__init__()
        self.buffer = bytearray()
        self.count = self.byte_count = self.last_ns = 0
        self._hash = hashlib.sha256()
        self.calls: list[tuple[int, int]] = []
        self.final_received = self.complete = False
        self.sequence_deadline_ns = 0
        self.active_context = self.closed_context = 0
        self.failed_context = False
        self.finish_response_seen = False
        self.context: dict[str, Any] | None = None
        self.seek: dict[str, Any] | None = None

    @property
    def hexdigest(self) -> str:
        """Return the digest of exact bytes successfully replayed."""
        return self._hash.hexdigest()

    def _record(self, data: bytes) -> None:  # noqa: C901, PLR0912, PLR0915 - independent fixed transcript replay without retained records
        record = decode_evidence_record(data)
        constant(record.record_id, self.count + 1)
        if (
            self.final_received
            or self.byte_count + len(data) > MAX_EVIDENCE_BYTES
            or record.monotonic_ns < self.last_ns
        ):
            raise ProtocolError("evidence-replay-bound-or-order")
        event, kind = record.event, record.event["event"]
        if self.count == 0:
            constant(kind, "hello")
            self.sequence_deadline_ns = event["sequence_deadline_ns"]
            if record.monotonic_ns < event["sequence_started_ns"]:
                raise ProtocolError("evidence-replay-hello-time")
            evidence_auth_deadline_ns(
                event["sequence_started_ns"],
                event["sequence_deadline_ns"],
                now_ns=record.monotonic_ns,
            )
        elif kind == "hello":
            raise ProtocolError("evidence-replay-duplicate-hello")
        if record.monotonic_ns >= self.sequence_deadline_ns or any(
            record.monotonic_ns >= deadline for _, deadline in self.calls
        ):
            raise ProtocolError("evidence-replay-sequence-time")
        if kind == "phase_enter":
            if self.calls or self.active_context != self.closed_context:
                raise ProtocolError("evidence-replay-phase-overlap")
            constant(event["context_id"], self.closed_context + 1)
            self.active_context = event["context_id"]
            self.context = event
        elif kind == "phase_end":
            if self.calls or self.active_context == self.closed_context:
                raise ProtocolError("evidence-replay-phase-pending")
            constant(event["context_id"], self.active_context)
            if self.context is None or self.seek is not None:
                raise ProtocolError("evidence-replay-phase-not-clean")
            for name in _CONTEXT_CORRELATION.split():
                constant(event[name], self.context[name])
            if event["completed_ns"] > record.monotonic_ns:
                raise ProtocolError("evidence-replay-future-completion")
            if self.active_context == MAX_REQUESTS and not self.finish_response_seen:
                raise ProtocolError("evidence-replay-finish-before-response")
            self.closed_context = self.active_context
        elif kind == "finish_response_sent":
            if (
                self.context is None
                or self.active_context != MAX_REQUESTS
                or self.closed_context == MAX_REQUESTS
                or self.finish_response_seen
                or self.calls
            ):
                raise ProtocolError("evidence-replay-finish-response-state")
            for name in _CONTEXT_CORRELATION.split():
                constant(event[name], self.context[name])
            self.finish_response_seen = True
        elif kind == "call_start":
            if self.active_context == MAX_REQUESTS and not self.finish_response_seen:
                raise ProtocolError("evidence-replay-cleanup-before-response")
            deadline = event["effective_deadline_ns"]
            if (
                len(self.calls) >= MAX_EVIDENCE_DEPTH
                or self.active_context == self.closed_context
                or not record.monotonic_ns
                < deadline
                <= min(record.monotonic_ns + MOVE_NS, self.sequence_deadline_ns)
            ):
                raise ProtocolError("evidence-replay-call-ceiling")
            self.calls.append((record.monotonic_ns, deadline))
        elif kind == "call_end":
            if not self.calls:
                raise ProtocolError("evidence-replay-unpaired-call")
            started, deadline = self.calls.pop()
            if not started <= record.monotonic_ns < deadline:
                raise ProtocolError("evidence-replay-call-time")
        elif kind in ("edit_read", "seek_start", "seek_end"):
            if self.active_context == self.closed_context:
                raise ProtocolError("evidence-replay-unscoped-native")
            if kind == "seek_start":
                if self.seek is not None:
                    raise ProtocolError("evidence-replay-overlapping-seek")
                self.seek = event
            elif kind == "seek_end":
                if self.seek is None:
                    raise ProtocolError("evidence-replay-unpaired-seek")
                for name in ("vertical", "horizontal", "restoring"):
                    constant(event[name], self.seek[name])
                self.seek = None
        elif kind == "failure":
            self.failed_context = True
        elif kind == "final":
            if (
                self.calls
                or self.failed_context
                or self.closed_context != MAX_REQUESTS
                or self.active_context != self.closed_context
            ):
                raise ProtocolError("evidence-replay-final-state")
            constant(event["preceding_records"], self.count)
            constant(event["preceding_bytes"], self.byte_count)
            constant(event["preceding_sha256"], self.hexdigest)
            self.final_received = True
        self.count += 1
        self.byte_count += len(data)
        self._hash.update(data)
        self.last_ns = record.monotonic_ns

    def feed(self, chunk: bytes) -> None:
        """Consume bounded chunks, never accumulating a transcript-sized list."""
        with self._guard():
            if (
                type(chunk) is not bytes
                or len(chunk) > MAX_EVIDENCE_RECORD_BYTES
                or self.complete
                or (self.final_received and chunk)
            ):
                raise ProtocolError("evidence-replay-chunk-bound")
            offset = 0
            while offset < len(chunk):
                target = EVIDENCE_HEADER_BYTES
                if len(self.buffer) >= EVIDENCE_HEADER_BYTES:
                    size = _EVIDENCE_HEADER.unpack(self.buffer[:EVIDENCE_HEADER_BYTES])[
                        0
                    ]
                    if (
                        not 0
                        < size
                        <= MAX_EVIDENCE_RECORD_BYTES - EVIDENCE_HEADER_BYTES
                    ):
                        raise ProtocolError("evidence-replay-framing")
                    target += size
                take = min(target - len(self.buffer), len(chunk) - offset)
                self.buffer.extend(chunk[offset : offset + take])
                offset += take
                if len(self.buffer) == target and target > EVIDENCE_HEADER_BYTES:
                    self._record(bytes(self.buffer))
                    self.buffer.clear()

    def eof(self) -> None:
        """A truncated, missing-final or duplicate EOF can never qualify."""
        with self._guard():
            if self.buffer or not self.final_received or self.complete:
                raise ProtocolError("evidence-replay-premature-eof")
            self.complete = True


class ParentEvidenceReplay(_EvidenceFailure):
    """Audit at most32 exact parent packets, retaining at most32KiB of raw bytes.

    The bounded parent transcript is read before streaming peer records. Stored
    timestamps establish producer-side chronology, never physical arrival or ACK
    delivery times. A failed or incomplete prefix remains diagnostic, not complete.
    """

    def __init__(self, expected: EvidenceIdentity) -> None:
        super().__init__()
        if type(expected) is not EvidenceIdentity:
            raise ProtocolError("parent-replay-expected-identity-type")
        self.expected = expected
        self.buffer = bytearray()
        self._records: list[bytes] = []
        self.count = self.byte_count = 0
        self.last_ns = self.last_completed_ns = expected.sequence_started_ns
        self._hash = hashlib.sha256()
        self.complete = False

    @property
    def hexdigest(self) -> str:
        """Hash exact framed context and completion records."""
        return self._hash.hexdigest()

    @property
    def status(self) -> dict[str, Any]:
        """Report bounded accepted-prefix evidence without claiming a full audit."""
        return {
            "count": self.count,
            "byte_count": self.byte_count,
            "sha256": self.hexdigest,
            "complete": self.complete and not self.failed,
            "failed": self.failed,
        }

    def _context(self, record: EvidenceRecord) -> None:
        packet, stamp = record.event, record.monotonic_ns
        if "completed_ns" in packet:
            raise ProtocolError("parent-replay-context-required")
        requested, deadline = packet["requested_ns"], packet["deadline_ns"]
        if (
            not self.last_completed_ns
            <= requested
            <= stamp
            < deadline
            <= self.expected.sequence_deadline_ns
        ):
            raise ProtocolError("parent-replay-context-time")
        operation = packet["operation"]
        if operation == "begin":
            constant(
                deadline,
                min(
                    requested + OBSERVE_NS,
                    self.expected.sequence_deadline_ns - RESTORE_RESERVE_NS,
                ),
            )
        elif operation in ("move", "observe"):
            duration = MOVE_NS if operation == "move" else OBSERVE_NS
            if deadline > min(
                requested + duration,
                _phase_limit(self.expected.sequence_deadline_ns, packet["step_id"]),
            ):
                raise ProtocolError("parent-replay-expanded-context")
        else:
            constant(
                deadline,
                min(requested + 5_000_000_000, self.expected.sequence_deadline_ns),
            )
            constant(
                packet["response_deadline_ns"],
                min(requested + OBSERVE_NS, self.expected.sequence_deadline_ns),
            )
            if stamp >= packet["response_deadline_ns"]:
                raise ProtocolError("parent-replay-finish-dispatch-expired")

    def _completion(self, record: EvidenceRecord) -> None:
        packet, stamp = record.event, record.monotonic_ns
        if "completed_ns" not in packet:
            raise ProtocolError("parent-replay-completion-required")
        context = decode_evidence_record(self._records[-1], parent=True).event
        for name in _CONTEXT_CORRELATION.split():
            constant(packet[name], context[name])
        if (
            not context["requested_ns"]
            <= packet["completed_ns"]
            <= stamp
            < context["deadline_ns"]
        ):
            raise ProtocolError("parent-replay-completion-time")
        if packet["operation"] == "finish":
            started, deadline = (
                packet["retirement_started_ns"],
                packet["retirement_deadline_ns"],
            )
            if (
                not packet["completed_ns"] <= started <= stamp
                or started >= context["response_deadline_ns"]
            ):
                raise ProtocolError("parent-replay-retirement-start")
            constant(
                deadline,
                min(started + 5_000_000_000, self.expected.sequence_deadline_ns),
            )
            if stamp >= min(deadline, context["deadline_ns"]):
                raise ProtocolError("parent-replay-retirement-expired")
        self.last_completed_ns = packet["completed_ns"]

    def _record(self, data: bytes) -> None:
        if (
            self.count >= MAX_PARENT_EVIDENCE_RECORDS
            or self.byte_count + len(data) > MAX_PARENT_EVIDENCE_BYTES
        ):
            raise ProtocolError("parent-replay-transcript-bound")
        record = decode_evidence_record(data, parent=True)
        constant(record.record_id, self.count + 1)
        constant(record.event["context_id"], self.count // 2 + 1)
        constant(record.event["session"], self.expected.session)
        constant(record.event["epoch"], self.expected.epoch)
        if not self.last_ns <= record.monotonic_ns < self.expected.sequence_deadline_ns:
            raise ProtocolError("parent-replay-record-time")
        if self.count % 2 == 0:
            self._context(record)
        else:
            self._completion(record)
        self._records.append(data)
        self.count += 1
        self.byte_count += len(data)
        self._hash.update(data)
        self.last_ns = record.monotonic_ns

    def feed(self, chunk: bytes) -> None:
        """Parse bounded fragments with one1024-byte buffer and fixed total cap."""
        with self._guard():
            if (
                type(chunk) is not bytes
                or len(chunk) > MAX_PARENT_EVIDENCE_RECORD_BYTES
                or self.complete
            ):
                raise ProtocolError("parent-replay-chunk-bound")
            offset = 0
            while offset < len(chunk):
                target = EVIDENCE_HEADER_BYTES
                if len(self.buffer) >= EVIDENCE_HEADER_BYTES:
                    size = _EVIDENCE_HEADER.unpack(self.buffer[:EVIDENCE_HEADER_BYTES])[
                        0
                    ]
                    if (
                        not 0
                        < size
                        <= MAX_PARENT_EVIDENCE_RECORD_BYTES - EVIDENCE_HEADER_BYTES
                    ):
                        raise ProtocolError("parent-replay-framing")
                    target += size
                take = min(target - len(self.buffer), len(chunk) - offset)
                self.buffer.extend(chunk[offset : offset + take])
                offset += take
                if len(self.buffer) == target and target > EVIDENCE_HEADER_BYTES:
                    self._record(bytes(self.buffer))
                    self.buffer.clear()

    def eof(self) -> None:
        """Require every complete context/completion pair; retain a failed prefix."""
        with self._guard():
            if (
                self.buffer
                or self.complete
                or self.count != MAX_PARENT_EVIDENCE_RECORDS
            ):
                raise ProtocolError("parent-replay-incomplete-eof")
            self.complete = True

    def context_pair(self, context_id: int) -> tuple[EvidenceRecord, EvidenceRecord]:
        """Return detached parsed records from the fully bounded parent transcript."""
        with self._guard():
            integer(context_id, 1, MAX_REQUESTS)
            if not self.complete:
                raise ProtocolError("parent-replay-not-complete")
            index = 2 * (context_id - 1)
            return (
                decode_evidence_record(self._records[index], parent=True),
                decode_evidence_record(self._records[index + 1], parent=True),
            )


class _CorrelatedPeerReplay(EvidenceReplay):
    """Stream peer bytes against parent authority without inventing arrival times."""

    def __init__(self, parent: ParentEvidenceReplay) -> None:
        super().__init__()
        self.parent = parent
        self.phase_started_ns = 0
        self._fixed_seeks = _FixedSeekAudit()

    def _hello_identity(self, event: dict[str, Any]) -> None:
        expected = self.parent.expected
        for name, value in (
            ("evidence_token", expected.session),
            ("epoch", expected.epoch),
            ("pid", expected.peer_pid),
            ("created_100ns", expected.peer_created_100ns),
            ("sequence_started_ns", expected.sequence_started_ns),
            ("sequence_deadline_ns", expected.sequence_deadline_ns),
        ):
            constant(event[name], value)

    def _correlate(self, record: EvidenceRecord) -> None:  # noqa: C901, PLR0912 - explicit two-stream causal constraints, not arrival reconstruction
        event, stamp = record.event, record.monotonic_ns
        kind = event["event"]
        if kind == "hello":
            self._hello_identity(event)
            return
        if kind in ("failure", "failure_restore_enter", "failure_restore_phase"):
            raise ProtocolError("correlated-replay-failed-transcript")
        context_id = (
            event["context_id"] if kind == "phase_enter" else self.active_context
        )
        context_record, completion_record = self.parent.context_pair(context_id)
        context, completion = context_record.event, completion_record.event
        ceiling = context["deadline_ns"]
        if context["operation"] == "finish":
            ceiling = min(ceiling, completion["retirement_deadline_ns"])
        if not context_record.monotonic_ns <= stamp < ceiling:
            raise ProtocolError("correlated-replay-context-time")
        if kind in ("phase_enter", "phase_end", "finish_response_sent"):
            for name in _CONTEXT_CORRELATION.split():
                constant(event[name], context[name])
        if kind == "phase_enter":
            self.phase_started_ns = stamp
            self._fixed_seeks.enter()
        elif kind == "phase_end":
            self._fixed_seeks.close(context)
            if not self.phase_started_ns <= event["completed_ns"] <= stamp:
                raise ProtocolError("correlated-replay-phase-completion-time")
            if (
                context["operation"] in ("begin", "move")
                and stamp > completion["completed_ns"]
            ):
                raise ProtocolError("correlated-replay-response-before-peer-end")
            if context["operation"] == "observe" and completion["completed_ns"] > stamp:
                raise ProtocolError("correlated-replay-peer-end-before-frame-accept")
        elif kind == "seek_start":
            self._fixed_seeks.start(event, context)
        elif kind == "seek_end":
            self._fixed_seeks.end(event, context)
        elif kind == "final":
            self._fixed_seeks.final()
        elif kind == "finish_response_sent":
            if (
                context["operation"] != "finish"
                or stamp >= context["response_deadline_ns"]
            ):
                raise ProtocolError("correlated-replay-finish-response-time")
        elif kind == "call_start" and event["effective_deadline_ns"] > min(
            stamp + MOVE_NS, ceiling
        ):
            raise ProtocolError("correlated-replay-native-context-ceiling")

    @override
    def _record(self, data: bytes) -> None:
        if not self.parent.complete or self.parent.failed:
            raise ProtocolError("correlated-replay-parent-required")
        record = decode_evidence_record(data)
        self._correlate(record)
        super()._record(data)


class EvidenceCorrelatedReplay(_EvidenceFailure):
    """Audit both stored streams as a correlated producer-side partial order.

    This checks canonical identity metadata, independent command digests, original
    context deadlines, paired native-call ceilings and compatible completion
    order. It does not recover physical arrival/ACK scheduling, prove retained
    kernel identities or establish actual EOF/job retirement. Those are live gates.
    """

    def __init__(self, expected: EvidenceIdentity) -> None:
        super().__init__()
        self.expected = expected
        self.parent = ParentEvidenceReplay(expected)
        self.peer = _CorrelatedPeerReplay(self.parent)

    @property
    def complete(self) -> bool:
        """Require both complete transcripts and no independent audit failure."""
        return (
            self.parent.complete
            and self.peer.complete
            and not (self.failed or self.parent.failed or self.peer.failed)
        )

    @property
    def status(self) -> dict[str, Any]:
        """State the audit's exact limits alongside independently computed digests."""
        return {
            "audit_scope": "correlated-partial-order",
            "arrival_order_reconstructed": False,
            "acknowledgments_reconstructed": False,
            "kernel_identity_verified": False,
            "complete": self.complete,
            "parent": self.parent.status,
            "peer": {
                "count": self.peer.count,
                "byte_count": self.peer.byte_count,
                "sha256": self.peer.hexdigest,
                "complete": self.peer.complete and not self.peer.failed,
            },
        }

    def feed_parent(self, chunk: bytes) -> None:
        """Read fixed parent raw storage before beginning the streamed peer audit."""
        with self._guard():
            if self.peer.count or self.peer.buffer:
                raise ProtocolError("correlated-replay-parent-after-peer")
            self.parent.feed(chunk)

    def parent_eof(self) -> None:
        """Require actual storage EOF after all32 complete parent records."""
        with self._guard():
            self.parent.eof()

    def feed_peer(self, chunk: bytes) -> None:
        """Stream peer storage only after the independent parent audit is complete."""
        with self._guard():
            if not self.parent.complete:
                raise ProtocolError("correlated-replay-parent-required")
            self.peer.feed(chunk)

    def peer_eof(self) -> None:
        """Require the peer's complete final summary and exact stored EOF."""
        with self._guard():
            if not self.parent.complete:
                raise ProtocolError("correlated-replay-parent-required")
            self.peer.eof()
