"""Inert Windows named-pipe proof, not product transport or capture integration.

Parent uses Qt signals/timers only; child uses blocking Win32 I/O in its own
contained process. Qt's Windows socketDescriptor currently exposes its pipe
HANDLE. This experiment requires GetNamedPipeClientProcessId to prove that on
our actual runtime, with no fallback to trusting a self-reported PID.
Sources: https://code.qt.io/cgit/qt/qtbase.git/tree/src/network/socket/qlocalsocket_win.cpp
https://code.qt.io/cgit/qt/qtbase.git/tree/src/network/socket/qlocalserver_win.cpp
"""

from __future__ import annotations

import argparse
import ctypes as c
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from ctypes import wintypes as w
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from verdiclip.platform.owned_helper import HelperCommand, OwnedHelper

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

MAX_MESSAGE_BYTES = 16_384
MAX_REQUESTS = 128
MAX_RECEIVED_MESSAGES = 4
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 1024
HEADER_BYTES = 4
VERSION = 1
STARTUP_MS = 3000
COMMAND_MS = 2000
SESSION_MS = 20_000
CLEANUP_MS = 5000
POLL_MS = 10
CASES = (
    "fragmented",
    "oversized",
    "duplicate",
    "wrong-id",
    "startup-timeout",
    "command-timeout",
    "cancel",
    "wrong-peer",
)
_BOOTSTRAP = (
    "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
    "sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')"
)


@dataclass
class GapStats:
    """Constant-space event-loop gap observations, not a responsiveness promise."""

    last_ns: int | None = None
    count: int = 0
    total_ns: int = 0
    maximum_ns: int = 0
    first_ns: int | None = None

    def tick(self, now_ns: int) -> None:
        """Include delayed timer delivery instead of resetting away its stall."""
        if self.last_ns is not None:
            elapsed = now_ns - self.last_ns
            self.count += 1
            self.total_ns += elapsed
            self.maximum_ns = max(self.maximum_ns, elapsed)
            if self.first_ns is None:
                self.first_ns = elapsed
        self.last_ns = now_ns

    def report(self) -> dict[str, int | None]:
        """Summarize bounded counters without retaining individual samples."""
        return {
            "count": self.count,
            "first_gap_ns": self.first_ns,
            "maximum_gap_ns": self.maximum_ns,
            "mean_gap_ns": self.total_ns // self.count if self.count else None,
        }


class LatencyMetrics:
    """Measurements use perf_counter_ns, separate from unchanged safety clocks."""

    SPANS = ("start_callback", "helper_start", "verify_ready", "cancel_callback")
    MARKS = (
        "cancel_requested",
        "termination_requested",
        "cleanup_started",
        "terminal",
        "cleanup_confirmed",
    )

    def __init__(self) -> None:
        self.spans: dict[str, int] = {}
        self.marks: dict[str, int] = {}
        self.heartbeat = GapStats()
        self.poll = GapStats()

    @contextmanager
    def measure(self, name: str) -> Generator[None]:
        """Retain a fixed named span even when its measured action fails."""
        if name not in self.SPANS:
            raise ValueError("Unknown latency span")
        started = time.perf_counter_ns()
        try:
            yield
        finally:
            self.spans.setdefault(name, time.perf_counter_ns() - started)

    def mark(self, name: str) -> None:
        """Latch the first transition; repeated failures cannot reset elapsed time."""
        if name not in self.MARKS:
            raise ValueError("Unknown latency transition")
        self.marks.setdefault(name, time.perf_counter_ns())

    def report(self) -> dict[str, object]:
        """Publish missing observations as null rather than invented zero latency."""
        ordering: dict[str, str] = {}

        def elapsed(start: str, end: str) -> int | None:
            key = f"{start}_to_{end}"
            if start not in self.marks or end not in self.marks:
                ordering[key] = "missing_observation"
                return None
            duration = self.marks[end] - self.marks[start]
            if duration < 0:
                # Kernel retirement can precede protocol cleanup when the final
                # reply is still buffered. Preserve timestamps, not a fake zero.
                ordering[key] = "end_observed_before_start"
                return None
            ordering[key] = "in_order"
            return duration

        return {
            "units": "nanoseconds",
            "observation_only": True,
            "spans_ns": {name: self.spans.get(name) for name in self.SPANS},
            "heartbeat": self.heartbeat.report(),
            "poll": self.poll.report(),
            "timer_interval_ms": POLL_MS,
            "heartbeat_armed_before_start_callback": True,
            "poll_timer_starts_after_helper_start": True,
            "cancel_to_termination_request_ns": elapsed(
                "cancel_requested", "termination_requested"
            ),
            "cancel_to_cleanup_confirmed_ns": elapsed(
                "cancel_requested", "cleanup_confirmed"
            ),
            "cleanup_to_confirmed_ns": elapsed("cleanup_started", "cleanup_confirmed"),
            "cleanup_to_terminal_ns": elapsed("cleanup_started", "terminal"),
            "transition_ordering": ordering,
        }


