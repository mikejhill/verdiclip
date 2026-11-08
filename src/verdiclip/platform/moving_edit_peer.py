"""Fixed peer5 transport for the owned EDIT seven-write research profile.

No GUI or provider selection is imported here. Completed native writes, bounded
fresh binary streams, exact private acceptance proofs and the final restore all
share the original worker deadline. A failed transport never initiates a write.
Public failure paths cancel and dispose; the provider's deferred failure-only
restoration primitive is not reachable through this peer entrypoint.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never, Protocol, cast

from verdiclip.capture.edit_observation import (
    EditObservation,
    baseline_identity_sha256,
    canonical_json,
    fields,
    parse_json,
    validate_continuity,
    validate_identity_evidence,
    validate_native_state,
    validate_target,
)
from verdiclip.capture.edit_scroll_contract import FRAME_LINES
from verdiclip.capture.edit_scroll_provider import (
    Captured,
    MovingProvider,
    NativeBoundary,
    ProviderResult,
)
from verdiclip.capture.frame_envelope import FrameHeader, header_for_rgb
from verdiclip.capture.moving_edit_frame import (
    MAX_READ_BYTES,
    PeerPixelSlots,
    discard_traceback,
)
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.capture.sequence_credit import CHUNK_BYTES, CreditToken, PeerCredit
from verdiclip.platform.moving_edit_protocol import (
    CREDIT_SCHEMA,
    HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    MAX_SEQUENCE_FRAMES,
    MOVING_SCHEMA,
    PEER_SCHEMA,
    RUNTIME_SCHEMA,
    VERSION,
    EvidenceCorrelatedReplay,
    EvidenceIdentity,
    EvidenceProducer,
    EvidenceReaderLaunch,
    EvidenceReaderState,
    Message,
    MovingEditLaunch,
    MovingFrameProof,
    ProtocolError,
    SequenceBudget,
    SequenceDeadline,
    decode,
    deserialize_evidence_reader_launch,
    deserialize_launch,
    encode,
    evidence_pipe_name,
    frame_pipe_name,
    make_evidence_hello,
    move_digest,
    validate_begin_payload,
    validate_begin_reply,
    validate_finish_reply,
    validate_finish_request,
    validate_frame_accept,
    validate_frame_request,
    validate_move_ack,
    validate_move_request,
)

_WAIT_FAILED = 0xFFFFFFFF
_WAIT_TIMEOUT = 258
_FINAL_CORRELATION_NS = 1_000_000_000


def _reject(reason: str) -> Never:
    raise ProtocolError(reason)


def _admission(check: Callable[[], object]) -> Callable[[], None]:
    def admitted() -> None:
        check()

    return admitted


class _ControlPipe(Protocol):
    """Bounded control operations used by the synchronous transport owner."""

    sequence_clock: SequenceDeadline | None

    def checkpoint(self) -> None: ...

    def write(self, message: Message) -> None: ...

    def read(self) -> Message: ...


class _EvidenceBoundary(Protocol):
    """Detached evidence supplied only by the fixed native boundary."""

    initial_state: dict[str, Any]
    identity_evidence: dict[str, Any]
    last_move: dict[str, Any]
    last_acquisition: dict[str, int]

    def set_deadline(self, deadline_ns: int) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _PendingFrame:
    """Keep the actual odd request ID, never derive it from frame plus one."""

    request_id: int
    frame_id: int
    requested_ns: int
    deadline_ns: int
    move_sha256: str


class MovingEditPeerProtocol:
    """Admit begin1, seven move/frame pairs and finish16 exactly once."""

    def __init__(self, launch: MovingEditLaunch) -> None:
        self.launch = launch
        self.failed = False
        self.finished = False
        self.frames = 0
        self.last_id = 0
        self.clock: SequenceDeadline | None = None
        self.target: ObserveTarget | None = None
        self.begin_payload: dict[str, Any] | None = None
        self.provider: MovingProvider | None = None
        self.identity_evidence: dict[str, Any] | None = None
        self.baseline = ""
        self.move_ack: dict[str, Any] | None = None
        self.move_reply_pending = False
        self.pending: _PendingFrame | None = None
        self.observations: list[EditObservation] = []
        self.proofs: list[str] = []
        self.chain = bytes(32)

    @contextmanager
    def _guard(self) -> Generator[None]:
        try:
            if self.failed or self.finished:
                _reject("moving-peer-terminal")
            yield
        except BaseException:
            self.failed = True
            raise

    def _admit(self, request: Message, request_id: int) -> None:
        if (
            type(request) is not Message
            or request.kind != "request"
            or type(request.version) is not int
            or request.version != VERSION
            or type(request.request_id) is not int
            or request.request_id != request_id
            or request_id != self.last_id + 1
            or request_id > MAX_REQUESTS
            or self.pending is not None
        ):
            raise ProtocolError("invalid-moving-request")

    def _live(self) -> tuple[SequenceDeadline, MovingProvider, _EvidenceBoundary]:
        if self.clock is None or self.provider is None:
            raise ProtocolError("moving-peer-not-begun")
        self.clock.check()
        return (
            self.clock,
            self.provider,
            cast("_EvidenceBoundary", self.provider.boundary),
        )

    def begin(self, request: Message) -> None:
        """Admit immutable worker-entry stamps before acquiring native resources."""
        with self._guard():
            self._admit(request, 1)
            if self.begin_payload is not None:
                raise ProtocolError("duplicate-moving-begin")
            value = validate_begin_payload(request.payload)
            self.clock = SequenceDeadline(value["sequence_deadline_ns"])
            now = self.clock.check()
            self.launch.validate_time(now)
            if (value["sequence_started_ns"], value["sequence_deadline_ns"]) != (
                self.launch.sequence_started_ns,
                self.launch.sequence_deadline_ns,
            ):
                raise ProtocolError("moving-begin-launch-stamps-mismatch")
            validate_begin_payload(value, now_ns=now)
            self.clock.frame_deadline_ns = value["command_deadline_ns"]
            self.clock.check()
            self.target = validate_target(value["target"])
            self.begin_payload = value
            self.last_id = 1

    def bind_provider(
        self, provider: MovingProvider, check: Callable[[], object]
    ) -> Message:
        """Validate the retained baseline after native setup under the same clock."""
        with self._guard():
            if (
                self.begin_payload is None
                or self.clock is None
                or self.provider is not None
            ):
                raise ProtocolError("invalid-moving-provider-bind")
            self.provider = provider
            boundary = cast("_EvidenceBoundary", provider.boundary)
            boundary.set_deadline(self.begin_payload["command_deadline_ns"])
            provider.begin(_admission(check))
            state = validate_native_state(boundary.initial_state, 0)
            identity = validate_identity_evidence(
                boundary.identity_evidence, state["native"]
            )
            target = self.target
            if target is None or (
                state["native"]["pid"],
                state["native"]["creation"],
                state["native"]["root"],
            ) != (target.pid, target.process_created_100ns, target.hwnd):
                raise ProtocolError("moving-baseline-target-mismatch")
            if (
                state["font"]["attestation"]
                != self.begin_payload["profile_evidence"]["font_attestation"]
            ):
                raise ProtocolError("moving-baseline-attestation-mismatch")
            self.baseline = baseline_identity_sha256(state, identity)
            self.identity_evidence = identity
            check()
            payload = validate_begin_reply(
                {
                    "operation": "moving_begun",
                    "epoch": self.launch.epoch,
                    "sequence_started_ns": self.begin_payload["sequence_started_ns"],
                    "sequence_deadline_ns": self.clock.deadline_ns,
                    "baseline_identity_sha256": self.baseline,
                }
            )
            return Message("response", 1, payload)

    def begin_sent(self, check: Callable[[], object]) -> None:
        """Retire the begin bound only after its actual successful response write."""
        with self._guard():
            if (
                self.clock is None
                or self.begin_payload is None
                or self.provider is None
                or self.last_id != 1
                or self.clock.frame_deadline_ns
                != self.begin_payload["command_deadline_ns"]
            ):
                raise ProtocolError("unexpected-moving-begin-completion")
            check()
            self.clock.frame_deadline_ns = None

    def move(self, request: Message, check: Callable[[], object]) -> Message:
        """Produce acknowledgment from the actual write and subsequent readback."""
        with self._guard():
            clock, provider, boundary = self._live()
            step = self.frames + 1
            self._admit(request, 2 * step)
            if (
                self.move_ack is not None
                or self.move_reply_pending
                or step > MAX_SEQUENCE_FRAMES
            ):
                raise ProtocolError("unaccepted-moving-step")
            value = validate_move_request(
                request.payload,
                session_deadline_ns=clock.deadline_ns,
                now_ns=clock.check(),
            )
            if value["step_id"] != step:
                raise ProtocolError("wrong-moving-step")
            clock.move(value["requested_ns"], value["deadline_ns"], step_id=step)
            boundary.set_deadline(value["deadline_ns"])
            provider.move(step, _admission(check))
            evidence = fields(
                boundary.last_move,
                "write_started_ns write_returned_ns readback_ns "
                "completed_ns native_state",
            )
            state = validate_native_state(
                evidence["native_state"], FRAME_LINES[step - 1]
            )
            if baseline_identity_sha256(state, self.identity_evidence) != self.baseline:
                raise ProtocolError("moving-write-identity-changed")
            ack = validate_move_ack(
                {
                    "operation": "move_completed",
                    "epoch": self.launch.epoch,
                    "request_id": request.request_id,
                    "step_id": step,
                    "expected_line": FRAME_LINES[step - 1],
                    "requested_ns": value["requested_ns"],
                    "deadline_ns": value["deadline_ns"],
                    **evidence,
                    "restoring": step == MAX_SEQUENCE_FRAMES,
                    "native_state_sha256": hashlib.sha256(
                        canonical_json(state)
                    ).hexdigest(),
                    "baseline_identity_sha256": self.baseline,
                },
                session_deadline_ns=clock.deadline_ns,
                now_ns=clock.check(),
            )
            check()
            self.move_ack = ack
            self.move_reply_pending = True
            self.last_id = request.request_id
            return Message("response", request.request_id, ack)

    def move_sent(self, check: Callable[[], object]) -> None:
        """The whole move clock includes acknowledgment serialization and write."""
        with self._guard():
            clock, _, _ = self._live()
            if not self.move_reply_pending or self.move_ack is None:
                raise ProtocolError("unexpected-moving-ack-completion")
            check()
            self.move_reply_pending = False
            clock.frame_deadline_ns = None

    def observe(self, request: Message) -> _PendingFrame:
        """Preserve the owner prequeue frame stamp until exact proof commitment."""
        with self._guard():
            clock, _, boundary = self._live()
            step = self.frames + 1
            self._admit(request, 2 * step + 1)
            if self.move_ack is None or self.move_reply_pending:
                raise ProtocolError("missing-completed-moving-write")
            value = validate_frame_request(
                request.payload,
                session_deadline_ns=clock.deadline_ns,
                now_ns=clock.check(),
            )
            if value["frame_id"] != step or value["move_sha256"] != move_digest(
                self.move_ack
            ):
                raise ProtocolError("stale-moving-write-ack")
            clock.frame(value["requested_ns"], value["deadline_ns"], step_id=step)
            boundary.set_deadline(value["deadline_ns"])
            self.pending = _PendingFrame(
                request.request_id,
                step,
                value["requested_ns"],
                value["deadline_ns"],
                value["move_sha256"],
            )
            self.last_id = request.request_id
            return self.pending

    def make_observation(self, captured: Captured) -> EditObservation:
        """Freeze sampled state, original target and native acquisition evidence."""
        with self._guard():
            clock, _, boundary = self._live()
            pending = self.pending
            if pending is None or self.begin_payload is None or self.move_ack is None:
                raise ProtocolError("unexpected-moving-observation")
            state = parse_json(captured.state, 16_384)
            if state != self.move_ack["native_state"]:
                raise ProtocolError("moving-state-changed-after-write-readback")
            observation = EditObservation.from_value(
                {
                    "schema": 1,
                    "profile": "owned-edit-moving-v1",
                    "epoch": self.launch.epoch,
                    "target": self.begin_payload["target"],
                    "request_id": pending.request_id,
                    "frame_id": pending.frame_id,
                    "step_id": pending.frame_id,
                    "move_sha256": pending.move_sha256,
                    "baseline_identity_sha256": self.baseline,
                    "native_state": state,
                    "identity_evidence": self.identity_evidence,
                    "acquisition": boundary.last_acquisition,
                    "image": {
                        "origin": state["native"]["client_screen"][:2],
                        "width": captured.frame.width,
                        "height": captured.frame.height,
                        "stride": captured.frame.width * 3,
                        "byte_count": len(captured.frame.rgb),
                        "sha256": hashlib.sha256(captured.frame.rgb).hexdigest(),
                    },
                }
            )
            acquisition = observation.value["acquisition"]
            if (
                not pending.requested_ns
                <= acquisition["started_ns"]
                <= acquisition["finished_ns"]
                <= clock.check()
            ):
                raise ProtocolError("moving-acquisition-outside-frame")
            for previous in self.observations:
                validate_continuity(previous, observation)
            clock.check()
            return observation

    def accept_frame(  # noqa: PLR0913 - explicit independent acceptance evidence
        self,
        message: Message,
        header: FrameHeader,
        observation: EditObservation,
        transfer: PeerCredit,
        check: Callable[[], object],
        *,
        binary_closed: bool,
    ) -> None:
        """Check the proof, chain and closed stream before allowing another write."""
        with self._guard():
            clock, provider, _ = self._live()
            pending = self.pending
            if (
                pending is None
                or type(message) is not Message
                or message.kind != "frame_accept"
                or type(message.version) is not int
                or message.version != VERSION
                or message.request_id != pending.request_id
            ):
                raise ProtocolError("unexpected-moving-frame-accept")
            accepted = validate_frame_accept(
                message.payload,
                previous_chain=self.chain,
                expected_observation=observation,
                expected_header=header,
                now_ns=clock.check(),
            )
            proof = MovingFrameProof(canonical_json(accepted["proof"]))
            value = proof.value
            if (
                (
                    value["epoch"],
                    value["request_id"],
                    value["frame_id"],
                    value["requested_ns"],
                    value["deadline_ns"],
                    value["move_sha256"],
                )
                != (
                    self.launch.epoch,
                    pending.request_id,
                    pending.frame_id,
                    pending.requested_ns,
                    pending.deadline_ns,
                    pending.move_sha256,
                )
                or binary_closed is not True
                or not transfer.complete
            ):
                raise ProtocolError("moving-frame-accept-mismatch")
            check()
            provider.accept(pending.frame_id, _admission(check))
            check()
            self.proofs.append(proof.digest.hex())
            self.chain = bytes.fromhex(accepted["sequence_sha256"])
            self.observations.append(observation)
            self.frames += 1
            self.pending = None
            self.move_ack = None
            clock.frame_deadline_ns = None

    def finish(self, request: Message, check: Callable[[], object]) -> Message:
        """Recompute provider restoration and final assembly before bounded receipt."""
        with self._guard():
            clock, provider, boundary = self._live()
            self._admit(request, 16)
            if self.frames != MAX_SEQUENCE_FRAMES or self.move_ack is not None:
                raise ProtocolError("incomplete-moving-finish")
            value = validate_finish_request(
                request.payload,
                sequence_started_ns=self.launch.sequence_started_ns,
                sequence_deadline_ns=clock.deadline_ns,
                now_ns=clock.check(),
            )
            clock.frame_deadline_ns = min(
                clock.check() + _FINAL_CORRELATION_NS, value["command_deadline_ns"]
            )
            boundary.set_deadline(clock.frame_deadline_ns)
            result = provider.finish(_admission(check))
            payload = self._finish_payload(result)
            check()
            self.last_id = 16
            self.finished = True
            return Message("response", 16, validate_finish_reply(payload))

    def _finish_payload(self, result: ProviderResult) -> dict[str, object]:
        if len(result.frames) != MAX_SEQUENCE_FRAMES:
            raise ProtocolError("incomplete-moving-provider-result")
        initial, restored = result.frames[0], result.frames[-1]
        provenance = [[source, row] for source, row in result.provenance]
        return {
            "operation": "moving_finished",
            "epoch": self.launch.epoch,
            "accepted_frames": self.frames,
            "sequence_sha256": self.chain.hex(),
            "restoration": {
                "status": "verified",
                "step_id": 7,
                "initial_frame_id": 1,
                "restored_frame_id": 7,
                "state_equal": initial.state == restored.state,
                "root_equal": initial.root_rgb == restored.root_rgb,
                "client_equal": initial.frame.rgb == restored.frame.rgb,
            },
            "assembly": {
                "width": result.image.width,
                "height": result.image.height,
                "byte_count": len(result.image.rgb),
                "sha256": hashlib.sha256(result.image.rgb).hexdigest(),
                "source_frame_ids": [2, 3, 4],
                "row_provenance_sha256": hashlib.sha256(
                    canonical_json(provenance, 16_384)
                ).hexdigest(),
            },
        }


class _MovingEditPipe:
    """Synchronous Win32 I/O confined to the GUI-free owned child process."""

    def __init__(self, launch: MovingEditLaunch, frame_id: int | None = None) -> None:
        self.sequence_clock: SequenceDeadline | None = None
        if frame_id is not None and (
            type(frame_id) is not int or not 1 <= frame_id <= MAX_SEQUENCE_FRAMES
        ):
            raise ProtocolError("invalid-frame-pipe-id")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle: int | None = None
        self.parent_handle: int | None = None
        signatures = {
            "CreateFileW": (
                [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ],
                wintypes.HANDLE,
            ),
            "GetNamedPipeServerProcessId": (
                [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)],
                wintypes.BOOL,
            ),
            "ReadFile": (
                [
                    wintypes.HANDLE,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "WriteFile": (
                [
                    wintypes.HANDLE,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "OpenProcess": (
                [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
                wintypes.HANDLE,
            ),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        handle = self.api.CreateFileW(
            "\\\\.\\pipe\\"
            + (
                launch.pipe_name
                if frame_id is None
                else frame_pipe_name(launch.pipe_name, frame_id)
            ),
            0xC0000000,
            0,
            None,
            3,
            0,
            None,
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        try:
            self._check_parent(launch.parent_pid)
            self.parent_handle = self.api.OpenProcess(0x00100000, 0, launch.parent_pid)
            self._validate_parent_handle()
            self._check_parent(launch.parent_pid)
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:  # noqa: BLE001 - preserve primary failure
                primary.add_note(f"Pipe cleanup failed: {cleanup!r}")
            raise

    def _check_parent(self, expected: int) -> None:
        pid = wintypes.ULONG()
        if not self.api.GetNamedPipeServerProcessId(self.handle, ctypes.byref(pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if pid.value != expected:
            raise ProtocolError("wrong-pipe-server-pid")

    def _validate_parent_handle(self) -> None:
        if self.parent_handle is None or self.parent_handle == 0:
            self.parent_handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        self._parent_alive()

    def _parent_alive(self) -> None:
        wait = self.api.WaitForSingleObject(self.parent_handle, 0)
        if wait == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if wait != _WAIT_TIMEOUT:
            raise ProtocolError("parent-not-alive")

    def _read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            self.checkpoint()
            buffer = ctypes.create_string_buffer(size - len(data))
            count = wintypes.DWORD()
            if not self.api.ReadFile(
                self.handle, buffer, len(buffer), ctypes.byref(count), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.checkpoint()
            if not 0 < count.value <= len(buffer):
                raise ProtocolError("unexpected-peer-eof")
            data.extend(buffer.raw[: count.value])
        return bytes(data)

    def read(self) -> Message:
        """Validate the length before allocating or reading its payload."""
        size = struct.unpack(">I", self._read(HEADER_BYTES))[0]
        if not 0 < size <= MAX_MESSAGE_BYTES:
            raise ProtocolError("oversized")
        return decode(self._read(size))

    def checkpoint(self) -> None:
        """Authoritative ns admission around the pinned-parent liveness read."""
        if self.sequence_clock is not None:
            self.sequence_clock.check()
        self._parent_alive()
        if self.sequence_clock is not None:
            self.sequence_clock.check()

    def write(self, message: Message) -> None:
        """Write one bounded control response, with no deadline reset."""
        self.checkpoint()
        data = encode(message)
        for offset in range(0, len(data), MAX_READ_BYTES):
            self.write_bytes(data[offset : offset + MAX_READ_BYTES])

    def write_bytes(self, data: bytes) -> None:
        """Write a fixed bounded control/header/body chunk, handling partial writes."""
        if type(data) is not bytes or not 0 < len(data) <= MAX_READ_BYTES:
            raise ProtocolError("invalid-peer-write-chunk")
        while data:
            self.checkpoint()
            buffer = ctypes.create_string_buffer(data)
            count = wintypes.DWORD()
            if not self.api.WriteFile(
                self.handle, buffer, len(data), ctypes.byref(count), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not 0 < count.value <= len(data):
                raise ProtocolError("peer-write-failed")
            self.checkpoint()
            data = data[count.value :]

    def close(self) -> None:
        """Close pipe and pinned parent handles once, retaining the first error."""
        handles = (self.handle, self.parent_handle)
        self.handle = self.parent_handle = None
        failure: OSError | None = None
        for handle in handles:
            if handle is not None and not self.api.CloseHandle(handle):
                error = ctypes.WinError(ctypes.get_last_error())
                failure = failure or error
        if failure is not None:
            raise failure


# Evidence transport lives in this already-attested origin. It never selects a
# module, target, path, or arbitrary dispatch from peer data.
_EVIDENCE_HEADER_BYTES = 16
_EVIDENCE_ACK_BYTES = 80
_EVIDENCE_MAX_BYTES = 4096
_EVIDENCE_PIPE_BUFFERS = 65_536
_IO_PENDING = 997
_IO_INCOMPLETE = 996
_OPERATION_ABORTED = 995
_BROKEN_PIPE = 109
_PIPE_NOT_CONNECTED = 233
_PIPE_CONNECTED = 535
_ERROR_NOT_FOUND = 1168
_EVIDENCE_PARENT_PACKETS = 32
_STATUS_INTERVAL_NS = 10_000_000
_MAX_SID_CHARS = 256
_EVIDENCE_SOURCE_CHUNK_BYTES = 65_536
_EVIDENCE_SOURCE_MAX_BYTES = 262_144
_FILE_FLAG_OVERLAPPED = 0x40000000
# Failed cancellation must retain native buffers, not free memory the OS owns.
# At most the two endpoints' read/write operations can reach this failure path.
_unretired_evidence_io: list[object] = []


class _EvidenceOverlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", wintypes.HANDLE),
    ]


class _EvidenceSecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int32),
    ]


def _evidence_api() -> Any:  # noqa: ANN401 - explicitly bound Win32 ABI
    if _unretired_evidence_io:
        raise ProtocolError("evidence-unretired-io")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, ptr, dword = wintypes.HANDLE, ctypes.c_void_p, ctypes.c_uint32
    signatures = {
        "CreateFileW": (
            [wintypes.LPCWSTR, dword, dword, ptr, dword, dword, handle],
            handle,
        ),
        "CreateNamedPipeW": (
            [wintypes.LPCWSTR, dword, dword, dword, dword, dword, dword, ptr],
            handle,
        ),
        "CreateEventW": ([ptr, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], handle),
        "ConnectNamedPipe": ([handle, ptr], wintypes.BOOL),
        "ReadFile": ([handle, ptr, dword, ptr, ptr], wintypes.BOOL),
        "WriteFile": ([handle, ptr, dword, ptr, ptr], wintypes.BOOL),
        "GetOverlappedResult": ([handle, ptr, ptr, wintypes.BOOL], wintypes.BOOL),
        "CancelIoEx": ([handle, ptr], wintypes.BOOL),
        "GetNamedPipeServerProcessId": ([handle, ptr], wintypes.BOOL),
        "GetNamedPipeClientProcessId": ([handle, ptr], wintypes.BOOL),
        "OpenProcess": ([dword, wintypes.BOOL, dword], handle),
        "GetCurrentProcess": ([], handle),
        "GetProcessTimes": ([handle, ptr, ptr, ptr, ptr], wintypes.BOOL),
        "GetExitCodeProcess": ([handle, ptr], wintypes.BOOL),
        "GetProcessId": ([handle], dword),
        "QueryFullProcessImageNameW": (
            [handle, dword, wintypes.LPWSTR, ptr],
            wintypes.BOOL,
        ),
        "WaitForSingleObject": ([handle, dword], dword),
        "CloseHandle": ([handle], wintypes.BOOL),
        "LocalFree": ([ptr], ptr),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def _win_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def _raise_win_error() -> Never:
    raise _win_error()


def _valid_evidence_handle(handle: object) -> bool:
    return handle not in (None, 0, ctypes.c_void_p(-1).value)


def _base_executable() -> str:
    """Use only the base interpreter already verified by the fixed bootstrap."""
    value = getattr(sys, "_base_executable", None)
    if type(value) is not str or not value:
        raise ProtocolError("evidence-missing-base-executable")
    return str(Path(value).resolve())


def _process_creation(api: Any, handle: int) -> int:  # noqa: ANN401 - Win32 ABI
    times = [ctypes.c_uint64() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        _raise_win_error()
    return times[0].value


def _process_identity(api: Any, handle: int) -> tuple[int, str]:  # noqa: ANN401
    created = _process_creation(api, handle)
    size = ctypes.c_uint32(2048)
    image = ctypes.create_unicode_buffer(size.value)
    if not api.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        _raise_win_error()
    if not 0 < size.value < len(image):
        raise ProtocolError("evidence-image-bound")
    return created, cast("str", image.value)


class _EvidenceProcess:
    """Retain one exact process handle; never reopen a reused PID."""

    def __init__(self, api: Any, pid: int, created: int, executable: str) -> None:  # noqa: ANN401
        self.api = api
        self.pid, self.created, self.executable = pid, created, executable
        self.handle = api.OpenProcess(0x00101000, 0, pid)
        if not _valid_evidence_handle(self.handle):
            self.handle = None
            _raise_win_error()
        try:
            self.check()
        except BaseException:
            _close_preserving(self)
            raise

    def check(self, *, allow_exit: bool = False) -> None:
        """Check retained identity; only an acknowledged final permits clean exit."""
        if self.handle is None:
            raise ProtocolError("evidence-process-closed")
        if self.api.GetProcessId(self.handle) != self.pid:
            raise ProtocolError("evidence-retained-pid-mismatch")
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result == _WAIT_FAILED:
            _raise_win_error()
        if result != _WAIT_TIMEOUT:
            if not allow_exit or result != 0:
                raise ProtocolError("evidence-process-not-live")
            code = ctypes.c_uint32()
            if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(code)):
                _raise_win_error()
            if code.value != 0:
                raise ProtocolError("evidence-peer-exit-failed")
            if _process_creation(self.api, self.handle) != self.created:
                raise ProtocolError("evidence-process-identity-mismatch")
            # The image was pinned while live. Querying its path again after
            # exit is neither needed nor universally reliable. Keep this handle.
            return
        created, image = _process_identity(self.api, self.handle)
        if created != self.created or os.path.normcase(image) != os.path.normcase(
            self.executable
        ):
            raise ProtocolError("evidence-process-identity-mismatch")

    def close(self) -> None:
        """Attempt to release the pinned identity exactly once."""
        handle, self.handle = self.handle, None
        if handle is not None and not self.api.CloseHandle(handle):
            _raise_win_error()


class _EvidenceIO:
    """Keep one overlapped buffer/event alive until completion or cancellation."""

    def __init__(self, api: Any, buffer: Any, *, connect: bool = False) -> None:  # noqa: ANN401
        self.buffer = buffer
        self.connect = connect
        self.overlapped = _EvidenceOverlapped()
        self.overlapped.hEvent = api.CreateEventW(None, 1, 0, None)
        if not _valid_evidence_handle(self.overlapped.hEvent):
            _raise_win_error()
        self.immediate: int | None = None
        self.pending = False


class _EvidenceEndpoint:
    """One read and one write, always overlapped and never waiting in ReadFile."""

    def __init__(self, api: Any, handle: int) -> None:  # noqa: ANN401
        self.api, self.handle = api, handle
        self.reading: _EvidenceIO | None = None
        self.writing: _EvidenceIO | None = None
        self.write_remaining = b""
        self.closed = False
        self.close_deadline_ns = 0

    def _finish(self, operation: _EvidenceIO) -> int | None:
        if operation.immediate is not None:
            return operation.immediate
        count = ctypes.c_uint32()
        if self.api.GetOverlappedResult(
            self.handle, ctypes.byref(operation.overlapped), ctypes.byref(count), 0
        ):
            return count.value
        error = ctypes.get_last_error()
        if error == _IO_INCOMPLETE:
            return None
        if error in (_BROKEN_PIPE, _PIPE_NOT_CONNECTED):
            return 0
        raise ctypes.WinError(error)

    def _release(self, operation: _EvidenceIO) -> None:
        event, operation.overlapped.hEvent = operation.overlapped.hEvent, None
        if event is not None and not self.api.CloseHandle(event):
            _raise_win_error()

    def start_connect(self) -> None:
        """Start the only allowed server connection, without blocking."""
        if self.reading is not None or self.closed:
            raise ProtocolError("evidence-read-outstanding")
        operation = _EvidenceIO(self.api, None, connect=True)
        self.reading = operation
        if self.api.ConnectNamedPipe(self.handle, ctypes.byref(operation.overlapped)):
            operation.immediate = 1
        else:
            error = ctypes.get_last_error()
            if error == _PIPE_CONNECTED:
                operation.immediate = 1
            elif error != _IO_PENDING:
                raise ctypes.WinError(error)
            else:
                operation.pending = True

    def poll_connect(self) -> bool:
        """Poll connection completion; PID pinning still precedes any read."""
        operation = self.reading
        if operation is None or not operation.connect:
            raise ProtocolError("evidence-no-connect")
        result = self._finish(operation)
        if result is None:
            return False
        self.reading = None
        self._release(operation)
        return True

    def start_read(self, size: int) -> None:
        """Allocate at most one bounded native read buffer."""
        if type(size) is not int or not 0 < size <= _EVIDENCE_MAX_BYTES:
            raise ProtocolError("evidence-read-bound")
        if self.reading is not None or self.closed:
            raise ProtocolError("evidence-read-outstanding")
        operation = _EvidenceIO(self.api, ctypes.create_string_buffer(size))
        self.reading = operation
        count = ctypes.c_uint32()
        if self.api.ReadFile(
            self.handle,
            operation.buffer,
            size,
            ctypes.byref(count),
            ctypes.byref(operation.overlapped),
        ):
            operation.immediate = count.value
        else:
            error = ctypes.get_last_error()
            if error in (_BROKEN_PIPE, _PIPE_NOT_CONNECTED):
                operation.immediate = 0
            elif error != _IO_PENDING:
                raise ctypes.WinError(error)
            else:
                operation.pending = True

    def poll_read(self) -> bytes | None:
        """Return only completed bytes; None means watchdog work can continue."""
        operation = self.reading
        if operation is None or operation.connect:
            raise ProtocolError("evidence-no-read")
        count = self._finish(operation)
        if count is None:
            return None
        self.reading = None
        try:
            if not 0 <= count <= len(operation.buffer):
                raise ProtocolError("evidence-read-count")
            return cast("bytes", operation.buffer.raw[:count])
        finally:
            self._release(operation)

    def start_write(self, data: bytes) -> None:
        """Start a bounded immutable record with no queue or future-record credit."""
        if type(data) is not bytes or not 0 < len(data) <= _EVIDENCE_MAX_BYTES:
            raise ProtocolError("evidence-write-bound")
        if self.writing is not None or self.closed:
            raise ProtocolError("evidence-write-outstanding")
        self.write_remaining = data
        self._write_part()

    def _write_part(self) -> None:
        operation = _EvidenceIO(
            self.api, ctypes.create_string_buffer(self.write_remaining)
        )
        self.writing = operation
        count = ctypes.c_uint32()
        if self.api.WriteFile(
            self.handle,
            operation.buffer,
            len(self.write_remaining),
            ctypes.byref(count),
            ctypes.byref(operation.overlapped),
        ):
            operation.immediate = count.value
        else:
            error = ctypes.get_last_error()
            if error != _IO_PENDING:
                raise ctypes.WinError(error)
            operation.pending = True

    def poll_write(self) -> bool:
        """Consume partial writes without resetting the original operation clock."""
        operation = self.writing
        if operation is None:
            return True
        count = self._finish(operation)
        if count is None:
            return False
        self.writing = None
        try:
            if not 0 < count <= len(self.write_remaining):
                raise ProtocolError("evidence-write-count")
            self.write_remaining = self.write_remaining[count:]
        finally:
            self._release(operation)
        if self.write_remaining:
            self._write_part()
            return False
        return True

    def _cancel(self, operation: _EvidenceIO) -> None:
        if not operation.pending:
            return
        if (
            not self.api.CancelIoEx(self.handle, ctypes.byref(operation.overlapped))
            and ctypes.get_last_error() != _ERROR_NOT_FOUND
        ):
            _raise_win_error()
        count = ctypes.c_uint32()
        while True:
            if self.api.GetOverlappedResult(
                self.handle, ctypes.byref(operation.overlapped), ctypes.byref(count), 0
            ):
                return
            error = ctypes.get_last_error()
            if error in (_OPERATION_ABORTED, _BROKEN_PIPE, _PIPE_NOT_CONNECTED):
                return
            if error != _IO_INCOMPLETE:
                raise ctypes.WinError(error)
            if time.monotonic_ns() >= self.close_deadline_ns:
                raise ProtocolError("evidence-cancel-not-retired")
            if (
                self.api.WaitForSingleObject(operation.overlapped.hEvent, 1)
                == _WAIT_FAILED
            ):
                _raise_win_error()

    def close(self) -> None:
        """Cancel pending I/O, close handles once, and preserve the first failure."""
        if self.closed:
            return
        self.closed = True
        pending = [item for item in (self.reading, self.writing) if item is not None]
        self.reading = self.writing = None
        failure: BaseException | None = None
        unreaped = []
        for operation in pending:
            try:
                self._cancel(operation)
            except BaseException as error:  # noqa: BLE001 - all handles still close
                unreaped.append(operation)
                failure = failure or error
        handle, self.handle = self.handle, None
        if handle is not None and not self.api.CloseHandle(handle):
            failure = failure or _win_error()
        for operation in pending:
            if operation in unreaped:
                _unretired_evidence_io.append(operation)
                continue
            try:
                self._release(operation)
            except BaseException as error:  # noqa: BLE001 - preserve first error
                failure = failure or error
        if failure is not None:
            raise failure


def _evidence_pid(api: Any, handle: int | None, *, server: bool) -> int:  # noqa: ANN401
    value = ctypes.c_uint32()
    query = (
        api.GetNamedPipeServerProcessId if server else api.GetNamedPipeClientProcessId
    )
    if not query(handle, ctypes.byref(value)):
        _raise_win_error()
    return value.value


class _EvidenceClient:
    """Authenticated fixed reader connection, usable only on a non-GUI worker."""

    def __init__(
        self,
        launch: MovingEditLaunch,
        *,
        parent: bool,
        reader_executable: str,
        check: Callable[[], object],
    ) -> None:
        self.launch, self.check = launch, check
        self.api = _evidence_api()
        self.endpoint: _EvidenceEndpoint | None = None
        self.reader: _EvidenceProcess | None = None
        self.failed = False
        try:
            self._check(launch.sequence_deadline_ns)
            self.reader = _EvidenceProcess(
                self.api,
                launch.evidence_reader_pid,
                launch.evidence_reader_created_100ns,
                reader_executable,
            )
            handle = self.api.CreateFileW(
                "\\\\.\\pipe\\"
                + evidence_pipe_name(launch.evidence_token, parent=parent),
                0xC0000000,
                0,
                None,
                3,
                _FILE_FLAG_OVERLAPPED,
                None,
            )
            if not _valid_evidence_handle(handle):
                _raise_win_error()
            self.endpoint = _EvidenceEndpoint(self.api, handle)
            self.endpoint.close_deadline_ns = launch.sequence_deadline_ns
            if (
                _evidence_pid(self.api, handle, server=True)
                != launch.evidence_reader_pid
            ):
                _reject("evidence-wrong-server-pid")
            self.reader.check()
            if (
                _evidence_pid(self.api, handle, server=True)
                != launch.evidence_reader_pid
            ):
                _reject("evidence-server-changed")
            self._check(launch.sequence_deadline_ns)
        except BaseException:
            self.failed = True
            _close_preserving(self)
            raise

    def _check(self, deadline_ns: int) -> None:
        if self.failed:
            raise ProtocolError("evidence-client-failed")
        self.check()
        if time.monotonic_ns() >= min(deadline_ns, self.launch.sequence_deadline_ns):
            raise ProtocolError("evidence-client-expired")
        if self.reader is not None:
            self.reader.check()
        self.check()
        if time.monotonic_ns() >= min(deadline_ns, self.launch.sequence_deadline_ns):
            raise ProtocolError("evidence-client-expired")

    def _yield(self) -> None:
        # No synchronous pipe operation or infinite event wait can hide a call
        # watchdog. A 1ms event wait is inside the original operation allowance.
        if self.endpoint is not None:
            operation = self.endpoint.reading or self.endpoint.writing
            if operation is not None:
                result = self.api.WaitForSingleObject(operation.overlapped.hEvent, 1)
                if result == _WAIT_FAILED:
                    _raise_win_error()

    def exchange(self, frame: bytes, deadline_ns: int) -> bytes:
        """Write one record and receive exactly its fixed ACK, never future credit."""
        try:
            endpoint = self.endpoint
            if endpoint is None:
                _reject("evidence-client-closed")
            self._check(deadline_ns)
            endpoint.close_deadline_ns = min(
                deadline_ns, self.launch.sequence_deadline_ns
            )
            endpoint.start_write(frame)
            while not endpoint.poll_write():
                self._check(deadline_ns)
                self._yield()
            answer = bytearray()
            while len(answer) < _EVIDENCE_ACK_BYTES:
                self._check(deadline_ns)
                endpoint.start_read(_EVIDENCE_ACK_BYTES - len(answer))
                data = endpoint.poll_read()
                while data is None:
                    self._check(deadline_ns)
                    self._yield()
                    data = endpoint.poll_read()
                if not data:
                    _reject("evidence-ack-eof")
                answer.extend(data)
            self._check(deadline_ns)
            return bytes(answer)
        except BaseException:
            self.failed = True
            raise

    def close(self) -> None:
        """Close the stream and retained reader identity even after admission loss."""
        failure: BaseException | None = None
        for resource in (self.endpoint, self.reader):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:  # noqa: BLE001 - attempt every owned cleanup
                    failure = failure or error
        self.endpoint = self.reader = None
        if failure is not None:
            raise failure


class EvidenceParentClient(_EvidenceClient):
    """Fixed independently authenticated context path, one packet/ACK at a time."""

    def __init__(
        self,
        launch: MovingEditLaunch,
        *,
        reader_executable: str,
        check: Callable[[], object],
    ) -> None:
        self.producer = EvidenceProducer(
            launch.evidence_token, launch.epoch, parent=True
        )
        super().__init__(
            launch, parent=True, reader_executable=reader_executable, check=check
        )

    def send(self, packet: dict[str, Any], *, deadline_ns: int) -> None:
        """Publish the actual original context/completion before command progress."""
        try:
            frame = self.producer.prepare(packet, time.monotonic_ns(), deadline_ns)
            self.producer.acknowledge(
                self.exchange(frame, deadline_ns), time.monotonic_ns()
            )
            self._check(deadline_ns)
        except BaseException:
            self.failed = True
            raise

    def finish(self, deadline_ns: int) -> None:
        """Keep the parent endpoint live until the reader's audited final EOF."""

        def admit() -> None:
            self.check()
            if time.monotonic_ns() >= min(
                deadline_ns, self.launch.sequence_deadline_ns
            ):
                _reject("evidence-final-eof-expired")

        try:
            if (
                self.producer.count != _EVIDENCE_PARENT_PACKETS
                or self.producer.pending is not None
                or self.endpoint is None
            ):
                _reject("evidence-parent-incomplete")
            # The reader may retire immediately after EOF. Its retained image
            # was authenticated before all records; final job/status remains an
            # independent outer requirement, not inferred from EOF alone.
            admit()
            self.endpoint.close_deadline_ns = deadline_ns
            self.endpoint.start_read(1)
            while True:
                admit()
                data = self.endpoint.poll_read()
                admit()
                if data is not None:
                    if data:
                        _reject("evidence-parent-trailing-bytes")
                    break
                self._yield()
            self.close()
            admit()
        except BaseException:
            self.failed = True
            raise


