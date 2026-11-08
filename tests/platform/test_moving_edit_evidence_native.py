"""Portable fake-ABI checks for the independent evidence native transport."""

from __future__ import annotations

import ctypes
import hashlib
import io
from collections import deque
from collections.abc import Generator
from pathlib import Path
from typing import Any, BinaryIO, Protocol, TextIO, cast
from unittest.mock import ANY, Mock

import pytest

from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform import moving_edit_protocol as wire


class _Output(Protocol):
    _obj: Any


@pytest.fixture
def abi(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Fake only the Win32 ABI; exercise real endpoint ownership and framing."""
    api = Mock()
    api.CreateEventW.side_effect = range(100, 200)
    api.CloseHandle.return_value = True
    api.CancelIoEx.return_value = True
    api.GetOverlappedResult.return_value = False
    errors = deque([997, 996, 995] + [996] * 100)
    monkeypatch.setattr(peer.ctypes, "get_last_error", errors.popleft, raising=False)
    monkeypatch.setattr(
        peer.ctypes, "WinError", lambda code: OSError(code, "fake ABI"), raising=False
    )
    return api


def test_pending_read_is_nonblocking_and_cancelled_before_handles_close(
    abi: Mock,
) -> None:
    """A pending read never blocks watchdog polling and close cancels its buffer."""
    abi.ReadFile.return_value = False
    endpoint = peer._EvidenceEndpoint(abi, 77)
    endpoint.start_read(16)
    assert endpoint.poll_read() is None
    abi.GetOverlappedResult.assert_called_with(77, ANY, ANY, 0)
    endpoint.close()
    assert abi.CancelIoEx.call_count == 1
    assert [call.args[0] for call in abi.CloseHandle.call_args_list] == [77, 100]
    endpoint.close()
    assert abi.CloseHandle.call_count == 2


def test_partial_overlapped_reads_and_writes_do_not_drop_bytes(abi: Mock) -> None:
    """Exact one-record transfers survive partial immediate ABI completions."""
    incoming = bytearray(b"abcdef")
    outgoing = bytearray()

    def read(
        _h: int,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        count: _Output,
        _ov: object,
    ) -> bool:
        data = bytes(incoming[: min(size, 2)])
        del incoming[: len(data)]
        ctypes.memmove(buffer, data, len(data))
        count._obj.value = len(data)
        return True

    def write(
        _h: int,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        count: _Output,
        _ov: object,
    ) -> bool:
        amount = min(size, 2)
        outgoing.extend(ctypes.string_at(buffer, amount))
        count._obj.value = amount
        return True

    abi.ReadFile.side_effect = read
    abi.WriteFile.side_effect = write
    endpoint = peer._EvidenceEndpoint(abi, 77)
    endpoint.start_read(6)
    assert endpoint.poll_read() == b"ab"
    endpoint.start_write(b"abcdef")
    while not endpoint.poll_write():
        pass
    assert outgoing == b"abcdef"
    endpoint.close()


@pytest.mark.parametrize("size", [0, True, 4097])
def test_read_bound_is_checked_before_abi(abi: Mock, size: int) -> None:
    """The evidence adapter has one fixed 4096-byte allocation ceiling."""
    endpoint = peer._EvidenceEndpoint(abi, 77)
    with pytest.raises(ValueError, match=r".+"):
        endpoint.start_read(size)
    abi.ReadFile.assert_not_called()
    endpoint.close()


def test_reader_launch_has_no_path_or_dispatch_surface() -> None:
    """The fixed role accepts only identity/session/deadline data."""
    value = peer.EvidenceReaderLaunch("a" * 32, 7, 99, 30_000_000_000)
    assert peer.deserialize_reader_launch(value.serialize()) == value
    with pytest.raises(ValueError, match=r".+"):
        peer.deserialize_reader_launch(value.serialize()[:-1] + ',"path":"x"}')


class FakeEvidenceKernel:
    """Two byte pipes modeled at the Win32 ABI, with real overlapped adapters."""

    def __init__(self) -> None:
        self.error = 0
        self.events = 300
        self.queues = {
            101: bytearray(),
            102: bytearray(),
            201: bytearray(),
            202: bytearray(),
        }
        self.other = {101: 102, 102: 101, 201: 202, 202: 201}
        self.pending: dict[int, tuple[int, ctypes.Array[ctypes.c_char], int]] = {}
        self.aborted: set[int] = set()
        self.closed: set[int] = set()
        self.sent: list[tuple[int, bytes]] = []
        self.pump = lambda: None
        self.identities = {7: 70, 50: 500, 30: 3000, 9: 90}
        self.image = "C:\\Python\\python.exe"

    def CreateEventW(self, *_args: object) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the CreateEventW ABI without a real native operation."""
        self.events += 1
        return self.events

    def CreateFileW(self, name: str, *_args: object) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the CreateFileW ABI without a real native operation."""
        return 202 if "-parent-" in name else 102

    def ConnectNamedPipe(self, *_args: object) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the ConnectNamedPipe ABI without a real native operation."""
        return 1

    def OpenProcess(self, _flags: int, _inherit: int, pid: int) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the OpenProcess ABI without a real native operation."""
        return pid + 1000

    def GetCurrentProcess(self) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the GetCurrentProcess ABI without a real native operation."""
        return 1050

    def GetProcessId(self, handle: int) -> int:  # noqa: N802 - exact Win32 ABI
        """Read the process identity from the same retained fake handle."""
        return handle - 1000

    def GetExitCodeProcess(self, _handle: int, code: _Output) -> int:  # noqa: N802 - exact Win32 ABI
        """Return a successful natural process exit for final EOF checks."""
        code._obj.value = 0
        return 1

    def GetProcessTimes(self, handle: int, creation: _Output, *_args: object) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the GetProcessTimes ABI without a real native operation."""
        creation._obj.value = self.identities[handle - 1000]
        return 1

    def QueryFullProcessImageNameW(  # noqa: N802 - exact Win32 ABI
        self,
        _handle: int,
        _flags: int,
        buffer: ctypes.Array[ctypes.c_wchar],
        size: _Output,
    ) -> int:
        """Model the QueryFullProcessImageNameW ABI without a real native operation."""
        buffer.value = self.image
        size._obj.value = len(self.image)
        return 1

    def GetNamedPipeServerProcessId(self, _handle: int, pid: _Output) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the GetNamedPipeServerProcessId ABI without a real native operation."""
        pid._obj.value = 30
        return 1

    def GetNamedPipeClientProcessId(self, handle: int, pid: _Output) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the GetNamedPipeClientProcessId ABI without a real native operation."""
        pid._obj.value = 7 if handle == 201 else 50
        return 1

    def _read(
        self,
        handle: int,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        count: _Output,
    ) -> bool:
        amount = min(size, 7, len(self.queues[handle]))
        if not amount and self.other[handle] not in self.closed:
            return False
        data = bytes(self.queues[handle][:amount])
        del self.queues[handle][:amount]
        ctypes.memmove(buffer, data, amount)
        count._obj.value = amount
        return True

    def ReadFile(  # noqa: N802 - exact Win32 ABI
        self,
        handle: int,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        count: _Output,
        overlapped: _Output,
    ) -> int:
        """Model the ReadFile ABI without a real native operation."""
        if self._read(handle, buffer, size, count):
            return 1
        self.pending[id(overlapped._obj)] = (handle, buffer, size)
        self.error = 997
        return 0

    def WriteFile(  # noqa: N802 - exact Win32 ABI
        self,
        handle: int,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        count: _Output,
        _overlapped: _Output,
    ) -> int:
        """Model the WriteFile ABI without a real native operation."""
        amount = min(size, 7)
        data = ctypes.string_at(buffer, amount)
        self.queues[self.other[handle]].extend(data)
        self.sent.append((handle, data))
        count._obj.value = amount
        return 1

    def GetOverlappedResult(  # noqa: N802 - exact Win32 ABI
        self, _handle: int, overlapped: _Output, count: _Output, wait: int
    ) -> int:
        """Model the GetOverlappedResult ABI without a real native operation."""
        assert not wait
        key = id(overlapped._obj)
        if key in self.aborted:
            self.error = 995
            return 0
        handle, buffer, size = self.pending[key]
        if self._read(handle, buffer, size, count):
            del self.pending[key]
            return 1
        self.error = 996
        return 0

    def CancelIoEx(self, _handle: int, overlapped: _Output) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the CancelIoEx ABI without a real native operation."""
        key = id(overlapped._obj)
        self.pending.pop(key, None)
        self.aborted.add(key)
        return 1

    def CloseHandle(self, handle: int) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the CloseHandle ABI without a real native operation."""
        self.closed.add(handle)
        return 1

    def WaitForSingleObject(self, _handle: int, milliseconds: int) -> int:  # noqa: N802 - exact Win32 ABI
        """Model the WaitForSingleObject ABI without a real native operation."""
        if milliseconds:
            self.pump()
        return 258


class EvidenceLoopback:
    """Actual producer/client/native ABI/reader/ACK path without real Windows."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        now: list[int],
        launch: peer.MovingEditLaunch,
    ) -> None:
        self.now, self.launch = now, launch
        self.kernel = FakeEvidenceKernel()
        monkeypatch.setattr(peer, "_evidence_api", lambda: self.kernel)
        monkeypatch.setattr(
            peer.ctypes, "get_last_error", lambda: self.kernel.error, raising=False
        )
        monkeypatch.setattr(
            peer.ctypes,
            "WinError",
            lambda code: OSError(code, "fake ABI"),
            raising=False,
        )
        monkeypatch.setattr(peer.os, "getpid", lambda: 50)
        monkeypatch.setattr(
            peer, "_base_executable", lambda image=self.kernel.image: image
        )
        expected = peer.EvidenceIdentity(
            launch.evidence_token,
            launch.epoch,
            launch.sequence_started_ns,
            launch.sequence_deadline_ns,
            50,
            500,
            self.kernel.image,
            7,
            70,
            self.kernel.image,
        )
        self.state = peer.EvidenceReaderState(expected)
        self.reader = object.__new__(peer._EvidenceReader)
        self.reader.launch = peer.EvidenceReaderLaunch(
            launch.evidence_token, 9, 90, launch.sequence_deadline_ns
        )
        self.reader.outer = None
        self.reader.state = self.state
        self.reader.raw = io.BytesIO()
        self.reader.parent_raw = io.BytesIO()
        self.published: list[dict[str, object]] = []
        monkeypatch.setattr(
            self.reader,
            "publish",
            lambda **_kw: self.published.append(self.state.status),
        )
        self.peer = peer._ReaderConnection(
            peer._EvidenceEndpoint(self.kernel, 101), parent=False
        )
        self.parent = peer._ReaderConnection(
            peer._EvidenceEndpoint(self.kernel, 201), parent=True
        )
        self.peer.pin(expected, self.state, now[0])
        self.parent.pin(expected, self.state, now[0])
        self.reader.peer, self.reader.parent = self.peer, self.parent
        self.kernel.pump = self.pump
        self.parent_client = peer.EvidenceParentClient(
            launch, reader_executable=self.kernel.image, check=lambda: None
        )
        self.context: dict[str, Any] | None = None
        self.last_context = 0
        self.deferred_completion = ""
        self.deferred_answer = bytearray()

    def pump(self) -> None:
        """Advance both real reader endpoints without a native thread."""
        for endpoint in (self.parent, self.peer):
            frame = endpoint.poll()
            if frame is not None:
                if endpoint.parent:
                    self.reader._parent_record(frame, self.now[0])
                else:
                    self.reader._peer_record(frame, self.now[0])
        self.reader._parent_ack()
        self._deferred_progress()

    def _deferred_progress(self) -> None:
        endpoint = self.parent_client.endpoint
        assert endpoint is not None
        if self.deferred_completion == "writing":
            if not endpoint.poll_write():
                return
            endpoint.start_read(80)
            self.deferred_completion = "reading"
        if self.deferred_completion == "reading":
            data = endpoint.poll_read()
            if data is None:
                return
            assert data
            self.deferred_answer.extend(data)
            if len(self.deferred_answer) < 80:
                endpoint.start_read(80 - len(self.deferred_answer))
                return
            self.parent_client.producer.acknowledge(
                bytes(self.deferred_answer), self.now[0]
            )
            self.deferred_answer.clear()
            self.deferred_completion = ""

    def context_for(self, request: peer.Message) -> None:
        """Send the independent original request metadata before peer dispatch."""
        while self.deferred_completion:
            self.pump()
        operation = {
            "moving_begin": "begin",
            "move_next": "move",
            "observe": "observe",
            "moving_finish": "finish",
        }.get(cast("str", request.payload["operation"]))
        if operation is None:
            raise peer.ProtocolError("invalid-command-operation")
        requested = request.payload.get(
            "requested_ns", request.payload.get("command_started_ns")
        )
        deadline = request.payload.get(
            "deadline_ns", request.payload.get("command_deadline_ns")
        )
        if operation in ("begin", "finish"):
            assert type(requested) is int
            assert type(deadline) is int
        self.context = {
            "schema": 1,
            "session": self.launch.evidence_token,
            "epoch": self.launch.epoch,
            "context_id": request.request_id,
            "operation": operation,
            "request_id": request.request_id,
            "step_id": request.payload.get("step_id", 0),
            "command_sha256": hashlib.sha256(peer.encode(request)[4:]).hexdigest(),
        }
        packet = {**self.context, "requested_ns": requested, "deadline_ns": deadline}
        if operation == "finish":
            packet["response_deadline_ns"] = deadline
            packet["deadline_ns"] = min(
                cast("int", requested) + 5_000_000_000, self.launch.sequence_deadline_ns
            )
        self.parent_client.send(packet, deadline_ns=cast("int", deadline))
        self.last_context = request.request_id

    def complete(self, *, deferred: bool = False) -> None:
        """Send matching parent completion, including the actual retirement stamp."""
        assert self.context is not None
        packet = {**self.context, "completed_ns": self.now[0]}
        if self.context["operation"] == "finish":
            packet.update(
                retirement_started_ns=self.now[0],
                retirement_deadline_ns=min(
                    self.now[0] + 5_000_000_000, self.launch.sequence_deadline_ns
                ),
            )
        if deferred:
            frame = self.parent_client.producer.prepare(
                packet, self.now[0], self.launch.sequence_deadline_ns
            )
            assert self.parent_client.endpoint is not None
            self.parent_client.endpoint.start_write(frame)
            self.deferred_completion = "writing"
        else:
            self.parent_client.send(
                packet, deadline_ns=self.launch.sequence_deadline_ns
            )

    def audit(self) -> None:
        """Replay the stored complete transcript after actual modeled peer EOF."""
        self.state.peer_eof(self.now[0])
        replay = wire.EvidenceCorrelatedReplay(self.state.expected)
        parent_raw = self.reader.parent_raw.getvalue()
        for offset in range(0, len(parent_raw), 1024):
            replay.feed_parent(parent_raw[offset : offset + 1024])
        replay.parent_eof()
        raw = self.reader.raw.getvalue()
        for offset in range(0, len(raw), 4096):
            replay.feed_peer(raw[offset : offset + 4096])
        replay.peer_eof()
        self.state.verify_correlated_replay(replay)


