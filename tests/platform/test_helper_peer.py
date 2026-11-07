"""Portable fixed inert-peer contracts; no real pipe or helper process is launched."""

import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from verdiclip.capture.frame_envelope import FrameIdentity, FrameStream
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
    protocol = peer.PeerProtocol()
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
    protocol = peer.PeerProtocol()
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
    assert create.call_count == 2
    assert pipe.close.call_count == 2
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
    monkeypatch.setattr(peer, "_Pipe", lambda _, **_kw: pipe)
    peer.run(launch().serialize())
    assert [call.args[0].kind for call in pipe.write.call_args_list] == [
        "ready",
        "response",
        "response",
    ]
    assert pipe.close.call_count == 3


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
    pipe.deadline_ns = None
    pipe.observe_clock = None
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
    monkeypatch.setattr(peer, "_Pipe", lambda _, **_kw: pipe)
    with pytest.raises(ProtocolError, match="unexpected-peer-eof") as caught:
        peer.run(launch().serialize())
    assert "close failed" in caught.value.__notes__[0]
    assert pipe.close.call_count == 2


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


def observe_request(
    target: peer.ObserveTarget, deadline: int, request_id: int = 1
) -> Message:
    """Build the fixed wrapper, retaining the independent strict target schema."""
    return Message(
        "request",
        request_id,
        {
            "operation": "observe",
            "observe_schema": 1,
            "deadline_ns": deadline,
            "request": json.loads(target.serialize()),
        },
    )


