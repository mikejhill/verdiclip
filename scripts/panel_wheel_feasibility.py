"""Owned Edge wheel experiment with independent supervisor-only fixture oracle."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from scripts.panel_feasibility import (
    ROOT,
    NativeWindow,
    attest,
    edge_fixture,
    events,
    snapshots,
    write,
)
from scripts.panel_fixture_server import FixtureServer
from scripts.panel_owned_job import OwnedJob

CALL_SECONDS = 3
HELPER_SECONDS = 90
PERCENT = 100
OFFSET_TOLERANCE = 0.5
Json = dict[str, Any]


def fixture_page(initial: str) -> str:
    """Configure only the authored fixture source, never a foreign browser page."""
    source = (ROOT / "scripts/panel_fixture/edge.html").read_text(encoding="utf-8")
    marker = "target.scrollTop = 119;"
    if source.count(marker) != 1 or initial not in ("top", "middle"):
        raise ValueError("Unexpected owned fixture source or initial state")
    return source.replace(
        marker, f"target.scrollTop = {0 if initial == 'top' else 119};"
    )


def launch_probe(job: OwnedJob, manifest: Path, output: Path, mode: str) -> Json:
    """Externally bound every native call, including calls that never return."""
    base = Path(str(getattr(sys, "_base_executable", ""))).resolve(strict=True)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT), str(Path(sys.prefix) / "Lib/site-packages"), str(ROOT / "src"))
    )
    start = time.monotonic()
    pid, handle = job.start(
        [
            str(base),
            "-m",
            "scripts.panel_wheel_probe",
            "--fixture-manifest",
            str(manifest),
            "--output",
            str(output),
            "--mode",
            mode,
        ],
        ROOT,
    )
    while time.monotonic() - start < HELPER_SECONDS:
        code = job.wait(handle, 0)
        now = time.monotonic()
        if now - start >= HELPER_SECONDS:
            raise RuntimeError("Wheel helper exceeded total deadline")
        records = events(output / "calls.jsonl")
        now = time.monotonic()
        validate_log(records, start, now, finished=code is not None)
        if code is not None:
            now = time.monotonic()
            if now - start >= HELPER_SECONDS:
                raise RuntimeError("Wheel helper exceeded total deadline")
            return {"pid": pid, "exit_code": code, "seconds": now - start}
        time.sleep(0.05)
    raise RuntimeError("Wheel helper exceeded total deadline")


def validate_log(
    records: list[Json], start: float, now: float, *, finished: bool
) -> None:
    """Reject late completed calls as well as currently hung nested operations."""
    if now - start >= HELPER_SECONDS:
        raise RuntimeError("Wheel helper exceeded total deadline")
    if not records:
        if finished or now - start >= CALL_SECONDS:
            raise RuntimeError("Wheel helper startup missing or timed out")
        return
    if (
        records[0]["event"] != "process"
        or records[0]["monotonic"] - start >= CALL_SECONDS
    ):
        raise RuntimeError("Wheel startup evidence invalid")
    pending = paired_calls(records, start, now)
    if pending and (finished or now - pending[0]["monotonic"] >= CALL_SECONDS):
        raise RuntimeError("Wheel call incomplete or timed out")
    if len(records) == 1 and now - records[0]["monotonic"] >= CALL_SECONDS:
        raise RuntimeError("Wheel initial setup timed out")


def paired_calls(records: list[Json], start: float, now: float) -> list[Json]:
    """Validate bounded nested call evidence, including completed native latency."""
    pending: list[Json] = []
    previous = start
    for record in records:
        timestamp = record["monotonic"]
        if not math.isfinite(timestamp) or not previous <= timestamp <= now:
            raise RuntimeError("Wheel event time invalid")
        previous = timestamp
        if record["event"] in ("first_failure", "cleanup_error", "call_error"):
            raise RuntimeError(f"Wheel child failure event: {record}")
        if record["event"] == "call_start":
            pending.append(record)
        elif record["event"] == "call_end":
            if not pending:
                raise RuntimeError("Wheel call has no start")
            call = pending.pop()
            if (record.get("name"), record.get("slot")) != (
                call.get("name"),
                call.get("slot"),
            ):
                raise RuntimeError("Wheel call pairing mismatch")
            if timestamp - call["monotonic"] >= CALL_SECONDS:
                raise RuntimeError("Wheel completed call exceeded deadline")
    return pending


def admit(report: Json, probe: Json) -> None:
    """Keep child failure ahead of secondary independent-oracle failures."""
    if (
        probe.get("first_failure") is not None
        or probe.get("cleanup_error") is not None
        or report["helper"]["exit_code"] != 0
        or not probe.get("wheel_sequence_completed")
        or not probe.get("restored")
    ):
        raise RuntimeError(f"Wheel probe failed: {probe.get('first_failure')}")
    if "oracle_failure" in report:
        raise RuntimeError(report["oracle_failure"])


def audit(states: list[Json], observations: list[Json]) -> Json:
    """Independent DOM truth rejects propagated/horizontal scroll and false readback."""
    if not states or not observations:
        raise ValueError("Missing independent wheel evidence")
    ordered = sorted(states, key=lambda item: item["sequence"])
    baseline = ordered[0]["state"]
    target = baseline["target"]
    for sample in ordered:
        state = sample["state"]
        if (
            state["outer"] != baseline["outer"]
            or state["sibling"] != baseline["sibling"]
        ):
            raise ValueError("Wheel propagated to an ancestor or sibling")
        if {k: v for k, v in state["target"].items() if k != "vertical"} != {
            k: v for k, v in target.items() if k != "vertical"
        }:
            raise ValueError("Wheel changed horizontal position or geometry")
    extent = target["extent_height"] - target["viewport_height"]
    if extent <= 0:
        raise ValueError("Invalid independent fixture extent")
    offsets = [sample["state"]["target"]["vertical"] for sample in ordered]
    cursor = 0
    for observation in observations:
        offset = observation["state"]["vertical"] * extent / PERCENT
        match = next(
            (
                index
                for index in range(cursor, len(offsets))
                if abs(offset - offsets[index]) <= OFFSET_TOLERANCE
            ),
            None,
        )
        if match is None:
            raise ValueError("UIA wheel observation disagrees with independent oracle")
        cursor = match
    if ordered[-1]["state"] != baseline:
        raise ValueError("Independent oracle restoration was not exact")
    return {
        "unchanged_ancestors_siblings": True,
        "exact_restoration": True,
        "observations": len(observations),
        "snapshots": len(states),
    }


def main() -> int:
    """Run one explicit case once; preserve first failure and all cleanup evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--initial", choices=("top", "middle"), required=True)
    parser.add_argument("--mode", choices=("interior", "endpoint"), required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report: Json = {
        "passed": False,
        "first_failure": None,
        "cleanup": {},
        "initial": args.initial,
        "mode": args.mode,
    }
    jobs: dict[str, OwnedJob] = {}
    server = None
    try:
        report["source"] = attest()
        NativeWindow()
        jobs = {"fixture": OwnedJob(), "probe": OwnedJob()}
        page = output / "owned-wheel.html"
        page.write_text(fixture_page(args.initial), encoding="utf-8")
        server = FixtureServer(page, output / "states")
        fixture = edge_fixture(jobs["fixture"], server, output, "1")
        report["fixture"] = fixture
        # Do not leak DOM dimensions/offsets into acquisition or wheel decisions.
        selected = {
            key: fixture[key]
            for key in (
                "kind",
                "root_hwnd",
                "pid",
                "allowed_rect",
                "owned_process_ids",
                "click",
            )
        }
        manifest = output / "fixture.json"
        write(manifest, selected)
        report["helper"] = launch_probe(
            jobs["probe"], manifest, output / "probe", args.mode
        )
        probe = json.loads((output / "probe/manifest.json").read_text(encoding="utf-8"))
        report["probe"] = probe
        records = events(output / "probe/calls.jsonl")
        try:
            report["oracle"] = audit(
                snapshots(output / "states"),
                [
                    record
                    for record in records
                    if record["event"] == "wheel_observation"
                ],
            )
        except ValueError as error:
            report["oracle_failure"] = str(error)
        admit(report, probe)
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - preserve first diagnostic failure
        report["first_failure"] = repr(error)
    finally:
        for name, job in reversed(tuple(jobs.items())):
            try:
                report["cleanup"][name] = job.close()
            except Exception as error:  # noqa: BLE001 - keep original failure
                report["cleanup"][name] = {"confirmed": False, "error": repr(error)}
        if server is not None:
            try:
                server.close()
                report["cleanup"]["server"] = {"confirmed": True}
            except Exception as error:  # noqa: BLE001 - keep original failure
                report["cleanup"]["server"] = {"confirmed": False, "error": repr(error)}
        report["passed"] = (
            report["passed"]
            and report["first_failure"] is None
            and all(item["confirmed"] for item in report["cleanup"].values())
        )
        write(output / "receipt.json", report)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
