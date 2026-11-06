"""Bounded, content-free timing evidence for one owned helper lifetime."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar


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
        self._start = clock()
        self.events: deque[WorkerStamp] = deque(maxlen=self.MAX_EVENTS)
        self.dropped_events = 0
        self.stdout_bytes = self.stderr_bytes = self.pipe_bytes = 0
        self.max_deadline_lateness_ms = 0
        self._marker = bytearray()
        self._skip_line = False

    def mark(self, stage: str) -> None:
        """Callers pass only fixed lifecycle constants, never provider strings."""
        if len(self.events) == self.MAX_EVENTS:
            self.dropped_events += 1
        self.events.append(
            WorkerStamp(stage, max(0, round((self._clock() - self._start) * 1000)))
        )

    def error_bytes(self, data: bytes) -> bool:
        """Recognize fragmented fixed markers without retaining unrelated text."""
        self.stderr_bytes += len(data)
        if self.stderr_bytes > self.MAX_ERROR_BYTES:
            self._marker.clear()
            return False
        for byte in data:
            if byte == self.LF:
                stage = self.MARKERS.get(bytes(self._marker))
                if stage is not None:
                    self.mark(stage)
                self._marker.clear()
                self._skip_line = False
            elif byte != self.CR and not self._skip_line:
                self._marker.append(byte)
                if not any(key.startswith(self._marker) for key in self.MARKERS):
                    self._marker.clear()
                    self._skip_line = True
        return True
