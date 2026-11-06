"""Phase-aligned scheduler aggregation tested without native APIs or desktop use."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO

import pytest
from scripts import scheduler_phase_summary as module
from scripts.scheduler_phase_summary import (
    aggregate,
    main,
    scheduler_segments,
    verify_independent_decoder,
)


def evidence() -> tuple[list[list[str]], dict[str, object]]:
    """Use QPC at 1kHz and a phase spanning running, waiting and ready time."""
    rows = [
        ["L", "0", "1", "10", "20"],
        ["S", "1", "20", "30", "5"],
        ["S", "20", "30", "20", "5"],
        ["R", "60", "20"],
        ["S", "80", "20", "30", "1"],
        ["L", "100", "2", "10", "20"],
        ["H", "1", "1000", "0", "0", "0", "0", "0", "7"],
    ]
    phases: dict[str, object] = {
        "pid": 10,
        "tid": 20,
        "errors": [],
        "truncated": False,
        "intervals": [["editor_show", 1, 10_000_000, 90_000_000]],
    }
    return rows, phases


@pytest.mark.parametrize(
    "phase_name", ["editor_show", "hotkey_message_handler", "capture_pending_callback"]
)
def test_exact_phase_clipping_distinguishes_waiting_from_ready_and_scheduled(
    phase_name: str,
) -> None:
    """Whole-process delays outside the measured phase cannot pollute its totals."""
    rows, phases = evidence()
    phases["intervals"] = [[phase_name, 1, 10_000_000, 90_000_000]]
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is True
    assert report["alignment_endpoint_rounding_bound_ns"] == 1_000_001
    assert report["event_loss_checked"] is True
    assert report["thread_lifetime_checked"] is True
    assert report["phases"] == [
        {
            "phase": phase_name,
            "capture_ordinal": 1,
            "wall_ms": 80.0,
            "scheduled_ms": 20.0,
            "waiting_ms": 40.0,
            "ready_ms": 20.0,
            "unknown_ms": 0.0,
            "state_complete": True,
        }
    ]
    assert report["causal_claim"] is False


@pytest.mark.parametrize(
    "phase_name", ["hotkey_message_handler", "capture_pending_callback"]
)
def test_new_phase_labels_do_not_hide_unknown_scheduler_time(phase_name: str) -> None:
    """Valid phase labels cannot make incomplete scheduling evidence complete."""
    rows, phases = evidence()
    phases["intervals"] = [[phase_name, 1, 10_000_000, 90_000_000]]
    rows[2][4] = "99"
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is False
    entries = report["phases"]
    assert isinstance(entries, list)
    assert entries[0]["phase"] == phase_name
    assert entries[0]["unknown_ms"] == 40
    assert entries[0]["state_complete"] is False
    assert report["causal_claim"] is False


@pytest.mark.parametrize("column", [1, 2, 3, 4, 5, 6, 7])
def test_clock_loss_or_reader_failure_cannot_be_qualified(column: int) -> None:
    """Any missing clock or trace-loss/retirement evidence fails closed."""
    rows, phases = evidence()
    rows[-1][column] = "0" if column in (1, 2) else "1"
    with pytest.raises(ValueError, match="trace loss or clock"):
        aggregate(rows, phases)


@pytest.mark.parametrize(
    "change",
    [
        "missing_birth",
        "missing_death",
        "wrong_pid",
        "reused_tid",
        "time_inversion",
        "phase_outside",
        "unknown_state",
    ],
)
def test_lifecycle_and_order_gaps_are_never_hidden(change: str) -> None:
    """Reject reuse and missing coverage; retain unknown scheduler states explicitly."""
    rows, phases = evidence()
    if change == "missing_birth":
        rows.pop(0)
    elif change == "missing_death":
        rows.pop(-2)
    elif change == "wrong_pid":
        rows[0][3] = "99"
    elif change == "reused_tid":
        rows.insert(-1, ["L", "200", "1", "99", "20"])
    elif change == "time_inversion":
        rows[3][1] = "19"
    elif change == "phase_outside":
        phases["intervals"] = [["editor_show", 1, 10_000_000, 101_000_000]]
    else:
        rows[2][4] = "99"
        report = aggregate(rows, phases)
        entries = report["phases"]
        assert isinstance(entries, list)
        assert entries[0]["unknown_ms"] == 40
        assert entries[0]["state_complete"] is False
        return
    with pytest.raises(ValueError, match=r"lifetime|inversion"):
        aggregate(rows, phases)


def test_more_than_old_thousand_event_cap_preserves_late_wait() -> None:
    """Aggregate all records, including a late wait excluded by the old preview cap."""
    rows, phases = evidence()
    prefix = [["S", str(t), "20", "20", "1"] for t in range(1, 1501)]
    rows = [
        rows[0],
        *prefix,
        ["S", "1501", "30", "20", "5"],
        ["R", "1601", "20"],
        ["S", "1701", "20", "30", "1"],
        ["L", "1800", "2", "10", "20"],
        rows[-1],
    ]
    phases = deepcopy(phases)
    phases["intervals"] = [["editor_show", 1, 1_500_000_000, 1_702_000_000]]
    entries = aggregate(rows, phases)["phases"]
    assert isinstance(entries, list)
    assert entries[0]["waiting_ms"] == 100
    assert entries[0]["ready_ms"] == 100
    assert entries[0]["scheduled_ms"] == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_timestamp_migration_never_depends_on_callback_order(
    *,
    reverse: bool,
) -> None:
    """Equal-QPC cross-CPU switches cannot manufacture complete scheduled time."""
    rows, phases = evidence()
    tied = [["S", "20", "30", "20", "1"], ["S", "20", "20", "30", "1"]]
    rows[2:3] = list(reversed(tied)) if reverse else tied
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is False
    entries = report["phases"]
    assert isinstance(entries, list)
    assert entries[0]["unknown_ms"] == 40


def test_native_decode_failure_exposes_only_fixed_numeric_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mocked reader failure remains failed without echoing paths or raw data."""
    phase = tmp_path / "phase.json"
    phase.write_text(json.dumps({"pid": 10, "tid": 20, "source_sha": "a" * 40}))
    raw = tmp_path / "private.csv"
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        "sys.argv",
        [
            "script",
            "owned-reader.exe",
            "private.etl",
            str(phase),
            str(raw),
            "private-xperf.csv",
        ],
    )

    def failed_reader(
        _args: list[str], *, stdout: TextIO, **_kwargs: object
    ) -> SimpleNamespace:
        stdout.write("D,36,4,1,1168,0,4\n")
        return SimpleNamespace(returncode=13)

    monkeypatch.setattr("scripts.scheduler_phase_summary.subprocess.run", failed_reader)
    with pytest.raises(ValueError, match="native reader failed"):
        main()
    output = capsys.readouterr().out
    assert "GUI_PHASE_DECODER_STATUS=[[36, 4, 1, 1168, 0, 4]]" in output
    assert "private" not in output


