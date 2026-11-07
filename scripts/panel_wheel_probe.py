"""Owned-browser wheel-only diagnostic child, never a production fallback."""

from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path
from typing import Protocol, override

import mss
from scripts.panel_uia import PanelUia, Point, ScrollState
from scripts.panel_uia_probe import NativeGuard, Probe

FULL_PERCENT = 100
MIN_SAMPLES = 3
QUIET_SECONDS = 0.2
WHEEL = 120
MAX_WRITES = 40
CALL_SECONDS = 3
SESSION_SECONDS = 90
SETTLE_SECONDS = 2
MAX_CAPTURE_SAMPLES = 1024
_SIGNED_MIN = -(2**15)
_SIGNED_MAX = 2**15 - 1
_MODIFIERS = (0x01, 0x02, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12, 0x5B, 0x5C)


def wheel_parameters(x: int, y: int, delta: int) -> tuple[int, int]:
    """Reject truncation and restrict this experiment to single wheel notches."""
    if any(type(value) is not int for value in (x, y, delta)):
        raise ValueError("Wheel coordinates and delta must be integers")
    if not all(_SIGNED_MIN <= value <= _SIGNED_MAX for value in (x, y)):
        raise ValueError("Wheel point exceeds signed16 coordinate range")
    if delta not in (-WHEEL, WHEEL):
        raise ValueError("Only one signed wheel notch is allowed")
    return (delta & 0xFFFF) << 16, (x & 0xFFFF) | ((y & 0xFFFF) << 16)


class WheelApi(Protocol):
    """Small native boundary for deterministic rejection tests."""

    def check(self) -> None:
        """Verify input, foreground, selected HWND and modifiers."""
        ...

    def send(self, wparam: int, lparam: int) -> None:
        """Send once or raise with an uncertain outcome."""
        ...


class WheelWriter:
    """Latch uncertainty before each bounded native call, never retry it."""

    def __init__(self, api: WheelApi, x: int, y: int) -> None:
        """Validate coordinates before storing any native action capability."""
        wheel_parameters(x, y, WHEEL)
        self.api, self.x, self.y = api, x, y
        self.started = time.monotonic()
        self.writes = 0
        self.uncertain = False
        self.failed = False

    def step(self, delta: int) -> None:
        """Only settled acknowledgement can authorize a subsequent write."""
        parameters = wheel_parameters(self.x, self.y, delta)
        if self.failed or self.uncertain or self.writes >= MAX_WRITES:
            raise RuntimeError("Wheel writes prohibited")
        if time.monotonic() - self.started >= SESSION_SECONDS:
            raise RuntimeError("Wheel session deadline expired")
        self.api.check()
        if time.monotonic() - self.started >= SESSION_SECONDS:
            raise RuntimeError("Wheel session deadline expired before send")
        self.uncertain = True
        self.writes += 1
        started = time.monotonic()
        try:
            self.api.send(*parameters)
            self.api.check()
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Wheel send completed after deadline")  # noqa: TRY301 - latch failed operation
        except BaseException:
            self.failed = True
            raise

    def acknowledge(self) -> None:
        """Caller must first verify settled guarded native state and pixels."""
        if self.failed:
            raise RuntimeError("Wheel failed write cannot be acknowledged")
        self.api.check()
        if time.monotonic() - self.started >= SESSION_SECONDS:
            raise RuntimeError("Wheel session deadline expired")
        self.uncertain = False


