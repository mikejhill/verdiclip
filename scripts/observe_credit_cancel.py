"""Independent bounded held-credit cancellation proof; no production imports.

This validates protocol wait cancellation only. The caller separately proves
process retirement with retained native handles and controls the original outer
deadlines. Neither these records nor their timestamps prove OS backpressure.
"""

from __future__ import annotations

import json
import re
import struct
from copy import deepcopy
from typing import Any, cast

Json = dict[str, Any]
_CHUNK = 65_536
_MAX_FRAME = 1_572_864
_MAX_TIME = 2**63 - 1
_PREFIX_BYTES = 4
_RECORD_BYTES = 512
_EVENT_FIELDS = 4
_SNAPSHOT_FIELDS = (
    "pending granted_bytes accepted_bytes last_grant_sequence cancel_sequence "
    "cancel_time_ns observed_sequence observed_time_ns failed events"
)


def _object(value: object, fields: str) -> Json:
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("Cancellation proof fields differ")
    return cast("Json", value)


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Cancellation proof integer differs")
    return value


def _exact(actual: object, expected: object) -> None:
    if type(actual) is not type(expected):
        raise ValueError("Cancellation proof type differs")
    if isinstance(expected, dict):
        actual_object = _object(actual, " ".join(expected))
        for key, value in expected.items():
            _exact(actual_object[key], value)
    elif isinstance(expected, list):
        actual_list = cast("list[object]", actual)
        if len(actual_list) != len(expected):
            raise ValueError("Cancellation proof list differs")
        for item, wanted in zip(actual_list, expected, strict=True):
            _exact(item, wanted)
    elif actual != expected:
        raise ValueError("Cancellation proof value differs")


def _payload(epoch: object, size: int, index: int) -> Json:
    _integer(size, 1, _MAX_FRAME)
    _integer(index, 0, 1)
    if (
        type(epoch) is not str
        or re.fullmatch(r"[0-9a-f]{32}", epoch) is None
        or epoch == "0" * 32
        or size <= index * _CHUNK
    ):
        raise ValueError("Invalid held credit identity")
    return {
        "credit_schema": 1,
        "epoch": epoch,
        "frame_id": 1,
        "chunk_index": index,
        "offset": index * _CHUNK,
        "count": min(_CHUNK, size - index * _CHUNK),
    }


def _unique(pairs: list[tuple[str, object]]) -> Json:
    result: Json = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate held credit field")
        result[key] = value
    return result


def parse_held_token(  # noqa: PLR0913, PLR0917 - explicit independent inputs
    generation: int,
    raw: bytes,
    expected_generation: int,
    epoch: str,
    size: int,
    held_index: int,
) -> Json:
    """Independently validate one complete fixed wait record, including its prefix."""
    _integer(expected_generation, 1, _MAX_TIME)
    _integer(generation, expected_generation, expected_generation)
    payload = _payload(epoch, size, held_index)
    if (
        type(raw) is not bytes
        or not _PREFIX_BYTES < len(raw) <= _RECORD_BYTES
        or struct.unpack(">I", raw[:_PREFIX_BYTES])[0] != len(raw) - _PREFIX_BYTES
    ):
        raise ValueError("Invalid held credit record size")
    try:
        message = json.loads(
            raw[_PREFIX_BYTES:].decode("utf-8"), object_pairs_hook=_unique
        )
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid held credit JSON") from error
    _exact(
        message,
        {"version": 2, "kind": "frame_wait", "request_id": 1, "payload": payload},
    )
    return payload


def _helper(receipt: Json, identity: Json) -> Json:
    _object(receipt, "failure cleanup_failure not_launched helper passed observe")
    for key, expected in {
        "failure": "cancelled",
        "cleanup_failure": None,
        "not_launched": False,
        "passed": False,
    }.items():
        _exact(receipt[key], expected)
    helper = _object(
        receipt["helper"],
        "pid producer_verified exit_code remaining_pids "
        "termination_requested cleanup_confirmed closed failure cleanup_errors",
    )
    pid = _integer(identity.get("pid"), 1, 2**32 - 1)
    _integer(helper["exit_code"], 1, 1)
    for key, expected in {
        "pid": pid,
        "producer_verified": True,
        "remaining_pids": [],
        "termination_requested": True,
        "cleanup_confirmed": True,
        "closed": True,
        "failure": None,
        "cleanup_errors": [],
    }.items():
        _exact(helper[key], expected)
    return _object(
        receipt["observe"],
        "deadline_ns first_body_ns validated_ns finished_ns bytes "
        "transfer_messages credit credit_cancellation image_sha256 observation",
    )


