"""Private QPC sidecar adapter for the unchanged finalized scheduler reader."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from scripts.scheduler_phase_summary import (
    HEADER_FIELDS,
    LIFETIME_EVENTS,
    MAX_ROWS,
    aggregate,
    verify_independent_decoder,
)

NANOSECONDS = 1_000_000_000
MAX_PRIVATE_BYTES = 256 * 1024 * 1024
MAX_SIDECAR_BYTES = 64 * 1024
MAX_MARKERS = 26
MARKER_FIELDS = 7
MAX_INTEGER = (1 << 63) - 1
LIFETIME_FIELDS = 4
REPAIR_BASE = "5a5983275dfbd7e7243bd329ca3d859faca024c2"
PHASES = (
    "assemblies",
    "identity",
    "window",
    "controls",
    "pixels",
    "bitmap",
    "freeze",
    "content",
    "activation",
    "snapshot",
)
MARKERS = (
    "entry",
    *(name + suffix for name in PHASES[:8] for suffix in ("_begin", "_end")),
    "show_begin",
    "render",
    *(name + suffix for name in PHASES[8:] for suffix in ("_begin", "_end")),
    "stop_observed",
    "retirement_begin",
    "fixture_retired",
)


@dataclass(frozen=True)
class Marker:
    """One fixed private record; none of its identifiers are public output."""

    name: str
    pid: int
    tid: int
    tick: int
    frequency: int


def integer(value: object) -> int:
    """Refuse bools, coercions, negatives and unbounded clocks or identifiers."""
    if type(value) is not int or not 0 < value <= MAX_INTEGER:
        raise ValueError("integer")
    return value


def private_directory(path: Path) -> Path:
    """Admit only the runner's direct, non-linked, exclusive GUID directory."""
    root = Path(os.environ["RUNNER_TEMP"])
    if (
        not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
        or path.is_junction()
        or root.is_symlink()
        or root.is_junction()
        or path.parent.resolve() != root.resolve()
        or re.fullmatch(r"VerdiClip-[a-f0-9]{32}", path.name) is None
    ):
        raise ValueError("private directory")
    return path


def read_private(path: Path, limit: int = MAX_SIDECAR_BYTES) -> str:
    """Bound both stat and read, and refuse linked or non-file inputs."""
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("private file")
    if path.stat().st_size > limit:
        raise ValueError("file budget")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file budget")
    return data.decode("utf-8-sig")


def read_receipt(path: Path) -> dict[str, object]:
    """Require a bounded JSON object, with later exact field validation."""
    value = json.loads(read_private(path))
    if not isinstance(value, dict):
        raise TypeError("receipt")
    return cast(dict[str, object], value)


def validate_owner(
    owner: dict[str, object], exited: dict[str, object], source: str
) -> tuple[int, str, int, int, int]:
    """Join the actual Popen PID/nonce to the exact child's observed lifetime."""
    if (
        set(owner)
        != {
            "pid",
            "nonce",
            "source_sha",
            "fixture_directory",
            "launch_begin_ns",
            "launch_end_ns",
            "clock",
            "repair_base_sha",
        }
        or set(exited) != {"pid", "nonce", "exit_ns", "returncode", "forced"}
        or re.fullmatch(r"[a-f0-9]{40}", source) is None
        or owner["source_sha"] != source
        or owner["repair_base_sha"] != REPAIR_BASE
        or owner["clock"] != "QueryPerformanceCounter()"
        or type(exited["forced"]) is not bool
        or type(exited["returncode"]) is not int
        or not isinstance(owner["fixture_directory"], str)
    ):
        raise ValueError("owner schema")
    pid = integer(owner["pid"])
    nonce = owner["nonce"]
    if (
        not isinstance(nonce, str)
        or re.fullmatch(r"[a-f0-9]{32}", nonce) is None
        or integer(exited["pid"]) != pid
        or exited["nonce"] != nonce
    ):
        raise ValueError("owner identity")
    begin = integer(owner["launch_begin_ns"])
    end = integer(owner["launch_end_ns"])
    exit_ns = integer(exited["exit_ns"])
    if not begin <= end < exit_ns:
        raise ValueError("parent clock")
    return pid, nonce, begin, end, exit_ns


