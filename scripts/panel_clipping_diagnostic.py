"""Fixed owned-WPF clipping observations; never relax capture gates.

WPF provider arithmetic source (not proof of hosted runtime version):
https://source.dot.net/PresentationFramework/System/Windows/Automation/Peers/ScrollViewerAutomationPeer.cs.html
"""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import override

from scripts.panel_full_sweep import FULL_PERCENT, FullSweep

from verdiclip.capture.scroll_stitch import ScrollFrame, ScrollStitcher, StitchError

# Fixed before any native run: surround the failing 504 -> 630 pair and put
# document rows near 754 comfortably inside the reference frame at offset 630.
REFERENCE_OFFSET = 630
EXPECTED_DPI = 96
MAX_DIAGNOSTIC_PIXELS = 1_000_000


def validate_fixture(fixture: dict[str, object]) -> None:
    """Limit the deliberate fixed-offset diagnostic to its owned native fixture."""
    if (
        fixture.get("kind"),
        fixture.get("mode"),
        fixture.get("dpi_x"),
        fixture.get("dpi_y"),
    ) != (
        "owned-wpf",
        "nested",
        EXPECTED_DPI,
        EXPECTED_DPI,
    ):
        raise RuntimeError("Clipping diagnostic requires owned nested WPF at 96 DPI")


def requests(scroll_range: float) -> tuple[tuple[str, int, float], ...]:
    """Choose every input before observing pixels; never search for a passing seam."""
    direct = 504 * FULL_PERCENT / scroll_range
    accumulated = 0.0
    for _ in range(5):
        percent = accumulated + 126 * FULL_PERCENT / scroll_range
        native_offset = scroll_range * percent * 0.01
        accumulated = native_offset * FULL_PERCENT / scroll_range
    return (
        ("503", 503, 503 * FULL_PERCENT / scroll_range),
        ("504-lower", 504, math.nextafter(direct, -math.inf)),
        ("504-direct", 504, direct),
        ("504-upper", 504, math.nextafter(direct, math.inf)),
        ("505", 505, 505 * FULL_PERCENT / scroll_range),
        ("630-direct", 630, 630 * FULL_PERCENT / scroll_range),
        ("630-accumulated", 630, percent),
    )


def compare_overlap(
    old: ScrollFrame, new: ScrollFrame, displacement: int
) -> dict[str, object]:
    """Describe every changed pixel in a predicted overlap, without accepting it."""
    if (old.width, old.height) != (new.width, new.height):
        raise ValueError("Diagnostic frame dimensions changed")
    if not 0 <= displacement < old.height:
        raise ValueError("Diagnostic frames do not overlap")
    stride = old.width * 3
    changed_rows = []
    for y in range(new.height - displacement):
        a = old.rgb[(y + displacement) * stride : (y + displacement + 1) * stride]
        b = new.rgb[y * stride : (y + 1) * stride]
        if a == b:
            continue
        columns = [
            x for x in range(new.width) if a[x * 3 : x * 3 + 3] != b[x * 3 : x * 3 + 3]
        ]
        differences = [abs(left - right) for left, right in zip(a, b, strict=True)]
        changed_rows.append(
            {
                "old_y": y + displacement,
                "new_y": y,
                "changed_pixels": len(columns),
                "changed_channels": sum(value != 0 for value in differences),
                "max_channel_delta": max(differences),
                "changed_columns": columns,
            }
        )
    stitcher = ScrollStitcher()
    stitcher.append(old)
    try:
        step = stitcher.append(new)
    except StitchError as error:
        gate = {"accepted": False, "error": str(error)}
    else:
        gate = {"accepted": True, "measured_rows": step.new_rows}
    return {
        "predicted_displacement": displacement,
        "overlap_rows": old.height - displacement,
        "changed_rows": changed_rows,
        "unchanged_stitcher_gate": gate,
        "zero_displacement_control": displacement == 0,
    }


class ClippingDiagnostic(FullSweep):
    """Run a predetermined sequence with the existing bounds and ownership guard."""

    @override
    def run(self) -> None:
        """Collect all fixed observations, compare, and verify exact restoration."""
        probe = self.probe
        self.check()
        initial = probe.initial
        if initial is None or not 0 < initial.vertical_view < FULL_PERCENT:
            raise RuntimeError("Invalid diagnostic initial state")
        height, scroll_range, _ = self.guidance(initial.vertical_view)
        if height * (self.viewport[2] - self.viewport[0]) > MAX_DIAGNOSTIC_PIXELS:
            raise RuntimeError("Diagnostic retained-frame pixel budget exceeded")
        if scroll_range <= REFERENCE_OFFSET:
            raise RuntimeError("Diagnostic offsets exceed native scroll range")
        frames = {}
        observations = []
        probe.report["clipping_observations"] = observations
        plan = requests(scroll_range)
        for name, offset, percent in plan:
            # Reset each observation so sub-ULP neighbors do not require a tiny
            # movement to be acknowledged from an almost-identical prior state.
            self.capture(0.0, f"reset-{name}")
            observation = {
                "name": name,
                "target_offset": offset,
                "requested_percent": percent,
                "requested_percent_hex": float(percent).hex(),
                "predicted_wpf_offset": scroll_range * percent * 0.01,
            }
            probe.emit({"event": "clipping_request", **observation})
            frame = self.capture(percent, f"offset-{name}")
            frames[name] = self.content(frame)
            observation["observed_state"] = (
                asdict(probe.last) if probe.last is not None else None
            )
            observations.append(observation)
            probe.emit({"event": "clipping_observation", **observation})
        comparisons = []
        for name, offset, _ in plan:
            result = compare_overlap(
                frames[name], frames["630-direct"], REFERENCE_OFFSET - offset
            )
            result.update(
                name=name, old_offset=offset, reference_offset=REFERENCE_OFFSET
            )
            comparisons.append(result)
            probe.emit({"event": "clipping_comparison", **result})
        probe.report["clipping_comparisons"] = comparisons
        if (
            self.capture(initial.vertical, "restore") != self.before
            or probe.last != initial
        ):
            raise RuntimeError("Diagnostic restoration changed")
        probe.report["restored"] = True
        probe.report["clipping_diagnostic_completed"] = True
        probe.emit({"event": "clipping_diagnostic_completed", "frames": self.frames})
