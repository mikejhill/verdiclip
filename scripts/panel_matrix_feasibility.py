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

from scripts import panel_matrix_raw_geometry as raw_geometry
from scripts.panel_feasibility import (
    ROOT,
    NativeWindow,
    attest,
    events,
    load_pixels,
    run_probe,
    snapshots,
    verify_diagnostic,
    wait_snapshot,
    write,
)
from scripts.panel_fixture_matrix import MAX_REFERENCE_OFFSET, MatrixCase, render
from scripts.panel_fixture_server import FixtureServer
from scripts.panel_matrix_reference import reference_offsets
from scripts.panel_owned_job import OwnedJob
from scripts.panel_probe_oracle import color_bounds
from scripts.panel_reference_outcome import retain_failure

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
    job: OwnedJob,
    server: FixtureServer,
    output: Path,
    scale: str,
) -> tuple[Json, Json]:
    """Locate only the authored entry border; no DOM geometry selects capture."""
    candidates = [
        Path(os.environ.get(v, "C:/missing")) / "Microsoft/Edge/Application/msedge.exe"
        for v in ("PROGRAMFILES(X86)", "PROGRAMFILES")
    ]
    edge = next((p for p in candidates if p.is_file()), None)
    if edge is None:
        raise RuntimeError("Installed Edge unavailable")
    argv = [
        str(edge),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        f"--user-data-dir={output / 'edge-profile'}",
        "--window-size=760,520",
        "--window-position=40,40",
        f"--force-device-scale-factor={scale}",
        "--app=" + server.url,
    ]
    _, handle = job.start(argv, ROOT)
    initial = wait_snapshot(server.output, job, handle)
    native = NativeWindow()
    owner = native.foreground(job.pids())
    left, top, right, bottom = owner["allowed_rect"]
    manifest = {
        **owner,
        "kind": "owned-edge",
        "owned_process_ids": job.pids(),
        "click": [(left + right) / 2, (top + bottom) / 2],
    }
    return manifest, {
        "initial": initial,
        "root_rect": owner["allowed_rect"],
        "edge_executable": str(edge),
        "launch_argv": argv,
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
        try:
            validate_reference_log(records, start, now, finished=code is not None)
        except RuntimeError as error:
            primary = retain_failure(output, manifest, records, code, str(error))
            raise RuntimeError(primary) from error
        if code is not None:
            if code != 0:
                primary = retain_failure(output, manifest, records, code, None)
                raise RuntimeError(primary)
            # Re-read the clock after validation, immediately before admission.
            elapsed = time.monotonic() - start
            if elapsed >= HELPER_SECONDS:
                primary = retain_failure(
                    output,
                    manifest,
                    records,
                    code,
                    "Reference helper total deadline exceeded",
                )
                raise RuntimeError(primary)
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


def prepare_acquisition(helper_job: OwnedJob, output: Path, receipt: Json) -> None:
    """Locate the owned entry only after a bounded root-only readiness helper."""
    entry = output / "entry-readiness"
    entry.mkdir(exist_ok=False)
    owner = receipt["acquisition"]
    receipt["entry_helper"] = run_reference(
        helper_job, {**owner, "capture_mode": "entry-readiness"}, entry
    )
    root = load_pixels(entry / "reference/reference.png")
    if (root.width, root.height) != (
        owner["allowed_rect"][2] - owner["allowed_rect"][0],
        owner["allowed_rect"][3] - owner["allowed_rect"][1],
    ):
        raise RuntimeError("Owned entry root raster dimensions changed")
    left, top, _, _ = owner["allowed_rect"]
    x1, y1, x2, y2 = color_bounds(root, BORDER)
    border = [left + x1, top + y1, left + x2, top + y2]
    receipt["acquisition"] = acquisition_manifest(
        owner, border, owner["owned_process_ids"]
    )
    receipt["oracle_only"]["pixel_border_rect"] = border


def collect_session(
    case: MatrixCase,
    helper_job: OwnedJob,
    output: Path,
    receipt: Json,
) -> None:
    """Collect one raw or fixed-reference session without swallowing failures."""
    manifest, oracle = receipt["acquisition"], receipt["oracle_only"]
    if case.reference_offset is None and not case.reference_endpoint:
        prepare_acquisition(helper_job, output, receipt)
        manifest = receipt["acquisition"]
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
            "state_directory": str((output / "states").resolve(strict=True)),
            "capture_mode": "oracle-reference",
        }
        receipt["helper"] = run_reference(helper_job, reference_manifest, output)
        captured = json.loads((output / "reference/reference.json").read_text())
        image = load_pixels(output / "reference/reference.png")
        border = oracle["root_rect"]
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


def validate_raw_completion(helper: Json, records: list[Json]) -> None:
    """Validate returned-call timing independently of the live pending watchdog."""
    elapsed: Any = helper.get("seconds")
    if (
        type(elapsed) not in (int, float)
        or not math.isfinite(elapsed)
        or not 0 <= elapsed < HELPER_SECONDS
        or helper.get("timed_out") is not False
        or type(helper.get("exit_code")) is not int
        or helper["exit_code"] != 0
    ):
        raise RuntimeError("Raw helper completion/total deadline invalid")
    completed = [record for record in records if record.get("event") == "call_end"]
    if not completed:
        raise RuntimeError("Raw completed native call evidence missing")
    # Wrappers and nested COM calls each retain their own measured duration.
    for record in completed:
        duration: Any = record.get("seconds")
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or not 0 <= duration < CALL_SECONDS
        ):
            raise RuntimeError("Raw completed native call duration invalid or >=3s")


