"""Original-call evidence timing through actual new-profile native producers."""

from __future__ import annotations

import gc
import json
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast
from unittest.mock import Mock

import pytest
from tests.capture.test_moving_edit_owned_abi_extraction import prepare_constructor
from tests.capture.test_moving_edit_provider import (
    CaptureSource,
    _accepted,
    _check,
    _evidence,
    _native_fixture,
)
from tests.platform.test_moving_edit_evidence_protocol import (
    context,
    phase,
    send,
    setup,
)

from verdiclip.capture import edit_scroll_provider as module
from verdiclip.capture.edit_scroll_provider import MovingProvider, NativeBoundary
from verdiclip.capture.moving_edit_frame import PeerPixelSlots, discard_traceback
from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform.moving_edit_protocol import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_RECORDS,
    EvidenceRecord,
    ProtocolError,
    encode_evidence_record,
)


class StrictEvidence:
    """Require explicit original stamps and exact schemas before simulated ACK."""

    def __init__(self, clock: Callable[[], int]) -> None:
        self.clock = clock
        self.records: list[tuple[dict[str, Any], int]] = []
        self.pending: list[tuple[str, int]] = []
        self.handoff: Callable[[dict[str, Any]], None] = lambda _value: None
        self.broken = False

    def unstamped(self, _value: object) -> None:
        """Reject any production-style accidental fallback to plain callbacks."""
        raise AssertionError("Unstamped native evidence")

    def emit(self, value: dict[str, Any], *, timestamp_ns: int) -> None:
        """Retain the original producer timestamp, then model the ACK handoff."""
        if self.broken:
            raise OSError("Evidence reader lost")
        assert type(timestamp_ns) is int
        assert 0 <= timestamp_ns <= self.clock()
        assert not self.records or timestamp_ns >= self.records[-1][1]
        event = value["event"]
        if event == "call_start":
            assert set(value) == {"event", "name", "effective_deadline_ns"}
            assert type(value["effective_deadline_ns"]) is int
            assert value["effective_deadline_ns"] <= timestamp_ns + 3_000_000_000
            self.pending.append((value["name"], timestamp_ns))
        elif event == "call_end":
            assert set(value) == {"event", "seconds"}
            assert self.pending
            _, started = self.pending.pop()
            assert value["seconds"] == (timestamp_ns - started) / 1e9
        elif event == "edit_read":
            assert set(value) == {"event", "message", "index", "result", "timeout_ms"}
        elif event == "seek_start":
            assert set(value) == {"event", "vertical", "horizontal", "restoring"}
        elif event == "seek_end":
            assert set(value) == {
                "event",
                "vertical",
                "horizontal",
                "restoring",
                "status",
            }
        else:
            assert event in {
                "failure",
                "failure_restore_enter",
                "failure_restore_phase",
            }
        if event in {"call_start", "call_end", "edit_read", "seek_start", "seek_end"}:
            # Schema/size comes from the real raw wire producer, not this fake.
            encode_evidence_record(
                EvidenceRecord(len(self.records) + 1, timestamp_ns, value)
            )
        self.records.append((dict(value), timestamp_ns))
        self.handoff(value)

    def failure(self, reason: str, *, timestamp_ns: int) -> None:
        """Model controller context injection without changing the original clock."""
        self.emit({"event": "failure", "reason": reason}, timestamp_ns=timestamp_ns)

    def restore_enter(self, failure_ns: int, *, timestamp_ns: int) -> None:
        """Keep the first failure separate from subsequent entry delivery cost."""
        self.emit(
            {"event": "failure_restore_enter", "failure_ns": failure_ns},
            timestamp_ns=timestamp_ns,
        )

    def restore_phase(
        self, phase_id: int, operation: str, *, timestamp_ns: int
    ) -> None:
        """Keep each ordered phase's actual local entry timestamp."""
        self.emit(
            {
                "event": "failure_restore_phase",
                "phase_id": phase_id,
                "operation": operation,
            },
            timestamp_ns=timestamp_ns,
        )