def launch_data() -> peer.MovingEditLaunch:
    """Return strict original stamps and the exact independent reader binding."""
    return peer.MovingEditLaunch(
        "VerdiClip-moving-edit-" + "a" * 32,
        "b" * 32,
        7,
        1_000_000_000,
        31_000_000_000,
        "c" * 32,
        30,
        3000,
    )


def test_actual_native_frame_raw_flush_ack_precedes_call_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual original call events traverse native framing and independent flush."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": now[0],
            "command_deadline_ns": now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    journal.phase_enter(request)
    journal.emit(
        {
            "event": "call_start",
            "name": "GetClientRect",
            "effective_deadline_ns": now[0] + 2_000_000_000,
        },
        timestamp_ns=now[0],
    )
    assert len(loop.state.calls) == 1
    assert loop.published[-1]["pending_calls"]
    assert loop.reader.raw.getvalue()
    assert journal.producer.pending is None
    # This marker models the native invocation after raw()'s evidence callback.
    dispatched = ["GetClientRect"]
    now[0] += 1
    journal.emit({"event": "call_end", "seconds": 0.000000001}, timestamp_ns=now[0])
    assert dispatched == ["GetClientRect"]
    assert not loop.state.calls
    journal.phase_end()
    loop.complete()
    journal.close()
    loop.parent_client.close()