def collect_raw_geometry(  # noqa: PLR0913, PLR0917 - explicit fixed experiment inputs
    case: MatrixCase,
    scale: str,
    html: str,
    helper_job: OwnedJob,
    output: Path,
    receipt: Json,
) -> None:
    """One original-pose read only; no browser sequence or reference planning."""
    initial = receipt["oracle_only"]["initial"]
    # Reject wrong assets/pose before readiness or the native diagnostic starts.
    raw_geometry.manifest(receipt["acquisition"], initial, case, scale, html)
    prepare_acquisition(helper_job, output, receipt)
    manifest = raw_geometry.manifest(receipt["acquisition"], initial, case, scale, html)
    receipt["acquisition"] = manifest
    current = snapshots(output / "states")
    if not current or any(item["state"] != initial["state"] for item in current):
        raise RuntimeError("Matrix pose drift before raw probe")
    raw_geometry.validate_manifest(manifest)
    receipt["raw_geometry_diagnostic"] = True
    receipt["helper"] = run_probe(
        helper_job, manifest, output, experiment="raw-geometry"
    )
    validate_raw_completion(receipt["helper"], events(output / "probe/calls.jsonl"))
    verify_diagnostic(receipt, output)
    # Same bounded root-only raster helper, not a reference pose or seek.
    after = output / "exit-readiness"
    after.mkdir(exist_ok=False)
    receipt["exit_helper"] = run_reference(
        helper_job, {**manifest, "capture_mode": "entry-readiness"}, after
    )
    if load_pixels(output / "entry-readiness/reference/reference.png") != load_pixels(
        after / "reference/reference.png"
    ):
        raise RuntimeError("Matrix full-root pixels changed")
    current = snapshots(output / "states")
    if not current or any(item["state"] != initial["state"] for item in current):
        raise RuntimeError("Matrix pose drift during raw probe")
    receipt["full_root_unchanged"] = True


def session(case: MatrixCase, scale: str, output: Path, *, raw: bool = False) -> Json:
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
        manifest, oracle = launch(
            fixture_job,
            server,
            output,
            scale,
        )
        receipt["acquisition"] = manifest
        receipt["oracle_only"] = oracle
        if raw:
            receipt["oracle_only"]["edge_sha256"] = hashlib.sha256(
                Path(oracle["edge_executable"]).read_bytes()
            ).hexdigest()
            collect_raw_geometry(case, scale, html, helper_job, output, receipt)
        else:
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


def verify_reference_identity(raw: Json, reference: Json, offset: float | None) -> None:
    """Admit identical authored raster conditions and exact requested DOM pose."""
    oracle, observed = raw["initial"], reference["initial"]
    target, actual = oracle["state"]["target"], observed["state"]["target"]
    if (
        oracle.get("observer_ready") is not True
        or observed.get("observer_ready") is not True
    ):
        raise RuntimeError("Reference content-box observer not initialized")
    if raw["root_rect"] != reference["root_rect"]:
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
            "fractional_border",
            "content_box",
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
    if offset is None:
        if (
            observed["config"].get("reference_endpoint") is not True
            or observed.get("endpoint_limit") != actual["vertical"]
            or not 0 < actual["vertical"] <= MAX_REFERENCE_OFFSET
        ):
            raise RuntimeError("Reference actual settled endpoint not established")
    elif abs(actual["vertical"] - offset) * oracle["dpr"] > OFFSET_TOLERANCE:
        raise RuntimeError("Reference authored offset was not attained")


def collect_batch(case_name: str, output: Path, result: Json, start: float) -> None:
    """Finish raw acquisition before independent reference planning."""
    case, scale = CASES[case_name]
    raw = session(case, scale, output / "raw")
    oracle = raw["oracle_only"]["initial"]
    target = oracle["state"]["target"]
    offsets: list[float | None] = list(
        reference_offsets(
            target["extent_height"], target["viewport_height"], oracle["dpr"]
        )
    )
    offsets[-1] = None  # Explicit authored bottom, never rounded integer DOM extent.
    result["oracle_reference_offsets"] = offsets
    for index, offset in enumerate(offsets):
        if time.monotonic() - start >= BATCH_SECONDS:
            raise RuntimeError("Matrix batch deadline exceeded")
        reference = session(
            replace(case, reference_offset=offset, reference_endpoint=offset is None),
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
    parser.add_argument("--raw-geometry-diagnostic", action="store_true")
    args = parser.parse_args()
    if args.raw_geometry_diagnostic and args.case != "table-dark125":
        parser.error("Raw geometry requires original table-dark125")
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
        if args.raw_geometry_diagnostic:
            case, scale = CASES[args.case]
            raw_geometry.provenance()
            result["raw"] = session(case, scale, args.output / "raw", raw=True)
            if time.monotonic() - start >= BATCH_SECONDS:
                raise RuntimeError("Matrix batch deadline exceeded")  # noqa: TRY301
            result["raw_geometry_diagnostic_completed"] = True
        else:
            collect_batch(args.case, args.output, result, start)
    except Exception as error:  # noqa: BLE001 - preserve first collection failure
        result["first_failure"] = repr(error)
    write(args.output / "receipt.json", result)
    return int(result["first_failure"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
