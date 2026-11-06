"""Bounded private ETL aggregation; no raw events or absolute clocks are public."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from itertools import groupby
from pathlib import Path

from scripts.scheduler_adjacency import adjacency_detail
from scripts.scheduler_occupancy import occupancy_detail
from scripts.scheduler_trace_summary import numeric_event

MAX_DETAIL_COUNT = 2**31 - 1
OUT_OF_RANGE_STATE = 256
STATE_BUCKETS = 257  # uint8 plus one out-of-range bucket; never echo raw values.
TIE_CLASS_LIMIT = 3  # 3 means three or more events of that kind.

MAX_ROWS = 2_000_000
HEADER_FIELDS = 9
THREAD_END = 2
LIFETIME_EVENTS = 2
MAX_PHASES = 128
READY_FIELDS = 3
SWITCH_FIELDS = 5
PHASE_FIELDS = 4
MAX_DECODER_DIAGNOSTICS = 2
DECODER_DIAGNOSTIC_FIELDS = 6
UNKNOWN_REASONS = (
    "initial_lifetime",
    "same_timestamp_group",
    "ready_while_scheduled",
    "unsupported_old_state",
)


def aggregate(rows: list[list[str]], phases: dict[str, object]) -> dict[str, object]:
    """Require finalized zero-loss QPC evidence and one exact thread lifetime."""
    pid, tid = phases["pid"], phases["tid"]
    if type(pid) is not int or type(tid) is not int or pid <= 0 or tid <= 0:
        raise ValueError("identity")
    if phases.get("errors") or phases.get("truncated"):
        raise ValueError("phase completeness")
    headers = [row for row in rows if row and row[0] == "H"]
    if len(headers) != 1 or len(headers[0]) != HEADER_FIELDS or rows[-1] != headers[0]:
        raise ValueError("trace finalization")
    clock, frequency, lost, buffers, callback_lost, result, closed, count = map(
        int, headers[0][1:]
    )
    if (
        clock != 1
        or frequency <= 0
        or any((lost, buffers, callback_lost, result, closed))
        or count > MAX_ROWS
    ):
        raise ValueError("trace loss or clock")
    life = [tuple(map(int, row[1:])) for row in rows if row and row[0] == "L"]
    starts = [
        stamp
        for stamp, opcode, owner, thread in life
        if opcode == 1 and owner == pid and thread == tid
    ]
    ends = [
        stamp
        for stamp, opcode, owner, thread in life
        if opcode == THREAD_END and owner == pid and thread == tid
    ]
    if (
        len(life) != LIFETIME_EVENTS
        or len(starts) != 1
        or len(ends) != 1
        or starts[0] >= ends[0]
    ):
        raise ValueError("thread lifetime or reuse")
    segments, unknown_reasons = scheduler_segments(rows, tid, starts[0], ends[0])
    intervals = phases["intervals"]
    if not isinstance(intervals, list) or not 1 <= len(intervals) <= MAX_PHASES:
        raise ValueError("phase budget")
    output = [
        phase_totals(item, frequency, segments, starts[0], ends[0])
        for item in intervals
    ]
    return {
        "event_loss_checked": True,
        "events_lost": 0,
        "buffers_lost": 0,
        "thread_lifetime_checked": True,
        "thread_reuse_rejected": True,
        "outside_lifetime_events_excluded": sum(
            1
            for row in rows
            if row and row[0] in {"S", "R"} and not starts[0] <= int(row[1]) <= ends[0]
        ),
        "clock": "raw QPC aligned to Python perf_counter_ns",
        "alignment_endpoint_rounding_bound_ns": (1_000_000_000 + frequency - 1)
        // frequency
        + 1,
        "thread_lifetime_unknown_state_reasons": [
            {
                "reason": reason,
                "entry_count": counts[0],
                "unknown_ms": counts[1] * 1000 / frequency,
            }
            for reason, counts in unknown_reasons.items()
        ],
        "transition_detail": transition_detail(rows, tid, starts[0], ends[0]),
        "adjacency_detail": adjacency_detail(
            rows, tid, (starts[0], ends[0]), frequency, next_state
        ),
        "occupancy_detail": occupancy_detail(
            rows, tid, (starts[0], ends[0]), frequency, intervals
        ),
        "analysis_complete": all(entry["state_complete"] for entry in output),
        "phase_count": len(output),
        "phases": output,
        "causal_claim": False,
        "scheduled_time_is_not_exclusive_application_cpu": True,
    }


def transition_detail(
    rows: list[list[str]], tid: int, birth: int, death: int
) -> dict[str, object]:
    """Count validated lifetime transitions without interpreting scheduler states.

    Fixed cardinality: 257 outgoing-state bins and 16 tie-composition bins.
    No timestamps, identifiers, event ordering or provider strings are retained.
    State bucket 256 is out-of-range; tie counts of three mean three-or-more.
    """
    states = [0] * STATE_BUCKETS
    ties = [0] * 16
    saturated = False

    def increment(bins: list[int], index: int) -> None:
        nonlocal saturated
        if bins[index] < MAX_DETAIL_COUNT:
            bins[index] += 1
        else:
            saturated = True

    candidate = (row for row in rows if row and row[0] in {"S", "R"})
    for stamp, group in groupby(candidate, key=lambda row: int(row[1])):
        if not birth <= stamp <= death:
            continue
        switches = ready = 0
        for row in group:
            if row[0] == "S":
                switches = min(TIE_CLASS_LIMIT, switches + 1)
                if int(row[3]) == tid:
                    value = int(row[4])
                    increment(
                        states,
                        value
                        if 0 <= value < OUT_OF_RANGE_STATE
                        else OUT_OF_RANGE_STATE,
                    )
            else:
                ready = min(TIE_CLASS_LIMIT, ready + 1)
        if switches + ready > 1:
            increment(ties, switches * 4 + ready)
    return {
        "outgoing_state_counts": [
            {"state_bucket": index, "count": count}
            for index, count in enumerate(states)
            if count
        ],
        "timestamp_tie_counts": [
            {
                "switch_count_class": index // 4,
                "ready_count_class": index % 4,
                "group_count": count,
            }
            for index, count in enumerate(ties)
            if count
        ],
        "count_saturated": saturated,
        "tie_class_limit": TIE_CLASS_LIMIT,
        "out_of_range_state_bucket": 256,
    }


def next_state(row: list[str], tid: int, state: str) -> str:
    """Decode only owned switch/ready state transitions."""
    if row[0] == "R":
        if len(row) != READY_FIELDS or int(row[2]) != tid:
            raise ValueError("ready identity")
        state = "ready" if state != "scheduled" else "unknown"
    else:
        if len(row) != SWITCH_FIELDS:
            raise ValueError("switch schema")
        new, old, old_state = map(int, row[2:])
        if tid not in (new, old):
            raise ValueError("switch identity")
        if old == tid:
            state = {1: "ready", 3: "ready", 7: "ready", 5: "waiting"}.get(
                old_state, "unknown"
            )
        if new == tid:
            state = "scheduled"
    return state


def unknown_state_reason(transitions: list[list[str]]) -> str:
    """Label an already-unknown result without interpreting or changing its state."""
    if len(transitions) != 1:
        return "same_timestamp_group"
    if transitions[0][0] == "R":
        return "ready_while_scheduled"
    return "unsupported_old_state"


def scheduler_segments(
    rows: list[list[str]], tid: int, start: int, end: int
) -> tuple[list[tuple[int, int, str]], dict[str, list[int]]]:
    """Keep state intervals unchanged; count fixed causes across the owned lifetime.

    Counts include each entry/renewal, even one lasting zero ticks. Durations
    cover disjoint lifetime segments, never the overlapping measured phases.
    A tied group has precedence over its individual rows' possible reasons.
    """
    reasons = {reason: [0, 0] for reason in UNKNOWN_REASONS}
    reason = "initial_lifetime"
    reasons[reason][0] = 1
    last, state = start, "unknown"
    previous_stamp: int | None = None
    segments: list[tuple[int, int, str]] = []
    candidate = (row for row in rows if row and row[0] in {"S", "R"})
    for stamp, group in groupby(candidate, key=lambda row: int(row[1])):
        transitions = list(group)
        if previous_stamp is not None and stamp < previous_stamp:
            raise ValueError("event time inversion")
        previous_stamp = stamp
        if not start <= stamp <= end:
            continue  # Outside the proven identity interval: never attribute it.
        if stamp < last:
            raise ValueError("event time inversion")
        if stamp > last:
            segments.append((last, stamp, state))
            if state == "unknown":
                reasons[reason][1] += stamp - last
        # ETW does not establish cross-CPU ordering for tied timestamps.
        # Validate every row, but do not guess the resulting state for a tie.
        decoded = [next_state(row, tid, state) for row in transitions]
        state = decoded[0] if len(decoded) == 1 else "unknown"
        if state == "unknown":
            reason = unknown_state_reason(transitions)
            reasons[reason][0] += 1
        last = stamp
    if last < end:
        segments.append((last, end, state))
        if state == "unknown":
            reasons[reason][1] += end - last
    return segments, reasons


def phase_totals(
    item: object,
    frequency: int,
    segments: list[tuple[int, int, str]],
    birth: int,
    death: int,
) -> dict[str, object]:
    """Clip all scheduler intervals to one exact measured phase without truncation."""
    if not isinstance(item, (list, tuple)) or len(item) != PHASE_FIELDS:
        raise ValueError("phase schema")
    name, ordinal, begin_ns, end_ns = item
    if not isinstance(name, str) or re.fullmatch(r"[a-z_]{1,64}", name) is None:
        raise ValueError("phase name")
    if any(type(value) is not int for value in (ordinal, begin_ns, end_ns)):
        raise ValueError("phase clock")
    begin, end = (
        begin_ns * frequency // 1_000_000_000,
        end_ns * frequency // 1_000_000_000,
    )
    if not birth <= begin <= end <= death:
        raise ValueError("phase outside lifetime")
    totals = dict.fromkeys(("scheduled", "waiting", "ready", "unknown"), 0)
    for left, right, state in segments:
        overlap = max(0, min(end, right) - max(begin, left))
        totals[state] += overlap
    if sum(totals.values()) != end - begin:
        raise ValueError("incomplete phase coverage")
    return {
        "phase": name,
        "capture_ordinal": ordinal,
        "wall_ms": (end_ns - begin_ns) / 1_000_000,
        **{key + "_ms": value * 1000 / frequency for key, value in totals.items()},
        "state_complete": totals["unknown"] == 0,
    }


def verify_independent_decoder(
    rows: list[list[str]], lines: list[str], tid: int
) -> None:
    """Cross-check kind/ID/state multisets, NOT timestamps, ordering or CPU."""
    native = Counter(
        (row[0], *map(int, row[2:])) for row in rows if row and row[0] in {"S", "R"}
    )
    decoded: Counter[tuple[object, ...]] = Counter()
    headers: dict[str, list[str]] = {}
    in_header = False
    for index, row in enumerate(csv.reader(lines)):
        if index > MAX_ROWS:
            raise ValueError("event budget")
        if not row:
            continue
        kind = row[0].strip()
        if kind in {"BeginHeader", "EndHeader"}:
            in_header = kind == "BeginHeader"
            continue
        if kind not in {"CSwitch", "ReadyThread"}:
            continue
        if in_header:
            headers[kind] = [re.sub(r"[^a-z0-9]", "", name.lower()) for name in row]
            continue
        fields = numeric_event(kind, row, headers.get(kind), tid)
        if fields is None:
            continue
        if kind == "CSwitch":
            decoded[
                (
                    "S",
                    int(fields["newthreadid"]),
                    int(fields["oldthreadid"]),
                    int(fields["oldthreadstate"]),
                )
            ] += 1
        else:
            decoded[("R", int(fields["tthreadid"]))] += 1
    if not native or native != decoded:
        raise ValueError("independent decoder mismatch")


def main() -> None:
    """Run only the repository-built file reader on a private finalized ETL."""
    reader, etl, phase_path, raw, exported = map(Path, sys.argv[1:])
    if phase_path.stat().st_size > 1024 * 1024:
        raise ValueError("phase file budget")
    phases = json.loads(phase_path.read_text(encoding="utf-8"))
    if phases.get("source_sha") != os.environ["VERDICLIP_BENCHMARK_SOURCE_SHA"]:
        raise ValueError("source identity")
    with raw.open("x", encoding="utf-8") as stream:
        result = subprocess.run(  # noqa: S603 - explicit reviewed reader path, numeric identity, no shell
            [str(reader), str(etl), str(phases["pid"]), str(phases["tid"])],
            stdout=stream,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    sys.stdout.write("GUI_PHASE_READER_EXIT=" + str(result.returncode) + "\n")
    if raw.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("native reader failed or budget exceeded")
    rows = list(csv.reader(raw.read_text(encoding="utf-8").splitlines()))
    if result.returncode != 0:
        diagnostics = [list(map(int, row[1:])) for row in rows if row and row[0] == "D"]
        if len(diagnostics) <= MAX_DECODER_DIAGNOSTICS and all(
            len(row) == DECODER_DIAGNOSTIC_FIELDS for row in diagnostics
        ):
            sys.stdout.write(
                "GUI_PHASE_DECODER_STATUS=" + json.dumps(diagnostics) + "\n"
            )
        raise ValueError("native reader failed or budget exceeded")
    if len(rows) > MAX_ROWS:
        raise ValueError("event budget")
    if exported.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("event budget")
    verify_independent_decoder(
        rows, exported.read_text(encoding="utf-8-sig").splitlines(), phases["tid"]
    )
    report = aggregate(rows, phases)
    report["independent_xperf_field_crosscheck"] = True
    report["independent_crosscheck_excludes_timestamp_order_cpu"] = True
    sys.stdout.write("GUI_PHASE_SCHEDULER=" + json.dumps(report, sort_keys=True) + "\n")
    if not report["analysis_complete"]:
        raise ValueError("unknown phase state")


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        known = {
            "identity",
            "phase completeness",
            "trace finalization",
            "trace loss or clock",
            "thread lifetime or reuse",
            "phase budget",
            "event time inversion",
            "ready identity",
            "switch schema",
            "switch identity",
            "phase schema",
            "phase name",
            "phase clock",
            "phase outside lifetime",
            "incomplete phase coverage",
            "phase file budget",
            "source identity",
            "native reader failed or budget exceeded",
            "event budget",
            "unknown phase state",
            "independent decoder mismatch",
        }
        reason = str(error) if str(error) in known else type(error).__name__
        sys.stdout.write(
            "GUI_PHASE_SCHEDULER=unavailable; reason=" + reason + "; no causal claim\n"
        )
        raise SystemExit(2) from None
