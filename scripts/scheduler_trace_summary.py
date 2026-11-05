"""Export only numeric scheduler events for the owned diagnostic GUI thread.

Raw ETL/CSV stays on the disposable VM. Unknown schemas fail closed; this first
receipt does not assert timestamp units, losslessness or causal attribution.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path

FIELDS = frozenset(
    {
        "timestamp",
        "newthreadid",
        "oldthreadid",
        "oldthreadstate",
        "oldthreadwaitreason",
        "tthreadid",
        "threadid",
        "adjustreason",
        "adjustincrement",
    }
)

MAX_EVENTS = 1000


def numeric_fields(values: dict[str, str]) -> dict[str, float]:
    """Reject unknown numeric formats without echoing raw field contents."""
    numeric: dict[str, float] = {}
    for name, value in values.items():
        if name in FIELDS:
            try:
                numeric[name] = float(value.strip())
            except ValueError:
                raise ValueError("Non-numeric scheduler field " + name) from None
    return numeric


def numeric_event(
    kind: str, row: list[str], names: list[str] | None, thread_id: int
) -> dict[str, float] | None:
    """Validate a scheduler row and retain only allowlisted numeric fields."""
    if names is None or len(row) != len(names):
        raise ValueError("Unrecognized scheduler event schema")
    values = dict(zip(names, row, strict=True))
    aliases = {
        "newtid": "newthreadid",
        "oldtid": "oldthreadid",
        "oldstate": "oldthreadstate",
        "rdytid": "tthreadid",
    }
    for source, target in aliases.items():
        if source in values:
            values[target] = values.pop(source)
    # Xperf's ThreadID is the readying thread; Rdy TID is the target.
    if kind == "ReadyThread" and "tthreadid" in values:
        values.pop("threadid", None)
    states = {
        "Initialized": 0,
        "Ready": 1,
        "Running": 2,
        "Standby": 3,
        "Terminated": 4,
        "Waiting": 5,
        "Wait": 5,
        "Transition": 6,
        "DeferredReady": 7,
        "GateWaitObsolete": 8,
    }
    state = values.get("oldthreadstate", "").strip()
    if state in states:
        values["oldthreadstate"] = str(states[state])
    identifiers = (
        ("newthreadid", "oldthreadid") if kind == "CSwitch" else ("tthreadid",)
    )
    ids = [int(values[name].strip()) for name in identifiers if name in values]
    if not ids:
        raise ValueError("Missing scheduler thread identity")
    if thread_id not in ids:
        return None
    numeric = numeric_fields(values)
    if not all(math.isfinite(value) for value in numeric.values()):
        raise ValueError("Invalid scheduler numeric field")
    if "timestamp" not in numeric:
        raise ValueError("Missing scheduler timestamp")
    numeric["is_switch"] = float(kind == "CSwitch")
    return numeric


def summarize(lines: list[str], thread_id: int) -> dict[str, object]:
    """Ignore all names, paths, command lines and events of unrelated threads."""
    headers: dict[str, list[str]] = {}
    events: list[dict[str, float]] = []
    count = 0
    in_header = False
    for row in csv.reader(lines):
        if not row:
            continue
        kind = row[0].strip()
        if kind == "BeginHeader":
            in_header = True
            continue
        if kind == "EndHeader":
            in_header = False
            continue
        if kind not in {"CSwitch", "ReadyThread"}:
            continue
        if in_header:
            headers[kind] = [re.sub(r"[^a-z0-9]", "", value.lower()) for value in row]
            continue
        numeric = numeric_event(kind, row, headers.get(kind), thread_id)
        if numeric is None:
            continue
        count += 1
        if len(events) < MAX_EVENTS:
            events.append(numeric)
    if not events:
        raise ValueError("No owned GUI-thread scheduler events")
    return {
        "candidate_gui_thread_id": thread_id,
        "thread_lifetime_checked": False,
        "matched_events": count,
        "truncated": count > len(events),
        "events": events,
        "timestamp_units_validated": False,
        "event_loss_checked": False,
        "causal_claim": False,
    }


def schema_summary(lines: list[str]) -> dict[str, object]:
    """Expose only bounded scheduler column names, never event payloads."""
    headers: dict[str, list[str]] = {}
    counts = {"CSwitch": 0, "ReadyThread": 0}
    in_header = False
    for row in csv.reader(lines):
        if not row:
            continue
        kind = row[0].strip()
        if kind in {"BeginHeader", "EndHeader"}:
            in_header = kind == "BeginHeader"
        elif kind in counts:
            if in_header:
                headers[kind] = [
                    value.strip()
                    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _()]{0,47}", value.strip())
                    else "REDACTED"
                    for value in row[:40]
                ]
            else:
                counts[kind] += 1
    return {"scheduler_headers": headers, "scheduler_row_counts": counts}


def main() -> None:
    """Read bounded private inputs and emit only the allowlisted numeric receipt."""
    csv_path, log_path = map(Path, sys.argv[1:])
    if (
        csv_path.stat().st_size > 256 * 1024 * 1024
        or log_path.stat().st_size > 8 * 1024 * 1024
    ):
        raise ValueError("Trace export exceeds diagnostic input budget")
    lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
    sys.stdout.write("GUI_SCHEDULER_SCHEMA=" + json.dumps(schema_summary(lines)) + "\n")
    packets = [
        json.JSONDecoder().raw_decode(line.split("GUI_READINESS_PROFILE=", 1)[1])[0]
        for line in log_path.read_text(encoding="utf-8-sig").splitlines()
        if "GUI_READINESS_PROFILE={" in line
    ]
    if len(packets) != 1 or packets[0].get("trial") != "1":
        raise ValueError("Missing exact first-trial identity")
    thread_id = packets[0].get("native_thread_id")
    if type(thread_id) is not int or thread_id <= 0:
        raise ValueError("Invalid owned GUI thread identity")
    result = summarize(lines, thread_id)
    sys.stdout.write("GUI_SCHEDULER_TRACE=" + json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, csv.Error) as error:
        # Do not echo raw exporter fields, paths or a traceback into public logs.
        known = {
            "Unrecognized scheduler event schema",
            "Missing scheduler thread identity",
            "Invalid scheduler numeric field",
            "Missing scheduler timestamp",
            "No owned GUI-thread scheduler events",
            "Trace export exceeds diagnostic input budget",
            "Missing exact first-trial identity",
            "Invalid owned GUI thread identity",
            *("Non-numeric scheduler field " + name for name in FIELDS),
        }
        reason = str(error) if str(error) in known else type(error).__name__
        sys.stdout.write("GUI_SCHEDULER_TRACE=unavailable; reason=" + reason + "\n")
        raise SystemExit(2) from None
