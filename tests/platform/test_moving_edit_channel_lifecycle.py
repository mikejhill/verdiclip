"""Actual moving peer, worker and facade lifecycle on deterministic Qt I/O seams.

These tests never open a native socket. A bounded retained peer thread allows
actual peer acceptance to progress while the Qt owner awaits evidence ACKs.
Native snapshot fields come from the extracted producer; the actual peer loop
supplies every header, chunk and proof to the actual channel consumer.
"""

from __future__ import annotations

import gc
import hashlib
import io
import json
import queue
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import cast, override
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtNetwork import QLocalSocket
from tests.capture.moving_wire_fixtures import WIRE_EPOCH, make_begin
from tests.platform.test_helper_channel import helper_status
from tests.platform.test_moving_edit_evidence_native import FakeEvidenceKernel, _Output
from tests.platform.test_moving_edit_peer_transport import ProducedBoundary
from tests.platform.test_sequence_channel import MemoryServer, MemorySocket
from tests.platform.test_sequence_channel_guards import socket_with

from verdiclip.capture import sequence_credit
from verdiclip.capture.edit_scroll_contract import FRAME_LINES
from verdiclip.capture.edit_scroll_provider import Captured, MovingProvider
from verdiclip.capture.moving_edit_frame import FrameState
from verdiclip.capture.moving_edit_retention import SequenceCandidate, SequenceCollector
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.platform import moving_edit_channel as channel
from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform import moving_edit_protocol as wire
from verdiclip.platform import moving_edit_runtime as runtime_module
from verdiclip.platform.moving_edit_runtime import MovingEditRuntimeManifest
from verdiclip.platform.owned_helper import HelperCommand, OwnedHelper

_NATIVE_EVIDENCE_CLIENT_INIT = peer._EvidenceClient.__init__
_NATIVE_PROCESS_IDENTITY = peer._process_identity


class MemoryEvidenceEndpoint:
    """Replace only native I/O; retain exact framed producer/reader/ACK admission."""

    def __init__(self, evidence: EvidenceBridge, *, parent: bool) -> None:
        self.evidence, self.parent = evidence, parent
        self.answer = b""
        self.waiting_packet: dict[str, object] | None = None
        self.read_size = 0
        self.closed = False
        self.reading = self.writing = None

    def start_write(self, frame: bytes) -> None:
        """Serialize the independent reader's two endpoint callbacks."""
        with self.evidence.reader_lock:
            self._start_write(frame)

    def _start_write(self, frame: bytes) -> None:
        assert not self.closed
        assert not self.answer
        evidence = self.evidence
        packet = json.loads(frame[16:])
        kind = (
            ("context" if "requested_ns" in packet else "completion")
            if self.parent
            else packet["event"]
        )
        evidence.events.append((kind, dict(packet)))
        if self.parent:
            evidence.parent_packets.append(dict(packet))
            evidence.reader.prepare_parent(frame, evidence.now[0])
            evidence.parent_raw.write(frame)
            evidence.parent_raw.flush()
            if evidence.delay == "before-ack" and kind == evidence.delay_kind:
                assert evidence.reader.context is not None
                evidence.now[0] = cast("int", evidence.reader.context["deadline_ns"])
            answer = evidence.reader.acknowledge_parent(evidence.now[0])
            if answer is None:
                self.waiting_packet = dict(packet)
                return
            self.answer = answer
        else:
            evidence.reader.prepare_peer(frame, evidence.now[0])
            evidence.raw.write(frame)
            evidence.raw.flush()
            self.answer = evidence.reader.acknowledge_persisted(evidence.now[0])
        evidence.events.append((kind + "-ack", dict(packet)))
        if (
            self.parent
            and evidence.delay == "after-ack"
            and kind == evidence.delay_kind
        ):
            assert evidence.reader.context is not None
            evidence.now[0] = cast("int", evidence.reader.context["deadline_ns"])
        if not self.parent and evidence.delay == "call-ack" and kind == "call_start":
            evidence.now[0] = cast("int", packet["effective_deadline_ns"])

    def poll_write(self) -> bool:
        """A write completes only after the reader has consumed the whole frame."""
        return True

    def start_read(self, size: int) -> None:
        """Deliver every ACK in bounded pieces through the actual client loop."""
        self.read_size = min(size, 17)

    def poll_read(self) -> bytes | None:
        """Never synthesize an ACK or final EOF for unverified evidence."""
        if self.waiting_packet is not None:
            with self.evidence.reader_lock:
                self.evidence.reader.check(self.evidence.now[0])
                if self.evidence.reader.parent_ack_ready:
                    answer = self.evidence.reader.acknowledge_parent(
                        self.evidence.now[0]
                    )
                    assert answer is not None
                    self.answer = answer
                    self.evidence.events.append(("completion-ack", self.waiting_packet))
                    self.waiting_packet = None
                    if self.evidence.delay == "after-ack":
                        assert self.evidence.reader.context is not None
                        self.evidence.now[0] = self.evidence.reader.context[
                            "deadline_ns"
                        ]
            if self.waiting_packet is not None:
                self.evidence.bridge.yield_peer()
                return None
        if self.parent and not self.answer and not self.evidence.reader.complete:
            self.evidence.now[0] += 1_000_000_000
            return None
        value, self.answer = (
            self.answer[: self.read_size],
            self.answer[self.read_size :],
        )
        return value

    def close(self) -> None:
        """Expose actual endpoint closure independently of successful response data."""
        if self.closed:
            return
        self.closed = True
        evidence = self.evidence
        evidence.events.append(("parent-eof" if self.parent else "peer-eof", {}))
        try:
            if self.parent:
                if not evidence.reader.complete:
                    evidence.reader.parent_closed()
            else:
                evidence.reader.peer_eof(evidence.now[0])
                replay = wire.EvidenceReplay()
                evidence.raw.seek(0)
                while chunk := evidence.raw.read(4096):
                    replay.feed(chunk)
                replay.eof()
                evidence.reader.verify_replay(replay)
                assert not evidence.reader.complete  # Peer-only replay is diagnostic.
                correlated = wire.EvidenceCorrelatedReplay(evidence.reader.expected)
                evidence.parent_raw.seek(0)
                while chunk := evidence.parent_raw.read(1024):
                    correlated.feed_parent(chunk)
                correlated.parent_eof()
                evidence.raw.seek(0)
                while chunk := evidence.raw.read(4096):
                    correlated.feed_peer(chunk)
                correlated.peer_eof()
                evidence.reader.verify_correlated_replay(correlated)
        except wire.ProtocolError as error:
            # Match the reader entrypoint's permanent failure boundary. An audit
            # parser failure cannot leave an otherwise live state qualified.
            evidence.audit_failure = str(error)
            with suppress(wire.ProtocolError):
                evidence.reader.fail(type(error).__name__ + ": " + str(error)[:256])
            assert evidence.reader.failed


