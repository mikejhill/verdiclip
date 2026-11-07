"""Fixture-only full-width oracle independent of production alignment and seams.

At native 96 DPI, fixture snapshots identify physical offsets. Whole authored
31-pixel rows are taken from their most central raw observation. No interpolation,
image registration, padding, or production stitcher decisions enter this oracle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from scripts.panel_probe_oracle import ROW_COUNT, ROW_HEIGHT, Pixels

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EPSILON = 1e-8  # Numeric association only; RGB comparisons remain exact.
NATIVE_DPI = 96


@dataclass(frozen=True)
class ReferenceFrame:
    """Named raw content pixels with an independently associated fixture offset."""

    name: str
    offset: int
    pixels: Pixels


def _integer(value: float) -> int:
    if not math.isfinite(value) or abs(value - round(value)) > EPSILON:
        raise ValueError("Fixture geometry or offset is not integral")
    return round(value)


def associate_frames(
    fixture: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    images: Mapping[str, Pixels],
) -> list[ReferenceFrame]:
    """Associate stable receipts with unique fixture states, never inferred motion.

    Repeated snapshots of the same state are allowed. Conflicting states matching a
    receipt, missing images, changed geometry and nonintegral offsets fail closed.
    Images are supplied by the caller; receipt names are never opened as file paths.
    """
    if fixture["dpi_x"] != NATIVE_DPI or fixture["dpi_y"] != NATIVE_DPI:
        raise ValueError("Full-width reference requires native 96 DPI")
    rect = tuple(_integer(v) for v in fixture["rect"])
    content = tuple(_integer(v) for v in fixture["content_rect"])
    crop = (
        content[0] - rect[0],
        content[1] - rect[1],
        content[2] - rect[0],
        content[3] - rect[1],
    )
    frames = []
    names: set[str] = set()
    for event in events:
        if event["event"] != "stable_frame":
            continue
        name = event["name"]
        if name in names or name not in images:
            raise ValueError("Duplicate receipt or missing raw frame")
        names.add(name)
        matches = _matching_states(states, event["state"]["vertical"])
        if not matches or any(s["state"] != matches[0]["state"] for s in matches):
            raise ValueError("Missing or ambiguous fixture state association")
        state = matches[0]
        if any(
            matched[key] != fixture[key]
            for matched in matches
            for key in ("rect", "content_rect", "dpi_x", "dpi_y")
        ):
            raise ValueError("Fixture geometry changed")
        target = state["state"]["target"]
        if (
            target["extent_height"] != ROW_COUNT * ROW_HEIGHT
            or target["viewport_height"] != content[3] - content[1]
            or not 0
            <= target["vertical"]
            <= target["extent_height"] - target["viewport_height"]
        ):
            raise ValueError("Fixture extent changed")
        raw = images[name]
        if (raw.width, raw.height) != (rect[2] - rect[0], rect[3] - rect[1]):
            raise ValueError("Raw frame bounds differ from fixture")
        frames.append(
            ReferenceFrame(name, _integer(target["vertical"]), raw.crop(crop))
        )
    if not frames:
        raise ValueError("No stable raw frames")
    return frames


def _matching_states(
    states: Sequence[Mapping[str, Any]], percent: float
) -> list[Mapping[str, Any]]:
    matches = []
    if not math.isfinite(percent):
        raise ValueError("Invalid receipt percentage")
    for state in states:
        target = state["state"]["target"]
        extent = target["extent_height"] - target["viewport_height"]
        if extent <= 0:
            raise ValueError("Invalid fixture scroll range")
        expected = target["vertical"] * 100 / extent
        if math.isfinite(expected) and abs(expected - percent) <= EPSILON:
            matches.append(state)
    return matches


def compare_reference(
    frames: Sequence[ReferenceFrame], composite: Pixels
) -> dict[str, Any]:
    """Compare all pixels against complete row bands and corroborating views.

    Every row band must be wholly visible in at least one raw frame. Additional
    views with the whole band strictly inside the viewport must agree exactly.
    Endpoint bands can use repeated endpoint views. Report every differing band;
    never use the composite to select a reference or resolve disagreement.
    """
    if not frames:
        raise ValueError("No reference frames")
    width, height = frames[0].pixels.width, frames[0].pixels.height
    if any((f.pixels.width, f.pixels.height) != (width, height) for f in frames):
        raise ValueError("Reference geometry changed")
    if (composite.width, composite.height) != (width, ROW_COUNT * ROW_HEIGHT):
        raise ValueError("Composite extent differs from fixture")
    bands = []
    for row in range(ROW_COUNT):
        lo, hi = row * ROW_HEIGHT, (row + 1) * ROW_HEIGHT
        eligible = [f for f in frames if f.offset <= lo and hi <= f.offset + height]
        if not eligible:
            raise ValueError(f"Missing full-width reference coverage for row {row}")
        chosen = max(eligible, key=lambda f: min(lo - f.offset, f.offset + height - hi))
        gold = _band(chosen, lo, hi)
        witnesses = [
            f
            for f in eligible
            if (f.offset < lo and hi < f.offset + height) or f.offset == chosen.offset
        ]
        conflicts = [f.name for f in witnesses if _band(f, lo, hi) != gold]
        actual = composite.rgb[lo * width * 3 : hi * width * 3]
        bands.append(
            {
                "row": row,
                "source": chosen.name,
                "witnesses": [f.name for f in witnesses],
                "conflicts": conflicts,
                "matches": actual == gold,
            }
        )
    return {
        "passed": all(b["matches"] and not b["conflicts"] for b in bands),
        "all_pixels_checked": width * ROW_COUNT * ROW_HEIGHT,
        "bands": bands,
    }


def _band(frame: ReferenceFrame, lo: int, hi: int) -> bytes:
    stride = frame.pixels.width * 3
    return frame.pixels.rgb[(lo - frame.offset) * stride : (hi - frame.offset) * stride]
