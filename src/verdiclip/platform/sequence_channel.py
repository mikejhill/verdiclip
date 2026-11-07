"""Unused asynchronous owner for a fixed bounded read-only sequence peer.

No application call sites exist. Only the fixed attested peer can launch;
Each read-only owned-WPF observation uses a fresh authenticated binary pipe.
Deterministic seam tests do
not qualify its installed native runtime. Native lifetime and Qt I/O belong to
one worker thread.
Facade retention does not govern application/interpreter shutdown: callers must
keep the GUI event loop alive until terminal delivery after thread.finished.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Never, cast

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from verdiclip.capture.frame_envelope import HEADER_BYTES as FRAME_HEADER_BYTES
from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity, FrameStream
from verdiclip.capture.observe_protocol import (
    MAX_READ_BYTES,
    ObserveTarget,
)
from verdiclip.capture.retained_frame import (
    MAX_FRAME_BYTES,
    FrameSink,
    FrameState,
    VerifiedCandidate,
)
from verdiclip.capture.sequence_credit import (
    CREDIT_SCHEMA,
    CreditGate,
    CreditSnapshot,
    CreditToken,
    ParentCredit,
)
from verdiclip.capture.sequence_retention import (
    MAX_SOURCE_BYTES,
    SequenceCandidate,
    SequenceCollector,
    SequenceVault,
    VerifiedSequence,
)
from verdiclip.platform.owned_helper import HelperCommand, OwnedHelper
from verdiclip.platform.sequence_bootstrap import sequence_arguments
from verdiclip.platform.sequence_peer import (
    OBSERVE_NS,
    PEER_SCHEMA,
    RUNTIME_SCHEMA,
    SEQUENCE_SCHEMA,
    SequenceDeadline,
    SequenceLaunch,
)
from verdiclip.platform.sequence_protocol import (
    HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    MAX_SEQUENCE_CONTROL_RECORDS,
    MAX_SEQUENCE_FRAMES,
    VERSION,
    Decoder,
    Message,
    ProtocolError,
    SequenceBudget,
    SequenceFrameProof,
    SequenceObservation,
    decode,
    encode,
)
from verdiclip.platform.sequence_runtime import SequenceRuntimeManifest, inspect_runtime

_STARTUP_SECONDS = 3.0
_COMMAND_SECONDS = 2.0
_SESSION_SECONDS = 30.0
_CLEANUP_SECONDS = 5.0
_POLL_MS = 10


def _reject(code: str) -> Never:
    raise ProtocolError(code)


def _build_command(
    runtime: SequenceRuntimeManifest, launch: SequenceLaunch
) -> HelperCommand:
    """Launch only the reviewed fixed peer, with no arbitrary dispatch."""
    return _fixed_peer_command(runtime, launch)


def _fixed_peer_command(
    runtime: SequenceRuntimeManifest, launch: SequenceLaunch
) -> HelperCommand:
    """Construct the fixed peer from validated installed parent identity."""
    if inspect_runtime() != runtime:
        raise ValueError("Selected runtime differs from installed parent")
    return HelperCommand(
        runtime.executable,
        sequence_arguments(runtime.serialize(), launch.serialize()),
        str(Path(runtime.executable).parent),
    )


def _peer_pid(descriptor: int) -> int:
    """Require the actual kernel client PID, never a self-reported substitute."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetNamedPipeClientProcessId.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.ULONG),
    ]
    kernel.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    pid = wintypes.ULONG()
    if not kernel.GetNamedPipeClientProcessId(descriptor, ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return pid.value


def _payload(data: bytes) -> dict[str, object]:
    """Validate immutable control bytes through the proven strict JSON parser."""
    if type(data) is not bytes or not 0 < len(data) <= MAX_MESSAGE_BYTES:
        raise ValueError("Control payload must be bounded immutable bytes")
    envelope = (
        f'{{"version":{VERSION},"kind":"request","request_id":1,"payload":'.encode()
        + data
        + b"}"
    )
    message = decode(envelope)
    encode(message)  # Include envelope overhead in the existing wire limit.
    return message.payload


def _credit_snapshot(value: CreditSnapshot) -> dict[str, object]:
    """Compact bounded event evidence, without repeated full token dictionaries."""
    result = cast("dict[str, object]", asdict(value))
    result["events"] = [
        [
            event.sequence,
            event.time_ns,
            event.kind,
            event.token.chunk_index if event.token is not None else None,
        ]
        for event in value.events
    ]
    return result


def _serialized(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


class _Flight:
    """Strict request correlation; frame acceptance is not another request."""

    def __init__(self) -> None:
        self.completed = 0
        self.pending: int | None = None

    def begin(self, payload: dict[str, object]) -> Message:
        if self.pending is not None or self.completed >= MAX_REQUESTS:
            _reject("single-flight")
        self.pending = self.completed + 1
        return Message("request", self.pending, payload)

    def accept(self, message: Message) -> None:
        if (
            message.kind != "response"
            or type(message.request_id) is not int
            or self.pending is None
            or message.request_id != self.pending
        ):
            _reject("unexpected-response")
        self.completed = self.pending
        self.pending = None


class SequencePairing:
    """Bind one wrapper/header and prove actual EOF without buffering RGB."""

    def __init__(
        self,
        target: ObserveTarget,
        epoch: str,
        request_id: int,
        frame_id: int,
        check: Callable[[], None],
    ) -> None:
        if (
            type(target) is not ObserveTarget
            or type(epoch) is not str
            or re.fullmatch(r"[0-9a-f]{32}", epoch) is None
            or epoch == "0" * 32
            or type(request_id) is not int
            or not 1 <= request_id <= MAX_REQUESTS
            or type(frame_id) is not int
            or not 1 <= frame_id <= MAX_SEQUENCE_FRAMES
        ):
            _reject("invalid-frame-pairing-identity")
        self.target, self.epoch, self.request_id, self.frame_id = (
            target,
            epoch,
            request_id,
            frame_id,
        )
        self.check = check
        self.raw_header = b""
        self.observation: SequenceObservation | None = None
        self.stream: FrameStream | None = None
        self.terminal = False

    @property
    def payload_ready(self) -> bool:
        """Expose readiness only after exact metadata/header pairing."""
        return self.stream is not None and not self.terminal

    def _admit(self) -> None:
        if self.terminal:
            _reject("terminal-frame-pairing")
        self.check()

    def _pair(self) -> None:
        if self.observation is not None and len(self.raw_header) == FRAME_HEADER_BYTES:
            if self.stream is not None:
                _reject("duplicate-frame-pairing")
            self.stream = FrameStream(
                self.raw_header, self.observation.frame_identity()
            )
            self.check()

    def metadata(self, raw: bytes) -> None:
        """Bind exactly one canonical provider wrapper."""
        try:
            self._admit()
            if self.observation is not None:
                _reject("duplicate-frame-metadata")
            observation = SequenceObservation.parse(raw)
            identity = observation.frame_identity()
            if observation.target != self.target or (
                identity.session_id.hex(),
                identity.request_id,
                identity.frame_id,
            ) != (self.epoch, self.request_id, self.frame_id):
                _reject("wrong-frame-correlation")
            self.observation = observation
            self._pair()
            self.check()
        except Exception:
            self.terminal = True
            raise

    def header(self, raw: bytes) -> None:
        """Accumulate only the fixed header before allocating pixels."""
        try:
            self._admit()
            if (
                type(raw) is not bytes
                or not raw
                or len(self.raw_header) + len(raw) > FRAME_HEADER_BYTES
            ):
                _reject("invalid-frame-header")
            self.raw_header += raw
            self._pair()
            self.check()
        except Exception:
            self.terminal = True
            raise

    def feed(self, raw: bytes) -> None:
        """Hash bounded credited bytes without retaining another image."""
        try:
            self._admit()
            if self.stream is None:
                _reject("unpaired-frame-body")
            self.stream.feed(raw)
            self.check()
        except Exception:
            self.terminal = True
            raise

    def eof(self) -> FrameHeader:
        """Validate the digest only at actual transport EOF."""
        try:
            self._admit()
            if self.stream is None:
                _reject("unpaired-frame-eof")
            header = self.stream.finish()
            self.check()
            return header
        finally:
            self.terminal = True


class _Worker(QObject):
    """Single affinity owner; never exposed as a public launch-target interface."""

    progress = Signal(int, bytes)
    credit_required = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)

    def __init__(  # noqa: PLR0915 - explicit per-session and per-frame ownership
        self,
        generation: int,
        runtime: SequenceRuntimeManifest,
        cancelled: threading.Event,
    ) -> None:
        super().__init__()
        self.generation, self.runtime, self.cancelled = generation, runtime, cancelled
        self.vault = SequenceVault(generation, cancelled)
        self.credit_gate = CreditGate(cancelled)
        self.credits: ParentCredit | None = None
        self.credit_notified: CreditToken | None = None
        self.transfer_messages = 0
        self.pixel_sink: FrameSink | None = None
        self.pixel_candidate: VerifiedCandidate | None = None
        self.helper: OwnedHelper | None = None
        self.frame_server: QLocalServer | None = None
        self.frame_socket: QLocalSocket | None = None
        self.frame_disconnected = False
        self.frame_eof = False
        self.frame_drain_queued = False
        self.ready_notified = False
        self.observe_consumed = False
        self.observe_deadline_ns: int | None = None
        self.sequence_clock: SequenceDeadline | None = None
        self.observe_pairing: SequencePairing | None = None
        self.observe_metadata: SequenceObservation | None = None
        self.frame_header_read = 0
        self.frame_bytes = 0
        self.frame_first_byte_ns: int | None = None
        self.frame_validated_ns: int | None = None
        self.observe_finished_ns: int | None = None
        self.frame_digest: str | None = None
        self.server: QLocalServer | None = None
        self.socket: QLocalSocket | None = None
        self.timer: QTimer | None = None
        self.decoder, self.flight = Decoder(), _Flight()
        self.budget = SequenceBudget()
        self.collector: SequenceCollector | None = None
        self.sequence_candidate: SequenceCandidate | None = None
        self.sequence_target: ObserveTarget | None = None
        self.sequence_begun = False
        self.frame_id = 0
        self.requested_ns = 0
        self.sequence_deadline_ns = 0
        self.proofs: list[str] = []
        self.pipe_name = ""
        self.frame_serial = 0
        self.phase = "starting"
        self.failure: str | None = None
        self.cleanup_failure: str | None = None
        self.session_deadline = self.deadline = 0.0
        self.last_clock = 0.0
        self.caller_id = 0
        self.peer = 0
        self.epoch = uuid.uuid4().hex
        self.eof = False
        self.finish_requested = False
        self.pending_reply: tuple[int, bytes] | None = None
        self.verified_identity: bytes | None = None
        self.launch_returned = False
        self.done = False

    def _reserve_pixels(self, identity: FrameIdentity) -> None:
        if self.collector is None:
            _reject("missing-sequence-collector")
        self.collector.reserve(identity)

    def _accept_frame_connection(self, serial: int) -> None:
        if serial != self.frame_serial:
            self._fail("connection-on-retired-frame")
            return
        self._frame_connected()

    def _frame_event(self, serial: int, *, disconnected: bool) -> None:
        if serial != self.frame_serial:
            return  # Old queued notifications cannot operate on the next socket.
        if disconnected:
            self.frame_disconnected = True
        self._frame_readable()

    def _resume_current_frame(self, serial: int) -> None:
        if serial != self.frame_serial:
            return
        self.frame_drain_queued = False
        self._frame_readable()

    def _close_frame(self) -> None:
        if self.frame_server is not None:
            if self.frame_server.hasPendingConnections():
                _reject("surplus-frame-connection")
            self.frame_server.close()
            self.frame_server.deleteLater()
            self.frame_server = None
        if self.frame_socket is not None:
            self.frame_socket.deleteLater()
            self.frame_socket = None
        self.frame_serial += 1
        self.frame_drain_queued = False

    def _publish_stamp(self) -> None:
        if self.deadline > 0:
            effective = (
                self.deadline
                if self.phase == "retiring"
                else min(self.deadline, self.session_deadline)
            )
            self.vault.publish_stamp(self.phase, effective)

    def _discard_pixels(self) -> None:
        error: BaseException | None = None
        sink, self.pixel_sink = self.pixel_sink, None
        self.pixel_candidate = None
        self.sequence_candidate = None
        if sink is not None:
            try:
                sink.discard()
            except BaseException as failure:  # noqa: BLE001 - rethrow first after all disposal attempts
                error = failure
        if self.collector is not None:
            try:
                self.collector.discard()
            except BaseException as failure:  # noqa: BLE001 - rethrow first after all disposal attempts
                self.collector = None
                error = error or failure
        try:
            self.vault.invalidate()
        except BaseException as failure:  # noqa: BLE001 - rethrow first after all disposal attempts
            error = error or failure
        if error is not None:
            raise error

    def _admit(self) -> None:
        self._publish_stamp()
        if self.cancelled.is_set():
            _reject("cancelled")
        if self.phase != "retiring":
            if self.sequence_clock is None:
                _reject("missing-sequence-clock")
            self.sequence_clock.check()
        now = time.monotonic()
        if (
            type(now) not in (int, float)
            or not math.isfinite(now)
            or now < self.last_clock
        ):
            _reject("invalid-monotonic-clock")
        self.last_clock = now
        if now >= self.deadline or (
            self.phase != "retiring" and now >= self.session_deadline
        ):
            _reject("operation-timeout")

    @Slot()
    def start(self) -> None:
        """Construct resources only after arrival in the lifecycle thread."""
        try:
            issued = time.monotonic_ns()
            if type(issued) is not int or not 0 <= issued <= 2**63 - 1 - 30_000_000_000:
                _reject("invalid-worker-entry-clock")
            self.sequence_deadline_ns = issued + 30_000_000_000
            self.sequence_clock = SequenceDeadline(
                self.sequence_deadline_ns, last_ns=issued
            )
            self.session_deadline = self.sequence_deadline_ns / 1_000_000_000
            self.deadline = (issued + 3_000_000_000) / 1_000_000_000
            self.collector = SequenceCollector(self.epoch, self._admit)
            self._admit()
            name = f"VerdiClip-sequence-{uuid.uuid4().hex}"
            self.pipe_name = name
            command = _build_command(
                self.runtime, SequenceLaunch(name, self.epoch, os.getpid())
            )
            self._admit()
            self.server = QLocalServer(self)
            self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self.server.setMaxPendingConnections(1)
            self.server.newConnection.connect(self._connected)
            if not self.server.listen(name):
                _reject("listen-failed")
            self._admit()
            self.helper = OwnedHelper()
            self.helper.start(command)
        except Exception as error:  # noqa: BLE001 - terminal owned boundary
            self.failure = str(error) or type(error).__name__
        finally:
            self.launch_returned = True
        self.timer = QTimer(self)
        self.timer.setInterval(_POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        try:
            self._admit()
            if self.failure is not None:
                self._fail(self.failure)
        except Exception as error:  # noqa: BLE001 - cancellation after native return
            self._fail(str(error))

    @Slot()
    def _connected(self) -> None:
        try:
            self._admit()
            if (
                self.server is None
                or self.socket is not None
                or self.phase != "starting"
            ):
                _reject("unexpected-connection")
            self.socket = self.server.nextPendingConnection()
            if self.socket is None or self.helper is None:
                _reject("missing-owned-peer")
            self.socket.setReadBufferSize(MAX_MESSAGE_BYTES + HEADER_BYTES)
            self.peer = _peer_pid(self.socket.socketDescriptor())
            status = self.helper.poll()
            if self.peer != status.pid or self.peer not in (
                status.remaining_pids or ()
            ):
                _reject("wrong-peer-pid")
            self.socket.readyRead.connect(self._readable)
            self.socket.disconnected.connect(self._disconnected)
            self._readable()
        except Exception as error:  # noqa: BLE001 - one permanent rejection
            self._fail(str(error))

    @Slot()
    def _frame_connected(self) -> None:
        try:
            self._admit()
            if (
                self.frame_server is None
                or self.frame_socket is not None
                or self.phase != "busy"
                or self.observe_pairing is None
            ):
                _reject("unexpected-frame-connection")
            self.frame_socket = self.frame_server.nextPendingConnection()
            if self.frame_socket is None or self.helper is None:
                _reject("missing-owned-frame-peer")
            self.frame_socket.setReadBufferSize(MAX_READ_BYTES)
            peer = _peer_pid(self.frame_socket.socketDescriptor())
            status = self.helper.poll()
            if (
                peer != status.pid
                or not status.producer_verified
                or peer not in (status.remaining_pids or ())
                or status.failure is not None
                or status.cleanup_errors
                or status.closed
                or status.termination_requested
                or status.cleanup_confirmed
                or status.exit_code is not None
            ):
                _reject("wrong-frame-peer-pid")
            serial = self.frame_serial
            self.frame_socket.readyRead.connect(
                lambda: self._frame_event(serial, disconnected=False)
            )
            self.frame_socket.disconnected.connect(
                lambda: self._frame_event(serial, disconnected=True)
            )
            self._frame_readable()
            if self.phase == "ready":
                self._notify_ready()
        except Exception as error:  # noqa: BLE001 - permanent endpoint rejection
            self._fail(str(error))

    def _maybe_ready(self) -> None:
        if self.verified_identity is not None and self.decoder.buffer:
            _reject("unsolicited-trailing-bytes")
        if self.phase == "starting" and self.verified_identity is not None:
            self._admit()
            self.phase = "ready"
            self.deadline = self.session_deadline
            self._publish_stamp()

    @Slot()
    def _frame_disconnected(self) -> None:
        self.frame_disconnected = True
        self._frame_readable()

    @Slot()
    def _resume_frame(self) -> None:
        self.frame_drain_queued = False
        self._frame_readable()

    def _queue_frame(self) -> None:
        if not self.frame_drain_queued:
            self.frame_drain_queued = True
            serial = self.frame_serial
            QTimer.singleShot(0, self, lambda: self._resume_current_frame(serial))

    @Slot()
    def _frame_readable(self) -> None:
        if self.done or self.failure is not None or self.frame_socket is None:
            return
        try:
            self._admit()
            if self._inert_frame():
                return
            self._drain_frame()
            self._finish_frame()
        except Exception as error:  # noqa: BLE001 - bounded one-shot frame admission
            self._fail(str(error))

    def _inert_frame(self) -> bool:
        if self.frame_socket is None:
            _reject("missing-frame-socket")
        if self.observe_pairing is None:
            if self.frame_socket.bytesAvailable():
                _reject("unsolicited-frame-bytes")
            if self.frame_disconnected:
                if not self.finish_requested:
                    _reject("unexpected-frame-eof")
                self.frame_eof = True
            return True
        if self.frame_eof:
            if self.frame_socket.bytesAvailable():
                _reject("frame-bytes-after-eof")
            return True
        return False

    def _drain_frame(self) -> None:  # noqa: C901, PLR0912 - independent header, credit and sink gates
        if self.frame_socket is None or self.observe_pairing is None:
            _reject("missing-frame-state")
        budget = MAX_READ_BYTES
        while budget and self.frame_socket.bytesAvailable():
            self._admit()
            if self.frame_header_read < FRAME_HEADER_BYTES:
                size = min(budget, FRAME_HEADER_BYTES - self.frame_header_read)
                data = bytes(self.frame_socket.read(size).data())
                if not data:
                    break
                self.observe_pairing.header(data)
                self.frame_header_read += len(data)
                if self.frame_header_read == FRAME_HEADER_BYTES:
                    self._notify_credit()
            elif self.observe_pairing.payload_ready:
                if self.credits is None:
                    _reject("missing-frame-credit")
                credit_state = self.credits.snapshot()
                remaining = credit_state.granted_bytes - credit_state.accepted_bytes
                if remaining <= 0 or self.frame_socket.bytesAvailable() > remaining:
                    _reject("body-without-credit")
                data = bytes(self.frame_socket.read(min(budget, remaining)).data())
                if not data:
                    break
                self.observe_pairing.feed(data)
                if self.pixel_sink is None:
                    if self.observe_metadata is None:
                        _reject("missing-pixel-metadata")
                    identity = self.observe_metadata.frame_identity()
                    self._reserve_pixels(identity)
                    self.pixel_sink = FrameSink(identity, self._admit)
                self.pixel_sink.feed(data)
                self.credits.accept_body(len(data))
                self.frame_bytes += len(data)
                if self.frame_first_byte_ns is None:
                    self.frame_first_byte_ns = time.monotonic_ns()
                    self._admit()
                    self.progress.emit(
                        self.generation,
                        _serialized({"phase": "frame-body", "bytes": self.frame_bytes}),
                    )
            else:
                break  # Wait for metadata, without a zero-progress continuation loop.
            budget -= len(data)
        self._admit()
        self._notify_credit()

    def _transfer_control(self) -> None:
        self.budget.record(self.frame_id if self.observe_pairing is not None else None)
        self.transfer_messages += 1

    def _notify_credit(self) -> None:
        if (
            self.credits is None
            or self.observe_pairing is None
            or not self.observe_pairing.payload_ready
            or self.decoder.buffer
        ):
            return
        token = self.credits.grantable
        if token is None or token == self.credit_notified:
            return
        self._admit()
        if self.pixel_sink is None:
            if self.observe_metadata is None:
                _reject("missing-pixel-metadata")
            identity = self.observe_metadata.frame_identity()
            self._reserve_pixels(identity)
            self.pixel_sink = FrameSink(identity, self._admit)
        self._admit()
        self.credit_notified = token
        self.credit_required.emit(
            self.generation,
            encode(Message("frame_wait", token.request_id, token.payload())),
        )

    @Slot(int, bytes)
    def grant_credit(self, generation: int, raw: bytes) -> None:
        """Reserve one credit against cancellation before any native pipe write."""
        try:
            self._admit()
            if (
                generation != self.generation
                or self.phase != "busy"
                or self.credits is None
                or self.socket is None
            ):
                _reject("invalid-credit-phase")
            decoder = Decoder()
            messages = decoder.feed(raw)
            if (
                len(messages) != 1
                or decoder.buffer
                or messages[0].kind != "frame_credit"
            ):
                _reject("invalid-credit-command")
            message = messages[0]
            token = CreditToken.from_payload(message.request_id, message.payload)
            if token != self.credit_notified:
                _reject("unannounced-credit")
            self._admit()
            self._transfer_control()
            self.credits.reserve_grant(token)
            self.credit_notified = None
            if self.socket.bytesToWrite() or self.socket.write(raw) != len(raw):
                _reject("credit-write-failed")
            self._admit()
        except Exception as error:  # noqa: BLE001 - no duplicate or failed credit recovery
            self._fail(str(error))

    def _finish_frame(self) -> None:  # noqa: C901, PLR0915 - EOF, proof and timely acceptance gates
        if self.frame_socket is None or self.observe_pairing is None:
            _reject("missing-frame-state")
        if self.frame_socket.bytesAvailable():
            if self.observe_pairing.payload_ready:
                self._queue_frame()
            return
        if not self.frame_disconnected:
            return
        if not self.observe_pairing.payload_ready:
            _reject("truncated-frame-header-or-metadata")
        if (
            self.credits is None
            or self.pixel_sink is None
            or self.observe_metadata is None
        ):
            _reject("missing-retained-frame-state")
        self.credits.finish()
        header = self.observe_pairing.eof()
        self._admit()
        candidate = self.pixel_sink.freeze(header, self.observe_metadata.canonical)
        self.pixel_sink = None
        self._admit()
        verified_ns = time.monotonic_ns()
        snapshot = self.credits.snapshot()
        proof = SequenceFrameProof(
            _canonical(
                {
                    "proof_schema": 1,
                    "epoch": self.epoch,
                    "request_id": header.identity.request_id,
                    "frame_id": self.frame_id,
                    "requested_ns": self.requested_ns,
                    "deadline_ns": self.observe_deadline_ns,
                    "verified_ns": verified_ns,
                    "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                    "observation_sha256": header.identity.observation_sha256.hex(),
                    "image_sha256": header.image_sha256.hex(),
                    "bytes": self.frame_bytes,
                    "credit_chunks": len(snapshot.events) // 2,
                    "granted_bytes": snapshot.granted_bytes,
                    "accepted_bytes": snapshot.accepted_bytes,
                    "credit_events": _credit_snapshot(snapshot)["events"],
                    "observation": json.loads(self.observe_metadata.canonical),
                }
            )
        )
        self._admit()
        if self.collector is None or self.socket is None:
            _reject("missing-sequence-collector")
        self.collector.accept(candidate, proof)
        del candidate
        self._admit()
        if self.decoder.buffer or self.socket.bytesAvailable():
            _reject("unsolicited-control-before-accept")
        self._transfer_control()
        data = encode(Message("frame_accept", header.identity.request_id, proof.value))
        self._admit()
        if self.socket.bytesToWrite() or self.socket.write(data) != len(data):
            _reject("frame-accept-write-failed")
        self._admit()
        self.collector.commit(proof.digest)
        self._admit()
        self.proofs.append(proof.digest.hex())
        self.frame_digest = header.image_sha256.hex()
        self.frame_validated_ns = verified_ns
        self.frame_eof = True
        self._close_frame()
        self.observe_deadline_ns = None
        if self.sequence_clock is None:
            _reject("missing-sequence-clock-after-accept")
        self.sequence_clock.frame_deadline_ns = None
        self.observe_pairing = None
        self.observe_metadata = None
        self.phase = "ready"
        self.deadline = self.session_deadline
        self._publish_stamp()
        self._admit()
        self.reply.emit(
            self.generation,
            self.caller_id,
            _serialized(
                {
                    "operation": "frame_accepted",
                    "frame_id": self.frame_id,
                    "proof_sha256": proof.digest.hex(),
                    "sequence_sha256": self.collector.chain.hex(),
                }
            ),
        )

    def _begin_observe(self, payload: dict[str, object]) -> dict[str, object]:
        if (
            not self.sequence_begun
            or self.sequence_target is None
            or self.frame_id >= MAX_SEQUENCE_FRAMES
        ):
            _reject("sequence-not-ready-for-frame")
        if (
            set(payload) != {"operation", "frame_id", "requested_ns", "deadline_ns"}
            or type(payload["frame_id"]) is not int
            or payload["frame_id"] != self.frame_id + 1
        ):
            _reject("invalid-frame-request")
        if (
            self.collector is None
            or self.collector.count >= MAX_SEQUENCE_FRAMES
            or self.collector.retained_bytes > MAX_SOURCE_BYTES - MAX_FRAME_BYTES
        ):
            _reject("sequence-capacity-before-request")
        if self.sequence_clock is None:
            _reject("missing-sequence-clock")
        self.sequence_clock.frame(payload["requested_ns"], payload["deadline_ns"])
        self.observe_deadline_ns = cast("int", payload["deadline_ns"])
        self.requested_ns = cast("int", payload["requested_ns"])
        self.deadline = self.observe_deadline_ns / 1_000_000_000
        self.frame_id += 1
        self.observe_consumed = True
        self.frame_eof = self.frame_disconnected = self.frame_drain_queued = False
        self.frame_header_read = self.frame_bytes = 0
        self.frame_first_byte_ns = self.frame_validated_ns = None
        self.credit_notified = None
        self.credits = None
        self.observe_pairing = SequencePairing(
            self.sequence_target,
            self.epoch,
            self.flight.completed + 1,
            self.frame_id,
            self._admit,
        )
        self.frame_serial += 1
        serial = self.frame_serial
        self.frame_server = QLocalServer(self)
        self.frame_server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.frame_server.setMaxPendingConnections(1)
        self.frame_server.newConnection.connect(
            lambda: self._accept_frame_connection(serial)
        )
        self._admit()
        if not self.frame_server.listen(f"{self.pipe_name}-frame-{self.frame_id}"):
            _reject("frame-listen-failed")
        self._admit()
        return payload

    @Slot(int, bytes)
    def submit(self, caller_id: int, payload: bytes) -> None:
        """Receive exactly one already-admitted immutable request."""
        try:
            self._admit()
            if self.phase != "ready" or self.flight.completed >= MAX_REQUESTS - 1:
                _reject("single-flight")
            if (
                type(caller_id) is not int
                or not self.caller_id < caller_id < MAX_REQUESTS
            ):
                _reject("caller-id")
            self.caller_id = caller_id
            self._send(_payload(payload))
        except Exception as error:  # noqa: BLE001 - fail closed on caller races
            self._fail(str(error))

    def _send(self, payload: dict[str, object]) -> None:
        self._admit()
        if self.socket is None:
            _reject("no-connection")
        operation = payload.get("operation")
        if operation == "observe":
            payload = self._begin_observe(payload)
        elif operation == "sequence_begin":
            if self.sequence_begun or set(payload) != {"operation", "target"}:
                _reject("invalid-sequence-begin")
            self.sequence_target = ObserveTarget.parse(_serialized(payload["target"]))
            payload = {
                **payload,
                "sequence_schema": 1,
                "epoch": self.epoch,
                "max_frames": 8,
                "deadline_ns": self.sequence_deadline_ns,
            }
        elif (
            operation != "sequence_finish" or not self.sequence_begun or not self.proofs
        ):
            _reject("unsupported-sequence-operation")
        self._transfer_control()
        data = encode(self.flight.begin(payload))
        self._admit()
        if self.socket.bytesToWrite() or self.socket.write(data) != len(data):
            _reject("write-failed")
        self._admit()
        self.phase = "busy"
        if operation != "observe":
            self.deadline = min(
                time.monotonic() + _COMMAND_SECONDS, self.session_deadline
            )
        self._publish_stamp()

    @Slot()
    def finish(self) -> None:
        """Reserve the final bounded operation for orderly EOF and retirement."""
        try:
            if self.phase != "ready":
                _reject("finish-while-busy")
            self.finish_requested = True
            self._send({"operation": "sequence_finish"})
        except Exception as error:  # noqa: BLE001 - no unsafe command retry
            self._fail(str(error))

    @Slot()
    def cancel(self) -> None:
        """Observe cancellation without enqueueing a provider command."""
        self.credit_gate.cancel()
        if self.credits is not None:
            self.credits.observe_cancel()
        self._fail("cancelled")

    def _ready_message(self, message: Message) -> None:
        if self.verified_identity is not None:
            _reject("duplicate-ready")
        if (
            message.kind != "ready"
            or message.request_id != 0
            or set(message.payload)
            != {
                "pid",
                "executable",
                "epoch",
                "protocol_version",
                "peer_schema",
                "credit_schema",
                "sequence_schema",
                "runtime_schema",
                "max_frames",
                "max_requests",
            }
        ):
            _reject("invalid-ready")
        if (
            message.payload["epoch"] != self.epoch
            or type(message.payload["protocol_version"]) is not int
            or message.payload["protocol_version"] != VERSION
            or type(message.payload["peer_schema"]) is not int
            or message.payload["peer_schema"] != PEER_SCHEMA
            or type(message.payload["credit_schema"]) is not int
            or message.payload["credit_schema"] != CREDIT_SCHEMA
        ):
            _reject("wrong-session-epoch")
        for key, expected in (
            ("sequence_schema", SEQUENCE_SCHEMA),
            ("runtime_schema", RUNTIME_SCHEMA),
            ("max_frames", MAX_SEQUENCE_FRAMES),
            ("max_requests", MAX_REQUESTS),
        ):
            if (
                type(message.payload[key]) is not int
                or message.payload[key] != expected
            ):
                _reject("invalid-ready-profile")
        pid, executable = message.payload["pid"], message.payload["executable"]
        if type(pid) is not int or pid != self.peer or type(executable) is not str:
            _reject("invalid-ready")
        if self.helper is None:
            _reject("unverified-producer")
        status = self.helper.verify_ready(pid, executable)
        if (
            not status.producer_verified
            or status.pid != self.peer
            or status.failure is not None
            or status.cleanup_errors
            or status.closed
            or status.termination_requested
            or status.cleanup_confirmed
            or status.exit_code is not None
            or self.peer not in (status.remaining_pids or ())
        ):
            _reject("unverified-producer")
        identity = _serialized(message.payload)
        self._admit()
        self.verified_identity = identity
        self._maybe_ready()

    def _message(self, message: Message) -> None:
        self._admit()
        self._transfer_control()
        if self.phase == "starting":
            self._ready_message(message)
            return
        if self.phase != "busy":
            _reject("unsolicited-message")
        if message.kind == "frame_wait":
            if self.credits is None or self.finish_requested:
                _reject("unexpected-frame-wait")
            self.credits.receive_wait(
                CreditToken.from_payload(message.request_id, message.payload)
            )
            return
        self.flight.accept(message)
        if self.finish_requested:
            if self.collector is None or _canonical(message.payload) != _canonical(
                {
                    "operation": "sequence_finish",
                    "sequence_schema": 1,
                    "epoch": self.epoch,
                    "frames": len(self.proofs),
                    "proof_sha256": self.proofs,
                    "sequence_sha256": self.collector.chain.hex(),
                }
            ):
                _reject("invalid-sequence-finish")
            self._admit()
            self.sequence_candidate = self.collector.finish()
            self._admit()
            self.phase = "retiring"
            self.deadline = time.monotonic() + _CLEANUP_SECONDS
            self._publish_stamp()
        elif self.observe_pairing is not None:
            if (
                self.observe_metadata is not None
                or set(message.payload)
                != {"operation", "sequence_schema", "epoch", "frame_id", "observation"}
                or message.payload["operation"] != "observe"
                or type(message.payload["sequence_schema"]) is not int
                or message.payload["sequence_schema"] != 1
                or message.payload["epoch"] != self.epoch
                or type(message.payload["frame_id"]) is not int
                or message.payload["frame_id"] != self.frame_id
            ):
                _reject("invalid-sequence-observation")
            metadata = SequenceObservation.parse(
                _canonical(message.payload["observation"])
            )
            self.observe_pairing.metadata(metadata.canonical)
            self._admit()
            self.observe_metadata = metadata
            identity = metadata.frame_identity()
            self.credits = ParentCredit(
                self.epoch,
                identity.request_id,
                self.frame_id,
                identity.payload_bytes,
                gate=self.credit_gate,
            )
        else:
            if self.sequence_begun or _canonical(message.payload) != _canonical(
                {
                    "operation": "sequence_begin",
                    "sequence_schema": 1,
                    "epoch": self.epoch,
                    "max_frames": 8,
                    "deadline_ns": self.sequence_deadline_ns,
                }
            ):
                _reject("invalid-sequence-begin-response")
            self.sequence_begun = True
            self.vault.begin(self.epoch)
            self.pending_reply = (self.caller_id, _serialized(message.payload))
            self.phase = "ready"
            self.deadline = self.session_deadline
            self._publish_stamp()

    @Slot()
    def _readable(self) -> None:  # noqa: C901 - validate complete bounded control batch
        if self.done or self.failure is not None or self.socket is None:
            return
        was_starting = self.phase == "starting"
        try:
            budget = MAX_MESSAGE_BYTES + HEADER_BYTES
            while budget and self.socket.bytesAvailable():
                if (
                    self.budget.failed
                    or self.budget.records >= MAX_SEQUENCE_CONTROL_RECORDS
                ):
                    _reject("sequence-record-bound-before-read")
                data = bytes(self.socket.read(budget).data())
                if not data:
                    break
                budget -= len(data)
                for message in self.decoder.feed(data):
                    self._message(message)
            if self.socket.bytesAvailable():
                _reject("buffer-limit")
            if self.phase in ("ready", "retiring") and self.decoder.buffer:
                _reject("unsolicited-trailing-bytes")
            self._admit()
            if was_starting and self.phase == "ready":
                self._notify_ready()
            if self.observe_metadata is not None:
                self._notify_credit()
                self._frame_readable()  # Resume a header-paused socket after metadata.
            if self.pending_reply is not None:
                caller, payload = self.pending_reply
                self.pending_reply = None
                self.reply.emit(self.generation, caller, payload)
        except Exception as error:  # noqa: BLE001 - validate batch before notifying
            self.pending_reply = None
            self._fail(str(error))

    def _notify_ready(self) -> None:
        if self.ready_notified:
            return
        if self.verified_identity is None:
            _reject("missing-verified-identity")
        self.ready_notified = True
        self.identity.emit(self.generation, self.verified_identity)
        self._admit()
        self.ready.emit(self.generation)

    @Slot()
    def _disconnected(self) -> None:
        self._readable()
        self.eof = True
        if self.decoder.buffer or self.phase != "retiring":
            self._fail("unexpected-eof")

    def _fail(self, reason: str) -> None:
        if self.done:
            return
        if self.failure is None:
            self.failure = reason or "channel-failure"
        self.pending_reply = None
        self.credit_gate.cancel()
        if self.credits is not None:
            self.credits.observe_cancel()
        self._discard_pixels()
        if self.phase != "retiring":
            self.phase = "retiring"
            self.deadline = time.monotonic() + _CLEANUP_SECONDS
            self._publish_stamp()
        if self.frame_server is not None:
            self.frame_server.close()
        if self.server is not None:
            self.server.close()
        if self.helper is not None:
            self.helper.terminate()
        if self.frame_socket is not None:
            self.frame_socket.abort()
        if self.socket is not None:
            self.socket.abort()

    @Slot()
    def _poll(self) -> None:  # noqa: C901 - explicit terminal admission gates
        if self.done:
            return
        try:
            if self.phase != "retiring":
                self._admit()
            if self.cancelled.is_set():
                self._fail("cancelled")
            now = time.monotonic()
            if self.phase != "retiring" and (
                now >= self.deadline
                or now >= self.session_deadline
                or (
                    self.observe_deadline_ns is not None
                    and time.monotonic_ns() >= self.observe_deadline_ns
                )
            ):
                self._fail("operation-timeout")
            if time.monotonic() >= self.deadline:
                self.cleanup_failure = "cleanup-timeout"
                if self.helper is not None:
                    self.helper.release_unconfirmed("Channel cleanup deadline exceeded")
                self._terminal()
                return
            if self.helper is None:
                self._terminal()
                return
            status = self.helper.poll()
            if status.exit_code is not None:
                self._readable()  # Preserve queued final bytes after process exit.
                self._frame_readable()
            if status.failure is not None or (
                status.exit_code is not None and status.exit_code != 0
            ):
                self._fail("producer-failed")
            if time.monotonic() >= self.deadline:
                return  # Next timer handles expiry; never admit an overdue success.
            if (
                status.cleanup_confirmed
                and self.phase == "retiring"
                and (
                    self.failure is not None
                    or (self.eof and self.frame_eof and not self.decoder.buffer)
                )
            ):
                self._terminal()
        except Exception as error:  # noqa: BLE001 - keep ownership on unexpected errors
            self._fail(str(error))

    def _observe_validated(self) -> bool:
        return (
            self.sequence_candidate is not None
            and 1 <= len(self.proofs) <= MAX_SEQUENCE_FRAMES
        )

    def _terminal(self) -> None:
        if self.done or not self.launch_returned:
            return
        try:
            try:
                self._terminal_impl()
            except BaseException as primary:
                try:
                    self._discard_pixels()
                except BaseException:  # noqa: BLE001 - preserve original terminal error
                    primary.add_note("Sequence payload disposal also failed")
                raise
        finally:
            primary = sys.exception()
            try:
                self.deleteLater()
            except BaseException as cleanup:
                try:
                    self.vault.invalidate()
                except BaseException:  # noqa: BLE001 - preserve first cleanup failure
                    cleanup.add_note("Sequence invalidation also failed")
                if primary is None:
                    raise
                primary.add_note("Sequence worker deferred deletion also failed")
            finally:
                primary = sys.exception()
                try:
                    QThread.currentThread().quit()
                except BaseException as cleanup:
                    try:
                        self.vault.invalidate()
                    except BaseException:  # noqa: BLE001 - preserve first cleanup failure
                        cleanup.add_note("Sequence invalidation also failed")
                    if primary is None:
                        raise
                    primary.add_note("Sequence worker thread quit also failed")

    def _terminal_impl(self) -> None:  # noqa: C901, PLR0912 - final independent cleanup gates
        if self.done or not self.launch_returned:
            return
        if self.cancelled.is_set():
            self.failure = self.failure or "cancelled"
        self.done = True
        if self.pixel_sink is not None:
            self.pixel_sink.discard()
            self.pixel_sink = None
        if self.timer is not None:
            self.timer.stop()
        if self.frame_server is not None:
            self.frame_server.close()
        if self.server is not None:
            self.server.close()
        if self.frame_socket is not None:
            self.frame_socket.close()
        if self.socket is not None:
            self.socket.close()
        status = self.helper.status if self.helper is not None else None
        receipt = {
            "failure": self.failure,
            "cleanup_failure": self.cleanup_failure,
            "not_launched": self.helper is None,
            "helper": asdict(status) if status is not None else None,
            "passed": self.finish_requested
            and self.failure is None
            and self.cleanup_failure is None
            and status is not None
            and status.cleanup_confirmed
            and status.closed
            and not status.termination_requested
            and not status.cleanup_errors
            and status.failure is None
            and status.exit_code == 0
            and self.eof
            and self.frame_eof
            and (not self.observe_consumed or self._observe_validated())
            and not self.decoder.buffer,
        }
        observation_receipt: dict[str, object] = {
            "frames": len(self.proofs),
            "proof_sha256": self.proofs,
            "sequence_sha256": self.sequence_candidate.chain.hex()
            if receipt["passed"] and self.sequence_candidate is not None
            else None,
            "frame_id": self.frame_id,
            "bytes": self.frame_bytes,
            "deadline_ns": self.observe_deadline_ns,
            "credit": _credit_snapshot(self.credits.snapshot())
            if self.credits is not None
            else None,
            "credit_cancellation": {
                "latched": _credit_snapshot(self.credits.witness().latched),
                "final": _credit_snapshot(self.credits.witness().final),
            }
            if self.credits is not None and self.credits.snapshot().observed_sequence
            else None,
        }
        receipt["sequence"] = observation_receipt
        serialized = _serialized(receipt)
        # Closing Qt resources and serializing cannot erase cleanup expiry.
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            if self.cancelled.is_set():
                self.failure = self.failure or "cancelled"
            if time.monotonic() >= self.deadline:
                self.cleanup_failure = self.cleanup_failure or "cleanup-timeout"
            receipt.update(
                passed=False, failure=self.failure, cleanup_failure=self.cleanup_failure
            )
            if self.observe_consumed:
                observation_receipt["sequence_sha256"] = None
            serialized = _serialized(receipt)
        if receipt["passed"] and self.observe_consumed:
            try:
                if self.sequence_candidate is None:
                    _reject("missing-retained-candidate")
                self.vault.seal(self.sequence_candidate, serialized)
                self.sequence_candidate = None
                self._admit()
            except Exception as error:  # noqa: BLE001 - never publish partial pixels
                self._discard_pixels()
                self.failure = self.failure or str(error)
                receipt.update(passed=False, failure=self.failure)
                observation_receipt["sequence_sha256"] = None
                serialized = _serialized(receipt)
        else:
            self._discard_pixels()
        self.terminal.emit(self.generation, serialized)


_RETAINED: set[_Lifetime] = set()


class _Lifetime(QObject):
    """Outlive facade deletion until terminal evidence and actual thread finish."""

    progress = Signal(int, bytes)
    credit_required = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)
    request = Signal(int, bytes)
    credit = Signal(int, bytes)
    cancellation = Signal()
    finishing = Signal()

    def __init__(
        self,
        generation: int,
        runtime: SequenceRuntimeManifest,
        cancelled: threading.Event,
    ) -> None:
        super().__init__()
        self.generation, self.cancelled = generation, cancelled
        self.worker_thread = QThread()
        self.worker = _Worker(generation, runtime, cancelled)
        self.vault = self.worker.vault
        self.credit_gate = self.worker.credit_gate
        self.expiry_timer = QTimer(self)
        self.expiry_timer.setInterval(_POLL_MS)
        self.expiry_timer.timeout.connect(self._expiry)
        self.expiry_timer.start()
        self.worker.moveToThread(self.worker_thread)
        self.receipt: bytes | None = None
        self.stopped = False
        self.completed = False
        self._released = False
        self._missing_receipt = False
        self.worker_thread.started.connect(self.worker.start)
        queued = Qt.ConnectionType.QueuedConnection
        self.request.connect(self.worker.submit, queued)
        self.credit.connect(self.worker.grant_credit, queued)
        self.worker.credit_required.connect(self.credit_required, queued)
        self.cancellation.connect(self.worker.cancel, queued)
        self.finishing.connect(self.worker.finish, queued)
        self.worker.progress.connect(self.progress, queued)
        self.worker.identity.connect(self.identity, queued)
        self.worker.ready.connect(self.ready, queued)
        self.worker.reply.connect(self.reply, queued)
        self.worker.terminal.connect(self._retired, queued)
        self.worker_thread.finished.connect(self._stopped, queued)
        _RETAINED.add(self)

    @Slot()
    def abandon(self) -> None:
        """Deletion of the GUI facade is cancellation, never thread destruction."""
        self.vault.invalidate()
        self.credit_gate.cancel()
        if not self.completed:
            self.cancellation.emit()
        self._release_if_disposed()

    @Slot(int, bytes)
    def _retired(self, generation: int, receipt: bytes) -> None:
        if generation == self.generation and self.receipt is None:
            self.receipt = receipt
            self._complete()
        else:
            self.vault.invalidate()
            self._release_if_disposed()

    @Slot()
    def _stopped(self) -> None:
        self.stopped = True
        self._complete()

    def _complete(self) -> None:
        if self.receipt is None or not self.stopped or self.completed:
            return
        if (
            self.worker.observe_consumed
            and json.loads(self.receipt).get("passed") is True
        ):
            try:
                self.vault.authorize(self.receipt)
            except Exception:  # noqa: BLE001 - metadata mismatch prevents publication
                self.vault.invalidate()
                value = json.loads(self.receipt)
                value.update(
                    passed=False,
                    failure=value.get("failure") or "retained-frame-unavailable",
                )
                if "sequence" in value:
                    value["sequence"]["sequence_sha256"] = None
                self.receipt = _serialized(value)
        self.completed = True
        self.worker_thread.deleteLater()
        self.terminal.emit(self.generation, self.receipt)
        self._release_if_disposed()

    def _release_if_disposed(self) -> None:
        if self._released:
            return
        if (
            self._missing_receipt
            and self.worker.helper is not None
            and not self.worker.helper.status.closed
        ):
            # Missing metadata is not evidence that native ownership was released.
            self.expiry_timer.stop()
            return
        if (
            self.completed
            and self.stopped
            and self.vault.state
            in (FrameState.EMPTY, FrameState.INVALID, FrameState.TAKEN)
        ):
            self._released = True
            self.expiry_timer.stop()
            _RETAINED.discard(self)
            self.deleteLater()

    @Slot()
    def _expiry(self) -> None:
        if self._released:
            return
        stamp = self.vault.stamp()
        expired = stamp is not None and self.vault.expire(
            stamp.revision, time.monotonic()
        )
        missing = self.stopped and self.receipt is None and (expired or stamp is None)
        if missing:
            self._missing_receipt = True
            self.vault.invalidate()
            self.receipt = _serialized(
                {"passed": False, "failure": "missing-terminal-receipt"}
            )
            self._complete()
        self._release_if_disposed()


class SequenceChannel(QObject):
    """Unused facade with no public executable, module, path or command selection."""

    progress = Signal(int, bytes)
    credit_required = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)
    _request = Signal(int, bytes)
    _credit = Signal(int, bytes)
    _cancel = Signal()
    _finish = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._state = "idle"
        self._cancelled = threading.Event()
        self._lifetime: _Lifetime | None = None
        self._vault: SequenceVault | None = None
        self._terminal_receipt: bytes | None = None
        self._last_caller = 0
        self._observe_consumed = False
        self._sequence_begun = False
        self._sequence_deadline_ns: int | None = None
        self._frame_id = 0
        self._pending_credit: CreditToken | None = None
        self._credit_epoch: str | None = None
        self._credit_next = 0

    def _affinity(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Channel facade requires its owning thread")

    def start(self, runtime: SequenceRuntimeManifest) -> int:
        """Start once using only the attested installed fixed peer."""
        self._affinity()
        if self._state != "idle" or type(runtime) is not SequenceRuntimeManifest:
            raise RuntimeError("Channel cannot start")
        self._generation += 1
        self._state = "starting"
        self._lifetime = _Lifetime(self._generation, runtime, self._cancelled)
        self._vault = self._lifetime.vault
        self._credit_epoch = self._lifetime.worker.epoch
        queued = Qt.ConnectionType.QueuedConnection
        self._request.connect(self._lifetime.request)
        self._credit.connect(self._lifetime.credit)
        self._lifetime.credit_required.connect(self._credit_required, queued)
        self._cancel.connect(self._lifetime.cancellation)
        self._finish.connect(self._lifetime.finishing)
        self._lifetime.progress.connect(self._progress, queued)
        self._lifetime.identity.connect(self._identity, queued)
        self._lifetime.ready.connect(self._ready, queued)
        self._lifetime.reply.connect(self._reply, queued)
        self._lifetime.terminal.connect(self._retired, queued)
        self.destroyed.connect(self._lifetime.abandon)
        self._lifetime.worker_thread.start()
        return self._generation

    def begin(self, caller_id: int, target: ObserveTarget) -> None:
        """Freeze the selected target once, without changing the old profile."""
        self._affinity()
        try:
            if (
                self._state != "ready"
                or self._sequence_begun
                or type(target) is not ObserveTarget
            ):
                _reject("invalid-public-sequence-begin")
            self._caller(caller_id)
            self._state = "busy"
            self._request.emit(
                caller_id,
                _serialized(
                    {
                        "operation": "sequence_begin",
                        "target": json.loads(target.serialize()),
                    }
                ),
            )
        except Exception:
            self.cancel()
            raise

    def _caller(self, caller_id: int) -> None:
        if (
            type(caller_id) is not int
            or not self._last_caller < caller_id < MAX_REQUESTS
        ):
            _reject("invalid-caller-id")
        self._last_caller = caller_id

    def observe(self, caller_id: int) -> None:
        """Stamp before serialization/queuing; acceptance gets no renewed allowance."""
        self._affinity()
        try:
            if (
                self._state != "ready"
                or not self._sequence_begun
                or self._frame_id >= MAX_SEQUENCE_FRAMES
                or self._vault is None
            ):
                _reject("invalid-public-observe")
            issued = time.monotonic_ns()
            stamp = self._vault.stamp()
            if (
                type(issued) is not int
                or not 0 <= issued <= 2**63 - 1 - OBSERVE_NS
                or stamp is None
                or stamp.phase != "ready"
                or self._sequence_deadline_ns is None
            ):
                _reject("missing-sequence-deadline")
            deadline = min(issued + OBSERVE_NS, self._sequence_deadline_ns)
            self._caller(caller_id)
            self._frame_id += 1
            self._credit_next = 0
            self._pending_credit = None
            self._observe_consumed = True
            self._state = "busy"
            payload = _serialized(
                {
                    "operation": "observe",
                    "frame_id": self._frame_id,
                    "requested_ns": issued,
                    "deadline_ns": deadline,
                }
            )
            _payload(payload)
            if time.monotonic_ns() >= deadline or self._cancelled.is_set():
                _reject("expired-before-queue")
            self._request.emit(caller_id, payload)
        except Exception:
            self.cancel()
            raise

    @Slot(int, bytes)
    def _progress(self, generation: int, payload: bytes) -> None:
        if (
            generation == self._generation
            and self._state == "busy"
            and not self._cancelled.is_set()
        ):
            self.progress.emit(generation, payload)

    def cancel(self) -> None:
        """Latch before the queued wake; retain ownership until retirement."""
        self._affinity()
        already_cancelled = self._cancelled.is_set()
        self._pending_credit = None
        if self._lifetime is not None:
            self._lifetime.credit_gate.cancel()
        if self._vault is not None:
            self._vault.invalidate()
        if self._state in ("idle", "terminal") or already_cancelled:
            if self._lifetime is not None:
                self._lifetime._release_if_disposed()
            return
        self._cancelled.set()
        self._state = "closing"
        self._cancel.emit()

    @Slot(int, bytes)
    def _credit_required(self, generation: int, raw: bytes) -> None:
        if (
            generation != self._generation
            or self._state != "busy"
            or self._cancelled.is_set()
        ):
            return
        try:
            decoder = Decoder()
            messages = decoder.feed(raw)
            if (
                len(messages) != 1
                or decoder.buffer
                or messages[0].kind != "frame_wait"
                or self._pending_credit is not None
                or not self._observe_consumed
            ):
                _reject("invalid-public-credit-wait")
            token = CreditToken.from_payload(
                messages[0].request_id, messages[0].payload
            )
            if (
                token.epoch != self._credit_epoch
                or token.chunk_index != self._credit_next
                or token.frame_id != self._frame_id
                or token.request_id != self._frame_id + 1
            ):
                _reject("stale-public-credit-wait")
            self._pending_credit = token
            self.credit_required.emit(generation, raw)
        except Exception:
            self.cancel()
            raise

    def grant_frame_credit(
        self, generation: int, request_id: int, frame_id: int, chunk_index: int
    ) -> None:
        """Consume one capacity token before queuing its single worker grant."""
        self._affinity()
        try:
            token = self._pending_credit
            if (
                self._state != "busy"
                or self._cancelled.is_set()
                or type(generation) is not int
                or generation != self._generation
                or type(request_id) is not int
                or type(frame_id) is not int
                or type(chunk_index) is not int
                or token is None
                or token.frame_id != frame_id
                or token.request_id != request_id
                or token.chunk_index != chunk_index
            ):
                _reject("invalid-public-credit-grant")
            self._pending_credit = None
            self._credit_next += 1
            self._credit.emit(
                generation, encode(Message("frame_credit", request_id, token.payload()))
            )
        except Exception:
            self.cancel()
            raise

    def finish(self) -> None:
        """Request orderly retirement only when no operation is outstanding."""
        self._affinity()
        if self._state != "ready" or not self._sequence_begun or not self._frame_id:
            raise RuntimeError("Channel is not ready to finish")
        self._state = "closing"
        self._finish.emit()

    @Slot(int, bytes)
    def _identity(self, generation: int, identity: bytes) -> None:
        if generation == self._generation and self._state == "starting":
            self.identity.emit(generation, identity)

    @Slot(int)
    def _ready(self, generation: int) -> None:
        if generation == self._generation and self._state == "starting":
            self._state = "ready"
            self.ready.emit(generation)

    @Slot(int, int, bytes)
    def _reply(self, generation: int, caller: int, payload: bytes) -> None:
        if (
            generation == self._generation
            and self._state == "busy"
            and caller == self._last_caller
        ):
            value = json.loads(payload)
            if value.get("operation") == "sequence_begin":
                self._sequence_begun = True
                self._sequence_deadline_ns = value["deadline_ns"]
            self._pending_credit = None
            self._state = "ready"
            self.reply.emit(generation, caller, payload)

    @Slot(int, bytes)
    def _retired(self, generation: int, receipt: bytes) -> None:
        if generation != self._generation or self._state == "terminal":
            if self._vault is not None:
                self._vault.invalidate()
            return
        self._state = "terminal"
        if self._cancelled.is_set():
            value = json.loads(receipt)
            value["passed"] = False
            value["failure"] = value.get("failure") or "cancelled"
            if "sequence" in value:
                value["sequence"]["sequence_sha256"] = None
            receipt = _serialized(value)
        if self._observe_consumed and json.loads(receipt).get("passed") is True:
            value = json.loads(receipt)
            try:
                if self._vault is None:
                    _reject("Missing retained frame vault")
                self._vault.check_authorized(receipt)
            except Exception:  # noqa: BLE001 - queued delivery cannot publish stale success
                if self._vault is not None:
                    self._vault.invalidate()
                value.update(passed=False, failure="retained-frame-unavailable")
                if "sequence" in value:
                    value["sequence"]["sequence_sha256"] = None
                receipt = _serialized(value)
        self._terminal_receipt = receipt
        self.terminal.emit(generation, receipt)

    def take_sequence_result(self, generation: int) -> VerifiedSequence:
        """Transfer the immutable sequence once; later cancel cannot revoke it."""
        self._affinity()
        if (
            self._state != "terminal"
            or self._vault is None
            or self._terminal_receipt is None
            or json.loads(self._terminal_receipt).get("passed") is not True
        ):
            raise RuntimeError("No qualified retained frame")
        try:
            return self._vault.take(generation, self._terminal_receipt)
        finally:
            if self._lifetime is not None:
                self._lifetime._release_if_disposed()