def instrumented(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, CaptureSource, NativeBoundary, StrictEvidence]:
    """Run real boundary methods and real subclass raw against fake SDK calls."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    fake.now = 1.0
    journal = StrictEvidence(lambda: round(fake.now * 1e9))
    boundary = NativeBoundary(
        fake.native.target,
        _evidence(),
        30_000_000_000,
        _check,
        slots,
        journal.unstamped,
        evidence_emit=journal.emit,
        evidence_controller=journal,
    )
    return fake, source, boundary, journal


def test_native_start_is_original_not_ack_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow ACK consumes the original tighter phase, including before dispatch."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    boundary.set_deadline(2_500_000_000)
    fake.now = 1.25

    def ack(value: dict[str, Any]) -> None:
        if value["event"] == "call_start":
            fake.now += 0.2

    journal.handoff = ack
    native = Mock(return_value=123)
    assert fake.native.raw(native) == 123
    start, stamp = journal.records[-2]
    assert stamp == 1_250_000_000
    assert start["effective_deadline_ns"] == 2_500_000_000
    assert journal.records[-1][1] == 1_450_000_000
    native.assert_called_once()
    journal.handoff = lambda _value: None
    boundary.close()
    assert not journal.pending


@pytest.mark.parametrize("phase_ns", [1_500_000_000, 4_000_000_000])
def test_delayed_ack_at_exact_original_bound_never_dispatches(
    monkeypatch: pytest.MonkeyPatch, phase_ns: int
) -> None:
    """Both a tighter phase and the native three-second bound remain exclusive."""
    fake, source, boundary, journal = instrumented(monkeypatch)
    boundary.set_deadline(phase_ns)

    def ack(value: dict[str, Any]) -> None:
        if value["event"] == "call_start":
            fake.now = phase_ns / 1e9

    journal.handoff = ack
    native = Mock()
    with pytest.raises(RuntimeError, match="deadline"):
        fake.native.raw(native)
    native.assert_not_called()
    assert not journal.pending
    assert boundary.unsafe_write_reason is not None
    journal.handoff = lambda _value: None
    with pytest.raises(TimeoutError, match="cleanup"):
        boundary.close()
    assert source.closed == 1
    assert fake.native._closed


def test_native_exception_has_paired_end_and_preserves_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed failing call is not silently omitted from the raw transcript."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    first = OSError("native first")
    with pytest.raises(OSError, match="native first") as caught:
        fake.native.raw(Mock(side_effect=first))
    assert caught.value is first
    assert [record[0]["event"] for record in journal.records[-2:]] == [
        "call_start",
        "call_end",
    ]
    assert not journal.pending
    boundary.close()


@pytest.mark.parametrize("phase_ns", [2_000_000_000, 30_000_000_000])
def test_native_acquisition_keeps_returned_owner_before_end_failure(
    monkeypatch: pytest.MonkeyPatch, phase_ns: int
) -> None:
    """Post-return evidence loss cannot strand a HANDLE before registration."""
    fake, source, boundary, journal = instrumented(monkeypatch)
    boundary.set_deadline(phase_ns)
    released: list[int] = []
    first = OSError("lost acquisition end")

    def lose_end(value: dict[str, Any]) -> None:
        if value["event"] == "call_end":
            journal.broken = True
            raise first

    journal.handoff = lose_end
    handle = fake.native.raw(lambda: 123)
    fake.native.own(lambda: released.append(handle))
    assert handle == 123
    with pytest.raises(OSError, match="lost acquisition end") as caught:
        fake.native.boolean(handle)
    assert caught.value is first
    with pytest.raises(OSError, match="Evidence reader lost"):
        boundary.close()
    assert released == [123]
    assert source.closed == 1
    assert fake.native._closed


def test_actual_native_constructor_acquisition_closes_after_end_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual EditObservation construction owns OpenProcess output before error."""
    fake, close = prepare_constructor(monkeypatch)
    monkeypatch.setattr(
        module._TimedScrollObservation, "_edit_apis", lambda _native: None
    )
    monkeypatch.setattr(time, "monotonic_ns", lambda: round(fake.now * 1e9))
    fake.now = 1.0
    source = CaptureSource(fake)
    open_source = Mock(return_value=source)
    monkeypatch.setattr(module.mss, "MSS", open_source)
    journal = StrictEvidence(lambda: round(fake.now * 1e9))
    first = OSError("constructor post-return evidence")

    def lose_end(value: dict[str, Any]) -> None:
        if value["event"] == "call_end":
            journal.broken = True
            raise first

    journal.handoff = lose_end
    with pytest.raises(OSError, match="constructor post-return") as caught:
        NativeBoundary(
            fake.native.target,
            _evidence(),
            30_000_000_000,
            _check,
            PeerPixelSlots(),
            journal.unstamped,
            evidence_emit=journal.emit,
            evidence_controller=journal,
        )
    assert caught.value is first
    close.assert_called_once_with(123)
    open_source.assert_not_called()


