"""Print only fixed child timing fields from an owned diagnostic receipt."""

from __future__ import annotations

import json
from itertools import islice
from pathlib import Path
from typing import cast

from verdiclip.capture.scroll.uia_inspect_clock import LABELS

MAX_RECEIPT_BYTES = 2 * 1024 * 1024


def select_first_inspect(workers: object) -> tuple[dict[str, object] | None, str]:
    """Select by immutable first action, never worker name or successful outcome."""
    if not isinstance(workers, dict) or len(workers) > 2:
        return None, "inspect-worker-shape"
    candidates = []
    for diagnostic in workers.values():
        if not isinstance(diagnostic, dict):
            return None, "inspect-worker-shape"
        first = diagnostic.get("first_request")
        if isinstance(first, dict) and first.get("action") == "inspect":
            candidates.append(diagnostic)
    if len(candidates) != 1:
        return None, "inspect-worker-missing-or-ambiguous"
    return cast(dict[str, object], candidates[0]), ""


def summarize(value: dict[str, object]) -> dict[str, object]:
    """Never copy exception, provider, request, window or process data to output."""
    result: dict[str, object] = {
        "schema": 1,
        "receipt_present": True,
        "case_passed": value.get("passed") is True,
        "parent_retired": value.get("retired") is True,
        "cleanup_confirmed": value.get("cleanup_errors") == [],
        "child_cleanup_proven": False,
        "events": [],
        "clock_unavailable": True,
    }
    diagnostics, gap = select_first_inspect(value.get("provider_worker_diagnostics"))
    if diagnostics is None:
        return {**result, "gap": gap}
    result["first_action_inspect"] = True
    events = diagnostics.get("inspect_clock")
    if not isinstance(events, list) or len(events) > len(LABELS):
        return result
    cleaned = []
    for event in events:
        if not isinstance(event, dict) or set(event) != {"stage", "microseconds"}:
            return result
        label, elapsed = event["stage"], event["microseconds"]
        if label not in LABELS:
            return result
        if elapsed is not None and (
            type(elapsed) is not int or not 0 <= elapsed <= 2**31 - 1
        ):
            return result
        cleaned.append({"stage": label, "microseconds": elapsed})
    result["events"] = cleaned
    result["clock_unavailable"] = (
        diagnostics.get("inspect_clock_unavailable") is not False
        or not cleaned
        or cleaned[0]["stage"] != "armed"
    )
    if not cleaned or cleaned[0]["stage"] != "armed":
        result["gap"] = "inspect-clock-missing-origin"
    timing = diagnostics.get("first_request")
    if isinstance(timing, dict):
        result["first_action_inspect"] = timing.get("action") == "inspect"
        outcome = timing.get("outcome")
        result["first_outcome"] = (
            outcome
            if outcome
            in {
                "success",
                "failure",
                "startup_timeout",
                "operation_timeout",
                "cancelled",
            }
            else "unavailable"
        )
    return result


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Duplicate keys cannot replace earlier failure evidence."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt key")
        result[key] = value
    return result


def read_summary() -> dict[str, object]:
    """Return a fixed gap on ordinary filesystem/receipt failure, never raw text."""
    result: dict[str, object] = {"schema": 1, "receipt_present": False}
    category = "read"
    try:
        paths = list(
            islice(
                Path(".test-tmp").glob("native-broker-*/**/scrollbar-native.json"), 2
            )
        )
        category = "missing-or-multiple"
        if len(paths) != 1:
            return {**result, "gap": category}
        category = "read"
        with paths[0].open("rb") as stream:
            data = stream.read(MAX_RECEIPT_BYTES + 1)
        category = "oversize"
        if len(data) > MAX_RECEIPT_BYTES:
            return {**result, "gap": category}
        category = "malformed"
        value = json.loads(data, object_pairs_hook=unique_object)
        category = "shape"
        if not isinstance(value, dict):
            return {**result, "gap": category}
        return summarize(value)
    except Exception:  # noqa: BLE001 - content-free diagnostic gap; controls propagate
        result["gap"] = category
        return result


def main() -> None:
    """Always emit fixed evidence even when the first case left a partial receipt."""
    print("FIRST_INSPECT_CLOCK=" + json.dumps(read_summary(), sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
