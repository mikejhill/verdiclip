"""Portable fixed inert-peer contracts; no real pipe or helper process is launched."""

import json
import struct
from unittest.mock import Mock

import pytest

from verdiclip.platform import helper_peer as peer
from verdiclip.platform.helper_protocol import MAX_MESSAGE_BYTES, Message, ProtocolError


def launch() -> peer.PeerLaunch:
    """Build deterministic bounded session data without any launch-target selector."""
    return peer.PeerLaunch("VerdiClip-helper-" + "a" * 32, "b" * 32, 7)


def test_launch_roundtrip_has_only_fixed_fields() -> None:
    """No arbitrary module, executable, command or directory is accepted."""
    value = launch()
    assert peer.deserialize_launch(value.serialize()) == value
    extra = json.loads(value.serialize())
    extra["module"] = "arbitrary"
    with pytest.raises(ValueError, match=r"Invalid|Unsupported"):
        peer.deserialize_launch(json.dumps(extra))
    with pytest.raises(ProtocolError, match="duplicate-key"):
        peer.deserialize_launch(value.serialize()[:-1] + ',"epoch":"x"}')


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pipe_name", "\\\\remote\\pipe\\x"),
        ("epoch", "X" * 32),
        ("parent_pid", True),
        ("parent_pid", 0),
        ("parent_pid", 2**32),
        ("protocol_version", True),
        ("protocol_version", 2),
    ],
)
def test_launch_rejects_ambiguous_identity(key: str, value: object) -> None:
    """All launch fields have concrete bounds and strict scalar types."""
    data = json.loads(launch().serialize())
    data[key] = value
    with pytest.raises(ValueError, match=r"Invalid|Unsupported"):
        peer.deserialize_launch(json.dumps(data))


def test_peer_only_ping_then_finish_and_no_reuse() -> None:
    """The fixed peer cannot execute capture/provider commands or restart."""
    protocol = peer.InertProtocol()
    assert protocol.accept(Message("request", 1, {"operation": "ping"})) == Message(
        "response", 1, {"operation": "pong"}
    )
    assert protocol.accept(Message("request", 2, {"operation": "finish"})) == Message(
        "response", 2, {"operation": "finished"}
    )
    with pytest.raises(ProtocolError):
        protocol.accept(Message("request", 3, {"operation": "ping"}))


@pytest.mark.parametrize(
    "message",
    [
        Message("request", 1, {"operation": "capture"}),
        Message("request", 1, {"operation": "ping", "extra": 1}),
        Message("response", 1, {"operation": "ping"}),
        Message("request", 2, {"operation": "ping"}),
    ],
)
def test_peer_protocol_failure_is_permanent(message: Message) -> None:
    """No rejected request can be retried within this peer lifetime."""
    protocol = peer.InertProtocol()
    with pytest.raises(ProtocolError):
        protocol.accept(message)
    with pytest.raises(ProtocolError):
        protocol.accept(Message("request", 1, {"operation": "ping"}))


@pytest.mark.parametrize("server_pid", [7, 999])
def test_kernel_server_pid_checked_before_any_communication(
    monkeypatch: pytest.MonkeyPatch,
    server_pid: int,
) -> None:
    """Even a pipe with the expected name must belong to the actual parent."""
    api = Mock()
    api.CreateFileW.return_value = 88
    api.OpenProcess.return_value = 99
    api.WaitForSingleObject.return_value = 258

    def get_pid(_handle: int, pid: object) -> bool:
        pid._obj.value = server_pid  # ty: ignore[unresolved-attribute] - ctypes byref
        return True

    api.GetNamedPipeServerProcessId.side_effect = get_pid
    monkeypatch.setattr(peer.ctypes, "WinDLL", lambda *_args, **_kw: api, raising=False)
    if server_pid == 7:
        pipe = peer._Pipe(launch())
        pipe.close()
    else:
        with pytest.raises(ProtocolError, match="wrong-pipe-server-pid"):
            peer._Pipe(launch())
    assert [call.args[0] for call in api.CloseHandle.call_args_list] == (
        [88, 99] if server_pid == 7 else [88]
    )
    api.WriteFile.assert_not_called()
    api.ReadFile.assert_not_called()


def test_peer_eof_closes_without_reconnecting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Premature EOF is a failed lifetime with one open and one close."""
    pipe = Mock()
    pipe.read.side_effect = ProtocolError("unexpected-peer-eof")
    create = Mock(return_value=pipe)
    monkeypatch.setattr(peer, "_Pipe", create)
    with pytest.raises(ProtocolError, match="eof"):
        peer.run(launch().serialize())
    create.assert_called_once()
    pipe.close.assert_called_once()
    ready = pipe.write.call_args.args[0]
    assert ready.payload["epoch"] == launch().epoch
    assert ready.payload["protocol_version"] == 1


def test_peer_ready_ping_finish_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entire inert lifetime has only the fixed ready/pong/finished messages."""
    pipe = Mock()
    pipe.read.side_effect = [
        Message("request", 1, {"operation": "ping"}),
        Message("request", 2, {"operation": "finish"}),
    ]
    monkeypatch.setattr(peer, "_Pipe", lambda _: pipe)
    peer.run(launch().serialize())
    assert [call.args[0].kind for call in pipe.write.call_args_list] == [
        "ready",
        "response",
        "response",
    ]
    pipe.close.assert_called_once()