@pytest.mark.parametrize("changed", ["creation", "image", "dead", "server"])
def test_client_pins_full_kernel_identity_before_any_evidence(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """A claimed PID, reused PID, basename match or dead reader grants nothing."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    before = len(loop.kernel.sent)
    if changed == "creation":
        loop.kernel.identities[30] += 1
    elif changed == "image":
        loop.kernel.image = "C:\\Other\\python.exe"
    elif changed == "dead":
        monkeypatch.setattr(loop.kernel, "WaitForSingleObject", lambda *_a: 0)
    else:

        def wrong_server(_handle: int, value: _Output) -> int:
            value._obj.value = 31
            return 1

        monkeypatch.setattr(loop.kernel, "GetNamedPipeServerProcessId", wrong_server)
    with pytest.raises(ValueError, match="evidence-"):
        peer._Journal(loop.launch, lambda: None)
    assert len(loop.kernel.sent) == before
    assert 1030 in loop.kernel.closed


def test_delayed_ack_at_exact_call_deadline_never_reaches_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted evidence without an on-time matching ACK is not native admission."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": now[0],
            "command_deadline_ns": now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    journal.phase_enter(request)
    deadline = now[0] + 2_000_000_000
    original = loop.kernel.pump

    def delayed() -> None:
        original()
        if loop.state.calls:
            now[0] = deadline

    loop.kernel.pump = delayed
    dispatched: list[str] = []

    def dispatch() -> None:
        journal.emit(
            {
                "event": "call_start",
                "name": "GetClientRect",
                "effective_deadline_ns": deadline,
            },
            timestamp_ns=now[0],
        )
        dispatched.append("native")

    with pytest.raises(ValueError, match="expired"):
        dispatch()
    assert not dispatched
    assert journal.failed
    before = len(loop.kernel.sent)
    with pytest.raises(ValueError, match="journal-failed"):
        journal.emit(
            {
                "event": "call_start",
                "name": "GetClientRect",
                "effective_deadline_ns": deadline,
            },
            timestamp_ns=now[0],
        )
    assert len(loop.kernel.sent) == before


def test_pending_native_watchdog_runs_without_any_more_pipe_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung target call cannot hide behind an independent pending native read."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": now[0],
            "command_deadline_ns": now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    journal.phase_enter(request)
    deadline = now[0] + 2_000_000_000
    journal.emit(
        {
            "event": "call_start",
            "name": "GetClientRect",
            "effective_deadline_ns": deadline,
        },
        timestamp_ns=now[0],
    )
    now[0] = deadline
    with pytest.raises(ValueError, match="deadline"):
        loop.reader.check()
    assert loop.state.failed


def test_cancel_failure_closes_pipe_and_preserves_inflight_buffer(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown cancellation cannot free an OS-owned buffer or qualify retirement."""
    abi.ReadFile.return_value = False
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 997, raising=False)
    endpoint = peer._EvidenceEndpoint(abi, 77)
    endpoint.start_read(16)
    operation = endpoint.reading
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 996, raising=False)
    retained: list[object] = []
    monkeypatch.setattr(peer, "_unretired_evidence_io", retained)
    with pytest.raises(ValueError, match="cancel-not-retired"):
        endpoint.close()
    assert retained == [operation]
    abi.CloseHandle.assert_called_once_with(77)
    with pytest.raises(ValueError, match="unretired-io"):
        peer._evidence_api()