def test_every_registered_native_release_is_paired_and_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inner HANDLE, COM, array and apartment owners have independent call pairs."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    released: list[int] = []
    for index in range(4):
        fake.native.own(lambda index=index: released.append(index))
    boundary.close()
    assert released == [3, 2, 1, 0]
    names = [
        value["name"] for value, _ in journal.records if value["event"] == "call_start"
    ]
    assert names.count("<lambda>") == 4
    assert names[-5] == "native.close"
    assert not journal.pending


def test_every_release_runs_even_when_start_and_end_logger_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostic availability never determines mandatory native release attempts."""
    fake, source, boundary, journal = instrumented(monkeypatch)
    owners = [Mock(), Mock(), Mock()]
    for owner in owners:
        fake.native.own(owner)
    journal.broken = True
    with pytest.raises(OSError, match="Evidence reader lost"):
        boundary.close()
    for owner in owners:
        owner.assert_called_once()
    assert source.closed == 1
    assert fake.native._closed
    assert boundary.unsafe_write_reason is not None


@pytest.mark.parametrize("fault", ["geometry", "source", "end"])
def test_mss_grab_retains_alias_and_pairs_actual_call_before_postcheck(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Bad output, native errors and evidence loss retain owned failure aliases."""
    _, source, boundary, journal = instrumented(monkeypatch)
    if fault == "geometry":
        monkeypatch.setattr(
            source,
            "grab",
            lambda _region: type("Shot", (), {"width": 1, "height": 1})(),
        )
    elif fault == "source":
        monkeypatch.setattr(
            source, "grab", Mock(side_effect=ValueError("source failed"))
        )
    else:

        def lose_end(value: dict[str, Any]) -> None:
            if value["event"] == "call_end":
                journal.broken = True
                raise OSError("end failed")

        journal.handoff = lose_end
    with pytest.raises((ValueError, OSError)) as caught:
        boundary._read_pixels()
    assert not journal.pending
    held = caught.value
    if fault == "end":
        with pytest.raises(OSError, match="Evidence reader lost"):
            boundary.close()
    else:
        boundary.close()
    assert boundary.slots.reserved_bytes > 0
    discard_traceback(held)
    gc.collect()
    assert boundary.slots.reserved_bytes == 0


