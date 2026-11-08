"""Fixed moving peer ordering and byte transport, using actual native producers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any, override
from unittest.mock import Mock

import pytest
from tests.capture.moving_wire_fixtures import make_begin, producer_state
from tests.platform.test_moving_edit_evidence_native import EvidenceLoopback

from verdiclip.capture import sequence_credit
from verdiclip.capture.edit_scroll_provider import Captured, MovingProvider
from verdiclip.capture.frame_envelope import FrameHeader, FrameStream
from verdiclip.capture.moving_edit_frame import (
    FrameSink,
    PixelReservation,
    VerifiedCandidate,
)
from verdiclip.capture.moving_edit_retention import SequenceCollector
from verdiclip.capture.scroll_stitch import ScrollFrame
from verdiclip.capture.sequence_credit import CreditToken, ParentCredit
from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform import moving_edit_protocol as wire
from verdiclip.platform.moving_edit_protocol import MovingFrameProof


class ProducedBoundary:
    """SDK snapshot producer plus deterministic full-size source rasters."""

    unsafe_write_reason = None

    def __init__(self, now: list[int]) -> None:
        self.now = now
        self.pose = 0
        self.writes: list[int] = []
        self.initial_state, self.identity_evidence = producer_state(0)
        self.last_move: dict[str, Any] = {}
        self.last_acquisition: dict[str, int] = {}
        self.rows = [bytes([i % 256, i // 256, 71]) * 493 for i in range(621)]
        self.closed = False
        self.journal: peer._Journal | None = None

    def set_deadline(self, deadline_ns: int) -> None:
        """Reject an already-expired native phase."""
        assert self.now[0] < deadline_ns

    def verify_top_setup(self) -> None:
        """Verify the authored initial pose."""
        assert self.pose == 0

    def seek(
        self, percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        """Model a successful write followed by an actual producer snapshot."""
        seek = {
            "event": "seek_start",
            "vertical": percent,
            "horizontal": -1.0,
            "restoring": restoring,
        }
        if self.journal is not None:
            self.journal.emit(seek, timestamp_ns=self.now[0])
        admit_write()
        self.pose = round(percent * 13 / 100)
        self.writes.append(self.pose)
        state, _ = producer_state(self.pose)
        self.last_move = {
            "write_started_ns": self.now[0],
            "write_returned_ns": self.now[0],
            "readback_ns": self.now[0],
            "completed_ns": self.now[0],
            "native_state": state,
        }
        assert restoring == (len(self.writes) == 7)
        if self.journal is not None:
            self.journal.emit(
                dict(seek, event="seek_end", status=0), timestamp_ns=self.now[0]
            )

    def capture(self, expected_line: int, *, restoring: bool) -> Captured:
        """Produce fixed client/root bytes with exact sample timestamps."""
        assert self.pose == expected_line
        assert restoring == (len(self.writes) == 7)
        started = self.now[0]
        self.now[0] += 200_000_000
        self.last_acquisition = {
            "started_ns": started,
            "finished_ns": self.now[0],
            "samples": 3,
            "quiet_ns": 200_000_000,
        }
        state, _ = producer_state(self.pose)
        return Captured(
            ScrollFrame(
                493, 413, b"".join(self.rows[self.pose * 16 : self.pose * 16 + 413])
            ),
            bytes(533 * 661 * 3),
            wire.canonical_json(state),
            self.pose,
            (13 - self.pose) * 16,
        )

    def close(self) -> None:
        """Retire the fake native owner."""
        self.closed = True


type Setup = tuple[
    list[int], peer.MovingEditPeerProtocol, MovingProvider, ProducedBoundary
]


def clock(protocol: peer.MovingEditPeerProtocol) -> wire.SequenceDeadline:
    """Require the initialized original clock at every test boundary."""
    assert protocol.clock is not None
    return protocol.clock


@pytest.fixture
def initialized(monkeypatch: pytest.MonkeyPatch) -> Setup:
    """Start with the original worker clock, not a new provider allowance."""
    now = [1_000_000_000]
    monkeypatch.setattr(wire.time, "monotonic_ns", lambda: now[0])
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + "b" * 32,
        "a" * 32,
        7,
        make_begin()["sequence_started_ns"],
        make_begin()["sequence_deadline_ns"],
        "c" * 32,
        30,
        3000,
    )
    protocol = peer.MovingEditPeerProtocol(launch)
    protocol.begin(wire.Message("request", 1, make_begin()))
    boundary = ProducedBoundary(now)
    provider = MovingProvider(boundary)
    reply = protocol.bind_provider(provider, clock(protocol).check)
    protocol.begin_sent(clock(protocol).check)
    assert reply.payload["sequence_started_ns"] == make_begin()["sequence_started_ns"]
    assert reply.payload["sequence_deadline_ns"] == make_begin()["sequence_deadline_ns"]
    return now, protocol, provider, boundary


def move(
    protocol: peer.MovingEditPeerProtocol, now: list[int], step: int = 1
) -> wire.Message:
    """Exercise the exact fixed request plan and prequeue move deadline."""
    payload: dict[str, object] = {
        "operation": "move_next",
        "step_id": step,
        "requested_ns": now[0],
        "deadline_ns": min(now[0] + wire.MOVE_NS, clock(protocol).phase_limit(step)),
    }
    reply = protocol.move(
        wire.Message("request", 2 * step, payload), clock(protocol).check
    )
    protocol.move_sent(clock(protocol).check)
    return reply


def frame_request(
    protocol: peer.MovingEditPeerProtocol, now: list[int], step: int = 1
) -> wire.Message:
    """Bind only the immediately preceding completed native write."""
    return wire.Message(
        "request",
        2 * step + 1,
        {
            "operation": "observe",
            "frame_id": step,
            "step_id": step,
            "move_sha256": wire.move_digest(protocol.move_ack),
            "requested_ns": now[0],
            "deadline_ns": min(
                now[0] + wire.OBSERVE_NS, clock(protocol).phase_limit(step)
            ),
        },
    )


def test_startup_does_not_restart_original_clock(initialized: Setup) -> None:
    """The begin response repeats immutable worker-entry stamps exactly."""
    _, protocol, _, _ = initialized
    assert clock(protocol).deadline_ns == make_begin()["sequence_deadline_ns"]
    assert protocol.target is not None
    assert protocol.target.point == (80, 80)


def test_no_next_move_before_exact_frame_acceptance(initialized: Setup) -> None:
    """A completed write alone cannot authorize a second write."""
    now, protocol, _, boundary = initialized
    reply = move(protocol, now)
    assert reply.payload["operation"] == "move_completed"
    assert reply.request_id == 2
    with pytest.raises(ValueError, match=r".+"):
        move(protocol, now, 2)
    assert boundary.writes == [3]
    assert protocol.failed


def test_stale_move_digest_cannot_capture(initialized: Setup) -> None:
    """A matching frame number is insufficient without exact move evidence."""
    now, protocol, provider, _ = initialized
    move(protocol, now)
    request = frame_request(protocol, now)
    request.payload["move_sha256"] = "f" * 64
    with pytest.raises(ValueError, match=r".+"):
        protocol.observe(request)
    assert not provider.frames
    assert protocol.failed


def test_reserve_exhaustion_prevents_native_write(initialized: Setup) -> None:
    """No write starts after the non-restoration cutoff."""
    now, protocol, _, boundary = initialized
    now[0] = clock(protocol).deadline_ns - wire.RESTORE_RESERVE_NS
    with pytest.raises(ValueError, match=r".+"):
        move(protocol, now)
    assert not boundary.writes


@pytest.mark.parametrize("request_id", [True, 3, 4, 16])
def test_wrong_fixed_move_request_id_latches(
    initialized: Setup, request_id: int
) -> None:
    """The peer does not copy the old observe request equals frame plus one rule."""
    now, protocol, _, boundary = initialized
    with pytest.raises(ValueError, match=r".+"):
        protocol.move(
            wire.Message(
                "request",
                request_id,
                {
                    "operation": "move_next",
                    "step_id": 1,
                    "requested_ns": now[0],
                    "deadline_ns": now[0] + wire.MOVE_NS,
                },
            ),
            clock(protocol).check,
        )
    assert protocol.failed
    assert not boundary.writes


class LoopbackControl:
    """Real wire, parent credit, envelope, sink and collector around fake OS I/O."""

    def __init__(
        self, protocol: peer.MovingEditPeerProtocol, now: list[int], mode: str = ""
    ) -> None:

        self.protocol = protocol
        self.now = now
        self.mode = mode
        self.collector = SequenceCollector(protocol.launch.epoch, self.checkpoint)
        self.sequence_clock = protocol.clock
        self.observation: peer.EditObservation | None = None
        self.current: LoopbackFrame | None = None
        self.credit: CreditToken | None = None
        self.proof: MovingFrameProof | None = None
        self.endpoints: list[LoopbackFrame] = []
        self.records: list[wire.Message] = []

    def checkpoint(self) -> None:
        """Retain the original operation deadline throughout transport."""
        if self.protocol.clock is not None:
            self.protocol.clock.check()

    def write(self, message: wire.Message) -> None:
        """Decode every actual response and wait through canonical wire bytes."""
        decoded = wire.Decoder().feed(wire.encode(message))[0]
        self.records.append(decoded)
        if decoded.kind == "response":
            self.observation = peer.EditObservation.from_value(
                decoded.payload["observation"]
            )
        else:
            self.credit = CreditToken.from_payload(decoded.request_id, decoded.payload)
            assert self.current is not None
            assert self.current.credit is not None
            self.current.credit.receive_wait(self.credit)

    def read(self) -> wire.Message:
        """Return one original credit or independently constructed full proof."""
        assert self.current is not None
        assert self.current.credit is not None

        if self.credit is not None:
            token = self.credit
            self.credit = None
            self.current.credit.reserve_grant(token)
            payload = token.payload()
            if self.mode == "wrong-credit":
                payload["chunk_index"] = token.chunk_index + 1
            return wire.Decoder().feed(
                wire.encode(
                    wire.Message(
                        "request"
                        if self.mode == "wrong-credit-kind"
                        else "frame_credit",
                        token.request_id,
                        payload,
                    )
                )
            )[0]
        assert self.current.closed
        pending = self.protocol.pending
        observation = self.observation
        assert pending is not None
        assert observation is not None
        assert self.current.header is not None
        assert self.current.candidate is not None
        identity = observation.value
        snapshot = self.current.credit.snapshot()
        proof = MovingFrameProof(
            wire.canonical_json(
                {
                    "proof_schema": 1,
                    "epoch": self.protocol.launch.epoch,
                    "request_id": pending.request_id,
                    "frame_id": pending.frame_id,
                    "step_id": pending.frame_id,
                    "move_sha256": pending.move_sha256,
                    "baseline_identity_sha256": identity["baseline_identity_sha256"],
                    "requested_ns": pending.requested_ns,
                    "deadline_ns": pending.deadline_ns,
                    "verified_ns": self.now[0],
                    "header_sha256": hashlib.sha256(
                        self.current.header.encode()
                    ).hexdigest(),
                    "observation_sha256": observation.digest.hex(),
                    "image_sha256": self.current.header.image_sha256.hex(),
                    "bytes": 610827,
                    "credit_chunks": 10,
                    "granted_bytes": snapshot.granted_bytes,
                    "accepted_bytes": snapshot.accepted_bytes,
                    "credit_events": [
                        [
                            event.sequence,
                            event.time_ns,
                            event.kind,
                            event.token.chunk_index,
                        ]
                        for event in snapshot.events
                        if event.token is not None
                    ],
                    "observation": identity,
                }
            )
        )
        if self.mode == "changed-original-stamps":
            changed = proof.value
            changed["requested_ns"] -= 1
            changed["deadline_ns"] -= 1
            proof = MovingFrameProof(wire.canonical_json(changed))
        self.collector.accept(self.current.candidate, proof)
        accepted = {
            "epoch": identity["epoch"],
            "frame_id": pending.frame_id,
            "step_id": pending.frame_id,
            "proof_sha256": proof.digest.hex(),
            "sequence_sha256": hashlib.sha256(
                self.collector.chain + proof.digest
            ).hexdigest(),
            "proof": proof.value,
        }
        if self.mode == "wrong-chain":
            accepted["sequence_sha256"] = "0" * 64
        elif self.mode == "missing-proof":
            del accepted["proof"]
        elif self.mode == "wrong-proof":
            accepted["proof_sha256"] = "0" * 64
        elif self.mode == "late-accept":
            self.now[0] = pending.deadline_ns
        self.proof = proof
        return wire.Decoder().feed(
            wire.encode(
                wire.Message(
                    "request" if self.mode == "wrong-accept-kind" else "frame_accept",
                    pending.request_id,
                    accepted,
                )
            )
        )[0]

    def frame_factory(
        self, launch: wire.MovingEditLaunch, frame_id: int
    ) -> LoopbackFrame:
        """Create exactly one distinct endpoint for each frame."""
        assert launch == self.protocol.launch
        assert frame_id == len(self.endpoints) + 1
        self.current = LoopbackFrame(self, frame_id)
        self.endpoints.append(self.current)
        return self.current


class LoopbackFrame:
    """Each connection has independent header, credit accounting and real close."""

    def __init__(self, control: LoopbackControl, frame_id: int) -> None:
        self.control = control
        self.frame_id = frame_id
        self.sequence_clock = None
        self.stream: FrameStream | None = None
        self.sink: FrameSink | None = None
        self.header: FrameHeader | None = None
        self.credit: ParentCredit | None = None
        self.candidate: VerifiedCandidate | None = None
        self.closed = False
        self.chunks: list[int] = []

    def write_bytes(self, data: bytes) -> None:
        """Consume the real encoded header and each admitted body chunk."""
        self.control.checkpoint()
        assert not self.closed
        if self.stream is None:
            assert self.control.observation is not None
            identity = self.control.observation.frame_identity()
            self.stream = FrameStream(data, identity)
            self.control.collector.reserve(identity)
            self.sink = FrameSink(
                identity, self.control.checkpoint, slots=self.control.collector.slots
            )
            self.credit = ParentCredit(
                self.control.protocol.launch.epoch,
                identity.request_id,
                self.frame_id,
                identity.payload_bytes,
            )
            return
        assert self.sink is not None
        assert self.credit is not None
        self.stream.feed(data)
        self.sink.feed(data)
        self.credit.accept_body(len(data))
        self.chunks.append(len(data))

    def close(self) -> None:
        """Verify EOF and freeze only when the actual endpoint has closed."""
        if self.closed:
            return
        self.closed = True
        if self.control.mode == "missing-eof":
            raise OSError("fresh endpoint did not close")
        assert self.stream is not None
        assert self.sink is not None
        assert self.credit is not None
        assert self.control.observation is not None
        assert self.control.protocol.pending is not None
        if self.control.mode == "trailing-body":
            self.stream.feed(b"x")
        if self.control.mode == "late-freeze":
            self.control.now[0] = self.control.protocol.pending.deadline_ns
        self.header = self.stream.finish()
        self.credit.finish()
        self.candidate = self.sink.freeze(
            self.header, self.control.observation.canonical
        )


def test_full_seven_frame_producer_wire_consumer_and_restore(
    initialized: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All70 actual chunks reconstruct exact full-width output and row provenance."""
    now, protocol, provider, boundary = initialized
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now[0])
    parent = LoopbackControl(protocol, now)
    monkeypatch.setattr(peer, "_MovingEditPipe", parent.frame_factory)
    budget = wire.SequenceBudget()
    for step in range(1, 8):
        move(protocol, now, step)
        peer._observe(
            parent,
            protocol.launch,
            protocol,
            frame_request(protocol, now, step),
            budget,
        )
        assert parent.proof is not None
        parent.collector.commit(parent.proof.digest)
        assert provider.accepted == step
        assert protocol.frames == step
        assert parent.endpoints[-1].closed
        assert parent.endpoints[-1].chunks == [65536] * 9 + [21003]
    reply = protocol.finish(
        wire.Message(
            "request",
            16,
            {
                "operation": "moving_finish",
                "command_started_ns": now[0],
                "command_deadline_ns": min(
                    now[0] + 2_000_000_000, clock(protocol).deadline_ns
                ),
            },
        ),
        parent.checkpoint,
    )
    assembled = parent.collector.finish()
    assert boundary.writes == [3, 0, 8, 13, 13, 0, 3]
    assert len({id(endpoint) for endpoint in parent.endpoints}) == 7
    assert assembled.assembly.rgb == b"".join(boundary.rows)
    assert reply.payload["assembly"] == assembled.assembly.summary
    assert reply.payload["sequence_sha256"] == assembled.chain.hex()
    assert protocol.finished
    assert provider.restoration == "verified"
    assert protocol.observations[-1].target.point == (80, 80)


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-credit",
        "wrong-credit-kind",
        "wrong-accept-kind",
        "changed-original-stamps",
        "wrong-chain",
        "missing-proof",
        "wrong-proof",
        "late-accept",
        "missing-eof",
        "trailing-body",
        "late-freeze",
    ],
)
def test_transport_failure_never_accepts_or_permits_next_write(
    initialized: Setup,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """The actual producer and consumer cannot qualify count-only or late streams."""
    now, protocol, provider, boundary = initialized
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now[0])
    parent = LoopbackControl(protocol, now, mode)
    monkeypatch.setattr(peer, "_MovingEditPipe", parent.frame_factory)
    move(protocol, now)
    with pytest.raises((ValueError, OSError), match=r".+"):
        peer._observe(
            parent,
            protocol.launch,
            protocol,
            frame_request(protocol, now),
            wire.SequenceBudget(),
        )
    assert provider.accepted == 0
    assert protocol.frames == 0
    with pytest.raises(ValueError, match=r".+"):
        move(protocol, now, 2)
    assert boundary.writes == [3]
    assert all(
        not name.startswith("transport_") for name in provider.slots.active_slots
    )


