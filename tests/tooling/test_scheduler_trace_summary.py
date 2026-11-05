"""Private raw scheduler payloads never become diagnostic output."""

import json

import pytest
from scripts.scheduler_trace_summary import summarize

HEADER = [
    "BeginHeader",
    "CSwitch, TimeStamp, NewThreadId, OldThreadId, OldThreadState, OldThreadWaitReason",
    "ReadyThread, TimeStamp, TThreadId",
    "EndHeader",
]


def test_only_owned_thread_numeric_scheduler_fields_survive() -> None:
    """Unrelated process metadata and scheduling events are excluded."""
    result = summarize(
        [
            *HEADER,
            "Process, SECRET_COMMAND_LINE",
            "CSwitch, 100, 7, 8, 5, 6",
            "CSwitch, 110, 9, 10, 1, 0",
            "ReadyThread, 120, 7",
        ],
        7,
    )
    assert result["matched_events"] == 2
    assert result["truncated"] is False
    assert result["causal_claim"] is False
    assert "SECRET" not in json.dumps(result)


def test_trace_output_is_bounded_and_declares_truncation() -> None:
    """An over-budget export cannot look like complete timing evidence."""
    result = summarize(HEADER + ["ReadyThread, 120, 7"] * 1001, 7)
    assert result["matched_events"] == 1001
    assert result["truncated"] is True
    events = result["events"]
    assert isinstance(events, list)
    assert len(events) == 1000


@pytest.mark.parametrize(
    "rows",
    [
        [],
        ["CSwitch, 100, 7"],
        [*HEADER, "ReadyThread, NaN, 7"],
        [*HEADER, "ReadyThread, 100, 8"],
    ],
)
def test_missing_malformed_or_unrelated_evidence_is_rejected(rows: list[str]) -> None:
    """Missing ownership or schema never becomes a successful empty result."""
    with pytest.raises(ValueError, match="scheduler"):
        summarize(rows, 7)
