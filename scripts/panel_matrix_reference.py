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
from pathlib import Path
from typing import Any

import mss
import mss.tools
from scripts.panel_feasibility import NativeWindow, write
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia_probe import NativeGuard

MAX_TILES = 16
MAX_EXTENT = 8_000
MAX_PIXELS = 2_000_000
OVERLAP_PIXELS = 32
STABLE_COUNT = 3
MAX_DPR = 2
SETTLE_SECONDS = 2


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


def capture_reference(manifest: dict[str, Any], output: Path) -> None:
    """Read only an owned fixed reference, guarded before and after native calls."""
    output.mkdir(exist_ok=False)
    rect = manifest["pixel_border_rect"]
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("Reference raster exceeds bounds")
    started = time.monotonic()
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
        emit("call_start", "reference.setup")
        guard = NativeGuard(
            manifest["root_hwnd"],
            round(manifest["click"][0]),
            round(manifest["click"][1]),
        )
        native = NativeWindow()
        emit("call_end", "reference.setup")
        previous = None
        matched = 0
        with logged_source(emit) as source:
            check_monitor(source, guard, rect, emit)
            while time.monotonic() - started < SETTLE_SECONDS:
                emit("call_start", "reference.guard")
                guard.check()
                owner = native.foreground(manifest["owned_process_ids"])
                guard.check()
                emit("call_end", "reference.guard")
                if (
                    owner["root_hwnd"] != manifest["root_hwnd"]
                    or owner["allowed_rect"] != manifest["allowed_rect"]
                ):
                    raise RuntimeError("Owned reference geometry changed")
                emit("call_start", "reference.grab")
                guard.check()
                shot = source.grab(
                    {"left": rect[0], "top": rect[1], "width": width, "height": height}
                )
                guard.check()
                emit("call_end", "reference.grab")
                if (shot.width, shot.height) != (width, height):
                    raise RuntimeError("Reference native raster dimensions changed")
                rgb = shot.rgb
                matched = matched + 1 if rgb == previous else 1
                previous = rgb
                if matched == STABLE_COUNT:
                    if time.monotonic() - started >= SETTLE_SECONDS:
                        break
                    mss.tools.to_png(
                        rgb, (width, height), output=str(output / "reference.png")
                    )
                    emit("call_start", "reference.final_guard")
                    guard.check()
                    emit("call_end", "reference.final_guard")
                    write(
                        output / "reference.json",
                        {"reference_captured": True, "rect": rect, "samples": matched},
                    )
                    return
                time.sleep(0.1)
    raise RuntimeError("Reference pixels did not settle within two seconds")


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
