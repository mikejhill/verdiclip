"""Portable protocol proofs; never instantiate native Win32/Qt transport."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.owned_pipe_smoke import (
    HEADER_BYTES,
    MAX_JSON_DEPTH,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    Decoder,
    Message,
    ProtocolError,
    Session,
    SingleFlight,
    decode,
    encode,
)


def test_every_byte_boundary_can_fragment() -> None:
    """Header and UTF-8 payload splits preserve exactly one complete message."""
    message = Message("response", 1, {"text": "écho"})
    wire = encode(message)
    for cut in range(len(wire) + 1):
        decoder = Decoder()
        assert decoder.feed(wire[:cut]) + decoder.feed(wire[cut:]) == [message]
        assert decoder.buffer == b""
    decoder = Decoder()
    received = []
    for value in wire:
        received.extend(decoder.feed(bytes([value])))
    assert received == [message]


def test_coalesced_messages_and_partial_tail() -> None:
    """Multiple bounded frames do not lose the incomplete trailing message."""
    first, second = Message("ready", 0, {}), Message("response", 1, {})
    a, b = encode(first), encode(second)
    decoder = Decoder()
    assert decoder.feed(a + b[:8]) == [first]
    assert decoder.feed(b[8:]) == [second]


@pytest.mark.parametrize("size", [0, MAX_MESSAGE_BYTES + 1, 0xFFFFFFFF])
def test_bad_length_rejects_before_payload_and_latches(size: int) -> None:
    """A hostile header cannot request an allocation or recover after rejection."""
    decoder = Decoder()
    with pytest.raises(ProtocolError, match="oversized"):
        decoder.feed(struct.pack(">I", size))
    assert decoder.buffer == b""
    with pytest.raises(ProtocolError, match="decoder-failed"):
        decoder.feed(encode(Message("response", 1, {})))


def test_buffer_limit_even_without_message_completion() -> None:
    """Raw incoming buffers have the same explicit bounded allocation ceiling."""
    decoder = Decoder()
    with pytest.raises(ProtocolError, match="buffer-limit"):
        decoder.feed(b"x" * (MAX_MESSAGE_BYTES + HEADER_BYTES + 1))
    assert decoder.failed


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"{}",
        b"[]",
        b"{",
        b'{"version":1,"version":1}',
        b'{"version":true,"kind":"response","request_id":1,"payload":{}}',
        b'{"version":1,"kind":"response","request_id":true,"payload":{}}',
        b'{"version":1,"kind":"response","request_id":1,"payload":{"x":NaN}}',
        b'{"version":1,"kind":"response","request_id":1,"payload":{"x":1e999}}',
        b'{"version":1,"kind":"response","request_id":1,"payload":[],"extra":0}',
    ],
)
def test_malformed_envelopes_reject(payload: bytes) -> None:
    """Identity fields are not coerced, overwritten or nonfinite."""
    with pytest.raises(ProtocolError):
        Decoder().feed(struct.pack(">I", len(payload)) + payload)


def test_nesting_and_request_budget() -> None:
    """Small byte count is not permission for deeply nested JSON or endless IDs."""
    value: object = 1
    for _ in range(MAX_JSON_DEPTH + 1):
        value = [value]
    wire = json.dumps(
        {"version": 1, "kind": "response", "request_id": 1, "payload": {"x": value}}
    ).encode()
    with pytest.raises(ProtocolError, match="json-complexity"):
        decode(wire)
    with pytest.raises(ProtocolError, match="invalid-envelope"):
        encode(Message("response", MAX_REQUESTS + 1, {}))
    with pytest.raises(ProtocolError, match="oversized"):
        encode(Message("response", 1, {"text": "x" * MAX_MESSAGE_BYTES}))


def test_single_flight_clears_only_matching_response() -> None:
    """A second request cannot overlap an unacknowledged first operation."""
    flight = SingleFlight()
    assert flight.begin({"operation": "ping"}).request_id == 1
    with pytest.raises(ProtocolError, match="single-flight"):
        flight.begin({})
    assert flight.pending == 1
    flight.accept(Message("response", 1, {}))
    assert flight.pending is None
    assert flight.begin({}).request_id == 2


@pytest.mark.parametrize(
    ("request_id", "expected"), [(0, "duplicate"), (2, "wrong-id")]
)
def test_wrong_ids_fail_closed(request_id: int, expected: str) -> None:
    """No response ID can authorize the next step unless it is the pending ID."""
    flight = SingleFlight()
    flight.begin({})
    with pytest.raises(ProtocolError, match=expected):
        flight.accept(Message("response", request_id, {}))
    assert flight.pending == 1
    with pytest.raises(ProtocolError, match="single-flight"):
        flight.accept(Message("response", 1, {}))


def test_duplicate_after_next_request_rejects() -> None:
    """A duplicate reply does not satisfy a subsequently issued operation."""
    flight = SingleFlight()
    flight.begin({})
    flight.accept(Message("response", 1, {}))
    flight.begin({})
    with pytest.raises(ProtocolError, match="duplicate"):
        flight.accept(Message("response", 1, {}))
    assert flight.pending == 2


def test_unsolicited_and_wrong_kind_reject() -> None:
    """Ready is a startup handshake, never an ordinary operation response."""
    with pytest.raises(ProtocolError, match="wrong-id"):
        SingleFlight().accept(Message("response", 1, {}))
    flight = SingleFlight()
    flight.begin({})
    with pytest.raises(ProtocolError, match="unexpected-kind"):
        flight.accept(Message("ready", 0, {}))


def test_request_count_is_bounded() -> None:
    """Even a valid endless peer cannot exceed the fixed session request ceiling."""
    flight = SingleFlight()
    for request_id in range(1, MAX_REQUESTS + 1):
        assert flight.begin({}).request_id == request_id
        flight.accept(Message("response", request_id, {}))
    with pytest.raises(ProtocolError, match="single-flight"):
        flight.begin({})


@pytest.mark.parametrize("phase", ["startup", "command"])
def test_qt_deadline_callback_without_native_calls(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Timer callbacks expire fixed deadlines even without incoming traffic."""
    failures = []
    fake = SimpleNamespace(
        done=False,
        phase=phase,
        phase_deadline=2,
        session_deadline=20,
        _fail=failures.append,
    )
    monkeypatch.setattr("scripts.owned_pipe_smoke.time.monotonic", lambda: 2)
    Session._deadline(cast("Session", fake))
    assert failures == [f"{phase}-timeout"]