ParentEvidenceClient = EvidenceParentClient


class _Journal:
    """Synchronous visibility ACK admission, independent of pixels/control credit."""

    def __init__(self, launch: MovingEditLaunch, check: Callable[[], object]) -> None:
        self.launch, self.check = launch, check
        self.producer = EvidenceProducer(launch.evidence_token, launch.epoch)
        self.client = _EvidenceClient(
            launch,
            parent=False,
            reader_executable=_base_executable(),
            check=check,
        )
        self.context: dict[str, Any] | None = None
        self.deadline_ns = min(
            launch.sequence_started_ns + 3_000_000_000, launch.sequence_deadline_ns
        )
        self.failed = False
        try:
            created, _ = _process_identity(
                self.client.api, self.client.api.GetCurrentProcess()
            )
            self.emit(
                make_evidence_hello(launch, os.getpid(), created),
                timestamp_ns=time.monotonic_ns(),
            )
        except BaseException:
            _close_preserving(self)
            raise

    def __call__(self, value: dict[str, Any]) -> None:
        """Reject missing call stamps; non-call events stamp before serialization."""
        if value.get("event") in ("call_start", "call_end"):
            self.failed = True
            raise ProtocolError("evidence-original-call-stamp-required")
        self.emit(value, timestamp_ns=time.monotonic_ns())

    def emit(self, value: dict[str, Any], *, timestamp_ns: int) -> None:
        """Await the exact persisted record ACK and recheck the same deadline."""
        if self.failed:
            raise ProtocolError("moving-journal-failed")
        deadline = min(
            self.deadline_ns, value.get("effective_deadline_ns", self.deadline_ns)
        )
        try:
            frame = self.producer.prepare(value, timestamp_ns, deadline)
            answer = self.client.exchange(frame, deadline)
            self.producer.acknowledge(answer, time.monotonic_ns())
            self.client._check(deadline)
        except BaseException:
            self.failed = True
            raise

    def phase_enter(self, request: Message) -> None:
        """Bind a native phase to the exact independently delivered command digest."""
        operation = {
            "moving_begin": "begin",
            "move_next": "move",
            "observe": "observe",
            "moving_finish": "finish",
        }.get(request.payload.get("operation"))
        if operation is None:
            raise ProtocolError("evidence-unknown-phase")
        step = request.payload.get("step_id", 0)
        self.context = {
            "context_id": request.request_id,
            "operation": operation,
            "request_id": request.request_id,
            "step_id": step,
            "command_sha256": hashlib.sha256(
                encode(request)[HEADER_BYTES:]
            ).hexdigest(),
        }
        if operation in ("begin", "finish"):
            self.deadline_ns = cast("int", request.payload["command_deadline_ns"])
        else:
            self.deadline_ns = cast("int", request.payload["deadline_ns"])
        if operation == "finish":
            self.cleanup_deadline_ns = min(
                cast("int", request.payload["command_started_ns"]) + 5_000_000_000,
                self.launch.sequence_deadline_ns,
            )
        self.emit(
            {"event": "phase_enter", **self.context}, timestamp_ns=time.monotonic_ns()
        )

    def finish_response_sent(self, *, timestamp_ns: int) -> None:
        """Acknowledge the actual response-write return before any native cleanup."""
        if self.context is None or self.context["operation"] != "finish":
            raise ProtocolError("evidence-finish-response-context")
        self.emit(
            {"event": "finish_response_sent", **self.context}, timestamp_ns=timestamp_ns
        )
        self.deadline_ns = self.cleanup_deadline_ns

    def phase_end(self) -> None:
        """Close the peer observation; parent completion remains independently due."""
        if self.context is None:
            raise ProtocolError("evidence-no-phase")
        stamp = time.monotonic_ns()
        self.emit(
            {"event": "phase_end", **self.context, "completed_ns": stamp},
            timestamp_ns=stamp,
        )

    def failure(self, reason: str, *, timestamp_ns: int) -> None:
        """Preserve the original failure timestamp, never an evidence receipt stamp."""
        if self.context is None:
            raise ProtocolError("evidence-no-phase")
        self.emit(
            {
                "event": "failure",
                "context_id": self.context["context_id"],
                "reason": reason,
            },
            timestamp_ns=timestamp_ns,
        )

    def restore_enter(self, failure_ns: int, *, timestamp_ns: int) -> None:
        """Record provider-approved restoration inside the original one-shot reserve."""
        if self.context is None:
            raise ProtocolError("evidence-no-phase")
        self.deadline_ns = min(
            self.launch.sequence_deadline_ns, failure_ns + 8_000_000_000
        )
        self.emit(
            {
                "event": "failure_restore_enter",
                "context_id": self.context["context_id"],
                "sequence_started_ns": self.launch.sequence_started_ns,
                "sequence_deadline_ns": self.launch.sequence_deadline_ns,
                "failure_ns": failure_ns,
            },
            timestamp_ns=timestamp_ns,
        )

    def restore_phase(
        self, phase_id: int, operation: str, *, timestamp_ns: int
    ) -> None:
        """Stamp each ordered guard/write/capture/final entry before delivery."""
        self.emit(
            {
                "event": "failure_restore_phase",
                "phase_id": phase_id,
                "operation": operation,
            },
            timestamp_ns=timestamp_ns,
        )

    def finish(self) -> None:
        """Final exact transcript summary is acknowledged before actual pipe EOF."""
        self.emit(self.producer.final_event(), timestamp_ns=time.monotonic_ns())
        self.close()

    def close(self) -> None:
        """Close the actual evidence endpoint; no success is inferred from closure."""
        self.client.close()


