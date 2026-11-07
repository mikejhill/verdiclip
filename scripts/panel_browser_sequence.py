"""Owned-browser raw evidence only; no crop discovery or stitch admission.

Caller must verify fixture ownership and run under the existing 30-second process
supervisor and 3-second native-call watchdog. This module has no standalone CLI.
The inherited 90-second/128-frame limits also apply to fallback restoration.
"""

from __future__ import annotations

import math
from dataclasses import asdict, replace
from typing import override

from scripts.panel_full_sweep import FULL_PERCENT, FullSweep

from verdiclip.capture.scroll_stitch import MAX_FRAMES, ScrollFrame

CONTROL_AND_RESERVE_FRAMES = 6  # Initial, top, three replays, fallback restoration.


def sequence_offsets(height: int, scroll_range: float) -> tuple[float, ...]:
    """Preflight fixed quarter, half, then half-view offsets before any write."""
    quarter, half = height // 4, height // 2
    if quarter <= 0 or not math.isfinite(scroll_range) or scroll_range <= 0:
        raise RuntimeError("Invalid browser sequence extent")
    count = math.ceil(scroll_range / half) + 1
    if count > MAX_FRAMES:
        raise RuntimeError("Browser sequence frame limit exceeded")
    offsets = [min(float(quarter), scroll_range)]
    for index in range(1, count):
        offset = min(float(index * half), scroll_range)
        if offset > offsets[-1]:
            offsets.append(offset)
        if offset == scroll_range:
            break
    if len(offsets) + CONTROL_AND_RESERVE_FRAMES > MAX_FRAMES:
        raise RuntimeError("Browser sequence frame limit exceeded")
    return tuple(offsets)


class BrowserSequence(FullSweep):
    """Collect full outer Chrome frames; success means controls, not stitching."""

    @override
    def check(self) -> None:
        """Validate the unchanged outer provider rectangle without child crops."""
        self.check_deadline()
        probe = self.probe
        try:
            state = probe.validate()
        except Exception:
            probe.report["restoration_prohibited"] = True
            raise
        self.check_deadline()
        identity = probe.identity
        if identity is None or probe.uia is None or probe.initial is None:
            raise RuntimeError("Probe not initialized")
        if (
            identity.framework != "Chrome"
            or identity.offscreen
            or identity.rect != self.viewport
        ):
            probe.report["restoration_prohibited"] = True
            raise RuntimeError("Browser sequence outer Chrome geometry changed")
        left, top, right, bottom = self.viewport
        if right <= left or bottom <= top:
            raise RuntimeError("Invalid browser sequence outer rectangle")
        if replace(state, vertical=probe.initial.vertical) != probe.initial:
            probe.report["restoration_prohibited"] = True
            raise RuntimeError("Browser sequence extent or horizontal state changed")

    @override
    def capture(self, percent: float, name: str) -> ScrollFrame:
        """Retain original full-width pixels and observed provider state."""
        self.probe.emit(
            {"event": "browser_sequence_request", "name": name, "percent": percent}
        )
        frame = super().capture(percent, name)
        width = self.viewport[2] - self.viewport[0]
        height = self.viewport[3] - self.viewport[1]
        if (frame.width, frame.height) != (width, height):
            raise RuntimeError("Browser sequence frame is not the full outer rectangle")
        if self.probe.last is None:
            raise RuntimeError("Browser sequence missing observed state")
        self.probe.emit(
            {
                "event": "browser_sequence_observation",
                "name": name,
                "requested_percent": percent,
                "state": asdict(self.probe.last),
                "rect": self.viewport,
                "width": width,
                "height": height,
            }
        )
        return frame

    def _controls(self, top: ScrollFrame, bottom: ScrollFrame) -> None:
        """Require stable bottom, exact top replay and exact initial restoration."""
        probe = self.probe
        initial = probe.initial
        if initial is None:
            raise RuntimeError("Probe not initialized")
        endpoint = probe.last
        repeated = self.capture(FULL_PERCENT, "bottom-repeat")
        if repeated != bottom or probe.last != endpoint:
            raise RuntimeError("Browser sequence endpoint changed")
        repeated = self.capture(0.0, "top-repeat")
        if repeated != top or probe.last != replace(initial, vertical=0.0):
            raise RuntimeError("Browser sequence top replay changed")
        restored = self.capture(initial.vertical, "restore")
        if restored != self.before or probe.last != initial:
            raise RuntimeError("Browser sequence restoration changed")
        probe.report["restored"] = True

    @override
    def run(self) -> None:
        """Collect bounded overlapping evidence and publish no stitched output."""
        probe = self.probe
        self.check()
        initial = probe.initial
        if initial is None or not 0 < initial.vertical_view < FULL_PERCENT:
            raise RuntimeError("Invalid browser sequence initial state")
        height, scroll_range, _ = self.guidance(initial.vertical_view)
        offsets = sequence_offsets(height, scroll_range)
        if (self.before.width, self.before.height) != (
            self.viewport[2] - self.viewport[0],
            height,
        ):
            raise RuntimeError("Browser sequence initial frame is not full outer")
        probe.emit(
            {
                "event": "browser_sequence_plan",
                "scroll_range": scroll_range,
                "offsets": offsets,
                "rect": self.viewport,
                "initial_state": asdict(initial),
            }
        )
        top = self.capture(0.0, "top")
        if probe.last != replace(initial, vertical=0.0):
            raise RuntimeError("Browser sequence top state changed")
        position = 0.0
        bottom = top
        for index, offset in enumerate(offsets):
            percent = (
                FULL_PERCENT
                if offset == scroll_range
                else offset * FULL_PERCENT / scroll_range
            )
            bottom = self.capture(percent, f"sequence-{index:03d}")
            current = probe.last
            if current is None or not position < current.vertical <= FULL_PERCENT:
                raise RuntimeError("Browser sequence made no provider progress")
            position = current.vertical
        if position != FULL_PERCENT:
            raise RuntimeError("Browser sequence did not reach bottom")
        self._controls(top, bottom)
        self.check()
        if probe.uncertain or probe.report.get("restoration_prohibited"):
            raise RuntimeError("Browser sequence is not cleanup-ready")
        probe.report["browser_sequence_completed"] = True
        probe.report["browser_sequence_frames"] = self.frames
        probe.emit({"event": "browser_sequence_completed", "frames": self.frames})
