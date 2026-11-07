"""Owned WPF/Edge stable-identity diagnostic; not replacement qualification."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from scripts.panel_feasibility import (
    ROOT,
    NativeWindow,
    _wpf_executable,
    attest,
    edge_fixture,
    events,
    snapshots,
    wait_snapshot,
    write,
)
from scripts.panel_fixture_server import FixtureServer
from scripts.panel_owned_job import OwnedJob
from scripts.panel_wheel_feasibility import validate_log

HELPER_SECONDS = 30
CHECKPOINTS = 3
Json = dict[str, Any]


def launch(job: OwnedJob, manifest: Path, output: Path) -> Json:
    """Keep original UIA total30s and native/startup3s external watchdogs."""
    base = Path(str(getattr(sys, "_base_executable", ""))).resolve(strict=True)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT), str(Path(sys.prefix) / "Lib/site-packages"), str(ROOT / "src"))
    )
    start = time.monotonic()
    pid, handle = job.start(
        [
            str(base),
            "-m",
            "scripts.panel_identity_probe",
            "--fixture-manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        ROOT,
    )
    while time.monotonic() - start < HELPER_SECONDS:
        code = job.wait(handle, 0)
        records = events(output / "calls.jsonl")
        now = time.monotonic()
        if now - start >= HELPER_SECONDS:
            raise RuntimeError("Identity helper exceeded total deadline")
        validate_log(records, start, now, finished=code is not None)
        if code is not None:
            if time.monotonic() - start >= HELPER_SECONDS:
                raise RuntimeError("Identity completion exceeded deadline")
            return {"pid": pid, "exit_code": code}
        time.sleep(0.05)
    raise RuntimeError("Identity helper exceeded total deadline")


def admit(records: list[Json], report: Json, code: int) -> None:
    """Require all closed evidence, no native/write/failure events or partial result."""
    checkpoints = [x for x in records if x["event"] == "identity_checkpoint"]
    if (
        code != 0
        or report.get("first_failure") is not None
        or report.get("identity_checkpoints_completed") != CHECKPOINTS
        or report.get("read_only") is not True
        or len(checkpoints) != CHECKPOINTS
        or [x["checkpoint"] for x in checkpoints] != [0, 1, 2]
        or any(
            x["event"] in ("first_failure", "cleanup_error", "call_error")
            for x in records
        )
    ):
        raise RuntimeError("Identity evidence incomplete or failed")
    expected = checkpoints[0]
    for item in checkpoints:
        if any(
            item[key] != expected[key]
            for key in ("target_id", "root_id", "structure", "state")
        ):
            raise RuntimeError("Identity checkpoints disagree")
        if not item["ancestry"] or item["ancestry"][-1] != item["root_id"]:
            raise RuntimeError("Identity root association missing")


def main() -> int:
    """One immutable case and cleanup receipt; no retries or fallback actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("wpf-borderless", "edge-100"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wpf-executable", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report: Json = {"passed": False, "first_failure": None, "cleanup": {}}
    jobs: dict[str, OwnedJob] = {}
    server = None
    try:
        report["source"] = attest()
        NativeWindow()
        jobs = {"fixture": OwnedJob(), "probe": OwnedJob()}
        states = output / "states"
        if args.case == "wpf-borderless":
            _, handle = jobs["fixture"].start(
                [str(_wpf_executable(args.wpf_executable)), str(states), "borderless"],
                ROOT,
            )
            fixture = wait_snapshot(states, jobs["fixture"], handle)
        else:
            server = FixtureServer(ROOT / "scripts/panel_fixture/edge.html", states)
            fixture = edge_fixture(jobs["fixture"], server, output, "1")
        report["fixture"] = fixture
        manifest = output / "fixture.json"
        write(manifest, fixture)
        before = snapshots(states)[-1]["state"]
        helper = launch(jobs["probe"], manifest, output / "probe")
        report["helper"] = helper
        child = json.loads((output / "probe/manifest.json").read_text(encoding="utf-8"))
        report["probe"] = child
        admit(events(output / "probe/calls.jsonl"), child, helper["exit_code"])
        if any(item["state"] != before for item in snapshots(states)):
            raise RuntimeError("Read-only fixture state changed")  # noqa: TRY301
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - retain first evidence
        report["first_failure"] = repr(error)
    finally:
        for name, job in reversed(tuple(jobs.items())):
            try:
                report["cleanup"][name] = job.close()
            except Exception as error:  # noqa: BLE001 - continue cleanup
                report["cleanup"][name] = {"confirmed": False, "error": repr(error)}
        if server is not None:
            try:
                server.close()
                report["cleanup"]["server"] = {"confirmed": True}
            except Exception as error:  # noqa: BLE001 - preserve original error
                report["cleanup"]["server"] = {"confirmed": False, "error": repr(error)}
        report["passed"] = report["passed"] and all(
            item["confirmed"] for item in report["cleanup"].values()
        )
        write(output / "receipt.json", report)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