def _local_free_preserving(api: Any, value: object) -> None:  # noqa: ANN401 - Win32 ABI
    primary = sys.exception()
    if api.LocalFree(value):
        failure = _win_error()
        if primary is None:
            raise failure
        primary.add_note(f"LocalFree failed: {failure!r}")


def _current_user_security(  # noqa: C901, PLR0912 - exact token ownership and cleanup
    api: Any,  # noqa: ANN401 - bound Win32 ABI
) -> tuple[_EvidenceSecurityAttributes, ctypes.c_void_p]:
    """Build a protected DACL containing only the process token's actual user SID."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    for name, arguments in {
        "OpenProcessToken": [wintypes.HANDLE, ctypes.c_uint32, pointer],
        "GetTokenInformation": [
            wintypes.HANDLE,
            ctypes.c_uint32,
            pointer,
            ctypes.c_uint32,
            pointer,
        ],
        "ConvertSidToStringSidW": [pointer, pointer],
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": [
            wintypes.LPCWSTR,
            ctypes.c_uint32,
            pointer,
            pointer,
        ],
    }.items():
        function = getattr(advapi, name)
        function.argtypes, function.restype = arguments, wintypes.BOOL
    token = wintypes.HANDLE()
    sid_text = ctypes.c_wchar_p()
    descriptor = ctypes.c_void_p()
    if not advapi.OpenProcessToken(api.GetCurrentProcess(), 8, ctypes.byref(token)):
        _raise_win_error()
    failure: BaseException | None = None
    try:
        size = ctypes.c_uint32()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= _EVIDENCE_MAX_BYTES:
            _reject("evidence-token-bound")
        info = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token, 1, info, size.value, ctypes.byref(size)
        ):
            _raise_win_error()
        sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            _raise_win_error()
        if (
            sid_text.value is None
            or not sid_text.value
            or len(sid_text.value) > _MAX_SID_CHARS
        ):
            _reject("evidence-sid-bound")
        sddl = f"D:P(A;;GA;;;{sid_text.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            _raise_win_error()
    except BaseException as error:  # noqa: BLE001 - all native owners must unwind
        failure = error
    if sid_text and api.LocalFree(sid_text):
        error = _win_error()
        if failure is None:
            failure = error
        else:
            failure.add_note(f"SID cleanup failed: {error!r}")
    if not api.CloseHandle(token):
        error = _win_error()
        if failure is None:
            failure = error
        else:
            failure.add_note(f"Token cleanup failed: {error!r}")
    if failure is not None:
        if (
            descriptor.value is not None
            and descriptor.value != 0
            and api.LocalFree(descriptor)
        ):
            failure.add_note(f"Security descriptor cleanup failed: {_win_error()!r}")
        raise failure
    attributes = _EvidenceSecurityAttributes(
        ctypes.sizeof(_EvidenceSecurityAttributes), descriptor, 0
    )
    return attributes, descriptor


def _evidence_server(api: Any, token: str, *, parent: bool) -> _EvidenceEndpoint:  # noqa: ANN401
    security, descriptor = _current_user_security(api)
    endpoint: _EvidenceEndpoint | None = None
    try:
        try:
            handle = api.CreateNamedPipeW(
                "\\\\.\\pipe\\" + evidence_pipe_name(token, parent=parent),
                3 | _FILE_FLAG_OVERLAPPED | 0x00080000,
                0x00000008,
                1,
                _EVIDENCE_PIPE_BUFFERS,
                _EVIDENCE_PIPE_BUFFERS,
                0,
                ctypes.byref(security),
            )
            if not _valid_evidence_handle(handle):
                _raise_win_error()
            endpoint = _EvidenceEndpoint(api, handle)
        finally:
            _local_free_preserving(api, descriptor)
    except BaseException:
        if endpoint is not None:
            _close_preserving(endpoint)
        raise
    return endpoint


def _write_evidence_packet(name: str, packet: dict[str, Any]) -> None:
    if name not in ("moving-evidence-status.json", "moving-evidence-final.json"):
        raise ProtocolError("evidence-artifact-name")
    # Cwd is fixed by the surviving outer launcher. No path arrives over either
    # endpoint. Atomic replacement is confined to these two fixed artifacts.
    payload = canonical_json(packet, 16_384)
    path = Path.cwd() / name
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("wb") as output:
        if output.write(payload) != len(payload):
            raise ProtocolError("evidence-status-short-write")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class _ReaderConnection:
    """One bounded frame parser and one outstanding ACK per fixed endpoint."""

    def __init__(self, endpoint: _EvidenceEndpoint, *, parent: bool) -> None:
        self.endpoint, self.parent = endpoint, parent
        self.connected = False
        self.pinned: _EvidenceProcess | None = None
        self.data = bytearray()
        self.expected_bytes = _EVIDENCE_HEADER_BYTES
        self.eof = False
        self.ack_pending = False
        self.ack_deadline_ns = 0
        self.ack_is_hello = False
        self.hello_ack_written = False
        self.awaiting_acceptance = False
        try:
            self.endpoint.start_connect()
        except BaseException:
            _close_preserving(self)
            raise

    def pin(
        self, expected: EvidenceIdentity, state: EvidenceReaderState, now_ns: int
    ) -> None:
        """Compare actual kernel client identity against the outer's one-shot proof."""
        if not self.connected:
            self.connected = self.endpoint.poll_connect()
        if not self.connected or self.pinned is not None:
            return
        prefix = "parent" if self.parent else "peer"
        pid = getattr(expected, prefix + "_pid")
        if _evidence_pid(self.endpoint.api, self.endpoint.handle, server=False) != pid:
            raise ProtocolError("evidence-wrong-client-pid")
        self.pinned = _EvidenceProcess(
            self.endpoint.api,
            pid,
            getattr(expected, prefix + "_created_100ns"),
            getattr(expected, prefix + "_executable"),
        )
        if _evidence_pid(self.endpoint.api, self.endpoint.handle, server=False) != pid:
            raise ProtocolError("evidence-client-changed")
        bind = state.bind_parent if self.parent else state.bind_peer
        bind(pid, self.pinned.created, self.pinned.executable, now_ns)

    def poll(self, *, allow_exit: bool = False) -> bytes | None:  # noqa: C901, PLR0911, PLR0912 - bounded parser states
        """Read exactly one frame without consuming or retaining a following one."""
        if self.eof or self.pinned is None:
            return None
        self.pinned.check(allow_exit=allow_exit)
        if self.ack_pending:
            if time.monotonic_ns() >= self.ack_deadline_ns:
                raise ProtocolError("evidence-ack-write-expired")
            if not self.endpoint.poll_write():
                return None
            if time.monotonic_ns() >= self.ack_deadline_ns:
                raise ProtocolError("evidence-ack-write-expired")
            self.ack_pending = False
            if self.ack_is_hello:
                self.hello_ack_written = True
        if self.awaiting_acceptance:
            return None
        if self.endpoint.reading is None:
            self.endpoint.start_read(self.expected_bytes - len(self.data))
        part = self.endpoint.poll_read()
        if part is None:
            return None
        if not part:
            self.eof = True
            if self.data:
                raise ProtocolError("evidence-truncated-frame")
            return None
        self.data.extend(part)
        if len(self.data) < self.expected_bytes:
            return None
        if self.expected_bytes == _EVIDENCE_HEADER_BYTES:
            size = struct.unpack(">I", self.data[:4])[0]
            maximum = 1024 if self.parent else _EVIDENCE_MAX_BYTES
            if not 0 < size <= maximum - _EVIDENCE_HEADER_BYTES:
                raise ProtocolError("evidence-frame-bound")
            self.expected_bytes += size
            return None
        frame = bytes(self.data)
        self.awaiting_acceptance = True
        self.data.clear()
        self.expected_bytes = _EVIDENCE_HEADER_BYTES
        return frame

    def acknowledge(
        self, answer: bytes, *, deadline_ns: int, hello: bool = False
    ) -> None:
        """Grant exactly one accepted record, never queue another ACK."""
        if self.ack_pending or len(answer) != _EVIDENCE_ACK_BYTES:
            raise ProtocolError("evidence-ack-outstanding")
        self.ack_deadline_ns = deadline_ns
        self.ack_is_hello = hello
        self.endpoint.close_deadline_ns = deadline_ns
        self.endpoint.start_write(answer)
        self.awaiting_acceptance = False
        self.ack_pending = True

    def close(self) -> None:
        """Attempt endpoint cancellation and identity release independently."""
        failure: BaseException | None = None
        for resource in (self.endpoint, self.pinned):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:  # noqa: BLE001 - attempt every owned cleanup
                    failure = failure or error
        if failure is not None:
            raise failure


