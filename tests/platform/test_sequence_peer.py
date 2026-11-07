"""Fail-closed fixed launch and owner-stamped sequence request tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture.test_observe_provider import NativeFixture

from verdiclip.capture import sequence_credit
from verdiclip.capture.frame_envelope import FrameStream
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.capture.observe_provider import ObservedFrame, observe_once
from verdiclip.platform import sequence_peer as peer
from verdiclip.platform import sequence_protocol as wire
from verdiclip.platform.sequence_peer import SequenceLaunch, deserialize_launch


def test_launch_roundtrip() -> None:
    """Test launch roundtrip."""
    launch = SequenceLaunch("VerdiClip-sequence-" + "a" * 32, "b" * 32, 42)
    assert deserialize_launch(launch.serialize()) == launch


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", 2),
        ("credit_schema", 1),
        ("peer_schema", 3),
        ("runtime_schema", 4),
        ("sequence_schema", True),
        ("parent_pid", True),
    ],
)
def test_no_launch_downgrade(field: str, value: int) -> None:
    """Test no launch downgrade."""
    with pytest.raises(ValueError, match=r"Unsupported|Invalid"):
        SequenceLaunch(
            "VerdiClip-sequence-" + "a" * 32, "b" * 32, 42, **{field: value}
        ) if field != "parent_pid" else SequenceLaunch(
            "VerdiClip-sequence-" + "a" * 32, "b" * 32, value
        )


class WireHarness:
    """Portable native seam with real provider, framing, credits and body decoder."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, frames: int) -> None:
        self.native = NativeFixture(monkeypatch)
        self.frames = frames
        self.epoch = "a" * 32
        self.launch = SequenceLaunch("VerdiClip-sequence-" + "b" * 32, self.epoch, 42)
        self.accepted: list[bytes] = []
        self.proofs: list[bytes] = []
        self.pipes: dict[int, WirePipe] = {}
        self.begun = False
        self.response: wire.Message | None = None
        self.current: wire.SequenceObservation | None = None
        self.token: sequence_credit.CreditToken | None = None
        self.credit: sequence_credit.ParentCredit | None = None
        self.stamp = self.deadline = self.count = 0
        self.final: wire.Message | None = None
        monkeypatch.setattr(peer.time, "monotonic_ns", self.now)
        monkeypatch.setattr(sequence_credit, "monotonic_ns", self.now)
        monkeypatch.setattr(peer, "observe_once", self.observe)
        monkeypatch.setattr(peer, "_SequencePipe", self.pipe)

    def now(self) -> int:
        """Advance only when the actual provider's settling loop sleeps."""
        return 1_000_000_000 + int(self.native.now * 1_000_000_000)

    def observe(
        self,
        target: ObserveTarget,
        epoch: str,
        request_id: int,
        *,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> ObservedFrame:
        """Run the unchanged provider with its real fake-native ABI fixture."""
        return observe_once(
            target,
            epoch,
            request_id,
            deadline=deadline,
            cancelled=cancelled,
            clock=lambda: self.now() / 1_000_000_000,
        )

    def pipe(self, launch: SequenceLaunch, frame_id: int | None = None) -> WirePipe:
        """Create only a fresh current-frame endpoint."""
        assert launch == self.launch
        pipe = WirePipe(self, frame_id)
        if frame_id is not None:
            assert frame_id not in self.pipes
            self.pipes[frame_id] = pipe
        return pipe

    def accept(self) -> wire.Message:
        """Decode body only at EOF and construct proof from actual consumed bytes."""
        assert self.response is not None
        assert self.current is not None
        assert self.credit is not None
        frame = self.pipes[len(self.accepted) + 1]
        assert frame.closed, "No accept before frame connection close"
        stream = FrameStream(bytes(frame.data[:128]), self.current.frame_identity())
        stream.feed(bytes(frame.data[128:]))
        header = stream.finish()
        self.credit.accept_body(len(frame.data) - 128)
        self.credit.finish()
        snap = self.credit.snapshot()
        events = []
        for event in snap.events:
            assert event.token is not None
            events.append(
                [event.sequence, event.time_ns, event.kind, event.token.chunk_index]
            )
        proof = wire.encode_frame_proof(
            {
                "proof_schema": 1,
                "epoch": self.epoch,
                "request_id": self.response.request_id,
                "frame_id": len(self.accepted) + 1,
                "requested_ns": self.stamp,
                "deadline_ns": self.deadline,
                "verified_ns": self.now(),
                "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                "observation_sha256": self.current.digest.hex(),
                "image_sha256": header.image_sha256.hex(),
                "bytes": len(frame.data) - 128,
                "credit_chunks": 1,
                "granted_bytes": snap.granted_bytes,
                "accepted_bytes": snap.accepted_bytes,
                "credit_events": events,
                "observation": json.loads(self.current.canonical),
            }
        )
        self.accepted.append(bytes(frame.data[128:]))
        self.proofs.append(proof)
        return wire.Message("frame_accept", self.response.request_id, json.loads(proof))

    def command(self) -> wire.Message:
        """Stamp on the owner side before encoding, never on peer receipt."""
        if not self.begun:
            self.begun = True
            return wire.Message(
                "request",
                1,
                {
                    "operation": "sequence_begin",
                    "sequence_schema": 1,
                    "epoch": self.epoch,
                    "max_frames": 8,
                    "deadline_ns": self.now() + 30_000_000_000,
                    "target": json.loads(self.native.target.serialize()),
                },
            )
        if len(self.accepted) < self.frames:
            self.stamp = self.now()
            self.deadline = self.stamp + 2_000_000_000
            return wire.Message(
                "request",
                len(self.accepted) + 2,
                {
                    "operation": "observe",
                    "frame_id": len(self.accepted) + 1,
                    "requested_ns": self.stamp,
                    "deadline_ns": self.deadline,
                },
            )
        return wire.Message(
            "request", self.frames + 2, {"operation": "sequence_finish"}
        )


class WirePipe:
    """Byte-serialized control and an explicitly closed binary endpoint."""

    def __init__(self, harness: WireHarness, frame_id: int | None) -> None:
        self.harness = harness
        self.frame_id = frame_id
        self.sequence_clock: peer.SequenceDeadline | None = None
        self.data = bytearray()
        self.closed = False

    def checkpoint(self) -> None:
        """Exercise the same authoritative clock as the native adapter."""
        if self.sequence_clock:
            self.sequence_clock.check()

    def write(self, message: wire.Message) -> None:
        """Decode every producer record before the simulated consumer sees it."""
        h = self.harness
        message = wire.Decoder().feed(wire.encode(message))[0]
        h.count += 1
        if message.kind == "ready":
            assert message.payload["runtime_schema"] == 5
        elif message.kind == "frame_wait":
            h.token = sequence_credit.CreditToken.from_payload(
                message.request_id, message.payload
            )
            assert h.credit is not None
            h.credit.receive_wait(h.token)
        elif message.payload["operation"] == "observe":
            h.response = message
            h.current = wire.SequenceObservation(
                json.dumps(
                    message.payload["observation"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
            identity = h.current.frame_identity()
            h.credit = sequence_credit.ParentCredit(
                h.epoch, message.request_id, identity.frame_id, identity.payload_bytes
            )
        elif message.payload["operation"] == "sequence_finish":
            h.final = message

    def read(self) -> wire.Message:
        """Encode every consumer grant and accept before peer decoding."""
        h = self.harness
        h.count += 1
        if h.token is not None:
            token = h.token
            h.token = None
            assert h.credit is not None
            h.credit.reserve_grant(token)
            message = wire.Message("frame_credit", token.request_id, token.payload())
        elif h.response is not None and len(h.accepted) < cast(
            "int", h.response.payload["frame_id"]
        ):
            message = h.accept()
        else:
            message = h.command()
        return wire.Decoder().feed(wire.encode(message))[0]

    def write_bytes(self, data: bytes) -> None:
        """Retain a bounded test-only endpoint for the consumer stream decoder."""
        assert not self.closed
        self.data.extend(data)

    def close(self) -> None:
        """Expose EOF, separate from declared byte length."""
        self.closed = True


@pytest.mark.parametrize("frames", [2, 8])
def test_actual_provider_wire_consumer(
    monkeypatch: pytest.MonkeyPatch, frames: int
) -> None:
    """Actual provider and peer exchange serialized records and full RGB per frame."""
    harness = WireHarness(monkeypatch, frames)
    peer.run(harness.launch.serialize())
    assert harness.accepted == [bytes(range(36))] * frames
    assert list(harness.pipes) == list(range(1, frames + 1))
    chain = bytes(32)
    for proof in harness.proofs:
        chain = hashlib.sha256(chain + wire.proof_digest(proof)).digest()
    assert harness.final is not None
    assert harness.final.payload == {
        "operation": "sequence_finish",
        "sequence_schema": 1,
        "epoch": harness.epoch,
        "frames": frames,
        "proof_sha256": [wire.proof_digest(p).hex() for p in harness.proofs],
        "sequence_sha256": chain.hex(),
    }
    assert harness.count == 5 + 5 * frames


@pytest.mark.parametrize(
    "field", ["frame_id", "requested_ns", "deadline_ns", "operation"]
)
def test_bad_second_request_is_permanent(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """A malformed second frame cannot reset the clock or return a final receipt."""
    harness = WireHarness(monkeypatch, 2)
    original = harness.command

    def changed() -> wire.Message:
        message = original()
        if len(harness.accepted) == 1:
            payload = dict(message.payload)
            payload[field] = {
                "frame_id": 1,
                "requested_ns": harness.now() + 1,
                "deadline_ns": harness.now() + 2_000_000_001,
                "operation": "scroll",
            }[field]
            return wire.Message(message.kind, message.request_id, payload)
        return message

    monkeypatch.setattr(harness, "command", changed)
    with pytest.raises(wire.ProtocolError):
        peer.run(harness.launch.serialize())
    assert len(harness.accepted) == 1
    assert harness.final is None


@pytest.mark.parametrize(
    "mutation", ["epoch", "frame", "digest", "events", "deadline", "duplicate"]
)
def test_peer_rejects_bad_accept(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """An accepted earlier source never qualifies a later malformed acknowledgement."""
    harness = WireHarness(monkeypatch, 2)
    original = harness.accept

    def changed() -> wire.Message:
        message = original()
        payload = dict(message.payload)
        if mutation == "epoch":
            payload["epoch"] = "c" * 32
        elif mutation == "frame":
            payload["frame_id"] = 2
        elif mutation == "digest":
            payload["image_sha256"] = "0" * 64
        elif mutation == "events":
            payload["credit_events"] = []
        elif mutation == "deadline":
            harness.native.now += 2
        else:
            return wire.Message("frame_accept", message.request_id - 1, payload)
        return wire.Message("frame_accept", message.request_id, payload)

    monkeypatch.setattr(harness, "accept", changed)
    with pytest.raises(wire.ProtocolError):
        peer.run(harness.launch.serialize())
    assert harness.final is None


def test_frame_two_cancel_before_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Frame1 remains provisional when frame2's first public token is cancelled."""
    harness = WireHarness(monkeypatch, 2)
    original = WirePipe.read
    held = []

    def cancel_read(pipe: WirePipe) -> wire.Message:
        if harness.token is not None and harness.token.frame_id == 2:
            assert harness.credit is not None
            held.append(harness.token)
            harness.credit.cancel()
            harness.credit.observe_cancel()
            harness.credit.witness().validate_held(harness.token)
            raise wire.ProtocolError("cancelled")
        return original(pipe)

    monkeypatch.setattr(WirePipe, "read", cancel_read)
    with pytest.raises(wire.ProtocolError, match="cancelled"):
        peer.run(harness.launch.serialize())
    assert len(harness.accepted) == 1
    assert held[0].chunk_index == 0
    assert harness.credit is not None
    assert harness.credit.snapshot().granted_bytes == 0
    assert harness.final is None
    assert harness.pipes[2].closed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proof_schema", True),
        ("image_sha256", "G" * 64),
        ("credit_events", [[1, 1, "grant", 0]]),
        ("verified_ns", 0),
        ("deadline_ns", 1),
        ("granted_bytes", 35),
        ("header_sha256", "0" * 64),
        ("unexpected", 1),
    ],
)
def test_proof_tampering(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Canonical proof rejects malformed scalars, fields and accounting before use."""
    harness = WireHarness(monkeypatch, 1)
    peer.run(harness.launch.serialize())
    payload = json.loads(harness.proofs[0])
    payload[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.encode_frame_proof(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence_schema", True),
        ("frame_id", 9),
        ("request_id", 3),
        ("epoch", "d" * 32),
        ("observation", {}),
        ("unexpected", 1),
    ],
)
def test_outer_observation_tampering(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Actual provider bytes cannot be rebound to a stale request or malformed scope."""
    harness = WireHarness(monkeypatch, 1)
    peer.run(harness.launch.serialize())
    assert harness.current is not None
    payload = json.loads(harness.current.canonical)
    payload[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.SequenceObservation.parse(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )


def test_canonical_proof_and_observation_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonicalization, byte bounds and complete credit-record cap are explicit."""
    harness = WireHarness(monkeypatch, 1)
    peer.run(harness.launch.serialize())
    assert harness.current is not None
    assert wire.SequenceObservation.parse(harness.current.canonical) == harness.current
    assert (
        wire.SequenceFrameProof.parse(harness.proofs[0]).canonical == harness.proofs[0]
    )
    assert wire.parse_frame_proof(harness.proofs[0])["frame_id"] == 1
    for raw in (b"", b"x" * 8193, harness.current.canonical + b" "):
        with pytest.raises(wire.ProtocolError):
            wire.SequenceObservation(raw)
    for raw in (b"", b"x" * 12289, b"[]", harness.proofs[0] + b" "):
        with pytest.raises(wire.ProtocolError):
            wire.SequenceFrameProof(raw)
    with pytest.raises(wire.ProtocolError, match="oversized-credit-record"):
        wire.encode(wire.Message("frame_wait", 2, {"padding": "x" * 512}))
    with pytest.raises(wire.ProtocolError, match="invalid-envelope"):
        wire.decode(b'{"version":3,"kind":"response","request_id":1,"payload":[]}')


def test_proof_event_order_and_clock_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Event timestamps and exact deadline equality cannot become timely evidence."""
    harness = WireHarness(monkeypatch, 1)
    peer.run(harness.launch.serialize())
    payload = json.loads(harness.proofs[0])
    payload["credit_events"][0][2] = "grant"
    with pytest.raises(wire.ProtocolError, match="event-order"):
        wire.encode_frame_proof(payload)
    clock = peer.SequenceDeadline(harness.now() + 2)
    assert clock.check() == harness.now()
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: clock.deadline_ns)
    with pytest.raises(wire.ProtocolError):
        clock.check()
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 0)
    with pytest.raises(wire.ProtocolError):
        clock.check()


@pytest.mark.parametrize(
    ("field", "value"), [("pipe_name", "arbitrary"), ("epoch", "0" * 32)]
)
def test_launch_rejects_unbound_names(field: str, value: str) -> None:
    """Neither arbitrary endpoint names nor zero epochs select a peer."""
    fields = {"pipe_name": "VerdiClip-sequence-" + "a" * 32, "epoch": "b" * 32}
    fields[field] = value
    with pytest.raises(ValueError, match="Invalid"):
        SequenceLaunch(fields["pipe_name"], fields["epoch"], 7)


def test_launch_requires_complete_canonical_form() -> None:
    """Canonical launch rejects extras, omissions and alternate whitespace."""
    launch = SequenceLaunch("VerdiClip-sequence-" + "a" * 32, "b" * 32, 7)
    for payload in ("{}", launch.serialize() + " "):
        with pytest.raises(ValueError, match=r"Invalid|Noncanonical"):
            deserialize_launch(payload)


@pytest.mark.parametrize(("field", "value"), [("max_frames", 9), ("deadline_ns", 1)])
def test_bad_begin_latches_failure(
    monkeypatch: pytest.MonkeyPatch, field: str, value: int
) -> None:
    """Rejected begin cannot be repaired by a later otherwise valid request."""
    harness = WireHarness(monkeypatch, 1)
    request = harness.command()
    payload = dict(request.payload)
    payload[field] = value
    protocol = peer.SequencePeerProtocol(harness.launch)
    with pytest.raises(wire.ProtocolError):
        protocol.begin(wire.Message("request", 1, payload))
    with pytest.raises(wire.ProtocolError, match="invalid-sequence-request"):
        protocol.begin(request)


@pytest.mark.parametrize("invalid", [True, 1.0, -1, None, "1"])
def test_sequence_clock_invalid_callback_is_permanent(
    monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    """Malformed clock values cannot recover even when the callback later heals."""
    clock = peer.SequenceDeadline(100)
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: invalid)
    with pytest.raises(wire.ProtocolError) as first:
        clock.check()
    assert clock.failed
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1)
    with pytest.raises(wire.ProtocolError) as repeated:
        clock.check()
    assert repeated.value is first.value


def test_sequence_clock_exception_preserves_first_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising clock is terminal and is not retried on check or next-frame entry."""
    clock = peer.SequenceDeadline(3_000_000_000)
    failure = OSError("monotonic unavailable")
    callback = Mock(side_effect=failure)
    monkeypatch.setattr(peer.time, "monotonic_ns", callback)
    with pytest.raises(OSError, match="monotonic unavailable") as first:
        clock.check()
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    with pytest.raises(OSError, match="monotonic unavailable") as repeated:
        clock.frame(1_000_000_000, 3_000_000_000)
    assert first.value is repeated.value is failure
    assert clock.failed
    callback.assert_called_once()