class EvidenceBridge:
    """Two real producers and the real reader, with native process/I/O seams only."""

    def __init__(self, bridge: Bridge, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bridge, self.now = bridge, bridge.now
        self.events: list[tuple[str, dict[str, object]]] = []
        self.parent_packets: list[dict[str, object]] = []
        self.endpoints: list[MemoryEvidenceEndpoint] = []
        self.delay = ""
        self.delay_kind = "context"
        self.raw = io.BytesIO()
        self.parent_raw = io.BytesIO()
        self.audit_failure: str | None = None
        self.reader_lock = threading.RLock()
        launch = bridge.protocol.launch
        executable = "C:/fixed/python.exe"
        self.reader = wire.EvidenceReaderState(
            wire.EvidenceIdentity(
                launch.evidence_token,
                launch.epoch,
                launch.sequence_started_ns,
                launch.sequence_deadline_ns,
                peer.os.getpid(),
                100,
                executable,
                launch.parent_pid,
                200,
                executable,
            )
        )
        self.reader.bind_peer(peer.os.getpid(), 100, executable, self.now[0])
        self.reader.bind_parent(launch.parent_pid, 200, executable, self.now[0])

        def initialize(
            client: peer._EvidenceClient,
            supplied: wire.MovingEditLaunch,
            *,
            parent: bool,
            reader_executable: str,
            check: Callable[[], object],
        ) -> None:
            assert supplied == launch
            assert reader_executable
            client.launch, client.check = supplied, check
            client.api = Mock()
            client.reader = None
            client.failed = False
            endpoint = MemoryEvidenceEndpoint(self, parent=parent)
            self.endpoints.append(endpoint)
            client.endpoint = cast("peer._EvidenceEndpoint", cast("object", endpoint))

        monkeypatch.setattr(peer._EvidenceClient, "__init__", initialize)
        monkeypatch.setattr(peer, "_process_identity", lambda *_: (100, executable))
        bridge.worker.evidence_client = peer.EvidenceParentClient(
            launch,
            reader_executable=bridge.worker.runtime.executable,
            check=bridge.worker._admit,
        )
        assert not self.parent_packets
        assert not self.events
        self.journal = peer._Journal(launch, bridge.worker._admit)

    def call(self, name: str, action: Callable[[], object], deadline_ns: int) -> object:
        """Dispatch the simulated native operation only after a persisted start ACK."""
        started = self.now[0]
        self.journal.emit(
            {
                "event": "call_start",
                "name": name,
                "effective_deadline_ns": min(deadline_ns, started + wire.MOVE_NS),
            },
            timestamp_ns=started,
        )
        self.events.append(("dispatch", {"name": name}))
        result = action()
        self.journal.emit(
            {"event": "call_end", "seconds": (self.now[0] - started) / 1e9},
            timestamp_ns=self.now[0],
        )
        return result


class AuditedBoundary(ProducedBoundary):
    """Keep the actual raster producer while gating its calls on raw evidence."""

    def __init__(self, now: list[int], evidence: EvidenceBridge) -> None:
        super().__init__(now)
        self.evidence = evidence
        self.deadline_ns = evidence.bridge.worker.sequence_deadline_ns

    @override
    def set_deadline(self, deadline_ns: int) -> None:
        super().set_deadline(deadline_ns)
        self.deadline_ns = deadline_ns

    @override
    def seek(
        self, percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        def seek() -> None:
            super(AuditedBoundary, self).seek(
                percent, restoring=restoring, admit_write=admit_write
            )

        # This simplified boundary models the same explicit seek markers as the
        # native adapter. The actual default-provider tests emit their own.
        marker = {
            "vertical": percent,
            "horizontal": -1.0,
            "restoring": restoring,
        }
        self.evidence.journal.emit(
            {"event": "seek_start", **marker}, timestamp_ns=self.now[0]
        )
        self.evidence.call("SetScrollPercent", seek, self.deadline_ns)
        self.evidence.journal.emit(
            {"event": "seek_end", **marker, "status": 0}, timestamp_ns=self.now[0]
        )

    @override
    def capture(self, expected_line: int, *, restoring: bool) -> Captured:
        def capture() -> Captured:
            return super(AuditedBoundary, self).capture(
                expected_line, restoring=restoring
            )

        return cast(
            "Captured", self.evidence.call("Capture", capture, self.deadline_ns)
        )

    @override
    def close(self) -> None:
        self.evidence.call("CloseHandle", super().close, self.deadline_ns)


class ChannelControl:
    """Connect the actual child loop to actual Qt worker admission callbacks."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.sequence_clock: wire.SequenceDeadline | None = None

    def checkpoint(self) -> None:
        """Preserve both original peer and parent deadlines without restarting."""
        clock = self.bridge.protocol.clock
        assert clock is not None
        clock.check()
        if self.bridge.worker.failure is not None:
            raise wire.ProtocolError(self.bridge.worker.failure)

    def write(self, message: wire.Message) -> None:
        """Every peer record crosses the real wire decoder and worker reader."""
        bridge = self.bridge
        if message.kind == "response" and message.payload.get("operation") == "observe":
            observation = cast("dict[str, object]", message.payload["observation"])
            if bridge.mode == "wrong-point":
                target = cast("dict[str, object]", observation["target"])
                target["point"] = [81, 80]
            elif bridge.mode == "wrong-move":
                observation["move_sha256"] = "f" * 64
            elif bridge.mode == "wrong-response-id":
                message = wire.Message(
                    "response", message.request_id + 1, message.payload
                )
        bridge.on_owner(lambda: bridge.deliver(message))

    def read(self) -> wire.Message:
        """The facade grants each current token, or consumes the actual accept."""
        bridge = self.bridge
        if threading.get_ident() != bridge.owner_thread:
            token = bridge.facade._pending_credit
            if token is not None:
                return cast("wire.Message", bridge.on_owner(self.read))
            while bridge.cursor >= len(bridge.socket.writes):
                bridge.yield_peer()
            return self._read_accept()
        token = bridge.facade._pending_credit
        if token is not None:
            if bridge.mode == "cancel-held-credit":
                bridge.facade.cancel()
                self.checkpoint()
            bridge.facade.grant_frame_credit(
                1, token.request_id, token.frame_id, token.chunk_index
            )
        self.checkpoint()
        return self._read_accept()

    def _read_accept(self) -> wire.Message:
        bridge = self.bridge
        self.checkpoint()
        message = bridge.outgoing()
        if message.kind == "frame_accept":
            if bridge.mode == "late-peer-accept":
                assert bridge.protocol.pending is not None
                bridge.now[0] = bridge.protocol.pending.deadline_ns
            bridge.accepts.append(message)
        return message


class ChannelFrame:
    """A fresh bounded binary endpoint with an explicit observed EOF event."""

    def __init__(self, bridge: Bridge, frame_id: int) -> None:
        self.bridge = bridge
        self.frame_id = frame_id
        self.sequence_clock: wire.SequenceDeadline | None = None
        self.socket = MemorySocket()
        self.closed = False
        self.chunks: list[int] = []
        self.header_written = False
        assert bridge.worker.frame_socket is None
        bridge.worker.frame_socket = cast("QLocalSocket", cast("object", self.socket))

    def write_bytes(self, raw: bytes) -> None:
        """Drain the producer's exact header and each independently granted chunk."""
        if threading.get_ident() != self.bridge.owner_thread:
            self.bridge.on_owner(lambda: self.write_bytes(raw))
            return
        assert not self.closed
        assert not self.socket.raw
        self.socket.raw = raw
        self.bridge.worker._frame_readable()
        self.bridge.control.checkpoint()
        assert not self.socket.raw
        if self.header_written:
            self.chunks.append(len(raw))
        self.header_written = True

    def close(self) -> None:
        """Exact bytes are insufficient until the endpoint really disconnects."""
        if self.closed:
            return
        self.closed = True
        self.bridge.on_owner(self._close_owner, wait=False)

    def _close_owner(self) -> None:
        bridge = self.bridge
        if bridge.worker.failure is not None:
            return
        assert bridge.worker.collector is not None
        assert bridge.worker.collector.count == self.frame_id - 1
        assert len(bridge.accepts) == self.frame_id - 1
        bridge.worker._finish_frame()
        assert bridge.worker.collector.count == self.frame_id - 1
        if bridge.mode == "missing-eof":
            raise OSError("fresh binary endpoint did not close")
        if bridge.mode == "late-eof":
            assert bridge.worker.observe_deadline_ns is not None
            bridge.now[0] = bridge.worker.observe_deadline_ns
        elif bridge.mode == "trailing-body":
            self.socket.raw = b"x"
        bridge.worker._frame_disconnected()
        bridge.control.checkpoint()


class Bridge:
    """One real facade, retained lifetime, worker, peer, provider and seven pipes."""

    def __init__(
        self,
        lifetime: channel._Lifetime,
        now: list[int],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.lifetime, self.worker, self.now = lifetime, lifetime.worker, now
        self.owner_thread = threading.get_ident()
        self.peer_pool = ThreadPoolExecutor(max_workers=1)
        self.owner_queue: queue.Queue[tuple[Callable[[], object], Future[object]]] = (
            queue.Queue(maxsize=1)
        )
        self.async_errors: list[BaseException] = []
        self.peer_waits = 0
        self.mode = ""
        self.socket = MemorySocket()
        self.cursor = 0
        self.requests: list[wire.Message] = []
        self.accepts: list[wire.Message] = []
        self.endpoints: list[ChannelFrame] = []
        self.receipts: list[bytes] = []
        worker = self.worker
        worker.epoch = WIRE_EPOCH
        worker.phase = "ready"
        worker.launch_returned = True
        worker.sequence_started_ns = 0
        worker.sequence_deadline_ns = 30_000_000_000
        worker.sequence_clock = wire.SequenceDeadline(worker.sequence_deadline_ns)
        worker.session_deadline = worker.deadline = 30.0
        worker.collector = SequenceCollector(worker.epoch, worker._admit)
        worker.pipe_name = "VerdiClip-moving-edit-" + "b" * 32
        worker.socket = cast("QLocalSocket", cast("object", self.socket))
        worker.helper = Mock(status=helper_status(retired=True, exit_code=0))
        worker._publish_stamp()
        self.facade = channel.MovingEditChannel()
        facade = self.facade
        facade._generation = 1
        facade._state = "ready"
        facade._vault = worker.vault
        facade._lifetime = lifetime
        facade._cancelled = lifetime.cancelled
        facade._credit_epoch = worker.epoch
        facade._request.connect(worker.submit)
        facade._credit.connect(worker.grant_credit)
        facade._cancel.connect(worker.cancel)
        facade._finish.connect(worker.finish)
        facade.destroyed.connect(lifetime.abandon)
        worker.reply.connect(facade._reply)
        worker.credit_required.connect(facade._credit_required)
        worker.terminal.connect(lambda _generation, raw: self.receipts.append(raw))
        self.protocol = peer.MovingEditPeerProtocol(
            wire.MovingEditLaunch(
                worker.pipe_name,
                worker.epoch,
                7,
                worker.sequence_started_ns,
                worker.sequence_deadline_ns,
                worker.evidence_binding.token,
                worker.evidence_binding.reader_pid,
                worker.evidence_binding.reader_created_100ns,
            )
        )
        worker.evidence_launch = self.protocol.launch
        self.control = ChannelControl(self)
        self.evidence = EvidenceBridge(self, monkeypatch)
        self._create_provider()
        self.budget = wire.SequenceBudget()

    def _create_provider(self) -> None:
        """Provide the deterministic raster-only fixture unless a test overrides it."""
        self.boundary = AuditedBoundary(self.now, self.evidence)
        self.provider = MovingProvider(self.boundary)

    def on_owner(self, action: Callable[[], object], *, wait: bool = True) -> object:
        """Deliver a native transport notification on the actual worker affinity."""
        if threading.get_ident() == self.owner_thread:
            return action()
        future: Future[object] = Future()
        self.owner_queue.put((action, future), timeout=10)
        return future.result(timeout=10) if wait else None

    def yield_peer(self) -> None:
        """Let actual peer acceptance finish while the parent waits for evidence."""
        if self.async_errors:
            raise self.async_errors[0]
        self.peer_waits += 1
        if self.peer_waits > 20_000:
            raise TimeoutError("Test peer scheduler stalled")
        threading.Event().wait(0.0005)

    def run_peer(self, action: Callable[[], object]) -> object:
        """Run the real peer stack while pumping its actual owner notifications."""
        future = self.peer_pool.submit(action)
        while not future.done() or not self.owner_queue.empty():
            try:
                callback, result = self.owner_queue.get(timeout=0.01)
            except queue.Empty:
                continue
            try:
                result.set_result(callback())
            except BaseException as error:  # noqa: BLE001 - retain exact peer/owner exception and finish the test thread
                self.async_errors.append(error)
                result.set_exception(error)
        value = future.result(timeout=10)
        if self.worker.failure is not None:
            raise wire.ProtocolError(self.worker.failure)
        return value

    def outgoing(self) -> wire.Message:
        """Read exactly one new parent record, preserving serialized correlation."""
        assert self.cursor < len(self.socket.writes)
        raw = self.socket.writes[self.cursor]
        self.cursor += 1
        decoder = wire.Decoder()
        records = decoder.feed(raw)
        assert len(records) == 1
        assert not decoder.buffer
        message = records[0]
        if message.kind == "request":
            self.requests.append(message)
            self.evidence.events.append(("command", {"request_id": message.request_id}))
            self.evidence.journal.phase_enter(message)
        return message

    def deliver(self, message: wire.Message) -> None:
        """Deliver actual encoded peer bytes through the worker's bounded reader."""
        assert not self.socket.raw
        if message.payload.get("operation") in ("moving_begun", "move_completed"):
            self.evidence.journal.phase_end()
        self.socket.raw = wire.encode(message)
        self.worker._readable()
        if self.worker.failure is not None:
            raise wire.ProtocolError(self.worker.failure)
        assert not self.socket.raw
        if message.payload.get("operation") == "moving_begun":
            self.protocol.begin_sent(self.control.checkpoint)
        if message.payload.get("operation") == "moving_finished":
            self.evidence.journal.finish_response_sent(timestamp_ns=self.now[0])

    def begin(self) -> None:
        """Start from immutable worker-entry stamps and the exact original point."""
        value = make_begin()
        target = ObserveTarget(99, 12345, 100, (80, 80))
        self.facade.begin(1, target, value["profile_evidence"])
        request = self.outgoing()
        self.protocol.begin(request)
        assert self.protocol.clock is not None
        self.deliver(
            self.protocol.bind_provider(self.provider, self.protocol.clock.check)
        )
        assert self.facade._state == "ready"
        assert self.facade._sequence_deadline_ns == 30_000_000_000

    def move(self, step: int) -> wire.Message:
        """Issue the fixed facade move and acknowledge the actual producer write."""
        self.facade.move_next(2 * step)
        request = self.outgoing()
        reply = self.protocol.move(request, self.control.checkpoint)
        self.deliver(reply)
        self.protocol.move_sent(self.control.checkpoint)
        return reply

    def frame_factory(
        self, launch: wire.MovingEditLaunch, frame_id: int
    ) -> ChannelFrame:
        """Keep seven distinct endpoint lifetimes; never reuse a previous pipe."""
        assert launch == self.protocol.launch
        assert frame_id == len(self.endpoints) + 1
        endpoint = ChannelFrame(self, frame_id)
        self.endpoints.append(endpoint)
        return endpoint

    def observe(self, step: int) -> None:
        """Run the real producer transfer loop into actual worker/facade callbacks."""
        self.facade.observe(2 * step + 1)
        request = self.outgoing()
        self.observe_request(request)

    def observe_request(self, request: wire.Message) -> None:
        """Continue the real transfer stack until its own phase end, without a stub."""

        def observed() -> None:
            peer._observe(
                self.control, self.protocol.launch, self.protocol, request, self.budget
            )
            self.evidence.journal.phase_end()

        self.run_peer(observed)

    def complete(self) -> None:
        """Require seven moves, seven accepts, restoration and independent assembly."""
        self.begin()
        for step in range(1, 8):
            self.move(step)
            self.observe(step)
        self.facade.finish()
        self.deliver(self.protocol.finish(self.outgoing(), self.control.checkpoint))
        self.finish_cleanup()

    def finish_cleanup(self) -> None:
        """Release resources inside context16, then final journal ACK and real EOF."""
        if self.boundary.closed:
            return
        self.boundary.set_deadline(int(self.worker.deadline * 1e9))
        self.boundary.close()
        self.evidence.journal.phase_end()
        self.evidence.journal.finish()

    def seal(self) -> bytes:
        """Deliver actual control EOF and terminal cleanup without a native thread."""
        self.worker._disconnected()
        self.worker._terminal_impl()
        assert len(self.receipts) == 1
        return self.receipts[0]


@pytest.fixture
def bridge(
    qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Bridge]:
    """Use real Qt objects on one thread and no native listener or timing waits."""
    now = [1_000_000_000]
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(channel.time, "monotonic", lambda: now[0] / 1_000_000_000)
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(channel._Worker, "moveToThread", lambda *_: None)
    monkeypatch.setattr(channel, "QLocalServer", MemoryServer)
    runtime = MovingEditRuntimeManifest(
        str(tmp_path / "python.exe"),
        (str(tmp_path),),
        tuple(str(tmp_path / f"module{i}.py") for i in range(23)),
        ("0.13.3", "10.2.0"),
        (3, 13, 1),
        64,
    )
    lifetime = channel._Lifetime(
        1, runtime, threading.Event(), wire.EvidenceBinding("c" * 32, 30, 300)
    )
    lifetime.expiry_timer.stop()
    value = Bridge(lifetime, now, monkeypatch)
    assert value.worker.thread() is qapp.thread()
    monkeypatch.setattr(peer, "_MovingEditPipe", value.frame_factory)
    yield value
    value.worker._close_evidence()
    value.peer_pool.shutdown(wait=True)
    value.evidence.journal.close()
    value.provider.cancel()
    value.worker._discard_pixels()
    lifetime.worker.helper = None
    lifetime._missing_receipt = False
    if lifetime.receipt is None:
        lifetime._retired(1, b'{"passed":false}')
    if not lifetime.stopped:
        lifetime._stopped()
    lifetime._release_if_disposed()
    if shiboken6.isValid(value.facade):
        shiboken6.delete(value.facade)
    if shiboken6.isValid(value.worker):
        value.worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert lifetime not in channel._RETAINED


def test_actual_seven_frame_worker_facade_fullproof_and_provenance(
    bridge: Bridge,
) -> None:
    """All 70 credits and exact EOFs precede the 493x621 independent result."""
    bridge.complete()
    worker = bridge.worker
    assert worker.failure is None
    assert worker.phase == "retiring"
    assert [request.request_id for request in bridge.requests] == list(range(1, 17))
    assert bridge.boundary.writes == list(FRAME_LINES)
    assert bridge.provider.accepted == bridge.protocol.frames == 7
    assert len({id(item.socket) for item in bridge.endpoints}) == 7
    assert all(item.closed for item in bridge.endpoints)
    assert all(item.chunks == [65536] * 9 + [21003] for item in bridge.endpoints)
    assert worker.sequence_candidate is not None
    candidate = worker.sequence_candidate
    assert candidate.chain == bridge.protocol.chain
    chain = bytes(32)
    for index, accepted in enumerate(bridge.accepts, 1):
        proof = wire.MovingFrameProof(wire.canonical_json(accepted.payload["proof"]))
        chain = hashlib.sha256(chain + proof.digest).digest()
        assert accepted.request_id == 2 * index + 1
        assert accepted.payload["proof_sha256"] == proof.digest.hex()
        assert accepted.payload["sequence_sha256"] == chain.hex()
        assert proof.value["credit_chunks"] == 10
        assert proof.value["accepted_bytes"] == 610827
        assert proof.observation.target.point == (80, 80)
        assert len(candidate.frames[index - 1].candidate.rgb) == 610827
    assert candidate.assembly.rgb == b"".join(bridge.boundary.rows)
    assert candidate.assembly.row_provenance == tuple(
        [(2, row) for row in range(270)]
        + [(3, row) for row in range(142, 246)]
        + [(4, row) for row in range(166, 413)]
    )
    for index, (frame_id, row) in enumerate(candidate.assembly.row_provenance):
        assert (
            candidate.assembly.rgb[index * 1479 : (index + 1) * 1479]
            == (
                candidate.frames[frame_id - 1].candidate.rgb[
                    row * 1479 : (row + 1) * 1479
                ]
            )
        )
    for request in bridge.requests[1:-1]:
        duration = wire.MOVE_NS if request.request_id % 2 == 0 else wire.OBSERVE_NS
        assert (
            request.payload["deadline_ns"]
            == cast("int", request.payload["requested_ns"]) + duration
        )
    receipt = bridge.seal()
    assert json.loads(receipt)["passed"] is True
    assert worker.vault.state is FrameState.SEALED
    assert bridge.lifetime in channel._RETAINED


@pytest.mark.parametrize(
    "mode",
    ["wrong-point", "wrong-move", "wrong-response-id", "late-eof", "trailing-body"],
)
def test_bad_frame_latches_worker_and_never_accepts(bridge: Bridge, mode: str) -> None:
    """Correlation, original deadline and exact body EOF all fail closed."""
    bridge.begin()
    bridge.move(1)
    bridge.mode = mode
    with pytest.raises(ValueError, match=r".+"):
        bridge.observe(1)
    assert bridge.worker.failure is not None
    assert not bridge.accepts
    assert bridge.protocol.frames == bridge.provider.accepted == 0
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.retained_bytes == 0
    assert bridge.boundary.writes == [3]
    with pytest.raises(RuntimeError, match="qualified"):
        bridge.facade.take_sequence_result(1)


@pytest.mark.parametrize("step", [1, 2, 7])
def test_cancel_held_credit_discards_every_private_source(
    bridge: Bridge, step: int
) -> None:
    """Cancellation before a grant cannot accept or expose partial older frames."""
    bridge.begin()
    for previous in range(1, step):
        bridge.move(previous)
        bridge.observe(previous)
    bridge.move(step)
    bridge.mode = "cancel-held-credit"
    with pytest.raises(ValueError, match="cancelled"):
        bridge.observe(step)
    assert bridge.worker.failure == "cancelled"
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.retained_bytes == 0
    assert bridge.worker.collector.slots.reserved_bytes == 0
    assert len(bridge.accepts) == step - 1
    assert bridge.worker.credits is not None
    assert bridge.worker.credits.witness().final.granted_bytes == 0
    with pytest.raises(RuntimeError, match="qualified"):
        bridge.facade.take_sequence_result(1)


@pytest.mark.parametrize("mode", ["missing-eof", "late-peer-accept"])
def test_peer_cannot_finish_without_timely_acceptance(
    bridge: Bridge, mode: str
) -> None:
    """Even privately retained bytes grant no peer success after failed delivery."""
    bridge.begin()
    bridge.move(1)
    bridge.mode = mode
    with pytest.raises((ValueError, OSError), match=r".+"):
        bridge.observe(1)
    assert bridge.protocol.frames == bridge.provider.accepted == 0
    bridge.worker._disconnected()
    assert bridge.worker.failure is not None
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.retained_bytes == 0
    with pytest.raises(RuntimeError, match="qualified"):
        bridge.facade.take_sequence_result(1)


@pytest.mark.parametrize("finished_first", [False, True])
def test_full_result_requires_receipt_and_thread_finished(
    bridge: Bridge, *, finished_first: bool
) -> None:
    """Both completion orders authorize only one result, never a terminal shortcut."""
    bridge.complete()
    receipt = bridge.seal()
    lifetime, facade = bridge.lifetime, bridge.facade
    events: list[tuple[int, bytes]] = []
    lifetime.terminal.connect(lambda generation, raw: events.append((generation, raw)))
    if finished_first:
        lifetime._stopped()
    else:
        lifetime._retired(1, receipt)
    assert not events
    with pytest.raises(RuntimeError, match="qualified"):
        facade.take_sequence_result(1)
    if finished_first:
        lifetime._retired(1, receipt)
    else:
        lifetime._stopped()
    assert events == [(1, receipt)]
    facade._retired(*events[0])
    result = facade.take_sequence_result(1)
    assert result.assembly.rgb == b"".join(bridge.boundary.rows)
    assert len(result.frames) == 7
    assert lifetime.vault.state is FrameState.TAKEN
    assert lifetime not in channel._RETAINED
    facade.cancel()
    assert result.assembly.rgb == b"".join(bridge.boundary.rows)
    with pytest.raises(ValueError, match=r".+"):
        facade.take_sequence_result(1)
    assert bridge.worker.collector is not None
    slots = bridge.worker.collector.slots
    assert slots.reserved_bytes == 7 * 610827 + 918459
    del result
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("when", ["before-finished", "before-facade", "after-facade"])
def test_cancel_after_seal_invalidates_unclaimed_result(
    bridge: Bridge, when: str
) -> None:
    """A sealed result is private until taken, including queued terminal races."""
    bridge.complete()
    receipt = bridge.seal()
    lifetime, facade = bridge.lifetime, bridge.facade
    lifetime._retired(1, receipt)
    if when != "before-finished":
        lifetime._stopped()
    if when == "after-facade":
        facade._retired(1, receipt)
    facade.cancel()
    if when == "before-finished":
        lifetime._stopped()
    if when != "after-facade":
        assert lifetime.receipt is not None
        facade._retired(1, lifetime.receipt)
    assert lifetime.vault.state is FrameState.INVALID
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        facade.take_sequence_result(1)


@pytest.mark.parametrize("delivered", [False, True])
def test_deleted_facade_drops_pixels_but_retains_running_owner(
    bridge: Bridge, *, delivered: bool
) -> None:
    """Facade destruction releases pixels without destroying an unfinished owner."""
    bridge.complete()
    receipt = bridge.seal()
    lifetime, facade = bridge.lifetime, bridge.facade
    lifetime._retired(1, receipt)
    if delivered:
        lifetime._stopped()
        facade._retired(1, receipt)
    shiboken6.delete(facade)
    assert lifetime.vault.state is FrameState.INVALID
    assert (lifetime in channel._RETAINED) is (not delivered)
    if not delivered:
        lifetime._stopped()
    assert lifetime not in channel._RETAINED


@pytest.mark.parametrize("stage", ["retirement", "queued-terminal", "take"])
def test_original_deadline_survives_delayed_retirement_and_delivery(
    bridge: Bridge, stage: str
) -> None:
    """Finish, delivery and take never create a new session or publication budget."""
    bridge.complete()
    lifetime, facade = bridge.lifetime, bridge.facade
    assert bridge.worker.deadline <= 30.0
    if stage == "retirement":
        bridge.now[0] = 30_000_000_000
        receipt = bridge.seal()
        assert json.loads(receipt)["passed"] is False
        assert bridge.worker.vault.state is FrameState.INVALID
    else:
        receipt = bridge.seal()
        lifetime._retired(1, receipt)
        lifetime._stopped()
        if stage == "take":
            facade._retired(1, receipt)
        bridge.now[0] = 30_000_000_000
        lifetime._expiry()
        if stage == "queued-terminal":
            facade._retired(1, receipt)
            assert facade._terminal_receipt is not None
            assert json.loads(facade._terminal_receipt)["passed"] is False
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        facade.take_sequence_result(1)
    assert bridge.worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("field", ["sequence_started_ns", "sequence_deadline_ns"])
def test_begin_reply_must_repeat_exact_worker_entry_stamps(
    bridge: Bridge, field: str
) -> None:
    """A valid-looking but changed begin timestamp cannot rebase either clock."""
    value = make_begin()
    bridge.facade.begin(
        1, ObserveTarget(99, 12345, 100, (80, 80)), value["profile_evidence"]
    )
    bridge.protocol.begin(bridge.outgoing())
    assert bridge.protocol.clock is not None
    reply = bridge.protocol.bind_provider(bridge.provider, bridge.protocol.clock.check)
    # Either altered span remains valid in isolation, but is not the issued one.
    reply.payload[field] = 1 if field == "sequence_started_ns" else 29_999_999_999
    with pytest.raises(ValueError, match="invalid-moving-begin-response"):
        bridge.deliver(reply)
    assert bridge.worker.vault.state is FrameState.INVALID
    assert not bridge.facade._sequence_begun
    assert not bridge.boundary.writes


def test_old_wrapped_target_schema_is_rejected_before_wire_write(
    bridge: Bridge,
) -> None:
    """The old observe wrapper must never become the moving begin target field."""
    value = make_begin()
    payload = {
        "operation": "moving_begin",
        "target": json.loads(ObserveTarget(99, 12345, 100, (80, 80)).serialize()),
        "profile_evidence": value["profile_evidence"],
    }
    bridge.worker.submit(1, wire.canonical_json(payload))
    assert bridge.worker.failure is not None
    assert not bridge.socket.writes
    assert bridge.worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("mode", ["correlation", "original-stamps", "next-step"])
def test_move_acknowledgment_must_match_queued_request(
    bridge: Bridge, mode: str
) -> None:
    """Another valid move's stamps or ID cannot satisfy this exact request."""
    bridge.begin()
    bridge.facade.move_next(2)
    request = bridge.outgoing()
    reply = bridge.protocol.move(request, bridge.control.checkpoint)
    if mode == "correlation":
        reply = wire.Message("response", 4, reply.payload)
    elif mode == "original-stamps":
        reply.payload["requested_ns"] = 0
        reply.payload["deadline_ns"] = 3_000_000_000
    else:
        reply.payload["step_id"] = 2
    with pytest.raises(ValueError, match=r".+"):
        bridge.deliver(reply)
    assert bridge.worker.failure is not None
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.facade._move_ack is None
    assert not bridge.endpoints


def test_nonrestore_move_cannot_spend_reserved_final_eight_seconds(
    bridge: Bridge,
) -> None:
    """The queued three-second move is clipped at the original 22-second cutoff."""
    bridge.begin()
    bridge.now[0] = 21_000_000_000
    bridge.move(1)
    request = bridge.requests[-1]
    assert request.payload["requested_ns"] == 21_000_000_000
    assert request.payload["deadline_ns"] == 22_000_000_000
    bridge.now[0] = 22_000_000_000
    before = len(bridge.socket.writes)
    with pytest.raises(ValueError, match="expired"):
        bridge.facade.observe(3)
    assert len(bridge.socket.writes) == before
    assert bridge.worker.failure == "cancelled"
    assert bridge.boundary.writes == [3]


def test_restore_move_uses_only_remaining_original_session(bridge: Bridge) -> None:
    """The final move may use the reserve, but cannot extend the 30-second clock."""
    bridge.begin()
    for step in range(1, 7):
        bridge.move(step)
        bridge.observe(step)
    bridge.now[0] = 29_000_000_000
    bridge.move(7)
    assert bridge.requests[-1].payload["deadline_ns"] == 30_000_000_000
    bridge.observe(7)
    assert bridge.requests[-1].payload["deadline_ns"] == 30_000_000_000
    bridge.facade.finish()
    bridge.deliver(bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint))
    assert bridge.worker.deadline == 30.0
    assert bridge.protocol.clock is not None
    assert bridge.protocol.clock.deadline_ns == 30_000_000_000
    bridge.finish_cleanup()
    receipt = bridge.seal()
    assert json.loads(receipt)["passed"] is True


@pytest.mark.parametrize("mode", ["late-write", "short-write"])
def test_frame_accept_write_failure_cannot_commit_retained_bytes(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Proof validation alone does not commit bytes before the actual timely write."""
    bridge.begin()
    bridge.move(1)
    write = bridge.socket.write

    def changed(raw: bytes) -> int:
        message = wire.Decoder().feed(raw)[0]
        result = write(raw)
        if message.kind == "frame_accept":
            assert bridge.worker.collector is not None
            assert bridge.worker.collector.count == 0
            if mode == "late-write":
                assert bridge.worker.observe_deadline_ns is not None
                bridge.now[0] = bridge.worker.observe_deadline_ns
            else:
                return result - 1
        return result

    monkeypatch.setattr(bridge.socket, "write", changed)
    with pytest.raises(ValueError, match=r".+"):
        bridge.observe(1)
    assert bridge.worker.failure is not None
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.count == 0
    assert bridge.worker.collector.retained_bytes == 0
    assert bridge.worker.collector.slots.reserved_bytes == 0
    assert bridge.provider.accepted == 0
    assert bridge.worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("closed", [False, True])
def test_missing_terminal_never_implies_native_ownership_released(
    bridge: Bridge, *, closed: bool
) -> None:
    """A stopped callback without a receipt yields failure and retains live handles."""
    bridge.complete()
    lifetime = bridge.lifetime
    bridge.worker.helper = Mock(status=helper_status(retired=closed, exit_code=0))
    events: list[bytes] = []
    lifetime.terminal.connect(lambda _generation, raw: events.append(raw))
    lifetime._stopped()
    assert not events
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    bridge.now[0] = int(stamp.deadline * 1_000_000_000)
    lifetime._expiry()
    lifetime._expiry()
    assert len(events) == 1
    assert json.loads(events[0]) == {
        "passed": False,
        "failure": "missing-terminal-receipt",
    }
    assert lifetime.vault.state is FrameState.INVALID
    assert (lifetime in channel._RETAINED) is (not closed)
    assert bridge.worker.sequence_candidate is not None
    # A missing terminal cannot be used to destroy the still-owning worker.
    assert shiboken6.isValid(bridge.worker)


def test_late_notification_from_retired_frame_cannot_touch_next_frame(
    bridge: Bridge,
) -> None:
    """Old queued readable/disconnect signals are inert for the next fresh pipe."""
    bridge.begin()
    bridge.move(1)
    bridge.observe(1)
    old_serial = bridge.worker.frame_serial - 1
    bridge.move(2)
    bridge.facade.observe(5)
    current_serial = bridge.worker.frame_serial
    bridge.worker._frame_event(old_serial, disconnected=True)
    bridge.worker._resume_current_frame(old_serial)
    assert bridge.worker.failure is None
    assert not bridge.worker.frame_disconnected
    assert bridge.worker.frame_serial == current_serial
    bridge.observe_request(bridge.outgoing())
    assert bridge.provider.accepted == 2


def test_duplicate_terminal_invalidates_unclaimed_full_result(bridge: Bridge) -> None:
    """A repeated generation receipt cannot preserve a second transferable alias."""
    bridge.complete()
    receipt = bridge.seal()
    bridge.lifetime._retired(1, receipt)
    bridge.lifetime._stopped()
    bridge.facade._retired(1, receipt)
    bridge.facade._retired(1, receipt)
    assert bridge.worker.vault.state is FrameState.INVALID
    with pytest.raises(ValueError, match=r".+"):
        bridge.facade.take_sequence_result(1)


@pytest.mark.parametrize("caller", [True, 0, 2, 15, 16])
def test_worker_rejects_nonexact_begin_caller_before_writing(
    bridge: Bridge, caller: int
) -> None:
    """Even a queued internal command must preserve caller1 equals wire request1."""
    payload = make_begin()
    del payload["sequence_started_ns"]
    del payload["sequence_deadline_ns"]
    bridge.worker.submit(caller, wire.canonical_json(payload))
    assert bridge.worker.failure == "caller-id"
    assert not bridge.socket.writes
    assert bridge.worker.vault.state is FrameState.INVALID


def test_worker_rejects_skipped_move_caller_after_valid_begin(bridge: Bridge) -> None:
    """A valid begin cannot make caller3 stand in for the fixed move caller2."""
    bridge.begin()
    before = len(bridge.socket.writes)
    bridge.worker.submit(
        3,
        wire.canonical_json(
            {
                "operation": "move_next",
                "step_id": 1,
                "requested_ns": bridge.now[0],
                "deadline_ns": bridge.now[0] + wire.MOVE_NS,
            }
        ),
    )
    assert bridge.worker.failure == "caller-id"
    assert len(bridge.socket.writes) == before
    assert not bridge.boundary.writes


@pytest.mark.parametrize("mode", ["chain", "assembly"])
def test_finish_cannot_publish_mismatching_peer_claims(
    bridge: Bridge, mode: str
) -> None:
    """Seven accepted sources still require matching independent final evidence."""
    bridge.begin()
    for step in range(1, 8):
        bridge.move(step)
        bridge.observe(step)
    bridge.facade.finish()
    reply = bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint)
    if mode == "chain":
        reply.payload["sequence_sha256"] = "f" * 64
    else:
        assembly = cast("dict[str, object]", reply.payload["assembly"])
        assembly["sha256"] = "f" * 64
    with pytest.raises(ValueError, match=r".+"):
        bridge.deliver(reply)
    assert bridge.worker.failure is not None
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.retained_bytes == 0
    assert bridge.worker.collector.slots.reserved_bytes == 0
    assert bridge.worker.sequence_candidate is None
    with pytest.raises(RuntimeError, match="qualified"):
        bridge.facade.take_sequence_result(1)


def ready_record(bridge: Bridge) -> wire.Message:
    """Exact moving startup schema, including all independent version domains."""
    return wire.Message(
        "ready",
        0,
        {
            "pid": 7,
            "executable": "/verified/python",
            "epoch": bridge.worker.epoch,
            "protocol_version": wire.VERSION,
            "peer_schema": wire.PEER_SCHEMA,
            "credit_schema": channel.CREDIT_SCHEMA,
            "moving_schema": wire.MOVING_SCHEMA,
            "runtime_schema": wire.RUNTIME_SCHEMA,
            "max_frames": wire.MAX_SEQUENCE_FRAMES,
            "max_requests": wire.MAX_REQUESTS,
        },
    )


def configure_ready(bridge: Bridge) -> Mock:
    """Require retained-helper evidence, not the ready record's PID alone."""
    bridge.worker.phase = "starting"
    bridge.facade._state = "starting"
    bridge.worker.peer = 7
    helper = Mock(spec=OwnedHelper)
    helper.status = helper_status()
    helper.verify_ready.return_value = helper_status()
    helper.poll.return_value = helper_status()
    bridge.worker.helper = helper
    return helper


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", True),
        ("pid", 9),
        ("executable", 7),
        ("epoch", "b" * 32),
        ("protocol_version", 3),
        ("peer_schema", 4),
        ("credit_schema", 1),
        ("moving_schema", True),
        ("runtime_schema", 5),
        ("max_frames", 8),
        ("max_requests", 32),
        ("extra", 1),
    ],
)
def test_ready_rejects_wrong_profile_before_publication(
    bridge: Bridge, field: str, value: object
) -> None:
    """An old profile or plausible PID cannot authorize the moving channel."""
    configure_ready(bridge)
    record = ready_record(bridge)
    record.payload[field] = value
    events: list[int] = []
    bridge.facade.ready.connect(events.append)
    with pytest.raises(ValueError, match=r".+"):
        bridge.deliver(record)
    assert not events
    assert bridge.worker.verified_identity is None
    assert bridge.worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("producer_verified", False),
        ("pid", 9),
        ("failure", "identity failed"),
        ("cleanup_errors", ("close failed",)),
        ("closed", True),
        ("termination_requested", True),
        ("cleanup_confirmed", True),
        ("exit_code", 0),
        ("remaining_pids", (9,)),
    ],
)
def test_ready_requires_all_live_owned_helper_evidence(
    bridge: Bridge, field: str, value: object
) -> None:
    """Readiness cannot replace every retained kernel membership check."""
    helper = configure_ready(bridge)
    helper.verify_ready.return_value = replace(helper_status(), **{field: value})
    with pytest.raises(ValueError, match="unverified-producer"):
        bridge.deliver(ready_record(bridge))
    assert bridge.worker.verified_identity is None
    assert not bridge.worker.ready_notified