@pytest.mark.parametrize("corrupt", [False, True])
def test_native_fields_must_agree_with_independent_xperf(*, corrupt: bool) -> None:
    """A wrong binary prefix/state offset cannot pass through plausible numbers."""
    raw = [["S", "12345", "20", "30", "1" if corrupt else "5"], ["R", "12346", "20"]]
    exported = [
        "BeginHeader",
        "CSwitch, TimeStamp, New TID, Old TID, OldState",
        "ReadyThread, TimeStamp, ThreadID, Rdy TID",
        "EndHeader",
        "CSwitch, 1, 20, 30, Waiting",
        "ReadyThread, 2, 30, 20",
    ]
    if corrupt:
        with pytest.raises(ValueError, match="independent decoder mismatch"):
            verify_independent_decoder(raw, exported, 20)
    else:
        verify_independent_decoder(raw, exported, 20)


def test_post_exit_switch_is_excluded_from_proven_identity_interval() -> None:
    """An observed teardown transition after ThreadEnd cannot extend phase ownership."""
    rows, phases = evidence()
    rows.insert(-1, ["S", "101", "30", "20", "4"])
    report = aggregate(rows, phases)
    baseline = aggregate(*evidence())
    assert report["outside_lifetime_events_excluded"] == 1
    assert report["phases"] == baseline["phases"]
    assert report["thread_lifetime_checked"] is True


