"""Independent held-credit proof tests using genuine production state snapshots."""

from __future__ import annotations

import json
import struct
import time
from copy import deepcopy
from dataclasses import asdict
from typing import Any

import pytest
from scripts.observe_credit_cancel import parse_held_token, validate_cancel

from verdiclip.capture.frame_credit import CreditSnapshot, CreditToken, ParentCredit

Json = dict[str, Any]
EPOCH = "ab" * 16
SIZE = 2 * 65_536 + 3


def compact(snapshot: CreditSnapshot) -> Json:
    """Use the documented receipt representation of an actual worker snapshot."""
    result = asdict(snapshot)
    result["events"] = [
        [
            event.sequence,
            event.time_ns,
            event.kind,
            event.token.chunk_index if event.token is not None else None,
        ]
        for event in snapshot.events
    ]
    return result


def evidence(index: int) -> tuple[Json, Json, list[Json], Json]:
    """Perform real credit accounting and cancellation, not synthetic witnesses."""
    parent = ParentCredit(EPOCH, 1, SIZE)
    prefix: list[Json] = []
    first_body: int | None = None
    for chunk in range(index):
        credit = CreditToken.for_chunk(EPOCH, 1, SIZE, chunk)
        parent.receive_wait(credit)
        event = {
            "token": credit.payload(),
            "wait_received_ns": time.monotonic_ns(),
            "grant_called_ns": time.monotonic_ns(),
        }
        parent.reserve_grant(credit)
        event["grant_returned_ns"] = time.monotonic_ns()
        prefix.append(event)
        parent.accept_body(credit.count)
        first_body = time.monotonic_ns()
    held = CreditToken.for_chunk(EPOCH, 1, SIZE, index)
    parent.receive_wait(held)
    public = {
        "token": held.payload(),
        "wait_received_ns": time.monotonic_ns(),
        "cancel_called_ns": time.monotonic_ns(),
    }
    parent.cancel()
    public["cancel_returned_ns"] = time.monotonic_ns()
    parent.observe_cancel()
    witness = parent.witness()
    latched, final = compact(witness.latched), compact(witness.final)
    receipt = {
        "failure": "cancelled",
        "cleanup_failure": None,
        "not_launched": False,
        "passed": False,
        "helper": {
            "pid": 321,
            "producer_verified": True,
            "exit_code": 1,
            "remaining_pids": [],
            "termination_requested": True,
            "cleanup_confirmed": True,
            "closed": True,
            "failure": None,
            "cleanup_errors": [],
        },
        "observe": {
            "deadline_ns": time.monotonic_ns() + 2_000_000_000,
            "first_body_ns": first_body,
            "validated_ns": None,
            "finished_ns": None,
            "bytes": index * 65_536,
            "transfer_messages": 3 + 2 * index,
            "credit": final,
            "credit_cancellation": {"latched": latched, "final": final},
            "image_sha256": None,
            "observation": None,
        },
    }
    return receipt, public, prefix, {"epoch": EPOCH, "pid": 321}


def record(index: int = 0) -> bytes:
    """Encode an independent fixed control record without production codecs."""
    payload = CreditToken.for_chunk(EPOCH, 1, SIZE, index).payload()
    body = json.dumps(
        {"version": 2, "kind": "frame_wait", "request_id": 1, "payload": payload},
        separators=(",", ":"),
    ).encode()
    return struct.pack(">I", len(body)) + body


