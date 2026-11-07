"""Supervised Windows-only UIA feasibility probe; never product capture code.

Run only against an owned fixture with a hard-timeout process supervisor. Output
must be a new directory. No retries, stitching, automatic fallback or GUI.
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

import mss
import mss.tools
from scripts.panel_uia import PanelUia, Point, ScrollState, Structure

MAX_PIXELS = 16_000_000
SETTLE_SECONDS = 2
STABLE_SAMPLES = 3
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
        self.uncertain = True
        self.uia.set_vertical(percent)
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

    def frame(self, source: mss.MSS, name: str) -> None:
        """Require three equal physical-pixel samples within two seconds."""
        if self.identity is None:
            raise RuntimeError("Probe not initialized")
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
                return
            time.sleep(0.1)
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

    def execute(self) -> None:
        """Validate explicit owned-fixture identity before the first write."""
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
        self.identity = self.uia.discover(self.args.x, self.args.y)
        self.guard.check()
        self.report["target"] = asdict(self.identity)
        self.emit({"event": "discovered_target", **asdict(self.identity)})
        self.report["ancestors"] = [asdict(item) for item in self.uia.ancestors]
        self.report["children"] = [asdict(item) for item in self.uia.inspect_children()]
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
            self.frame(source, "before")
            if not self.args.inspect_only:
                mid = min(
                    50.0,
                    0.5
                    * self.initial.vertical_view
                    * FULL_PERCENT
                    / (FULL_PERCENT - self.initial.vertical_view),
                )
                for name, percent in (("top", 0.0), ("mid", mid), ("bottom", 100.0)):
                    self.seek(percent)
                    self.frame(source, name)
                self.seek(self.initial.vertical)
                self.frame(source, "restore")
                self.report["restored"] = True

    def run(self) -> int:
        """Preserve first failure; restore only when every guard still agrees."""
        try:
            self.execute()
        except Exception as error:  # noqa: BLE001 - terminal diagnostic boundary
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
        if self.uncertain or self.initial is None or self.last == self.initial:
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
    parser.add_argument("--inspect-only", action="store_true")
    return Probe(parser.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