@pytest.mark.parametrize(
    ("cause", "duration"),
    [
        ("initial_lifetime", 20.0),
        ("same_timestamp_group", 40.0),
        ("ready_while_scheduled", 40.0),
        ("unsupported_old_state", 40.0),
    ],
)
def test_unknown_reason_counts_preserve_incomplete_phase_result(
    cause: str, duration: float
) -> None:
    """Each fixed reason retains unknown time and cannot qualify an incomplete phase."""
    rows, phases = evidence()
    if cause == "initial_lifetime":
        rows.pop(1)
    elif cause == "same_timestamp_group":
        rows.insert(3, ["S", "20", "20", "30", "1"])
    elif cause == "ready_while_scheduled":
        rows[2] = ["R", "20", "20"]
    else:
        rows[2][4] = "99"
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is False
    reasons = report["thread_lifetime_unknown_state_reasons"]
    assert isinstance(reasons, list)
    assert len(reasons) == 4
    reason = next(row for row in reasons if row["reason"] == cause)
    assert reason["entry_count"] == 1
    assert reason["unknown_ms"] == duration
    segments, _ = scheduler_segments(rows, 20, 0, 100)
    unknown_ticks = sum(
        right - left for left, right, state in segments if state == "unknown"
    )
    assert sum(row["unknown_ms"] for row in reasons) == unknown_ticks
    assert all(set(row) == {"reason", "entry_count", "unknown_ms"} for row in reasons)
    assert report["causal_claim"] is False


def test_unknown_reason_duration_is_lifetime_not_overlapping_phase_sum() -> None:
    """Repeated/overlapping phases never multiply unknown-duration diagnostics."""
    rows, phases = evidence()
    phases["intervals"] = [
        ["editor_show", 1, 10_000_000, 90_000_000],
        ["hotkey_dispatch", 1, 10_000_000, 90_000_000],
    ]
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is True
    assert report["thread_lifetime_unknown_state_reasons"] == [
        {"reason": "initial_lifetime", "entry_count": 1, "unknown_ms": 1.0},
        {"reason": "same_timestamp_group", "entry_count": 0, "unknown_ms": 0.0},
        {"reason": "ready_while_scheduled", "entry_count": 0, "unknown_ms": 0.0},
        {"reason": "unsupported_old_state", "entry_count": 0, "unknown_ms": 0.0},
    ]


def test_zero_duration_unknown_entry_does_not_fabricate_time() -> None:
    """An immediate birth-time switch resolves the initial unknown at zero cost."""
    rows, phases = evidence()
    rows[1][1] = "0"
    report = aggregate(rows, phases)
    assert report["analysis_complete"] is True
    reasons = report["thread_lifetime_unknown_state_reasons"]
    assert isinstance(reasons, list)
    assert reasons[0] == {
        "reason": "initial_lifetime",
        "entry_count": 1,
        "unknown_ms": 0.0,
    }
    assert sum(row["unknown_ms"] for row in reasons) == 0
    assert report["phases"] == aggregate(*evidence())["phases"]


def test_unknown_renewal_conserves_duration_and_excludes_outside_lifetime() -> None:
    """Repeated unsupported states count entries without overlapping their intervals."""
    rows, phases = evidence()
    rows[2][4] = "99"
    rows.insert(3, ["S", "30", "30", "20", "99"])
    rows.insert(-1, ["S", "101", "30", "20", "99"])
    report = aggregate(rows, phases)
    reasons = report["thread_lifetime_unknown_state_reasons"]
    assert isinstance(reasons, list)
    assert reasons[3] == {
        "reason": "unsupported_old_state",
        "entry_count": 2,
        "unknown_ms": 40.0,
    }
    assert sum(row["unknown_ms"] for row in reasons) == 41.0
    assert report["outside_lifetime_events_excluded"] == 1
    assert report["analysis_complete"] is False