def test_cleanup_deadline_does_not_claim_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup timeout is distinct evidence, never a success or an infinite wait."""
    calls = []
    fake = SimpleNamespace(
        done=False,
        phase="cleanup",
        phase_deadline=5,
        session_deadline=20,
        cleanup_failure=None,
        helper=SimpleNamespace(release_unconfirmed=lambda _: calls.append("release")),
        _finish=lambda: calls.append("finish"),
    )
    monkeypatch.setattr("scripts.owned_pipe_smoke.time.monotonic", lambda: 5)
    Session._deadline(cast("Session", fake))
    assert fake.cleanup_failure == "cleanup-timeout"
    assert calls == ["release", "finish"]


def test_session_deadline_cannot_be_extended_by_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session deadline remains independent of individual request deadlines."""
    failures = []
    fake = SimpleNamespace(
        done=False,
        phase="command",
        phase_deadline=25,
        session_deadline=20,
        _fail=failures.append,
    )
    monkeypatch.setattr("scripts.owned_pipe_smoke.time.monotonic", lambda: 20)
    Session._deadline(cast("Session", fake))
    assert failures == ["session-timeout"]


def test_kernel_peer_mismatch_rejects_before_handshake() -> None:
    """Self-reported ready fields cannot replace the kernel's pipe-client identity."""
    failures = []
    socket = SimpleNamespace(
        setReadBufferSize=lambda _: None, socketDescriptor=lambda: 123
    )
    fake = SimpleNamespace(
        server=SimpleNamespace(nextPendingConnection=lambda: socket),
        socket=None,
        phase="startup",
        api=SimpleNamespace(client_pid=lambda _: 222),
        helper=SimpleNamespace(
            status=SimpleNamespace(pid=111),
            poll=lambda: SimpleNamespace(remaining_pids=(111, 222)),
        ),
        _fail=failures.append,
    )
    Session._connected(cast("Session", fake))
    assert failures == ["wrong-peer-pid"]


