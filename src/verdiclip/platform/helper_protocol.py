"""Bounded helper framing and single-flight correlation, independent of transport.

Extracted unchanged from the inert native pipe proof. No application path uses
this module yet. Payload meaning, peer identity, deadlines, clean EOF and process
retirement belong to the future lifecycle owner, not this framing layer.
Message is a frozen envelope, but its payload dictionary is not deeply immutable;
thread boundaries must use serialized bytes or an explicit immutable snapshot.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass

MAX_MESSAGE_BYTES = 16_384
MAX_REQUESTS = 128
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 1024
HEADER_BYTES = 4
VERSION = 2
MAX_CREDIT_RECORD_BYTES = 512


class ProtocolError(ValueError):
    """A permanently rejected framed message or request transition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(*, condition: bool, code: str) -> None:
    """One explicit failed requirement feeds the terminal rejection boundary."""
    if not condition:
        raise ProtocolError(code)


def bounded_json(value: object) -> None:
    """Reject deep, excessive or nonfinite values, including exponent overflow."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ProtocolError("json-complexity")
        if isinstance(item, float) and not math.isfinite(item):
            raise ProtocolError("nonfinite-json")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Do not let duplicate JSON keys replace identity/protocol fields."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate-key")
        result[key] = value
    return result


def invalid_constant(_value: str) -> object:
    """Reject JSON's nonstandard nonfinite constants."""
    raise ProtocolError("nonfinite-json")


@dataclass(frozen=True)
class Message:
    """One explicit bounded envelope; phase-specific payloads are checked separately."""

    kind: str
    request_id: int
    payload: dict[str, object]
    version: int = VERSION


def decode(payload: bytes) -> Message:
    """Validate UTF-8, JSON structure and exact envelope types."""
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
    except ProtocolError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError("malformed-json") from error
    if not isinstance(value, dict) or set(value) != {
        "version",
        "kind",
        "request_id",
        "payload",
    }:
        raise ProtocolError("invalid-envelope")
    version, request_id = value["version"], value["request_id"]
    if type(version) is not int or version != VERSION or type(request_id) is not int:
        raise ProtocolError("invalid-envelope")
    if not 0 <= request_id <= MAX_REQUESTS or value["kind"] not in (
        "ready",
        "request",
        "response",
        "frame_wait",
        "frame_credit",
    ):
        raise ProtocolError("invalid-envelope")
    if not isinstance(value["payload"], dict):
        raise ProtocolError("invalid-envelope")
    if value["kind"] in ("frame_wait", "frame_credit") and (
        len(payload) + HEADER_BYTES > MAX_CREDIT_RECORD_BYTES
    ):
        raise ProtocolError("oversized-credit-record")
    bounded_json(value)
    return Message(value["kind"], request_id, value["payload"], version)


def encode(message: Message) -> bytes:
    """Length-prefix a checked envelope without permitting nonfinite JSON."""
    payload = json.dumps(
        asdict(message), allow_nan=False, separators=(",", ":")
    ).encode()
    decode(payload)
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ProtocolError("oversized")
    return struct.pack(">I", len(payload)) + payload


class Decoder:
    """Incremental parser with bounded buffering and a permanent failure latch."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> list[Message]:
        """Accept fragmentation/coalescing without trusting an unbounded length."""
        if self.failed:
            raise ProtocolError("decoder-failed")
        try:
            return self._feed(data)
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise

    def _feed(self, data: bytes) -> list[Message]:
        if len(data) + len(self.buffer) > MAX_MESSAGE_BYTES + HEADER_BYTES:
            raise ProtocolError("buffer-limit")
        self.buffer.extend(data)
        messages = []
        while len(self.buffer) >= HEADER_BYTES:
            size = struct.unpack(">I", self.buffer[:HEADER_BYTES])[0]
            if not 0 < size <= MAX_MESSAGE_BYTES:
                raise ProtocolError("oversized")
            total = HEADER_BYTES + size
            if len(self.buffer) < total:
                break
            messages.append(decode(bytes(self.buffer[HEADER_BYTES:total])))
            del self.buffer[:total]
        return messages


class SingleFlight:
    """One request at a time, with no recovery from unsolicited/duplicate replies."""

    def __init__(self) -> None:
        self.pending: int | None = None
        self.completed = 0
        self.failed = False

    def begin(self, payload: dict[str, object]) -> Message:
        """Reserve the next identifier before scheduling its bytes."""
        if self.failed or self.pending is not None or self.completed >= MAX_REQUESTS:
            raise ProtocolError("single-flight")
        self.pending = self.completed + 1
        return Message("request", self.pending, payload)

    def accept(self, message: Message) -> None:
        """Validate a reply before clearing the sole outstanding request."""
        code = None
        if self.failed:
            code = "single-flight"
        elif message.kind != "response":
            code = "unexpected-kind"
        elif message.request_id <= self.completed:
            code = "duplicate"
        elif self.pending is None or message.request_id != self.pending:
            code = "wrong-id"
        if code is not None:
            self.failed = True
            raise ProtocolError(code)
        self.completed = message.request_id
        self.pending = None