class AutoControl(LoopbackControl):
    """Drive the actual fixed peer run loop, without replacing its lifecycle."""

    def __init__(
        self, protocol: peer.MovingEditPeerProtocol, now: list[int], mode: str = ""
    ) -> None:
        super().__init__(protocol, now, mode)
        self.phase = "ready"
        self.closed = False
        self.finished_reply: wire.Message | None = None

    @override
    def write(self, message: wire.Message) -> None:
        """Consume exact wire replies before issuing the next fixed request."""
        decoded = wire.Decoder().feed(wire.encode(message))[0]
        operation = decoded.payload.get("operation")
        if decoded.kind == "ready":
            wire.validate_ready(decoded.payload)
            self.phase = "begin"
        elif operation == "moving_begun":
            wire.validate_begin_reply(decoded.payload)
            self.phase = "move"
        elif operation == "move_completed":
            wire.validate_move_ack(decoded.payload)
            self.phase = "observe"
        elif operation == "moving_finished":
            self.finished_reply = decoded
        else:
            super().write(message)

    @override
    def read(self) -> wire.Message:
        """Never send the next move until the peer accepted the prior full proof."""
        if self.phase == "begin":
            self.phase = "awaiting"
            return wire.Message("request", 1, make_begin())
        if self.phase == "accepted":
            assert self.proof is not None
            assert self.protocol.frames == self.proof.value["frame_id"]
            self.collector.commit(self.proof.digest)
            self.phase = "move"
        if self.phase == "move":
            self.phase = "awaiting"
            step = self.protocol.frames + 1
            if step == 8:
                return wire.Message(
                    "request",
                    16,
                    {
                        "operation": "wrong_finish"
                        if self.mode == "wrong-finish"
                        else "moving_finish",
                        "command_started_ns": self.now[0],
                        "command_deadline_ns": min(
                            self.now[0] + 2_000_000_000,
                            clock(self.protocol).deadline_ns,
                        ),
                    },
                )
            return wire.Message(
                "request",
                2 * step,
                {
                    "operation": "move_next",
                    "step_id": step,
                    "requested_ns": self.now[0],
                    "deadline_ns": min(
                        self.now[0] + wire.MOVE_NS,
                        clock(self.protocol).phase_limit(step),
                    ),
                },
            )
        if self.phase == "observe":
            self.phase = "transferring"
            return frame_request(self.protocol, self.now, self.protocol.frames + 1)
        response = super().read()
        if response.kind == "frame_accept":
            self.phase = "accepted"
        return response

    def close(self) -> None:
        """Keep actual control retirement distinct from the final reply."""
        self.closed = True
        if self.mode == "late-retirement":
            self.now[0] = clock(self.protocol).deadline_ns