def test_oversized_header_never_reads_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Untrusted length is checked before allocating or waiting on payload bytes."""
    pipe = object.__new__(peer._Pipe)
    read = Mock(return_value=struct.pack(">I", MAX_MESSAGE_BYTES + 1))
    monkeypatch.setattr(pipe, "_read", read)
    with pytest.raises(ProtocolError, match="oversized"):
        pipe.read()
    read.assert_called_once_with(4)


def test_dead_pinned_parent_prevents_pipe_io() -> None:
    """An already terminated parent cannot receive another response."""
    pipe = object.__new__(peer._Pipe)
    pipe.api = Mock()
    pipe.handle, pipe.parent_handle = 88, 99
    pipe.api.WaitForSingleObject.return_value = 0
    with pytest.raises(ProtocolError, match="parent-not-alive"):
        pipe.write(Message("response", 1, {"operation": "pong"}))
    pipe.api.WriteFile.assert_not_called()
    pipe.close()
    assert [call.args[0] for call in pipe.api.CloseHandle.call_args_list] == [88, 99]


def test_wrong_server_pid_remains_first_error_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup failure cannot hide why the server was rejected."""
    api = Mock()
    api.CreateFileW.return_value = 88
    api.GetNamedPipeServerProcessId.return_value = True
    api.CloseHandle.return_value = False
    monkeypatch.setattr(peer.ctypes, "WinDLL", lambda *_args, **_kw: api, raising=False)
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "close failed"),
        raising=False,
    )
    with pytest.raises(ProtocolError, match="wrong-pipe-server-pid") as caught:
        peer._Pipe(launch())
    assert "cleanup failed" in caught.value.__notes__[0]
    api.CloseHandle.assert_called_once_with(88)


def test_peer_eof_remains_first_error_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protocol failures retain their identity while exposing cleanup errors too."""
    pipe = Mock()
    pipe.read.side_effect = ProtocolError("unexpected-peer-eof")
    pipe.close.side_effect = OSError("close failed")
    monkeypatch.setattr(peer, "_Pipe", lambda _: pipe)
    with pytest.raises(ProtocolError, match="unexpected-peer-eof") as caught:
        peer.run(launch().serialize())
    assert "close failed" in caught.value.__notes__[0]
    pipe.close.assert_called_once()


@pytest.fixture
def native_api(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Model successful Win32 identity calls without touching a native endpoint."""
    api = Mock()
    api.CreateFileW.return_value = 88
    api.OpenProcess.return_value = 99
    api.WaitForSingleObject.return_value = 258

    def server_pid(_handle: int, value: object) -> bool:
        value._obj.value = 7  # ty: ignore[unresolved-attribute] - ctypes byref
        return True

    api.GetNamedPipeServerProcessId.side_effect = server_pid
    monkeypatch.setattr(peer.ctypes, "WinDLL", lambda *_a, **_k: api, raising=False)
    monkeypatch.setattr(peer.ctypes, "get_last_error", lambda: 123, raising=False)
    monkeypatch.setattr(
        peer.ctypes,
        "WinError",
        lambda code: OSError(code, "native failure"),
        raising=False,
    )
    return api


@pytest.mark.parametrize("payload", [None, b"{}", "x" * 1025, "é" * 513])
def test_launch_input_byte_bound(payload: object) -> None:
    """Reject invalid types and UTF8 overflow before JSON or pipe access."""
    with pytest.raises(ValueError, match="exceeds bound"):
        peer.deserialize_launch(payload)  # ty: ignore[invalid-argument-type]