def _snapshot(value: object, pending: Json, index: int, *, final: bool) -> Json:
    snapshot = _object(value, _SNAPSHOT_FIELDS)
    cancel_sequence = 2 * index + 2
    for key, expected in {
        "pending": pending,
        "granted_bytes": index * _CHUNK,
        "accepted_bytes": index * _CHUNK,
        "last_grant_sequence": 2 * index,
        "cancel_sequence": cancel_sequence,
        "failed": True,
        "observed_sequence": cancel_sequence + 1 if final else 0,
    }.items():
        _exact(snapshot[key], expected)
    _integer(snapshot["cancel_time_ns"], 1, _MAX_TIME)
    if final:
        _integer(snapshot["observed_time_ns"], snapshot["cancel_time_ns"], _MAX_TIME)
    else:
        _exact(snapshot["observed_time_ns"], 0)
    events = snapshot["events"]
    if type(events) is not list or len(events) != cancel_sequence + int(final):
        raise ValueError("Invalid bounded cancellation events")
    previous = 0
    for sequence, event in enumerate(events, 1):
        if type(event) is not list or len(event) != _EVENT_FIELDS:
            raise ValueError("Invalid cancellation event shape")
        _integer(event[0], sequence, sequence)
        previous = _integer(event[1], max(1, previous), _MAX_TIME)
        if sequence < cancel_sequence:
            _exact(event[2], "wait" if sequence % 2 else "grant")
            _integer(event[3], (sequence - 1) // 2, (sequence - 1) // 2)
        else:
            _exact(event[2], "cancel" if sequence == cancel_sequence else "observed")
            _exact(event[3], None)
    _exact(events[cancel_sequence - 1][1], snapshot["cancel_time_ns"])
    if final:
        _exact(events[-1][1], snapshot["observed_time_ns"])
    return snapshot


def _public_order(  # noqa: PLR0913, PLR0917 - independent event correlation
    hold: Json,
    prefix: object,
    events: list[Any],
    epoch: str,
    size: int,
    index: int,
    deadline: int,
) -> None:
    if type(prefix) is not list or len(prefix) != index:
        raise ValueError("Invalid public grant prefix")
    previous_return = 0
    for chunk, entry in enumerate(prefix):
        public = _object(
            entry, "token wait_received_ns grant_called_ns grant_returned_ns"
        )
        _exact(public["token"], _payload(epoch, size, chunk))
        wait, grant = events[2 * chunk : 2 * chunk + 2]
        received = _integer(
            public["wait_received_ns"], max(wait[1], previous_return), deadline - 1
        )
        called = _integer(public["grant_called_ns"], received, deadline - 1)
        previous_return = _integer(public["grant_returned_ns"], called, deadline - 1)
        _integer(grant[1], called, deadline - 1)
    held_wait, cancelled, observed = events[-3:]
    received = _integer(
        hold["wait_received_ns"], max(held_wait[1], previous_return), deadline - 1
    )
    called = _integer(hold["cancel_called_ns"], received, deadline - 1)
    returned = _integer(hold["cancel_returned_ns"], called, deadline - 1)
    _integer(cancelled[1], called, returned)
    _integer(observed[1], cancelled[1], deadline - 1)


def validate_cancel(  # noqa: PLR0913 - independent proof inputs
    receipt: Json,
    public_hold: Json,
    prefix_events: list[Json],
    identity: Json,
    held_index: int,
    *,
    size: int,
) -> Json:
    """Prove an ungranted first/interior wait and confirmed cancelled cleanup.

    ``size`` is the independently known reference RGB length. ``identity`` is
    the independently retained peer identity, including its epoch and PID.
    Returns detached evidence, never the mutable caller dictionaries.
    """
    if type(identity) is not dict:
        raise ValueError("Invalid independent peer identity")
    payload = _payload(identity.get("epoch"), size, held_index)
    hold = _object(
        public_hold, "token wait_received_ns cancel_called_ns cancel_returned_ns"
    )
    _exact(hold["token"], payload)
    pending = {key: value for key, value in payload.items() if key != "credit_schema"}
    pending["request_id"] = 1
    observe = _helper(receipt, identity)
    for key in ("validated_ns", "finished_ns", "image_sha256", "observation"):
        _exact(observe[key], None)
    _exact(observe["bytes"], held_index * _CHUNK)
    _exact(observe["transfer_messages"], 3 + 2 * held_index)
    deadline = _integer(observe["deadline_ns"], 1, _MAX_TIME)
    witness = _object(observe["credit_cancellation"], "latched final")
    latched = _snapshot(witness["latched"], pending, held_index, final=False)
    final = _snapshot(witness["final"], pending, held_index, final=True)
    _exact(observe["credit"], final)
    _exact(final["events"][:-1], latched["events"])
    _exact(final["cancel_time_ns"], latched["cancel_time_ns"])
    _public_order(
        hold,
        prefix_events,
        final["events"],
        identity["epoch"],
        size,
        held_index,
        deadline,
    )
    if held_index:
        _integer(
            observe["first_body_ns"], final["events"][1][1], hold["wait_received_ns"]
        )
    else:
        _exact(observe["first_body_ns"], None)
    return deepcopy(
        {
            "schema": 1,
            "held_index": held_index,
            "accepted_bytes": held_index * _CHUNK,
            "granted_bytes": held_index * _CHUNK,
            "public_hold": hold,
            "public_prefix": prefix_events,
            "worker_witness": witness,
            "exact_sequence": True,
            "no_held_grant": True,
        }
    )