def test_failed_read_start_closes_event_without_cancelling_unsubmitted_io(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synchronous API failure never claims an overlapped request was submitted."""
    abi.ReadFile.return_value = False
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    endpoint = peer._EvidenceEndpoint(abi, 77)
    with pytest.raises(OSError, match="fake ABI"):
        endpoint.start_read(16)
    endpoint.close()
    abi.CancelIoEx.assert_not_called()
    assert [call.args[0] for call in abi.CloseHandle.call_args_list] == [77, 100]


def test_fixed_expected_artifact_is_one_shot_and_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only the fixed outer file can bind identity; replacement is permanent failure."""
    monkeypatch.chdir(tmp_path)
    reader = object.__new__(peer._EvidenceReader)
    reader.launch = peer.EvidenceReaderLaunch("c" * 32, 9, 90, 49_000_000_000)
    reader.expected = reader.state = reader.peer = reader.parent = None
    reader.expected_fingerprint = None
    reader._expected()
    assert reader.expected is None
    expected = peer.EvidenceIdentity(
        "c" * 32,
        "b" * 32,
        1_000_000_000,
        31_000_000_000,
        50,
        500,
        "C:\\Python\\python.exe",
        7,
        70,
        "C:\\Python\\python.exe",
    )
    target = tmp_path / "moving-evidence-expected.json"
    target.write_bytes(peer.canonical_json(expected.value))
    reader._expected()
    assert reader.expected == expected
    target.write_bytes(peer.canonical_json(expected.value) + b" ")
    with pytest.raises(ValueError, match="identity-changed"):
        reader._expected()


def test_raw_storage_failure_publishes_failed_status_without_success_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The fixed reader role preserves failure while still closing every owner."""
    monkeypatch.chdir(tmp_path)
    launch = peer.EvidenceReaderLaunch("c" * 32, 9, 90, 49_000_000_000)
    state = Mock()
    state.fail.side_effect = peer.ProtocolError("first failure")
    reader = Mock()
    reader.state = state
    reader.run.side_effect = OSError("flush failed")
    reader.packet.return_value = {
        "schema": 1,
        "stage": "failed",
        "failure": "flush failed",
    }
    monkeypatch.setattr(peer, "_EvidenceReader", lambda _launch: reader)
    with pytest.raises(OSError, match="flush failed"):
        peer.run_evidence_reader(launch.serialize())
    reader.close.assert_called_once()
    status = peer.parse_json(
        (tmp_path / "moving-evidence-status.json").read_bytes(), 16384
    )
    assert status["stage"] == "failed"
    assert not (tmp_path / "moving-evidence-final.json").exists()


@pytest.mark.parametrize("changed", ["none", "creation", "pid", "exit", "wait"])
def test_final_retained_identity_does_not_requery_image_after_exit(
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    """The same pinned handle retains live-image proof after natural peer exit."""
    kernel = FakeEvidenceKernel()
    pinned = peer._EvidenceProcess(kernel, 50, 500, kernel.image)
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "ABI failure"),
        raising=False,
    )
    image = Mock(side_effect=OSError("image query unavailable after exit"))
    monkeypatch.setattr(kernel, "QueryFullProcessImageNameW", image)
    monkeypatch.setattr(kernel, "WaitForSingleObject", lambda *_a: 0)
    if changed == "creation":
        kernel.identities[50] += 1
    elif changed == "pid":
        monkeypatch.setattr(kernel, "GetProcessId", lambda *_a: 51)
    elif changed == "exit":

        def failed_exit(_handle: int, code: _Output) -> int:
            code._obj.value = 1
            return 1

        monkeypatch.setattr(kernel, "GetExitCodeProcess", failed_exit)
    elif changed == "wait":
        monkeypatch.setattr(kernel, "WaitForSingleObject", lambda *_a: 0xFFFFFFFF)
    if changed == "none":
        pinned.check(allow_exit=True)
    else:
        with pytest.raises((ValueError, OSError), match=r".+"):
            pinned.check(allow_exit=True)
    image.assert_not_called()
    pinned.close()
    assert 1050 in kernel.closed


def test_fixed_server_is_current_user_local_one_instance_overlapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both evidence endpoints retain exact one-instance buffers and noninheritance."""
    api = Mock()
    api.CreateNamedPipeW.return_value = 77
    api.LocalFree.return_value = None
    descriptor = ctypes.c_void_p(99)
    attributes = peer._EvidenceSecurityAttributes(
        ctypes.sizeof(peer._EvidenceSecurityAttributes), descriptor, 0
    )
    monkeypatch.setattr(
        peer, "_current_user_security", lambda _api: (attributes, descriptor)
    )
    for parent in (False, True):
        endpoint = peer._evidence_server(api, "c" * 32, parent=parent)
        arguments = api.CreateNamedPipeW.call_args.args
        assert arguments[0] == "\\\\.\\pipe\\" + peer.evidence_pipe_name(
            "c" * 32, parent=parent
        )
        assert arguments[1:7] == (0x40080003, 8, 1, 65536, 65536, 0)
        assert arguments[7]._obj.bInheritHandle == 0
        endpoint.close()
    assert api.LocalFree.call_count == 2


@pytest.mark.parametrize("delayed_identity", [False, True])
@pytest.mark.parametrize(
    "audit_fault",
    [
        "none",
        "cancel",
        "corrupt",
        "parent-corrupt",
        "parent-truncated",
        "parent-changed",
        "parent-final-hash",
        "parent-write",
        "parent-short",
        "parent-flush",
        "parent-delay",
        "status-fsync-expired",
        "status-replace-expired",
        "final-fsync-expired",
        "final-replace-expired",
        "final-replace-cancelled",
        "final-parent-query-expired",
        "final-replace-expired-status-failed",
    ],
)
def test_actual_fixed_reader_pumps_raw_ack_eof_and_streaming_final_audit(  # noqa: C901, PLR0912, PLR0915 - full independent reader lifecycle
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    delayed_identity: bool,
    audit_fault: str,
) -> None:
    """The real reader entrypoint owns fixed files and two live ABI endpoints."""
    now = 1_000_000_000
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now)
    monkeypatch.chdir(tmp_path)
    kernel = FakeEvidenceKernel()
    monkeypatch.setattr(peer, "_evidence_api", lambda: kernel)
    monkeypatch.setattr(peer, "_base_executable", lambda: kernel.image)
    monkeypatch.setattr(peer.os, "getpid", lambda: 30)
    monkeypatch.setattr(kernel, "GetCurrentProcess", lambda: 1030)
    monkeypatch.setattr(
        peer.ctypes, "get_last_error", lambda: kernel.error, raising=False
    )
    monkeypatch.setattr(
        peer.ctypes, "WinError", lambda code: OSError(code, "fake ABI"), raising=False
    )
    monkeypatch.setattr(
        peer,
        "_evidence_server",
        lambda _api, _token, *, parent: peer._EvidenceEndpoint(
            kernel, 201 if parent else 101
        ),
    )
    expected = peer.EvidenceIdentity(
        "c" * 32,
        "b" * 32,
        now,
        31_000_000_000,
        50,
        500,
        kernel.image,
        7,
        70,
        kernel.image,
    )
    expected_path = tmp_path / "moving-evidence-expected.json"
    if not delayed_identity:
        expected_path.write_bytes(peer.canonical_json(expected.value))
    producers = {
        False: wire.EvidenceProducer(expected.session, expected.epoch),
        True: wire.EvidenceProducer(expected.session, expected.epoch, parent=True),
    }
    endpoints = {
        False: peer._EvidenceEndpoint(kernel, 102),
        True: peer._EvidenceEndpoint(kernel, 202),
    }

    def packets() -> Generator[tuple[bool, dict[str, Any]]]:
        yield False, wire.make_evidence_hello(launch_data(), 50, 500)
        for index in range(1, 17):
            operation, step = (
                ("begin", 0)
                if index == 1
                else (
                    ("finish", 0)
                    if index == 16
                    else ("move" if index % 2 == 0 else "observe", index // 2)
                )
            )
            correlation = {
                "context_id": index,
                "operation": operation,
                "request_id": index,
                "step_id": step,
                "command_sha256": "a" * 64,
            }
            context = dict(
                schema=1, session=expected.session, epoch=expected.epoch, **correlation
            )
            entry = {**context, "requested_ns": now, "deadline_ns": now + 2_000_000_000}
            if operation == "finish":
                entry.update(
                    deadline_ns=now + 5_000_000_000,
                    response_deadline_ns=now + 2_000_000_000,
                )
            yield True, entry
            yield False, {"event": "phase_enter", **correlation}
            seek = None
            if operation == "move":
                seek = {
                    "event": "seek_start",
                    "vertical": 100.0 * (3, 0, 8, 13, 13, 0, 3)[step - 1] / 13,
                    "horizontal": -1.0,
                    "restoring": step == 7,
                }
                yield False, seek
            if operation == "finish":
                yield False, {"event": "finish_response_sent", **correlation}
                yield (
                    True,
                    {
                        **context,
                        "completed_ns": now,
                        "retirement_started_ns": now,
                        "retirement_deadline_ns": now + 5_000_000_000,
                    },
                )
            yield (
                False,
                {
                    "event": "call_start",
                    "name": "CloseHandle" if operation == "finish" else "GetClientRect",
                    "effective_deadline_ns": now + 1_000_000_000,
                },
            )
            yield False, {"event": "call_end", "seconds": 0}
            if operation == "move":
                assert seek is not None
                yield False, dict(seek, event="seek_end", status=0)
            yield False, {"event": "phase_end", **correlation, "completed_ns": now}
            if operation != "finish":
                yield True, {**context, "completed_ns": now}
        yield False, producers[False].final_event()

    script = packets()
    parent_frames: list[bytes] = []
    active: bool | None = None
    reading = False
    answer = bytearray()
    exhausted = False

    def drive_producers() -> None:
        nonlocal active, reading, exhausted
        if delayed_identity and not expected_path.exists():
            # This real client constructor returns after kernel connection and
            # server pinning, without waiting for peer identity or a context ACK.
            early_parent = peer.EvidenceParentClient(
                launch_data(), reader_executable=kernel.image, check=lambda: None
            )
            assert early_parent.endpoint is not None
            endpoints[True] = early_parent.endpoint
            assert (tmp_path / "moving-evidence-status.json").exists()
            expected_path.write_bytes(peer.canonical_json(expected.value))
        if exhausted:
            return
        if active is None:
            try:
                active, event = next(script)
            except StopIteration:
                exhausted = True
                endpoints[False].close()
                return
            frame = producers[active].prepare(event, now, 31_000_000_000)
            if active:
                parent_frames.append(frame)
            endpoints[active].start_write(frame)
        endpoint = endpoints[active]
        if not reading:
            if not endpoint.poll_write():
                return
            reading = True
            endpoint.start_read(80)
        data = endpoint.poll_read()
        if data is None:
            return
        assert data
        answer.extend(data)
        if len(answer) < 80:
            endpoint.start_read(80 - len(answer))
            return
        producers[active].acknowledge(bytes(answer), now)
        answer.clear()
        active, reading = None, False

    kernel.pump = drive_producers
    publication_fault = audit_fault.startswith(("status-", "final-"))
    publication_changed = False
    if publication_fault:
        target = (
            "moving-evidence-status.json.tmp"
            if audit_fault.startswith("status-")
            else "moving-evidence-final.json.tmp"
        )

        def change_final_admission() -> None:
            nonlocal now, publication_changed
            publication_changed = True
            if audit_fault.endswith("cancelled"):
                kernel.closed.add(202)
            elif audit_fault != "final-parent-query-expired":
                now = 6_000_000_000  # Original context16 cleanup bound, exactly.

        if audit_fault == "final-parent-query-expired":
            original_query = kernel.GetProcessTimes

            def final_parent_query(
                handle: int, creation: _Output, *args: object
            ) -> int:
                nonlocal now
                result = original_query(handle, creation, *args)
                if publication_changed and handle == 1007:
                    now = 6_000_000_000
                return result

            monkeypatch.setattr(kernel, "GetProcessTimes", final_parent_query)

        if "fsync" in audit_fault:
            original_fsync = peer.os.fsync

            def final_fsync(descriptor: int) -> None:
                original_fsync(descriptor)
                path = tmp_path / target
                if (
                    not publication_changed
                    and path.exists()
                    and wire.parse_json(path.read_bytes(), 16384)["stage"] == "complete"
                ):
                    change_final_admission()

            monkeypatch.setattr(peer.os, "fsync", final_fsync)
        else:
            original_replace = Path.replace

            def final_replace(path: Path, destination: Path) -> Path:
                if (
                    audit_fault == "final-replace-expired-status-failed"
                    and publication_changed
                    and path.name == "moving-evidence-status.json.tmp"
                ):
                    raise OSError("failure-status replacement failed")
                complete = (
                    path.name == target
                    and wire.parse_json(path.read_bytes(), 16384)["stage"] == "complete"
                )
                result = original_replace(path, destination)
                if complete and not publication_changed:
                    change_final_admission()
                return result

            monkeypatch.setattr(Path, "replace", final_replace)
    storage_fault = audit_fault in {
        "parent-write",
        "parent-short",
        "parent-flush",
        "parent-delay",
    }
    if storage_fault:
        original_open = Path.open

        class FailingParentStorage:
            def __init__(self, raw: BinaryIO) -> None:
                self.raw = raw
                self.count = 0

            def write(self, frame: bytes) -> int:
                self.count += 1
                if self.count == 3:
                    if audit_fault == "parent-write":
                        raise OSError("parent-storage write failure")
                    if audit_fault == "parent-short":
                        return self.raw.write(frame[:1])
                return self.raw.write(frame)

            def flush(self) -> None:
                nonlocal now
                self.raw.flush()
                if self.count == 3:
                    if audit_fault == "parent-flush":
                        raise OSError("parent-storage flush failure")
                    now = 3_000_000_000

            def close(self) -> None:
                self.raw.close()

        def storage_open(  # noqa: PLR0913, PLR0917 - preserve Path.open's exact interface
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> BinaryIO | TextIO | FailingParentStorage:
            raw_file = original_open(path, mode, buffering, encoding, errors, newline)
            if path.name == "moving-evidence-parent.raw" and mode == "xb":
                return FailingParentStorage(cast("BinaryIO", raw_file))
            return cast("BinaryIO | TextIO", raw_file)

        monkeypatch.setattr(Path, "open", storage_open)
    if audit_fault != "none":
        original_audit = peer._EvidenceReader._audit

        def changed_storage_or_parent(reader: peer._EvidenceReader) -> None:
            if audit_fault == "cancel":
                kernel.closed.add(202)
            elif audit_fault == "corrupt":
                with (tmp_path / "moving-evidence.raw").open("r+b") as raw_file:
                    raw_file.write(b"\xff")
            elif audit_fault == "parent-corrupt":
                with (tmp_path / "moving-evidence-parent.raw").open("r+b") as raw_file:
                    raw_file.write(b"\xff")
            elif audit_fault == "parent-truncated":
                with (tmp_path / "moving-evidence-parent.raw").open("r+b") as raw_file:
                    raw_file.truncate(1)
            elif audit_fault == "parent-final-hash":
                # A later final completion header is individually admissible and
                # correlation-compatible. Exact live-storage hash still binds it.
                path = tmp_path / "moving-evidence-parent.raw"
                raw_parent = path.read_bytes()
                final = parent_frames[-1]
                size, record_id, stamp = peer.struct.unpack(">IIQ", final[:16])
                changed = (
                    peer.struct.pack(">IIQ", size, record_id, stamp + 1) + final[16:]
                )
                path.write_bytes(raw_parent[: -len(final)] + changed)
            else:
                # Still canonical and individually valid, but no longer the
                # independently accepted command digest or the live raw hash.
                path = tmp_path / "moving-evidence-parent.raw"
                raw_parent = path.read_bytes()
                path.write_bytes(raw_parent.replace(b"a" * 64, b"b" * 64))
            original_audit(reader)

        if not storage_fault and not publication_fault:
            monkeypatch.setattr(
                peer._EvidenceReader, "_audit", changed_storage_or_parent
            )
        with pytest.raises(
            (ValueError, OSError), match=r"evidence-|parent-|correlated-|invalid-fixed"
        ) as failure:
            peer.run_evidence_reader(
                peer.EvidenceReaderLaunch("c" * 32, 9, 90, 49_000_000_000).serialize()
            )
        status_path = tmp_path / "moving-evidence-status.json"
        if audit_fault == "final-replace-expired-status-failed":
            assert str(failure.value) == "evidence-reader-deadline"
            assert any("publication" in note for note in failure.value.__notes__)
            assert (
                wire.parse_json(status_path.read_bytes(), 16384)["stage"] == "complete"
            )
            status_path = tmp_path / "moving-evidence-status.json.tmp"
        failed = wire.parse_json(status_path.read_bytes(), 16384)
        assert failed["stage"] == "failed"
        if audit_fault == "parent-final-hash":
            assert "evidence-correlated-replay-mismatch" in failed["failure"]
        final_path = tmp_path / "moving-evidence-final.json"
        if audit_fault.startswith("final-"):
            # A persisted complete packet is retained as failed-run evidence,
            # never sufficient without live status, exit and original-clock gates.
            assert final_path.exists()
            assert failed["evidence"]["failed"]
            assert not failed["evidence"]["complete"]
        else:
            assert not final_path.exists()
        if publication_fault:
            assert publication_changed
        assert {101, 201, 1009, 1050, 1007} <= kernel.closed
        if storage_fault:
            raw_prefix = (tmp_path / "moving-evidence-parent.raw").read_bytes()
            accepted_prefix = b"".join(parent_frames[:2])
            assert raw_prefix.startswith(accepted_prefix)
            assert producers[True].count == 3
            assert producers[True].pending is not None  # Third packet was not ACKed.
            if audit_fault == "parent-write":
                assert raw_prefix == accepted_prefix
            elif audit_fault == "parent-short":
                assert raw_prefix == accepted_prefix + parent_frames[2][:1]
            else:
                assert raw_prefix == b"".join(parent_frames[:3])
        return
    peer.run_evidence_reader(
        peer.EvidenceReaderLaunch("c" * 32, 9, 90, 49_000_000_000).serialize()
    )
    final = wire.parse_json(
        (tmp_path / "moving-evidence-final.json").read_bytes(), 16384
    )
    assert final["stage"] == "complete"
    assert final["evidence"]["complete"]
    assert final["evidence"]["replay_verified"]
    assert final["evidence"]["correlated_replay_verified"]
    assert final["audit"]["audit_scope"] == "correlated-partial-order"
    assert not final["audit"]["arrival_order_reconstructed"]
    assert not final["audit"]["acknowledgments_reconstructed"]
    raw = (tmp_path / "moving-evidence.raw").read_bytes()
    assert len(raw) == producers[False].byte_count
    assert hashlib.sha256(raw).hexdigest() == producers[False].hexdigest
    parent_raw = (tmp_path / "moving-evidence-parent.raw").read_bytes()
    assert len(parent_raw) == producers[True].byte_count <= 32768
    assert final["evidence"]["parent_count"] == producers[True].count == 32
    assert final["evidence"]["parent_byte_count"] == len(parent_raw)
    assert final["evidence"]["parent_sha256"] == hashlib.sha256(parent_raw).hexdigest()
    assert final["audit"]["parent"]["sha256"] == producers[True].hexdigest
    assert {101, 201, 1009, 1050, 1007} <= kernel.closed
    assert not kernel.pending


def test_server_descriptor_failure_cannot_leak_successfully_created_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing finally step retains ownership until the new pipe is closed."""
    api = Mock()
    api.CreateNamedPipeW.return_value = 77
    api.LocalFree.return_value = 99
    api.CloseHandle.return_value = True
    descriptor = ctypes.c_void_p(99)
    attributes = peer._EvidenceSecurityAttributes(
        ctypes.sizeof(peer._EvidenceSecurityAttributes), descriptor, 0
    )
    monkeypatch.setattr(
        peer, "_current_user_security", lambda _api: (attributes, descriptor)
    )
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "descriptor failed"),
        raising=False,
    )
    with pytest.raises(OSError, match="descriptor failed"):
        peer._evidence_server(api, "c" * 32, parent=False)
    api.CloseHandle.assert_called_once_with(77)


def test_parent_artifact_collision_is_not_overwritten_or_redirected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The fixed parent file is exclusively owned, even on a partial startup."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    monkeypatch.chdir(tmp_path)
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    target = tmp_path / "moving-evidence-parent.raw"
    target.write_bytes(b"prior-owner-prefix")
    reader = peer._EvidenceReader(loop.reader.launch)
    with pytest.raises(FileExistsError):
        reader.run()
    raw = reader.raw
    reader.close()
    assert raw.closed
    assert reader.parent_raw is None
    assert target.read_bytes() == b"prior-owner-prefix"
    assert 1009 in loop.kernel.closed
    assert {path.name for path in tmp_path.iterdir()} == {
        "moving-evidence.raw",
        "moving-evidence-parent.raw",
    }


def test_parent_total_storage_bound_precedes_append_or_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exhausted parent byte allowance cannot append another accepted packet."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": now[0],
            "command_deadline_ns": now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    journal.phase_enter(request)
    journal.phase_end()
    prefix = loop.reader.parent_raw.getvalue()
    loop.state.parent_byte_count = wire.MAX_PARENT_EVIDENCE_BYTES
    with pytest.raises(ValueError, match="evidence-parent-byte-bound"):
        loop.complete()
    assert loop.reader.parent_raw.getvalue() == prefix
    assert loop.parent_client.producer.pending is not None
    assert loop.state.failed


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "open",
        "size-zero",
        "size-large",
        "token-read",
        "sid",
        "sid-empty",
        "sid-large",
        "descriptor",
        "sid-free",
        "token-close",
        "both-close",
    ],
)
def test_current_user_security_abi_cleanup_and_exact_protected_acl(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Security setup never broadens access or skips token/SID cleanup on error."""
    api, advapi = Mock(), Mock()
    api.GetCurrentProcess.return_value = 9
    api.CloseHandle.return_value = fault not in {"token-close", "both-close"}
    api.LocalFree.return_value = 99 if fault in {"sid-free", "both-close"} else None

    def open_token(_process: int, rights: int, output: _Output) -> int:
        assert rights == 8
        output._obj.value = 77
        return int(fault != "open")

    def token_info(
        _token: object, kind: int, buffer: object, _size: int, output: _Output
    ) -> int:
        assert kind == 1
        output._obj.value = (
            0 if fault == "size-zero" else (4097 if fault == "size-large" else 16)
        )
        return int(buffer is not None and fault != "token-read")

    def convert_sid(_sid: object, output: _Output) -> int:
        if fault == "sid":
            return 0
        output._obj.value = (
            ""
            if fault == "sid-empty"
            else ("x" * 257 if fault == "sid-large" else "S-1-5-21-123")
        )
        return 1

    def descriptor(sddl: str, revision: int, output: _Output, _size: object) -> int:
        assert sddl == "D:P(A;;GA;;;S-1-5-21-123)"
        assert revision == 1
        output._obj.value = 99
        return int(fault != "descriptor")

    advapi.OpenProcessToken.side_effect = open_token
    advapi.GetTokenInformation.side_effect = token_info
    advapi.ConvertSidToStringSidW.side_effect = convert_sid
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.side_effect = descriptor
    monkeypatch.setattr(peer.ctypes, "WinDLL", lambda *_a, **_kw: advapi, raising=False)
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "security ABI"),
        raising=False,
    )
    if fault == "none":
        attributes, owned = peer._current_user_security(api)
        assert attributes.bInheritHandle == 0
        assert owned.value == 99
        assert api.LocalFree.call_count == 1
    else:
        with pytest.raises((ValueError, OSError), match=r".+"):
            peer._current_user_security(api)
    assert api.CloseHandle.call_count == (0 if fault == "open" else 1)
    if fault in {"descriptor", "sid-free", "token-close", "both-close"}:
        assert api.LocalFree.call_count == 2


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        ("read", 5),
        ("read", 109),
        ("write", 5),
        ("connect", 5),
        ("connect", 535),
        ("connect", 997),
    ],
)
def test_overlapped_start_failure_and_fixed_connect_result(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error: int,
) -> None:
    """Immediate failures, EOF and already-connected native results remain distinct."""
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: error, raising=False)
    abi.ReadFile.return_value = abi.WriteFile.return_value = (
        abi.ConnectNamedPipe.return_value
    ) = False
    endpoint = peer._EvidenceEndpoint(abi, 77)
    if error == 5:

        def start() -> None:
            if operation == "read":
                endpoint.start_read(16)
            elif operation == "write":
                endpoint.start_write(b"frame")
            else:
                endpoint.start_connect()

        with pytest.raises(OSError, match="fake ABI"):
            start()
        endpoint.close()
        abi.CancelIoEx.assert_not_called()
    elif operation == "read":
        endpoint.start_read(16)
        assert endpoint.poll_read() == b""
        endpoint.close()
    else:
        endpoint.start_connect()
        if error == 535:
            assert endpoint.poll_connect()
        else:
            monkeypatch.setattr(
                peer.ctypes, "get_last_error", lambda: 996, raising=False
            )
            assert not endpoint.poll_connect()
            monkeypatch.setattr(
                peer.ctypes, "get_last_error", lambda: 995, raising=False
            )
        endpoint.close()


