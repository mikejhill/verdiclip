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