@pytest.mark.parametrize("index", [0, 1])
def test_real_snapshots_qualify(index: int) -> None:
    """Real first and interior waits yield immutable independent proof results."""
    receipt, hold, prefix, identity = evidence(index)
    result = validate_cancel(receipt, hold, prefix, identity, index, size=SIZE)
    assert result["held_index"] == index
    assert result["no_held_grant"] is True
    assert result["accepted_bytes"] == result["granted_bytes"] == index * 65_536
    assert result["worker_witness"] == receipt["observe"]["credit_cancellation"]
    assert result["public_prefix"] == prefix
    hold["token"]["count"] = 1
    assert result["public_hold"]["token"]["count"] == 65_536
    assert (
        parse_held_token(2, record(index), 2, EPOCH, SIZE, index)["chunk_index"]
        == index
    )


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        (("passed",), True),
        (("failure",), "timeout"),
        (("not_launched",), True),
        (("cleanup_failure",), "timeout"),
        (("helper", "pid"), 322),
        (("helper", "pid"), True),
        (("helper", "producer_verified"), False),
        (("helper", "exit_code"), None),
        (("helper", "exit_code"), 0),
        (("helper", "exit_code"), 23),
        (("helper", "exit_code"), True),
        (("helper", "remaining_pids"), [321]),
        (("helper", "cleanup_errors"), ["error"]),
        (("helper", "failure"), "error"),
        (("helper", "closed"), False),
        (("helper", "termination_requested"), False),
        (("helper", "cleanup_confirmed"), False),
        (("observe", "observation"), {}),
        (("observe", "image_sha256"), "a" * 64),
        (("observe", "validated_ns"), 1),
        (("observe", "finished_ns"), 1),
        (("observe", "bytes"), 0),
        (("observe", "bytes"), True),
        (("observe", "transfer_messages"), 6),
        (("observe", "deadline_ns"), 1),
        (("observe", "first_body_ns"), None),
        (("observe", "credit_cancellation"), None),
    ],
)
def test_reject_terminal_tampering(path: tuple[str, ...], bad: object) -> None:
    """Failed disposal or any admitted image invalidates cancellation proof."""
    receipt, hold, prefix, identity = evidence(1)
    target = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(
        ValueError, match=r"[Cc]ancellation|[Hh]eld|public grant prefix"
    ):
        validate_cancel(receipt, hold, prefix, identity, 1, size=SIZE)


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("pending", None),
        ("granted_bytes", 65_537),
        ("accepted_bytes", 65_535),
        ("last_grant_sequence", 4),
        ("cancel_sequence", 0),
        ("cancel_time_ns", 0),
        ("observed_sequence", 0),
        ("observed_time_ns", 0),
        ("failed", False),
        ("events", []),
    ],
)
def test_reject_forged_snapshot(key: str, bad: object) -> None:
    """Even consistently forged final snapshots cannot hide wrong accounting."""
    receipt, hold, prefix, identity = evidence(1)
    receipt["observe"]["credit_cancellation"]["final"][key] = bad
    with pytest.raises(
        ValueError, match=r"[Cc]ancellation|[Hh]eld|public grant prefix"
    ):
        validate_cancel(receipt, hold, prefix, identity, 1, size=SIZE)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "tuple",
        "sequence",
        "time",
        "bool",
        "direction",
        "held_grant",
        "identity",
        "null_token",
        "cancel_time",
        "observed_time",
    ],
)
def test_reject_forged_event_sequence(mutation: str) -> None:  # noqa: C901 - explicit adversarial variants
    """Bounded exact event shape and order prevent a forged no-grant witness."""
    receipt, hold, prefix, identity = evidence(1)
    witness = receipt["observe"]["credit_cancellation"]
    events = witness["final"]["events"]
    if mutation == "extra":
        events.append(events[-1])
    elif mutation == "missing":
        events.pop()
    elif mutation == "tuple":
        events[0] = tuple(events[0])
    elif mutation == "sequence":
        events[0][0] = 2
    elif mutation == "time":
        events[1][1] = events[0][1] - 1
    elif mutation == "bool":
        events[0][3] = False
    elif mutation == "direction":
        events[0][2] = "grant"
    elif mutation == "held_grant":
        events[2][2] = "grant"
    elif mutation == "identity":
        witness["final"]["pending"]["request_id"] = 2
    elif mutation == "null_token":
        events[-1][3] = 1
    elif mutation == "cancel_time":
        witness["final"]["cancel_time_ns"] += 1
    else:
        witness["final"]["observed_time_ns"] += 1
    with pytest.raises(
        ValueError, match=r"[Cc]ancellation|[Hh]eld|public grant prefix"
    ):
        validate_cancel(receipt, hold, prefix, identity, 1, size=SIZE)