def parse_markers(text: str, pid: int, nonce: str) -> list[Marker]:
    """Refuse partial rows, duplicate/foreign names and per-marker identity drift."""
    if not text.endswith("\n") or len(text) > MAX_SIDECAR_BYTES:
        raise ValueError("partial markers")
    rows = list(csv.reader(text.splitlines()))
    if not 1 <= len(rows) <= MAX_MARKERS:
        raise ValueError("marker budget")
    markers: list[Marker] = []
    for index, row in enumerate(rows, 1):
        if (
            len(row) != MARKER_FIELDS
            or row[1] not in MARKERS
            or row[6] != nonce
            or any(
                re.fullmatch(r"[0-9]{1,19}", row[i]) is None for i in (0, 2, 3, 4, 5)
            )
        ):
            raise ValueError("marker schema")
        sequence, actual_pid, tid, tick, frequency = (
            integer(int(row[i])) for i in (0, 2, 3, 4, 5)
        )
        if sequence != index or actual_pid != pid or frequency > NANOSECONDS:
            raise ValueError("marker identity")
        markers.append(Marker(row[1], actual_pid, tid, tick, frequency))
    first = markers[0]
    if (
        first.name != "entry"
        or len({marker.name for marker in markers}) != len(markers)
        or any(
            (marker.tid, marker.frequency) != (first.tid, first.frequency)
            for marker in markers
        )
        or any(left.tick > right.tick for left, right in pairwise(markers))
    ):
        raise ValueError("marker sequence")
    # Cancellation may interrupt any phase, but may not be followed by more work.
    ordered = [
        MARKERS.index(marker.name)
        for marker in markers
        if marker.name != "stop_observed"
    ]
    if ordered != sorted(ordered):
        raise ValueError("marker ordering")
    stop = next(
        (i for i, item in enumerate(markers) if item.name == "stop_observed"), None
    )
    if stop is not None and any(
        item.name not in {"retirement_begin", "fixture_retired"}
        for item in markers[stop + 1 :]
    ):
        raise ValueError("work after cancellation")
    return markers


def qpc_nanoseconds(tick: int, frequency: int) -> int:
    """Use ceil, then prove the unchanged reducer's floor recovers the same tick."""
    integer(tick)
    integer(frequency)
    value = (tick * NANOSECONDS + frequency - 1) // frequency
    if value * frequency // NANOSECONDS != tick:
        raise ValueError("unrepresentable clock bridge")
    return value


def phase_input(
    markers: list[Marker], source: str
) -> tuple[dict[str, object], list[str]]:
    """Supply only complete pairs; never fabricate a missing phase endpoint."""
    named = {marker.name: marker for marker in markers}
    intervals: list[list[object]] = []
    incomplete: list[str] = []
    for name in PHASES:
        begin, end = named.get(name + "_begin"), named.get(name + "_end")
        if end is not None and begin is None:
            raise ValueError("missing phase begin")
        if begin is None:
            incomplete.append(name)
            continue
        if end is None:
            incomplete.append(name)
            continue
        intervals.append(
            [
                name,
                1,
                qpc_nanoseconds(begin.tick, begin.frequency),
                qpc_nanoseconds(end.tick, end.frequency),
            ]
        )
    return {
        "pid": markers[0].pid,
        "tid": markers[0].tid,
        "source_sha": source,
        "intervals": intervals,
        "errors": [],
        "truncated": False,
    }, incomplete


def validate_clock_lifetime(
    rows: list[list[str]], markers: list[Marker], bounds: tuple[int, int, int]
) -> None:
    """Check every marker and parent launch/exit against exact finalized ETW time."""
    first = markers[0]
    header = rows[-1]
    if (
        len(header) != HEADER_FIELDS
        or header[0] != "H"
        or int(header[2]) != first.frequency
    ):
        raise ValueError("frequency mismatch")
    # aggregate() separately requires exactly this lifetime, zero loss and no reuse.
    life = [list(map(int, row[1:])) for row in rows if row and row[0] == "L"]
    if len(life) != LIFETIME_EVENTS or any(
        len(item) != LIFETIME_FIELDS for item in life
    ):
        raise ValueError("thread lifetime")
    birth, death = life
    if birth[1:] != [1, first.pid, first.tid] or death[1:] != [2, first.pid, first.tid]:
        raise ValueError("thread identity")
    begin_ns, _end_ns, exit_ns = bounds
    low = begin_ns * first.frequency // NANOSECONDS
    final = (exit_ns * first.frequency + NANOSECONDS - 1) // NANOSECONDS
    # PowerShell's STA runspace thread may be born after Popen returns. The
    # first marker proves it exists; do not pretend it is the initial OS thread.
    if not low <= birth[0] <= first.tick or not birth[0] < death[0] <= final:
        raise ValueError("parent lifetime")
    if any(not birth[0] <= marker.tick <= death[0] for marker in markers):
        raise ValueError("marker outside lifetime")