class ProtocolError(ValueError):
    """A permanently rejected framed message or request transition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(*, condition: bool, code: str) -> None:
    """One explicit failed requirement feeds the terminal rejection boundary."""
    if not condition:
        raise ProtocolError(code)


def bounded_json(value: object) -> None:
    """Reject deep, excessive or nonfinite values, including exponent overflow."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ProtocolError("json-complexity")
        if isinstance(item, float) and not math.isfinite(item):
            raise ProtocolError("nonfinite-json")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Do not let duplicate JSON keys replace identity/protocol fields."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate-key")
        result[key] = value
    return result


def invalid_constant(_value: str) -> object:
    """Reject JSON's nonstandard nonfinite constants."""
    raise ProtocolError("nonfinite-json")


@dataclass(frozen=True)
class Message:
    """One explicit bounded envelope; phase-specific payloads are checked separately."""

    kind: str
    request_id: int
    payload: dict[str, object]
    version: int = VERSION


def decode(payload: bytes) -> Message:
    """Validate UTF-8, JSON structure and exact envelope types."""
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
    except ProtocolError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError("malformed-json") from error
    if not isinstance(value, dict) or set(value) != {
        "version",
        "kind",
        "request_id",
        "payload",
    }:
        raise ProtocolError("invalid-envelope")
    version, request_id = value["version"], value["request_id"]
    if type(version) is not int or version != VERSION or type(request_id) is not int:
        raise ProtocolError("invalid-envelope")
    if not 0 <= request_id <= MAX_REQUESTS or value["kind"] not in (
        "ready",
        "request",
        "response",
    ):
        raise ProtocolError("invalid-envelope")
    if not isinstance(value["payload"], dict):
        raise ProtocolError("invalid-envelope")
    bounded_json(value)
    return Message(value["kind"], request_id, value["payload"], version)


def encode(message: Message) -> bytes:
    """Length-prefix a checked envelope without permitting nonfinite JSON."""
    payload = json.dumps(
        asdict(message), allow_nan=False, separators=(",", ":")
    ).encode()
    decode(payload)
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ProtocolError("oversized")
    return struct.pack(">I", len(payload)) + payload