@pytest.mark.parametrize(
    "mutation",
    [
        "hold_grant",
        "hold_bool",
        "hold_epoch",
        "before_wait",
        "late_cancel",
        "late_return",
        "latch_outside_call",
        "prefix_missing",
        "prefix_extra",
        "prefix_token",
        "prefix_called",
        "prefix_returned",
        "prefix_tuple",
        "extra_receipt",
        "extra_snapshot",
        "different_latch",
        "credit_differs",
        "first_body",
        "identity",
    ],
)
def test_reject_public_or_cross_snapshot_tampering(mutation: str) -> None:  # noqa: C901, PLR0912 - explicit adversarial variants
    """Public proof must correlate exact worker events and original deadlines."""
    receipt, hold, prefix, identity = evidence(1)
    observe = receipt["observe"]
    if mutation == "hold_grant":
        hold["grant_called_ns"] = hold["cancel_called_ns"]
    elif mutation == "hold_bool":
        hold["token"]["frame_id"] = True
    elif mutation == "hold_epoch":
        hold["token"]["epoch"] = "cd" * 16
    elif mutation == "before_wait":
        hold["wait_received_ns"] = 1
    elif mutation == "late_cancel":
        hold["cancel_called_ns"] = observe["deadline_ns"]
    elif mutation == "late_return":
        hold["cancel_returned_ns"] = observe["deadline_ns"]
    elif mutation == "latch_outside_call":
        hold["cancel_called_ns"] = observe["credit"]["cancel_time_ns"] + 1
    elif mutation == "prefix_missing":
        prefix.clear()
    elif mutation == "prefix_extra":
        prefix.append(prefix[0])
    elif mutation == "prefix_token":
        prefix[0]["token"]["offset"] = 1
    elif mutation == "prefix_called":
        prefix[0]["grant_called_ns"] = observe["credit"]["events"][1][1] + 1
    elif mutation == "prefix_returned":
        prefix[0]["grant_returned_ns"] = hold["wait_received_ns"] + 1
    elif mutation == "prefix_tuple":
        prefix[0]["token"] = []
    elif mutation == "extra_receipt":
        receipt["extra"] = 1
    elif mutation == "extra_snapshot":
        observe["credit"]["extra"] = 1
    elif mutation == "different_latch":
        observe["credit_cancellation"]["latched"]["events"][0][1] += 1
    elif mutation == "credit_differs":
        observe["credit"] = deepcopy(observe["credit"])
        observe["credit"]["pending"] = None
    elif mutation == "first_body":
        observe["first_body_ns"] = hold["wait_received_ns"] + 1
    else:
        identity["pid"] = 322
    with pytest.raises(
        ValueError, match=r"[Cc]ancellation|[Hh]eld|public grant prefix"
    ):
        validate_cancel(receipt, hold, prefix, identity, 1, size=SIZE)


@pytest.mark.parametrize(
    "mutation",
    [
        "prefix",
        "short",
        "oversize",
        "duplicate",
        "kind",
        "version",
        "request",
        "schema",
        "extra",
        "float",
        "bool",
        "nan",
        "utf8",
        "generation",
        "held_index",
        "size",
        "epoch",
    ],
)
def test_strict_held_record(mutation: str) -> None:  # noqa: C901, PLR0912 - explicit adversarial variants
    """The independent parser rejects alternate envelopes and malformed records."""
    raw = record()
    generation, index, size, epoch = 2, 0, SIZE, EPOCH
    if mutation == "prefix":
        raw = b"\0\0\0\1" + raw[4:]
    elif mutation == "short":
        raw = b"\0\0\0\0"
    elif mutation == "oversize":
        raw = struct.pack(">I", 509) + raw[4:].ljust(509, b" ")
    elif mutation == "generation":
        generation = 3
    elif mutation == "held_index":
        index = 2
    elif mutation == "size":
        size = 0
    elif mutation == "epoch":
        epoch = "0" * 32
    else:
        body = raw[4:]
        if mutation == "duplicate":
            body = body.replace(b'"version":2', b'"version":2,"version":2')
        elif mutation == "kind":
            body = body.replace(b"frame_wait", b"frame_credit")
        elif mutation == "version":
            body = body.replace(b'"version":2', b'"version":1')
        elif mutation == "request":
            body = body.replace(b'"request_id":1', b'"request_id":2')
        elif mutation == "schema":
            body = body.replace(b'"credit_schema":1', b'"credit_schema":2')
        elif mutation == "extra":
            body = body.replace(b'"version":2', b'"extra":1,"version":2')
        elif mutation == "float":
            body = body.replace(b'"frame_id":1', b'"frame_id":1.0')
        elif mutation == "bool":
            body = body.replace(b'"frame_id":1', b'"frame_id":true')
        elif mutation == "nan":
            body = body.replace(b'"frame_id":1', b'"frame_id":NaN')
        else:
            body = b"\xff"
        raw = struct.pack(">I", len(body)) + body
    with pytest.raises(
        ValueError, match=r"[Cc]ancellation|[Hh]eld|public grant prefix"
    ):
        parse_held_token(generation, raw, 2, epoch, size, index)


