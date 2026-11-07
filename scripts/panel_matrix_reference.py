"""Bounded owned reference planning and full outer-raster helper; no stitching."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mss
import mss.tools
from scripts.panel_feasibility import NativeWindow, snapshots, write
from scripts.panel_matrix_origin import ViewportOrigin, verify_calibration
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia_probe import NativeGuard

MAX_TILES = 16
MAX_EXTENT = 8_000
MAX_PIXELS = 2_000_000
OVERLAP_PIXELS = 32
STABLE_COUNT = 3
MAX_DPR = 2
SETTLE_SECONDS = 2
MAX_READINESS_SAMPLES = 16


def reference_offsets(extent: float, viewport: float, dpr: float) -> tuple[float, ...]:
    """Plan oracle tiles using only independently authored DOM dimensions."""
    if not all(math.isfinite(x) for x in (extent, viewport, dpr)):
        raise ValueError("Nonfinite reference geometry")
    if not 0 < viewport < extent <= MAX_EXTENT or not 1 <= dpr <= MAX_DPR:
        raise ValueError("Unsupported reference extent or scale")
    step = math.floor(viewport * dpr) - OVERLAP_PIXELS
    distance = (extent - viewport) * dpr
    if step <= 0 or math.ceil(distance / step) + 1 > MAX_TILES:
        raise ValueError("Reference tile budget exceeded")
    return tuple(
        min(index * step, distance) / dpr
        for index in range(math.ceil(distance / step) + 1)
    )


def body_bounds(content: list[float], outer: list[int]) -> tuple[int, int, int, int]:
    """Include fractional body-edge pixels; never silently shrink to interiors."""
    left, top = math.floor(content[0]), math.floor(content[1])
    right, bottom = math.ceil(content[2]), math.ceil(content[3])
    if (
        not outer[0] <= left < right <= outer[2]
        or not outer[1] <= top < bottom <= outer[3]
    ):
        raise ValueError("Reference body is outside its outer frame")
    return left - outer[0], top - outer[1], right - outer[0], bottom - outer[1]


@contextmanager
def logged_source(emit: Callable[[str, str], None]) -> Generator[mss.MSS]:
    """Watch setup and cleanup too, without ownership guards during cleanup."""
    emit("call_start", "reference.source")
    source = mss.MSS()
    emit("call_end", "reference.source")
    try:
        yield source
    finally:
        failure = sys.exception()
        emit("call_start", "reference.source.close")
        try:
            source.close()
        except Exception as error:
            emit("cleanup_error", repr(error))
            if failure is None:
                raise
        finally:
            emit("call_end", "reference.source.close")


def check_monitor(
    source: mss.MSS,
    guard: NativeGuard,
    rect: list[int],
    emit: Callable[[str, str], None],
) -> None:
    """Guard and watch native monitor enumeration before using its result."""
    emit("call_start", "reference.monitors")
    guard.check()
    monitors = source.monitors
    guard.check()
    emit("call_end", "reference.monitors")
    if not any(
        rect[0] >= monitor["left"]
        and rect[1] >= monitor["top"]
        and rect[2] <= monitor["left"] + monitor["width"]
        and rect[3] <= monitor["top"] + monitor["height"]
        for monitor in monitors[1:]
    ):
        raise RuntimeError("Reference target is not wholly on one monitor")


def latest_authored_state(directory: Path) -> dict[str, Any]:
    """Require fresh observer-backed authored telemetry, never zero-box fallback."""
    values = snapshots(directory)
    if not values or values[-1].get("observer_ready") is not True:
        raise RuntimeError("Authored content-box observer not ready")
    latest = values[-1]
    for pane in ("target", "outer", "sibling"):
        box = latest["state"][pane].get("content_box")
        if not box or not all(
            math.isfinite(box[k]) and box[k] > 0 for k in ("width", "height")
        ):
            raise RuntimeError("Authored content-box dimensions unavailable")
    return latest


@dataclass
class RootObservationContext:
    """Bounded read-only evidence inputs, never capture matcher geometry."""

    manifest: dict[str, Any]
    output: Path
    states: Path | None
    observations: list[dict[str, Any]]
    started: float


def check_readiness_deadline(started: float) -> None:
    """Reject expiration after native resource cleanup, before admission."""
    if time.monotonic() - started >= SETTLE_SECONDS:
        raise RuntimeError("Owned root readiness deadline exceeded")


def stable_owned_root(
    manifest: dict[str, Any], output: Path, states: Path | None = None
) -> Pixels:
    """Retain full root pixels across three guarded stable observations in2s."""
    output.mkdir(exist_ok=False)
    rect = manifest["allowed_rect"]
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("Owned root raster exceeds bounds")
    observations: list[dict[str, Any]] = []
    started = time.monotonic()
    failure = None
    with (output / "calls.jsonl").open("x", encoding="utf-8") as log:

        def emit(event: str, operation: str) -> None:
            log.write(
                json.dumps(
                    {
                        "event": event,
                        "operation": operation,
                        "monotonic": time.monotonic(),
                    }
                )
                + "\n"
            )
            log.flush()

        emit("process", "reference")
        try:
            emit("call_start", "reference.setup")
            guard = NativeGuard(
                manifest["root_hwnd"],
                round((rect[0] + rect[2]) / 2),
                round((rect[1] + rect[3]) / 2),
            )
            native = NativeWindow()
            emit("call_end", "reference.setup")
            with logged_source(emit) as source:
                check_monitor(source, guard, rect, emit)
                image = root_observations(
                    source,
                    native,
                    guard,
                    RootObservationContext(
                        manifest, output, states, observations, started
                    ),
                    emit,
                )
                emit("call_start", "reference.final_guard")
                guard.check()
                emit("call_end", "reference.final_guard")
            check_readiness_deadline(started)
        except Exception as error:
            failure = repr(error)
            raise
        else:
            return image
        finally:
            write(
                output / "readiness.json",
                {
                    "root_rect": rect,
                    "observations": observations,
                    "first_failure": failure,
                },
            )


def prepare_origin_reader(
    states: Path | None, guard: NativeGuard, emit: Callable[[str, str], None]
) -> ViewportOrigin | None:
    """Guard and log reference-only native library setup."""
    origin_reader = None
    if states is not None:
        emit("call_start", "reference.origin.setup")
        guard.check()
        origin_reader = ViewportOrigin(guard, emit)
        guard.check()
        emit("call_end", "reference.origin.setup")
    return origin_reader


def root_observations(
    source: mss.MSS,
    native: NativeWindow,
    guard: NativeGuard,
    context: RootObservationContext,
    emit: Callable[[str, str], None],
) -> Pixels:
    """Bracket each full root raster with ownership and optional fresh DOM state."""
    manifest, output, states, observations, started = (
        context.manifest,
        context.output,
        context.states,
        context.observations,
        context.started,
    )
    rect = manifest["allowed_rect"]
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    origin_reader = prepare_origin_reader(states, guard, emit)
    previous_origin = None
    previous = None
    previous_state = None
    matched = 0
    while (
        time.monotonic() - started < SETTLE_SECONDS
        and len(observations) < MAX_READINESS_SAMPLES
    ):
        before = latest_authored_state(states) if states is not None else None
        origin_before = origin_reader.observe(manifest) if origin_reader else None
        emit("call_start", "reference.grab")
        guard.check()
        owner = native.foreground(manifest["owned_process_ids"])
        if owner["root_hwnd"] != manifest["root_hwnd"] or owner["allowed_rect"] != rect:
            raise RuntimeError("Owned root geometry changed before raster")
        shot = source.grab(
            {"left": rect[0], "top": rect[1], "width": width, "height": height}
        )
        guard.check()
        after_owner = native.foreground(manifest["owned_process_ids"])
        guard.check()
        emit("call_end", "reference.grab")
        if after_owner != owner or (shot.width, shot.height) != (width, height):
            raise RuntimeError("Owned root geometry changed after raster")
        rgb = shot.rgb
        if not observations:
            mss.tools.to_png(
                rgb, (width, height), output=str(output / "root-first.png")
            )
        mss.tools.to_png(rgb, (width, height), output=str(output / "root-last.png"))
        origin_after = origin_reader.observe(manifest) if origin_reader else None
        write(
            output / f"origin-{len(observations):02d}.json",
            {"before": origin_before, "after": origin_after},
        )
        if origin_before != origin_after or (
            previous_origin is not None and previous_origin != origin_after
        ):
            raise RuntimeError("Render widget origin changed across reference raster")
        previous_origin = origin_after
        calibration = (
            verify_calibration(rgb, rect, origin_after, before)
            if origin_after is not None and before is not None
            else None
        )
        after = before
        while states is not None and time.monotonic() - started < SETTLE_SECONDS:
            after = latest_authored_state(states)
            if before is not None and after["sequence"] > before["sequence"]:
                break
            time.sleep(0.02)
        stable_state = before is None or (
            after is not None
            and after["sequence"] > before["sequence"]
            and after["state"] == before["state"]
        )
        state = after["state"] if after is not None else None
        matched = (
            matched + 1
            if stable_state and rgb == previous and state == previous_state
            else int(stable_state)
        )
        observations.append(
            {
                "origin": origin_after,
                "calibration": calibration,
                "before": before,
                "after": after,
                "root_rect": rect,
                "stable_count": matched,
            }
        )
        previous, previous_state = rgb, state
        if matched == STABLE_COUNT and time.monotonic() - started < SETTLE_SECONDS:
            return Pixels(width, height, rgb)
        time.sleep(0.05)
    raise RuntimeError("Owned root pixels/state did not settle within two seconds")


def capture_reference(manifest: dict[str, Any], output: Path) -> None:
    """Capture the whole owned root; color borders never crop reference evidence."""
    mode = manifest.get("capture_mode")
    if mode not in {"entry-readiness", "oracle-reference"}:
        raise ValueError("Unknown owned root capture mode")
    states = Path(manifest["state_directory"]) if mode == "oracle-reference" else None
    image = stable_owned_root(manifest, output, states)
    mss.tools.to_png(
        image.rgb, (image.width, image.height), output=str(output / "reference.png")
    )
    write(
        output / "reference.json",
        {
            "reference_captured": True,
            "rect": manifest["allowed_rect"],
            "samples": STABLE_COUNT,
            "capture_scope": "owned-root",
        },
    )


def main() -> int:
    """Child entry is restricted to the same disposable owned CI environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("Reference capture requires disposable hosted CI")
    capture_reference(json.loads(args.manifest.read_text()), args.output)
    return 0