def test_ready_pid_must_match_preverified_kernel_peer() -> None:
    """A forged ready PID fails before asking OwnedHelper to verify its image."""
    fake = SimpleNamespace(
        phase="startup", verified_pid=111, _check_operation_deadline=lambda: None
    )
    with pytest.raises(ProtocolError, match="invalid-ready"):
        Session._message(
            cast("Session", fake),
            Message("ready", 0, {"pid": 222, "executable": "C:\\python.exe"}),
        )


def test_cancel_does_not_enqueue_protocol_operation() -> None:
    """Cancellation remains possible while the child is blocked in ReadFile."""
    failures = []
    fake = SimpleNamespace(_fail=failures.append)
    Session.cancel(cast("Session", fake))
    assert failures == ["cancelled"]


def test_clean_exit_before_queued_final_bytes_keeps_command_deadline() -> None:
    """Kernel retirement may precede Qt readyRead; do not turn that into failure."""
    calls = []
    status = SimpleNamespace(exit_code=0, cleanup_confirmed=True, failure=None)
    fake = SimpleNamespace(
        done=False,
        phase="command",
        helper=SimpleNamespace(poll=lambda: status),
        _readable=lambda: calls.append("read"),
        _fail=calls.append,
        failure=None,
        _deadline=lambda: None,
    )
    Session._poll(cast("Session", fake))
    assert calls == ["read"]
    assert fake.phase == "command"


def test_final_bytes_drained_before_cleanup_completion() -> None:
    """The last reply is processed even when lifetime polling first sees exit."""
    calls = []
    status = SimpleNamespace(exit_code=0, cleanup_confirmed=True, failure=None)
    fake = SimpleNamespace(
        done=False,
        phase="command",
        helper=SimpleNamespace(poll=lambda: status),
        decoder=SimpleNamespace(buffer=b""),
        _finish=lambda: calls.append("finish"),
        failure=None,
        eof=True,
        _deadline=lambda: None,
    )

    def read() -> None:
        fake.phase = "cleanup"
        calls.append("read")

    fake._readable = read
    Session._poll(cast("Session", fake))
    assert calls[-1] == "finish"
    assert calls[0] == "read"


@pytest.mark.parametrize(
    ("now", "session_deadline", "code"),
    [(2, 20, "command-timeout"), (2, 2, "session-timeout")],
)
def test_overdue_final_reply_cannot_win_timer_race(
    monkeypatch: pytest.MonkeyPatch, now: float, session_deadline: float, code: str
) -> None:
    """A final reply admitted before the timer callback still obeys absolute time."""
    flight = SingleFlight()
    flight.begin({})
    flight.accept(Message("response", 1, {}))
    flight.begin({})
    fake = SimpleNamespace(
        phase="command",
        phase_deadline=2,
        session_deadline=session_deadline,
        flight=flight,
        _cleanup=lambda: pytest.fail("Late reply admitted"),
    )
    fake._check_operation_deadline = lambda: Session._check_operation_deadline(
        cast("Session", fake)
    )
    monkeypatch.setattr("scripts.owned_pipe_smoke.time.monotonic", lambda: now)
    with pytest.raises(ProtocolError, match=code):
        Session._message(
            cast("Session", fake), Message("response", 2, {"operation": "finished"})
        )
    assert flight.pending == 2