def _evidence_source_digest(deadline_ns: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with Path(__file__).open("rb") as source:
        while chunk := source.read(
            min(_EVIDENCE_SOURCE_CHUNK_BYTES, _EVIDENCE_SOURCE_MAX_BYTES - total + 1)
        ):
            total += len(chunk)
            if total > _EVIDENCE_SOURCE_MAX_BYTES:
                raise ProtocolError("evidence-source-size-bound")
            if time.monotonic_ns() >= deadline_ns:
                raise ProtocolError("evidence-outer-deadline")
            digest.update(chunk)
    return digest.hexdigest()


class _EvidenceReader:
    """Independent reader state and append-only storage, with no target/provider."""

    def __init__(self, launch: EvidenceReaderLaunch) -> None:
        self.launch = launch
        if time.monotonic_ns() >= launch.absolute_deadline_ns:
            raise ProtocolError("evidence-outer-deadline")
        self.api = _evidence_api()
        self.outer: _EvidenceProcess | None = None
        self.peer: _ReaderConnection | None = None
        self.parent: _ReaderConnection | None = None
        self.expected: EvidenceIdentity | None = None
        self.state: EvidenceReaderState | None = None
        self.raw: Any = None
        self.parent_raw: Any = None
        self.audit: dict[str, Any] | None = None
        self.stage = "starting"
        self.source_sha256 = _evidence_source_digest(launch.absolute_deadline_ns)
        self.expected_fingerprint: tuple[int, int, int] | None = None
        self.last_status_ns = 0
        self.created, self.executable = _process_identity(
            self.api, self.api.GetCurrentProcess()
        )

    @property
    def authentication_ack_written(self) -> bool:
        """Distinguish validated hello from both pins and actual complete ACK write."""
        return (
            self.peer is not None
            and self.peer.pinned is not None
            and self.peer.hello_ack_written
            and self.parent is not None
            and self.parent.pinned is not None
        )

    def packet(self, *, failed: str = "") -> dict[str, Any]:
        """Return bounded independent progress and pending-call state for the outer."""
        return {
            "schema": 1,
            "session": self.launch.token,
            "pid": os.getpid(),
            "created_100ns": self.created,
            "executable": self.executable,
            "source_sha256": self.source_sha256,
            "stage": self.stage,
            "authentication_ack_written": self.authentication_ack_written,
            "progress_ns": time.monotonic_ns(),
            "failure": failed,
            "evidence": None if self.state is None else self.state.status,
            "audit": self.audit,
        }

    def publish(self, *, force: bool = False) -> None:
        now = time.monotonic_ns()
        if force or now - self.last_status_ns >= _STATUS_INTERVAL_NS:
            _write_evidence_packet("moving-evidence-status.json", self.packet())
            self.last_status_ns = now

    def check(self) -> int:
        """Watch native-call/phase deadlines even while all pipe reads are pending."""
        now = time.monotonic_ns()
        if now >= self.launch.absolute_deadline_ns:
            raise ProtocolError("evidence-outer-deadline")
        if self.outer is not None:
            self.outer.check()
            # Native identity queries consume the same original allowance.
            now = time.monotonic_ns()
            if now >= self.launch.absolute_deadline_ns:
                raise ProtocolError("evidence-outer-deadline")
        if self.state is not None:
            self.state.check(now)
        return now

    def _expected(self) -> None:
        path = Path.cwd() / "moving-evidence-expected.json"
        if self.expected is not None:
            stat = path.stat()
            if (
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
            ) != self.expected_fingerprint:
                raise ProtocolError("evidence-expected-identity-changed")
            return
        try:
            with path.open("rb") as incoming:
                payload = incoming.read(16_385)
                stat = os.fstat(incoming.fileno())
                self.expected_fingerprint = (
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                )
        except FileNotFoundError:
            return
        expected = EvidenceIdentity.from_value(parse_json(payload, 16_384))
        if (
            expected.session != self.launch.token
            or expected.sequence_deadline_ns > self.launch.absolute_deadline_ns
        ):
            raise ProtocolError("evidence-expected-identity-mismatch")
        self.expected = expected
        for connection in (self.peer, self.parent):
            if connection is not None:
                connection.endpoint.close_deadline_ns = expected.sequence_deadline_ns
        self.state = EvidenceReaderState(expected)

    def _peer_record(self, frame: bytes, now: int) -> None:
        if self.state is None or self.peer is None or self.raw is None:
            raise ProtocolError("evidence-reader-not-bound")
        self.state.prepare_peer(frame, now)
        if self.raw.write(frame) != len(frame):
            raise ProtocolError("evidence-raw-short-write")
        self.raw.flush()
        # The call_start and its pending deadline are visible before the ACK.
        self.publish(force=True)
        deadline = self.state.ack_deadline_ns
        self.peer.acknowledge(
            self.state.acknowledge_persisted(self.check()),
            deadline_ns=deadline,
            hello=self.state.count == 1,
        )

    def _parent_record(self, frame: bytes, now: int) -> None:
        if self.state is None or self.parent is None or self.parent_raw is None:
            raise ProtocolError("evidence-reader-not-bound")
        self.state.prepare_parent(frame, now)
        # The strict state limits accepted input to 32 complete <=1024-byte
        # frames (32768 bytes total). Persist once even when completion ACK is
        # held for the peer phase end. Never discard a failure prefix.
        if self.parent_raw.write(frame) != len(frame):
            raise ProtocolError("evidence-parent-raw-short-write")
        self.parent_raw.flush()
        self.check()
        self.publish(force=True)
        self._parent_ack()

    def _parent_ack(self) -> None:
        if self.state is None or self.parent is None or not self.state.parent_ack_ready:
            return
        deadline = self.state.ack_deadline_ns
        answer = self.state.acknowledge_parent(self.check())
        if answer is not None:
            self.parent.acknowledge(answer, deadline_ns=deadline)

    def _audit_parent(self) -> None:
        if self.parent is None or self.state is None:
            raise ProtocolError("evidence-final-identities-missing")
        frame = self.parent.poll()
        if self.parent.eof:
            self.state.parent_closed()
        if frame is not None or self.parent.data:
            self.state.fail("evidence-parent-trailing-data")

    def _final_admission(self) -> None:
        self.check()
        self._audit_parent()
        self.check()

    def _audit(self) -> None:
        if self.raw is None or self.parent_raw is None:
            raise ProtocolError("evidence-raw-missing")
        if self.state is None:
            raise ProtocolError("evidence-final-identities-missing")
        for raw in (self.parent_raw, self.raw):
            raw.flush()
            raw.close()
        self.raw = self.parent_raw = None
        replay = EvidenceCorrelatedReplay(self.state.expected)
        with (Path.cwd() / "moving-evidence-parent.raw").open("rb") as parent_raw:
            while chunk := parent_raw.read(1024):
                self.check()
                self._audit_parent()
                replay.feed_parent(chunk)
        replay.parent_eof()
        with (Path.cwd() / "moving-evidence.raw").open("rb") as raw:
            while chunk := raw.read(_EVIDENCE_MAX_BYTES):
                self.check()
                self._audit_parent()
                replay.feed_peer(chunk)
        replay.peer_eof()
        if (
            self.peer is None
            or self.peer.pinned is None
            or self.parent is None
            or self.parent.pinned is None
        ):
            raise ProtocolError("evidence-final-identities-missing")
        self.peer.pinned.check(allow_exit=True)
        self.parent.pinned.check()
        self.state.verify_correlated_replay(replay)
        self.audit = replay.status

    def run(self) -> None:  # noqa: C901, PLR0912 - two independently pumped endpoints
        """Run two independent authenticated endpoint pumps until final actual EOF."""
        self.outer = _EvidenceProcess(
            self.api,
            self.launch.outer_pid,
            self.launch.outer_created_100ns,
            _base_executable(),
        )
        self.check()
        self.raw = (Path.cwd() / "moving-evidence.raw").open("xb", buffering=0)
        self.parent_raw = (Path.cwd() / "moving-evidence-parent.raw").open(
            "xb", buffering=0
        )
        self.peer = _ReaderConnection(
            _evidence_server(self.api, self.launch.token, parent=False), parent=False
        )
        self.parent = _ReaderConnection(
            _evidence_server(self.api, self.launch.token, parent=True), parent=True
        )
        self.peer.endpoint.close_deadline_ns = self.launch.absolute_deadline_ns
        self.parent.endpoint.close_deadline_ns = self.launch.absolute_deadline_ns
        self.stage = "ready"
        self.publish(force=True)
        while True:
            now = self.check()
            self._expected()
            if self.expected is not None and self.state is not None:
                for connection in (self.parent, self.peer):
                    connection.pin(self.expected, self.state, now)
                for connection in (self.parent, self.peer):
                    if not connection.parent and self.parent.pinned is None:
                        continue
                    frame = connection.poll(
                        allow_exit=not connection.parent and self.state.final_received
                    )
                    if connection.parent and connection.eof and not self.state.complete:
                        self.state.parent_closed()
                    if frame is not None:
                        if connection.parent:
                            self._parent_record(frame, self.check())
                        else:
                            self._peer_record(frame, self.check())
                self._parent_ack()
                if self.peer.eof:
                    if not self.state.eof_received:
                        self.state.peer_eof(self.check())
                    if not self.parent.ack_pending:
                        break
                if self.authentication_ack_written:
                    self.stage = "authenticated"
            self.publish()
            # A finite event wait yields to the other process while all original
            # watchdogs continue. There are no synchronous pipe reads.
            operation = self.peer.endpoint.reading or self.parent.endpoint.reading
            if operation is not None:
                result = self.api.WaitForSingleObject(operation.overlapped.hEvent, 1)
                if result == _WAIT_FAILED:
                    _raise_win_error()
        self.stage = "auditing"
        self.publish(force=True)
        self._audit()
        self._final_admission()
        self.stage = "complete"
        self.publish(force=True)
        self._final_admission()
        _write_evidence_packet("moving-evidence-final.json", self.packet())
        # Persistence can consume the remaining allowance or coincide with
        # observed parent loss. A written packet alone is not successful exit.
        self._final_admission()

    def close(self) -> None:
        """Attempt every owned resource release without changing an earlier failure."""
        failure: BaseException | None = None
        for resource in (self.raw, self.parent_raw, self.peer, self.parent, self.outer):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:  # noqa: BLE001 - attempt every owned cleanup
                    failure = failure or error
        self.raw = self.parent_raw = self.peer = self.parent = self.outer = None
        if failure is not None:
            raise failure


def run_evidence_reader(payload: str) -> None:
    """Fixed second research role; no target, images, inherited jobs, or dispatch."""
    launch = deserialize_evidence_reader_launch(payload)
    reader = _EvidenceReader(launch)
    try:
        reader.run()
    except BaseException as error:
        if reader.state is not None:
            with suppress(ProtocolError):
                reader.state.fail(type(error).__name__ + ": " + str(error)[:256])
        reader.stage = "failed"
        try:
            _write_evidence_packet(
                "moving-evidence-status.json",
                reader.packet(failed=type(error).__name__ + ": " + str(error)[:256]),
            )
        except BaseException as publication:  # noqa: BLE001 - preserve original failure
            error.add_note(f"Evidence failure publication failed: {publication!r}")
        raise
    finally:
        _close_preserving(reader)


# The runtime bootstrap exposes only this fixed parser/role, never caller-selected
# module or function dispatch.
def deserialize_reader_launch(payload: str) -> EvidenceReaderLaunch:
    """Validate the fixed evidence reader role launch before any native I/O."""
    return deserialize_evidence_reader_launch(payload)


def _observe(
    pipe: _ControlPipe,
    launch: MovingEditLaunch,
    protocol: MovingEditPeerProtocol,
    request: Message,
    budget: SequenceBudget,
) -> None:
    pending = protocol.observe(request)
    provider = protocol.provider
    if provider is None:
        raise ProtocolError("moving-peer-not-begun")
    pipe.checkpoint()
    frame = _MovingEditPipe(launch, pending.frame_id)
    frame.sequence_clock = protocol.clock
    owners = []
    try:
        pipe.checkpoint()
        for name in ("transport_1", "transport_2", "transport_3"):
            owners.append(provider.slots.reserve(name))  # noqa: PERF401 - retain each lease before the next fallible reservation
        result = provider.capture(pending.frame_id, pipe.checkpoint)
        observation = protocol.make_observation(result)
        header = header_for_rgb(observation.frame_identity(), result.frame.rgb)
        pipe.checkpoint()
        budget.record(pending.frame_id)
        pipe.write(
            Message(
                "response",
                request.request_id,
                {
                    "operation": "observe",
                    "moving_schema": MOVING_SCHEMA,
                    "epoch": launch.epoch,
                    "frame_id": pending.frame_id,
                    "observation": observation.value,
                },
            )
        )
        frame.write_bytes(header.encode())
        transfer = PeerCredit(
            launch.epoch, request.request_id, pending.frame_id, len(result.frame.rgb)
        )
        for offset in range(0, len(result.frame.rgb), CHUNK_BYTES):
            pipe.checkpoint()
            token = transfer.announce_wait()
            budget.record(pending.frame_id)
            pipe.write(Message("frame_wait", request.request_id, token.payload()))
            budget.record(pending.frame_id)
            credit = pipe.read()
            pipe.checkpoint()
            if credit.kind != "frame_credit" or credit.version != VERSION:
                _reject("expected-moving-frame-credit")
            transfer.consume_credit(
                CreditToken.from_payload(credit.request_id, credit.payload)
            )
            frame.write_bytes(result.frame.rgb[offset : offset + token.count])
            transfer.wrote_body(token.count)
        # Closing the actual fresh endpoint is mandatory before reading acceptance.
        # The parent proves it observed EOF; a byte count alone is insufficient.
        frame.close()
        pipe.checkpoint()
        budget.record(pending.frame_id)
        accepted = pipe.read()
        protocol.accept_frame(
            accepted, header, observation, transfer, pipe.checkpoint, binary_closed=True
        )
    except BaseException as error:
        # Failed WriteFile frames may retain a chunk and native staging buffer.
        # Drop those unwound aliases before releasing their transport leases.
        discard_traceback(error)
        protocol.failed = True
        provider.cancel()
        raise
    finally:
        try:
            _close_preserving(frame)
        finally:
            for owner in owners:
                owner.release()


class _Closable(Protocol):
    """Owned resources attempt cleanup without replacing a primary failure."""

    def close(self) -> None: ...


def _close_preserving(resource: _Closable) -> None:
    primary = sys.exception()
    try:
        resource.close()
    except BaseException as cleanup:
        if primary is None:
            raise
        primary.add_note(
            f"Moving resource cleanup failed: {type(cleanup).__name__}: "
            f"{str(cleanup)[:256]}"
        )


def _serve(  # noqa: PLR0915 - fixed sixteen-request native lifetime
    pipe: _ControlPipe,
    launch: MovingEditLaunch,
    protocol: MovingEditPeerProtocol,
    budget: SequenceBudget,
) -> None:
    budget.record()
    pipe.write(
        Message(
            "ready",
            0,
            {
                "pid": os.getpid(),
                "executable": str(Path(sys.executable).resolve()),
                "epoch": launch.epoch,
                "protocol_version": VERSION,
                "peer_schema": PEER_SCHEMA,
                "credit_schema": CREDIT_SCHEMA,
                "moving_schema": MOVING_SCHEMA,
                "runtime_schema": RUNTIME_SCHEMA,
                "max_frames": MAX_SEQUENCE_FRAMES,
                "max_requests": MAX_REQUESTS,
            },
        )
    )
    # Runtime ready is only identity. Hello ACK authenticates the independent
    # evidence route before reading/admitting begin or constructing any target.
    journal = _Journal(launch, pipe.checkpoint)
    try:
        budget.record()
        request = pipe.read()
        protocol.begin(request)
        pipe.sequence_clock = protocol.clock
        if (
            protocol.clock is None
            or protocol.begin_payload is None
            or protocol.target is None
        ):
            raise ProtocolError("missing-moving-begin")
        journal.phase_enter(request)
        slots = PeerPixelSlots()
        boundary = NativeBoundary(
            protocol.target,
            protocol.begin_payload["profile_evidence"],
            protocol.clock.deadline_ns,
            pipe.checkpoint,
            slots,
            journal,
            evidence_emit=journal.emit,
            evidence_controller=journal,
            phase_deadline_ns=journal.deadline_ns,
        )
        try:
            provider = MovingProvider(boundary, slots)
            response = protocol.bind_provider(provider, pipe.checkpoint)
            journal.phase_end()
            budget.record()
            pipe.write(response)
            protocol.begin_sent(pipe.checkpoint)
            while not protocol.finished:
                budget.record()
                request = pipe.read()
                journal.phase_enter(request)
                operation = request.payload.get("operation")
                if operation == "move_next":
                    budget.attribute_frame(protocol.frames + 1)
                    response = protocol.move(request, pipe.checkpoint)
                    journal.phase_end()
                    budget.record(protocol.frames + 1)
                    pipe.write(response)
                    protocol.move_sent(pipe.checkpoint)
                elif operation == "observe":
                    budget.attribute_frame(protocol.frames + 1)
                    _observe(pipe, launch, protocol, request, budget)
                    journal.phase_end()
                else:
                    response = protocol.finish(request, pipe.checkpoint)
                    budget.record()
                    pipe.write(response)
                    journal.finish_response_sent(timestamp_ns=time.monotonic_ns())
                    pipe.checkpoint()
                    protocol.clock.frame_deadline_ns = journal.deadline_ns
                    boundary.set_deadline(journal.deadline_ns)
        finally:
            # Context16 remains active through every mandatory native release.
            # Failed cleanup propagates and cannot emit successful phase_end/final.
            _close_preserving(boundary)
        journal.phase_end()
        journal.finish()
    finally:
        _close_preserving(journal)


def run(payload: str) -> None:
    """Serve one fixed research profile; successful retirement shares its deadline."""
    launch = deserialize_launch(payload)
    launch.validate_time(time.monotonic_ns())
    protocol = MovingEditPeerProtocol(launch)
    pipe = _MovingEditPipe(launch)
    try:
        _serve(pipe, launch, protocol, SequenceBudget())
    except BaseException as error:
        protocol.failed = True
        if protocol.provider is not None:
            protocol.provider.cancel()
        discard_traceback(error)
        raise
    finally:
        try:
            _close_preserving(pipe)
        finally:
            if protocol.provider is not None:
                protocol.provider.dispose()
    if protocol.clock is None or not protocol.finished or protocol.failed:
        raise ProtocolError("moving-peer-incomplete-retirement")
    protocol.clock.check()
