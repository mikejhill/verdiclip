"""Supervised Windows-only UIA feasibility probe; never product capture code.

Run only against an owned fixture with a hard-timeout process supervisor. Output
must be a new directory. No write retries, automatic fallback or GUI.
Full stitching is opt-in.
Explicit discovery settling permits one guarded read-only delayed observation.
"""

from __future__ import annotations

import argparse
import ctypes as c
import json
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import mss
import mss.tools
from scripts.panel_browser_sequence import BrowserSequence
from scripts.panel_clipping_diagnostic import ClippingDiagnostic, validate_fixture
from scripts.panel_full_sweep import FullSweep
from scripts.panel_msaa_probe import probe_msaa
from scripts.panel_seam_candidate import SeamSweep
from scripts.panel_uia import MAX_ANCESTORS, PanelUia, Point, ScrollState, Structure
from scripts.panel_viewport import midpoint_percent, viewport_rect

from verdiclip.capture.scroll_stitch import ScrollFrame

MAX_PIXELS = 16_000_000
SETTLE_SECONDS = 2
STABLE_SAMPLES = 3
DISCOVERY_DELAY_SECONDS = 1.0
DISCOVERY_GUARD_INTERVAL_SECONDS = 0.05
FULL_PERCENT = 100


class LastInput(c.Structure):
    """SDK LASTINPUTINFO."""

    _fields_ = [("size", c.c_uint32), ("tick", c.c_uint32)]


class NativeGuard:
    """Latch input/foreground/root changes; never reactivate the fixture."""

    def __init__(self, root: int, x: int, y: int) -> None:
        self.user = c.WinDLL("user32", use_last_error=True)
        self.user.SetProcessDpiAwarenessContext.argtypes = [c.c_void_p]
        self.user.SetProcessDpiAwarenessContext.restype = c.c_int32
        self.user.GetThreadDpiAwarenessContext.restype = c.c_void_p
        self.user.AreDpiAwarenessContextsEqual.argtypes = [c.c_void_p, c.c_void_p]
        self.user.AreDpiAwarenessContextsEqual.restype = c.c_int32
        self.user.SetProcessDpiAwarenessContext(c.c_void_p(-4))
        if not self.user.AreDpiAwarenessContextsEqual(
            self.user.GetThreadDpiAwarenessContext(), c.c_void_p(-4)
        ):
            raise OSError("Per-monitor-v2 DPI awareness not established")
        self.user.GetLastInputInfo.argtypes = [c.POINTER(LastInput)]
        self.user.GetLastInputInfo.restype = c.c_int32
        self.user.GetForegroundWindow.restype = c.c_void_p
        self.user.WindowFromPoint.argtypes = [Point]
        self.user.WindowFromPoint.restype = c.c_void_p
        self.user.GetAncestor.argtypes = [c.c_void_p, c.c_uint32]
        self.user.GetAncestor.restype = c.c_void_p
        self.root, self.point = root, Point(x, y)
        self.tick = self.input_tick()
        self.lost = False
        self.check()

    def input_tick(self) -> int:
        """Read the native 32-bit input epoch without synthesis."""
        value = LastInput(c.sizeof(LastInput), 0)
        if not self.user.GetLastInputInfo(c.byref(value)):
            raise OSError("GetLastInputInfo failed")
        return int(value.tick)

    def check(self) -> None:
        """Fail permanently on evidence of interference."""
        hit = self.user.WindowFromPoint(self.point)
        if (
            self.input_tick() != self.tick
            or self.user.GetForegroundWindow() != self.root
            or self.user.GetAncestor(hit, 2) != self.root
        ):
            self.lost = True
        if self.lost:
            raise RuntimeError("Input, foreground or point ownership changed")


