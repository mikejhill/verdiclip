"""One fixed table125 paint-control experiment, separate from matrix admission."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from scripts.panel_feasibility import ROOT, attest, events, snapshots, write
from scripts.panel_matrix_feasibility import launch as launch_fixture
from scripts.panel_owned_job import OwnedJob
from scripts.panel_paint_fixture import PaintServer, page, request_phase
from scripts.panel_paint_probe import phase_state, state_key
from scripts.panel_wheel_feasibility import validate_log

HELPER_SECONDS = 30
PERCENT = 100
FIXED_OFFSET = 119
Json = dict[str, Any]


def launch(job: OwnedJob, manifest: Path, output: Path) -> Json:
    """Same3s startup/native watchdog and30s owned-helper lifetime, no retries."""
    base = Path(str(getattr(sys, "_base_executable", ""))).resolve(strict=True)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT), str(Path(sys.prefix) / "Lib/site-packages"), str(ROOT / "src"))
    )
    started = time.monotonic()
    pid, handle = job.start(
        [
            str(base),
            "-m",
            "scripts.panel_paint_probe",
            "--fixture-manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        ROOT,
    )
    while time.monotonic() - started < HELPER_SECONDS:
        code = job.wait(handle, 0)
        records = events(output / "calls.jsonl")
        now = time.monotonic()
        if now - started >= HELPER_SECONDS:
            raise RuntimeError("Paint helper total deadline exceeded")
        validate_log(records, started, now, finished=code is not None)
        if code is not None:
            if time.monotonic() - started >= HELPER_SECONDS:
                raise RuntimeError("Paint helper late completion")
            return {"pid": pid, "exit_code": code}
        time.sleep(0.05)
    raise RuntimeError("Paint helper total deadline exceeded")


def admit(report: Json, child: Json, states: list[Json], records: list[Json]) -> None:
    """No stale success after failure/cleanup; audit all observed authored poses."""
    if (
        report["helper"]["exit_code"] != 0
        or child.get("first_failure") is not None
        or child.get("paint_control_completed") is not True
        or child.get("selected_before") != child.get("selected_after")
        or child.get("complete_capture_qualified") is not False
        or not states
    ):
        raise RuntimeError(f"Paint child failed: {child.get('first_failure')}")
    if any(
        r["event"] in ("first_failure", "call_error", "cleanup_error") for r in records
    ):
        raise RuntimeError("Paint native/cleanup failure recorded")
    if not any(
        r["event"] == "call_end" and r.get("name") == "paint.source.close"
        for r in records
    ):
        raise RuntimeError("Paint source cleanup evidence missing")
    checkpoints = [r for r in records if r["event"] == "identity_checkpoint"]
    if [r["checkpoint"] for r in checkpoints] != [0, 1]:
        raise RuntimeError("Paint identity checkpoints missing")
    expected = state_key(states[0])
    phases = []
    for state in states:
        phase = state["paint"]["phase"]
        if type(phase) is not int or phase not in (0, 1, 2):
            raise RuntimeError("Invalid authored phase")
        phase_state(state, phase, expected)
        if not phases or phases[-1] != phase:
            phases.append(phase)
    if phases != [0, 1, 2]:
        raise RuntimeError("Authored phases absent/reordered")
    # Independent intended-panel association: fixture's unique target scroll
    # fingerprint differs from outer/sibling. No CSS origin chooses pixel edges.
    target_association(states[0], child["native_scroll_state"])


def offset_interval(pane: Json, axis: str) -> tuple[float, float]:
    """The existing one-CSS-pixel identity tolerance, expressed in percent."""
    dimension = "height" if axis == "vertical" else "width"
    extent = pane[f"extent_{dimension}"] - pane[f"viewport_{dimension}"]
    offset = pane[axis]
    if (
        not all(math.isfinite(v) for v in (extent, offset))
        or extent <= 0
        or not 0 <= offset <= extent
    ):
        raise RuntimeError("Invalid authored scroll fingerprint")
    return float((offset - 1) * PERCENT / extent), float(
        (offset + 1) * PERCENT / extent
    )


def target_association(state: Json, native: Json) -> None:
    """Require the fixed nonzero pose and disjoint target/other-pane intervals."""
    if (
        type(state["config"].get("reference_offset")) not in (int, float)
        or state["config"]["reference_offset"] != FIXED_OFFSET
    ):
        raise RuntimeError("Paint fixture is not the fixed119 pose")
    target = state["state"]["target"]
    intervals = {}
    for axis in ("vertical", "horizontal"):
        lower, upper = offset_interval(target, axis)
        if (
            target[axis] <= 0
            or not math.isfinite(native[axis])
            or not 0 <= native[axis] <= PERCENT
            or not lower <= native[axis] <= upper
        ):
            raise RuntimeError("Native selection differs from nonzero target offset")
        intervals[axis] = (lower, upper)
    for name in ("outer", "sibling"):
        competitor = state["state"][name]
        disjoint = False
        for axis in ("vertical", "horizontal"):
            low, high = offset_interval(competitor, axis)
            target_low, target_high = intervals[axis]
            disjoint |= high < target_low or target_high < low
        if not disjoint:
            raise RuntimeError("Authored target fingerprint overlaps another pane")


def main() -> int:
    """Retain original first failure and close every owned fixture/helper handle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report: Json = {
        "passed": False,
        "first_failure": None,
        "cleanup": {},
        "single_pose_only": True,
        "replaces_matrix_oracle": False,
    }
    jobs: dict[str, OwnedJob] = {}
    server = None
    try:
        report["attestation"] = attest()
        for name in ("fixture", "helper"):
            jobs[name] = OwnedJob()
        source = output / "authored.html"
        source.write_text(page(), encoding="utf-8")
        command = output / "paint-phase.json"
        request_phase(command, 0)
        server = PaintServer(source, output / "states", command)
        fixture, launch_receipt = launch_fixture(
            jobs["fixture"], server, output, "1.25"
        )
        fixture |= {
            "phase_command": str(command),
            "state_directory": str(server.output),
        }
        report["fixture_launch"] = launch_receipt
        report["fixture"] = fixture
        manifest = output / "fixture.json"
        write(manifest, fixture)
        report["helper"] = launch(jobs["helper"], manifest, output / "probe")
        child = json.loads((output / "probe/manifest.json").read_text(encoding="utf-8"))
        report["probe"] = child
        admit(
            report,
            child,
            snapshots(server.output),
            events(output / "probe/calls.jsonl"),
        )
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - retain first failure
        report["first_failure"] = repr(error)
    finally:
        for name, job in reversed(tuple(jobs.items())):
            try:
                report["cleanup"][name] = job.close()
            except Exception as error:  # noqa: BLE001 - continue all cleanup
                report["cleanup"][name] = {"confirmed": False, "error": repr(error)}
        if server is not None:
            try:
                server.close()
                report["cleanup"]["server"] = {"confirmed": True}
            except Exception as error:  # noqa: BLE001 - retain cleanup uncertainty
                report["cleanup"]["server"] = {"confirmed": False, "error": repr(error)}
        report["passed"] = report["passed"] and all(
            x["confirmed"] for x in report["cleanup"].values()
        )
        write(output / "receipt.json", report)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