class Decoder:
    """Incremental parser with bounded buffering and a permanent failure latch."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> list[Message]:
        """Accept fragmentation/coalescing without trusting an unbounded length."""
        if self.failed:
            raise ProtocolError("decoder-failed")
        try:
            return self._feed(data)
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise

    def _feed(self, data: bytes) -> list[Message]:
        if len(data) + len(self.buffer) > MAX_MESSAGE_BYTES + HEADER_BYTES:
            raise ProtocolError("buffer-limit")
        self.buffer.extend(data)
        messages = []
        while len(self.buffer) >= HEADER_BYTES:
            size = struct.unpack(">I", self.buffer[:HEADER_BYTES])[0]
            if not 0 < size <= MAX_MESSAGE_BYTES:
                raise ProtocolError("oversized")
            total = HEADER_BYTES + size
            if len(self.buffer) < total:
                break
            messages.append(decode(bytes(self.buffer[HEADER_BYTES:total])))
            del self.buffer[:total]
        return messages


class SingleFlight:
    """One request at a time, with no recovery from unsolicited/duplicate replies."""

    def __init__(self) -> None:
        self.pending: int | None = None
        self.completed = 0
        self.failed = False

    def begin(self, payload: dict[str, object]) -> Message:
        """Reserve the next identifier before scheduling its bytes."""
        if self.failed or self.pending is not None or self.completed >= MAX_REQUESTS:
            raise ProtocolError("single-flight")
        self.pending = self.completed + 1
        return Message("request", self.pending, payload)

    def accept(self, message: Message) -> None:
        """Validate a reply before clearing the sole outstanding request."""
        code = None
        if self.failed:
            code = "single-flight"
        elif message.kind != "response":
            code = "unexpected-kind"
        elif message.request_id <= self.completed:
            code = "duplicate"
        elif self.pending is None or message.request_id != self.pending:
            code = "wrong-id"
        if code is not None:
            self.failed = True
            raise ProtocolError(code)
        self.completed = message.request_id
        self.pending = None


class NativePipe:
    """Minimal Win32 boundary; never substitute an unverified descriptor."""

    def __init__(self) -> None:
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "GetNamedPipeClientProcessId": ([w.HANDLE, c.POINTER(w.ULONG)], w.BOOL),
            "CreateFileW": (
                [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE],
                w.HANDLE,
            ),
            "ReadFile": (
                [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p],
                w.BOOL,
            ),
            "WriteFile": (
                [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p],
                w.BOOL,
            ),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def client_pid(self, descriptor: int) -> int:
        """Obtain identity from the actual accepted pipe handle."""
        pid = w.ULONG()
        if not self.kernel.GetNamedPipeClientProcessId(descriptor, c.byref(pid)):
            raise c.WinError(c.get_last_error())
        return int(pid.value)

    def connect(self, name: str) -> int:
        """Connect once to the already-listening owned local pipe."""
        handle = self.kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        return int(handle)

    def read_message(self, handle: int) -> Message:
        """Blocking child-only reads, with length checked before allocation."""
        header = self._read_exact(handle, HEADER_BYTES)
        size = struct.unpack(">I", header)[0]
        if not 0 < size <= MAX_MESSAGE_BYTES:
            raise ProtocolError("oversized")
        return decode(self._read_exact(handle, size))

    def _read_exact(self, handle: int, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            buffer = c.create_string_buffer(size - len(result))
            count = w.DWORD()
            if (
                not self.kernel.ReadFile(
                    handle, buffer, len(buffer), c.byref(count), None
                )
                or not count.value
            ):
                raise OSError("Fixture pipe read failed/closed")
            result.extend(buffer.raw[: count.value])
        return bytes(result)

    def write(self, handle: int, data: bytes) -> None:
        """Blocking child-only writes also handle short writes explicitly."""
        position = 0
        while position < len(data):
            buffer = c.create_string_buffer(data[position:])
            count = w.DWORD()
            if (
                not self.kernel.WriteFile(
                    handle, buffer, len(data) - position, c.byref(count), None
                )
                or not count.value
            ):
                raise OSError("Fixture pipe write failed/closed")
            position += count.value


def wrong_peer(name: str) -> None:
    """Let a contained descendant, never the direct producer, connect to the pipe."""
    script = Path(__file__).resolve()
    descendant_command = HelperCommand.base_python(
        (
            "-c",
            _BOOTSTRAP,
            str(script.parents[1] / "src"),
            str(script),
            "--child",
            "--pipe",
            name,
            "--case",
            "fragmented",
        ),
        Path.cwd(),
    )
    # No breakaway flag: this child inherits the producer's already-owned job.
    descendant = subprocess.Popen(  # noqa: S603 - explicit owned inert fixture
        [descendant_command.executable, *descendant_command.arguments],
        creationflags=0x08000000,
        close_fds=True,
    )
    descendant.wait(timeout=60)  # Child-only; parent remains fully asynchronous.


def child(name: str, case: str) -> None:
    """Only inert protocol traffic; no UIA, capture, credentials or persistent state."""
    special = {
        "startup-timeout": lambda: time.sleep(60),
        "wrong-peer": lambda: wrong_peer(name),
    }
    if case in special:
        special[case]()
        return
    api = NativePipe()
    handle = api.connect(name)
    try:
        ready = encode(
            Message("ready", 0, {"pid": os.getpid(), "executable": sys.executable})
        )
        api.write(handle, ready)
        request = api.read_message(handle)
        if request != Message("request", 1, {"operation": "ping"}):
            raise ProtocolError("unexpected-request")
        if case in ("command-timeout", "cancel"):
            time.sleep(60)
            return
        reply = encode(
            Message("response", 2 if case == "wrong-id" else 1, {"operation": "pong"})
        )
        if case == "oversized":
            reply = struct.pack(">I", MAX_MESSAGE_BYTES + 1)
        if case == "duplicate":
            reply += reply
        for part in (reply[:1], reply[1:3], reply[3:8], reply[8:]):
            if part:
                api.write(handle, part)
                time.sleep(0.02)
        if case != "fragmented":
            time.sleep(60)
            return
        request = api.read_message(handle)
        if request != Message("request", 2, {"operation": "finish"}):
            raise ProtocolError("unexpected-request")
        api.write(handle, encode(Message("response", 2, {"operation": "finished"})))
    finally:
        api.kernel.CloseHandle(handle)


class Session:
    """A single inert case, driven entirely by parent Qt signals and timers."""

    def __init__(
        self, application: QCoreApplication, case: str, directory: Path
    ) -> None:
        # Child runs isolated stdlib-only. Qt is imported only by the parent.
        from PySide6.QtCore import QTimer  # noqa: PLC0415
        from PySide6.QtNetwork import QLocalServer  # noqa: PLC0415

        self.application, self.case, self.directory = application, case, directory
        self.latency = LatencyMetrics()
        self.helper = OwnedHelper()
        self.api = NativePipe()
        self.decoder, self.flight = Decoder(), SingleFlight()
        self.socket: QLocalSocket | None = None
        self.phase = "startup"
        self.failure: str | None = None
        self.cleanup_failure: str | None = None
        self.verified_pid: int | None = None
        self.peer_job_members: tuple[int, ...] = ()
        self.received = 0
        self.partial_reads = 0
        self.done = False
        self.eof = False
        self.phase_deadline = time.monotonic() + STARTUP_MS / 1000
        self.session_deadline = time.monotonic() + SESSION_MS / 1000
        self.server = QLocalServer(application)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(1)
        self.server.newConnection.connect(self._connected)
        self.poll_timer = QTimer(application)
        self.poll_timer.setInterval(POLL_MS)
        self.poll_timer.timeout.connect(self._poll)
        self.deadline_timer = QTimer(application)
        self.deadline_timer.setInterval(POLL_MS)
        self.deadline_timer.timeout.connect(self._deadline)
        self.cancel_timer = QTimer(application)
        self.cancel_timer.setSingleShot(True)
        self.cancel_timer.timeout.connect(self.cancel)
        self.heartbeat_timer = QTimer(application)
        self.heartbeat_timer.setInterval(POLL_MS)
        self.heartbeat_timer.timeout.connect(self._heartbeat)
        self.latency.heartbeat.tick(time.perf_counter_ns())
        self.heartbeat_timer.start()

    def start(self) -> None:
        """Time the existing Qt startup callback without moving work or deadlines."""
        with self.latency.measure("start_callback"):
            self._start()

    def _start(self) -> None:
        """Listen before spawning the direct contained helper, with no Qt waits."""
        try:
            require(
                condition=self.server.listen(
                    f"VerdiClip-pipe-proof-{uuid.uuid4().hex}"
                ),
                code="listen-failed",
            )
            script = Path(__file__).resolve()
            command = HelperCommand.base_python(
                (
                    "-c",
                    _BOOTSTRAP,
                    str(script.parents[1] / "src"),
                    str(script),
                    "--child",
                    "--pipe",
                    self.server.fullServerName(),
                    "--case",
                    self.case,
                ),
                self.directory,
            )
            with self.latency.measure("helper_start"):
                self.helper.start(command)
            self.latency.poll.tick(time.perf_counter_ns())
            self.poll_timer.start()
            self.deadline_timer.start()
            if self.helper.status.failure is not None:
                self._fail("startup-failed")
        except Exception as error:  # noqa: BLE001 - native experiment boundary
            self._fail(f"startup-error:{error}")
            self.latency.poll.tick(time.perf_counter_ns())
            self.poll_timer.start()
            self.deadline_timer.start()

    def _connected(self) -> None:
        try:
            socket = self.server.nextPendingConnection()
            require(
                condition=socket is not None
                and self.socket is None
                and self.phase == "startup",
                code="unexpected-connection",
            )
            self.socket = socket
            socket.setReadBufferSize(MAX_MESSAGE_BYTES + HEADER_BYTES)
            self.verified_pid = self.api.client_pid(socket.socketDescriptor())
            self.peer_job_members = self.helper.poll().remaining_pids or ()
            require(
                condition=self.verified_pid == self.helper.status.pid,
                code="wrong-peer-pid",
            )
            self.server.close()
            socket.readyRead.connect(self._readable)
            socket.disconnected.connect(self._disconnected)
            self._readable()
        except Exception as error:  # noqa: BLE001 - reject and retire owned producer
            self._fail(
                error.code
                if isinstance(error, ProtocolError)
                else f"peer-error:{error}"
            )

    def _readable(self) -> None:
        if self.done or self.failure is not None or self.socket is None:
            return
        try:
            remaining = MAX_MESSAGE_BYTES + HEADER_BYTES
            while remaining and self.socket.bytesAvailable():
                data = bytes(self.socket.read(remaining).data())
                if not data:
                    break
                remaining -= len(data)
                messages = self.decoder.feed(data)
                if self.decoder.buffer:
                    self.partial_reads += 1
                for message in messages:
                    self.received += 1
                    require(
                        condition=self.received <= MAX_RECEIVED_MESSAGES,
                        code="message-count",
                    )
                    self._message(message)
            require(condition=not self.socket.bytesAvailable(), code="buffer-limit")
        except Exception as error:  # noqa: BLE001 - one terminal failure
            self._fail(
                error.code
                if isinstance(error, ProtocolError)
                else f"read-error:{error}"
            )

    def _message(self, message: Message) -> None:
        self._check_operation_deadline()
        if self.phase == "startup":
            if (
                message.kind != "ready"
                or message.request_id != 0
                or set(message.payload) != {"pid", "executable"}
            ):
                raise ProtocolError("invalid-ready")
            pid, executable = message.payload["pid"], message.payload["executable"]
            if (
                type(pid) is not int
                or pid != self.verified_pid
                or not isinstance(executable, str)
            ):
                raise ProtocolError("invalid-ready")
            with self.latency.measure("verify_ready"):
                ready_status = self.helper.verify_ready(pid, executable)
            if not ready_status.producer_verified:
                raise ProtocolError("unverified-producer")
            self._send({"operation": "ping"})
            if self.case == "cancel":
                self.cancel_timer.start(100)
            return
        self.flight.accept(message)
        expected = "pong" if message.request_id == 1 else "finished"
        if message.payload != {"operation": expected}:
            raise ProtocolError("invalid-response")
        if message.request_id == 1:
            self._send({"operation": "finish"})
        else:
            self._cleanup()

    def _send(self, payload: dict[str, object]) -> None:
        if self.socket is None:
            raise ProtocolError("no-connection")
        self._check_operation_deadline()
        data = encode(self.flight.begin(payload))
        if self.socket.bytesToWrite() or self.socket.write(data) != len(data):
            raise ProtocolError("write-failed")
        self.phase = "command"
        self.phase_deadline = time.monotonic() + COMMAND_MS / 1000

    def _check_operation_deadline(self) -> None:
        if self.phase == "cleanup":
            return
        now = time.monotonic()
        if now >= self.session_deadline:
            raise ProtocolError("session-timeout")
        if now >= self.phase_deadline:
            raise ProtocolError(f"{self.phase}-timeout")

    def _disconnected(self) -> None:
        self._readable()
        self.eof = True
        if self.decoder.buffer:
            self._fail("truncated-message")
        if not self.done and self.phase != "cleanup":
            self._fail("unexpected-disconnect")

    def cancel(self) -> None:
        """Cancellation is out-of-band retirement, never a queued child command."""
        self.latency.mark("cancel_requested")
        with self.latency.measure("cancel_callback"):
            self._fail("cancelled")

    def _cleanup(self) -> None:
        self.latency.mark("cleanup_started")
        self.phase = "cleanup"
        self.phase_deadline = time.monotonic() + CLEANUP_MS / 1000
        self.server.close()

    def _fail(self, reason: str) -> None:
        if self.done:
            return
        if self.failure is None:
            self.failure = reason
        if self.phase != "cleanup":
            self._cleanup()
        self.latency.mark("termination_requested")
        self.helper.terminate()
        if self.socket is not None:
            self.socket.abort()

    def _heartbeat(self) -> None:
        if not self.done:
            self.latency.heartbeat.tick(time.perf_counter_ns())

    def _poll(self) -> None:
        self.latency.poll.tick(time.perf_counter_ns())
        if self.done or self._deadline():
            return
        status = self.helper.poll()
        if status.cleanup_confirmed:
            self.latency.mark("cleanup_confirmed")
        if status.exit_code is not None:
            self._readable()
        if self.failure is None and (
            status.failure is not None
            or (status.exit_code is not None and status.exit_code != 0)
        ):
            self._fail("unexpected-exit")
        # Polling/provider callbacks cannot extend the absolute cleanup deadline.
        if self._deadline():
            return
        if (
            self.phase == "cleanup"
            and status.cleanup_confirmed
            and (self.failure is not None or (self.eof and not self.decoder.buffer))
        ):
            self._finish()

    def _deadline(self) -> bool:
        if self.done:
            return True
        now = time.monotonic()
        if now >= self.phase_deadline:
            if self.phase == "cleanup":
                self.cleanup_failure = "cleanup-timeout"
                self.helper.release_unconfirmed("Pipe cleanup deadline exceeded")
                self._finish()
            else:
                self._fail(f"{self.phase}-timeout")
        elif now >= self.session_deadline and self.phase != "cleanup":
            self._fail("session-timeout")
        return self.done

    def _finish(self) -> None:
        self.done = True
        self.poll_timer.stop()
        self.deadline_timer.stop()
        self.cancel_timer.stop()
        self.heartbeat_timer.stop()
        self.server.close()
        if self.socket is not None:
            self.socket.close()
            self.socket.deleteLater()
        self.server.deleteLater()
        self.poll_timer.deleteLater()
        self.deadline_timer.deleteLater()
        self.cancel_timer.deleteLater()
        self.heartbeat_timer.deleteLater()
        if self.helper.status.cleanup_confirmed:
            self.latency.mark("cleanup_confirmed")
        self.latency.mark("terminal")
        self.application.quit()

    def result(self) -> dict[str, object]:
        """Never call an expected rejection a success without verified cleanup."""
        expected = None if self.case == "fragmented" else self.case
        if self.case == "cancel":
            expected = "cancelled"
        if self.case == "wrong-peer":
            expected = "wrong-peer-pid"
        wrong_peer_proved = (
            self.verified_pid != self.helper.status.pid
            and self.verified_pid in self.peer_job_members
        )
        return {
            "case": self.case,
            "passed": self.failure == expected
            and self.cleanup_failure is None
            and self.helper.status.cleanup_confirmed
            and not self.helper.status.cleanup_errors
            and self.helper.status.failure is None
            and (self.case != "wrong-peer" or wrong_peer_proved)
            and (
                self.case != "fragmented"
                or (
                    self.partial_reads > 0
                    and self.eof
                    and self.helper.status.exit_code == 0
                )
            ),
            "failure": self.failure,
            "cleanup_failure": self.cleanup_failure,
            "kernel_pipe_client_pid": self.verified_pid,
            "peer_job_members_before_cleanup": self.peer_job_members,
            "received_messages": self.received,
            "partial_message_reads": self.partial_reads,
            "clean_eof_observed": self.eof and not self.decoder.buffer,
            "socket_access": "UserAccessOption",
            "helper": asdict(self.helper.status),
            "latency": self.latency.report(),
        }


def parent(output: Path) -> int:
    """Run inert cases with one event loop at a time and retain bounded receipts."""
    from PySide6.QtCore import QCoreApplication, QTimer, qVersion  # noqa: PLC0415

    output.mkdir(parents=True, exist_ok=False)
    application = QCoreApplication([])
    results = []
    for case_index, case in enumerate(CASES):
        with tempfile.TemporaryDirectory(
            prefix="verdiclip-pipe-proof-", ignore_cleanup_errors=True
        ) as temporary:
            session = Session(application, case, Path(temporary))
            QTimer.singleShot(0, session.start)
            application.exec()
            result = session.result()
            result["launch_order"] = case_index
            result["launch_context"] = (
                "first-in-process" if case_index == 0 else "later-in-process"
            )
            results.append(result)
            (output / f"{case}.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            if not session.helper.status.cleanup_confirmed:
                break
    passed = all(result["passed"] is True for result in results)
    (output / "receipt.json").write_text(
        json.dumps(
            {
                "passed": passed,
                "qt_version": qVersion(),
                "latency_note": (
                    "Observation only; first launch in this process is labeled, "
                    "but OS cache state is not controlled. "
                    "Deadlines and admission gates are unchanged."
                ),
                "python": sys.version,
                "pid": os.getpid(),
                "cases": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return int(not passed)


def main() -> int:
    """Select isolated child mode or the explicit Windows-only native proof."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--pipe")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This native proof requires Windows")
    if args.child:
        if not args.pipe or not args.case:
            parser.error("Child requires --pipe and --case")
        child(args.pipe, args.case)
        return 0
    if args.output is None:
        parser.error("Parent requires --output")
    return parent(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