class Probe:
    """Small fail-closed qualification sequence with preserved first failure."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output = Path(args.output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.log = (self.output / "calls.jsonl").open("x", encoding="utf-8")
        self.report: dict[str, object] = {
            "experimental": True,
            "producer_pid": os.getpid(),
            "parent_pid": os.getppid(),
            "executable": sys.executable,
            "restored": False,
            "first_failure": None,
        }
        self.emit({"event": "process", **self.report})
        self.uncertain = False
        self.full_sweep: FullSweep | None = None
        self.guard: NativeGuard | None = None
        self.uia: PanelUia | None = None
        self.identity: Structure | None = None
        self.initial: ScrollState | None = None
        self.last: ScrollState | None = None

    def emit(self, event: dict[str, object]) -> None:
        """Flush each event so killed/hung helpers retain their first evidence."""
        self.log.write(json.dumps({"monotonic": time.monotonic(), **event}) + "\n")
        self.log.flush()

    def validate(self) -> ScrollState:
        """Check ownership and unchanged geometry/extent/acknowledged state."""
        if self.guard is None or self.uia is None or self.uia.target is None:
            raise RuntimeError("Probe not initialized")
        self.guard.check()
        identity = self.uia.structure(self.uia.target)
        state = self.uia.read_state()
        self.guard.check()
        if identity != self.identity:
            raise RuntimeError("Target structure changed")
        if self.last is not None and state != self.last:
            raise RuntimeError(
                "Provider moved or extent changed outside last acknowledgement"
            )
        return state

    def seek(self, percent: float) -> None:
        """One scroll, no retries; uncertain outcomes prohibit restoration."""
        self.validate()
        if self.uia is None or self.guard is None or self.initial is None:
            raise RuntimeError("Probe not initialized")
        # Chromium's SetScrollPercent computes both offsets even for NoScroll.
        # Preserve the already-validated horizontal value, without a second write.
        # https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/accessibility/platform/ax_platform_node_win.cc
        horizontal = (
            self.initial.horizontal if self.initial.horizontal_scrollable else -1.0
        )
        sweep = getattr(self, "full_sweep", None)
        if getattr(self, "report", {}).get("restoration_prohibited"):
            raise RuntimeError("Further scroll writes prohibited")
        if sweep is not None:
            sweep.before_write()
        self.uncertain = True
        self.uia.set_vertical(percent, horizontal=horizontal)
        self.guard.check()
        current = self.observe_acknowledgement(percent)
        self.last = current
        self.uncertain = False
        self.validate()
        self.emit({"event": "acknowledged", "state": asdict(current)})

    def observe_acknowledgement(self, percent: float) -> ScrollState:
        """Wait for stable readback, without ever repeating the write."""
        if (
            self.uia is None
            or self.uia.target is None
            or self.guard is None
            or self.initial is None
            or self.last is None
        ):
            raise RuntimeError("Probe not initialized")
        start = time.monotonic()
        previous = None
        matched = 0
        while time.monotonic() - start < SETTLE_SECONDS:
            self.guard.check()
            identity = self.uia.structure(self.uia.target)
            current = self.uia.read_state()
            self.guard.check()
            self.emit(
                {
                    "event": "scroll_readback",
                    "requested": percent,
                    "observed": asdict(current),
                }
            )
            if identity != self.identity:
                raise RuntimeError("Target structure changed during scroll")
            if replace(current, vertical=self.initial.vertical) != self.initial:
                raise RuntimeError("Scroll extent/horizontal state changed")
            if not 0 <= current.vertical <= FULL_PERCENT:
                raise RuntimeError("Scroll percentage is out of range")
            matched = matched + 1 if current == previous else 1
            previous = current
            eligible = current.vertical == percent
            if percent not in (0, FULL_PERCENT, self.initial.vertical):
                eligible = self.last.vertical < current.vertical < FULL_PERCENT
            if time.monotonic() - start >= SETTLE_SECONDS:
                break
            if eligible and matched >= STABLE_SAMPLES:
                return current
            time.sleep(0.1)
        raise RuntimeError("Scroll acknowledgement did not settle within deadline")

    def frame(self, source: mss.MSS, name: str) -> ScrollFrame:
        """Require three equal physical-pixel samples within two seconds."""
        if self.identity is None:
            raise RuntimeError("Probe not initialized")
        sweep = getattr(self, "full_sweep", None)
        if sweep is not None:
            sweep.begin_frame()
        left, top, right, bottom = self.identity.rect
        area = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        previous = None
        matched = 0
        start = time.monotonic()
        while time.monotonic() - start < SETTLE_SECONDS:
            self.validate()
            shot = source.grab(area)
            self.validate()
            if time.monotonic() - start >= SETTLE_SECONDS:
                self.report["restoration_prohibited"] = True
                raise RuntimeError(f"Frame deadline exceeded for {name}")
            pixels = shot.rgb
            matched = matched + 1 if previous == pixels else 1
            previous = pixels
            if matched == STABLE_SAMPLES:
                mss.tools.to_png(
                    pixels, shot.size, output=str(self.output / f"{name}.png")
                )
                self.emit(
                    {
                        "event": "stable_frame",
                        "name": name,
                        "seconds": time.monotonic() - start,
                        "samples": matched,
                        "state": asdict(self.last) if self.last is not None else None,
                    }
                )
                return ScrollFrame(shot.size[0], shot.size[1], bytes(pixels))
            time.sleep(0.1)
        self.report["restoration_prohibited"] = True
        raise RuntimeError(f"Pixels did not settle for {name}")

    def check_rect(self, fixture: dict[str, list[int]]) -> None:
        """Admit exact fixture geometry or explicit owned-root bounds."""
        if self.identity is None:
            raise RuntimeError("Probe not initialized")
        if "rect" in fixture and list(self.identity.rect) != fixture["rect"]:
            raise RuntimeError("Target rectangle differs from owned fixture")
        left, top, right, bottom = self.identity.rect
        if "rect" not in fixture:
            a, b, d, e = fixture["allowed_rect"]
            if not (a <= left < right <= d and b <= top < bottom <= e):
                raise RuntimeError("Target lies outside owned fixture allowed bounds")
            self.report["viewport_verified"] = False
        if not (
            left <= self.args.x < right
            and top <= self.args.y < bottom
            and 0 < (right - left) * (bottom - top) <= MAX_PIXELS
        ):
            raise RuntimeError("Invalid/oversized fixture rectangle or point")

    def discovery_observation(self, name: str) -> Structure:
        """Retain each cold/delayed read, including failures before selection."""
        if self.uia is None or self.guard is None:
            raise RuntimeError("Probe not initialized")
        self.guard.check()
        observation: dict[str, object] = {
            "name": name,
            "monotonic": time.monotonic(),
        }
        self.report[f"discovery_{name}"] = observation
        self.emit({"event": "discovery_start", **observation})
        try:
            target = self.uia.discover(self.args.x, self.args.y)
            self.guard.check()
            observation["target"] = asdict(target)
        except Exception as error:
            observation["error"] = repr(error)
            raise
        else:
            return target
        finally:
            observation["ancestors"] = [asdict(item) for item in self.uia.ancestors]
            self.emit({"event": "discovery_observation", **observation})

    def discover_target(self) -> Structure:
        """Optionally settle one normal cold miss; never retry native errors."""
        if self.uia is None or self.guard is None:
            raise RuntimeError("Probe not initialized")
        if not self.args.settle_discovery:
            return self.uia.discover(self.args.x, self.args.y)
        try:
            return self.discovery_observation("cold")
        except OSError as error:
            count = len(self.uia.ancestors)
            if not (
                self.uia.target is None
                and 1 <= count <= MAX_ANCESTORS
                and str(error)
                == (
                    "No vertical ScrollPattern: ancestor chain ended after "
                    f"{count} nodes"
                )
            ):
                raise
        # No scroll has occurred. Preserve the same client, root and input epoch.
        deadline = time.monotonic() + DISCOVERY_DELAY_SECONDS
        while True:
            self.guard.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(DISCOVERY_GUARD_INTERVAL_SECONDS, remaining))
        self.uia.ancestors.clear()
        return self.discovery_observation("delayed")

    def execute(self) -> None:
        """Validate explicit owned-fixture identity before the first write."""
        started = time.monotonic()
        fixture = json.loads(
            Path(self.args.fixture_manifest).read_text(encoding="utf-8")
        )
        self.guard = NativeGuard(int(fixture["root_hwnd"]), self.args.x, self.args.y)
        user = self.guard.user
        user.GetWindowThreadProcessId.argtypes = [c.c_void_p, c.POINTER(c.c_uint32)]
        user.GetWindowThreadProcessId.restype = c.c_uint32
        root_pid = c.c_uint32()
        if not user.GetWindowThreadProcessId(fixture["root_hwnd"], c.byref(root_pid)):
            raise RuntimeError("Cannot identify fixture root process")
        if root_pid.value != self.args.expected_pid:
            raise RuntimeError("Root PID differs from expected owned fixture")
        self.uia = PanelUia(self.emit)
        self.identity = self.discover_target()
        self.guard.check()
        self.report["target"] = asdict(self.identity)
        self.emit({"event": "discovered_target", **asdict(self.identity)})
        self.report["ancestors"] = [asdict(item) for item in self.uia.ancestors]
        children = self.uia.inspect_children()
        self.report["children"] = [asdict(item) for item in children]
        viewport = viewport_rect(self.identity, children)
        self.report["derived_viewport"] = viewport
        if (
            self.identity.pid
            not in fixture.get("owned_process_ids", [self.args.expected_pid])
            or self.identity.offscreen
        ):
            raise RuntimeError("Target PID/visibility differs from owned fixture")
        self.check_rect(fixture)
        self.initial = self.last = self.uia.read_state()
        self.report["initial"] = asdict(self.initial)
        if (
            not 0 <= self.initial.vertical <= FULL_PERCENT
            or not self.initial.vertical_scrollable
            or not 0 < self.initial.vertical_view < FULL_PERCENT
        ):
            raise RuntimeError("Invalid initial vertical state")
        self.validate()
        with mss.MSS() as source:
            left, top, right, bottom = self.identity.rect
            if not any(
                left >= item["left"]
                and top >= item["top"]
                and right <= item["left"] + item["width"]
                and bottom <= item["top"] + item["height"]
                for item in source.monitors[1:]
            ):
                raise RuntimeError("Fixture not wholly on one monitor")
            before = self.frame(source, "before")
            self.run_selected_experiment(fixture, source, before, viewport, started)

    def run_selected_experiment(
        self,
        fixture: dict[str, Any],
        source: mss.MSS,
        before: ScrollFrame,
        viewport: tuple[int, int, int, int],
        started: float,
    ) -> None:
        """Keep independent experiments explicit; never fallback between them."""
        if self.initial is None or self.identity is None:
            raise RuntimeError("Experiment requires acknowledged initial state")
        if getattr(self.args, "browser_sequence", False):
            BrowserSequence(self, source, self.identity.rect, before, started).run()
        elif getattr(self.args, "msaa_diagnostic", False):
            self.read_msaa(fixture, source, before)
        elif getattr(self.args, "seam_sweep", False):
            self.check_viewport_point(viewport)
            SeamSweep(self, source, viewport, before, started).run()
        elif getattr(self.args, "clipping_diagnostic", False):
            validate_fixture(fixture)
            self.check_viewport_point(viewport)
            ClippingDiagnostic(self, source, viewport, before, started).run()
        elif getattr(self.args, "full_sweep", False):
            self.check_viewport_point(viewport)
            FullSweep(self, source, viewport, before, started).run()
        elif not self.args.inspect_only:
            mid = midpoint_percent(
                self.initial.vertical_view, viewport[3] - viewport[1]
            )
            for name, percent in (("top", 0.0), ("mid", mid), ("bottom", 100.0)):
                self.seek(percent)
                self.frame(source, name)
            self.seek(self.initial.vertical)
            self.frame(source, "restore")
            self.report["restored"] = True

    def read_msaa(
        self, fixture: dict[str, Any], source: mss.MSS, before: ScrollFrame
    ) -> None:
        """Inspect only the owned browser; never scroll, including on failure."""
        if fixture.get("kind") != "owned-edge" or self.identity is None:
            raise RuntimeError("MSAA diagnostic requires the owned browser")
        self.report["restoration_prohibited"] = True

        def guard() -> None:
            self.validate()

        self.report["msaa"] = probe_msaa(
            self.identity.rect,
            frozenset(fixture["owned_process_ids"]),
            int(fixture["root_hwnd"]),
            guard,
            self.emit,
        )
        after = self.frame(source, "after")
        self.validate()
        self.report["read_only_unchanged"] = (
            after == before and self.last == self.initial
        )
        if not self.report["msaa"]["diagnostic_completed"]:
            raise RuntimeError("MSAA diagnostic stopped; inspect first provider error")
        if not self.report["read_only_unchanged"]:
            raise RuntimeError("Read-only MSAA diagnostic observed changed state")
        self.report["msaa_diagnostic_completed"] = True

    def check_viewport_point(self, viewport: tuple[int, int, int, int]) -> None:
        """Do not admit a scrollbar click to either guarded WPF experiment."""
        if not (
            viewport[0] <= self.args.x < viewport[2]
            and viewport[1] <= self.args.y < viewport[3]
        ):
            raise RuntimeError("Selected point lies outside native viewport")

    def run(self) -> int:
        """Preserve first failure; restore only when every guard still agrees."""
        try:
            self.execute()
        except Exception as error:  # noqa: BLE001 - terminal diagnostic boundary
            if self.uia is not None:
                self.report["ancestors"] = [asdict(item) for item in self.uia.ancestors]
            self.report["first_failure"] = repr(error)
            self.emit({"event": "first_failure", "error": repr(error)})
            self.restore_after_failure()
        finally:
            (self.output / "manifest.json").write_text(
                json.dumps(self.report, indent=2), encoding="utf-8"
            )
            if self.uia is not None:
                self.uia.close()
            self.log.close()
        return int(self.report["first_failure"] is not None)

    def restore_after_failure(self) -> None:
        """Do not overwrite first failure with a secondary restoration failure."""
        if (
            getattr(getattr(self, "args", None), "msaa_diagnostic", False)
            or self.uncertain
            or self.report.get("full_sweep_deadline_exceeded")
            or self.report.get("restoration_prohibited")
            or self.initial is None
            or self.last == self.initial
        ):
            return
        try:
            self.validate()
            self.seek(self.initial.vertical)
            with mss.MSS() as source:
                self.frame(source, "restore")
            self.report["restored"] = True
        except Exception as error:  # noqa: BLE001 - terminal diagnostic boundary
            self.report["restore_failure"] = repr(error)


def main() -> int:
    """Parse explicit fixture authorization and start the isolated probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--fixture-manifest", required=True)
    parser.add_argument("--output", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--full-sweep", action="store_true")
    mode.add_argument("--clipping-diagnostic", action="store_true")
    mode.add_argument("--msaa-diagnostic", action="store_true")
    mode.add_argument("--seam-sweep", action="store_true")
    mode.add_argument("--browser-sequence", action="store_true")
    parser.add_argument("--settle-discovery", action="store_true")
    return Probe(parser.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
