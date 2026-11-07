"""Fixed sequence4 peer, limited to repeated owned-WPF read-only observations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Never, cast

from verdiclip.capture.frame_envelope import FrameHeader, header_for_rgb
from verdiclip.capture.observe_protocol import MAX_READ_BYTES, ObserveTarget
from verdiclip.capture.observe_provider import observe_once
from verdiclip.capture.sequence_credit import CreditToken, PeerCredit
from verdiclip.platform.helper_protocol import unique_object
from verdiclip.platform.sequence_protocol import (
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    MAX_SEQUENCE_FRAMES,
    VERSION,
    Message,
    ProtocolError,
    SequenceBudget,
    SequenceFrameProof,
    SequenceObservation,
    decode,
    encode,
)

PEER_SCHEMA = 4
CREDIT_SCHEMA = 2
SEQUENCE_SCHEMA = 1
RUNTIME_SCHEMA = 5
OBSERVE_NS = 2_000_000_000
SEQUENCE_NS = 30_000_000_000
_WAIT_FAILED = 0xFFFFFFFF
_WAIT_TIMEOUT = 258
HEADER_BYTES = 4
_MAX_PID = 0xFFFFFFFF
_MAX_LAUNCH_BYTES = 1024


def _reject(code: str) -> Never:
    """Reject."""
    raise ProtocolError(code)


@dataclass(frozen=True, slots=True)
class SequenceLaunch:
    """Only canonical session data selects the one fixed packaged peer."""

    pipe_name: str
    epoch: str
    parent_pid: int
    protocol_version: int = VERSION
    peer_schema: int = PEER_SCHEMA
    credit_schema: int = CREDIT_SCHEMA
    sequence_schema: int = SEQUENCE_SCHEMA
    runtime_schema: int = RUNTIME_SCHEMA

    def __post_init__(self) -> None:
        """Post init."""
        if (
            type(self.pipe_name) is not str
            or re.fullmatch(r"VerdiClip-sequence-[0-9a-f]{32}", self.pipe_name) is None
        ):
            raise ValueError("Invalid sequence pipe name")
        if (
            type(self.epoch) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.epoch) is None
            or self.epoch == "0" * 32
        ):
            raise ValueError("Invalid sequence epoch")
        if type(self.parent_pid) is not int or not 0 < self.parent_pid <= _MAX_PID:
            raise ValueError("Invalid parent PID")
        for value, expected in (
            (self.protocol_version, VERSION),
            (self.peer_schema, PEER_SCHEMA),
            (self.credit_schema, CREDIT_SCHEMA),
            (self.sequence_schema, SEQUENCE_SCHEMA),
            (self.runtime_schema, RUNTIME_SCHEMA),
        ):
            if type(value) is not int or value != expected:
                raise ValueError("Unsupported sequence profile")

    def serialize(self) -> str:
        """Serialize."""
        payload = json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)
        if len(payload.encode()) > _MAX_LAUNCH_BYTES:
            raise ValueError("Sequence launch exceeds bound")
        return payload


def deserialize_launch(payload: str) -> SequenceLaunch:
    """Reject noncanonical, duplicate and unknown launch fields before native I/O."""
    if type(payload) is not str or len(payload.encode()) > _MAX_LAUNCH_BYTES:
        raise ValueError("Sequence launch exceeds bound")
    value = json.loads(payload, object_pairs_hook=unique_object)
    if type(value) is not dict or set(value) != {
        "pipe_name",
        "epoch",
        "parent_pid",
        "protocol_version",
        "peer_schema",
        "credit_schema",
        "sequence_schema",
        "runtime_schema",
    }:
        raise ValueError("Invalid sequence launch fields")
    launch = SequenceLaunch(**value)
    if launch.serialize() != payload:
        raise ValueError("Noncanonical sequence launch")
    return launch


@dataclass
class SequenceDeadline:
    """One original sequence deadline and optional original owner frame stamp."""

    deadline_ns: int
    frame_deadline_ns: int | None = None
    last_ns: int = 0
    failed: bool = False
    failure: Exception | None = None

    def check(self) -> int:
        """Latch callback failures and invalid/regressed clocks permanently."""
        if self.failure is not None:
            raise self.failure
        try:
            now = time.monotonic_ns()
            deadline = (
                self.frame_deadline_ns
                if self.frame_deadline_ns is not None
                else self.deadline_ns
            )
            if (
                self.failed
                or type(now) is not int
                or now < self.last_ns
                or not now < deadline <= 2**63 - 1
            ):
                _reject("expired-sequence-deadline")
            self.last_ns = now
        except Exception as error:
            self.failed = True
            self.failure = error
            raise
        else:
            return now

    def frame(self, requested_ns: object, deadline_ns: object) -> None:
        """Frame."""
        now = self.check()
        if (
            type(requested_ns) is not int
            or type(deadline_ns) is not int
            or not 0 <= requested_ns <= now
            or deadline_ns != min(requested_ns + OBSERVE_NS, self.deadline_ns)
        ):
            self.failed = True
            self.failure = ProtocolError("invalid-owner-frame-stamp")
            raise self.failure
        self.frame_deadline_ns = deadline_ns
        self.check()


class SequencePeerProtocol:
    """Only begin, one outstanding observation, matching accept, then finish."""

    def __init__(self, launch: SequenceLaunch) -> None:
        """Init."""
        self.launch = launch
        self.last_id = 0
        self.frames = 0
        self.failed = False
        self.finished = False
        self.target: ObserveTarget | None = None
        self.clock: SequenceDeadline | None = None
        self.pending: tuple[int, int, int, int] | None = None
        self.proofs: list[str] = []
        self.chain = bytes(32)
        self.geometry: tuple[int, int, int, int] | None = None

    def _admit(self, request: Message) -> None:
        """Admit."""
        if (
            self.failed
            or self.finished
            or self.pending is not None
            or request.version != VERSION
            or type(request.request_id) is not int
            or request.kind != "request"
            or request.request_id != self.last_id + 1
            or request.request_id > MAX_REQUESTS
        ):
            self.failed = True
            _reject("invalid-sequence-request")

    def begin(self, request: Message) -> Message:
        """Begin."""
        try:
            self._admit(request)
            p = request.payload
            if (
                self.target is not None
                or set(p)
                != {
                    "operation",
                    "sequence_schema",
                    "epoch",
                    "max_frames",
                    "deadline_ns",
                    "target",
                }
                or p["operation"] != "sequence_begin"
                or type(p["sequence_schema"]) is not int
                or p["sequence_schema"] != 1
                or p["epoch"] != self.launch.epoch
                or type(p["max_frames"]) is not int
                or p["max_frames"] != MAX_SEQUENCE_FRAMES
            ):
                _reject("invalid-sequence-begin")
            now = time.monotonic_ns()
            deadline = p["deadline_ns"]
            if type(deadline) is not int or not now < deadline <= min(
                2**63 - 1, now + SEQUENCE_NS
            ):
                _reject("invalid-sequence-deadline")
            self.clock = SequenceDeadline(deadline)
            self.target = ObserveTarget.parse(json.dumps(p["target"]).encode())
            self.clock.check()
            self.last_id = request.request_id
            return Message(
                "response",
                request.request_id,
                {
                    "operation": "sequence_begin",
                    "sequence_schema": 1,
                    "epoch": self.launch.epoch,
                    "max_frames": 8,
                    "deadline_ns": deadline,
                },
            )
        except Exception:
            self.failed = True
            raise

    def observe(self, request: Message) -> tuple[ObserveTarget, int, int, int]:
        """Observe."""
        try:
            self._admit(request)
            p = request.payload
            if (
                self.clock is None
                or self.target is None
                or self.frames >= MAX_SEQUENCE_FRAMES
                or request.request_id >= MAX_REQUESTS
                or set(p) != {"operation", "frame_id", "requested_ns", "deadline_ns"}
                or p["operation"] != "observe"
                or type(p["frame_id"]) is not int
                or p["frame_id"] != self.frames + 1
            ):
                _reject("invalid-sequence-observe")
            self.clock.frame(p["requested_ns"], p["deadline_ns"])
            requested_ns = cast("int", p["requested_ns"])
            deadline_ns = cast("int", p["deadline_ns"])
            self.last_id = request.request_id
            self.pending = (
                request.request_id,
                p["frame_id"],
                requested_ns,
                deadline_ns,
            )
            return self.target, p["frame_id"], requested_ns, deadline_ns
        except Exception:
            self.failed = True
            raise

    def accept_frame(
        self,
        message: Message,
        header: FrameHeader,
        observation: SequenceObservation,
        transfer: PeerCredit,
    ) -> None:
        """Accept frame."""
        try:
            if (
                self.failed
                or self.pending is None
                or self.clock is None
                or message.kind != "frame_accept"
                or message.version != VERSION
            ):
                _reject("unexpected-frame-accept")
            self.clock.check()
            proof = SequenceFrameProof(
                json.dumps(
                    message.payload, sort_keys=True, separators=(",", ":")
                ).encode()
            )
            p = proof.value
            if (
                (message.request_id, p["frame_id"], p["requested_ns"], p["deadline_ns"])
                != self.pending
                or p["request_id"] != message.request_id
                or p["epoch"] != self.launch.epoch
                or proof.observation != observation
                or p["header_sha256"] != hashlib.sha256(header.encode()).hexdigest()
                or not transfer.complete
            ):
                _reject("frame-accept-mismatch")
            identity = observation.frame_identity()
            geometry = (identity.x, identity.y, identity.width, identity.height)
            if observation.target != self.target or (
                self.geometry is not None and geometry != self.geometry
            ):
                _reject("changed-sequence-geometry")
            digest = proof.digest
            chain = hashlib.sha256(self.chain + digest).digest()
            self.clock.check()
            self.geometry = geometry
            self.proofs.append(digest.hex())
            self.chain = chain
            self.frames += 1
            self.pending = None
            self.clock.check()
            self.clock.frame_deadline_ns = None
        except Exception:
            self.failed = True
            raise

    def finish(self, request: Message) -> Message:
        """Finish."""
        try:
            self._admit(request)
            if (
                self.clock is None
                or self.frames == 0
                or request.payload != {"operation": "sequence_finish"}
            ):
                _reject("invalid-sequence-finish")
            self.clock.check()
            self.last_id = request.request_id
            self.finished = True
            return Message(
                "response",
                request.request_id,
                {
                    "operation": "sequence_finish",
                    "sequence_schema": 1,
                    "epoch": self.launch.epoch,
                    "frames": self.frames,
                    "proof_sha256": list(self.proofs),
                    "sequence_sha256": self.chain.hex(),
                },
            )
        except Exception:
            self.failed = True
            raise


class _SequencePipe:
    """Synchronous Win32 I/O confined to the GUI-free owned child process."""

    def __init__(self, launch: SequenceLaunch, frame_id: int | None = None) -> None:
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
            + launch.pipe_name
            + (f"-frame-{frame_id}" if frame_id is not None else ""),
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
            if not count.value:
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
        self.write_bytes(data)

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


def _observe(
    pipe: _SequencePipe,
    launch: SequenceLaunch,
    protocol: SequencePeerProtocol,
    request: Message,
    budget: SequenceBudget,
) -> None:
    """Observe."""
    target, frame_id, _, deadline = protocol.observe(request)
    pipe.sequence_clock = protocol.clock
    pipe.checkpoint()
    frame = _SequencePipe(launch, frame_id)
    frame.sequence_clock = protocol.clock
    try:
        pipe.checkpoint()

        def cancelled() -> bool:
            """Cancelled."""
            pipe.checkpoint()
            return False

        result = observe_once(
            target,
            launch.epoch,
            request.request_id,
            # The provider's float budget is strictly tighter by one ULP; all
            # transport, proof and acceptance checks retain the exact original ns.
            deadline=math.nextafter(deadline / 1_000_000_000, -math.inf),
            cancelled=cancelled,
        )
        pipe.checkpoint()
        observation = SequenceObservation.from_observation(result.observation, frame_id)
        header = header_for_rgb(observation.frame_identity(), result.rgb)
        pipe.checkpoint()
        budget.record(frame_id)
        pipe.write(
            Message(
                "response",
                request.request_id,
                {
                    "operation": "observe",
                    "sequence_schema": 1,
                    "epoch": launch.epoch,
                    "frame_id": frame_id,
                    "observation": json.loads(observation.canonical),
                },
            )
        )
        frame.write_bytes(header.encode())
        transfer = PeerCredit(
            launch.epoch, request.request_id, frame_id, len(result.rgb)
        )
        for offset in range(0, len(result.rgb), 65536):
            token = transfer.announce_wait()
            budget.record(frame_id)
            pipe.write(Message("frame_wait", request.request_id, token.payload()))
            budget.record(frame_id)
            credit = pipe.read()
            if credit.kind != "frame_credit":
                raise ProtocolError("expected-frame-credit")
            transfer.consume_credit(
                CreditToken.from_payload(credit.request_id, credit.payload)
            )
            frame.write_bytes(result.rgb[offset : offset + token.count])
            transfer.wrote_body(token.count)
        frame.close()
        pipe.checkpoint()
        budget.record(frame_id)
        accepted = pipe.read()
        protocol.accept_frame(accepted, header, observation, transfer)
    finally:
        _close_preserving(frame)


def run(payload: str) -> None:
    """Serve one fixed bounded sequence; no arbitrary provider or module dispatch."""
    launch = deserialize_launch(payload)
    protocol = SequencePeerProtocol(launch)
    budget = SequenceBudget()
    pipe = _SequencePipe(launch)
    try:
        budget.record()
        pipe.write(
            Message(
                "ready",
                0,
                {
                    "pid": os.getpid(),
                    "executable": str(Path(sys.executable).resolve()),
                    "epoch": launch.epoch,
                    "protocol_version": 3,
                    "peer_schema": 4,
                    "credit_schema": 2,
                    "sequence_schema": 1,
                    "runtime_schema": 5,
                    "max_frames": 8,
                    "max_requests": 32,
                },
            )
        )
        budget.record()
        request = pipe.read()
        response = protocol.begin(request)
        pipe.sequence_clock = protocol.clock
        budget.record()
        pipe.write(response)
        while not protocol.finished:
            budget.record()
            request = pipe.read()
            if request.payload.get("operation") == "observe":
                budget.attribute_frame(protocol.frames + 1)
                _observe(pipe, launch, protocol, request, budget)
            else:
                response = protocol.finish(request)
                budget.record()
                pipe.write(response)
    finally:
        _close_preserving(pipe)


def _close_preserving(pipe: _SequencePipe) -> None:
    """Retire the endpoint without replacing an already propagating failure."""
    primary = sys.exception()
    try:
        pipe.close()
    except BaseException as cleanup:
        if primary is None:
            raise
        primary.add_note(f"Sequence pipe cleanup failed: {cleanup!r}")
