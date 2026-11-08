"""Portable ABI tests for the independent fixed sequence native adapter."""

import time
from unittest.mock import Mock

import pytest

from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform.moving_edit_protocol import Message, ProtocolError


def launch() -> peer.MovingEditLaunch:
    """Provide the exact fixed sequence profile without opening a socket."""
    return peer.MovingEditLaunch(
        "VerdiClip-moving-edit-" + "a" * 32,
        "b" * 32,
        7,
        1,
        30_000_000_001,
        "c" * 32,
        30,
        3000,
    )


def test_dead_pinned_parent_prevents_pipe_io() -> None:
    """An already terminated parent cannot receive another response."""
    pipe = object.__new__(peer._MovingEditPipe)
    pipe.api = Mock()
    pipe.handle, pipe.parent_handle = 88, 99
    pipe.sequence_clock = None
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
        peer._MovingEditPipe(launch())
    assert "cleanup failed" in caught.value.__notes__[0]
    api.CloseHandle.assert_called_once_with(88)


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


@pytest.mark.parametrize("invalid", [0, None, peer.ctypes.c_void_p(-1).value])
def test_pipe_open_failure_never_closes_invalid_handle(
    native_api: Mock, invalid: int | None
) -> None:
    """No identity query or cleanup is performed against a failed open result."""
    native_api.CreateFileW.return_value = invalid
    with pytest.raises(OSError, match="native failure") as caught:
        peer._MovingEditPipe(launch())
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
        peer._MovingEditPipe(launch())
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
    pipe = peer._MovingEditPipe(launch())
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
    pipe = peer._MovingEditPipe(launch())
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
    pipe = peer._MovingEditPipe(launch())
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
    pipe = peer._MovingEditPipe(launch())
    try:
        with pytest.raises(OSError if failure == "api" else ProtocolError):
            pipe.write(Message("response", 1, {"operation": "pong"}))
        native_api.WriteFile.assert_called_once()
    finally:
        pipe.close()


def test_close_attempts_both_handles_and_is_not_retried(native_api: Mock) -> None:
    """The first close error survives, with neither handle accidentally reused."""
    pipe = peer._MovingEditPipe(launch())
    native_api.CloseHandle.return_value = False
    with pytest.raises(OSError, match="native failure") as caught:
        pipe.close()
    assert caught.value.errno == 123
    assert [call.args[0] for call in native_api.CloseHandle.call_args_list] == [88, 99]
    pipe.close()
    assert native_api.CloseHandle.call_count == 2


@pytest.mark.parametrize(
    "value",
    [b"", bytearray(b"x"), b"x" * 65537],
    ids=["empty", "mutable", "oversized"],
)
def test_binary_chunk_bound_before_native_io(native_api: Mock, value: bytes) -> None:
    """Binary I/O cannot bypass the fixed chunk bound or immutable-buffer rule."""
    pipe = peer._MovingEditPipe(launch())
    with pytest.raises(ProtocolError, match="write-chunk"):
        pipe.write_bytes(value)
    native_api.WriteFile.assert_not_called()
    pipe.close()


@pytest.mark.parametrize("frame_id", [0, 9, True])
def test_rejects_unbounded_frame_pipe(native_api: Mock, frame_id: int) -> None:
    """Invalid current frame IDs never reach CreateFileW."""
    with pytest.raises(ProtocolError, match="invalid-frame-pipe-id"):
        peer._MovingEditPipe(launch(), frame_id)
    native_api.CreateFileW.assert_not_called()


def test_pipe_name_and_original_clock(
    native_api: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh fixed frame name and both pre/post native clock checks are retained."""
    pipe = peer._MovingEditPipe(launch(), 2)
    assert (
        native_api.CreateFileW.call_args.args[0]
        == "\\\\.\\pipe\\" + launch().pipe_name + "-frame-2"
    )
    monkeypatch.setattr(time, "monotonic_ns", lambda: 100)
    pipe.sequence_clock = peer.SequenceDeadline(101)
    pipe.checkpoint()
    monkeypatch.setattr(time, "monotonic_ns", lambda: 101)
    with pytest.raises(ProtocolError):
        pipe.checkpoint()
    pipe.close()


def test_oversized_native_record(
    native_api: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declared control size is rejected before a second native payload read."""
    pipe = peer._MovingEditPipe(launch())
    read = Mock(return_value=b"\xff" * 4)
    monkeypatch.setattr(pipe, "_read", read)
    with pytest.raises(ProtocolError, match="oversized"):
        pipe.read()
    read.assert_called_once_with(4)
    native_api.ReadFile.assert_not_called()
    pipe.close()


def test_cleanup_failure_preserves_primary() -> None:
    """Cleanup can annotate, but cannot erase, the failure that caused retirement."""
    pipe = Mock()
    pipe.close.side_effect = OSError("close failed")
    with pytest.raises(OSError, match="close failed"):
        peer._close_preserving(pipe)
    error = ProtocolError("primary")
    try:
        raise error
    except ProtocolError:
        peer._close_preserving(pipe)
    assert "close failed" in error.__notes__[0]