def report_from_evidence(
    rows: list[list[str]],
    markers: list[Marker],
    owner: dict[str, object],
    exited: dict[str, object],
    source: str,
) -> dict[str, object]:
    """Keep existing scheduler unknown/loss rules and honest incompleteness."""
    _, _, begin, end, exit_ns = validate_owner(owner, exited, source)
    validate_clock_lifetime(rows, markers, (begin, end, exit_ns))
    phases, incomplete = phase_input(markers, source)
    scheduler = aggregate(rows, phases)
    # The reducer is unchanged; describe this adapter's verified clock bridge.
    scheduler["clock"] = "raw QPC via Stopwatch with exact integer tick bridge"
    scheduler["independent_xperf_field_crosscheck"] = True
    complete = (
        markers[-1].name == "fixture_retired"
        and not incomplete
        and {"show_begin", "render"} <= {marker.name for marker in markers}
    )
    return {
        "schema": 1,
        "status": "complete"
        if scheduler["analysis_complete"] and complete
        else "incomplete",
        "source_sha": source,
        "repair_base_sha": REPAIR_BASE,
        "marker_count": len(markers),
        "marker_capture_complete": complete,
        "incomplete_phases": incomplete,
        "clock_bridge_exact": True,
        "scheduler": scheduler,
        "reason": "none",
    }


def unavailable(source: str) -> dict[str, object]:
    """Never disclose raw exception content or identifiers to public output."""
    return {
        "schema": 1,
        "status": "unavailable",
        "source_sha": source,
        "repair_base_sha": REPAIR_BASE,
        "marker_count": 0,
        "marker_capture_complete": False,
        "incomplete_phases": [],
        "clock_bridge_exact": False,
        "scheduler": None,
        "reason": "evidence_unavailable",
    }


def collect(reader: Path, directory: Path, source: str) -> dict[str, object]:
    """Decode once, privately, using the unchanged repository reader and Xperf check."""
    owner = read_receipt(directory / "owner.json")
    exited = read_receipt(directory / "exit.json")
    pid, nonce, *_ = validate_owner(owner, exited, source)
    markers = parse_markers(read_private(directory / "markers.csv"), pid, nonce)
    raw = directory / "scheduler.csv"
    etl = directory / "trace.etl"
    if etl.is_symlink() or not etl.is_file() or etl.stat().st_size > MAX_PRIVATE_BYTES:
        raise ValueError("ETL file")
    with raw.open("x", encoding="ascii") as stream:
        result = subprocess.run(  # noqa: S603 - repository-built reader, owned file, numeric identity
            [str(reader), str(etl), str(pid), str(markers[0].tid)],
            stdout=stream,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise ValueError("reader failure")
    rows = list(csv.reader(read_private(raw, MAX_PRIVATE_BYTES).splitlines()))
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("row budget")
    verify_independent_decoder(
        rows,
        read_private(directory / "exported.csv", MAX_PRIVATE_BYTES).splitlines(),
        markers[0].tid,
    )
    return report_from_evidence(rows, markers, owner, exited, source)


def main() -> int:
    """Stage only bounded JSON; the owner may publish it only AFTER verified cleanup."""
    reader, directory = map(Path, sys.argv[1:])
    private_directory(directory)
    source = os.environ["VERDICLIP_BENCHMARK_SOURCE_SHA"]
    if re.fullmatch(r"[a-f0-9]{40}", source) is None:
        return 2
    try:
        report = collect(reader, directory, source)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.TimeoutExpired,
    ):
        report = unavailable(source)
    with (directory / "analysis.json").open("x", encoding="ascii") as stream:
        json.dump(report, stream, sort_keys=True, allow_nan=False)
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