def test_launch_serialization_enforces_transport_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future smaller wire budget cannot silently permit oversized launch data."""
    value = launch()
    monkeypatch.setattr(peer, "_MAX_LAUNCH_BYTES", len(value.serialize().encode()) - 1)
    with pytest.raises(ValueError, match="exceeds bound"):
        value.serialize()


@pytest.mark.parametrize("invalid", [0, None, peer.ctypes.c_void_p(-1).value])
def test_pipe_open_failure_never_closes_invalid_handle(
    native_api: Mock, invalid: int | None
) -> None:
    """No identity query or cleanup is performed against a failed open result."""
    native_api.CreateFileW.return_value = invalid
    with pytest.raises(OSError, match="native failure") as caught:
        peer._Pipe(launch())
    assert caught.value.errno == 123
    native_api.GetNamedPipeServerProcessId.assert_not_called()
    native_api.CloseHandle.assert_not_called()


@pytest.mark.parametrize("failure", ["server", "parent", "wait", "recheck"])
def test_identity_failure_closes_only_acquired_handles(
    native_api: Mock, failure: str
) -> None:
    """Failed mutual identity or parent pinning never reaches communication."""
    if failure == "server":
        native_api.GetNamedPipeServerProcessId.side_effect = None
        native_api.GetNamedPipeServerProcessId.return_value = False
    elif failure == "parent":
        native_api.OpenProcess.return_value = 0
    elif failure == "wait":
        native_api.WaitForSingleObject.return_value = 0xFFFFFFFF
    else:
        original = native_api.GetNamedPipeServerProcessId.side_effect
        calls = 0

        def changed_server(handle: int, value: object) -> bool:
            nonlocal calls
            calls += 1
            return bool(original(handle, value)) if calls == 1 else False

        native_api.GetNamedPipeServerProcessId.side_effect = changed_server
    with pytest.raises(OSError, match="native failure") as caught:
        peer._Pipe(launch())
    assert caught.value.errno == 123
    assert [call.args[0] for call in native_api.CloseHandle.call_args_list] == (
        [88] if failure in {"server", "parent"} else [88, 99]
    )
    native_api.ReadFile.assert_not_called()
    native_api.WriteFile.assert_not_called()


def test_partial_native_reads_reconstruct_one_message(native_api: Mock) -> None:
    """Fragmented header and payload are read exactly, without consuming trailers."""
    message = Message("request", 1, {"operation": "ping"})
    wire = bytearray(peer.encode(message))
    received = bytearray()

    def read(
        _handle: int,
        buffer: peer.ctypes.Array[peer.ctypes.c_char],
        size: int,
        count: object,
        _ov: object,
    ) -> bool:
        chunk = bytes(wire[: min(3, size)])
        del wire[: len(chunk)]
        peer.ctypes.memmove(buffer, chunk, len(chunk))
        count._obj.value = len(chunk)  # ty: ignore[unresolved-attribute] - ctypes byref
        received.extend(chunk)
        return True

    native_api.ReadFile.side_effect = read
    pipe = peer._Pipe(launch())
    try:
        assert pipe.read() == message
        assert bytes(received) == peer.encode(message)
        assert not wire
        assert native_api.ReadFile.call_count > 2
    finally:
        pipe.close()


@pytest.mark.parametrize("failure", ["api", "eof"])
def test_native_read_failure_does_not_retry(native_api: Mock, failure: str) -> None:
    """A failed or zero-byte native read ends the operation immediately."""
    native_api.ReadFile.return_value = failure != "api"
    pipe = peer._Pipe(launch())
    try:
        with pytest.raises(OSError if failure == "api" else ProtocolError):
            pipe.read()
        native_api.ReadFile.assert_called_once()
    finally:
        pipe.close()


def test_partial_native_writes_preserve_exact_framing(native_api: Mock) -> None:
    """Each subsequent write contains only the unconsumed suffix."""
    message = Message("response", 1, {"operation": "pong"})
    sent = bytearray()

    def write(
        _handle: int,
        buffer: peer.ctypes.Array[peer.ctypes.c_char],
        size: int,
        count: object,
        _ov: object,
    ) -> bool:
        amount = min(3, size)
        sent.extend(peer.ctypes.string_at(buffer, amount))
        count._obj.value = amount  # ty: ignore[unresolved-attribute] - ctypes byref
        return True

    native_api.WriteFile.side_effect = write
    pipe = peer._Pipe(launch())
    try:
        pipe.write(message)
        assert bytes(sent) == peer.encode(message)
        assert native_api.WriteFile.call_count > 1
    finally:
        pipe.close()


@pytest.mark.parametrize("failure", ["api", "zero", "excess"])
def test_native_write_failure_does_not_retry(native_api: Mock, failure: str) -> None:
    """Failed or impossible byte counts never permit another write."""

    def write(_h: int, _b: object, size: int, count: object, _ov: object) -> bool:
        count._obj.value = size + 1 if failure == "excess" else 0  # ty: ignore[unresolved-attribute]
        return failure != "api"

    native_api.WriteFile.side_effect = write
    pipe = peer._Pipe(launch())
    try:
        with pytest.raises(OSError if failure == "api" else ProtocolError):
            pipe.write(Message("response", 1, {"operation": "pong"}))
        native_api.WriteFile.assert_called_once()
    finally:
        pipe.close()


def test_close_attempts_both_handles_and_is_not_retried(native_api: Mock) -> None:
    """The first close error survives, with neither handle accidentally reused."""
    pipe = peer._Pipe(launch())
    native_api.CloseHandle.return_value = False
    with pytest.raises(OSError, match="native failure") as caught:
        pipe.close()
    assert caught.value.errno == 123
    assert [call.args[0] for call in native_api.CloseHandle.call_args_list] == [88, 99]
    pipe.close()
    assert native_api.CloseHandle.call_count == 2
