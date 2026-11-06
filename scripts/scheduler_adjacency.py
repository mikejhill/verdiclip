"""Bounded anonymous adjacency evidence; never resolve scheduler uncertainty."""

from collections.abc import Callable
from itertools import groupby

MAX_COUNT = 2**31 - 1
ANCHORS = ("outgoing_running", "self_switch_running", "ready_while_scheduled")
ROLES = ("absent", "switch_in", "switch_out", "self_switch", "ready", "tied")
CPUS = ("unknown", "same", "different")
GAPS = (
    "absent",
    "same_timestamp",
    "at_most_1us",
    "at_most_10us",
    "at_most_100us",
    "at_most_1ms",
    "over_1ms",
)
MAX_BINS = len(ANCHORS) * 2 * len(ROLES) * len(CPUS) * len(GAPS)
CPU_FIELDS = 3
INDEX_MAX = 65535
NUMBER_MAX = 255
RUNNING_STATE = 2
type Event = tuple[list[str], tuple[int, int] | None]
type Group = tuple[int, list[Event]]


def private_events(
    rows: list[list[str]],
) -> list[tuple[list[str], tuple[int, int] | None]]:
    """Pair optional private CPU records; old receipts retain unknown CPU."""
    events = []
    pending = None
    for row in rows:
        if row and row[0] == "C":
            if pending is not None or len(row) != CPU_FIELDS:
                raise ValueError("private CPU schema")
            mode, cpu = map(int, row[1:])
            if mode not in (0, 1) or not 0 <= cpu <= (
                INDEX_MAX if mode else NUMBER_MAX
            ):
                raise ValueError("private CPU schema")
            pending = (mode, cpu)
        elif row and row[0] in {"S", "R"}:
            events.append((row, pending))
            pending = None
        elif pending is not None:
            raise ValueError("private CPU pairing")
    if pending is not None:
        raise ValueError("private CPU pairing")
    return events


def role(row: list[str], tid: int) -> int:
    """Fixed role relative to the owned thread, not a state inference."""
    if row[0] == "R":
        return 4
    incoming, outgoing = int(row[2]) == tid, int(row[3]) == tid
    return 3 if incoming and outgoing else 1 if incoming else 2


def gap_class(delta: int, frequency: int) -> int:
    """Compare integer QPC ratios without changing or retaining timestamps."""
    if delta == 0:
        return 1
    for index, microseconds in enumerate((1, 10, 100, 1000), start=2):
        if delta * 1_000_000 <= microseconds * frequency:
            return index
    return 6


def anchor_kind(row: list[str], tid: int, state: str) -> int | None:
    """Select observed roles without replacing the existing decoder's state."""
    if row[0] == "S" and int(row[3]) == tid and int(row[4]) == RUNNING_STATE:
        return 1 if int(row[2]) == tid else 0
    if row[0] == "R" and state == "scheduled":
        return 2
    return None


def neighbor_detail(
    groups: list[Group],
    position: tuple[int, int],
    cpu: tuple[int, int] | None,
    tid: int,
    frequency: int,
) -> tuple[int, int, int]:
    """Classify only provable adjacency; tied or mixed-encoding CPUs stay unknown."""
    index, offset = position
    stamp, events = groups[index]
    if len(events) > 1:
        return 5, 0, 1
    if not 0 <= index + offset < len(groups):
        return 0, 0, 0
    other_stamp, neighbors = groups[index + offset]
    gap = gap_class(abs(other_stamp - stamp), frequency)
    if len(neighbors) != 1:
        return 5, 0, gap
    other, other_cpu = neighbors[0]
    relation = 0
    if cpu is not None and other_cpu is not None and cpu[0] == other_cpu[0]:
        relation = 1 if cpu[1] == other_cpu[1] else 2
    return role(other, tid), relation, gap


def adjacency_detail(
    rows: list[list[str]],
    tid: int,
    lifetime: tuple[int, int],
    frequency: int,
    transition: Callable[[list[str], int, str], str],
) -> dict[str, object]:
    """Report nearest timestamp groups; tied groups have no invented event order.

    For a tied anchor, both neighbors describe its same-time group as tied with
    unknown CPU. No arbitrary choice from that group is exposed. Otherwise the
    preceding/following distinct group is used; a tied neighbor is also unknown.
    CPU encodings must match before comparison; mixed encodings stay unknown.
    """
    birth, death = lifetime
    groups = [
        (stamp, list(group))
        for stamp, group in groupby(
            private_events(rows), key=lambda event: int(event[0][1])
        )
        if birth <= stamp <= death
    ]
    bins = [0] * MAX_BINS
    saturated = False
    state = "unknown"
    for index, (_, events) in enumerate(groups):
        for row, cpu in events:
            anchor = anchor_kind(row, tid, state)
            if anchor is None:
                continue
            for side, offset in enumerate((-1, 1)):
                neighbor_role, relation, gap = neighbor_detail(
                    groups, (index, offset), cpu, tid, frequency
                )
                slot = (
                    ((anchor * 2 + side) * len(ROLES) + neighbor_role) * len(CPUS)
                    + relation
                ) * len(GAPS) + gap
                if bins[slot] < MAX_COUNT:
                    bins[slot] += 1
                else:
                    saturated = True
        # Exactly the existing state-machine rule, used only to select anchors.
        decoded = [transition(row, tid, state) for row, _ in events]
        state = decoded[0] if len(decoded) == 1 else "unknown"
    counts = []
    for slot, count in enumerate(bins):
        if not count:
            continue
        quotient, gap = divmod(slot, len(GAPS))
        quotient, cpu = divmod(quotient, len(CPUS))
        quotient, neighbor = divmod(quotient, len(ROLES))
        anchor, side = divmod(quotient, 2)
        counts.append(
            {
                "anchor": ANCHORS[anchor],
                "side": "previous" if side == 0 else "next",
                "neighbor_role": ROLES[neighbor],
                "cpu_relation": CPUS[cpu],
                "gap_class": GAPS[gap],
                "count": count,
            }
        )
    return {
        "counts": counts,
        "count_saturated": saturated,
        "max_bins": MAX_BINS,
        "cpu_is_logging_processor_not_proof_of_execution": True,
        "timestamp_order_is_not_causal_order": True,
    }