@pytest.mark.parametrize("tail", [b"", b"\x00", b"duplicate"])
def test_ready_batch_is_atomic_before_identity_notification(
    bridge: Bridge, tail: bytes
) -> None:
    """A partial or second record cancels a ready batch before publication."""
    configure_ready(bridge)
    events: list[bytes | int] = []
    bridge.worker.identity.connect(lambda _generation, raw: events.append(raw))
    bridge.worker.ready.connect(events.append)
    record = wire.encode(ready_record(bridge))
    bridge.socket.raw = record + (record if tail == b"duplicate" else tail)
    bridge.worker._readable()
    if tail:
        assert bridge.worker.failure is not None
        assert not events
    else:
        assert bridge.worker.failure is None
        assert len(events) == 2
        assert events[-1] == 1
        assert isinstance(events[0], bytes)
        identity = json.loads(events[0])
        assert identity["sequence_started_ns"] == bridge.worker.sequence_started_ns
        assert identity["sequence_deadline_ns"] == bridge.worker.sequence_deadline_ns
        assert identity["evidence_token"] == bridge.worker.evidence_binding.token
        assert bridge.worker.ready_notified
        bridge.worker._notify_ready()
        assert len(events) == 2


@pytest.mark.parametrize("mode", ["wrong-pid", "not-member", "valid"])
def test_control_connection_authenticates_kernel_pid_before_ready(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Identity comes from the retained helper and kernel pipe peer."""
    helper = configure_ready(bridge)
    worker = bridge.worker
    worker.socket = None
    server = Mock()
    socket = socket_with()
    server.nextPendingConnection.return_value = socket
    worker.server = server
    if mode == "not-member":
        helper.poll.return_value = replace(helper_status(), remaining_pids=())
    monkeypatch.setattr(
        channel, "_peer_pid", lambda _descriptor: 8 if mode == "wrong-pid" else 7
    )
    worker._connected()
    socket.setReadBufferSize.assert_called_once_with(
        wire.MAX_MESSAGE_BYTES + wire.HEADER_BYTES
    )
    if mode == "valid":
        assert worker.failure is None
        socket.readyRead.connect.assert_called_once_with(worker._readable)
        socket.disconnected.connect.assert_called_once_with(worker._disconnected)
    else:
        assert worker.failure == "wrong-peer-pid"
        socket.read.assert_not_called()


@pytest.mark.parametrize("mode", ["wrong-pid", "closed", "valid", "duplicate"])
def test_fresh_frame_connection_reauthenticates_same_live_helper(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A fresh listener cannot use an unrelated or already retired producer."""
    bridge.begin()
    bridge.move(1)
    bridge.facade.observe(3)
    worker = bridge.worker
    server = Mock()
    socket = socket_with()
    server.nextPendingConnection.return_value = socket
    worker.frame_server = server
    helper = cast("Mock", worker.helper)
    helper.poll.return_value = helper_status()
    if mode == "closed":
        helper.poll.return_value = replace(helper_status(), closed=True)
    if mode == "duplicate":
        worker.frame_socket = cast("QLocalSocket", cast("object", MemorySocket()))
    monkeypatch.setattr(
        channel, "_peer_pid", lambda _descriptor: 8 if mode == "wrong-pid" else 7
    )
    worker._accept_frame_connection(worker.frame_serial)
    if mode == "valid":
        assert worker.failure is None
        assert worker.frame_socket is socket
        socket.readyRead.connect.assert_called_once()
        socket.disconnected.connect.assert_called_once()
    else:
        assert worker.failure is not None
        socket.read.assert_not_called()
        if mode == "duplicate":
            server.nextPendingConnection.assert_not_called()


def native_startup(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> FakeEvidenceKernel:
    """Restore the real metadata client and fake only its exact Win32 boundary."""
    worker = bridge.worker
    worker._close_evidence()
    bridge.evidence.journal.close()
    worker.phase = "starting"
    worker.helper = None
    worker.socket = None
    worker.evidence_launch = None
    worker.launch_returned = False
    kernel = FakeEvidenceKernel()
    kernel.identities[30] = worker.evidence_binding.reader_created_100ns
    # Model native path parsing only while constructing the Windows manifest.
    with monkeypatch.context() as native_paths:
        native_paths.setattr(runtime_module, "Path", PureWindowsPath)
        worker.runtime = replace(
            worker.runtime,
            executable=kernel.image,
            roots=("C:/fixed",),
            origins=tuple(f"C:/fixed/module{i}.py" for i in range(23)),
        )
    monkeypatch.setattr(peer._EvidenceClient, "__init__", _NATIVE_EVIDENCE_CLIENT_INIT)
    monkeypatch.setattr(peer, "_process_identity", _NATIVE_PROCESS_IDENTITY)
    monkeypatch.setattr(peer, "_evidence_api", lambda: kernel)
    monkeypatch.setattr(peer, "_base_executable", lambda: kernel.image)
    monkeypatch.setattr(peer.os, "getpid", lambda: 7)
    monkeypatch.setattr(
        peer.ctypes, "get_last_error", lambda: kernel.error, raising=False
    )
    monkeypatch.setattr(
        peer.ctypes, "WinError", lambda code: OSError(code, "fake ABI"), raising=False
    )
    return kernel


@pytest.mark.parametrize("failure", [None, "command", "listen", "launch", "cancel"])
def test_startup_retains_original_clock_and_owns_every_failure(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Run actual startup with only native launch, server and helper adapters."""
    worker = bridge.worker
    native_startup(bridge, monkeypatch)
    command = HelperCommand("C:/fixed/python.exe", ("-I", "-S"), "C:/fixed")
    build = Mock(return_value=command)
    helper = Mock(spec=OwnedHelper)
    helper.status = helper_status()
    server = Mock()
    server.listen.return_value = failure != "listen"
    monkeypatch.setattr(channel, "_build_command", build)
    monkeypatch.setattr(channel, "QLocalServer", Mock(return_value=server))
    monkeypatch.setattr(channel, "OwnedHelper", Mock(return_value=helper))
    if failure == "command":
        build.side_effect = ValueError("manifest mismatch")
    elif failure == "launch":
        helper.start.side_effect = OSError("launch failed")
    elif failure == "cancel":
        helper.start.side_effect = lambda _command: worker.cancelled.set()
    worker.start()
    assert worker.timer is not None
    worker.timer.stop()
    assert worker.launch_returned
    assert worker.sequence_started_ns == bridge.now[0]
    assert worker.sequence_deadline_ns == bridge.now[0] + 30_000_000_000
    assert worker.evidence_launch is not None
    launch = worker.evidence_launch
    assert build.call_args.args == (worker.runtime, launch)
    assert launch.sequence_started_ns == worker.sequence_started_ns
    assert launch.sequence_deadline_ns == worker.sequence_deadline_ns
    assert launch.evidence_token == worker.evidence_binding.token
    assert launch.evidence_reader_pid == worker.evidence_binding.reader_pid
    assert (
        launch.evidence_reader_created_100ns
        == worker.evidence_binding.reader_created_100ns
    )
    assert wire.deserialize_launch(launch.serialize()) == launch
    if failure is None:
        assert worker.failure is None
        assert worker.phase == "starting"
        helper.start.assert_called_once_with(command)
        server.setMaxPendingConnections.assert_called_once_with(1)
        assert server.listen.call_args.args[0].startswith("VerdiClip-moving-edit-")
    else:
        assert worker.failure is not None
        assert worker.phase == "retiring"
        assert worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("fault", ["connect-expired", "pin-expired", "pin-and-close"])
def test_startup_native_parent_connection_failure_prevents_helper_and_keeps_first_error(  # noqa: PLR0915 - independent startup, ownership and first-failure checks
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Actual metadata pinning owns both handles and cannot reset startup3s."""
    worker = bridge.worker
    kernel = native_startup(bridge, monkeypatch)
    entered = bridge.now[0]
    command = HelperCommand("C:/fixed/python.exe", ("-I", "-S"), "C:/fixed")
    server = Mock()
    helper_factory = Mock()
    monkeypatch.setattr(channel, "_build_command", Mock(return_value=command))
    monkeypatch.setattr(channel, "QLocalServer", Mock(return_value=server))
    monkeypatch.setattr(channel, "OwnedHelper", helper_factory)
    create = kernel.CreateFileW
    query = kernel.GetNamedPipeServerProcessId
    close = kernel.CloseHandle
    pin_queries = 0
    closed: list[int] = []

    def connect(name: str, *args: object) -> int:
        result = create(name, *args)
        if fault == "connect-expired":
            bridge.now[0] = entered + 3_000_000_000
        return result

    def pin(handle: int, output: _Output) -> int:
        nonlocal pin_queries
        pin_queries += 1
        result = query(handle, output)
        if fault == "pin-expired" and pin_queries == 2:
            bridge.now[0] = entered + 3_000_000_000
        if fault == "pin-and-close":
            output._obj.value = 31
        return result

    def closing(handle: int) -> int:
        closed.append(handle)
        result = close(handle)
        if fault == "pin-and-close" and handle == 202:
            kernel.error = 5
            return 0
        return result

    monkeypatch.setattr(kernel, "CreateFileW", connect)
    monkeypatch.setattr(kernel, "GetNamedPipeServerProcessId", pin)
    monkeypatch.setattr(kernel, "CloseHandle", closing)
    worker.start()
    assert worker.timer is not None
    worker.timer.stop()
    assert worker.sequence_started_ns == entered
    assert worker.sequence_deadline_ns == entered + 30_000_000_000
    assert worker.launch_returned
    assert worker.phase == "retiring"
    assert worker.failure == (
        "evidence-wrong-server-pid" if fault == "pin-and-close" else "operation-timeout"
    )
    first = worker.failure
    worker._fail("later-cleanup-failure")
    assert worker.failure == first
    helper_factory.assert_not_called()
    assert worker.helper is None
    assert worker.evidence_client is None
    assert closed.count(202) == closed.count(1030) == 1
    assert not kernel.pending
    assert not kernel.sent  # A metadata connection sends no context or auth ACK.
    assert not bridge.socket.writes
    assert not bridge.boundary.writes
    server.close.assert_called()
    assert worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("timing", ["within", "hello-at-expiry", "hello-after-ack"])
def test_actual_outer_startup_order_and_original_authentication_ceiling(  # noqa: C901, PLR0915 - composed startup ownership and independent reader transport
    bridge: Bridge,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timing: str,
) -> None:
    """Real worker/client/reader stages cannot circularly wait or renew startup3s."""
    worker = bridge.worker
    kernel = native_startup(bridge, monkeypatch)
    entered = bridge.now[0]
    order: list[str] = []
    current_pid = [30]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(peer.os, "getpid", lambda: current_pid[0])
    monkeypatch.setattr(kernel, "GetCurrentProcess", lambda: 1000 + current_pid[0])
    reader = peer._EvidenceReader(
        wire.EvidenceReaderLaunch(
            worker.evidence_binding.token, 9, 90, entered + 49_000_000_000
        )
    )
    reader.raw = (tmp_path / "moving-evidence.raw").open("xb", buffering=0)
    reader.parent_raw = (tmp_path / "moving-evidence-parent.raw").open(
        "xb", buffering=0
    )
    reader.peer = peer._ReaderConnection(
        peer._EvidenceEndpoint(kernel, 101), parent=False
    )
    reader.parent = peer._ReaderConnection(
        peer._EvidenceEndpoint(kernel, 201), parent=True
    )
    reader.stage = "ready"
    reader.publish(force=True)
    order.append("reader-start")
    journal: peer._Journal | None = None
    try:
        expected_path = tmp_path / "moving-evidence-expected.json"
        create = kernel.CreateFileW

        def connect(name: str, *args: object) -> int:
            result = create(name, *args)
            if "-parent-" in name:
                assert not expected_path.exists()
                assert reader.state is None
                assert not kernel.sent
                order.append("parent-connect")
            return result

        monkeypatch.setattr(kernel, "CreateFileW", connect)
        command = HelperCommand("C:/fixed/python.exe", ("-I", "-S"), "C:/fixed")
        server = Mock()
        helper = Mock(spec=OwnedHelper)
        status = replace(helper_status(), pid=50, remaining_pids=(50,))
        helper.status = helper.poll.return_value = helper.verify_ready.return_value = (
            status
        )

        def launch(_command: HelperCommand) -> None:
            assert order == ["reader-start", "parent-connect"]
            assert worker.evidence_client is not None
            assert worker.evidence_client.producer.count == 0
            assert not kernel.sent
            assert not bridge.boundary.writes

        helper.start.side_effect = launch
        monkeypatch.setattr(channel, "_build_command", Mock(return_value=command))
        monkeypatch.setattr(channel, "QLocalServer", Mock(return_value=server))
        monkeypatch.setattr(channel, "OwnedHelper", Mock(return_value=helper))
        current_pid[0] = 7
        worker.start()
        assert worker.timer is not None
        worker.timer.stop()
        assert worker.failure is None
        helper.start.assert_called_once_with(command)
        assert worker.evidence_launch is not None
        launch_data = worker.evidence_launch
        assert launch_data.parent_pid == 7
        assert worker.evidence_client is not None
        assert not kernel.sent
        worker.socket = cast("QLocalSocket", cast("object", bridge.socket))
        worker.peer = 50
        ready = ready_record(bridge)
        ready.payload.update(pid=50, executable=kernel.image)
        # Runtime readiness arrives well into startup. It cannot restart auth3s.
        bridge.now[0] = entered + 2_000_000_000
        bridge.socket.raw = wire.encode(ready)
        worker._readable()
        assert worker.failure is None
        assert worker.phase == "ready"
        assert worker.verified_identity is not None
        order.append("helper-runtime-ready")
        identity = json.loads(worker.verified_identity)
        expected = wire.EvidenceIdentity(
            identity["evidence_token"],
            identity["epoch"],
            identity["sequence_started_ns"],
            identity["sequence_deadline_ns"],
            identity["pid"],
            500,
            identity["executable"],
            7,
            70,
            kernel.image,
        )
        expected_path.write_bytes(wire.canonical_json(expected.value))
        reader._expected()
        assert reader.state is not None
        assert reader.expected == expected
        order.append("expected-identity")
        for connection in (reader.parent, reader.peer):
            connection.pin(expected, reader.state, bridge.now[0])
        assert not reader.authentication_ack_written
        assert reader.state.auth_deadline_ns == entered + 3_000_000_000

        def pump() -> None:
            reader.check()
            for connection in (reader.parent, reader.peer):
                assert connection is not None
                frame = connection.poll()
                if frame is not None:
                    if connection.parent:
                        reader._parent_record(frame, bridge.now[0])
                    else:
                        reader._peer_record(frame, bridge.now[0])
            reader._parent_ack()

        kernel.pump = pump
        current_pid[0] = 50
        if timing == "hello-at-expiry":
            bridge.now[0] = entered + 3_000_000_000
        elif timing == "hello-after-ack":
            poll_read = peer._EvidenceEndpoint.poll_read
            received = 0

            def late_read(endpoint: peer._EvidenceEndpoint) -> bytes | None:
                nonlocal received
                value = poll_read(endpoint)
                if endpoint.handle == 102 and value is not None:
                    received += len(value)
                    if received == 80:
                        bridge.now[0] = entered + 3_000_000_000
                return value

            monkeypatch.setattr(peer._EvidenceEndpoint, "poll_read", late_read)
        if timing != "within":
            with pytest.raises(
                wire.ProtocolError,
                match=(
                    "evidence-producer-pending-or-time"
                    if timing == "hello-at-expiry"
                    else "evidence-client-expired"
                ),
            ):
                peer._Journal(launch_data, worker._admit)
            assert not bridge.socket.writes
            assert not bridge.boundary.writes
            assert 102 in kernel.closed
            assert "both-authenticated" not in order
            return
        journal = peer._Journal(launch_data, worker._admit)
        pump()  # Complete the reader's physical ACK-write bookkeeping.
        assert reader.authentication_ack_written
        assert reader.state.status["authenticated"]
        assert journal.deadline_ns == entered + 3_000_000_000
        assert not bridge.boundary.writes
        order.append("both-authenticated")
        bridge.facade.begin(
            1, ObserveTarget(99, 12345, 100, (80, 80)), make_begin()["profile_evidence"]
        )
        assert worker.failure is None
        decoder = wire.Decoder()
        (request,) = decoder.feed(bridge.socket.writes[0])
        journal.phase_enter(request)
        order.append("begin")
        assert order == [
            "reader-start",
            "parent-connect",
            "helper-runtime-ready",
            "expected-identity",
            "both-authenticated",
            "begin",
        ]
        assert reader.state.context is not None
        assert (
            reader.state.context["requested_ns"]
            == request.payload["command_started_ns"]
            == bridge.now[0]
        )
        assert launch_data.sequence_started_ns == entered
        assert reader.state.context["deadline_ns"] == bridge.now[0] + 2_000_000_000
        assert not bridge.boundary.writes
    finally:
        worker._close_evidence()
        if journal is not None:
            journal.close()
        reader.close()
    assert not kernel.pending
    assert {101, 102, 201, 202, 1007, 1030, 1050} <= kernel.closed


def test_begin_never_lazily_replaces_missing_startup_evidence_endpoint(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Public begin cannot repair or bypass a failed startup metadata connection."""
    bridge.worker._close_evidence()
    constructor = Mock(side_effect=AssertionError("unexpected lazy connection"))
    monkeypatch.setattr(channel, "EvidenceParentClient", constructor)
    bridge.facade.begin(
        1, ObserveTarget(99, 12345, 100, (80, 80)), make_begin()["profile_evidence"]
    )
    assert bridge.worker.failure == "missing-startup-evidence-endpoint"
    assert not bridge.socket.writes
    assert not bridge.boundary.writes
    assert not bridge.evidence.parent_packets
    constructor.assert_not_called()


@pytest.mark.parametrize("fault", ["serialize", "seal", "cancel-during-seal"])
def test_terminal_exception_disposes_full_sequence_and_quits_owner(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Receipt exceptions still run disposal and request the owner thread quit."""
    bridge.complete()
    worker = bridge.worker
    worker._disconnected()
    thread = Mock()
    monkeypatch.setattr(channel.QThread, "currentThread", lambda: thread)
    if fault == "serialize":
        monkeypatch.setattr(
            channel, "_serialized", Mock(side_effect=MemoryError("receipt"))
        )
        with pytest.raises(MemoryError, match="receipt"):
            worker._terminal()
        assert not bridge.receipts
    else:
        if fault == "seal":

            def fail_seal(candidate: SequenceCandidate, receipt: bytes) -> None:
                del candidate, receipt  # No test-spy call history owns the raster.
                raise ValueError("seal")

            monkeypatch.setattr(worker.vault, "seal", fail_seal)
        else:
            original = worker.vault.seal

            def seal(candidate: SequenceCandidate, receipt: bytes) -> None:
                original(candidate, receipt)
                worker.cancelled.set()

            monkeypatch.setattr(worker.vault, "seal", seal)
        worker._terminal()
        assert json.loads(bridge.receipts[0])["passed"] is False
    assert worker.vault.state is FrameState.INVALID
    assert worker.sequence_candidate is None
    assert worker.collector is not None
    assert worker.collector.slots.reserved_bytes == 0
    thread.quit.assert_called_once_with()


@pytest.mark.parametrize(
    "fault", ["termination", "exit", "cleanup", "control-eof", "frame-eof"]
)
def test_terminal_needs_every_natural_retirement_gate(
    bridge: Bridge, fault: str
) -> None:
    """Independent assembly cannot substitute for natural cleanup evidence."""
    bridge.complete()
    worker = bridge.worker
    worker._disconnected()
    helper = cast("Mock", worker.helper)
    if fault == "termination":
        helper.status = replace(helper.status, termination_requested=True)
    elif fault == "exit":
        helper.status = replace(helper.status, exit_code=1)
    elif fault == "cleanup":
        helper.status = replace(helper.status, cleanup_errors=("close failed",))
    elif fault == "control-eof":
        worker.eof = False
    else:
        worker.frame_eof = False
    worker._terminal_impl()
    assert json.loads(bridge.receipts[0])["passed"] is False
    assert worker.vault.state is FrameState.INVALID
    assert worker.sequence_candidate is None


@pytest.mark.parametrize("mode", ["eof", "timeout", "exit"])
def test_poll_preserves_eof_and_cleanup_deadline_gates(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Process exit alone cannot prove clean transport EOF."""
    bridge.complete()
    worker = bridge.worker
    helper = cast("Mock", worker.helper)
    helper.poll.return_value = helper_status(
        retired=True, exit_code=9 if mode == "exit" else 0
    )
    monkeypatch.setattr(worker, "_terminal", worker._terminal_impl)
    worker._poll()
    if mode == "exit":
        assert worker.failure == "producer-failed"
    else:
        assert not bridge.receipts
        if mode == "timeout":
            bridge.now[0] = int(worker.deadline * 1_000_000_000)
        else:
            worker._disconnected()
        worker._poll()
    assert len(bridge.receipts) == 1
    assert json.loads(bridge.receipts[0])["passed"] is (mode == "eof")
    if mode == "timeout":
        helper.release_unconfirmed.assert_called_once()
        assert worker.cleanup_failure == "cleanup-timeout"


def test_facade_start_connects_retained_lifetime_without_gui_launch(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The facade requests a worker-thread start, never performs native startup."""
    facade = channel.MovingEditChannel()
    start = Mock()
    monkeypatch.setattr(bridge.lifetime.worker_thread, "start", start)
    monkeypatch.setattr(channel, "_Lifetime", lambda *_: bridge.lifetime)
    assert facade.start(bridge.worker.runtime, bridge.worker.evidence_binding) == 1
    assert facade._state == "starting"
    assert facade._vault is bridge.worker.vault
    start.assert_called_once_with()
    with pytest.raises(RuntimeError, match="cannot start"):
        facade.start(bridge.worker.runtime, bridge.worker.evidence_binding)
    shiboken6.delete(facade)
    assert bridge.lifetime.vault.state is FrameState.INVALID
    assert bridge.lifetime in channel._RETAINED


def test_foreign_thread_facade_call_has_no_mutation(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign-thread call cannot mutate token ownership or cancellation."""
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    with pytest.raises(RuntimeError, match="owning thread"):
        bridge.facade.cancel()
    assert not bridge.lifetime.cancelled.is_set()
    assert bridge.facade._state == "ready"


@pytest.mark.parametrize("bad", [True, 1.0, -1, 2**63, "throw"])
def test_startup_bad_clock_fails_before_any_native_resource(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    """The first clock read is inside the owned fail-closed startup boundary."""
    worker = bridge.worker
    worker.phase = "starting"
    worker.helper = None
    worker.socket = None
    worker.launch_returned = False
    build = Mock()
    monkeypatch.setattr(channel, "_build_command", build)
    if bad == "throw":
        monkeypatch.setattr(
            channel.time, "monotonic_ns", Mock(side_effect=OSError("clock"))
        )
    else:
        monkeypatch.setattr(channel.time, "monotonic_ns", lambda: bad)
    worker.start()
    assert worker.timer is not None
    worker.timer.stop()
    build.assert_not_called()
    assert worker.failure is not None
    assert worker.launch_returned
    assert worker.phase == "retiring"
    assert worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize(
    "raw", [b"\xff\xff\xff\xff", b"\x00\x00\x00\x02{}", b"\x00\x00\x00\x01\xff"]
)
def test_malformed_control_decoder_cannot_recover_after_failure(
    bridge: Bridge, raw: bytes
) -> None:
    """Length, framing and UTF8 failures irreversibly retire the owner."""
    configure_ready(bridge)
    bridge.socket.raw = raw
    bridge.worker._readable()
    failure = bridge.worker.failure
    assert failure is not None
    bridge.socket.raw = wire.encode(ready_record(bridge))
    bridge.worker._readable()
    assert bridge.worker.failure == failure
    assert bridge.worker.verified_identity is None


@pytest.mark.parametrize(
    "fault", ["epoch", "frame", "request", "chunk", "duplicate", "kind", "trailing"]
)
def test_public_credit_wait_requires_exact_current_correlation(
    bridge: Bridge, fault: str
) -> None:
    """A queued token cannot grant capacity to another moving frame or request."""
    bridge.begin()
    bridge.move(1)
    bridge.facade.observe(3)
    token = sequence_credit.CreditToken.for_chunk(
        "b" * 32 if fault == "epoch" else WIRE_EPOCH,
        5 if fault == "request" else 3,
        2 if fault == "frame" else 1,
        610827,
        1 if fault == "chunk" else 0,
    )
    raw = wire.encode(
        wire.Message(
            "frame_credit" if fault == "kind" else "frame_wait",
            token.request_id,
            token.payload(),
        )
    )
    if fault == "duplicate":
        bridge.facade._credit_required(1, raw)
    if fault == "trailing":
        raw += b"x"
    before = len(bridge.socket.writes)
    with pytest.raises(ValueError, match=r".+"):
        bridge.facade._credit_required(1, raw)
    assert bridge.facade._cancelled.is_set()
    assert bridge.worker.failure == "cancelled"
    assert len(bridge.socket.writes) == before
    assert bridge.facade._pending_credit is None


@pytest.mark.parametrize(
    "fault", ["generation", "request", "frame", "chunk", "missing"]
)
def test_public_credit_grant_cancels_before_invalid_token_write(
    bridge: Bridge, fault: str
) -> None:
    """A malformed grant cannot raise while leaving the provider permitted to run."""
    bridge.begin()
    bridge.move(1)
    bridge.facade.observe(3)
    token = sequence_credit.CreditToken.for_chunk(WIRE_EPOCH, 3, 1, 610827, 0)
    if fault != "missing":
        bridge.facade._credit_required(
            1, wire.encode(wire.Message("frame_wait", 3, token.payload()))
        )
    arguments = [1, 3, 1, 0]
    if fault != "missing":
        arguments[["generation", "request", "frame", "chunk"].index(fault)] += 1
    before = len(bridge.socket.writes)
    with pytest.raises(ValueError, match="credit"):
        bridge.facade.grant_frame_credit(*arguments)
    assert bridge.facade._cancelled.is_set()
    assert bridge.worker.failure == "cancelled"
    assert len(bridge.socket.writes) == before


@pytest.mark.parametrize("mode", ["generation", "closing", "cancelled"])
def test_stale_queued_credit_cannot_reopen_a_retired_generation(
    bridge: Bridge, mode: str
) -> None:
    """Late queued waits are filtered before even parsing their bytes."""
    bridge.begin()
    bridge.move(1)
    bridge.facade.observe(3)
    emitted: list[bytes] = []
    bridge.facade.credit_required.connect(lambda _generation, raw: emitted.append(raw))
    if mode == "closing":
        bridge.facade._state = "closing"
    elif mode == "cancelled":
        bridge.facade._cancelled.set()
    bridge.facade._credit_required(0 if mode == "generation" else 1, b"malformed")
    assert not emitted
    assert bridge.facade._pending_credit is None


def test_public_metadata_is_generation_and_phase_filtered(bridge: Bridge) -> None:
    """Late identity, readiness and progress cannot cross the current session."""
    facade = bridge.facade
    events: list[tuple[object, ...]] = []
    facade.identity.connect(lambda *args: events.append(args))
    facade.ready.connect(lambda *args: events.append(args))
    facade.progress.connect(lambda *args: events.append(args))
    facade._state = "starting"
    facade._identity(0, b"old")
    facade._identity(1, b"identity")
    facade._ready(0)
    facade._ready(1)
    facade._progress(1, b"not-busy")
    facade._state = "busy"
    facade._progress(0, b"old")
    facade._progress(1, b"metadata")
    assert events == [(1, b"identity"), (1,), (1, b"metadata")]


def test_every_command_and_native_call_follows_exact_evidence_ack(
    bridge: Bridge,
) -> None:
    """Real ACK bytes precede command dispatch and every simulated native call."""
    bridge.complete()
    events = bridge.evidence.events
    contexts = [value for kind, value in events if kind == "context"]
    assert [packet["context_id"] for packet in contexts] == list(range(1, 17))
    assert len(bridge.evidence.parent_packets) == 32
    for request, context in zip(bridge.requests, contexts, strict=True):
        identifier = request.request_id
        assert (
            context["command_sha256"]
            == hashlib.sha256(wire.encode(request)[wire.HEADER_BYTES :]).hexdigest()
        )
        before = next(
            index
            for index, (kind, packet) in enumerate(events)
            if kind == "context-ack" and packet["context_id"] == identifier
        )
        after = next(
            index
            for index, (kind, packet) in enumerate(events)
            if kind == "command" and packet["request_id"] == identifier
        )
        assert before < after
        if request.payload["operation"] in ("move_next", "observe"):
            for field in ("requested_ns", "deadline_ns", "step_id"):
                assert context[field] == request.payload[field]
    dispatches = [index for index, (kind, _) in enumerate(events) if kind == "dispatch"]
    assert len(dispatches) == 15
    for index in dispatches:
        assert events[index - 1][0] == "call_start-ack"
        assert events[index - 1][1]["name"] == events[index][1]["name"]
        assert events[index + 1][0] == "call_end"
    assert bridge.evidence.reader.complete
    assert not bridge.evidence.reader.failed


@pytest.mark.parametrize("stage", ["queue", "serialization", "before-ack", "after-ack"])
@pytest.mark.parametrize("operation", ["move", "observe"])
def test_original_request_deadline_blocks_each_handoff_before_dispatch(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, stage: str, operation: str
) -> None:
    """Queue, serialization and ACK time consume the same original request bound."""
    bridge.begin()
    if operation == "observe":
        bridge.move(1)
    request_id = 2 if operation == "move" else 3
    request = bridge.facade.move_next if operation == "move" else bridge.facade.observe
    before = len(bridge.socket.writes)
    deadline = bridge.now[0] + (
        wire.MOVE_NS if operation == "move" else wire.OBSERVE_NS
    )
    if stage == "queue":
        pending: list[tuple[int, bytes]] = []
        bridge.facade._request.disconnect(bridge.worker.submit)
        bridge.facade._request.connect(
            lambda caller, raw: pending.append((caller, raw))
        )
        request(request_id)
        assert len(pending) == 1
        assert json.loads(pending[0][1])["deadline_ns"] == deadline
        bridge.now[0] = deadline
        bridge.worker.submit(*pending[0])
    else:
        if stage == "serialization":
            encode = channel.encode

            def delayed(message: wire.Message) -> bytes:
                raw = encode(message)
                if message.kind == "request" and message.request_id == request_id:
                    bridge.now[0] = deadline
                return raw

            monkeypatch.setattr(channel, "encode", delayed)
        else:
            bridge.evidence.delay = stage
        request(request_id)
    assert bridge.now[0] == deadline
    assert bridge.worker.failure is not None
    assert len(bridge.socket.writes) == before
    assert bridge.boundary.writes == ([] if operation == "move" else [3])
    assert bridge.worker.evidence_client is None
    assert bridge.evidence.reader.failed
    assert bridge.worker.vault.state is FrameState.INVALID


def test_frame_context_completes_only_after_actual_accept_write_and_commit(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither metadata nor payload EOF can complete the parent frame context."""
    bridge.begin()
    bridge.move(1)
    write = bridge.socket.write
    observed: list[int] = []

    def checked(raw: bytes) -> int:
        message = wire.Decoder().feed(raw)[0]
        if message.kind == "frame_accept":
            assert bridge.worker.collector is not None
            observed.append(bridge.worker.collector.count)
            assert bridge.worker.evidence_context is not None
            assert bridge.worker.evidence_context["context_id"] == 3
            assert not bridge.evidence.reader.status["parent_completed"]
            assert not any(
                "completed_ns" in packet and packet["context_id"] == 3
                for packet in bridge.evidence.parent_packets
            )
        return write(raw)

    monkeypatch.setattr(bridge.socket, "write", checked)
    bridge.observe(1)
    assert observed == [0]
    assert bridge.worker.collector is not None
    assert bridge.worker.collector.count == 1
    completions = [
        packet
        for packet in bridge.evidence.parent_packets
        if "completed_ns" in packet and packet["context_id"] == 3
    ]
    assert len(completions) == 1
    assert cast("int", completions[0]["completed_ns"]) < cast(
        "int", bridge.requests[-1].payload["deadline_ns"]
    )
    assert bridge.worker.evidence_context is None


def test_parent_endpoint_loss_prohibits_next_native_dispatch(bridge: Bridge) -> None:
    """A valid preceding context cannot survive independently observed parent loss."""
    bridge.begin()
    bridge.facade.move_next(2)
    request = bridge.outgoing()
    client = bridge.worker.evidence_client
    assert client is not None
    client.close()
    assert bridge.evidence.reader.failed
    with pytest.raises(wire.ProtocolError, match=r".+"):
        bridge.protocol.move(request, bridge.control.checkpoint)
    assert not bridge.boundary.writes
    assert not any(kind == "dispatch" for kind, _ in bridge.evidence.events)
    bridge.worker._disconnected()
    first_failure = bridge.worker.failure
    assert first_failure is not None
    bridge.worker.cancel()
    assert bridge.worker.failure == first_failure
    assert bridge.worker.vault.state is FrameState.INVALID


def test_finish_completion_retains_context_through_release_and_evidence_eof(
    bridge: Bridge,
) -> None:
    """The retirement timestamp tightens cleanup without closing context16 early."""
    bridge.begin()
    for step in range(1, 8):
        bridge.move(step)
        bridge.observe(step)
    bridge.facade.finish()
    bridge.deliver(bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint))
    evidence = bridge.evidence
    completion = evidence.parent_packets[-1]
    assert completion["context_id"] == 16
    assert completion["operation"] == "finish"
    assert completion["retirement_started_ns"] == bridge.now[0]
    assert completion["retirement_deadline_ns"] == min(
        bridge.now[0] + 5_000_000_000, bridge.worker.sequence_deadline_ns
    )
    assert completion["retirement_deadline_ns"] == int(bridge.worker.deadline * 1e9)
    assert not bridge.boundary.closed
    assert not evidence.reader.complete
    assert evidence.reader.status["parent_completed"]
    assert not evidence.reader.status["peer_phase_closed"]
    assert bridge.worker.evidence_client is not None
    parent = next(endpoint for endpoint in evidence.endpoints if endpoint.parent)
    assert not parent.closed
    bridge.finish_cleanup()
    assert bridge.boundary.closed
    assert evidence.reader.complete
    assert not parent.closed
    kinds = [kind for kind, _ in evidence.events]
    assert kinds[-1] == "peer-eof"
    completion_index = next(
        index
        for index, (kind, packet) in enumerate(evidence.events)
        if kind == "completion-ack" and packet["context_id"] == 16
    )
    release_index = next(
        index
        for index, (kind, packet) in enumerate(evidence.events)
        if kind == "dispatch" and packet["name"] == "CloseHandle"
    )
    assert completion_index < release_index
    assert json.loads(bridge.seal())["passed"]
    assert parent.closed


@pytest.mark.parametrize("binding", [None, {}, True, "c" * 32])
def test_facade_requires_valid_evidence_binding_before_lifetime_creation(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, binding: object
) -> None:
    """No lifecycle resource starts without the independently pinned reader."""
    facade = channel.MovingEditChannel()
    lifetime = Mock()
    monkeypatch.setattr(channel, "_Lifetime", lifetime)
    with pytest.raises(RuntimeError, match="cannot start"):
        facade.start(bridge.worker.runtime, cast("wire.EvidenceBinding", binding))
    lifetime.assert_not_called()
    assert facade._state == "idle"
    assert facade._generation == 0
    shiboken6.delete(facade)


def test_late_native_start_ack_never_dispatches_the_target_write(
    bridge: Bridge,
) -> None:
    """Persisted evidence is not permission after the same native call deadline."""
    bridge.begin()
    bridge.evidence.delay = "call-ack"
    with pytest.raises(wire.ProtocolError, match=r".+"):
        bridge.move(1)
    assert not bridge.boundary.writes
    assert not any(kind == "dispatch" for kind, _ in bridge.evidence.events)
    assert bridge.evidence.journal.failed
    assert bridge.protocol.failed
    bridge.worker._disconnected()
    assert bridge.worker.failure is not None
    assert bridge.worker.evidence_client is None
    assert bridge.evidence.reader.failed


def test_control_eof_without_final_evidence_cannot_publish_pixels(
    bridge: Bridge,
) -> None:
    """A finished response and retired helper cannot substitute for the raw audit."""
    bridge.begin()
    for step in range(1, 8):
        bridge.move(step)
        bridge.observe(step)
    bridge.facade.finish()
    bridge.deliver(bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint))
    deadline = bridge.worker.deadline
    assert not bridge.evidence.reader.complete
    receipt = json.loads(bridge.seal())
    assert not receipt["passed"]
    assert bridge.worker.failure is not None
    assert bridge.worker.deadline == deadline
    assert bridge.now[0] >= int(deadline * 1e9)
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.sequence_candidate is None
    assert bridge.worker.evidence_client is None


@pytest.mark.parametrize("damage", ["missing-context", "tampered-context"])
def test_parent_raw_audit_is_required_after_complete_peer_trace(
    bridge: Bridge, damage: str
) -> None:
    """Peer EOF and valid pixels cannot qualify a missing or altered parent context."""
    bridge.begin()
    for step in range(1, 8):
        bridge.move(step)
        bridge.observe(step)
    bridge.facade.finish()
    bridge.deliver(bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint))
    evidence = bridge.evidence
    parent_raw = evidence.parent_raw.getvalue()
    first_size = int.from_bytes(parent_raw[:4]) + wire.EVIDENCE_HEADER_BYTES
    first = wire.decode_evidence_record(parent_raw[:first_size], parent=True)
    assert first.event["operation"] == "begin"
    assert first.record_id == 1
    assert len(evidence.parent_packets) == wire.MAX_PARENT_EVIDENCE_RECORDS
    if damage == "missing-context":
        corrupted = parent_raw[first_size:]
    else:
        second_end = (
            first_size
            + int.from_bytes(parent_raw[first_size : first_size + 4])
            + wire.EVIDENCE_HEADER_BYTES
        )
        second = wire.decode_evidence_record(
            parent_raw[first_size:second_end], parent=True
        )
        assert second.event["context_id"] == 1
        assert "completed_ns" in second.event
        # Keep the parent pair self-consistent, so the peer/parent command
        # correlation, rather than a malformed packet, must reject the alteration.
        corrupted = (
            b"".join(
                wire.encode_evidence_record(
                    replace(record, event={**record.event, "command_sha256": "0" * 64}),
                    parent=True,
                )
                for record in (first, second)
            )
            + parent_raw[second_end:]
        )
    evidence.parent_raw = io.BytesIO(corrupted)
    bridge.finish_cleanup()
    assert evidence.reader.final_received
    assert evidence.reader.eof_received
    assert evidence.reader.replay_verified
    assert not evidence.reader.correlated_replay_verified
    assert evidence.audit_failure is not None
    assert not evidence.reader.complete
    assert bridge.worker.sequence_candidate is not None
    receipt = json.loads(bridge.seal())
    assert not receipt["passed"]
    assert bridge.worker.failure is not None
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.worker.sequence_candidate is None
    assert bridge.worker.evidence_client is None


def test_finish_keeps_retirement_transition_stamp_despite_later_serialization(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real clock progress cannot reject the preexisting retirement transition."""
    bridge.begin()
    for step in range(1, 8):
        bridge.move(step)
        bridge.observe(step)

    def tick() -> int:
        bridge.now[0] += 1
        return bridge.now[0]

    monkeypatch.setattr(channel.time, "monotonic_ns", tick)
    bridge.facade.finish()
    bridge.deliver(bridge.protocol.finish(bridge.outgoing(), bridge.control.checkpoint))
    completion = bridge.evidence.parent_packets[-1]
    assert completion["retirement_started_ns"] == completion["completed_ns"]
    assert cast("int", completion["completed_ns"]) < bridge.now[0]
    assert completion["retirement_deadline_ns"] == min(
        cast("int", completion["retirement_started_ns"]) + 5_000_000_000,
        bridge.worker.sequence_deadline_ns,
    )
    bridge.finish_cleanup()
    assert bridge.evidence.reader.complete
    assert json.loads(bridge.seal())["passed"]


@pytest.mark.parametrize("operation", ["move", "observe"])
@pytest.mark.parametrize("stage", ["before-ack", "after-ack"])
def test_late_completion_ack_cannot_authorize_a_later_target_write(
    bridge: Bridge, operation: str, stage: str
) -> None:
    """Response and frame acceptance costs do not renew the original phase bound."""
    bridge.begin()
    if operation == "observe":
        bridge.move(1)
    bridge.evidence.delay = stage
    bridge.evidence.delay_kind = "completion"
    request = bridge.move if operation == "move" else bridge.observe
    with pytest.raises(wire.ProtocolError, match=r".+"):
        request(1)
    assert bridge.boundary.writes == [3]
    assert bridge.worker.failure is not None
    assert bridge.evidence.reader.failed
    assert bridge.worker.evidence_client is None
    assert bridge.worker.vault.state is FrameState.INVALID
    before = len(bridge.socket.writes)
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        bridge.facade.move_next(4)
    assert len(bridge.socket.writes) == before
    assert bridge.boundary.writes == [3]


def test_altered_peer_command_cannot_borrow_parent_context(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid parent's exact command digest cannot authorize changed peer bytes."""
    bridge.begin()
    phase_enter = bridge.evidence.journal.phase_enter

    def changed(request: wire.Message) -> None:
        request.payload["requested_ns"] = (
            cast("int", request.payload["requested_ns"]) - 1
        )
        phase_enter(request)

    monkeypatch.setattr(bridge.evidence.journal, "phase_enter", changed)
    with pytest.raises(wire.ProtocolError, match=r".+"):
        bridge.move(1)
    assert bridge.evidence.reader.failed
    assert not bridge.boundary.writes
    assert not any(kind == "dispatch" for kind, _ in bridge.evidence.events)
    bridge.worker._disconnected()
    assert bridge.worker.failure is not None
    assert bridge.worker.evidence_client is None


def test_observe_completion_ack_waits_for_real_peer_phase_end(bridge: Bridge) -> None:
    """The actual peer accepts frame bytes before the parent receives completion ACK."""
    bridge.begin()
    bridge.move(1)
    bridge.observe(1)
    events = bridge.evidence.events
    positions = {
        kind: index
        for index, (kind, packet) in enumerate(events)
        if packet.get("context_id") == 3
        and kind in {"completion", "phase_end-ack", "completion-ack"}
    }
    assert (
        positions["completion"]
        < positions["phase_end-ack"]
        < positions["completion-ack"]
    )
    assert bridge.protocol.frames == bridge.provider.accepted == 1
    assert bridge.facade._state == "ready"
    assert bridge.evidence.reader.pending_parent is None


def test_delayed_real_peer_phase_end_cannot_renew_observe_deadline(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delivered frame_accept cannot make a late completion ACK timely."""
    bridge.begin()
    bridge.move(1)
    phase_end = bridge.evidence.journal.phase_end

    def delayed() -> None:
        assert bridge.evidence.journal.context is not None
        if bridge.evidence.journal.context["operation"] == "observe":
            assert bridge.worker.observe_deadline_ns is not None
            bridge.now[0] = bridge.worker.observe_deadline_ns
        phase_end()

    monkeypatch.setattr(bridge.evidence.journal, "phase_end", delayed)
    with pytest.raises(wire.ProtocolError, match=r".+"):
        bridge.observe(1)
    assert bridge.worker.failure is not None
    assert bridge.worker.vault.state is FrameState.INVALID
    assert bridge.boundary.writes == [3]
    assert not any(
        kind == "completion-ack" and packet["context_id"] == 3
        for kind, packet in bridge.evidence.events
    )
