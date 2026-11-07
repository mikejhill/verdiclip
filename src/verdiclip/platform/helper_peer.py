"""Fixed GUI-free inert peer: ping/finish only, with no provider or capture access."""

from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class PeerLaunch:
    """Session data only; no module, executable, directory or arbitrary pipe path."""

    pipe_name: str
    epoch: str
    parent_pid: int
    protocol_version: int = VERSION

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
    }:
        raise ValueError("Invalid helper launch fields")
    return PeerLaunch(**value)


class InertProtocol:
    """Strict sequential request handling with exactly two fixed operations."""

    def __init__(self) -> None:
        self.last_id = 0
        self.finished = False
        self.failed = False

    def accept(self, request: Message) -> Message:
        """Never dispatch arbitrary commands or silently reconnect/retry."""
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
        if request.payload not in ({"operation": "ping"}, {"operation": "finish"}):
            self.failed = True
            raise ProtocolError("unsupported-peer-operation")
        self.last_id = request.request_id
        self.finished = request.payload == {"operation": "finish"}
        return Message(
            "response",
            request.request_id,
            {"operation": "finished" if self.finished else "pong"},
        )


class _Pipe:
    """Synchronous Win32 I/O confined to the GUI-free owned child process."""

    def __init__(self, launch: PeerLaunch) -> None:
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
            "\\\\.\\pipe\\" + launch.pipe_name, 0xC0000000, 0, None, 3, 0, None
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
            self._parent_alive()
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

    def write(self, message: Message) -> None:
        """Write one bounded response, handling partial native writes."""
        data = encode(message)
        while data:
            self._parent_alive()
            buffer = ctypes.create_string_buffer(data)
            count = wintypes.DWORD()
            if not self.api.WriteFile(
                self.handle, buffer, len(data), ctypes.byref(count), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not 0 < count.value <= len(data):
                raise ProtocolError("peer-write-failed")
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


def run(payload: str) -> None:
    """Run only the fixed attested inert protocol; EOF/errors exit unsuccessfully."""
    launch = deserialize_launch(payload)
    pipe = _Pipe(launch)
    protocol = InertProtocol()
    try:
        pipe.write(
            Message(
                "ready",
                0,
                {
                    "pid": os.getpid(),
                    "executable": str(Path(sys.executable).resolve()),
                    "epoch": launch.epoch,
                    "protocol_version": launch.protocol_version,
                },
            )
        )
        while not protocol.finished:
            pipe.write(protocol.accept(pipe.read()))
    except BaseException as primary:
        try:
            pipe.close()
        except BaseException as cleanup:  # noqa: BLE001 - preserve primary failure
            primary.add_note(f"Pipe cleanup failed: {cleanup!r}")
        raise
    else:
        pipe.close()
