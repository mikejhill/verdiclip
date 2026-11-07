"""Actual sequence pairing/facade transitions, without native qualification."""

import json
import threading
from collections.abc import Generator
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QObject
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.test_sequence_retention import frame

from verdiclip.capture import sequence_credit
from verdiclip.capture.retained_frame import DeadlineStamp, VerifiedCandidate
from verdiclip.capture.sequence_credit import CreditToken, PeerCredit
from verdiclip.capture.sequence_retention import SequenceCollector
from verdiclip.platform import sequence_channel as channel
from verdiclip.platform.sequence_peer import SequenceLaunch, SequencePeerProtocol
from verdiclip.platform.sequence_protocol import (
    Decoder,
    Message,
    ProtocolError,
    SequenceFrameProof,
)
from verdiclip.platform.sequence_runtime import SequenceRuntimeManifest


def test_frame_pairing_requires_actual_eof() -> None:
    """No image is complete before exact observed EOF and digest validation."""
    candidate, proof = frame(1)
    pairing = channel.SequencePairing(
        proof.observation.target, "a" * 32, 2, 1, lambda: None
    )
    pairing.metadata(candidate.observation)
    pairing.header(candidate.header.encode())
    assert pairing.payload_ready
    pairing.feed(candidate.rgb)
    assert pairing.eof() == candidate.header
    with pytest.raises((ValueError, ProtocolError)):
        pairing.feed(b"x")


def test_frame_pairing_rejects_swapped_frame() -> None:
    """A valid frame2 wrapper cannot satisfy a frame1 request."""
    candidate, proof = frame(2)
    pairing = channel.SequencePairing(
        proof.observation.target, "a" * 32, 3, 1, lambda: None
    )
    with pytest.raises((ValueError, ProtocolError)):
        pairing.metadata(candidate.observation)