def run_fixture(
    monkeypatch: pytest.MonkeyPatch, mode: str = ""
) -> tuple[AutoControl, ProducedBoundary]:
    """Install only fake OS/native boundaries; leave the peer run loop intact."""
    now = [1_000_000_000]
    monkeypatch.setattr(wire.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now[0])
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + "b" * 32,
        "a" * 32,
        7,
        make_begin()["sequence_started_ns"],
        make_begin()["sequence_deadline_ns"],
        "c" * 32,
        30,
        3000,
    )
    protocol = peer.MovingEditPeerProtocol(launch)
    parent = AutoControl(protocol, now, mode)
    boundary = ProducedBoundary(now)
    monkeypatch.setattr(peer, "MovingEditPeerProtocol", lambda _launch: protocol)

    def boundary_factory(*args: object, **_kwargs: object) -> ProducedBoundary:
        journal = args[5]
        assert isinstance(journal, peer._Journal)
        boundary.journal = journal
        return boundary

    monkeypatch.setattr(peer, "NativeBoundary", boundary_factory)

    def pipe_factory(
        actual: wire.MovingEditLaunch, frame_id: int | None = None
    ) -> AutoControl | LoopbackFrame:
        assert actual == launch
        return parent if frame_id is None else parent.frame_factory(actual, frame_id)

    monkeypatch.setattr(peer, "_MovingEditPipe", pipe_factory)
    evidence = EvidenceLoopback(monkeypatch, now, launch)
    original_read, original_write = parent.read, parent.write
    original_close = parent.close

    def close_with_audit() -> None:
        if evidence.state.final_received:
            evidence.audit()
            assert evidence.state.complete
        original_close()

    monkeypatch.setattr(parent, "close", close_with_audit)

    def read_with_context() -> wire.Message:
        message = original_read()
        if message.kind == "request":
            evidence.context_for(message)
        elif message.kind == "frame_accept":
            evidence.complete(deferred=True)
        return message

    def write_with_completion(message: wire.Message) -> None:
        original_write(message)
        if message.kind == "response" and message.payload.get("operation") in (
            "moving_begun",
            "move_completed",
            "moving_finished",
        ):
            evidence.complete()

    monkeypatch.setattr(parent, "read", read_with_context)
    monkeypatch.setattr(parent, "write", write_with_completion)
    return parent, boundary