@pytest.mark.parametrize("operation", ["read", "write", "connect"])
def test_evidence_endpoint_rejects_second_outstanding_operation(
    abi: Mock,
    operation: str,
) -> None:
    """There is no native operation queue beyond the one owned read/write slot."""
    endpoint = peer._EvidenceEndpoint(abi, 77)
    if operation == "write":
        endpoint.start_write(b"first")
        with pytest.raises(ValueError, match="outstanding"):
            endpoint.start_write(b"second")
    else:
        endpoint.start_read(1)
        with pytest.raises(ValueError, match="outstanding"):
            endpoint.start_read(1) if operation == "read" else endpoint.start_connect()
    endpoint.close()


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("count", [0, 5])
def test_overlapped_completion_count_is_checked_before_buffer_admission(
    abi: Mock,
    operation: str,
    count: int,
) -> None:
    """Impossible native byte counts never become a frame or another write."""

    def completed(
        _handle: int, _buffer: object, _size: int, amount: _Output, _ov: object
    ) -> bool:
        amount._obj.value = count
        return True

    abi.ReadFile.side_effect = abi.WriteFile.side_effect = completed
    endpoint = peer._EvidenceEndpoint(abi, 77)
    if operation == "read":
        endpoint.start_read(4)
        if count == 0:
            assert endpoint.poll_read() == b""
        else:
            with pytest.raises(ValueError, match="read-count"):
                endpoint.poll_read()
    else:
        endpoint.start_write(b"abcd")
        with pytest.raises(ValueError, match="write-count"):
            endpoint.poll_write()
    endpoint.close()