def test_transition_detail_is_bounded_private_and_noninterpreting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown intervals and acceptance are identical with/without detail."""
    rows, phases = evidence()
    rows[2][4] = "99"
    rows.insert(3, ["R", "20", "20"])
    saved = deepcopy(rows)
    report = aggregate(rows, phases)
    detail = report.pop("transition_detail")
    assert isinstance(detail, dict)
    assert detail["outgoing_state_counts"] == [{"state_bucket": 99, "count": 1}]
    assert detail["timestamp_tie_counts"] == [
        {"switch_count_class": 1, "ready_count_class": 1, "group_count": 1}
    ]
    assert report["analysis_complete"] is False
    monkeypatch.setattr(module, "transition_detail", lambda *_: {})
    control = aggregate(rows, phases)
    control.pop("transition_detail")
    assert report == control
    assert rows == saved


def test_transition_detail_cardinality_clamping_and_saturation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arbitrary state values and long ties cannot expand the output schema."""
    monkeypatch.setattr(module, "MAX_DETAIL_COUNT", 2)
    rows = [["S", "1", "987654", "876543", str(i)] for i in range(-2, 300)]
    rows += [["R", "1", "876543"]] * 10
    rows += [["S", "2", "987654", "876543", "99"]] * 10
    rows += [["S", "3", "987654", "876543", "99"]] * 10
    rows += [["S", "4", "987654", "876543", "99"]] * 10
    rows += [["S", "6", "987654", "876543", "88"]]
    detail = module.transition_detail(rows, 876543, 0, 5)
    states = detail["outgoing_state_counts"]
    ties = detail["timestamp_tie_counts"]
    assert isinstance(states, list)
    assert isinstance(ties, list)
    assert len(states) == 257
    assert len(ties) <= 16
    assert detail["count_saturated"] is True
    assert max(entry["count"] for entry in states) == 2
    assert detail["timestamp_tie_counts"] == [
        {"switch_count_class": 3, "ready_count_class": 0, "group_count": 2},
        {"switch_count_class": 3, "ready_count_class": 3, "group_count": 1},
    ]
    serialized = json.dumps(detail)
    assert "876543" not in serialized
    assert "987654" not in serialized


@pytest.mark.parametrize("cause", ["ready", "tie", "outgoing"])
def test_transition_detail_never_resolves_unknown_states(cause: str) -> None:
    """Counts remain informational across all observed unknown transition causes."""
    rows, phases = evidence()
    if cause == "ready":
        rows.insert(2, ["R", "10", "20"])
    elif cause == "tie":
        rows.insert(3, ["R", "20", "20"])
    else:
        rows[2][4] = "2"
    before = scheduler_segments(rows, 20, 0, 100)
    detail = module.transition_detail(rows, 20, 0, 100)
    after = scheduler_segments(rows, 20, 0, 100)
    report = aggregate(rows, phases)
    assert before == after
    assert detail == report["transition_detail"]
    assert report["analysis_complete"] is False
    assert report["causal_claim"] is False


def test_tie_classes_do_not_depend_on_unproven_event_order() -> None:
    """A pair of ready records is a tie too; unrelated lifetime data stays absent."""
    rows = [["R", "10", "20"], ["S", "10", "30", "20", "6"]]
    forward = module.transition_detail(rows, 20, 0, 100)
    reverse = module.transition_detail(list(reversed(rows)), 20, 0, 100)
    assert forward == reverse
    ready = module.transition_detail([["R", "10", "20"]] * 2, 20, 0, 100)
    assert ready["timestamp_tie_counts"] == [
        {"switch_count_class": 0, "ready_count_class": 2, "group_count": 1}
    ]
    empty = module.transition_detail(rows, 20, 20, 100)
    assert empty["outgoing_state_counts"] == []
    assert empty["timestamp_tie_counts"] == []
    assert empty["count_saturated"] is False