def test_actual_run_loop_cleanup_and_natural_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly seven accepted writes retire native and pipe owners before return."""
    parent, boundary = run_fixture(monkeypatch)
    peer.run(parent.protocol.launch.serialize())
    assert parent.closed
    assert boundary.closed
    assert parent.protocol.finished
    assert parent.finished_reply is not None
    assembled = parent.collector.finish()
    assert parent.finished_reply.payload["assembly"] == assembled.assembly.summary
    provider = parent.protocol.provider
    assert provider is not None
    assert not provider.frames
    assert provider.slots.reserved_bytes == 0


@pytest.mark.parametrize(
    "mode", ["wrong-credit", "missing-eof", "late-retirement", "wrong-finish"]
)
def test_actual_run_failure_releases_owners_without_restoration(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Public failure cancels; deferred provider.fail is not a restoration route."""
    deferred_restore = Mock(side_effect=AssertionError("unapproved public restoration"))
    monkeypatch.setattr(MovingProvider, "fail", deferred_restore)
    parent, boundary = run_fixture(monkeypatch, mode)
    with pytest.raises((OSError, ValueError), match=r".+"):
        peer.run(parent.protocol.launch.serialize())
    assert parent.closed
    assert boundary.closed
    provider = parent.protocol.provider
    assert provider is not None
    assert not provider.frames
    assert provider.slots.reserved_bytes == 0
    deferred_restore.assert_not_called()
    if mode not in {"late-retirement", "wrong-finish"}:
        assert boundary.writes == [3]
        assert provider.cancelled
        assert provider.restoration == "prohibited"


