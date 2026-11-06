"""Bounded, content-free timing evidence for one owned helper lifetime."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from typing import ClassVar

from verdiclip.capture.scroll.uia_inspect_clock import (
    ChildClockStamp,
    InspectClockReceipt,
)
from verdiclip.capture.scroll.uia_messages import UiaAction
from verdiclip.capture.scroll.uia_timing import (
    ActionTiming,
    FirstRequestTiming,
    LifetimeTiming,
    Outcome,
)


@dataclass(frozen=True, slots=True)
class WorkerStamp:
    """Only a fixed lifecycle stage and monotonic relative time are retained."""

    stage: str
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class WorkerDiagnostics:
    """No command paths, endpoint names, payloads, provider text or user pixels."""

    phase: str
    process_state: str
    cleanup: str
    request_sent: bool
    events: tuple[WorkerStamp, ...]
    dropped_events: int
    stdout_bytes: int
    stderr_bytes: int
    pipe_bytes: int
    max_deadline_lateness_ms: int
    first_request: FirstRequestTiming = field(default_factory=FirstRequestTiming)
    action_timings: tuple[ActionTiming, ...] = ()
    timing_unavailable: bool = False
    inspect_clock: tuple[ChildClockStamp, ...] = ()
    inspect_clock_unavailable: bool = False


class WorkerTrace:
    """Keep fixed phase markers while discarding arbitrary helper stderr text."""

    MAX_EVENTS = 32
    MAX_ERROR_BYTES = 65_536
    LF = ord("\n")
    CR = ord("\r")
    MARKERS: ClassVar[dict[bytes, str]] = {
        b"VERDICLIP_UIA_PHASE=discovery_runtime_guard_before": (
            "discovery_runtime_guard_before"
        ),
        b"VERDICLIP_UIA_PHASE=discovery_runtime_call": ("discovery_runtime_call"),
        b"VERDICLIP_UIA_PHASE=discovery_runtime_array": ("discovery_runtime_array"),
        b"VERDICLIP_UIA_PHASE=discovery_runtime_guard_after": (
            "discovery_runtime_guard_after"
        ),
        b"VERDICLIP_UIA_PHASE=discovery_runtime_verified": (
            "discovery_runtime_verified"
        ),
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_process_read": (
            "inspect_ancestor_process_read"
        ),
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_process_guard": (
            "inspect_ancestor_process_guard"
        ),
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_runtime_read": (
            "inspect_ancestor_runtime_read"
        ),
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_cycle_guard": (
            "inspect_ancestor_cycle_guard"
        ),
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_verified": "inspect_ancestor_verified",
        b"VERDICLIP_UIA_PHASE=discovery_release": "discovery_release",
        b"VERDICLIP_UIA_PHASE=discovery_released": "discovery_released",
        b"VERDICLIP_UIA_PHASE=discovery_release_failed": "discovery_release_failed",
        b"VERDICLIP_UIA_PHASE=discovery_restore": "discovery_restore",
        b"VERDICLIP_UIA_PHASE=discovery_restored": "discovery_restored",
        b"VERDICLIP_UIA_PHASE=discovery_restore_failed": "discovery_restore_failed",
        b"VERDICLIP_UIA_PHASE=script_entered": "script_entered",
        b"VERDICLIP_UIA_PHASE=pipe_connected": "helper_pipe_connected",
        b"VERDICLIP_UIA_PHASE=runtime_ready": "runtime_ready",
        b"VERDICLIP_UIA_PHASE=inspect_desktop": "inspect_desktop",
        b"VERDICLIP_UIA_PHASE=inspect_root": "inspect_root",
        b"VERDICLIP_UIA_PHASE=inspect_process": "inspect_process",
        b"VERDICLIP_UIA_PHASE=inspect_root_runtime": "inspect_root_runtime",
        b"VERDICLIP_UIA_PHASE=inspect_bounds": "inspect_bounds",
        b"VERDICLIP_UIA_PHASE=inspect_pattern": "inspect_pattern",
        b"VERDICLIP_UIA_PHASE=inspect_scrollable": "inspect_scrollable",
        b"VERDICLIP_UIA_PHASE=inspect_hit": "inspect_hit",
        b"VERDICLIP_UIA_PHASE=inspect_ancestor_identity": "inspect_ancestor_identity",
        b"VERDICLIP_UIA_PHASE=inspect_parent": "inspect_parent",
        b"VERDICLIP_UIA_PHASE=inspect_node_identity": "inspect_node_identity",
        b"VERDICLIP_UIA_PHASE=inspect_control": "inspect_control",
        b"VERDICLIP_UIA_PHASE=inspect_reference": "inspect_reference",
        b"VERDICLIP_UIA_PHASE=inspect_complete": "inspect_complete",
        b"VERDICLIP_UIA_PHASE=native_process_verified": "native_process_verified",
        b"VERDICLIP_UIA_PHASE=nofocus_constructing": "nofocus_constructing",
        b"VERDICLIP_UIA_PHASE=nofocus_constructed": "nofocus_constructed",
        b"VERDICLIP_UIA_PHASE=nofocus_policy": "nofocus_policy",
        b"VERDICLIP_UIA_PHASE=nofocus_resolved": "nofocus_resolved",
        b"VERDICLIP_UIA_PHASE=nofocus_guarded": "nofocus_guarded",
        b"VERDICLIP_UIA_PHASE=nofocus_moving": "nofocus_moving",
        b"VERDICLIP_UIA_PHASE=nofocus_moved": "nofocus_moved",
        b"VERDICLIP_UIA_PHASE=nofocus_call_returned": "nofocus_call_returned",
    }

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self.timing_unavailable = False
        self._start = self._read_clock()
        self._last_ms = 0
        self.timing = LifetimeTiming()
        self.events: deque[WorkerStamp] = deque(maxlen=self.MAX_EVENTS)
        self.dropped_events = 0
        self.stdout_bytes = self.stderr_bytes = self.pipe_bytes = 0
        self.max_deadline_lateness_ms = 0
        self.inspect_clock = InspectClockReceipt()
        self._marker = bytearray()
        self._skip_line = False

    def _read_clock(self) -> float | None:
        try:
            value = self._clock()
            valid = isfinite(value)
        except Exception:  # noqa: BLE001 - optional telemetry cannot affect requests
            self.timing_unavailable = True
            return None
        if valid:
            return value
        self.timing_unavailable = True
        return None

    def _elapsed(self) -> int | None:
        value = self._read_clock()
        if self._start is not None and value is not None:
            delta = (value - self._start) * 1000
            if isfinite(delta) and self._last_ms <= delta <= 2**31 - 1:
                self._last_ms = delta
                return round(delta)
        self.timing_unavailable = True
        return None

    def _retain(self, observation: Callable[[], None]) -> None:
        try:
            observation()
        except Exception:  # noqa: BLE001 - diagnostics cannot change request outcomes
            self.timing_unavailable = True

    def begin_request(self, action: UiaAction) -> None:
        """Retain only a fixed action value and the existing queued timestamp."""
        elapsed = self.mark("queued")
        self._retain(lambda: self.timing.begin(action, elapsed))

    def finish_request(self, outcome: Outcome) -> None:
        """No provider/correlation data is retained by terminal accounting."""
        elapsed = self._elapsed()
        self._retain(lambda: self.timing.finish(outcome, elapsed))

    def mark(self, stage: str, *, helper: bool = False) -> int | None:
        """Keep parent milestones even after bounded tail events are evicted."""
        elapsed = self._elapsed()
        if not helper:
            self._retain(lambda: self.timing.mark(stage, elapsed))
        if elapsed is not None:
            if len(self.events) == self.MAX_EVENTS:
                self.dropped_events += 1
            self.events.append(WorkerStamp(stage, elapsed))
        return elapsed

    def error_bytes(self, data: bytes) -> bool:
        """Recognize fragmented fixed markers without retaining unrelated text."""
        self.stderr_bytes += len(data)
        if self.stderr_bytes > self.MAX_ERROR_BYTES:
            self._marker.clear()
            return False
        self._retain(lambda: self.inspect_clock.feed(data))
        for byte in data:
            if byte == self.LF:
                stage = self.MARKERS.get(bytes(self._marker))
                if stage is not None:
                    self.mark(stage, helper=True)
                self._marker.clear()
                self._skip_line = False
            elif byte != self.CR and not self._skip_line:
                self._marker.append(byte)
                if not any(key.startswith(self._marker) for key in self.MARKERS):
                    self._marker.clear()
                    self._skip_line = True
        return True