class NativeWheel:
    """Target only the fixed owned HWND; no cursor movement or key synthesis."""

    def __init__(self, guard: NativeGuard, allowed_pids: list[int]) -> None:
        """Bind bounded native calls and freeze the selected window owner."""
        self.guard = guard
        self.user = guard.user
        self.user.GetAsyncKeyState.argtypes = [c.c_int32]
        self.user.GetAsyncKeyState.restype = c.c_int16
        self.user.GetWindowThreadProcessId.argtypes = [
            c.c_void_p,
            c.POINTER(c.c_uint32),
        ]
        self.user.GetWindowThreadProcessId.restype = c.c_uint32
        self.user.SendMessageTimeoutW.argtypes = [
            c.c_void_p,
            c.c_uint32,
            c.c_size_t,
            c.c_ssize_t,
            c.c_uint32,
            c.c_uint32,
            c.POINTER(c.c_size_t),
        ]
        self.user.SendMessageTimeoutW.restype = c.c_ssize_t
        self.user.GetCursorPos.argtypes = [c.POINTER(Point)]
        self.user.GetCursorPos.restype = c.c_int32
        self.cursor = self.cursor_position()
        self.hwnd = self.user.WindowFromPoint(guard.point)
        pid = c.c_uint32()
        if (
            not self.user.GetWindowThreadProcessId(self.hwnd, c.byref(pid))
            or pid.value not in allowed_pids
        ):
            raise RuntimeError("Wheel HWND is not owned by fixture")
        self.pid = pid.value
        self.check()

    def cursor_position(self) -> tuple[int, int]:
        """Read the real cursor without repositioning it."""
        point = Point()
        if not self.user.GetCursorPos(c.byref(point)):
            raise OSError("Cannot read native cursor position")
        return int(point.x), int(point.y)

    def check(self) -> None:
        """Reject any target/input change or held button/modifier before sending."""
        self.guard.check()
        pid = c.c_uint32()
        if (
            self.cursor_position() != self.cursor
            or self.user.WindowFromPoint(self.guard.point) != self.hwnd
            or not self.user.GetWindowThreadProcessId(self.hwnd, c.byref(pid))
            or pid.value != self.pid
            or any(self.user.GetAsyncKeyState(key) & 0x8000 for key in _MODIFIERS)
        ):
            raise RuntimeError("Wheel target or modifier guard changed")

    def send(self, wparam: int, lparam: int) -> None:
        """Bound cross-process message handling; success still needs pixel evidence."""
        result = c.c_size_t()
        # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT; never NOTIMEOUTIFNOTHUNG.
        if not self.user.SendMessageTimeoutW(
            self.hwnd, 0x020A, wparam, lparam, 0x23, 1000, c.byref(result)
        ):
            raise OSError("Wheel SendMessageTimeout failed; outcome uncertain")