@pytest.mark.parametrize(
    "value",
    [True, None, float("nan"), float("inf"), 0, 1_000_000_000, 3_000_000_001, 2**63],
)
def test_observe_deadline_is_strict_authoritative_ns(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Neither receipt-time reset nor float/nonfinite input can extend two seconds."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    with pytest.raises(ProtocolError, match="deadline"):
        peer.validate_deadline_ns(value)
    assert peer.validate_deadline_ns(3_000_000_000) == 3_000_000_000


def test_peer_observe_is_one_shot_and_reserves_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only finish is legal after one accepted read-only operation."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    target = peer.ObserveTarget(7, 1234, 99, (1, 1))
    protocol = peer.PeerProtocol()
    assert protocol.observe(observe_request(target, 3_000_000_000)) == (
        target,
        3_000_000_000,
    )
    assert protocol.observe_consumed
    assert protocol.accept(Message("request", 2, {"operation": "finish"})).payload == {
        "operation": "finished"
    }
    protocol = peer.PeerProtocol()
    protocol.last_id = 127
    with pytest.raises(ProtocolError):
        protocol.observe(observe_request(target, 3_000_000_000, 128))
    assert protocol.failed
    assert protocol.observe_consumed


@pytest.mark.parametrize(
    "failure", ["extra", "schema", "deadline", "target", "twice", "ping-after"]
)
def test_bad_observe_is_permanently_consumed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Failed validation cannot be corrected and retried within a peer lifetime."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    target = peer.ObserveTarget(7, 1234, 99, (1, 1))
    protocol = peer.PeerProtocol()
    request = observe_request(target, 3_000_000_000)
    if failure == "extra":
        request.payload["extra"] = 1
    elif failure == "schema":
        request.payload["observe_schema"] = True
    elif failure == "deadline":
        request.payload["deadline_ns"] = 1_000_000_000
    elif failure == "target":
        request.payload["request"] = {}
    else:
        protocol.observe(request)
        request = observe_request(target, 3_000_000_000, 2)
    operation = protocol.accept if failure == "ping-after" else protocol.observe
    if failure == "ping-after":
        request = Message("request", 2, {"operation": "ping"})
    with pytest.raises(ValueError, match=r".+"):
        operation(request)
    assert protocol.failed
    assert protocol.observe_consumed


def test_binary_pipe_name_is_fixed_derived_suffix(native_api: Mock) -> None:
    """No caller-supplied second path participates in the native connection."""
    pipe = peer._Pipe(launch(), frame=True)
    assert (
        native_api.CreateFileW.call_args.args[0]
        == "\\\\.\\pipe\\" + launch().pipe_name + "-frame"
    )
    pipe.close()


def test_partial_second_pipe_setup_retires_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure to authenticate/open the data endpoint never leaves control alive."""
    pipe = Mock()
    monkeypatch.setattr(
        peer, "_Pipe", Mock(side_effect=[pipe, OSError("frame denied")])
    )
    with pytest.raises(OSError, match="frame denied"):
        peer.run(launch().serialize())
    pipe.close.assert_called_once()
    pipe.write.assert_not_called()


def test_peer_closed_frame_and_control_cleanup_preserves_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both endpoints are attempted even if each close reports failure."""
    control, frame = Mock(), Mock()
    control.read.side_effect = ProtocolError("primary EOF")
    control.close.side_effect = OSError("control close")
    frame.close.side_effect = OSError("frame close")
    monkeypatch.setattr(peer, "_Pipe", Mock(side_effect=[control, frame]))
    with pytest.raises(ProtocolError, match="primary EOF") as caught:
        peer.run(launch().serialize())
    control.close.assert_called_once()
    frame.close.assert_called_once()
    assert len(caught.value.__notes__) == 2


def test_peer_produces_metadata_before_binary_and_finishes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real header/chunk emission reuses a trusted provider result, no native calls."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    target = peer.ObserveTarget(7, 1234, 99, (1, 1))
    identity = FrameIdentity(
        bytes.fromhex(launch().epoch), 1, 1, 0, 0, 256, 256, bytes(32)
    )
    pixels = bytes(identity.payload_bytes)
    observation = Mock()
    observation.canonical = b'{"provider_fixture":true}'
    observation.frame_identity.return_value = identity
    provider = Mock(return_value=SimpleNamespace(observation=observation, rgb=pixels))
    control, frame = Mock(), Mock()
    control.read.side_effect = [
        observe_request(target, 3_000_000_000),
        Message("request", 2, {"operation": "finish"}),
    ]
    order = []
    control.write.side_effect = lambda msg: order.append(("control", msg.payload))
    frame.write_bytes.side_effect = lambda data: order.append(("frame", data))
    monkeypatch.setattr(peer, "observe_once", provider)
    monkeypatch.setattr(peer, "_Pipe", Mock(side_effect=[control, frame]))
    peer.run(launch().serialize())
    provider.assert_called_once()
    args, kwargs = provider.call_args
    assert args == (target, launch().epoch, 1)
    assert kwargs["deadline"] == 3.0
    assert kwargs["cancelled"]() is False
    assert order[0][0] == order[1][0] == "control"
    assert order[1][1] == {
        "operation": "observed",
        "observation": {"provider_fixture": True},
    }
    assert order[-1] == ("control", {"operation": "finished"})
    chunks = [data for kind, data in order if kind == "frame"]
    stream = FrameStream(chunks[0], identity)
    for chunk in chunks[1:]:
        assert len(chunk) <= 65536
        stream.feed(chunk)
    assert stream.finish().identity == identity
    assert control.deadline_ns == frame.deadline_ns == 3_000_000_000
    control.close.assert_called_once()


def test_pipe_checks_ns_after_slow_native_write(
    native_api: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed native write cannot hide expiry and permit another chunk."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    pipe = peer._Pipe(launch())
    pipe.deadline_ns = 3_000_000_000

    def write(_h: int, _b: object, size: int, count: object, _ov: object) -> bool:
        count._obj.value = size  # ty: ignore[unresolved-attribute] - ctypes byref
        now[0] = 3_000_000_000
        return True

    native_api.WriteFile.side_effect = write
    with pytest.raises(ProtocolError, match="deadline"):
        pipe.write_bytes(b"x")
    native_api.WriteFile.assert_called_once()
    pipe.close()


@pytest.mark.parametrize(
    "value",
    [b"", bytearray(b"x"), b"x" * 65537],
    ids=["empty", "mutable", "oversized"],
)
def test_binary_chunk_bound_before_native_io(native_api: Mock, value: bytes) -> None:
    """Binary I/O cannot bypass the fixed chunk bound or immutable-buffer rule."""
    pipe = peer._Pipe(launch())
    with pytest.raises(ProtocolError, match="write-chunk"):
        pipe.write_bytes(value)
    native_api.WriteFile.assert_not_called()
    pipe.close()


@pytest.mark.parametrize("schema", [True, 1, 3])
def test_peer_schema_is_distinct_from_framing(schema: object) -> None:
    """Old/ambiguous handshake shapes cannot claim the new binary capability."""
    data = json.loads(launch().serialize())
    data["peer_schema"] = schema
    with pytest.raises(ValueError, match="peer schema"):
        peer.deserialize_launch(json.dumps(data))


def test_ns_clock_must_be_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected clock corruption cannot turn a deadline into an unbounded wait."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: True)
    with pytest.raises(ProtocolError, match="monotonic-ns"):
        peer.validate_deadline_ns(2_000_000_000)


def test_normal_close_failure_still_attempts_both_endpoints() -> None:
    """Clean protocol completion does not waive endpoint retirement errors."""
    control, frame = Mock(), Mock()
    frame.close.side_effect = OSError("first close")
    control.close.side_effect = OSError("second close")
    with pytest.raises(OSError, match="first close") as caught:
        peer._close_pipes([control, frame])
    frame.close.assert_called_once()
    control.close.assert_called_once()
    assert "second close" in caught.value.__notes__[0]


def test_peer_clock_history_shared_across_both_streams(
    native_api: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching endpoints cannot reset the per-observe clock or recover its failure."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    control, frame = peer._Pipe(launch()), peer._Pipe(launch(), frame=True)
    shared = peer.ObserveDeadline(2_000_000_000)
    control.observe_clock = frame.observe_clock = shared
    control.checkpoint()
    now[0] = 900_000_000
    with pytest.raises(ProtocolError, match="regressed") as first:
        frame.checkpoint()
    now[0] = 1_100_000_000
    with pytest.raises(ProtocolError, match="regressed") as second:
        control.checkpoint()
    assert first.value is second.value
    native_api.WriteFile.assert_not_called()
    control.close()
    frame.close()


@pytest.mark.parametrize("value", [True, float("nan"), None])
def test_observe_clock_rejects_bad_callback(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Invalid same-host clock values are permanently latched."""
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: value)
    state = peer.ObserveDeadline(2_000_000_000)
    with pytest.raises(ProtocolError, match="clock"):
        state.check()
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: 1_000_000_000)
    with pytest.raises(ProtocolError, match="clock"):
        state.check()


def test_peer_rejects_clock_regression_during_target_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsing cannot erase the authoritative pre-parse clock floor."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    target = peer.ObserveTarget(7, 1234, 99, (1, 1))
    parse = peer.ObserveTarget.parse

    def regressing_parse(data: bytes) -> peer.ObserveTarget:
        result = parse(data)
        now[0] = 900_000_000
        return result

    monkeypatch.setattr(peer.ObserveTarget, "parse", regressing_parse)
    protocol = peer.PeerProtocol()
    request = observe_request(target, 2_000_000_000)
    with pytest.raises(ProtocolError, match="regressed") as first:
        protocol.observe(request)
    assert protocol.failed
    assert protocol.observe_consumed
    assert protocol.last_id == 0
    assert protocol.observe_clock is not None
    now[0] = 1_100_000_000
    with pytest.raises(ProtocolError, match="regressed") as second:
        protocol.observe_clock.check()
    assert first.value is second.value
    with pytest.raises(ProtocolError):
        protocol.observe(request)
