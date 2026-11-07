"""Owned single-pose paint control; no scrolling, crop fitting or product calls."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import mss
import mss.tools
from scripts.panel_feasibility import NativeWindow, write
from scripts.panel_identity_probe import Baseline, checkpoint
from scripts.panel_matrix_reference import latest_authored_state
from scripts.panel_paint_fixture import request_phase
from scripts.panel_paint_oracle import MAX_PIXELS, paint_support
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia_identity import (
    IdentityUia,
    cleanup,
    logged,
    record_cleanup_error,
)
from scripts.panel_uia_probe import NativeGuard, Probe

PHASE_SECONDS = 2
MAX_SAMPLES = 16
MAX_NODES = 512
STABLE_COUNT = 3
QUIET_SECONDS = 0.2
Json = dict[str, Any]


def state_key(state: Json) -> Json:
    """Only explicit opacity/phase fields may change across the control."""
    paint = state["paint"]
    if (
        paint["failure"] is not None
        or paint["descendants_same_opacity"] is not True
        or type(paint["descendants"]) is not int
        or not 0 < paint["descendants"] <= MAX_NODES
    ):
        raise RuntimeError("Authored opacity control failed")
    return {
        key: state[key]
        for key in ("state", "viewport", "dpr", "config", "stream_tick", "calibration")
    } | {"styles": paint["styles"], "descendants": paint["descendants"]}


def phase_state(state: Json, phase: int, expected: Json) -> None:
    """Require acknowledged fixed phase and invariant layout/scroll/styles."""
    paint = state["paint"]
    if (
        type(paint["phase"]) is not int
        or paint["phase"] != phase
        or paint["opacity"] != ("0" if phase == 1 else "1")
        or state_key(state) != expected
    ):
        raise RuntimeError("Paint phase or invariant authored state changed")


def retained_identity(probe: Probe, baseline: Baseline, phase: int) -> None:
    """Opacity may change paint only; even offscreen must stay unchanged."""
    client = baseline.client
    if probe.guard is None or client.target is None:
        raise RuntimeError("Paint identity missing")
    probe.guard.check()
    if (
        client.ids.read(client.target) != baseline.target_id
        or client.ids.read(baseline.root) != baseline.root_id
    ):
        raise RuntimeError("Paint target/root identity changed")
    structure = client.structure(client.target)
    current_state = client.read_state()
    probe.emit(
        {
            "event": "paint_identity",
            "phase": phase,
            "structure": asdict(structure),
            "state": asdict(current_state),
        }
    )
    if structure != baseline.structure or current_state != baseline.state:
        raise RuntimeError("Paint native geometry/state changed")
    probe.guard.check()


def capture_phase(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917 - bounded phase protocol
    probe: Probe,
    baseline: Baseline,
    source: mss.MSS,
    fixture: Json,
    phase: int,
    expected: Json,
) -> Pixels:
    """Fixed two-second phase budget includes acknowledgement and final readback."""
    started = time.monotonic()
    request_phase(Path(fixture["phase_command"]), phase)
    previous = None
    matched = 0
    equal_since = started
    native = NativeWindow()
    root = fixture["allowed_rect"]
    width, height = root[2] - root[0], root[3] - root[1]
    if not 0 < width * height <= MAX_PIXELS or min(width, height) <= 0:
        raise RuntimeError("Paint root pixel bound exceeded")
    states = Path(fixture["state_directory"])
    for sample in range(MAX_SAMPLES):
        if time.monotonic() - started >= PHASE_SECONDS:
            break
        before = latest_authored_state(states)
        if before["paint"]["phase"] != phase:
            # Phase acknowledgement is the only allowed wait, not native retries.
            if state_key(before) != expected or before["paint"]["phase"] != max(
                0, phase - 1
            ):
                raise RuntimeError("Unexpected paint transition telemetry")
            time.sleep(0.05)
            continue
        phase_state(before, phase, expected)
        retained_identity(probe, baseline, phase)

        def grab() -> Pixels:
            owner = native.foreground(fixture["owned_process_ids"])
            if (
                owner["root_hwnd"] != fixture["root_hwnd"]
                or owner["allowed_rect"] != root
            ):
                raise RuntimeError("Paint root geometry changed")
            shot = source.grab(
                {"left": root[0], "top": root[1], "width": width, "height": height}
            )
            if native.foreground(fixture["owned_process_ids"]) != owner:
                raise RuntimeError("Paint root changed across capture")
            return Pixels(shot.width, shot.height, shot.rgb)

        image = logged(probe.emit, "paint.grab", grab)
        retained_identity(probe, baseline, phase)
        after = latest_authored_state(states)
        while (
            after["sequence"] <= before["sequence"]
            and time.monotonic() - started < PHASE_SECONDS
        ):
            time.sleep(0.01)
            after = latest_authored_state(states)
        phase_state(after, phase, expected)
        if after["sequence"] <= before["sequence"]:
            raise RuntimeError("Paint observation lacks fresh telemetry")
        if (image.width, image.height) != (width, height):
            raise RuntimeError("Paint image geometry changed")
        now = time.monotonic()
        if image == previous:
            matched += 1
        else:
            previous, matched, equal_since = image, 1, now
        # Retain every completed sample's evidence even on late admission failure.
        write(
            probe.output / f"phase-{phase}-sample-{sample:02}.json",
            {
                "before": before,
                "after": after,
                "stable_count": matched,
                "phase_elapsed": now - started,
            },
        )
        mss.tools.to_png(
            image.rgb,
            (width, height),
            output=str(probe.output / f"phase-{phase}-last.png"),
        )
        if time.monotonic() - started >= PHASE_SECONDS:
            break
        if matched >= STABLE_COUNT and now - equal_since >= QUIET_SECONDS:
            retained_identity(probe, baseline, phase)
            if time.monotonic() - started >= PHASE_SECONDS:
                break
            return image
        time.sleep(0.05)
    raise RuntimeError("Paint phase did not qualify within original two-second bound")


def execute(probe: Probe, fixture: Json) -> None:  # noqa: PLR0915 - owned lifetime
    """Retain one native target before A, reselect/check it after restored A-prime."""
    guard = NativeGuard(int(fixture["root_hwnd"]), probe.args.x, probe.args.y)
    probe.guard = guard
    client = IdentityUia(probe.emit)
    probe.uia = client
    source = None
    try:
        initial = probe.discover_target()
        probe.identity = initial
        probe.check_rect(fixture)
        if client.target is None:
            raise RuntimeError("Paint target missing")
        root = client.from_handle(guard.root)
        baseline = Baseline(
            client,
            root,
            client.ids.read(root),
            client.ids.read(client.target),
            initial,
            client.read_state(),
        )
        checkpoint(probe, baseline, guard, 0)
        probe.report["selected_before"] = asdict(initial)
        probe.report["native_scroll_state"] = asdict(baseline.state)
        expected = state_key(latest_authored_state(Path(fixture["state_directory"])))
        # Retain a successfully created resource before any post-call admission.
        probe.emit({"event": "call_start", "name": "paint.source"})
        source = mss.MSS()
        probe.emit({"event": "call_end", "name": "paint.source"})
        monitors = logged(probe.emit, "paint.monitors", lambda: source.monitors)
        a, b, c, d = fixture["allowed_rect"]
        if not any(
            a >= m["left"]
            and b >= m["top"]
            and c <= m["left"] + m["width"]
            and d <= m["top"] + m["height"]
            for m in monitors[1:]
        ):
            raise RuntimeError("Paint root crosses monitor")
        images = [
            capture_phase(probe, baseline, source, fixture, phase, expected)
            for phase in range(3)
        ]
        checkpoint(probe, baseline, guard, 1)
        probe.report["selected_after"] = asdict(client.structure(client.target))
        result = paint_support(
            images[0], images[1], images[2], (a, b, c, d), initial.rect
        )
        guard.check()
        probe.report["paint_support"] = asdict(result)
        probe.report["single_pose_only"] = True
        probe.report["complete_capture_qualified"] = False
        probe.report["paint_control_completed"] = True
    finally:
        primary = sys.exception()
        failures: list[BaseException] = []
        if source is not None:
            try:
                cleanup(probe.emit, "paint.source.close", source.close)
            except BaseException as error:  # noqa: BLE001 - attempt all cleanup
                failures.append(error)
                record_cleanup_error(probe.emit, error)
        try:
            client.close()
        except BaseException as error:  # noqa: BLE001 - attempt all cleanup
            failures.append(error)
            record_cleanup_error(probe.emit, error)
        if primary is None and failures:
            raise failures[0]


def main() -> int:
    """Fixed owned helper, externally supervised; no restoration after failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture_manifest.read_text(encoding="utf-8"))
    args.x, args.y = (round(x) for x in fixture["click"])
    args.settle_discovery = True
    probe = Probe(args)
    try:
        execute(probe, fixture)
    except Exception as error:  # noqa: BLE001 - preserve first evidence
        probe.report["first_failure"] = repr(error)
        probe.emit({"event": "first_failure", "error": repr(error)})
    finally:
        write(probe.output / "manifest.json", probe.report)
        probe.log.close()
    return int(probe.report["first_failure"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