def test_record_includes_length_prefix_in_exact_cap() -> None:
    """Exactly 512 complete bytes are allowed, not 512 plus the prefix."""
    body = record()[4:].ljust(508, b" ")
    result = parse_held_token(2, struct.pack(">I", len(body)) + body, 2, EPOCH, SIZE, 0)
    assert result["count"] == 65_536
    with pytest.raises(ValueError, match="record size"):
        parse_held_token(2, struct.pack(">I", 509) + body + b" ", 2, EPOCH, SIZE, 0)


@pytest.mark.parametrize("index", [0, 1])
def test_real_reservation_before_cancel_cannot_qualify(index: int) -> None:
    """A real held-token reservation is disqualifying even if no write occurs."""
    receipt, hold, prefix, identity = evidence(index)
    parent = ParentCredit(EPOCH, 1, SIZE)
    for chunk in range(index + 1):
        credit = CreditToken.for_chunk(EPOCH, 1, SIZE, chunk)
        parent.receive_wait(credit)
        parent.reserve_grant(credit)
        if chunk < index:
            parent.accept_body(credit.count)
    parent.cancel()
    parent.observe_cancel()
    witness = parent.witness()
    receipt["observe"]["credit_cancellation"] = {
        "latched": compact(witness.latched),
        "final": compact(witness.final),
    }
    receipt["observe"]["credit"] = compact(witness.final)
    with pytest.raises(ValueError, match="Cancellation"):
        validate_cancel(receipt, hold, prefix, identity, index, size=SIZE)


def test_real_partial_previous_body_cannot_qualify() -> None:
    """An interior wait with outstanding prior credit is not a held-grant proof."""
    receipt, hold, prefix, identity = evidence(1)
    parent = ParentCredit(EPOCH, 1, SIZE)
    first = CreditToken.for_chunk(EPOCH, 1, SIZE, 0)
    parent.receive_wait(first)
    parent.reserve_grant(first)
    parent.accept_body(first.count - 1)
    parent.receive_wait(CreditToken.for_chunk(EPOCH, 1, SIZE, 1))
    parent.cancel()
    parent.observe_cancel()
    witness = parent.witness()
    receipt["observe"]["credit_cancellation"] = {
        "latched": compact(witness.latched),
        "final": compact(witness.final),
    }
    receipt["observe"]["credit"] = compact(witness.final)
    with pytest.raises(ValueError, match="Cancellation"):
        validate_cancel(receipt, hold, prefix, identity, 1, size=SIZE)


def test_non_object_identity_rejected() -> None:
    """A missing independently retained identity cannot qualify cleanup."""
    receipt, hold, prefix, _ = evidence(0)
    with pytest.raises(ValueError, match="independent peer identity"):
        validate_cancel(receipt, hold, prefix, None, 0, size=SIZE)  # ty: ignore[invalid-argument-type]


def test_validator_has_no_production_imports() -> None:
    """The harness checks its own schema without trusting production validators."""
    import ast  # noqa: PLC0415 - inspect only this standalone validator
    import inspect  # noqa: PLC0415 - inspect only this standalone validator

    from scripts import observe_credit_cancel  # noqa: PLC0415

    tree = ast.parse(inspect.getsource(observe_credit_cancel))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(
        not (node.module or "").startswith("verdiclip")
        if isinstance(node, ast.ImportFrom)
        else all(not alias.name.startswith("verdiclip") for alias in node.names)
        for node in imports
    )
