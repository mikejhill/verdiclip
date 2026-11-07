"""Fixed GUI-free peer: inert commands or one owned-WPF read-only observation."""

from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Never

from verdiclip.capture.frame_credit import CREDIT_SCHEMA, CreditToken, PeerCredit
from verdiclip.capture.frame_envelope import header_for_rgb
from verdiclip.capture.observe_protocol import MAX_READ_BYTES, ObserveTarget
from verdiclip.capture.observe_provider import observe_once
from verdiclip.platform.helper_protocol import (
    HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    VERSION,
    Message,
    ProtocolError,
    decode,
    encode,
    unique_object,
)

_MAX_LAUNCH_BYTES = 1024
_MAX_PID = 0xFFFFFFFF
_WAIT_FAILED = 0xFFFFFFFF
_WAIT_TIMEOUT = 258
PEER_SCHEMA = 3
OBSERVE_NS = 2_000_000_000
MAX_DEADLINE_NS = 2**63 - 1


@dataclass(frozen=True, slots=True)
class PeerLaunch:
    """Session data only; no module, executable, directory or arbitrary pipe path."""

    pipe_name: str
    epoch: str
    parent_pid: int
    protocol_version: int = VERSION
    peer_schema: int = PEER_SCHEMA
    credit_schema: int = CREDIT_SCHEMA

    def __post_init__(self) -> None:
        """Accept only the channel's fixed local pipe naming and bounded identity."""
        if (
            type(self.pipe_name) is not str
            or re.fullmatch(r"VerdiClip-helper-[0-9a-f]{32}", self.pipe_name) is None
        ):
            raise ValueError("Invalid fixed helper pipe name")
        if (
            type(self.epoch) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.epoch) is None
        ):
            raise ValueError("Invalid helper session epoch")
        if type(self.parent_pid) is not int or not 0 < self.parent_pid <= _MAX_PID:
            raise ValueError("Invalid helper parent PID")
        if type(self.credit_schema) is not int or self.credit_schema != CREDIT_SCHEMA:
            raise ValueError("Unsupported frame credit schema")
        if type(self.peer_schema) is not int or self.peer_schema != PEER_SCHEMA:
            raise ValueError("Unsupported fixed peer schema")
        if type(self.protocol_version) is not int or self.protocol_version != VERSION:
            raise ValueError("Unsupported helper protocol version")

    def serialize(self) -> str:
        """Encode one bounded immutable launch envelope as argv data."""
        payload = json.dumps(asdict(self), separators=(",", ":"))
        if len(payload.encode()) > _MAX_LAUNCH_BYTES:
            raise ValueError("Helper launch exceeds bound")
        return payload


