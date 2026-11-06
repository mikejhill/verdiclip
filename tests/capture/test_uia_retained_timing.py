"""Immutable first-operation evidence and fixed summaries survive tail eviction."""

from dataclasses import asdict, replace
from typing import cast

import pytest

from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace
from verdiclip.capture.scroll.uia_messages import UiaAction
from verdiclip.capture.scroll.uia_timing import LifetimeTiming, Outcome


def test_first_milestones_survive_warm_requests_and_tail_eviction() -> None:
    """Every first occurrence freezes, including terminal callback reentrancy."""
    now = 10.0
    trace = WorkerTrace(lambda: now)
    trace.begin_request(UiaAction.INSPECT)
    for stage in (
        "launch_requested",
        "process_started",
        "protocol_ready",
        "request_sent",
    ):
        now += 0.1
        trace.mark(stage)
    now += 0.2
    trace.finish_request("success")
    first = trace.timing.first
    original = trace.timing.actions
    for _ in range(50):
        now += 0.1
        trace.begin_request(UiaAction.SAMPLE)
        trace.mark("request_sent")
        now += 0.2
        trace.finish_request("success")
        trace.mark("response_received")
    assert trace.timing.first == first
    assert first.action is UiaAction.INSPECT
    assert first.queued_ms == 0
    assert first.protocol_ready_ms == 300
    assert first.request_sent_ms == 400
    assert first.terminal_ms == 600
    assert first.outcome == "success"
    assert len(trace.events) == 32
    assert trace.dropped_events > 100
    sample = trace.timing.actions[2]
    assert sample.accepted == sample.sent == sample.succeeded == 50
    assert sample.max_sent_to_terminal_ms == 200
    assert original[2].accepted == 0
    fresh = WorkerTrace(lambda: now)
    assert fresh.timing.first.action is None
    assert all(action.accepted == 0 for action in fresh.timing.actions)


@pytest.mark.parametrize(
    "outcome", ["failure", "cancelled", "startup_timeout", "operation_timeout"]
)
@pytest.mark.parametrize("sent", [False, True])
def test_failure_durations_and_once_only_accounting(
    outcome: Outcome, *, sent: bool
) -> None:
    """Failures are measured too, and repeated cleanup cannot finish twice."""
    timing = LifetimeTiming()
    timing.begin(UiaAction.OBSERVE, 10)
    if sent:
        timing.mark("request_sent", 20)
        timing.mark("request_sent", 30)
    timing.finish(outcome, 90)
    timing.finish("success", 100)
    summary = timing.actions[1]
    assert summary.accepted == 1
    assert summary.sent == int(sent)
    assert summary.succeeded == 0
    assert summary.failed + summary.cancelled + summary.timed_out == 1
    assert summary.max_queue_to_terminal_ms == 80
    assert summary.max_sent_to_terminal_ms == (70 if sent else None)
    assert summary.max_successful_sent_to_terminal_ms is None
    assert timing.first.outcome == outcome
    assert timing.first.terminal_ms == 90


def test_failure_and_success_maxima_are_distinct() -> None:
    """A subsequent success cannot overwrite the slow failed operation."""
    timing = LifetimeTiming()
    for start, end, outcome in ((0, 1000, "failure"), (1100, 1200, "success")):
        timing.begin(UiaAction.STEP, start)
        timing.mark("request_sent", start)
        timing.finish(outcome, end)
    result = timing.actions[3]
    assert result.max_sent_to_terminal_ms == 1000
    assert result.max_successful_sent_to_terminal_ms == 100
    assert result.failed == result.succeeded == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 1e300])
def test_invalid_clock_does_not_invent_elapsed_values(bad: float) -> None:
    """Null timing survives recovery and first missing milestones stay missing."""
    now = 0.0
    trace = WorkerTrace(lambda: now)
    now = bad
    trace.begin_request(UiaAction.RESTORE)
    trace.mark("protocol_ready")
    trace.mark("request_sent")
    now = 1.0
    trace.mark("protocol_ready")
    trace.finish_request("success")
    assert trace.timing_unavailable
    assert trace.timing.first.queued_ms is None
    assert trace.timing.first.protocol_ready_ms is None
    assert trace.timing.actions[4].succeeded == 1
    assert trace.timing.actions[4].max_sent_to_terminal_ms is None


def test_raising_clock_and_retention_failure_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional diagnostics never propagate clock or accounting errors."""

    def broken() -> float:
        raise RuntimeError("private exception")

    trace = WorkerTrace(broken)
    trace.begin_request(UiaAction.INSPECT)
    trace.mark("request_sent")
    trace.finish_request("success")
    assert trace.timing_unavailable
    assert trace.timing.first.terminal_ms is None

    def broken_finish(*_args: object) -> None:
        raise RuntimeError("private exception")

    monkeypatch.setattr(trace.timing, "finish", broken_finish)
    trace.finish_request("failure")
    assert "private" not in repr(asdict(trace.timing.first))


def test_saturated_counters_and_helper_text_cannot_expand_schema() -> None:
    """Only five fixed action records and fixed milestones are ever retained."""
    trace = WorkerTrace(lambda: 1.0)
    timing = trace.timing
    timing.actions = (
        replace(timing.actions[0], accepted=timing.MAX_COUNT),
        *timing.actions[1:],
    )
    trace.begin_request(UiaAction.INSPECT)
    received = trace.error_bytes(
        b"VERDICLIP_UIA_PHASE=protocol_ready\nprivate-provider-ID\n"
    )
    assert received
    assert timing.actions[0].accepted == timing.MAX_COUNT
    assert timing.actions[0].saturated
    assert timing.first.protocol_ready_ms is None
    assert len(timing.actions) == 5
    assert "private" not in repr(timing.actions) + repr(timing.first)


def test_clock_regression_never_becomes_a_negative_or_shorter_duration() -> None:
    """Relative time cannot move backward even if it remains above its origin."""
    now = 0.0
    trace = WorkerTrace(lambda: now)
    now = 1.0
    trace.begin_request(UiaAction.SAMPLE)
    now = 2.0
    trace.mark("request_sent")
    now = 1.5
    trace.finish_request("success")
    assert trace.timing_unavailable
    assert trace.timing.first.terminal_ms is None
    assert trace.timing.actions[2].max_queue_to_terminal_ms is None


def test_unrecognized_action_or_outcome_never_enters_receipts() -> None:
    """Even invalid internal inputs cannot widen fixed exported vocabularies."""
    trace = WorkerTrace(lambda: 0.0)
    trace.begin_request(cast(UiaAction, cast(object, "private-provider-value")))
    assert trace.timing.first.action is None
    trace.begin_request(UiaAction.INSPECT)
    trace.finish_request(cast(Outcome, cast(object, "private-provider-value")))
    assert trace.timing.first.outcome is None
    assert trace.timing_unavailable
    assert "private" not in repr(trace.timing.first) + repr(trace.timing.actions)
    trace.finish_request("failure")
    assert trace.timing.actions[0].failed == 1
