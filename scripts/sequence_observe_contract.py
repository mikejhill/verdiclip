"""Independent fixture-only sequence evidence checks; no native calls or parsing reuse.

These checks qualify metadata and retained bytes only. The harness must separately
prove native ownership, EOF, take-once behavior and unchanged post-reference state.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Never

# Fixed adjacent fixture dependency also works outside a source checkout under -I.
_spec = importlib.util.spec_from_file_location(
    "_sequence_cancel_contract", Path(__file__).with_name("observe_credit_cancel.py")
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Missing fixed cancellation contract")
_cancel_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cancel_contract)
_exact = _cancel_contract._exact
_integer = _cancel_contract._integer
_object = _cancel_contract._object
_snapshot = _cancel_contract._snapshot

_CHUNK = 65536
_MAX = 2**63 - 1
_PREFIX = 4
_CREDIT_LIMIT = 512
_EVENT_FIELDS = 4
_FRAME_COUNT = 2
_RECT_FIELDS = 4
_RUNTIME_LIMIT = 32
_PROOF_FIELDS = (
    "proof_schema epoch request_id frame_id requested_ns deadline_ns "
    "verified_ns header_sha256 observation_sha256 image_sha256 bytes "
    "credit_chunks granted_bytes accepted_bytes credit_events observation"
)


def canonical(value: object) -> bytes:
    """Independent canonical JSON encoding."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def _invalid(_value: object) -> Never:
    raise ValueError("Nonfinite JSON")


def parse_json(raw: bytes, limit: int = 16384) -> dict[str, Any]:
    """Read one complete bounded object with duplicate and nonfinite rejection."""
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("Invalid immutable JSON bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid JSON") from error
    if type(value) is not dict:
        raise ValueError("Expected object")
    return value