def test_actual_kernel_function_signatures_are_explicit_and_noninheriting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32 pointers, DWORD widths and overlapped entrypoints are not defaults."""
    api = Mock()
    monkeypatch.setattr(peer.ctypes, "WinDLL", lambda *_a, **_kw: api, raising=False)
    assert peer._evidence_api() is api
    assert api.GetProcessId.restype is ctypes.c_uint32
    assert api.GetOverlappedResult.argtypes[-1] is peer.wintypes.BOOL
    assert len(api.CreateNamedPipeW.argtypes) == 8
    assert api.CancelIoEx.argtypes == [peer.wintypes.HANDLE, ctypes.c_void_p]


def test_reader_connection_failed_connect_retains_and_closes_endpoint(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructor failure cannot orphan a successfully created server pipe."""
    abi.ConnectNamedPipe.return_value = False
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(OSError, match="fake ABI"):
        peer._ReaderConnection(peer._EvidenceEndpoint(abi, 77), parent=False)
    assert [call.args[0] for call in abi.CloseHandle.call_args_list] == [77, 100]


@pytest.mark.parametrize(
    "fault", ["none", "incomplete", "trailing", "expired", "cancelled", "closed"]
)
def test_parent_final_eof_wait_is_bounded_and_has_no_extra_packet(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Reader EOF is awaited only after32 packets; cancellation or bytes fail it."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    client = loop.parent_client
    client.producer.count = 31 if fault == "incomplete" else 32
    before = len(loop.kernel.sent)
    if fault == "closed":
        client.close()
    elif fault == "trailing":
        loop.kernel.queues[202].extend(b"x")
    elif fault == "cancelled":

        def cancelled() -> None:
            raise ValueError("cancelled")

        client.check = cancelled
    else:

        def close_or_expire() -> None:
            if fault == "expired":
                now[0] = 2_000_000_000
            else:
                loop.kernel.closed.add(201)

        loop.kernel.pump = close_or_expire
    if fault == "none":
        client.finish(2_000_000_000)
        assert client.endpoint is None
    else:
        with pytest.raises(ValueError, match=r".+"):
            client.finish(2_000_000_000)
        assert client.failed
    assert len(loop.kernel.sent) == before


@pytest.mark.parametrize(
    ("operation", "outcome"),
    [
        (operation, outcome)
        for operation in ("read", "endpoint-close", "process-close")
        for outcome in ("expired", "cancelled")
    ]
    + [("endpoint-close", "close-failure"), ("process-close", "close-failure")],
)
def test_parent_final_native_completion_rechecks_original_admission(  # noqa: C901 - three exact native completion sites
    monkeypatch: pytest.MonkeyPatch, operation: str, outcome: str
) -> None:
    """A slow actual EOF read or owner close cannot return timely success."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    client = loop.parent_client
    client.producer.count = 32
    cancelled = False
    changed = False

    def check() -> None:
        if cancelled:
            raise ValueError("cancelled after native completion")

    def change_admission() -> None:
        nonlocal cancelled, changed
        changed = True
        if outcome == "cancelled":
            cancelled = True
        else:
            now[0] = 2_000_000_000

    client.check = check
    loop.kernel.pump = lambda: loop.kernel.closed.add(201)
    if operation == "read":
        original_read = loop.kernel.GetOverlappedResult

        def slow_read(
            handle: int, overlapped: _Output, count: _Output, wait: int
        ) -> int:
            result = original_read(handle, overlapped, count, wait)
            if handle == 202 and result:
                change_admission()
            return result

        monkeypatch.setattr(loop.kernel, "GetOverlappedResult", slow_read)
    else:
        original_close = loop.kernel.CloseHandle

        def slow_close(handle: int) -> int:
            result = original_close(handle)
            if handle == (202 if operation == "endpoint-close" else 1030):
                change_admission()
                if outcome == "close-failure":
                    loop.kernel.error = 5
                    return 0
            return result

        monkeypatch.setattr(loop.kernel, "CloseHandle", slow_close)
    image_query = Mock(side_effect=AssertionError("must not query a retired image"))
    monkeypatch.setattr(loop.kernel, "QueryFullProcessImageNameW", image_query)
    failure = OSError if outcome == "close-failure" else ValueError
    reason = "fake ABI" if outcome == "close-failure" else "expired|cancelled"
    with pytest.raises(failure, match=reason):
        client.finish(2_000_000_000)
    assert changed
    assert client.failed
    image_query.assert_not_called()
    client.close()
    assert {202, 1030} <= loop.kernel.closed


@pytest.mark.parametrize("ceiling", ["outer", "state"])
def test_reader_clock_refreshes_after_retained_outer_kernel_query(
    monkeypatch: pytest.MonkeyPatch, ceiling: str
) -> None:
    """A retained-handle query cannot conceal elapsed time in a stale sample."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    loop.reader.outer = peer._EvidenceProcess(loop.kernel, 9, 90, loop.kernel.image)
    original = loop.kernel.GetProcessTimes

    def slow_query(handle: int, creation: _Output, *args: object) -> int:
        result = original(handle, creation, *args)
        if handle == 1009:
            now[0] = 31_000_000_000 if ceiling == "outer" else 4_000_000_000
        return result

    monkeypatch.setattr(loop.kernel, "GetProcessTimes", slow_query)
    with pytest.raises(ValueError, match=r"evidence-(outer|reader)-deadline"):
        loop.reader.check()


@pytest.mark.parametrize(
    "fault",
    ["header-zero", "header-large", "truncated", "unbound", "first-pid", "second-pid"],
)
def test_reader_connection_rejects_bad_frames_and_changed_kernel_client(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Actual bounded endpoint framing and both kernel checks precede admission."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    connection = loop.peer
    if fault == "unbound":
        connection.pinned = None
        assert connection.poll() is None
        return
    if fault in {"first-pid", "second-pid"}:
        connection.pinned = None
        calls = 0

        def changing(_handle: int, pid: _Output) -> int:
            nonlocal calls
            calls += 1
            pid._obj.value = 50 if fault == "second-pid" and calls == 1 else 51
            return 1

        monkeypatch.setattr(loop.kernel, "GetNamedPipeClientProcessId", changing)
        with pytest.raises(ValueError, match="evidence-"):
            connection.pin(loop.state.expected, loop.state, now[0])
        return
    if fault == "truncated":
        loop.kernel.queues[101].extend(b"abc")
        loop.kernel.closed.add(102)
    else:
        size = 0 if fault == "header-zero" else 4096
        loop.kernel.queues[101].extend(peer.struct.pack(">IIQ", size, 1, now[0]))

    def poll_all() -> None:
        for _ in range(10):
            connection.poll()

    with pytest.raises(ValueError, match="evidence-"):
        poll_all()


@pytest.mark.parametrize("after_write", [False, True])
def test_ack_physical_write_keeps_original_deadline_after_state_transition(
    monkeypatch: pytest.MonkeyPatch,
    *,
    after_write: bool,
) -> None:
    """A marker's ACK cannot gain the cleanup deadline before physical delivery."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    connection = loop.peer
    connection.acknowledge(b"a" * 80, deadline_ns=2_000_000_000)
    if after_write:
        original = connection.endpoint.poll_write

        def late_write() -> bool:
            while not original():
                pass
            now[0] = 2_000_000_000
            return True

        monkeypatch.setattr(connection.endpoint, "poll_write", late_write)
    else:
        now[0] = 2_000_000_000
    with pytest.raises(ValueError, match="ack-write-expired"):
        connection.poll()
    assert connection.endpoint.reading is None


@pytest.mark.parametrize("resource_type", ["client", "connection", "reader"])
def test_all_native_owners_attempt_close_after_first_resource_failure(
    resource_type: str,
) -> None:
    """The first cleanup error survives while every independent owner is attempted."""
    first, second, third, fourth = Mock(), Mock(), Mock(), Mock()
    first.close.side_effect = OSError("first")
    second.close.side_effect = OSError("second")
    if resource_type == "client":
        owner = object.__new__(peer._EvidenceClient)
        owner.endpoint, owner.reader = first, second
        resources = (first, second)
    elif resource_type == "connection":
        owner = object.__new__(peer._ReaderConnection)
        owner.endpoint, owner.pinned = first, second
        resources = (first, second)
    else:
        owner = object.__new__(peer._EvidenceReader)
        owner.raw, owner.peer, owner.parent, owner.outer = first, second, third, fourth
        owner.parent_raw = Mock()
        resources = (first, owner.parent_raw, second, third, fourth)
    with pytest.raises(OSError, match="first"):
        owner.close()
    for resource in resources:
        resource.close.assert_called_once()


@pytest.mark.parametrize("fault", ["none", "oversize", "deadline"])
def test_fixed_source_hash_is_chunked_size_capped_and_inside_original_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    """Attested source hashing cannot allocate an arbitrary file or add time."""
    source = tmp_path / "fixed_source.py"
    content = b"x" * (131073 if fault == "oversize" else 131072)
    source.write_bytes(content)
    monkeypatch.setattr(peer, "__file__", str(source))
    monkeypatch.setattr(peer, "_EVIDENCE_SOURCE_MAX_BYTES", 131072)
    monkeypatch.setattr(
        peer.time, "monotonic_ns", lambda: 2 if fault == "deadline" else 1
    )
    if fault == "none":
        assert peer._evidence_source_digest(2) == hashlib.sha256(content).hexdigest()
    else:
        with pytest.raises(ValueError, match="evidence-"):
            peer._evidence_source_digest(2)


def test_atomic_status_flush_failure_preserves_previous_packet_and_fixed_residual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No failure path replaces the previous status or switches artifact paths."""
    monkeypatch.chdir(tmp_path)
    status = tmp_path / "moving-evidence-status.json"
    status.write_bytes(b'{"stage":"previous"}')

    def failed(_descriptor: int) -> None:
        raise OSError("status fsync failed")

    monkeypatch.setattr(peer.os, "fsync", failed)
    with pytest.raises(OSError, match="fsync"):
        peer._write_evidence_packet("moving-evidence-status.json", {"stage": "new"})
    assert status.read_bytes() == b'{"stage":"previous"}'
    staging = tmp_path / "moving-evidence-status.json.tmp"
    assert staging.read_bytes() == b'{"stage":"new"}'
    assert staging.stat().st_size <= 16384
    assert {path.name for path in tmp_path.iterdir()} == {status.name, staging.name}


@pytest.mark.parametrize(
    ("name", "packet"),
    [
        ("elsewhere.json", {"stage": "x"}),
        ("moving-evidence-final.json", {"stage": "x" * 16384}),
    ],
)
def test_status_artifact_admission_precedes_any_file_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, packet: dict[str, str]
) -> None:
    """Arbitrary names and oversized JSON cannot create even a staging file."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r".+"):
        peer._write_evidence_packet(name, packet)
    assert not tuple(tmp_path.iterdir())


def test_reader_failure_publication_and_cleanup_preserve_first_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed diagnostic delivery never prevents mandatory reader owner cleanup."""
    reader = Mock()
    reader.state = None
    reader.run.side_effect = OSError("original raw failure")
    reader.close.side_effect = OSError("cleanup failure")
    monkeypatch.setattr(peer, "_EvidenceReader", lambda _launch: reader)

    def publication(_name: str, _packet: dict[str, Any]) -> None:
        raise OSError("status failure")

    monkeypatch.setattr(peer, "_write_evidence_packet", publication)
    with pytest.raises(OSError, match="original raw failure") as caught:
        peer.run_evidence_reader(
            peer.EvidenceReaderLaunch("c" * 32, 9, 90, 49_000_000_000).serialize()
        )
    reader.close.assert_called_once()
    assert any("publication" in note for note in caught.value.__notes__)
    assert any("cleanup" in note for note in caught.value.__notes__)


@pytest.mark.parametrize(
    "fault", ["open", "times", "image", "image-bound", "exit-query", "closed", "close"]
)
def test_retained_process_native_failures_never_release_identity_admission(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Every kernel identity failure rejects and acquired handles still close."""
    kernel = FakeEvidenceKernel()
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "identity ABI"),
        raising=False,
    )
    if fault == "open":
        monkeypatch.setattr(kernel, "OpenProcess", lambda *_a: 0)
    elif fault == "times":
        monkeypatch.setattr(kernel, "GetProcessTimes", lambda *_a: 0)
    elif fault == "image":
        monkeypatch.setattr(kernel, "QueryFullProcessImageNameW", lambda *_a: 0)
    elif fault == "image-bound":

        def invalid_image(_h: int, _flags: int, _buffer: object, size: _Output) -> int:
            size._obj.value = 2048
            return 1

        monkeypatch.setattr(kernel, "QueryFullProcessImageNameW", invalid_image)
    if fault in {"open", "times", "image", "image-bound"}:
        with pytest.raises((ValueError, OSError), match=r".+"):
            peer._EvidenceProcess(kernel, 50, 500, kernel.image)
        assert (1050 in kernel.closed) == (fault != "open")
        return
    process = peer._EvidenceProcess(kernel, 50, 500, kernel.image)
    if fault == "closed":
        process.close()
        with pytest.raises(ValueError, match="process-closed"):
            process.check()
    elif fault == "close":
        monkeypatch.setattr(kernel, "CloseHandle", lambda *_a: 0)
        with pytest.raises(OSError, match="identity ABI"):
            process.close()
        assert process.handle is None
    else:
        monkeypatch.setattr(kernel, "WaitForSingleObject", lambda *_a: 0)
        monkeypatch.setattr(kernel, "GetExitCodeProcess", lambda *_a: 0)
        with pytest.raises(OSError, match="identity ABI"):
            process.check(allow_exit=True)


@pytest.mark.parametrize(
    "fault",
    [
        "cancel",
        "completion",
        "wait",
        "retired",
        "completed-race",
        "pipe-close",
        "event-close",
    ],
)
def test_pending_overlapped_cancellation_preserves_exact_retirement_outcome(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Cancellation races are bounded; failed retirement never frees pending data."""
    monkeypatch.setattr(peer, "_unretired_evidence_io", [])
    error = [997]
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: error[0], raising=False)
    abi.ReadFile.return_value = False
    endpoint = peer._EvidenceEndpoint(abi, 77)
    endpoint.close_deadline_ns = 100
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1)
    endpoint.start_read(16)
    if fault == "cancel":
        abi.CancelIoEx.return_value = False
        error[0] = 5
    elif fault == "completion":
        error[0] = 5
    elif fault == "wait":
        error[0] = 996
        abi.WaitForSingleObject.return_value = 0xFFFFFFFF
    elif fault == "retired":
        error[0] = 996

        def wake(_handle: int, _wait: int) -> int:
            error[0] = 995
            return 0

        abi.WaitForSingleObject.side_effect = wake
    elif fault == "completed-race":
        abi.GetOverlappedResult.return_value = True
    else:
        error[0] = 995
        abi.CloseHandle.side_effect = lambda handle: (
            handle != (77 if fault == "pipe-close" else 100)
        )
    if fault in {"retired", "completed-race"}:
        endpoint.close()
        assert not peer._unretired_evidence_io
    else:
        with pytest.raises(OSError, match="fake ABI"):
            endpoint.close()
    assert any(call.args[0] == 77 for call in abi.CloseHandle.call_args_list)
    if fault in {"cancel", "completion", "wait"}:
        assert len(peer._unretired_evidence_io) == 1
    else:
        assert any(call.args[0] == 100 for call in abi.CloseHandle.call_args_list)