def deserialize_launch(payload: str) -> PeerLaunch:
    """Reject duplicate/unknown fields and paths before opening any pipe."""
    if type(payload) is not str or len(payload.encode()) > _MAX_LAUNCH_BYTES:
        raise ValueError("Helper launch exceeds bound")
    value = json.loads(payload, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {
        "pipe_name",
        "epoch",
        "parent_pid",
        "protocol_version",
        "peer_schema",
        "credit_schema",
    }:
        raise ValueError("Invalid helper launch fields")
    return PeerLaunch(**value)


class PeerProtocol:
    """Strict sequential inert commands plus one fixed read-only observation."""

    def __init__(self) -> None:
        self.last_id = 0
        self.finished = False
        self.failed = False
        self.observe_consumed = False
        self.observe_clock: ObserveDeadline | None = None

    def _admit(self, request: Message) -> None:
        if (
            self.finished
            or self.failed
            or type(request.version) is not int
            or request.version != VERSION
            or request.kind != "request"
            or type(request.request_id) is not int
            or request.request_id != self.last_id + 1
            or request.request_id > MAX_REQUESTS
        ):
            self.failed = True
            raise ProtocolError("invalid-peer-request")

    def accept(self, request: Message) -> Message:
        """Accept only ping/finish; after observe only the reserved finish is legal."""
        self._admit(request)
        if request.payload not in ({"operation": "ping"}, {"operation": "finish"}) or (
            self.observe_consumed and request.payload != {"operation": "finish"}
        ):
            self.failed = True
            raise ProtocolError("unsupported-peer-operation")
        self.last_id = request.request_id
        self.finished = request.payload == {"operation": "finish"}
        return Message(
            "response",
            request.request_id,
            {"operation": "finished" if self.finished else "pong"},
        )

    def observe(self, request: Message) -> tuple[ObserveTarget, int]:
        """Consume the single attempt before validation; reserve a finish wire ID."""
        self._admit(request)
        attempted = self.observe_consumed
        self.observe_consumed = True
        try:
            if attempted or request.request_id >= MAX_REQUESTS:
                self.failed = True
                self._admit(request)
            self.observe_clock = ObserveDeadline(request.payload.get("deadline_ns"))
            self.observe_clock.check()
            target, deadline = observe_payload(request.payload)
            self.observe_clock.check()
            self.last_id = request.request_id
        except Exception:
            self.failed = True
            raise
        else:
            return target, deadline


def validate_deadline_ns(value: object) -> int:
    """Validate authoritative same-host Windows monotonic time, never receipt+2s."""
    now = time.monotonic_ns()
    if type(now) is not int or not 0 <= now <= MAX_DEADLINE_NS:
        raise ProtocolError("invalid-monotonic-ns-clock")
    if (
        type(value) is not int
        or not 0 < value <= MAX_DEADLINE_NS
        or not now < value <= now + OBSERVE_NS
    ):
        raise ProtocolError("invalid-or-expired-observe-deadline")
    return value


def _deadline_error(reason: str) -> Never:
    raise ProtocolError(reason)


@dataclass
class ObserveDeadline:
    """One shared per-observe ns clock; ordinary callback failure is permanent."""

    deadline_ns: object
    last_ns: int | None = None
    failure: Exception | None = None

    def check(self) -> int:
        """Reject regression across control, provider and binary checkpoints."""
        if self.failure is not None:
            raise self.failure
        try:
            now = time.monotonic_ns()
            if (
                type(now) is not int
                or not 0 <= now <= MAX_DEADLINE_NS
                or (self.last_ns is not None and now < self.last_ns)
            ):
                _deadline_error("invalid-or-regressed-monotonic-ns-clock")
            deadline = self.deadline_ns
            if (
                type(deadline) is not int
                or not 0 < deadline <= MAX_DEADLINE_NS
                or not now < deadline <= now + OBSERVE_NS
            ):
                _deadline_error("invalid-or-expired-observe-deadline")
            self.last_ns = now
        except Exception as error:
            self.failure = error
            raise
        else:
            return now


def observe_payload(payload: dict[str, object]) -> tuple[ObserveTarget, int]:
    """Parse the exact fixed wrapper; target schema and byte bounds remain separate."""
    if (
        set(payload) != {"operation", "observe_schema", "deadline_ns", "request"}
        or payload["operation"] != "observe"
        or type(payload["observe_schema"]) is not int
        or payload["observe_schema"] != 1
    ):
        raise ProtocolError("invalid-observe-request")
    deadline = validate_deadline_ns(payload["deadline_ns"])
    target = ObserveTarget.parse(
        json.dumps(payload["request"], allow_nan=False).encode()
    )
    validate_deadline_ns(deadline)
    return target, deadline


class _Pipe:
    """Synchronous Win32 I/O confined to the GUI-free owned child process."""

    def __init__(self, launch: PeerLaunch, *, frame: bool = False) -> None:
        self.deadline_ns: int | None = None
        self.observe_clock: ObserveDeadline | None = None
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
            "\\\\.\\pipe\\" + launch.pipe_name + ("-frame" if frame else ""),
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
        if self.deadline_ns is not None and self.observe_clock is None:
            self.observe_clock = ObserveDeadline(self.deadline_ns)
        if self.observe_clock is not None:
            self.observe_clock.check()
        self._parent_alive()
        if self.observe_clock is not None:
            self.observe_clock.check()

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


def _close_pipes(pipes: list[_Pipe], primary: BaseException | None = None) -> None:
    failure = primary
    for pipe in reversed(pipes):
        try:
            pipe.close()
        except BaseException as error:  # noqa: BLE001 - attempt every owned endpoint
            if failure is None:
                failure = error
            else:
                BaseException.add_note(failure, f"Pipe cleanup failed: {error!r}")
    if failure is not None and primary is None:
        raise failure


def _observe(
    pipe: _Pipe,
    frame: _Pipe,
    launch: PeerLaunch,
    protocol: PeerProtocol,
    request: Message,
) -> None:
    target, deadline_ns = protocol.observe(request)
    pipe.deadline_ns = frame.deadline_ns = deadline_ns
    pipe.observe_clock = frame.observe_clock = protocol.observe_clock

    def cancelled() -> bool:
        pipe.checkpoint()
        return False

    result = observe_once(
        target,
        launch.epoch,
        request.request_id,
        deadline=deadline_ns / 1_000_000_000,
        cancelled=cancelled,
    )
    pipe.checkpoint()
    header = header_for_rgb(result.observation.frame_identity(), result.rgb)
    pipe.checkpoint()
    # Complete small metadata first, before any binary backpressure can block us.
    pipe.write(
        Message(
            "response",
            request.request_id,
            {
                "operation": "observed",
                "observation": json.loads(result.observation.canonical),
            },
        )
    )
    frame.write_bytes(header.encode())
    transfer = PeerCredit(launch.epoch, request.request_id, len(result.rgb))
    for offset in range(0, len(result.rgb), MAX_READ_BYTES):
        token = transfer.announce_wait()
        pipe.write(Message("frame_wait", request.request_id, token.payload()))
        credit = pipe.read()
        pipe.checkpoint()
        if credit.kind != "frame_credit":
            raise ProtocolError("expected-frame-credit")
        transfer.consume_credit(
            CreditToken.from_payload(credit.request_id, credit.payload)
        )
        frame.write_bytes(result.rgb[offset : offset + token.count])
        transfer.wrote_body(token.count)
    frame.close()
    pipe.checkpoint()


def run(payload: str) -> None:
    """Serve the fixed peer once; all resources close on EOF, failure or finish."""
    launch = deserialize_launch(payload)
    pipes: list[_Pipe] = []
    try:
        pipe = _Pipe(launch)
        pipes.append(pipe)
        frame = _Pipe(launch, frame=True)
        pipes.append(frame)
        protocol = PeerProtocol()
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
                },
            )
        )
        while not protocol.finished:
            request = pipe.read()
            if request.payload.get("operation") == "observe":
                _observe(pipe, frame, launch, protocol, request)
            else:
                response = protocol.accept(request)
                if protocol.finished:
                    frame.close()  # Empty EOF is also required for inert finish.
                pipe.write(response)
    except BaseException as primary:
        _close_pipes(pipes, primary)
        raise
    else:
        _close_pipes(pipes)
