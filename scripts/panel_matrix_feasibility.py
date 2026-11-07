"""Two isolated owned matrix cases and independent authored reference tiles.

No product integration, capture admission or legacy workflow changes. All native
work requires exact-head disposable CI attestation; every process is job-owned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import mss
from scripts.panel_feasibility import (
    ROOT,
    NativeWindow,
    attest,
    events,
    load_pixels,
    run_probe,
    snapshots,
    wait_snapshot,
    write,
)
from scripts.panel_fixture_matrix import MatrixCase, render
from scripts.panel_fixture_server import FixtureServer
from scripts.panel_matrix_reference import reference_offsets
from scripts.panel_owned_job import OwnedJob
from scripts.panel_probe_oracle import Pixels, color_bounds
from scripts.panel_uia_probe import NativeGuard

CASES = {
    "conversation-light100": (MatrixCase("conversation", width=320), "1"),
    "table-dark125": (MatrixCase("table", "dark", width=520), "1.25"),
}
BATCH_SECONDS = 300
OFFSET_TOLERANCE = 0.01
CALL_SECONDS = 3
HELPER_SECONDS = 30
BORDER = bytes((22, 155, 180))
Json = dict[str, Any]


def acquisition_manifest(owner: Json, border: list[int], pids: list[int]) -> Json:
    """Only ownership and authored entry point enter native raw acquisition."""
    a, b, c, d = owner["allowed_rect"]
    left, top, right, bottom = border
    if not a <= left < right <= c or not b <= top < bottom <= d:
        raise RuntimeError("Owned matrix target is not wholly in its window")
    return {
        **owner,
        "kind": "owned-edge",
        "owned_process_ids": pids,
        "click": [(left + right) / 2, (top + bottom) / 2],
    }


def launch(
    job: OwnedJob, server: FixtureServer, output: Path, scale: str
) -> tuple[Json, Json]:
    """Locate only the authored entry border; no DOM geometry selects capture."""
    candidates = [
        Path(os.environ.get(v, "C:/missing")) / "Microsoft/Edge/Application/msedge.exe"
        for v in ("PROGRAMFILES(X86)", "PROGRAMFILES")
    ]
    edge = next((p for p in candidates if p.is_file()), None)
    if edge is None:
        raise RuntimeError("Installed Edge unavailable")
    _, handle = job.start(
        [
            str(edge),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            f"--user-data-dir={output / 'edge-profile'}",
            "--window-size=760,520",
            "--window-position=40,40",
            f"--force-device-scale-factor={scale}",
            "--app=" + server.url,
        ],
        ROOT,
    )
    initial = wait_snapshot(server.output, job, handle)
    native = NativeWindow()
    owner = native.foreground(job.pids())
    left, top, right, bottom = owner["allowed_rect"]
    guard = NativeGuard(
        owner["root_hwnd"], round((left + right) / 2), round((top + bottom) / 2)
    )
    with mss.MSS() as source:
        guard.check()
        if native.foreground(job.pids()) != owner:
            raise RuntimeError("Owned window changed before border observation")
        shot = source.grab(
            {"left": left, "top": top, "width": right - left, "height": bottom - top}
        )
        guard.check()
        if native.foreground(job.pids()) != owner:
            raise RuntimeError("Owned window changed after border observation")
        guard.check()
        x1, y1, x2, y2 = color_bounds(Pixels(shot.width, shot.height, shot.rgb), BORDER)
    border = [left + x1, top + y1, left + x2, top + y2]
    manifest = acquisition_manifest(owner, border, job.pids())
    return manifest, {
        "initial": initial,
        "pixel_border_rect": border,
        "edge_executable": str(edge),
    }


def paired_reference_calls(
    records: list[Json], start: float, now: float
) -> tuple[Json | None, set[str]]:
    """Reject malformed event streams and every overlong completed native call."""
    pending = None
    previous = start
    completed = set()
    for index, record in enumerate(records):
        timestamp = record["monotonic"]
        if not math.isfinite(timestamp) or timestamp < previous or timestamp > now:
            raise RuntimeError("Reference event time invalid")
        previous = timestamp
        event = record["event"]
        if event == "process" and index == 0:
            continue
        if event == "call_start" and pending is None:
            pending = record
        elif (
            event == "call_end"
            and pending is not None
            and record["operation"] == pending["operation"]
        ):
            if timestamp - pending["monotonic"] >= CALL_SECONDS:
                raise RuntimeError("Reference completed native call timed out")
            completed.add(record["operation"])
            pending = None
        else:
            raise RuntimeError("Reference cleanup failure or invalid call pairing")
    return pending, completed


def validate_reference_log(
    records: list[Json], start: float, now: float, *, finished: bool
) -> None:
    """Inspect completed calls, cleanup, pairing and deadlines before admission."""
    if now - start >= HELPER_SECONDS:
        raise RuntimeError("Reference helper total deadline exceeded")
    if not records:
        if finished or now - start >= CALL_SECONDS:
            raise RuntimeError("Reference helper startup missing or timed out")
        return
    if (
        records[0]["event"] != "process"
        or records[0]["monotonic"] - start >= CALL_SECONDS
    ):
        raise RuntimeError("Reference helper process/startup invalid")
    if (
        len(records) > 1
        and records[1]["monotonic"] - records[0]["monotonic"] >= CALL_SECONDS
    ):
        raise RuntimeError("Reference first native setup timed out")
    pending, completed = paired_reference_calls(records, start, now)
    if len(records) == 1 and now - records[0]["monotonic"] >= CALL_SECONDS:
        raise RuntimeError("Reference native setup timed out")
    if pending is not None and (finished or now - pending["monotonic"] >= CALL_SECONDS):
        raise RuntimeError("Reference native call incomplete or timed out")
    if (
        finished
        and not {"reference.final_guard", "reference.source.close"} <= completed
    ):
        raise RuntimeError("Reference final guard/cleanup evidence missing")


def run_reference(job: OwnedJob, manifest: Json, output: Path) -> Json:
    """Apply30s process and3s logged-native-call bounds to a read-only helper."""
    path = output / "reference-manifest.json"
    write(path, manifest)
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if not base.is_file():
        raise RuntimeError("Verified direct interpreter unavailable")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(Path(sys.prefix) / "Lib/site-packages"), str(ROOT / "src")]
    )
    start = time.monotonic()
    pid, handle = job.start(
        [
            str(base),
            "-m",
            "scripts.panel_matrix_reference",
            "--manifest",
            str(path),
            "--output",
            str(output / "reference"),
        ],
        ROOT,
    )
    while True:
        code = job.wait(handle, 0)
        records = events(output / "reference/calls.jsonl")
        now = time.monotonic()
        validate_reference_log(records, start, now, finished=code is not None)
        if code is not None:
            if code != 0:
                raise RuntimeError(f"Reference helper failed with exit {code}")
            # Re-read the clock after validation, immediately before admission.
            elapsed = time.monotonic() - start
            if elapsed >= HELPER_SECONDS:
                raise RuntimeError("Reference helper total deadline exceeded")
            return {"pid": pid, "exit_code": code, "seconds": elapsed}
        time.sleep(0.05)


def verify_raw(output: Path, initial: Json) -> Json:
    """Require raw diagnostic controls, never a colored-row or stitching oracle."""
    report = json.loads((output / "probe/manifest.json").read_text())
    if (
        not report.get("browser_sequence_completed")
        or not report.get("restored")
        or report.get("first_failure")
    ):
        raise RuntimeError("Matrix raw diagnostic controls failed")
    states = snapshots(output / "states")
    if not states or states[-1]["state"] != initial["state"]:
        raise RuntimeError("Matrix authored initial state was not restored")
    for snapshot in states:
        for pane in ("outer", "sibling"):
            if snapshot["state"][pane] != initial["state"][pane]:
                raise RuntimeError("Matrix outer or sibling moved")
        if (
            snapshot["state"]["target"]["horizontal"]
            != initial["state"]["target"]["horizontal"]
        ):
            raise RuntimeError("Matrix horizontal origin changed")
    return report


def collect_session(
    case: MatrixCase,
    helper_job: OwnedJob,
    output: Path,
    receipt: Json,
) -> None:
    """Collect one raw or fixed-reference session without swallowing failures."""
    manifest, oracle = receipt["acquisition"], receipt["oracle_only"]
    if case.reference_offset is None:
        receipt["helper"] = run_probe(
            helper_job, manifest, output, experiment="browser"
        )
        if receipt["helper"]["exit_code"] != 0:
            raise RuntimeError("Matrix raw helper failed")
        time.sleep(0.15)
        receipt["raw_diagnostic"] = verify_raw(output, oracle["initial"])
    else:
        reference_manifest = {
            **manifest,
            "pixel_border_rect": oracle["pixel_border_rect"],
        }
        receipt["helper"] = run_reference(helper_job, reference_manifest, output)
        captured = json.loads((output / "reference/reference.json").read_text())
        image = load_pixels(output / "reference/reference.png")
        border = oracle["pixel_border_rect"]
        if (
            captured.get("reference_captured") is not True
            or captured.get("rect") != border
        ):
            raise RuntimeError("Reference capture receipt incomplete")
        if (image.width, image.height) != (
            border[2] - border[0],
            border[3] - border[1],
        ):
            raise RuntimeError("Reference saved raster dimensions changed")
        current = snapshots(output / "states")[-1]
        if current["state"] != oracle["initial"]["state"]:
            raise RuntimeError("Reference authored state changed during capture")


def session(case: MatrixCase, scale: str, output: Path) -> Json:
    """Preserve first failure and every independent cleanup result."""
    output.mkdir(exist_ok=False)
    receipt: Json = {
        "first_failure": None,
        "cleanup": {},
        "product_capture_qualified": False,
    }
    fixture_job = helper_job = server = None
    try:
        html = render(case)
        source = output / "authored.html"
        source.write_text(html, encoding="utf-8")
        receipt["source_sha256"] = hashlib.sha256(html.encode()).hexdigest()
        NativeWindow()
        fixture_job, helper_job = OwnedJob(), OwnedJob()
        server = FixtureServer(source, output / "states")
        manifest, oracle = launch(fixture_job, server, output, scale)
        receipt["acquisition"] = manifest
        receipt["oracle_only"] = oracle
        collect_session(case, helper_job, output, receipt)
    except Exception as error:  # noqa: BLE001 - diagnostic first-failure boundary
        receipt["first_failure"] = repr(error)
    finally:
        for name, job in (("helper", helper_job), ("fixture", fixture_job)):
            if job is not None:
                try:
                    receipt["cleanup"][name] = job.close()
                except Exception as error:  # noqa: BLE001 - independent cleanup
                    receipt["cleanup"][name] = {
                        "confirmed": False,
                        "error": repr(error),
                    }
        if server is not None:
            try:
                server.close()
                receipt["cleanup"]["server"] = {"confirmed": True}
            except Exception as error:  # noqa: BLE001 - preserve original failure
                receipt["cleanup"]["server"] = {
                    "confirmed": False,
                    "error": repr(error),
                }
        write(output / "receipt.json", receipt)
    if receipt["first_failure"] is not None or not all(
        v["confirmed"] for v in receipt["cleanup"].values()
    ):
        raise RuntimeError(
            f"Owned matrix session failed; see {output / 'receipt.json'}"
        )
    return receipt


def verify_reference_identity(raw: Json, reference: Json, offset: float) -> None:
    """Admit identical authored raster conditions and exact requested DOM pose."""
    oracle, observed = raw["initial"], reference["initial"]
    target, actual = oracle["state"]["target"], observed["state"]["target"]
    if raw["pixel_border_rect"] != reference["pixel_border_rect"]:
        raise RuntimeError("Reference physical outer rectangle changed")
    if observed["dpr"] != oracle["dpr"] or actual["horizontal"] != target["horizontal"]:
        raise RuntimeError("Reference scale or horizontal origin changed")
    if any(
        actual[k] != target[k]
        for k in (
            "viewport_width",
            "viewport_height",
            "extent_width",
            "extent_height",
            "rect",
            "border",
        )
    ):
        raise RuntimeError("Reference body extent or width changed")
    if any(
        observed["config"][k] != oracle["config"][k]
        for k in ("scenario", "theme", "variant", "width")
    ):
        raise RuntimeError("Reference authored rendering configuration changed")
    if (
        observed["mode"] != "scroll-reference"
        or observed["config"]["reference_offset"] != offset
    ):
        raise RuntimeError("Reference mode or requested offset changed")
    if abs(actual["vertical"] - offset) * oracle["dpr"] > OFFSET_TOLERANCE:
        raise RuntimeError("Reference authored offset was not attained")


def collect_batch(case_name: str, output: Path, result: Json, start: float) -> None:
    """Finish raw acquisition before independent reference planning."""
    case, scale = CASES[case_name]
    raw = session(case, scale, output / "raw")
    oracle = raw["oracle_only"]["initial"]
    target = oracle["state"]["target"]
    offsets = reference_offsets(
        target["extent_height"], target["viewport_height"], oracle["dpr"]
    )
    result["oracle_reference_offsets"] = offsets
    for index, offset in enumerate(offsets):
        if time.monotonic() - start >= BATCH_SECONDS:
            raise RuntimeError("Matrix batch deadline exceeded")
        reference = session(
            replace(case, reference_offset=offset),
            scale,
            output / f"reference-{index:02d}",
        )
        verify_reference_identity(raw["oracle_only"], reference["oracle_only"], offset)
    if time.monotonic() - start >= BATCH_SECONDS:
        raise RuntimeError("Matrix batch deadline exceeded")
    result["raw_and_reference_collection_completed"] = True
    result["body_raster_comparison_pending"] = True


def main() -> int:
    """Collect the raw sequence first, then predetermined independent references."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(CASES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.output = args.output.resolve(strict=True)
    result: Json = {
        "case": args.case,
        "first_failure": None,
        "product_capture_qualified": False,
    }
    start = time.monotonic()
    try:
        result["source"] = attest()
        collect_batch(args.case, args.output, result, start)
    except Exception as error:  # noqa: BLE001 - preserve first collection failure
        result["first_failure"] = repr(error)
    write(args.output / "receipt.json", result)
    return int(result["first_failure"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