@pytest.mark.parametrize(
    "mutation", ["native-invariant", "wrong-step", "uncertain-write"]
)
def test_failed_native_move_prevents_every_following_write(
    initialized: Setup, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """Known native state changes and uncertain writes both latch the peer."""
    now, protocol, provider, boundary = initialized
    original = boundary.seek

    def seek(
        percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        original(percent, restoring=restoring, admit_write=admit_write)
        if mutation == "native-invariant":
            boundary.last_move["native_state"]["input_tick"] += 1
        elif mutation == "uncertain-write":
            raise TimeoutError("uncertain completed write")

    monkeypatch.setattr(boundary, "seek", seek)
    request = wire.Message(
        "request",
        2,
        {
            "operation": "move_next",
            "step_id": 2 if mutation == "wrong-step" else 1,
            "requested_ns": now[0],
            "deadline_ns": now[0] + wire.MOVE_NS,
        },
    )
    with pytest.raises((ValueError, TimeoutError), match=r".+"):
        protocol.move(request, clock(protocol).check)
    with pytest.raises(ValueError, match="terminal"):
        protocol.move(request, clock(protocol).check)
    assert protocol.failed
    assert provider.accepted == 0
    assert len(boundary.writes) == (0 if mutation == "wrong-step" else 1)


def test_frame_requires_actual_completed_move_reply(initialized: Setup) -> None:
    """The next frame cannot race serialization of the move acknowledgment."""
    now, protocol, _, _ = initialized
    request = wire.Message(
        "request",
        2,
        {
            "operation": "move_next",
            "step_id": 1,
            "requested_ns": now[0],
            "deadline_ns": now[0] + wire.MOVE_NS,
        },
    )
    protocol.move(request, clock(protocol).check)
    with pytest.raises(ValueError, match="missing-completed"):
        protocol.observe(frame_request(protocol, now))


def test_native_state_change_between_readback_and_capture_latches(
    initialized: Setup,
) -> None:
    """A later valid but changed native observation cannot replace the write receipt."""
    now, protocol, provider, _ = initialized
    move(protocol, now)
    protocol.observe(frame_request(protocol, now))
    captured = provider.capture(1, lambda: None)
    state = wire.parse_json(captured.state, 16384)
    state["input_tick"] += 1
    with pytest.raises(ValueError, match="changed-after-write"):
        protocol.make_observation(replace(captured, state=wire.canonical_json(state)))
    assert protocol.failed
    assert provider.accepted == 0


def test_no_begin_provider_or_pending_frame_cannot_produce_evidence(
    initialized: Setup,
) -> None:
    """Uninitialized and incomplete lifecycles fail before a native write."""
    _, protocol, provider, _ = initialized
    launch = protocol.launch
    unbegun = peer.MovingEditPeerProtocol(launch)
    with pytest.raises(ValueError, match="not-begun"):
        unbegun.move(wire.Message("request", 2, {}), lambda: None)
    unbound = peer.MovingEditPeerProtocol(launch)
    with pytest.raises(ValueError, match="provider-bind"):
        unbound.bind_provider(provider, lambda: None)
    with pytest.raises(ValueError, match="ack-completion"):
        protocol.move_sent(lambda: None)


@pytest.mark.parametrize("changed", ["creation", "fixture-epoch"])
def test_actual_baseline_must_match_original_target_and_attestation(
    initialized: Setup,
    changed: str,
) -> None:
    """Valid native state from another ownership epoch cannot rebaseline begin."""
    now, previous, _, _ = initialized
    protocol = peer.MovingEditPeerProtocol(previous.launch)
    protocol.begin(wire.Message("request", 1, make_begin()))
    boundary = ProducedBoundary(now)
    if changed == "creation":
        boundary.initial_state["native"]["creation"] = 54321
        boundary.initial_state["font"]["attestation"]["creation"] = 54321
    else:
        boundary.initial_state["font"]["attestation"]["epoch"] = "d" * 32
    with pytest.raises(ValueError, match="baseline"):
        protocol.bind_provider(MovingProvider(boundary), clock(protocol).check)
    assert protocol.failed
    assert not boundary.writes


def test_observation_before_frame_admission_is_rejected(initialized: Setup) -> None:
    """Already captured source bytes cannot replace a missing original frame stamp."""
    now, protocol, provider, _ = initialized
    move(protocol, now)
    capture = provider.capture(1, lambda: None)
    with pytest.raises(ValueError, match="unexpected-moving-observation"):
        protocol.make_observation(capture)


def test_acquisition_cannot_start_before_original_request(initialized: Setup) -> None:
    """A structurally valid observation must also fit the actual public stamp."""
    now, protocol, provider, boundary = initialized
    move(protocol, now)
    protocol.observe(frame_request(protocol, now))
    capture = provider.capture(1, lambda: None)
    boundary.last_acquisition["started_ns"] -= 1
    with pytest.raises(ValueError, match="outside-frame"):
        protocol.make_observation(capture)


def test_native_acquisition_failure_closes_control_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial startup cannot leak the authenticated transport handle."""
    parent, _ = run_fixture(monkeypatch)

    def failed(*_args: object, **_kwargs: object) -> None:
        raise OSError("native constructor failed")

    monkeypatch.setattr(peer, "NativeBoundary", failed)
    with pytest.raises(OSError, match="native constructor"):
        peer.run(parent.protocol.launch.serialize())
    assert parent.closed
    assert parent.protocol.provider is None


def test_returning_without_finish_never_qualifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public run boundary requires complete lifecycle even if serving stops."""
    parent, _ = run_fixture(monkeypatch)
    monkeypatch.setattr(peer, "_serve", lambda *_args: None)
    with pytest.raises(ValueError, match="incomplete-retirement"):
        peer.run(parent.protocol.launch.serialize())
    assert parent.closed


@pytest.mark.parametrize("slot", ["transport_1", "transport_2", "transport_3"])
def test_transport_slot_failure_occurs_before_source_capture(
    initialized: Setup,
    monkeypatch: pytest.MonkeyPatch,
    slot: str,
) -> None:
    """Partial staging reservations unwind before any pixel acquisition begins."""
    now, protocol, provider, boundary = initialized
    original = provider.slots.reserve
    attempted: list[str] = []

    def reserve(name: str, *, size: int | None = None) -> PixelReservation:
        attempted.append(name)
        if name == slot:
            raise MemoryError("transport slot unavailable")
        return original(name, size=size)

    parent = LoopbackControl(protocol, now)
    monkeypatch.setattr(provider.slots, "reserve", reserve)
    monkeypatch.setattr(peer, "_MovingEditPipe", parent.frame_factory)
    move(protocol, now)
    with pytest.raises(MemoryError, match="transport slot"):
        peer._observe(
            parent,
            protocol.launch,
            protocol,
            frame_request(protocol, now),
            wire.SequenceBudget(),
        )
    assert not provider.frames
    assert provider.slots.reserved_bytes == 0
    assert attempted[-1] == slot
    assert all(name.startswith("transport_") for name in attempted)
    assert boundary.writes == [3]
    assert provider.cancelled


def test_error_traceback_discard_precedes_transport_lease_release(
    initialized: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No unwound transfer frame may own a chunk after its reservation is released."""
    now, protocol, provider, _ = initialized
    parent = LoopbackControl(protocol, now, "wrong-credit")
    monkeypatch.setattr(peer, "_MovingEditPipe", parent.frame_factory)
    original = peer.discard_traceback
    seen: list[tuple[str, ...]] = []

    def discard(error: BaseException) -> None:
        seen.append(tuple(provider.slots.active_slots))
        original(error)

    monkeypatch.setattr(peer, "discard_traceback", discard)
    move(protocol, now)
    with pytest.raises(ValueError, match=r".+"):
        peer._observe(
            parent,
            protocol.launch,
            protocol,
            frame_request(protocol, now),
            wire.SequenceBudget(),
        )
    assert seen
    assert all(
        name in seen[0] for name in ("transport_1", "transport_2", "transport_3")
    )
    assert all(
        not name.startswith("transport_") for name in provider.slots.active_slots
    )
