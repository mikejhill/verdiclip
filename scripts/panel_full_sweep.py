"""Opt-in supervised WPF experiment, not a product capture implementation."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import mss.tools
from scripts.panel_viewport import viewport_rect

from verdiclip.capture.scroll_stitch import (
    MAX_FRAMES,
    MAX_OUTPUT_PIXELS,
    MAX_OUTPUT_ROWS,
    ScrollFrame,
    ScrollStitcher,
)

if TYPE_CHECKING:
    import mss
    from scripts.panel_uia_probe import Probe

MAX_SECONDS = 90
ROUNDING_TOLERANCE = 1.0  # Candidate rejection gate, not mixed-DPI qualification.
FULL_PERCENT = 100.0


class FullSweep:
    """Reuse the probe's guarded operations and admit output only after replay."""

    def __init__(
        self,
        probe: Probe,
        source: mss.MSS,
        viewport: tuple[int, int, int, int],
        before: ScrollFrame,
        started: float,
    ) -> None:
        self.probe, self.source = probe, source
        self.viewport, self.before, self.started = viewport, before, started
        probe.full_sweep = self
        self.frames = 1  # The initial full-UIA frame also consumes the budget.

    def check_deadline(self) -> None:
        """Latch expiration even after a slow native validation call."""
        if time.monotonic() - self.started >= MAX_SECONDS:
            self.probe.report["full_sweep_deadline_exceeded"] = True
            self.probe.report["restoration_prohibited"] = True
            raise RuntimeError("Full sweep deadline exceeded")

    def check(self) -> None:
        """Bound the experiment and recheck native-derived content geometry."""
        self.check_deadline()
        probe = self.probe
        probe.validate()
        if probe.identity is None or probe.uia is None:
            raise RuntimeError("Probe not initialized")
        if probe.identity.framework != "WPF":
            raise RuntimeError("Full sweep experiment requires qualified WPF geometry")
        try:
            observed = viewport_rect(probe.identity, probe.uia.inspect_children())
        except Exception:
            probe.report["restoration_prohibited"] = True
            raise
        if observed != self.viewport:
            probe.report["restoration_prohibited"] = True
            raise RuntimeError("Native viewport changed")
        probe.validate()
        self.check_deadline()

    def check_frame_budget(self) -> None:
        """Include fallback restoration in the same frame budget."""
        if self.frames >= MAX_FRAMES:
            self.probe.report["restoration_prohibited"] = True
            raise RuntimeError("Full sweep frame limit exceeded")

    def before_write(self) -> None:
        """Revalidate geometry and deadline immediately before every write."""
        if self.probe.report.get("restoration_prohibited"):
            raise RuntimeError("Full sweep further writes prohibited")
        self.check()
        self.check_frame_budget()
        self.check_deadline()

    def begin_frame(self) -> None:
        """Charge all frame attempts, including a fallback restoration frame."""
        self.check_frame_budget()
        self.check_deadline()
        self.frames += 1

    def capture(self, percent: float, name: str) -> ScrollFrame:
        """Check bounds before every scroll and retain full rectangle evidence."""
        self.check()
        self.check_frame_budget()
        self.probe.seek(percent)
        self.check()
        frame = self.probe.frame(self.source, name)
        self.check()
        return frame

    def content(self, frame: ScrollFrame) -> ScrollFrame:
        """Remove only native UIA scrollbar edge strips, never fixture borders."""
        identity = self.probe.identity
        if identity is None:
            raise RuntimeError("Probe not initialized")
        left, top, right, bottom = identity.rect
        a, b, d, e = self.viewport
        if not (
            left == a < d <= right
            and top == b < e <= bottom
            and (frame.width, frame.height) == (right - left, bottom - top)
        ):
            raise RuntimeError("Invalid native viewport or full frame")
        width, height = d - a, e - b
        stride = frame.width * 3
        return ScrollFrame(
            width,
            height,
            b"".join(
                frame.rgb[row * stride : row * stride + width * 3]
                for row in range(height)
            ),
        )

    def guidance(self, view_percent: float) -> tuple[int, float, int]:
        """Reject unbounded derived extents before the initial scroll write."""
        height = self.viewport[3] - self.viewport[1]
        scroll_range = height * (FULL_PERCENT - view_percent) / view_percent
        distance = height // 2
        if distance <= 0:
            raise RuntimeError("Native viewport is too short")
        width = self.viewport[2] - self.viewport[0]
        if (
            not math.isfinite(scroll_range)
            or scroll_range <= 0
            or height + scroll_range > MAX_OUTPUT_ROWS
            or width * (height + scroll_range) > MAX_OUTPUT_PIXELS
            or math.ceil(scroll_range / distance) + 5 > MAX_FRAMES
        ):
            self.probe.report["restoration_prohibited"] = True
            raise RuntimeError("Derived full sweep extent exceeds safety limit")
        return height, scroll_range, distance

    def _make_stitcher(self) -> ScrollStitcher:
        """Keep the strict stitcher unless an explicit fixture experiment overrides."""
        return ScrollStitcher()

    def run(self) -> None:
        """Sweep, verify endpoint and top, restore, then publish one composite."""
        probe = self.probe
        self.check()
        initial = probe.initial
        if initial is None or not 0 < initial.vertical_view < FULL_PERCENT:
            raise RuntimeError("Invalid initial scroll state")
        height, scroll_range, distance = self.guidance(initial.vertical_view)
        top = self.capture(0.0, "top")
        stitcher = self._make_stitcher()
        stitcher.append(self.content(top))
        position = 0.0
        bottom = top
        while position < FULL_PERCENT:
            requested = min(
                FULL_PERCENT, position + distance * FULL_PERCENT / scroll_range
            )
            bottom = self.capture(requested, f"sweep-{self.frames:03d}")
            current = probe.last
            if current is None or not position < current.vertical <= FULL_PERCENT:
                raise RuntimeError("Full sweep made no provider progress")
            step = stitcher.append(self.content(bottom))
            implied = (current.vertical - position) * scroll_range / FULL_PERCENT
            if (
                step.new_rows > distance
                or abs(implied - step.new_rows) > ROUNDING_TOLERANCE
            ):
                raise RuntimeError(
                    "Provider displacement disagrees with stitched pixels"
                )
            probe.emit(
                {
                    "event": "sweep_step",
                    "implied_rows": implied,
                    "measured_rows": step.new_rows,
                    "percent": current.vertical,
                }
            )
            position = current.vertical
        endpoint = probe.last
        if (
            self.capture(FULL_PERCENT, "bottom-repeat") != bottom
            or probe.last != endpoint
        ):
            raise RuntimeError("Full sweep endpoint changed")
        if self.capture(0.0, "top-repeat") != top:
            raise RuntimeError("Full sweep top replay changed")
        if (
            self.capture(initial.vertical, "restore") != self.before
            or probe.last != initial
        ):
            raise RuntimeError("Full sweep restoration changed")
        probe.report["restored"] = True
        image = stitcher.finish()
        if abs(image.height - (height + scroll_range)) > ROUNDING_TOLERANCE:
            raise RuntimeError("Full sweep output extent disagrees with provider")
        self.check()
        pending = probe.output / "stitched.pending.png"
        mss.tools.to_png(image.rgb, (image.width, image.height), output=str(pending))
        self.check()
        pending.replace(probe.output / "stitched.png")
        probe.report["full_sweep_passed"] = True
        probe.report["full_sweep_frames"] = self.frames
        probe.emit(
            {
                "event": "full_sweep_passed",
                "frames": self.frames,
                "width": image.width,
                "height": image.height,
            }
        )
