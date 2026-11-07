"""Unused asynchronous owner for the fixed installed peer and one observation.

No application call sites exist. Only the fixed attested peer can launch;
one read-only owned-WPF observation uses a separately authenticated binary pipe.
Deterministic seam tests do
not qualify its installed native runtime. Native lifetime and Qt I/O belong to
one worker thread.
Facade retention does not govern application/interpreter shutdown: callers must
keep the GUI event loop alive until terminal delivery after thread.finished.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Never

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from verdiclip.capture.frame_envelope import HEADER_BYTES as FRAME_HEADER_BYTES
from verdiclip.capture.observe_protocol import (
    MAX_READ_BYTES,
    Observation,
    ObservePairing,
    ObserveTarget,
)
from verdiclip.platform.helper_bootstrap import peer_arguments
from verdiclip.platform.helper_peer import (
    OBSERVE_NS,
    PEER_SCHEMA,
    ObserveDeadline,
    PeerLaunch,
    observe_payload,
)
from verdiclip.platform.helper_protocol import (
    HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    Decoder,
    Message,
    ProtocolError,
    SingleFlight,
    decode,
    encode,
)
from verdiclip.platform.helper_runtime import RuntimeManifest, inspect_runtime
from verdiclip.platform.owned_helper import HelperCommand, OwnedHelper

_STARTUP_SECONDS = 3.0
_COMMAND_SECONDS = 2.0
_SESSION_SECONDS = 20.0
_CLEANUP_SECONDS = 5.0
_POLL_MS = 10


def _reject(code: str) -> Never:
    raise ProtocolError(code)


def _build_command(runtime: RuntimeManifest, launch: PeerLaunch) -> HelperCommand:
    """Launch only the reviewed fixed peer, with no arbitrary dispatch."""
    return _fixed_peer_command(runtime, launch)


def _fixed_peer_command(runtime: RuntimeManifest, launch: PeerLaunch) -> HelperCommand:
    """Construct the fixed peer from validated installed parent identity."""
    if inspect_runtime() != runtime:
        raise ValueError("Selected runtime differs from installed parent")
    return HelperCommand(
        runtime.executable,
        peer_arguments(runtime.serialize(), launch.serialize()),
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
    envelope = b'{"version":1,"kind":"request","request_id":1,"payload":' + data + b"}"
    message = decode(envelope)
    encode(message)  # Include envelope overhead in the existing wire limit.
    return message.payload


def _serialized(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


class _Worker(QObject):
    """Single affinity owner; never exposed as a public launch-target interface."""

    progress = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)

    def __init__(
        self, generation: int, runtime: RuntimeManifest, cancelled: threading.Event
    ) -> None:
        super().__init__()
        self.generation, self.runtime, self.cancelled = generation, runtime, cancelled
        self.helper: OwnedHelper | None = None
        self.frame_server: QLocalServer | None = None
        self.frame_socket: QLocalSocket | None = None
        self.frame_disconnected = False
        self.frame_eof = False
        self.frame_drain_queued = False
        self.ready_notified = False
        self.observe_consumed = False
        self.observe_deadline_ns: int | None = None
        self.observe_clock: ObserveDeadline | None = None
        self.observe_pairing: ObservePairing | None = None
        self.observe_metadata: Observation | None = None
        self.frame_header_read = 0
        self.frame_bytes = 0
        self.frame_first_byte_ns: int | None = None
        self.frame_validated_ns: int | None = None
        self.observe_finished_ns: int | None = None
        self.frame_digest: str | None = None
        self.server: QLocalServer | None = None
        self.socket: QLocalSocket | None = None
        self.timer: QTimer | None = None
        self.decoder, self.flight = Decoder(), SingleFlight()
        self.phase = "starting"
        self.failure: str | None = None
        self.cleanup_failure: str | None = None
        self.session_deadline = self.deadline = 0.0
        self.caller_id = 0
        self.peer = 0
        self.epoch = uuid.uuid4().hex
        self.eof = False
        self.finish_requested = False
        self.pending_reply: tuple[int, bytes] | None = None
        self.verified_identity: bytes | None = None
        self.launch_returned = False
        self.done = False

    def _admit(self) -> None:
        if self.cancelled.is_set():
            _reject("cancelled")
        if self.phase != "retiring" and self.observe_deadline_ns is not None:
            if self.observe_clock is None:
                _reject("missing-observe-clock")
            self.observe_clock.check()
        now = time.monotonic()
        if now >= self.deadline or (
            self.phase != "retiring" and now >= self.session_deadline
        ):
            _reject("operation-timeout")

    @Slot()
    def start(self) -> None:
        """Construct resources only after arrival in the lifecycle thread."""
        self.session_deadline = time.monotonic() + _SESSION_SECONDS
        self.deadline = time.monotonic() + _STARTUP_SECONDS
        try:
            self._admit()
            name = f"VerdiClip-helper-{uuid.uuid4().hex}"
            command = _build_command(
                self.runtime, PeerLaunch(name, self.epoch, os.getpid())
            )
            self._admit()
            self.server = QLocalServer(self)
            self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self.server.setMaxPendingConnections(1)
            self.server.newConnection.connect(self._connected)
            if not self.server.listen(name):
                _reject("listen-failed")
            self.frame_server = QLocalServer(self)
            self.frame_server.setSocketOptions(
                QLocalServer.SocketOption.UserAccessOption
            )
            self.frame_server.setMaxPendingConnections(1)
            self.frame_server.newConnection.connect(self._frame_connected)
            if not self.frame_server.listen(name + "-frame"):
                _reject("frame-listen-failed")
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
                or self.phase != "starting"
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
                or peer not in (status.remaining_pids or ())
                or status.failure is not None
                or status.cleanup_errors
                or status.closed
                or status.termination_requested
                or status.cleanup_confirmed
                or status.exit_code is not None
            ):
                _reject("wrong-frame-peer-pid")
            self.frame_socket.readyRead.connect(self._frame_readable)
            self.frame_socket.disconnected.connect(self._frame_disconnected)
            self._maybe_ready()
            self._frame_readable()
            if self.phase == "ready":
                self._notify_ready()
        except Exception as error:  # noqa: BLE001 - permanent endpoint rejection
            self._fail(str(error))

    def _maybe_ready(self) -> None:
        if self.verified_identity is not None and self.decoder.buffer:
            _reject("unsolicited-trailing-bytes")
        if (
            self.phase == "starting"
            and self.verified_identity is not None
            and self.frame_socket is not None
        ):
            self._admit()
            self.phase = "ready"
            self.deadline = self.session_deadline

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
            QTimer.singleShot(0, self, self._resume_frame)

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

    def _drain_frame(self) -> None:
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
            elif self.observe_pairing.payload_ready:
                data = bytes(self.frame_socket.read(budget).data())
                if not data:
                    break
                self.observe_pairing.feed(data)
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

    def _finish_frame(self) -> None:
        if self.frame_socket is None or self.observe_pairing is None:
            _reject("missing-frame-state")
        if self.frame_socket.bytesAvailable():
            if self.observe_pairing.payload_ready:
                self._queue_frame()
            return
        if self.frame_disconnected and self.observe_pairing.payload_ready:
            header = self.observe_pairing.eof()
            self._admit()
            self.frame_validated_ns = (
                self.observe_clock.check() if self.observe_clock is not None else None
            )
            if self.decoder.buffer or (
                self.socket is not None and self.socket.bytesAvailable()
            ):
                _reject("unsolicited-control-before-finish")
            self.frame_digest = header.image_sha256.hex()
            self.frame_eof = True
            self.finish_requested = True
            self._send({"operation": "finish"})
        elif self.frame_disconnected and self.frame_header_read < FRAME_HEADER_BYTES:
            _reject("truncated-frame-header")

    def _begin_observe(self, payload: dict[str, object]) -> dict[str, object]:
        attempted = self.observe_consumed
        self.observe_consumed = True
        if attempted or self.flight.completed >= MAX_REQUESTS - 1:
            _reject("observe-consumed-or-no-finish-slot")
        self.observe_clock = ObserveDeadline(payload.get("deadline_ns"))
        self.observe_clock.check()
        target, original = observe_payload(payload)
        self.observe_deadline_ns = min(
            original, math.floor(self.session_deadline * 1_000_000_000)
        )
        self.observe_clock.deadline_ns = self.observe_deadline_ns
        self.observe_clock.check()
        self.deadline = self.observe_deadline_ns / 1_000_000_000
        self.observe_pairing = ObservePairing(
            target,
            self.epoch,
            self.flight.completed + 1,
            clock=time.monotonic,
            cancelled=self.cancelled.is_set,
            deadline=self.deadline,
        )
        self._admit()
        return {**payload, "deadline_ns": self.observe_deadline_ns}

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
        if payload.get("operation") == "observe":
            payload = self._begin_observe(payload)
        data = encode(self.flight.begin(payload))
        self._admit()
        if self.socket.bytesToWrite() or self.socket.write(data) != len(data):
            _reject("write-failed")
        self._admit()  # Preserve the old deadline across the actual native write.
        self.phase = "busy"
        if not self.observe_consumed:
            self.deadline = time.monotonic() + _COMMAND_SECONDS

    @Slot()
    def finish(self) -> None:
        """Reserve the final bounded operation for orderly EOF and retirement."""
        try:
            if self.phase != "ready":
                _reject("finish-while-busy")
            self.finish_requested = True
            self._send({"operation": "finish"})
        except Exception as error:  # noqa: BLE001 - no unsafe command retry
            self._fail(str(error))

    @Slot()
    def cancel(self) -> None:
        """Observe cancellation without enqueueing a provider command."""
        self._fail("cancelled")

    def _ready_message(self, message: Message) -> None:
        if self.verified_identity is not None:
            _reject("duplicate-ready")
        if (
            message.kind != "ready"
            or message.request_id != 0
            or set(message.payload)
            != {"pid", "executable", "epoch", "protocol_version", "peer_schema"}
        ):
            _reject("invalid-ready")
        if (
            message.payload["epoch"] != self.epoch
            or type(message.payload["protocol_version"]) is not int
            or message.payload["protocol_version"] != 1
            or type(message.payload["peer_schema"]) is not int
            or message.payload["peer_schema"] != PEER_SCHEMA
        ):
            _reject("wrong-session-epoch")
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
        if self.phase == "starting":
            self._ready_message(message)
            return
        if self.phase != "busy":
            _reject("unsolicited-message")
        self.flight.accept(message)
        if self.finish_requested:
            if message.payload != {"operation": "finished"}:
                _reject("invalid-finished")
            self._admit()
            if self.observe_consumed:
                self.observe_finished_ns = (
                    self.observe_clock.check()
                    if self.observe_clock is not None
                    else None
                )
            self.phase = "retiring"
            self.deadline = time.monotonic() + _CLEANUP_SECONDS
        elif self.observe_consumed:
            if (
                self.observe_pairing is None
                or self.observe_metadata is not None
                or set(message.payload) != {"operation", "observation"}
                or message.payload["operation"] != "observed"
            ):
                _reject("invalid-observation-response")
            metadata = Observation.parse(_serialized(message.payload["observation"]))
            self.observe_pairing.metadata(metadata.canonical)
            self._admit()
            self.observe_metadata = metadata
        else:
            payload = _serialized(message.payload)
            self._admit()
            self.pending_reply = (self.caller_id, payload)
            self.phase = "ready"
            self.deadline = self.session_deadline

    @Slot()
    def _readable(self) -> None:  # noqa: C901 - validate complete bounded control batch
        if self.done or self.failure is not None or self.socket is None:
            return
        was_starting = self.phase == "starting"
        try:
            budget = MAX_MESSAGE_BYTES + HEADER_BYTES
            while budget and self.socket.bytesAvailable():
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
            if (
                self.observe_metadata is not None
                and not self.finish_requested
                and self.decoder.buffer
            ):
                _reject("unsolicited-partial-observe-trailer")
            if self.observe_metadata is not None:
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
        if self.phase != "retiring":
            self.phase = "retiring"
            self.deadline = time.monotonic() + _CLEANUP_SECONDS
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
            self.observe_deadline_ns is not None
            and self.frame_validated_ns is not None
            and self.observe_finished_ns is not None
            and self.frame_validated_ns
            <= self.observe_finished_ns
            < self.observe_deadline_ns
            and self.observe_metadata is not None
            and self.frame_digest is not None
            and self.frame_bytes == self.observe_metadata.frame_identity().payload_bytes
        )

    def _terminal(self) -> None:  # noqa: C901 - final independent cleanup gates
        if self.done or not self.launch_returned:
            return
        if self.cancelled.is_set():
            self.failure = self.failure or "cancelled"
        self.done = True
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
            and not status.cleanup_errors
            and status.failure is None
            and status.exit_code == 0
            and self.eof
            and self.frame_eof
            and (not self.observe_consumed or self._observe_validated())
            and not self.decoder.buffer,
        }
        observation_receipt: dict[str, object] = {}
        if self.observe_consumed:
            observation_receipt = {
                "deadline_ns": self.observe_deadline_ns,
                "first_body_ns": self.frame_first_byte_ns,
                "validated_ns": self.frame_validated_ns,
                "finished_ns": self.observe_finished_ns,
                "bytes": self.frame_bytes,
                "image_sha256": self.frame_digest,
                "observation": json.loads(self.observe_metadata.canonical)
                if receipt["passed"] and self.observe_metadata is not None
                else None,
            }
            receipt["observe"] = observation_receipt
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
                observation_receipt["observation"] = None
            serialized = _serialized(receipt)
        self.terminal.emit(self.generation, serialized)
        self.deleteLater()
        QThread.currentThread().quit()


_RETAINED: set[_Lifetime] = set()


class _Lifetime(QObject):
    """Outlive facade deletion until terminal evidence and actual thread finish."""

    progress = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)
    request = Signal(int, bytes)
    cancellation = Signal()
    finishing = Signal()

    def __init__(
        self, generation: int, runtime: RuntimeManifest, cancelled: threading.Event
    ) -> None:
        super().__init__()
        self.generation, self.cancelled = generation, cancelled
        self.worker_thread = QThread()
        self.worker = _Worker(generation, runtime, cancelled)
        self.worker.moveToThread(self.worker_thread)
        self.receipt: bytes | None = None
        self.stopped = False
        self.completed = False
        self.worker_thread.started.connect(self.worker.start)
        queued = Qt.ConnectionType.QueuedConnection
        self.request.connect(self.worker.submit, queued)
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
        if not self.completed:
            self.cancelled.set()
            self.cancellation.emit()

    @Slot(int, bytes)
    def _retired(self, generation: int, receipt: bytes) -> None:
        if generation == self.generation and self.receipt is None:
            self.receipt = receipt
            self._complete()

    @Slot()
    def _stopped(self) -> None:
        self.stopped = True
        self._complete()

    def _complete(self) -> None:
        if self.receipt is None or not self.stopped or self.completed:
            return
        self.completed = True
        _RETAINED.discard(self)
        self.worker_thread.deleteLater()
        self.terminal.emit(self.generation, self.receipt)
        self.deleteLater()


class HelperChannel(QObject):
    """Unused facade with no public executable, module, path or command selection."""

    progress = Signal(int, bytes)
    identity = Signal(int, bytes)
    ready = Signal(int)
    reply = Signal(int, int, bytes)
    terminal = Signal(int, bytes)
    _request = Signal(int, bytes)
    _cancel = Signal()
    _finish = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._state = "idle"
        self._cancelled = threading.Event()
        self._lifetime: _Lifetime | None = None
        self._last_caller = 0
        self._observe_consumed = False

    def _affinity(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Channel facade requires its owning thread")

    def start(self, runtime: RuntimeManifest) -> int:
        """Start once using only the attested installed fixed peer."""
        self._affinity()
        if self._state != "idle" or type(runtime) is not RuntimeManifest:
            raise RuntimeError("Channel cannot start")
        self._generation += 1
        self._state = "starting"
        self._lifetime = _Lifetime(self._generation, runtime, self._cancelled)
        queued = Qt.ConnectionType.QueuedConnection
        self._request.connect(self._lifetime.request)
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

    def submit(self, caller_id: int, payload: bytes) -> None:
        """Accept one monotonically increasing bounded correlation ID, never a queue."""
        self._affinity()
        if self._state != "ready" or type(caller_id) is not int:
            raise RuntimeError("Channel is not ready")
        if not self._last_caller < caller_id < MAX_REQUESTS:
            raise ValueError("Caller correlation ID exceeds reserved request budget")
        if _payload(payload).get("operation") == "observe":
            raise ValueError("Use the typed one-shot observe method")
        self._last_caller, self._state = caller_id, "busy"
        self._request.emit(caller_id, payload)

    def observe(self, caller_id: int, target: ObserveTarget) -> None:
        """Reserve one attempt and timestamp before serialization or worker queuing."""
        self._affinity()
        if self._state != "ready" or self._observe_consumed:
            raise RuntimeError("Channel cannot observe")
        self._observe_consumed = True
        self._state = "busy"
        try:
            issued = time.monotonic_ns()
            if type(issued) is not int or not 0 <= issued <= 2**63 - 1 - OBSERVE_NS:
                _reject("invalid-monotonic-ns-clock")
            original = issued + OBSERVE_NS
            original_clock = ObserveDeadline(original, last_ns=issued)
            if (
                type(caller_id) is not int
                or not self._last_caller < caller_id < MAX_REQUESTS
            ):
                _reject("Invalid observe caller ID")
            if type(target) is not ObserveTarget:
                _reject("Expected immutable observation target")
            payload = _serialized(
                {
                    "operation": "observe",
                    "observe_schema": 1,
                    "deadline_ns": original,
                    "request": json.loads(target.serialize()),
                }
            )
            _payload(payload)
            original_clock.check()
            self._last_caller = caller_id
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
        if self._state in ("idle", "terminal") or self._cancelled.is_set():
            return
        self._cancelled.set()
        self._state = "closing"
        self._cancel.emit()

    def finish(self) -> None:
        """Request orderly retirement only when no operation is outstanding."""
        self._affinity()
        if self._state != "ready":
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
            self._state = "ready"
            self.reply.emit(generation, caller, payload)

    @Slot(int, bytes)
    def _retired(self, generation: int, receipt: bytes) -> None:
        if generation != self._generation or self._state == "terminal":
            return
        self._state = "terminal"
        self._lifetime = None
        if self._cancelled.is_set():
            value = json.loads(receipt)
            value["passed"] = False
            value["failure"] = value.get("failure") or "cancelled"
            if "observe" in value:
                value["observe"]["observation"] = None
            receipt = _serialized(value)
        self.terminal.emit(generation, receipt)