def test_facade_stamps_before_queue_and_uses_authoritative_sequence_deadline(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture the immutable owner deadline before the request signal is queued."""
    facade = channel.SequenceChannel()
    facade._state = "ready"
    facade._sequence_begun = True
    facade._sequence_deadline_ns = 2_500_000_000
    facade._vault = Mock()
    facade._vault.stamp.return_value = DeadlineStamp("ready", 2, 2.5)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    payloads: list[tuple[int, dict[str, object]]] = []
    facade._request.connect(
        lambda caller, raw: payloads.append((caller, json.loads(raw)))
    )
    facade.observe(2)
    qtbot.waitUntil(lambda: len(payloads) == 1)
    assert payloads == [
        (
            2,
            {
                "operation": "observe",
                "frame_id": 1,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 2_500_000_000,
            },
        )
    ]


@pytest.fixture
def state_worker(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[channel._Worker]:
    """Keep deterministic state callbacks on one thread; no native pipe is opened."""
    runtime = SequenceRuntimeManifest(
        str(tmp_path / "python.exe"),
        (str(tmp_path),),
        tuple(str(tmp_path / f"module{i}.py") for i in range(21)),
        ("0.13.3", "10.2.0"),
        (3, 13, 1),
        64,
    )
    monkeypatch.setattr(channel.time, "monotonic", lambda: 1.5)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_500_000_000)
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: 1_500_000_000)
    worker = channel._Worker(1, runtime, threading.Event())
    assert worker.thread() is qapp.thread()
    worker.epoch = "a" * 32
    worker.phase = "ready"
    worker.session_deadline = worker.deadline = 31.0
    worker.sequence_deadline_ns = 31_000_000_000
    worker.sequence_clock = channel.SequenceDeadline(worker.sequence_deadline_ns)
    worker.collector = SequenceCollector(worker.epoch, worker._admit)
    worker.pipe_name = "VerdiClip-sequence-" + "b" * 32
    worker.socket = cast("QLocalSocket", cast("object", MemorySocket()))
    monkeypatch.setattr(channel, "QLocalServer", MemoryServer)
    yield worker
    if shiboken6.isValid(worker):
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)


class MemorySocket:
    """Bounded fake Qt bytes only; no replacement for native transport proof."""

    def __init__(self, raw: bytes = b"") -> None:
        self.raw = raw
        self.writes: list[bytes] = []

    def bytesAvailable(self) -> int:  # noqa: N802 - Qt test double
        """Return the current bounded fake receive buffer length."""
        return len(self.raw)

    def bytesToWrite(self) -> int:  # noqa: N802 - Qt test double
        """Every fake write is committed synchronously."""
        return 0

    def read(self, size: int) -> QByteArray:
        """Drain the same byte window the real worker requested."""
        data, self.raw = self.raw[:size], self.raw[size:]
        return QByteArray(data)

    def write(self, raw: bytes) -> int:
        """Record exactly the serialized control bytes produced by the worker."""
        self.writes.append(raw)
        return len(raw)

    def deleteLater(self) -> None:  # noqa: N802 - Qt test double
        """Own no native resources to delete in this portable seam."""

    def close(self) -> None:
        """Own no native handle in this portable seam."""

    def abort(self) -> None:
        """Own no pending native operation in this portable seam."""


class MemoryServer:
    """Single pending-connection seam with no native listener."""

    class SocketOption:
        """Mirror the one Qt option requested by the actual worker."""

        UserAccessOption = 1

    def __init__(self, parent: QObject) -> None:
        self.parent = parent
        self.newConnection = Mock()
        self.name: str | None = None

    def setSocketOptions(self, value: int) -> None:  # noqa: N802 - Qt test double
        """Require the actual user-only pipe permission request."""
        assert value == self.SocketOption.UserAccessOption

    def setMaxPendingConnections(self, value: int) -> None:  # noqa: N802 - Qt test double
        """Require the same single pending connection bound as production."""
        assert value == 1

    def listen(self, name: str) -> bool:
        """Record the fixed generated endpoint without opening a socket."""
        self.name = name
        return True

    def hasPendingConnections(self) -> bool:  # noqa: N802 - Qt test double
        """No unsolicited connection is injected by the success fixture."""
        return False

    def close(self) -> None:
        """Own no native handle in this portable seam."""

    def deleteLater(self) -> None:  # noqa: N802 - Qt test double
        """Own no native resources to delete in this portable seam."""


def memory_socket(worker: channel._Worker) -> MemorySocket:
    """Recover the explicitly installed fake at the native socket boundary."""
    assert isinstance(worker.socket, MemorySocket)
    return worker.socket


def begin_sequence(worker: channel._Worker) -> SequencePeerProtocol:
    """Exchange real begin wire bytes with the actual peer protocol."""
    _, proof = frame(1)
    peer = SequencePeerProtocol(SequenceLaunch(worker.pipe_name, worker.epoch, 7))
    worker.caller_id = 1
    worker._send(
        {
            "operation": "sequence_begin",
            "target": json.loads(proof.observation.target.serialize()),
        }
    )
    request = Decoder().feed(memory_socket(worker).writes[-1])[0]
    worker._message(peer.begin(request))
    worker.pending_reply = None
    return peer


def complete_frame(
    worker: channel._Worker, peer: SequencePeerProtocol, index: int
) -> tuple[VerifiedCandidate, SequenceFrameProof]:
    """Drive real credit, pairing, EOF, proof and accept for one source frame."""
    candidate, _ = frame(index)
    worker.caller_id = index + 1
    worker._send(
        {
            "operation": "observe",
            "frame_id": index,
            "requested_ns": 1_000_000_000,
            "deadline_ns": 3_000_000_000,
        }
    )
    request = Decoder().feed(memory_socket(worker).writes[-1])[0]
    _, actual, _, _ = peer.observe(request)
    assert actual == index
    worker._message(
        Message(
            "response",
            request.request_id,
            {
                "operation": "observe",
                "sequence_schema": 1,
                "epoch": worker.epoch,
                "frame_id": index,
                "observation": json.loads(candidate.observation),
            },
        )
    )
    transfer = PeerCredit(worker.epoch, request.request_id, index, len(candidate.rgb))
    token = transfer.announce_wait()
    worker._message(Message("frame_wait", request.request_id, token.payload()))
    frame_socket = MemorySocket(candidate.header.encode())
    worker.frame_socket = cast("QLocalSocket", cast("object", frame_socket))
    worker._drain_frame()
    raw = channel.encode(Message("frame_credit", request.request_id, token.payload()))
    worker.grant_credit(1, raw)
    assert worker.failure is None
    transfer.consume_credit(token)
    transfer.wrote_body(len(candidate.rgb))
    frame_socket.raw = candidate.rgb
    worker._drain_frame()
    assert worker.collector is not None
    assert worker.collector.count == index - 1
    worker.frame_disconnected = True
    worker._finish_frame()
    accepted = Decoder().feed(memory_socket(worker).writes[-1])[0]
    proof = SequenceFrameProof(channel._canonical(accepted.payload))
    peer.accept_frame(accepted, candidate.header, proof.observation, transfer)
    return candidate, proof


@pytest.mark.parametrize("count", [2, 8], ids=["two", "eight"])
def test_actual_peer_worker_collector_accepts_only_after_eof(
    state_worker: channel._Worker, count: int
) -> None:
    """Two and eight actual accepted proofs retain exact bytes until final EOF."""
    worker = state_worker
    peer = begin_sequence(worker)
    values = [complete_frame(worker, peer, index) for index in range(1, count + 1)]
    assert worker.phase == "ready"
    assert worker.collector is not None
    assert worker.collector.count == count
    worker.finish()
    request = Decoder().feed(memory_socket(worker).writes[-1])[0]
    worker._message(peer.finish(request))
    assert worker.phase == "retiring"
    assert worker.deadline == 6.5  # Unchanged separate five-second allowance.
    assert worker.sequence_candidate is not None
    assert [item.candidate.rgb for item in worker.sequence_candidate.frames] == [
        item[0].rgb for item in values
    ]
    assert worker.sequence_candidate.chain == peer.chain


@pytest.mark.parametrize("index", [1, 2])
def test_cancel_at_held_credit_never_accepts_current_frame(
    state_worker: channel._Worker, index: int
) -> None:
    """Cancellation at frame1/frame2 held credit discards all private sources."""
    worker = state_worker
    peer = begin_sequence(worker)
    if index == 2:
        complete_frame(worker, peer, 1)
    candidate, _ = frame(index)
    worker._send(
        {
            "operation": "observe",
            "frame_id": index,
            "requested_ns": 1_000_000_000,
            "deadline_ns": 3_000_000_000,
        }
    )
    request = Decoder().feed(memory_socket(worker).writes[-1])[0]
    peer.observe(request)
    worker._message(
        Message(
            "response",
            request.request_id,
            {
                "operation": "observe",
                "sequence_schema": 1,
                "epoch": worker.epoch,
                "frame_id": index,
                "observation": json.loads(candidate.observation),
            },
        )
    )
    token = CreditToken.for_chunk(
        worker.epoch, request.request_id, index, len(candidate.rgb), 0
    )
    worker._message(Message("frame_wait", request.request_id, token.payload()))
    frame_socket = MemorySocket(candidate.header.encode())
    worker.frame_socket = cast("QLocalSocket", cast("object", frame_socket))
    worker._drain_frame()
    before = len(memory_socket(worker).writes)
    worker.cancel()
    assert worker.failure == "cancelled"
    assert len(memory_socket(worker).writes) == before
    assert worker.collector is not None
    assert worker.collector.retained_bytes == 0
    assert worker.vault.state is channel.FrameState.INVALID
    assert worker.credits is not None
    assert worker.credits.witness().final.granted_bytes == 0