def safe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, NativeBoundary, MovingProvider, StrictEvidence]:
    """A fully completed native write followed by a safe exact-frame mismatch."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    provider = MovingProvider(boundary, boundary.slots)
    provider.begin(_check)
    _accepted(provider, 1)
    provider.move(2, _check)
    fake.native.snapshot = Mock(return_value={"text_layout": {"first_visible_line": 8}})
    with pytest.raises(ValueError, match="capture pose") as caught:
        provider.capture(2, _check)
    discard_traceback(caught.value)
    gc.collect()
    assert provider.safe_observation_failed
    assert provider.failure_ns is not None
    # Restore the actual producer's snapshot, rather than inventing another read.
    monkeypatch.delattr(fake.native, "snapshot")
    return fake, boundary, provider, journal


def test_failure_restore_uses_original_failure_and_ordered_subphase_stamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No phase delivery or acknowledgement can renew the one-shot eight seconds."""
    fake, boundary, provider, journal = safe_failure(monkeypatch)
    failure_ns = provider.failure_ns
    assert failure_ns is not None

    def delay_entry(value: dict[str, Any]) -> None:
        if value["event"] == "failure_restore_enter":
            fake.now += 0.25

    journal.handoff = delay_entry
    provider.fail(_check)
    assert provider.restoration == "verified"
    assert provider.failed
    phases = [
        (value, stamp)
        for value, stamp in journal.records
        if value["event"] == "failure_restore_phase"
    ]
    assert [(value["phase_id"], value["operation"]) for value, _ in phases] == [
        (1, "guard"),
        (2, "write"),
        (3, "capture"),
        (4, "final"),
    ]
    assert phases[0][1] == failure_ns + 250_000_000
    assert boundary._restore_ceiling_ns == failure_ns + 8_000_000_000
    assert boundary.budget.phase_deadline_ns <= boundary._restore_ceiling_ns
    count = len(fake.writes)
    provider.fail(_check)
    assert len(fake.writes) == count
    boundary.close()