def parse_credit_token(  # noqa: PLR0913 - explicit independent correlation inputs
    generation: int,
    raw: bytes,
    *,
    expected_generation: int,
    epoch: str,
    size: int,
    frame_id: int,
    chunk_index: int,
) -> dict[str, Any]:
    """Validate an entire VERSION3/credit2 wait, never a partial/padded packet."""
    _integer(expected_generation, 1, _MAX)
    _exact(generation, expected_generation)
    _integer(size, 1, 1572864)
    _integer(frame_id, 1, 2)
    _integer(chunk_index, 0, (size - 1) // _CHUNK)
    if (
        type(epoch) is not str
        or not re.fullmatch("[0-9a-f]{32}", epoch)
        or epoch == "0" * 32
    ):
        raise ValueError("Invalid epoch")
    if (
        type(raw) is not bytes
        or not _PREFIX < len(raw) <= _CREDIT_LIMIT
        or struct.unpack(">I", raw[:4])[0] != len(raw) - 4
    ):
        raise ValueError("Invalid complete credit wire")
    token = {
        "credit_schema": 2,
        "epoch": epoch,
        "frame_id": frame_id,
        "chunk_index": chunk_index,
        "offset": chunk_index * _CHUNK,
        "count": min(_CHUNK, size - chunk_index * _CHUNK),
    }
    _exact(
        parse_json(raw[4:], 508),
        {
            "version": 3,
            "kind": "frame_wait",
            "request_id": frame_id + 1,
            "payload": token,
        },
    )
    return token


class CreditTranscript:
    """Bounded public callback record, with explicit grant call identity."""

    def __init__(self, generation: int, epoch: str, size: int, frame_id: int) -> None:
        _integer(size, 1, 1572864)
        _integer(frame_id, 1, 2)
        _integer(generation, 1, _MAX)
        self.generation, self.epoch, self.size, self.frame_id = (
            generation,
            epoch,
            size,
            frame_id,
        )
        self.events: list[dict[str, Any]] = []

    def record_wait(
        self, generation: int, raw: bytes, received_ns: int
    ) -> tuple[int, int, int, int]:
        """Consume the next callback index before the public grant call."""
        if self.events and "grant_returned_ns" not in self.events[-1]:
            raise ValueError("Previous credit not granted")
        token = parse_credit_token(
            generation,
            raw,
            expected_generation=self.generation,
            epoch=self.epoch,
            size=self.size,
            frame_id=self.frame_id,
            chunk_index=len(self.events),
        )
        _integer(received_ns, 1, _MAX)
        self.events.append({"token": token, "wait_received_ns": received_ns})
        return self.generation, self.frame_id + 1, self.frame_id, len(self.events) - 1

    def record_grant(
        self, identity: tuple[int, int, int, int], called_ns: int, returned_ns: int
    ) -> None:
        """Record only after the exact public grant returned successfully."""
        if not self.events or "grant_called_ns" in self.events[-1]:
            raise ValueError("Missing or repeated grant")
        _exact(
            identity,
            (self.generation, self.frame_id + 1, self.frame_id, len(self.events) - 1),
        )
        event = self.events[-1]
        _integer(called_ns, event["wait_received_ns"], _MAX)
        _integer(returned_ns, called_ns, _MAX)
        event.update(grant_called_ns=called_ns, grant_returned_ns=returned_ns)

    def verify(self, proof: dict[str, Any]) -> dict[str, Any]:
        """Cross-check complete public grants against the retained worker proof."""
        _object(proof, _PROOF_FIELDS)
        for key, expected in {
            "epoch": self.epoch,
            "request_id": self.frame_id + 1,
            "frame_id": self.frame_id,
            "proof_schema": 1,
            "bytes": self.size,
            "granted_bytes": self.size,
            "accepted_bytes": self.size,
            "credit_chunks": (self.size + 65535) // 65536,
        }.items():
            _exact(proof[key], expected)
        requested = _integer(proof["requested_ns"], 1, _MAX)
        deadline = _integer(
            proof["deadline_ns"], requested + 1, min(_MAX, requested + 2_000_000_000)
        )
        verified = _integer(proof["verified_ns"], requested, deadline - 1)
        if len(self.events) != proof["credit_chunks"]:
            raise ValueError("Incomplete public credit")
        events = proof["credit_events"]
        if type(events) is not list or len(events) != 2 * len(self.events):
            raise ValueError("Invalid worker credit length")
        previous, previous_return = requested, requested
        for index, event in enumerate(self.events):
            _object(event, "token wait_received_ns grant_called_ns grant_returned_ns")
            expected = {
                "credit_schema": 2,
                "epoch": self.epoch,
                "frame_id": self.frame_id,
                "chunk_index": index,
                "offset": index * _CHUNK,
                "count": min(_CHUNK, self.size - index * _CHUNK),
            }
            _exact(event["token"], expected)
            wait, grant = events[2 * index : 2 * index + 2]
            for item, kind, sequence in (
                (wait, "wait", 2 * index + 1),
                (grant, "grant", 2 * index + 2),
            ):
                if type(item) is not list or len(item) != _EVENT_FIELDS:
                    raise ValueError("Invalid worker event")
                _exact(item[0], sequence)
                _exact(item[2], kind)
                _exact(item[3], index)
                previous = _integer(item[1], previous, verified)
            received = _integer(
                event["wait_received_ns"], max(wait[1], previous_return), deadline - 1
            )
            called = _integer(event["grant_called_ns"], received, deadline - 1)
            previous_return = _integer(event["grant_returned_ns"], called, deadline - 1)
            _integer(grant[1], called, verified)
        return {
            "frame_id": self.frame_id,
            "bytes": self.size,
            "chunks": len(self.events),
            "exact_sequence": True,
        }


def _terminal(raw: bytes, pid: int, *, cancelled: bool) -> dict[str, Any]:
    receipt = parse_json(raw)
    _object(receipt, "failure cleanup_failure not_launched helper passed sequence")
    for key, value in {
        "failure": "cancelled" if cancelled else None,
        "cleanup_failure": None,
        "not_launched": False,
        "passed": not cancelled,
    }.items():
        _exact(receipt[key], value)
    helper = _object(
        receipt["helper"],
        "pid producer_verified exit_code remaining_pids termination_requested "
        "cleanup_confirmed closed failure cleanup_errors",
    )
    _integer(pid, 1, 2**32 - 1)
    _exact(
        helper,
        {
            "pid": pid,
            "producer_verified": True,
            "exit_code": 1 if cancelled else 0,
            "remaining_pids": [],
            "termination_requested": cancelled,
            "cleanup_confirmed": True,
            "closed": True,
            "failure": None,
            "cleanup_errors": [],
        },
    )
    return _object(
        receipt["sequence"],
        "frames proof_sha256 sequence_sha256 frame_id bytes deadline_ns "
        "credit credit_cancellation",
    )


def _validate_target(actual: object, expected: dict[str, Any]) -> None:
    """Bind independently known descriptor, not an invented runtime ID oracle."""
    _object(expected, "pid process_created_100ns hwnd point")
    target = _object(
        actual, "pid process_created_100ns hwnd point runtime_id framework"
    )
    _exact({key: target[key] for key in expected}, expected)
    _exact(target["framework"], "WPF")
    runtime = target["runtime_id"]
    if type(runtime) is not list or not 1 <= len(runtime) <= _RUNTIME_LIMIT:
        raise ValueError("Invalid runtime ID")
    for item in runtime:
        _integer(item, -(2**31), 2**31 - 1)


def _public_calls(
    raw: bytes, case_deadline_ns: int, sequence_deadline_ns: int
) -> tuple[list[dict[str, Any]], int]:
    """Validate exactly two immutable public intervals against original caps."""
    cap = min(
        _integer(case_deadline_ns, 1, _MAX), _integer(sequence_deadline_ns, 1, _MAX)
    )
    envelope = _object(parse_json(raw, 1024), "calls")
    _exact(raw, canonical(envelope))
    calls = envelope["calls"]
    if type(calls) is not list or len(calls) != _FRAME_COUNT:
        raise ValueError("Expected two public observe intervals")
    previous = 1
    for index, value in enumerate(calls, 1):
        call = _object(value, "frame_id called_ns returned_ns")
        _exact(call["frame_id"], index)
        called = _integer(call["called_ns"], previous, cap - 1)
        previous = _integer(call["returned_ns"], called, cap - 1)
    return calls, cap


def compare_retained_sequence(  # noqa: C901, PLR0913, PLR0917, PLR0915 - independent evidence joins
    sequence: object,
    reference_rgb: bytes,
    terminal_bytes: bytes,
    expected_rect: tuple[int, ...],
    expected_target: bytes,
    expected_input: int,
    transcripts: tuple[CreditTranscript, ...],
    accepted_proof_digests: tuple[str, ...],
    *,
    pid: int,
    public_calls: bytes,
    case_deadline_ns: int,
    sequence_deadline_ns: int,
) -> dict[str, Any]:
    """Compare both original RGB sources off GUI and independently bind all proofs."""
    from verdiclip.capture.sequence_retention import (  # noqa: PLC0415 - installed type only
        VerifiedSequence,
    )

    if threading.current_thread() is threading.main_thread():
        raise ValueError("Consumer must run off GUI")
    if type(sequence) is not VerifiedSequence or type(reference_rgb) is not bytes:
        raise ValueError("Expected immutable result/reference")
    if type(expected_rect) is not tuple or len(expected_rect) != _RECT_FIELDS:
        raise ValueError("Expected immutable reference rectangle")
    calls, cap = _public_calls(public_calls, case_deadline_ns, sequence_deadline_ns)
    target = parse_json(expected_target, 4096)
    _integer(expected_input, 0, _MAX)
    if (
        len(sequence.frames) != _FRAME_COUNT
        or type(transcripts) is not tuple
        or len(transcripts) != _FRAME_COUNT
    ):
        raise ValueError("Expected exactly two frames")
    _exact(sequence.receipt_sha256, hashlib.sha256(terminal_bytes).digest())
    terminal = _terminal(terminal_bytes, pid, cancelled=False)
    chain, digests, diagnostics, previous_verified, first_state = (
        bytes(32),
        [],
        [],
        0,
        None,
    )
    for index, (frame, raw, transcript) in enumerate(
        zip(sequence.frames, sequence.proofs, transcripts, strict=True), 1
    ):
        proof = parse_json(raw, 12288)
        _exact(raw, canonical(proof))
        _exact(transcript.frame_id, index)
        _exact(transcript.epoch, sequence.epoch)
        diagnostics.append(transcript.verify(proof))
        call = calls[index - 1]
        _integer(
            transcript.events[0]["wait_received_ns"],
            call["returned_ns"],
            proof["deadline_ns"] - 1,
        )
        _integer(proof["requested_ns"], call["called_ns"], call["returned_ns"])
        _integer(
            proof["deadline_ns"],
            proof["requested_ns"] + 1,
            min(cap, proof["requested_ns"] + 2_000_000_000),
        )
        _integer(proof["requested_ns"], previous_verified, _MAX)
        previous_verified = proof["verified_ns"]
        wrapper = _object(
            proof["observation"],
            "sequence_schema epoch request_id frame_id observation",
        )
        for key, value in {
            "sequence_schema": 1,
            "epoch": sequence.epoch,
            "request_id": index + 1,
            "frame_id": index,
        }.items():
            _exact(wrapper[key], value)
        inner = _object(
            wrapper["observation"],
            "observation_schema epoch request_id frame_id target guard scroll settled",
        )
        for key, value in {
            "observation_schema": 1,
            "epoch": sequence.epoch,
            "request_id": index + 1,
            "frame_id": 1,
        }.items():
            _exact(inner[key], value)
        _validate_target(inner["target"], target)
        _exact(inner["guard"]["input_tick"], expected_input)
        _exact(inner["guard"]["capture_rect"], list(expected_rect))
        state = {key: inner[key] for key in ("target", "guard", "scroll")}
        if first_state is not None:
            _exact(state, first_state)
        first_state = state
        _exact(frame.observation, canonical(wrapper))
        _exact(frame.rgb, reference_rgb)
        left, top, right, bottom = expected_rect
        for coordinate in expected_rect:
            _integer(coordinate, -(2**31), 2**31 - 1)
        width, height = right - left, bottom - top
        _integer(width, 1, 1024)
        _integer(height, 1, 1024)
        _exact(len(reference_rgb), width * height * 3)
        observation_hash = hashlib.sha256(frame.observation).digest()
        rgb_hash = hashlib.sha256(reference_rgb).digest()
        expected_header = struct.pack(
            ">8sHH16sIIiiIIIQ32s32s",
            b"VCLPRGB\0",
            1,
            128,
            bytes.fromhex(sequence.epoch),
            index + 1,
            index,
            left,
            top,
            width,
            height,
            width * 3,
            len(reference_rgb),
            rgb_hash,
            observation_hash,
        )
        _exact(frame.header.encode(), expected_header)
        for name, digest in {
            "header_sha256": hashlib.sha256(expected_header).hexdigest(),
            "image_sha256": rgb_hash.hex(),
            "observation_sha256": observation_hash.hex(),
        }.items():
            _exact(proof[name], digest)
        digest = hashlib.sha256(raw).digest()
        _exact(frame.receipt_sha256, digest)
        digests.append(digest.hex())
        chain = hashlib.sha256(chain + digest).digest()
    last_proof = parse_json(sequence.proofs[-1], 12288)
    _exact(accepted_proof_digests, tuple(digests))
    _exact(sequence.chain, chain)
    _exact(
        terminal,
        {
            "frames": 2,
            "proof_sha256": digests,
            "sequence_sha256": chain.hex(),
            "frame_id": 2,
            "bytes": len(reference_rgb),
            "deadline_ns": None,
            "credit": {
                "pending": None,
                "granted_bytes": len(reference_rgb),
                "accepted_bytes": len(reference_rgb),
                "last_grant_sequence": 2 * last_proof["credit_chunks"],
                "cancel_sequence": 0,
                "cancel_time_ns": 0,
                "observed_sequence": 0,
                "observed_time_ns": 0,
                "failed": False,
                "events": last_proof["credit_events"],
            },
            "credit_cancellation": None,
        },
    )
    return {
        "schema": 1,
        "full_rgb_exact": True,
        "public_calls": calls,
        "case_deadline_ns": case_deadline_ns,
        "sequence_deadline_ns": sequence_deadline_ns,
        "public_request_bound": True,
        "consumer_off_gui": True,
        "frames": diagnostics,
        "sequence_sha256": chain.hex(),
        "receipt_sha256": sequence.receipt_sha256.hex(),
    }


def validate_cancel(  # noqa: PLR0913, PLR0917 - explicit public and worker evidence
    terminal_bytes: bytes,
    identity: dict[str, Any],
    frame1_transcript: CreditTranscript,
    frame1_accepted_digest: str,
    frame1_accepted_ns: int,
    public_hold: dict[str, Any],
    *,
    public_calls: bytes,
    case_deadline_ns: int,
    sequence_deadline_ns: int,
) -> dict[str, Any]:
    """Validate frame1 accepted history and exact frame2/chunk0 no-grant witness."""
    calls, cap = _public_calls(public_calls, case_deadline_ns, sequence_deadline_ns)
    first, second = calls
    first_bound = min(cap, first["called_ns"] + 2_000_000_000)
    _integer(
        frame1_accepted_ns,
        first["returned_ns"],
        min(first_bound - 1, second["called_ns"]),
    )
    _exact(frame1_transcript.frame_id, 1)
    _exact(frame1_transcript.epoch, identity["epoch"])
    _integer(frame1_accepted_ns, 1, _MAX)
    digest = frame1_accepted_digest
    if type(digest) is not str or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Invalid accepted frame1 digest")
    if len(frame1_transcript.events) != (frame1_transcript.size + 65535) // 65536:
        raise ValueError("Incomplete frame1 public grants")
    previous = first["returned_ns"]
    for index, event in enumerate(frame1_transcript.events):
        _object(event, "token wait_received_ns grant_called_ns grant_returned_ns")
        _exact(
            event["token"],
            {
                "credit_schema": 2,
                "epoch": identity["epoch"],
                "frame_id": 1,
                "chunk_index": index,
                "offset": index * 65536,
                "count": min(65536, frame1_transcript.size - index * 65536),
            },
        )
        for key in ("wait_received_ns", "grant_called_ns", "grant_returned_ns"):
            previous = _integer(event[key], previous, frame1_accepted_ns)
    terminal = _terminal(terminal_bytes, identity["pid"], cancelled=True)
    for key, value in {
        "frames": 1,
        "proof_sha256": [digest],
        "sequence_sha256": None,
        "frame_id": 2,
        "bytes": 0,
    }.items():
        _exact(terminal[key], value)
    deadline = _integer(
        terminal["deadline_ns"],
        second["returned_ns"] + 1,
        min(cap, second["returned_ns"] + 2_000_000_000),
    )
    # No private frame2 requested_ns exists after cancellation. This earlier
    # public-call bound is conservative, never a reconstructed worker deadline.
    deadline = min(deadline, second["called_ns"] + 2_000_000_000)
    hold = _object(
        public_hold, "token wait_received_ns cancel_called_ns cancel_returned_ns"
    )
    payload = {
        "credit_schema": 2,
        "epoch": identity["epoch"],
        "frame_id": 2,
        "chunk_index": 0,
        "offset": 0,
        "count": min(65536, frame1_transcript.size),
    }
    _exact(hold["token"], payload)
    pending = {key: value for key, value in payload.items() if key != "credit_schema"}
    pending["request_id"] = 3
    witness = _object(terminal["credit_cancellation"], "latched final")
    latched = _snapshot(witness["latched"], pending, 0, final=False)
    final = _snapshot(witness["final"], pending, 0, final=True)
    _exact(terminal["credit"], final)
    _exact(final["events"][:-1], latched["events"])
    _exact(final["cancel_time_ns"], latched["cancel_time_ns"])
    wait, cancel, observed = final["events"]
    received = _integer(
        hold["wait_received_ns"],
        max(wait[1], second["returned_ns"], frame1_accepted_ns),
        deadline - 1,
    )
    called = _integer(hold["cancel_called_ns"], received, deadline - 1)
    returned = _integer(hold["cancel_returned_ns"], called, deadline - 1)
    _integer(cancel[1], called, returned)
    _integer(observed[1], cancel[1], deadline - 1)
    return deepcopy(
        {
            "schema": 1,
            "held_frame": 2,
            "held_index": 0,
            "no_held_grant": True,
            "public_calls": calls,
            "case_deadline_ns": case_deadline_ns,
            "sequence_deadline_ns": sequence_deadline_ns,
            "cancellation_window": "conservative-public-call-plus-2s",
            "conservative_cancel_deadline_ns": deadline,
            "frame1_proof_sha256": digest,
            "worker_witness": witness,
        }
    )