def selected_from_root(image: Pixels, root: list[int], selected: list[int]) -> Pixels:
    """Use exact native half-open physical bounds, never color/DOM crop guesses."""
    if (image.width, image.height) != (root[2] - root[0], root[3] - root[1]):
        raise ValueError("Owned root raster geometry differs")
    left, top, right, bottom = selected
    if not root[0] <= left < right <= root[2] or not root[1] <= top < bottom <= root[3]:
        raise ValueError("Selected outer rectangle is outside reference root")
    return image.crop(
        [left - root[0], top - root[1], right - root[0], bottom - root[1]]
    )


def assemble_reference_body(tiles: list[tuple[int, Pixels]], total_rows: int) -> Pixels:
    """Build independent full-edge reference coverage; fail on any discrepancy.

    Only viewport clipping rows of nonendpoint tiles are ignored, and every
    resulting output row must still have exact independent reference coverage.
    Horizontal fractional-edge columns are always retained and compared.
    """
    if (
        not tiles
        or len(tiles) > MAX_TILES
        or not 0 < total_rows <= MAX_EXTENT * MAX_DPR
    ):
        raise ValueError("Invalid reference mosaic bounds")
    width = tiles[0][1].width
    if width * total_rows > MAX_PIXELS * MAX_TILES:
        raise ValueError("Reference mosaic memory budget exceeded")
    stride = width * 3
    result = bytearray(total_rows * stride)
    seen = bytearray(total_rows)
    for offset, tile in tiles:
        if tile.width != width or offset < 0 or offset + tile.height > total_rows:
            raise ValueError("Reference tile geometry disagrees")
        start = 0 if offset == 0 else 1
        stop = tile.height if offset + tile.height == total_rows else tile.height - 1
        for row in range(start, stop):
            destination = offset + row
            source = tile.rgb[row * stride : (row + 1) * stride]
            begin, end = destination * stride, (destination + 1) * stride
            if seen[destination] and result[begin:end] != source:
                raise ValueError("Reference body or fractional-edge overlap differs")
            result[begin:end] = source
            seen[destination] = 1
    if not all(seen):
        raise ValueError("Reference body coverage incomplete")
    if all(result[i : i + 3] == result[:3] for i in range(0, len(result), 3)):
        raise ValueError("Reference body is blank")
    return Pixels(width, total_rows, bytes(result))


def compare_body(actual: Pixels, expected: Pixels) -> dict[str, int | bool]:
    """Count every body-inclusive pixel, with no fractional-edge tolerance."""
    if (actual.width, actual.height) != (expected.width, expected.height):
        raise ValueError("Complete body raster geometry differs")
    mismatches = sum(
        actual.rgb[index : index + 3] != expected.rgb[index : index + 3]
        for index in range(0, len(actual.rgb), 3)
    )
    return {
        "exact": mismatches == 0,
        "mismatched_pixels": mismatches,
        "compared_pixels": actual.width * actual.height,
        "fractional_edge_columns_included": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
