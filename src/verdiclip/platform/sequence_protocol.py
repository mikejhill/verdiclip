"""Separate bounded sequence3 framing, independent of transport.

Extracted unchanged from the inert native pipe proof. No application path uses
this module yet. Payload meaning, peer identity, deadlines, clean EOF and process
retirement belong to the future lifecycle owner, not this framing layer.
Message is a frozen envelope, but its payload dictionary is not deeply immutable;
thread boundaries must use serialized bytes or an explicit immutable snapshot.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity
from verdiclip.capture.observe_protocol import Observation, ObserveTarget
from verdiclip.platform.helper_protocol import (
    ProtocolError,
    bounded_json,
    invalid_constant,
    unique_object,
)

MAX_MESSAGE_BYTES = 16_384
MAX_REQUESTS = 32
MAX_SEQUENCE_FRAMES = 8
MAX_SEQUENCE_CONTROL_RECORDS = 512
MAX_FRAME_CONTROL_RECORDS = 64
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 1024
HEADER_BYTES = 4
VERSION = 3
MAX_CREDIT_RECORD_BYTES = 512


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
        "frame_accept",
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
        """Init."""
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
        """Feed."""
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


class SequenceBudget:
    """Count both directions before enqueue; never replenish on frame completion."""

    def __init__(self) -> None:
        """Init."""
        self.records = 0
        self.frames = [0] * MAX_SEQUENCE_FRAMES
        self.failed = False

    def record(self, frame_id: int | None = None) -> None:
        """Reserve one bounded control record or fail permanently."""
        if self.failed:
            raise ProtocolError("sequence-budget-failed")
        if self.records >= MAX_SEQUENCE_CONTROL_RECORDS or (
            frame_id is not None
            and (
                type(frame_id) is not int
                or not 1 <= frame_id <= MAX_SEQUENCE_FRAMES
                or self.frames[frame_id - 1] >= MAX_FRAME_CONTROL_RECORDS
            )
        ):
            self.failed = True
            raise ProtocolError("sequence-record-bound")
        self.records += 1
        if frame_id is not None:
            self.frames[frame_id - 1] += 1

    def attribute_frame(self, frame_id: int) -> None:
        """Classify an already globally reserved inbound request after decoding."""
        if (
            self.failed
            or type(frame_id) is not int
            or not 1 <= frame_id <= MAX_SEQUENCE_FRAMES
            or self.frames[frame_id - 1] >= MAX_FRAME_CONTROL_RECORDS
        ):
            self.failed = True
            raise ProtocolError("sequence-frame-record-bound")
        self.frames[frame_id - 1] += 1


@dataclass(frozen=True, slots=True)
class SequenceObservation:
    """Explicit sequence binding around the unchanged provider schema1 evidence."""

    canonical: bytes

    def __post_init__(self) -> None:
        """Post init."""
        self._validated()

    def _validated(self) -> tuple[dict[str, Any], Observation]:
        """Validated."""
        if (
            type(self.canonical) is not bytes
            or not 0 < len(self.canonical) <= MAX_OBSERVATION_BYTES
        ):
            raise ProtocolError("sequence-observation-bound")
        try:
            value = json.loads(
                self.canonical,
                object_pairs_hook=unique_object,
                parse_constant=invalid_constant,
            )
            bounded_json(value)
            if type(value) is not dict or set(value) != {
                "sequence_schema",
                "epoch",
                "request_id",
                "frame_id",
                "observation",
            }:
                raise ProtocolError("sequence-observation-fields")
            if (
                type(value["sequence_schema"]) is not int
                or value["sequence_schema"] != 1
            ):
                raise ProtocolError("sequence-observation-schema")
            for name, limit in (("frame_id", 8), ("request_id", 32)):
                if type(value[name]) is not int or not 1 <= value[name] <= limit:
                    raise ProtocolError("sequence-observation-id")
            inner = Observation.parse(
                json.dumps(
                    value["observation"], separators=(",", ":"), sort_keys=True
                ).encode()
            )
            identity = inner.frame_identity()
            if (
                value["epoch"] != identity.session_id.hex()
                or value["request_id"] != identity.request_id
            ):
                raise ProtocolError("sequence-observation-correlation")
            if (
                self.canonical
                != json.dumps(
                    value, separators=(",", ":"), sort_keys=True, allow_nan=False
                ).encode()
            ):
                raise ProtocolError("sequence-observation-noncanonical")
            result = value, inner
        except (TypeError, ValueError, UnicodeError) as error:
            raise ProtocolError("invalid-sequence-observation") from error
        else:
            return result

    @classmethod
    def from_observation(
        cls, observation: Observation, frame_id: int
    ) -> SequenceObservation:
        """From observation."""
        identity = observation.frame_identity()
        return cls(
            json.dumps(
                {
                    "sequence_schema": 1,
                    "epoch": identity.session_id.hex(),
                    "request_id": identity.request_id,
                    "frame_id": frame_id,
                    "observation": json.loads(observation.canonical),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )

    @classmethod
    def parse(cls, data: bytes) -> SequenceObservation:
        """Parse."""
        return cls(data)

    @property
    def digest(self) -> bytes:
        """Digest."""
        return hashlib.sha256(self.canonical).digest()

    @property
    def target(self) -> ObserveTarget:
        """Target."""
        return self._validated()[1].target

    def frame_identity(self) -> FrameIdentity:
        """Frame identity."""
        value, inner = self._validated()
        return replace(
            inner.frame_identity(),
            frame_id=value["frame_id"],
            observation_sha256=self.digest,
        )


MAX_FRAME_PROOF_BYTES = 12288
MAX_OBSERVATION_BYTES = 8192
_DIGEST_HEX_LENGTH = 64
_EVENT_FIELDS = 4


@dataclass(frozen=True, slots=True)
class SequenceFrameProof:
    """Bounded canonical acceptance evidence, containing metadata but never RGB."""

    canonical: bytes

    def __post_init__(self) -> None:
        """Post init."""
        if (
            type(self.canonical) is not bytes
            or not 0 < len(self.canonical) <= MAX_FRAME_PROOF_BYTES
        ):
            raise ProtocolError("frame-proof-bound")
        value = self.value
        if set(value) != {
            "proof_schema",
            "epoch",
            "request_id",
            "frame_id",
            "requested_ns",
            "deadline_ns",
            "verified_ns",
            "header_sha256",
            "observation_sha256",
            "image_sha256",
            "bytes",
            "credit_chunks",
            "granted_bytes",
            "accepted_bytes",
            "credit_events",
            "observation",
        }:
            raise ProtocolError("frame-proof-fields")
        if (
            self.canonical
            != json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ):
            raise ProtocolError("frame-proof-noncanonical")
        limits = {
            "proof_schema": (1, 1),
            "request_id": (1, 32),
            "frame_id": (1, 8),
            "requested_ns": (0, 2**63 - 1),
            "deadline_ns": (1, 2**63 - 1),
            "verified_ns": (1, 2**63 - 1),
            "bytes": (1, 1572864),
            "credit_chunks": (1, 24),
            "granted_bytes": (1, 1572864),
            "accepted_bytes": (1, 1572864),
        }
        for name, (low, high) in limits.items():
            if type(value[name]) is not int or not low <= value[name] <= high:
                raise ProtocolError("frame-proof-integer")
        if (
            not value["requested_ns"]
            <= value["verified_ns"]
            < value["deadline_ns"]
            <= value["requested_ns"] + 2_000_000_000
        ):
            raise ProtocolError("frame-proof-deadline")
        for name in ("header_sha256", "observation_sha256", "image_sha256"):
            item = value[name]
            if (
                type(item) is not str
                or len(item) != _DIGEST_HEX_LENGTH
                or any(c not in "0123456789abcdef" for c in item)
            ):
                raise ProtocolError("frame-proof-digest")
        observation = self.observation
        identity = observation.frame_identity()
        header = FrameHeader(identity, bytes.fromhex(value["image_sha256"]))
        if (
            identity.session_id.hex() != value["epoch"]
            or identity.frame_id != value["frame_id"]
            or identity.request_id != value["request_id"]
            or identity.payload_bytes != value["bytes"]
            or value["granted_bytes"] != value["bytes"]
            or value["accepted_bytes"] != value["bytes"]
            or value["credit_chunks"] != (value["bytes"] + 65535) // 65536
            or value["observation_sha256"] != observation.digest.hex()
            or value["header_sha256"] != hashlib.sha256(header.encode()).hexdigest()
        ):
            raise ProtocolError("frame-proof-correlation")
        self._validate_events(value)

    @staticmethod
    def _validate_events(value: dict[str, Any]) -> None:
        events = value["credit_events"]
        if type(events) is not list or len(events) != 2 * value["credit_chunks"]:
            raise ProtocolError("frame-proof-events")
        last = value["requested_ns"]
        for index, event in enumerate(events):
            if (
                type(event) is not list
                or len(event) != _EVENT_FIELDS
                or type(event[0]) is not int
                or event[0] != index + 1
                or type(event[1]) is not int
                or not last <= event[1] <= value["verified_ns"]
                or event[2] != ("wait" if index % 2 == 0 else "grant")
                or type(event[3]) is not int
                or event[3] != index // 2
            ):
                raise ProtocolError("frame-proof-event-order")
            last = event[1]

    @property
    def value(self) -> dict[str, Any]:
        """Value."""
        value = json.loads(
            self.canonical,
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        bounded_json(value)
        if type(value) is not dict:
            raise ProtocolError("frame-proof-object")
        return value

    @property
    def observation(self) -> SequenceObservation:
        """Observation."""
        return SequenceObservation(
            json.dumps(
                self.value["observation"], sort_keys=True, separators=(",", ":")
            ).encode()
        )

    @classmethod
    def parse(cls, data: bytes) -> SequenceFrameProof:
        """Parse."""
        return cls(data)

    @property
    def digest(self) -> bytes:
        """Digest."""
        return hashlib.sha256(self.canonical).digest()


def parse_frame_proof(data: bytes) -> dict[str, object]:
    """Validate the exact canonical proof before returning bounded metadata."""
    return cast("dict[str, object]", SequenceFrameProof(data).value)


def encode_frame_proof(value: dict[str, object]) -> bytes:
    """Canonicalize and validate locally constructed acceptance evidence."""
    return SequenceFrameProof(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).canonical


def proof_digest(data: bytes) -> bytes:
    """Digest only validated canonical proof bytes."""
    return SequenceFrameProof(data).digest
