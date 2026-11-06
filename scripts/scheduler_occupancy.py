"""Conservative switch-pair occupancy evidence, separate from scheduler states."""

from collections import defaultdict
from itertools import groupby

from scripts.scheduler_adjacency import private_events

MAX_INTERVAL_COUNT = 2**31 - 1
PHASE_FIELDS = 4


def paired_spans(
    rows: list[list[str]], tid: int, lifetime: tuple[int, int]
) -> list[tuple[int, int, bool]]:
    """Match same-encoding/same-CPU switches; ambiguity creates exclusion spans.

    False spans are not waiting time. They prevent attribution where missing or
    conflicting pairs could overlap an otherwise plausible pair. Raw identities
    and pair endpoints are temporary private data and never emitted.
    """
    birth, death = lifetime
    pending: dict[tuple[int, int], tuple[int, bool]] = {}
    spans = []
    modes = set()
    unknown_cpu = False
    for stamp, group in groupby(private_events(rows), key=lambda e: int(e[0][1])):
        if not birth <= stamp <= death:
            continue
        events = list(group)
        tied = len(events) > 1
        switches: dict[tuple[int, int], list[list[str]]] = defaultdict(list)
        for row, cpu in events:
            if row[0] != "S":
                continue  # Ready events do not delimit processor occupancy.
            if cpu is None:
                unknown_cpu = True
            else:
                modes.add(cpu[0])
                switches[cpu].append(row)
        for cpu, records in switches.items():
            incoming = any(int(row[2]) == tid for row in records)
            outgoing = any(int(row[3]) == tid for row in records)
            # A tied group is processed together, never in arbitrary row order.
            if outgoing:
                start, valid = pending.pop(cpu, (birth, False))
                spans.append((start, stamp, valid and not tied))
            if incoming:
                previous = pending.get(cpu)
                pending[cpu] = (
                    (previous[0], False) if previous is not None else (stamp, not tied)
                )
    spans.extend((start, death, False) for start, _ in pending.values())
    if unknown_cpu or len(modes) > 1:
        spans.append((birth, death, False))
    return spans


def verified_segments(spans: list[tuple[int, int, bool]]) -> list[tuple[int, int]]:
    """Accept only exactly one matched pair and no ambiguity at each instant."""
    endpoints: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for start, end, valid in spans:
        if end < start:
            raise ValueError("occupancy time inversion")
        index = 0 if valid else 1
        endpoints[start][index] += 1
        endpoints[end][index] -= 1
    active = [0, 0]
    previous = None
    verified = []
    for stamp, change in sorted(endpoints.items()):
        if previous is not None and previous < stamp and active == [1, 0]:
            verified.append((previous, stamp))
        active = [active[i] + change[i] for i in range(2)]
        previous = stamp
    if active != [0, 0]:
        raise ValueError("occupancy pairing balance")
    return verified


def occupancy_detail(
    rows: list[list[str]],
    tid: int,
    lifetime: tuple[int, int],
    frequency: int,
    phases: list[object],
) -> dict[str, object]:
    """Emit bounded per-phase aggregates only, without changing any verdict."""
    verified = verified_segments(paired_spans(rows, tid, lifetime))
    output = []
    for item in phases:
        if not isinstance(item, (list, tuple)) or len(item) != PHASE_FIELDS:
            raise ValueError("occupancy phase schema")
        name, ordinal, begin_ns, end_ns = item
        if type(begin_ns) is not int or type(end_ns) is not int:
            raise ValueError("occupancy phase clock")
        begin = begin_ns * frequency // 1_000_000_000
        end = end_ns * frequency // 1_000_000_000
        ticks = count = 0
        for left, right in verified:
            overlap = max(0, min(end, right) - max(begin, left))
            ticks += overlap
            count += overlap > 0
        output.append(
            {
                "phase": name,
                "capture_ordinal": ordinal,
                "phase_duration_ms": (end - begin) * 1000 / frequency,
                "verified_switch_pair_ms": ticks * 1000 / frequency,
                "unverified_occupancy_ms": (end - begin - ticks) * 1000 / frequency,
                "coverage_complete": ticks == end - begin,
                "verified_segment_count": min(count, MAX_INTERVAL_COUNT),
                "count_saturated": count > MAX_INTERVAL_COUNT,
            }
        )
    return {
        "phases": output,
        "independently_validated": False,
        "exclusive_application_cpu": False,
        "ready_wait_classification": False,
        "changes_scheduler_completeness": False,
        "causal_claim": False,
    }