def test_cleanup_poll_cannot_win_expired_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retirement observed after the watchdog deadline cannot become a timely pass."""
    calls = []
    fake = SimpleNamespace(
        done=False,
        phase="cleanup",
        phase_deadline=5,
        session_deadline=20,
        cleanup_failure=None,
        helper=SimpleNamespace(
            release_unconfirmed=lambda _: calls.append("release"),
            poll=lambda: pytest.fail("Expired poll must not admit cleanup"),
        ),
    )

    def finish() -> None:
        fake.done = True
        calls.append("finish")

    fake._finish = finish
    fake._deadline = lambda: Session._deadline(cast("Session", fake))
    monkeypatch.setattr("scripts.owned_pipe_smoke.time.monotonic", lambda: 5)
    Session._poll(cast("Session", fake))
    assert calls == ["release", "finish"]
    assert fake.cleanup_failure == "cleanup-timeout"


def test_retired_child_without_qt_eof_is_not_success() -> None:
    """Late bytes remain observable after final reply and producer retirement."""
    calls = []
    status = SimpleNamespace(exit_code=0, cleanup_confirmed=True, failure=None)
    fake = SimpleNamespace(
        done=False,
        phase="cleanup",
        failure=None,
        eof=False,
        decoder=SimpleNamespace(buffer=b""),
        helper=SimpleNamespace(poll=lambda: status),
        _deadline=lambda: False,
        _readable=lambda: calls.append("read"),
        _finish=lambda: calls.append("finish"),
    )
    Session._poll(cast("Session", fake))
    assert calls == ["read"]
    fake.eof = True
    Session._poll(cast("Session", fake))
    assert calls[-1] == "finish"


def test_nonzero_exit_after_finished_reply_is_failure() -> None:
    """A completed protocol cannot conceal an unsuccessful producer exit."""
    calls = []
    status = SimpleNamespace(
        exit_code=23, cleanup_confirmed=True, failure="exit failed"
    )
    fake = SimpleNamespace(
        done=False,
        phase="cleanup",
        failure=None,
        eof=True,
        decoder=SimpleNamespace(buffer=b""),
        helper=SimpleNamespace(poll=lambda: status),
        _deadline=lambda: False,
        _readable=lambda: None,
        _finish=lambda: calls.append("finish"),
    )

    def fail(reason: str) -> None:
        fake.failure = reason
        calls.append(reason)

    fake._fail = fail
    Session._poll(cast("Session", fake))
    assert calls == ["unexpected-exit", "finish"]


def test_late_duplicate_after_finished_is_still_rejected() -> None:
    """Cleanup phase parses trailing complete replies rather than dropping them."""
    flight = SingleFlight()
    for request_id in (1, 2):
        flight.begin({})
        flight.accept(Message("response", request_id, {}))
    fake = SimpleNamespace(
        phase="cleanup", flight=flight, _check_operation_deadline=lambda: None
    )
    with pytest.raises(ProtocolError, match="duplicate"):
        Session._message(
            cast("Session", fake), Message("response", 2, {"operation": "finished"})
        )


def test_eof_with_trailing_partial_frame_is_failure() -> None:
    """Clean process death does not make truncated trailing bytes a valid protocol."""
    failures = []
    fake = SimpleNamespace(
        done=False,
        phase="cleanup",
        eof=False,
        decoder=SimpleNamespace(buffer=b"\x00\x00"),
        _readable=lambda: None,
        _fail=failures.append,
    )
    Session._disconnected(cast("Session", fake))
    assert fake.eof
    assert failures == ["truncated-message"]


def test_wrong_peer_fixture_spawns_only_contained_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative identity fixture uses no breakaway or detached process flags."""
    from scripts import owned_pipe_smoke  # noqa: PLC0415 - patch only this fixture

    calls = []
    monkeypatch.setattr(
        owned_pipe_smoke.HelperCommand,
        "base_python",
        lambda arguments, _directory: SimpleNamespace(
            executable="owned-python.exe", arguments=arguments
        ),
    )

    def spawn(
        arguments: list[str], *, creationflags: int, close_fds: bool
    ) -> SimpleNamespace:
        calls.append((arguments, creationflags, close_fds))
        return SimpleNamespace(wait=lambda *, timeout: calls.append(timeout))

    monkeypatch.setattr(owned_pipe_smoke.subprocess, "Popen", spawn)
    owned_pipe_smoke.wrong_peer("owned-pipe-name")
    arguments, flags, close_fds = calls[0]
    assert arguments[0] == "owned-python.exe"
    assert arguments[-4:] == ["--pipe", "owned-pipe-name", "--case", "fragmented"]
    assert flags == 0x08000000
    assert close_fds is True
    assert calls[1] == 60
