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
    GapStats,
    LatencyMetrics,
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
        b'{"version":2,"version":2}',
        b'{"version":true,"kind":"response","request_id":1,"payload":{}}',
        b'{"version":2,"kind":"response","request_id":true,"payload":{}}',
        b'{"version":2,"kind":"response","request_id":1,"payload":{"x":NaN}}',
        b'{"version":2,"kind":"response","request_id":1,"payload":{"x":1e999}}',
        b'{"version":2,"kind":"response","request_id":1,"payload":[],"extra":0}',
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
        {"version": 2, "kind": "response", "request_id": 1, "payload": {"x": value}}
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
    fake.latency = LatencyMetrics()
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
    fake.latency = LatencyMetrics()
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
    fake.latency = LatencyMetrics()
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
    fake.latency = LatencyMetrics()
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
    fake.latency = LatencyMetrics()
    Session._poll(cast("Session", fake))
    assert calls == ["read"]
    fake.eof = True
    fake.latency = LatencyMetrics()
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
    fake.latency = LatencyMetrics()
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


def test_latency_gap_stats_include_blocked_first_heartbeat() -> None:
    """Arming before startup ensures a blocking callback appears in the first gap."""
    heartbeat = GapStats()
    heartbeat.tick(0)
    heartbeat.tick(75_000_000)
    heartbeat.tick(85_000_000)
    assert heartbeat.report() == {
        "count": 2,
        "first_gap_ns": 75_000_000,
        "maximum_gap_ns": 75_000_000,
        "mean_gap_ns": 42_500_000,
    }


def test_latency_gap_storage_is_constant_space() -> None:
    """Longer waits grow counters, not a list of event-loop samples."""
    gaps = GapStats()
    for instant in range(10000):
        gaps.tick(instant * 10_000_000)
    assert gaps.count == 9999
    assert gaps.maximum_ns == 10_000_000
    assert len(vars(gaps)) == 5


def test_latency_spans_record_exceptions_without_changing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timing a failed native call cannot swallow or replace its failure."""
    now = iter([100, 175])
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: next(now)
    )
    metrics = LatencyMetrics()
    with (
        pytest.raises(RuntimeError, match="native failure"),
        metrics.measure("helper_start"),
    ):
        raise RuntimeError("native failure")
    spans = metrics.report()["spans_ns"]
    assert isinstance(spans, dict)
    assert spans["helper_start"] == 75


def test_latency_first_transitions_keep_cancel_and_cleanup_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated error callbacks cannot reset the measured cleanup interval."""
    now = iter([10, 12, 15, 20, 40, 45])
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: next(now)
    )
    metrics = LatencyMetrics()
    for mark in (
        "cancel_requested",
        "cleanup_started",
        "termination_requested",
        "cleanup_started",
        "cleanup_confirmed",
        "terminal",
    ):
        metrics.mark(mark)
    report = metrics.report()
    assert report["cancel_to_termination_request_ns"] == 5
    assert report["cancel_to_cleanup_confirmed_ns"] == 30
    assert report["cleanup_to_confirmed_ns"] == 28
    assert report["cleanup_to_terminal_ns"] == 33
    assert report["observation_only"] is True


def test_latency_missing_confirmed_cleanup_stays_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup timeout must not create a zero-duration successful cleanup sample."""
    now = iter([10, 60])
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: next(now)
    )
    metrics = LatencyMetrics()
    metrics.mark("cleanup_started")
    metrics.mark("terminal")
    report = metrics.report()
    assert report["cleanup_to_confirmed_ns"] is None
    assert report["cleanup_to_terminal_ns"] == 50
    assert report["cancel_to_cleanup_confirmed_ns"] is None


def test_latency_names_have_fixed_bounds() -> None:
    """Arbitrary metric keys cannot grow instrumentation state indefinitely."""
    metrics = LatencyMetrics()
    with (
        pytest.raises(ValueError, match="Unknown latency span"),
        metrics.measure("unbounded-key"),
    ):
        pytest.fail("Unknown span entered")
    with pytest.raises(ValueError, match="Unknown latency transition"):
        metrics.mark("unbounded-key")
    assert not metrics.spans
    assert not metrics.marks


def test_start_callback_span_and_first_heartbeat_capture_launch_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instrumenting the callback observes blocking work without changing its path."""
    clock = [0]
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: clock[0]
    )
    metrics = LatencyMetrics()
    metrics.heartbeat.tick(0)

    def start() -> None:
        with metrics.measure("helper_start"):
            clock[0] += 45_000_000
        clock[0] += 5_000_000

    fake = SimpleNamespace(latency=metrics, _start=start, done=False)
    Session.start(cast("Session", fake))
    Session._heartbeat(cast("Session", fake))
    assert metrics.spans == {"helper_start": 45_000_000, "start_callback": 50_000_000}
    assert metrics.heartbeat.maximum_ns == 50_000_000


def test_ready_verification_span_keeps_identity_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record the existing verify_ready call without bypassing its result."""
    clock = [0]
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: clock[0]
    )
    sent = []

    def verify(_pid: int, _executable: str) -> SimpleNamespace:
        clock[0] += 3_000_000
        return SimpleNamespace(producer_verified=True)

    metrics = LatencyMetrics()
    fake = SimpleNamespace(
        phase="startup",
        verified_pid=123,
        case="fragmented",
        latency=metrics,
        helper=SimpleNamespace(verify_ready=verify),
        _check_operation_deadline=lambda: None,
        _send=sent.append,
    )
    Session._message(
        cast("Session", fake),
        Message("ready", 0, {"pid": 123, "executable": "C:\\owned-python.exe"}),
    )
    assert metrics.spans["verify_ready"] == 3_000_000
    assert sent == [{"operation": "ping"}]


def test_latency_kernel_retirement_before_buffered_final_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retirement observed before protocol cleanup is not negative latency."""
    now = iter([10, 20, 30])
    monkeypatch.setattr(
        "scripts.owned_pipe_smoke.time.perf_counter_ns", lambda: next(now)
    )
    metrics = LatencyMetrics()
    metrics.mark("cleanup_confirmed")
    metrics.mark("cleanup_started")
    metrics.mark("terminal")
    report = metrics.report()
    assert report["cleanup_to_confirmed_ns"] is None
    assert report["cleanup_to_terminal_ns"] == 10
    assert report["transition_ordering"] == {
        "cancel_requested_to_termination_requested": "missing_observation",
        "cancel_requested_to_cleanup_confirmed": "missing_observation",
        "cleanup_started_to_cleanup_confirmed": "end_observed_before_start",
        "cleanup_started_to_terminal": "in_order",
    }
    assert metrics.marks == {
        "cleanup_confirmed": 10,
        "cleanup_started": 20,
        "terminal": 30,
    }