@pytest.mark.parametrize(
    "fault",
    ["reserve", "entry_ack", "guard_ack", "write_ack", "capture_ack", "final_ack"],
)
def test_restore_late_admission_or_phase_ack_never_renews_reserve(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Original per-phase ceilings include every record and its ACK latency."""
    fake, boundary, provider, journal = safe_failure(monkeypatch)
    before = len(fake.writes)
    if fault == "reserve":
        fake.now = 22.000000001
    else:
        phase = {"guard_ack": 1, "write_ack": 2, "capture_ack": 3, "final_ack": 4}.get(
            fault
        )

        def slow(value: dict[str, Any]) -> None:
            if fault == "entry_ack" and value["event"] == "failure_restore_enter":
                fake.now += 8
            if value["event"] == "failure_restore_phase" and value["phase_id"] == phase:
                assert phase is not None
                fake.now += {1: 2, 2: 3, 3: 2, 4: 1}[phase]

        journal.handoff = slow
    with pytest.raises(
        (RuntimeError, ValueError, TimeoutError), match=r"reserve|deadline|expired"
    ):
        provider.fail(_check)
    if fault in {"reserve", "entry_ack", "guard_ack", "write_ack"}:
        assert len(fake.writes) == before
    assert provider.restoration != "verified"
    journal.handoff = lambda _value: None
    with suppress(TimeoutError):
        boundary.close()


def test_evidence_failure_alias_cannot_confer_safe_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diagnostic error imitating a sampling mismatch still prohibits writes."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    provider = MovingProvider(boundary, boundary.slots)
    provider.begin(_check)
    _accepted(provider, 1)
    provider.move(2, _check)

    def alias(value: dict[str, Any]) -> None:
        if value["event"] == "call_start":
            raise ValueError("Readiness sample changed; no reset")

    journal.handoff = alias
    with pytest.raises(ValueError, match="Readiness sample"):
        provider.capture(2, _check)
    before = len(fake.writes)
    provider.fail(_check)
    assert len(fake.writes) == before
    assert provider.restoration == "prohibited"
    journal.handoff = lambda _value: None
    boundary.close()


def test_final_restore_comparison_cannot_overrun_original_final_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final comparison is charged to final entry, not just its journal ACK."""
    fake, boundary, provider, _ = safe_failure(monkeypatch)
    original = module.Captured.__eq__

    def slow_compare(left: module.Captured, right: object) -> bool:
        result = original(left, right)
        fake.now += 1.0
        return result

    monkeypatch.setattr(module.Captured, "__eq__", slow_compare)
    with pytest.raises(RuntimeError, match="deadline"):
        provider.fail(_check)
    assert provider.restoration != "verified"
    with suppress(TimeoutError):
        boundary.close()


def test_broken_float_clock_still_pairs_ns_stamped_mandatory_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure in supplemental timing cannot skip available original raw evidence."""
    fake, source, boundary, journal = instrumented(monkeypatch)
    release = Mock()
    fake.native.own(release)
    boundary._check_clock = Mock(side_effect=RuntimeError("legacy clock failed"))
    with pytest.raises(RuntimeError, match="legacy clock failed"):
        boundary.close()
    release.assert_called_once()
    assert source.closed == 1
    assert not journal.pending


@pytest.mark.parametrize("late_ack", [False, True])
def test_actual_native_producer_through_journal_reader_and_exact_ack(
    monkeypatch: pytest.MonkeyPatch, *, late_ack: bool
) -> None:
    """Real framing, journal and reader gate actual dispatch, not a dict-only fake."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    fake.now = 1.0
    reader, producer, parent = setup()
    packet = context(reader, parent, 1, 1_000_000_000)
    send(reader, producer, phase(packet, "phase_enter", 1_000_000_000), 1_000_000_000)
    journal = peer._Journal.__new__(peer._Journal)
    journal.producer = producer
    journal.failed = False
    journal.deadline_ns = 3_000_000_000
    exchange_records: list[bytes] = []
    delay = False

    class Client:
        """Persist exact encoded frames and return actual digest-correlated ACKs."""

        def exchange(self, frame: bytes, _deadline_ns: int) -> bytes:
            now = round(fake.now * 1e9)
            reader.prepare_peer(frame, now)
            exchange_records.append(frame)
            answer = reader.acknowledge_persisted(now)
            if delay:
                fake.now = 3.0
            return answer

        def _check(self, deadline_ns: int) -> None:
            if round(fake.now * 1e9) >= deadline_ns:
                raise ProtocolError("original deadline expired")

    journal.client = cast("Any", Client())
    boundary = NativeBoundary(
        fake.native.target,
        _evidence(),
        31_000_000_000,
        _check,
        slots,
        journal,
        evidence_emit=journal.emit,
        phase_deadline_ns=3_000_000_000,
    )
    native = Mock(return_value=123)
    delay = late_ack
    fake.now = 1.25
    if late_ack:
        with pytest.raises(ProtocolError):
            fake.native.raw(native)
        native.assert_not_called()
        assert journal.failed
        with pytest.raises(ProtocolError):
            boundary.close()
    else:
        assert fake.native.raw(native) == 123
        native.assert_called_once()
        boundary.close()
        assert not reader.calls
    assert source.closed == 1
    assert exchange_records


def test_seven_frame_fake_abi_native_body_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count actual fake-ABI producer frames, not a reconstruction or native claim."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    # This fixture stubs retained runtime-ID/structure helpers. Four explicit
    # fake owners exercise the new wrapper; no real-platform projection follows.
    for _ in range(4):
        fake.native.own(Mock())
    provider = MovingProvider(boundary, boundary.slots)
    provider.begin(_check)
    for step in range(1, 8):
        _accepted(provider, step)
    provider.finish(_check)
    boundary.close()
    frames = [
        encode_evidence_record(EvidenceRecord(index, stamp, value))
        for index, (value, stamp) in enumerate(journal.records, 1)
    ]
    count, byte_count = len(frames), sum(map(len, frames))
    assert count < MAX_EVIDENCE_RECORDS == 32768
    assert byte_count < MAX_EVIDENCE_BYTES == 4_000_000
    assert not journal.pending
    print(  # noqa: T201 -- retained machine-readable measurement in explicit test log
        json.dumps(
            {
                "measurement": "seven-frame fake-ABI native-body only",
                "records": count,
                "framed_bytes": byte_count,
                "call_starts": sum(
                    value["event"] == "call_start" for value, _ in journal.records
                ),
                "max_record_bytes": max(map(len, frames)),
                "fake_registered_owners": 4,
                "native_qualification": False,
                "omissions": (
                    "protocol hello/phase/final; fake retained identity helpers "
                    "do not acquire COM/BSTR/SAFEARRAY owners"
                ),
            },
            sort_keys=True,
        )
    )