@pytest.mark.parametrize(
    "fault", ["create", "pid-query", "second-pid", "ack-eof", "closed", "failed"]
)
def test_evidence_client_connect_and_ack_failures_latch_before_native_admission(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Failed identity, changed server, lost ACK or closed stream grants no call."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    if fault == "create":
        monkeypatch.setattr(loop.kernel, "CreateFileW", lambda *_a: 0)
    elif fault == "pid-query":
        monkeypatch.setattr(loop.kernel, "GetNamedPipeServerProcessId", lambda *_a: 0)
    elif fault == "second-pid":
        count = 0

        def changed_pid(_handle: int, pid: _Output) -> int:
            nonlocal count
            count += 1
            pid._obj.value = 30 if count == 1 else 31
            return 1

        monkeypatch.setattr(loop.kernel, "GetNamedPipeServerProcessId", changed_pid)
    if fault in {"create", "pid-query", "second-pid"}:
        with pytest.raises((ValueError, OSError), match=r".+"):
            peer._Journal(loop.launch, lambda: None)
        return
    client = loop.parent_client
    if fault == "ack-eof":
        loop.kernel.pump = lambda: loop.kernel.closed.add(201)
    elif fault == "closed":
        client.close()
    else:
        client.failed = True
    with pytest.raises(ValueError, match="evidence-"):
        client.exchange(b"frame", 2_000_000_000)
    assert client.failed


def test_journal_observation_failure_and_one_shot_restore_use_actual_raw_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferred primitive timing is audited; this does not exercise public restore."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    for index, operation in ((1, "moving_begin"), (2, "move_next"), (3, "observe")):
        payload: dict[str, object] = {"operation": operation}
        if index == 1:
            payload.update(
                command_started_ns=now[0], command_deadline_ns=now[0] + 2_000_000_000
            )
        else:
            payload.update(
                step_id=1,
                requested_ns=now[0],
                deadline_ns=now[0] + (3_000_000_000 if index == 2 else 2_000_000_000),
            )
        request = peer.Message("request", index, payload)
        loop.context_for(request)
        journal.phase_enter(request)
        if index == 2:
            seek = {
                "event": "seek_start",
                "vertical": 100.0 * 3 / 13,
                "horizontal": -1.0,
                "restoring": False,
            }
            journal.emit(seek, timestamp_ns=now[0])
            journal.emit(dict(seek, event="seek_end", status=0), timestamp_ns=now[0])
        if index < 3:
            journal.phase_end()
            loop.complete()
    journal.failure("observation", timestamp_ns=now[0])
    journal.restore_enter(now[0], timestamp_ns=now[0])
    assert journal.deadline_ns == now[0] + 8_000_000_000
    for index, operation in enumerate(("guard", "write", "capture", "final"), 1):
        journal.restore_phase(index, operation, timestamp_ns=now[0])
        journal.emit(
            {
                "event": "call_start",
                "name": "COM",
                "effective_deadline_ns": now[0] + 1_000_000_000,
            },
            timestamp_ns=now[0],
        )
        journal.emit({"event": "call_end", "seconds": 0}, timestamp_ns=now[0])
    assert loop.state.restore_phase == 4
    with pytest.raises(ValueError, match="evidence-"):
        journal.finish()
    assert journal.failed
    assert not loop.state.complete


@pytest.mark.parametrize("value", [None, "", 7])
def test_base_executable_must_exist_in_already_attested_runtime(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """No basename/current-GUI interpreter fallback can replace the base proof."""
    monkeypatch.setattr(peer.sys, "_base_executable", value, raising=False)
    with pytest.raises(ValueError, match="missing-base"):
        peer._base_executable()


def test_base_executable_returns_full_resolved_installed_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exact bootstrap-proven interpreter is retained as a complete path."""
    monkeypatch.setattr(
        peer.sys, "_base_executable", str(tmp_path / "python.exe"), raising=False
    )
    assert peer._base_executable() == str(tmp_path / "python.exe")


@pytest.mark.parametrize(
    "fault",
    [
        "event",
        "read-error",
        "read-eof",
        "pending-write",
        "write-bound",
        "no-read",
        "no-connect",
    ],
)
def test_native_endpoint_missing_or_failed_operations_cannot_grant_ack(
    abi: Mock,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Endpoint API failures and invalid operation state are explicit, not retries."""
    endpoint = peer._EvidenceEndpoint(abi, 77)
    error = [997]
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: error[0], raising=False)
    if fault == "event":
        abi.CreateEventW.side_effect = None
        abi.CreateEventW.return_value = 0
        with pytest.raises(OSError, match="fake ABI"):
            endpoint.start_read(16)
    elif fault.startswith("read-"):
        abi.ReadFile.return_value = False
        endpoint.start_read(16)
        error[0] = 109 if fault == "read-eof" else 5
        if fault == "read-eof":
            assert endpoint.poll_read() == b""
        else:
            with pytest.raises(OSError, match="fake ABI"):
                endpoint.poll_read()
    elif fault == "pending-write":
        abi.WriteFile.return_value = False
        endpoint.start_write(b"data")
        error[0] = 996
        assert not endpoint.poll_write()
    elif fault == "write-bound":
        with pytest.raises(ValueError, match="write-bound"):
            endpoint.start_write(b"x" * 4097)
    elif fault == "no-read":
        with pytest.raises(ValueError, match="no-read"):
            endpoint.poll_read()
    else:
        with pytest.raises(ValueError, match="no-connect"):
            endpoint.poll_connect()
    error[0] = 995
    endpoint.close()
    assert endpoint.poll_write()


@pytest.mark.parametrize(
    "operation", ["end", "failure", "restore", "finish-marker", "unknown"]
)
def test_journal_rejects_unscoped_native_phase_or_restore_before_any_wire_write(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """No naked cleanup/restore or unknown command can create evidence authority."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    before = len(loop.kernel.sent)

    def unscoped() -> None:
        if operation == "end":
            journal.phase_end()
        elif operation == "failure":
            journal.failure("observation", timestamp_ns=now[0])
        elif operation == "restore":
            journal.restore_enter(now[0], timestamp_ns=now[0])
        elif operation == "finish-marker":
            journal.finish_response_sent(timestamp_ns=now[0])
        else:
            journal.phase_enter(peer.Message("request", 1, {"operation": "unknown"}))

    with pytest.raises(ValueError, match="evidence-"):
        unscoped()
    assert len(loop.kernel.sent) == before


def test_client_second_deadline_check_and_wait_failure_are_not_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Time crossing during identity checks or an invalid wait stops admission."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    calls = 0

    def crossed() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            now[0] = 2_000_000_000

    loop.parent_client.check = crossed
    with pytest.raises(ValueError, match="expired"):
        loop.parent_client._check(2_000_000_000)
    now[0] = 1_000_000_000
    loop.parent_client.check = lambda: None
    monkeypatch.setattr(
        loop.kernel,
        "WaitForSingleObject",
        lambda _handle, wait: 0xFFFFFFFF if wait else 258,
    )
    with pytest.raises(OSError, match="fake ABI"):
        loop.parent_client.exchange(b"x" * 32, 2_000_000_000)
    assert loop.parent_client.failed


def test_journal_own_identity_query_failure_closes_already_authenticated_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure between connection ownership and hello must still close both owners."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    actual = loop.kernel.GetProcessTimes
    monkeypatch.setattr(
        loop.kernel,
        "GetProcessTimes",
        lambda handle, *args: 0 if handle == 1050 else actual(handle, *args),
    )
    with pytest.raises(OSError, match="fake ABI"):
        peer._Journal(loop.launch, lambda: None)
    assert {102, 1030} <= loop.kernel.closed
    assert not loop.kernel.sent


def test_parent_invalid_schema_latches_without_sending_a_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid context serialization cannot be retried as an unauthenticated grant."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    with pytest.raises(ValueError, match=r".+"):
        loop.parent_client.send({"unexpected": "field"}, deadline_ns=2_000_000_000)
    assert loop.parent_client.failed
    assert not loop.kernel.sent


@pytest.mark.parametrize("fault", ["token", "ceiling", "raw-short"])
def test_reader_expected_identity_and_raw_storage_never_ack_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    """Outer token/deadline disagreement and short writes are independent failures."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    monkeypatch.chdir(tmp_path)
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    if fault == "raw-short":
        raw = Mock()
        raw.write.return_value = 0
        loop.reader.raw = raw
        with pytest.raises(ValueError, match="raw-short-write"):
            peer._Journal(loop.launch, lambda: None)
        raw.flush.assert_not_called()
        assert not any(handle == 101 for handle, _data in loop.kernel.sent)
    else:
        loop.reader.expected = None
        loop.reader.expected_fingerprint = None
        loop.reader.launch = peer.EvidenceReaderLaunch(
            "a" * 32 if fault == "token" else "c" * 32,
            9,
            90,
            30_000_000_000 if fault == "ceiling" else 49_000_000_000,
        )
        (tmp_path / "moving-evidence-expected.json").write_bytes(
            peer.canonical_json(loop.state.expected.value)
        )
        with pytest.raises(ValueError, match="identity-mismatch"):
            loop.reader._expected()


def test_reader_authenticated_status_waits_for_actual_hello_ack_write_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validated hello is not mislabeled as an independently completed ready ACK."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    journal = peer._Journal(loop.launch, lambda: None)
    assert loop.state.authenticated
    assert not loop.reader.authentication_ack_written
    loop.pump()
    assert loop.reader.authentication_ack_written
    journal.close()


@pytest.mark.parametrize("delay", ["before-hello", "during-ack"])
def test_original_startup_three_second_ceiling_cannot_reset_on_runtime_ready(
    monkeypatch: pytest.MonkeyPatch, delay: str
) -> None:
    """Connection and hello costs remain within original worker entry plus3s."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    if delay == "before-hello":
        now[0] = 4_000_000_000
    else:
        pump = loop.kernel.pump

        def delayed() -> None:
            pump()
            if loop.state.count:
                now[0] = 4_000_000_000

        loop.kernel.pump = delayed
    with pytest.raises(ValueError, match="evidence-"):
        peer._Journal(loop.launch, lambda: None)
    assert not loop.state.calls
    assert not loop.state.complete
    assert 102 in loop.kernel.closed
