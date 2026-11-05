"""Private raw scheduler payloads never become diagnostic output."""

import json

import pytest
from scripts.scheduler_trace_summary import numeric_fields, schema_summary, summarize

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


def test_schema_diagnostics_exclude_all_payload_rows_and_unrelated_headers() -> None:
    """Static scheduler fields can be inspected without printing trace payloads."""
    result = schema_summary(
        [
            *HEADER,
            "Process, SECRET_PAYLOAD",
            "CSwitch, SECRET_PAYLOAD",
            "ReadyThread, 42, 7",
        ]
    )
    assert result["scheduler_row_counts"] == {"CSwitch": 1, "ReadyThread": 1}
    assert "SECRET" not in json.dumps(result)
    headers = result["scheduler_headers"]
    assert isinstance(headers, dict)
    assert headers["CSwitch"][1] == "TimeStamp"


def test_actual_xperf_columns_use_ready_target_not_emitter() -> None:
    """Real exporter names distinguish the readying thread from its target."""
    result = summarize(
        [
            "BeginHeader",
            (
                "CSwitch, TimeStamp, New Process Name ( PID), New TID, "
                "Old Process Name ( PID), Old TID, OldState, Wait Reason"
            ),
            (
                "ReadyThread, TimeStamp, Process Name ( PID), ThreadID, "
                "Rdy Process Name ( PID), Rdy TID, AdjustReason"
            ),
            "EndHeader",
            "CSwitch, 100, SECRET, 7, OTHER, 8, Waiting, Executive",
            "ReadyThread, 105, SECRET, 7, OTHER, 8, 0",
            "ReadyThread, 110, SECRET, 8, OTHER, 7, SymbolicPriorityBoost",
        ],
        7,
    )
    assert result["matched_events"] == 2
    events = result["events"]
    assert isinstance(events, list)
    assert events[0]["oldthreadstate"] == 5
    assert events[1]["tthreadid"] == 7
    assert "SECRET" not in json.dumps(result)


def test_emitter_only_ready_schema_cannot_prove_target_identity() -> None:
    """Missing Rdy TID must not silently use the emitter ThreadID."""
    with pytest.raises(ValueError, match="scheduler thread identity"):
        summarize(
            [
                "BeginHeader",
                "ReadyThread, TimeStamp, ThreadID",
                "EndHeader",
                "ReadyThread, 100, 7",
            ],
            7,
        )


def test_numeric_failure_reports_field_not_private_payload() -> None:
    """Invalid trace values never leak into the diagnostic reason."""
    with pytest.raises(
        ValueError, match=r"^Non-numeric scheduler field oldthreadstate$"
    ) as error:
        numeric_fields({"oldthreadstate": "PRIVATE_CONTENT"})
    assert "PRIVATE_CONTENT" not in str(error.value)
