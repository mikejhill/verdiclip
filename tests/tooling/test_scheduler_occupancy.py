"""Synthetic switch-pair occupancy without any platform or tracing APIs."""

import json
from typing import cast

import pytest
from scripts import scheduler_occupancy as module

TID = 876543
OTHER = 987654


def switch(
    stamp: int, cpu: int | None, kind: str, state: int = 5, mode: int = 1
) -> list[list[str]]:
    """Build private events; target roles are explicit and payloads synthetic."""
    prefix = [] if cpu is None else [["C", str(mode), str(cpu)]]
    new = TID if kind in {"in", "self"} else OTHER
    old = TID if kind in {"out", "self"} else OTHER
    return [*prefix, ["S", str(stamp), str(new), str(old), str(state)]]


def detail(rows: list[list[str]], begin: int = 0, end: int = 100) -> dict[str, object]:
    """One validated phase with one millisecond per QPC tick."""
    return module.occupancy_detail(
        rows,
        TID,
        (0, 100),
        1000,
        [["editor_show", 1, begin * 1_000_000, end * 1_000_000]],
    )


def phase(result: dict[str, object]) -> dict[str, object]:
    """Require an actual single phase aggregate."""
    phases = result["phases"]
    assert isinstance(phases, list)
    assert len(phases) == 1
    item = phases[0]
    assert isinstance(item, dict)
    return cast("dict[str, object]", item)


def test_migration_running_out_closes_without_filling_gap() -> None:
    """Outgoing Running ends occupancy; the cross-CPU gap stays unverified."""
    rows = switch(10, 300, "in") + switch(20, 300, "out", 2)
    rows += switch(21, 301, "in") + switch(40, 301, "out")
    result = detail(rows)
    assert phase(result)["verified_switch_pair_ms"] == 29
    assert phase(result)["unverified_occupancy_ms"] == 71
    assert phase(result)["coverage_complete"] is False
    assert result["independently_validated"] is False
    assert result["ready_wait_classification"] is False
    text = json.dumps(result)
    for private in ("876543", "987654", "300", "301"):
        assert private not in text


def test_self_switch_closes_and_starts_distinct_pairs() -> None:
    """A complete self-switch preserves the boundary without extending either pair."""
    rows = switch(10, 300, "in") + switch(20, 300, "self", 2) + switch(40, 300, "out")
    result = phase(detail(rows, 15, 30))
    assert result["verified_switch_pair_ms"] == 15
    assert result["unverified_occupancy_ms"] == 0
    assert result["coverage_complete"] is True
    assert result["verified_segment_count"] == 2


def test_overlapping_cpu_pairs_are_not_double_counted() -> None:
    """Only the overlapping interval is excluded, even when both pairs complete."""
    rows = switch(10, 300, "in") + switch(20, 301, "in")
    rows += switch(30, 300, "out") + switch(40, 301, "out")
    result = phase(detail(rows))
    assert result["verified_switch_pair_ms"] == 20
    assert result["unverified_occupancy_ms"] == 80


@pytest.mark.parametrize(
    "mode",
    ["missing_out", "missing_in", "repeated_in", "unknown_cpu", "mixed_encoding"],
)
def test_incomplete_evidence_excludes_overlapping_candidates(mode: str) -> None:
    """Missing or ambiguous pairs cannot make another overlapping pair verified."""
    rows = switch(20, 301, "in") + switch(30, 301, "out")
    if mode == "missing_out":
        rows = switch(10, 300, "in") + rows
    elif mode == "missing_in":
        rows += switch(40, 300, "out")
    elif mode == "repeated_in":
        rows = (
            switch(10, 300, "in")
            + switch(11, 300, "in")
            + rows
            + switch(40, 300, "out")
        )
    elif mode == "unknown_cpu":
        rows += switch(40, None, "out")
    else:
        rows += switch(40, 1, "in", mode=0) + switch(50, 1, "out", mode=0)
    result = phase(detail(rows))
    assert result["verified_switch_pair_ms"] == 0
    assert result["unverified_occupancy_ms"] == 100


def test_ties_do_not_depend_on_event_order() -> None:
    """Same-time outgoing/incoming switches cannot become a guessed self-switch."""
    before, after = switch(10, 300, "in"), switch(40, 300, "out")
    left, right = switch(20, 300, "out"), switch(20, 300, "in")
    first = detail(before + left + right + after)
    second = detail(before + right + left + after)
    assert first == second
    assert phase(first)["verified_switch_pair_ms"] == 0


def test_ready_event_alone_does_not_change_cpu_pair() -> None:
    """Ready classification is independent of matched processor occupancy."""
    rows = [*switch(10, 300, "in"), ["R", "20", str(TID)], *switch(30, 300, "out")]
    assert phase(detail(rows))["verified_switch_pair_ms"] == 20


def test_count_saturation_and_phase_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts saturate explicitly while exact integer coverage remains unchanged."""
    monkeypatch.setattr(module, "MAX_INTERVAL_COUNT", 1)
    rows = switch(10, 300, "in") + switch(20, 300, "out")
    rows += switch(30, 300, "in") + switch(40, 300, "out")
    result = phase(detail(rows, 10, 40))
    assert result["verified_switch_pair_ms"] == 20
    assert result["verified_segment_count"] == 1
    assert result["count_saturated"] is True
    assert phase(detail(rows, 20, 30))["verified_switch_pair_ms"] == 0