class WheelProbe(Probe):
    """Read UIA state but never invoke its scrolling write API."""

    def stable(self, source: mss.MSS) -> tuple[ScrollState, bytes]:
        """Observe three identical guarded state/pixel samples across200ms."""
        if (
            self.uia is None
            or self.uia.target is None
            or self.identity is None
            or self.initial is None
        ):
            raise RuntimeError("Wheel probe not initialized")
        previous = None
        matched = 0
        start = time.monotonic()
        while time.monotonic() - start < SETTLE_SECONDS:
            self.wheel.api.check()
            identity = self.uia.structure(self.uia.target)
            state = self.uia.read_state()
            if (
                identity != self.identity
                or replace(state, vertical=self.initial.vertical) != self.initial
            ):
                raise RuntimeError("Wheel changed target geometry or nonvertical state")
            if not 0 <= state.vertical <= FULL_PERCENT:
                raise RuntimeError("Invalid wheel readback")
            sample = getattr(self, "capture_samples", 0) + 1
            if sample > MAX_CAPTURE_SAMPLES:
                raise RuntimeError("Wheel capture evidence sample bound")
            self.capture_samples = sample
            self.emit(
                {
                    "event": "wheel_capture_sample",
                    "sample": sample,
                    "phase": "before",
                    "structure": asdict(identity),
                    "state": asdict(state),
                }
            )
            left, top, right, bottom = identity.rect
            self.emit({"event": "call_start", "name": "wheel.capture"})
            after_identity = None
            after_state = None
            try:
                pixels = source.grab(
                    {
                        "left": left,
                        "top": top,
                        "width": right - left,
                        "height": bottom - top,
                    }
                ).rgb
                self.wheel.api.check()
                after_identity = self.uia.structure(self.uia.target)
                # Preserve the original short circuit: a geometry/identity failure
                # must not cause another native state read merely for diagnostics.
                if after_identity == identity:
                    after_state = self.uia.read_state()
            finally:
                self.emit(
                    {
                        "event": "wheel_capture_sample",
                        "sample": sample,
                        "phase": "after",
                        "structure": (
                            asdict(after_identity)
                            if after_identity is not None
                            else None
                        ),
                        "state": asdict(after_state)
                        if after_state is not None
                        else None,
                    }
                )
            if after_identity != identity or after_state != state:
                raise RuntimeError("State changed across wheel capture")
            self.emit({"event": "call_end", "name": "wheel.capture"})
            if time.monotonic() - start >= SETTLE_SECONDS:
                break
            current = (state, pixels)
            matched = matched + 1 if current == previous else 1
            previous = current
            if matched >= MIN_SAMPLES and time.monotonic() - start >= QUIET_SECONDS:
                self.last = state
                self.wheel.acknowledge()
                self.emit(
                    {
                        "event": "wheel_observation",
                        "state": asdict(state),
                        "rgb_sha256": hashlib.sha256(pixels).hexdigest(),
                    }
                )
                return current
            time.sleep(0.1)
        raise RuntimeError("Wheel pixels/state did not settle")

    def move(self, source: mss.MSS, delta: int) -> tuple[ScrollState, bytes]:
        """Validate immediately before one wheel send and await readback afterward."""
        self.validate()
        self.emit({"event": "call_start", "name": "wheel.send", "delta": delta})
        self.wheel.step(delta)
        self.emit({"event": "call_end", "name": "wheel.send"})
        return self.stable(source)

    @override
    def execute(self) -> None:
        """One interior round trip followed by endpoint/propagation observation."""
        fixture = json.loads(
            Path(self.args.fixture_manifest).read_text(encoding="utf-8")
        )
        self.guard = NativeGuard(fixture["root_hwnd"], self.args.x, self.args.y)
        self.uia = PanelUia(self.emit)
        self.identity = self.discover_target()
        if (
            self.identity.pid not in fixture["owned_process_ids"]
            or self.identity.offscreen
        ):
            raise RuntimeError("Wheel target is not owned/visible")
        self.check_rect(fixture)
        self.initial = self.last = self.uia.read_state()
        if (
            not all(
                math.isfinite(value)
                for value in (
                    self.initial.vertical,
                    self.initial.horizontal,
                    self.initial.vertical_view,
                    self.initial.horizontal_view,
                )
            )
            or not self.initial.vertical_scrollable
            or not 0 < self.initial.vertical_view < FULL_PERCENT
        ):
            raise RuntimeError("Invalid wheel target scroll metrics")
        native = NativeWheel(self.guard, fixture["owned_process_ids"])
        self.wheel = WheelWriter(native, self.args.x, self.args.y)
        self.report["target"] = asdict(self.identity)
        self.report["wheel_hwnd"] = native.hwnd
        self.report["cursor_unchanged_at"] = native.cursor
        self.capture_sequence()

    def native_call[T](self, name: str, operation: Callable[[], T]) -> T:
        """Log every MSS lifecycle call and reject slow completed calls locally."""
        self.emit({"event": "call_start", "name": name})
        started = time.monotonic()
        result = operation()
        elapsed = time.monotonic() - started
        self.emit({"event": "call_end", "name": name})
        if elapsed >= CALL_SECONDS:
            raise RuntimeError(f"Wheel native call completed after deadline: {name}")
        return result

    def capture_sequence(self) -> None:
        """Guard MSS setup/monitor reads and always attempt logged resource cleanup."""
        if self.identity is None:
            raise RuntimeError("Missing wheel identity")
        self.wheel.api.check()
        self.emit({"event": "call_start", "name": "wheel.mss.create"})
        started = time.monotonic()
        source = mss.MSS()
        # Retain the returned native owner before post-call timing admission.
        try:
            self.emit({"event": "call_end", "name": "wheel.mss.create"})
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Wheel MSS creation completed after deadline")  # noqa: TRY301 - retained resource cleanup
            self.wheel.api.check()
            monitors = self.native_call("wheel.mss.monitors", lambda: source.monitors)
            self.wheel.api.check()
            left, top, right, bottom = self.identity.rect
            if not any(
                left >= item["left"]
                and top >= item["top"]
                and right <= item["left"] + item["width"]
                and bottom <= item["top"] + item["height"]
                for item in monitors[1:]
            ):
                raise RuntimeError("Wheel target must fit one monitor")  # noqa: TRY301 - owned cleanup
            self.sequence(source)
        except BaseException:
            with suppress(Exception):
                self.close_source(source)
            raise
        else:
            self.close_source(source)

    def close_source(self, source: mss.MSS) -> None:
        """Record cleanup failure without hiding an existing operation failure."""
        # Resource release is permitted after input loss, unlike scroll writes.
        try:
            self.native_call("wheel.mss.close", source.close)
        except Exception as error:
            self.report["cleanup_error"] = repr(error)
            self.emit({"event": "cleanup_error", "error": repr(error)})
            raise

    def sequence(self, source: mss.MSS) -> None:
        """Run fixed interior or endpoint case, without switching input methods."""
        before = self.stable(source)
        moved = self.move(source, -WHEEL)
        if moved[0].vertical <= before[0].vertical:
            raise RuntimeError("Wheel did not move selected panel downward")
        if self.move(source, WHEEL) != before:
            raise RuntimeError("Interior wheel restoration was not exact")
        self.report["interior_roundtrip_exact"] = True
        if self.args.mode == "interior":
            self.report["restored"] = True
            self.report["wheel_sequence_completed"] = True
            return
        self.endpoint(source, before)

    def endpoint(self, source: mss.MSS, before: tuple[ScrollState, bytes]) -> None:
        """Observe bottom propagation and attempt exact inverse only while guarded."""
        count = 0
        if self.last is None:
            raise RuntimeError("Missing wheel state")
        while self.last.vertical < FULL_PERCENT:
            previous = self.last.vertical
            observed = self.move(source, -WHEEL)
            count += 1
            if observed[0].vertical <= previous:
                raise RuntimeError("Wheel stopped before native endpoint")
        endpoint = self.stable(source)
        if self.move(source, -WHEEL) != endpoint:
            raise RuntimeError("Endpoint wheel changed selected panel")
        self.report["endpoint_repeat_exact"] = True
        for _ in range(count):
            restored = self.move(source, WHEEL)
            if restored == before:
                break
            if restored[0].vertical <= before[0].vertical:
                raise RuntimeError("Restoration overshot initial state")
        if self.stable(source) != before:
            raise RuntimeError("Endpoint wheel restoration was not exact")
        self.report["restored"] = True
        self.report["wheel_sequence_completed"] = True

    @override
    def restore_after_failure(self) -> None:
        """No fallback write after any diagnostic failure or uncertain outcome."""
        self.report["restoration_prohibited"] = True


def main() -> int:
    """Accept only explicit owned-fixture invocation, never arbitrary app targets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("interior", "endpoint"), required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture_manifest.read_text(encoding="utf-8"))
    if fixture.get("kind") != "owned-edge":
        raise ValueError("Only owned Edge fixtures are allowed")
    args.x, args.y = (round(value) for value in fixture["click"])
    args.settle_discovery = True
    return WheelProbe(args).run()


if __name__ == "__main__":
    sys.exit(main())
