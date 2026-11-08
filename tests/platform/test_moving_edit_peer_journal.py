"""The peer journal admits only independently persisted, acknowledged evidence."""

from __future__ import annotations

import io
from typing import override

import pytest
from tests.platform.test_moving_edit_evidence_native import (
    EvidenceLoopback,
    launch_data,
)

from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform import moving_edit_protocol as wire


def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[EvidenceLoopback, peer._Journal]:
    """Use the actual producer, fake Win32 pipe and independent reader."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    loop = EvidenceLoopback(monkeypatch, now, launch_data())
    return loop, peer._Journal(loop.launch, lambda: None)


def test_journal_exact_streamed_bytes_and_irreversible_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual raw frame is persisted, with no stderr or evidence-record list."""
    loop, journal = setup(monkeypatch)
    raw = loop.reader.raw.getvalue()
    assert wire.decode_evidence_record(raw).event["event"] == "hello"
    assert journal.producer.count == loop.state.count == 1
    assert journal.producer.byte_count == len(raw)
    monkeypatch.setattr(wire, "MAX_EVIDENCE_RECORDS", 1)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": loop.now[0],
            "command_deadline_ns": loop.now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    with pytest.raises(ValueError, match=r".+"):
        journal.phase_enter(request)
    with pytest.raises(ValueError, match="journal-failed"):
        journal.emit({"event": "call_end", "seconds": 0}, timestamp_ns=loop.now[0])
    assert loop.reader.raw.getvalue() == raw


@pytest.mark.parametrize("mode", ["record-bytes", "journal-bytes", "io", "flush"])
def test_journal_failure_latches_without_extra_diagnostic_attempts(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Failed schema/storage cannot emit an ACK or silently drop native records."""
    loop, journal = setup(monkeypatch)
    request = peer.Message(
        "request",
        1,
        {
            "operation": "moving_begin",
            "command_started_ns": loop.now[0],
            "command_deadline_ns": loop.now[0] + 2_000_000_000,
        },
    )
    loop.context_for(request)
    journal.phase_enter(request)
    value = {
        "event": "call_start",
        "name": "GetClientRect",
        "effective_deadline_ns": loop.now[0] + 2_000_000_000,
    }
    if mode == "record-bytes":
        value["name"] = "x" * 4096
    elif mode == "journal-bytes":
        monkeypatch.setattr(wire, "MAX_EVIDENCE_BYTES", journal.producer.byte_count)
    elif mode == "io":
        loop.reader.raw.close()
    else:

        class FailedFlush(io.BytesIO):
            @override
            def flush(self) -> None:
                """Inject failed persistence after the actual raw append."""
                raise OSError("raw flush failed")

        loop.reader.raw = FailedFlush(loop.reader.raw.getvalue())
    ack_bytes_before = sum(
        len(data) for handle, data in loop.kernel.sent if handle == 101
    )
    with pytest.raises((ValueError, OSError), match=r".+"):
        journal.emit(value, timestamp_ns=loop.now[0])
    assert journal.failed
    assert (
        sum(len(data) for handle, data in loop.kernel.sent if handle == 101)
        == ack_bytes_before
    )


def test_original_call_timestamp_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """A callback that loses the actual native start cannot silently restamp it."""
    _loop, journal = setup(monkeypatch)
    with pytest.raises(ValueError, match="original-call-stamp-required"):
        journal({"event": "call_start", "name": "GetClientRect"})
