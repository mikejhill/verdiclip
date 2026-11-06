"""Fixed-size parent-observed timing summaries, independent of the event tail."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from verdiclip.capture.scroll.uia_messages import UiaAction

Outcome = Literal[
    "success", "failure", "cancelled", "startup_timeout", "operation_timeout"
]


@dataclass(frozen=True, slots=True)
class FirstRequestTiming:
    """First occurrences only; null means not observed or clock unavailable."""

    queued_ms: int | None = None
    launch_requested_ms: int | None = None
    process_started_ms: int | None = None
    protocol_ready_ms: int | None = None
    request_sent_ms: int | None = None
    terminal_ms: int | None = None
    action: UiaAction | None = None
    outcome: Outcome | None = None


@dataclass(frozen=True, slots=True)
class ActionTiming:
    """One fixed action; terminal counters are mutually exclusive."""

    action: UiaAction
    accepted: int = 0
    sent: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    timed_out: int = 0
    max_queue_to_terminal_ms: int | None = None
    max_sent_to_terminal_ms: int | None = None
    max_successful_sent_to_terminal_ms: int | None = None
    saturated: bool = False


class LifetimeTiming:
    """Retain five summaries and one active action, never request/provider IDs."""

    MAX_COUNT = 2**31 - 1
    MILESTONES = frozenset({"launch_requested", "process_started", "protocol_ready"})

    def __init__(self) -> None:
        self.first = FirstRequestTiming()
        self.actions = tuple(ActionTiming(action) for action in UiaAction)
        self._seen: set[str] = set()
        self._active: UiaAction | None = None
        self._queued_ms: int | None = None
        self._sent_ms: int | None = None
        self._sent = False
        self._first_active = False

    def _update(self, action: UiaAction, **values: int | bool | None) -> None:
        index = tuple(UiaAction).index(action)
        items = list(self.actions)
        items[index] = replace(items[index], **values)
        self.actions = tuple(items)

    def _increment(self, action: UiaAction, field: str) -> None:
        item = self.actions[tuple(UiaAction).index(action)]
        value = getattr(item, field)
        self._update(
            action,
            **{field: min(self.MAX_COUNT, value + 1)},
            saturated=item.saturated or value >= self.MAX_COUNT,
        )

    def begin(self, action: UiaAction, elapsed_ms: int | None) -> None:
        """Admission, after the worker owns the request, before dispatch callbacks."""
        if not isinstance(action, UiaAction):
            raise TypeError("Invalid diagnostic action")
        self._active, self._queued_ms = action, elapsed_ms
        self._sent_ms, self._sent = None, False
        self._first_active = self.first.action is None
        if self._first_active:
            self.first = replace(self.first, action=action, queued_ms=elapsed_ms)
        self._increment(action, "accepted")

    def mark(self, stage: str, elapsed_ms: int | None) -> None:
        """Only parent lifecycle constants are admitted, not helper stderr."""
        if stage in self.MILESTONES and stage not in self._seen:
            self._seen.add(stage)
            self.first = replace(self.first, **{stage + "_ms": elapsed_ms})
        if stage == "request_sent" and self._active is not None and not self._sent:
            self._sent_ms, self._sent = elapsed_ms, True
            self._increment(self._active, "sent")
            if self._first_active:
                self.first = replace(self.first, request_sent_ms=elapsed_ms)

    def finish(self, outcome: Outcome, elapsed_ms: int | None) -> None:
        """Consume active accounting before any reentrant terminal notification."""
        counters = {
            "success": "succeeded",
            "failure": "failed",
            "cancelled": "cancelled",
            "startup_timeout": "timed_out",
            "operation_timeout": "timed_out",
        }
        counter = counters[outcome]
        action, self._active = self._active, None
        if action is None:
            return
        if self._first_active:
            self.first = replace(self.first, terminal_ms=elapsed_ms, outcome=outcome)
        self._increment(action, counter)
        item = self.actions[tuple(UiaAction).index(action)]
        for field, start in (
            ("max_queue_to_terminal_ms", self._queued_ms),
            ("max_sent_to_terminal_ms", self._sent_ms),
            (
                "max_successful_sent_to_terminal_ms",
                self._sent_ms if outcome == "success" else None,
            ),
        ):
            if start is not None and elapsed_ms is not None and elapsed_ms >= start:
                previous = getattr(item, field)
                self._update(action, **{field: max(previous or 0, elapsed_ms - start)})
