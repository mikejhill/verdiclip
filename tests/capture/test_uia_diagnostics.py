"""Content-free helper diagnostics stay bounded and tolerate stream fragments."""

import pytest

from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace


@pytest.mark.parametrize("split", [1, 7, 33])
def test_fragmented_fixed_boot_marker_is_recognized(split: int) -> None:
    """Qt may deliver one stderr line in several independent chunks."""
    trace = WorkerTrace(lambda: 0)
    marker = b"VERDICLIP_UIA_PHASE=script_entered\r\n"
    assert trace.error_bytes(marker[:split])
    assert not trace.events
    assert trace.error_bytes(marker[split:])
    assert [event.stage for event in trace.events] == ["script_entered"]


def test_unrelated_provider_text_never_enters_the_journal() -> None:
    """Only exact fixed markers survive; content and spoofed suffixes do not."""
    trace = WorkerTrace(lambda: 0)
    assert trace.error_bytes(b"private-window-title\nVERDICLIP_UIA_PHASE=secret\n")
    assert trace.error_bytes(b"VERDICLIP_UIA_PHASE=pipe_connected-extra\n")
    assert not trace.events
    assert trace.error_bytes(b"VERDICLIP_UIA_PHASE=runtime_ready\n")
    assert [event.stage for event in trace.events] == ["runtime_ready"]
    assert "private" not in repr(trace.events)
    assert "secret" not in repr(trace.events)


def test_repeated_lifecycle_events_have_a_finite_journal() -> None:
    """A warm worker cannot accumulate one unbounded record per frame."""
    trace = WorkerTrace(lambda: 0)
    for _ in range(trace.MAX_EVENTS + 5):
        trace.mark("request_sent")
    assert len(trace.events) == trace.MAX_EVENTS
    assert trace.dropped_events == 5


def test_many_small_diagnostic_writes_share_one_byte_budget() -> None:
    """Splitting stderr output cannot evade the worker's diagnostic limit."""
    trace = WorkerTrace(lambda: 0)
    assert trace.error_bytes(b"x" * (trace.MAX_ERROR_BYTES // 2))
    assert trace.error_bytes(b"x" * (trace.MAX_ERROR_BYTES // 2))
    assert not trace.error_bytes(b"x")
    assert not trace.events
