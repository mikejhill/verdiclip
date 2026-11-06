"""Pure adjacency diagnostics: no ETW, private traces or GUI required."""

import json
from copy import deepcopy

import pytest
from scripts import scheduler_adjacency as module
from scripts.scheduler_phase_summary import next_state, scheduler_segments


def report(rows: list[list[str]]) -> dict[str, object]:
    """Use distinct synthetic identifiers and microsecond QPC ticks."""
    return module.adjacency_detail(rows, 876543, (0, 10000), 1_000_000, next_state)


def events() -> list[list[str]]:
    """Incoming, running-out, incoming, ready-while-scheduled, self switch."""
    return [
        ["C", "1", "301"],
        ["S", "1", "876543", "987654", "5"],
        ["C", "1", "301"],
        ["S", "2", "987654", "876543", "2"],
        ["C", "1", "302"],
        ["S", "12", "876543", "987654", "1"],
        ["C", "1", "302"],
        ["R", "112", "876543"],
        ["C", "0", "3"],
        ["S", "1112", "876543", "876543", "2"],
    ]


def test_roles_cpu_gaps_and_privacy_leave_states_unchanged() -> None:
    """All anchor types are visible without IDs, CPUs, timestamps or inferred states."""
    rows = events()
    saved = deepcopy(rows)
    before = scheduler_segments(rows, 876543, 0, 10000)
    result = report(rows)
    after = scheduler_segments(rows, 876543, 0, 10000)
    assert before == after
    assert rows == saved
    counts = result["counts"]
    assert isinstance(counts, list)
    assert len(counts) == 6
    outgoing = [x for x in counts if x["anchor"] == "outgoing_running"]
    assert outgoing[0]["cpu_relation"] == "same"
    assert outgoing[0]["gap_class"] == "at_most_1us"
    assert outgoing[1]["cpu_relation"] == "different"
    assert outgoing[1]["gap_class"] == "at_most_10us"
    ready = [x for x in counts if x["anchor"] == "ready_while_scheduled"]
    assert ready[0]["gap_class"] == "at_most_100us"
    assert ready[1]["gap_class"] == "at_most_1ms"
    assert ready[1]["cpu_relation"] == "unknown"
    assert ready[1]["neighbor_role"] == "self_switch"
    text = json.dumps(result)
    for private in ("876543", "987654", "301", "302", "1112"):
        assert private not in text


def test_missing_cpu_and_ties_never_assume_order() -> None:
    """Same-time switch/ready records remain tied regardless of input order."""
    rows = [
        ["S", "1", "876543", "987654", "5"],
        ["R", "2", "876543"],
        ["S", "2", "987654", "876543", "2"],
    ]
    first = report(rows)
    second = report([rows[0], rows[2], rows[1]])
    assert first == second
    counts = first["counts"]
    assert isinstance(counts, list)
    assert all(x["neighbor_role"] == "tied" for x in counts)
    assert all(x["cpu_relation"] == "unknown" for x in counts)
    assert all(x["gap_class"] == "same_timestamp" for x in counts)


@pytest.mark.parametrize(
    "rows",
    [
        [["C", "1", "65536"]],
        [["C", "0", "256"]],
        [["C", "2", "1"]],
        [["C", "1", "-1"]],
        [["C", "1"]],
        [["C", "1", "1"]],
        [["C", "1", "1"], ["C", "1", "1"]],
        [["C", "1", "1"], ["H"]],
    ],
)
def test_malformed_private_cpu_fails_closed(rows: list[list[str]]) -> None:
    """Reject ambiguous CPU pairing rather than guessing a relation."""
    with pytest.raises(ValueError, match="private CPU"):
        report(rows)


def test_fixed_bounds_saturation_and_lifetime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repetition cannot expand schema or leak outside-lifetime evidence."""
    monkeypatch.setattr(module, "MAX_COUNT", 2)
    rows = [["S", str(t), "987654", "876543", "2"] for t in range(1, 100)]
    result = report(rows)
    counts = result["counts"]
    assert isinstance(counts, list)
    assert len(counts) <= module.MAX_BINS == 756
    assert result["count_saturated"] is True
    assert max(x["count"] for x in counts) == 2
    empty = module.adjacency_detail(rows, 876543, (200, 300), 1000, next_state)
    assert empty["counts"] == []


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        (0, 1),
        (1, 2),
        (2, 3),
        (10, 3),
        (11, 4),
        (100, 4),
        (101, 5),
        (1000, 5),
        (1001, 6),
    ],
)
def test_integer_gap_boundaries(gap: int, expected: int) -> None:
    """Bin rational clock deltas at exact documented boundaries."""
    actual = module.gap_class(gap, 1_000_000)
    assert actual == expected
