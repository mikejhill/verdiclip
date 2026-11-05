"""Pure bounded fixture protocol; descriptors never execute Qt or OS actions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Never


class Phase(StrEnum):
    """Observed fixture phases, including terminal refusal."""

    SELECTING = "selecting"
    CONFIRMING = "confirming"
    CAPTURING = "capturing"
    REVIEW = "review"
    RETRY_REVIEW = "retry-review"
    CLEANUP = "cleanup-await"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Action(StrEnum):
    """Semantic fixture goals; no generated OS input is represented."""

    BEGIN_SELECTION = "begin-selection"
    ADJUST_SELECTION = "adjust-selection"
    FINISH_SELECTION = "finish-selection"
    START = "start"
    STOP = "stop"
    CHOOSE_ROWS = "choose-rows"
    RETRY = "retry"
    ACCEPT_WHOLE = "accept-whole"
    ACCEPT_ROWS = "accept-rows"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Labels:
    """Generation is the fixture's immutable HWND/PID/start or equivalent label."""

    source_sha: str
    case_id: str
    generation: str

    def __post_init__(self) -> None:
        """Reject mutable reference names and missing ownership labels."""
        if re.fullmatch(r"[0-9a-f]{40}", self.source_sha) is None:
            raise ValueError("Immutable source SHA required")
        if not self.case_id or not self.generation:
            raise ValueError("Nonempty case and generation required")


@dataclass(frozen=True)
class Bounds:
    """All durations use the caller's one monotonic clock, in seconds."""

    total: float = 80
    phase: float = 15
    capturing: float = 30
    cleanup: float = 10
    command: float = 5
    observation_age: float = 2
    max_commands: int = 32

    def __post_init__(self) -> None:
        """Reject unbounded time or action budgets."""
        durations = (
            self.total,
            self.phase,
            self.capturing,
            self.cleanup,
            self.command,
            self.observation_age,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("Finite positive bounds required")
        max_command_limit = 256
        if (
            type(self.max_commands) is not int
            or not 1 <= self.max_commands <= max_command_limit
        ):
            raise ValueError("Finite command budget required")


_DEFAULT_BOUNDS = Bounds()


@dataclass(frozen=True)
class Observation:
    """Caller supplies actual fixture evidence; this class observes nothing."""

    labels: Labels
    sequence: int
    observed_at: float
    phase: Phase
    backend_moved: bool = False
    cleanup_confirmed: bool = False
    start_confirmed: bool = False
    capture_id: str | None = None
    delivery_failed: bool = False


@dataclass(frozen=True)
class Command:
    """One reserved semantic action, not acknowledgement or generated input."""

    labels: Labels
    command_id: str
    action: Action
    issued_at: float
    expires_at: float
    control_goal: str


class PlanError(RuntimeError):
    """A failed protocol gate is permanent for this planner instance."""


GOALS = {
    Action.BEGIN_SELECTION: "Selection: press or Enter begins the owned region",
    Action.ADJUST_SELECTION: "Selection: owned virtual-pointer movement or arrows",
    Action.FINISH_SELECTION: "Selection: release or Enter finishes the owned region",
    Action.START: "ScrollTargetPanel: accessible name 'Start scrolling capture'",
    Action.STOP: "Capture progress button: '&Stop and review'",
    Action.CHOOSE_ROWS: (
        "Review: 'First selected row, zero based' / 'Number of selected rows'"
    ),
    Action.RETRY: "Resume same-capture review with a fresh lease; activate Retry",
    Action.ACCEPT_WHOLE: "ScrollReviewPanel button: 'Use &whole capture'",
    Action.ACCEPT_ROWS: "ScrollReviewPanel button: 'Use &selected rows'",
    Action.CANCEL: "Owned selection/panel Cancel, or review 'Discard capture'",
}


class Planner:
    """Gate a real fixture's actions/results without emulating its UI or backend."""

    def __init__(
        self, labels: Labels, now: float, bounds: Bounds = _DEFAULT_BOUNDS
    ) -> None:
        if not math.isfinite(now):
            raise ValueError("Finite monotonic start required")
        self.labels = labels
        self.bounds = bounds
        self.phase = Phase.SELECTING
        self.failure: str | None = None
        self.deadline = now + bounds.total
        self.phase_deadline = min(self.deadline, now + bounds.phase)
        self.last_now = now
        self.last: Observation | None = None
        self.pending: Command | None = None
        self.commands = 0
        self.started = False
        self.selection_begun = False
        self.acceptance_reserved = False
        self.cancel_reserved = False
        self.cleanup_seen = False
        self.capture_id: str | None = None

    def _fail(self, message: str) -> Never:
        if self.failure is None:
            self.failure = message
            self.phase = Phase.FAILED
        raise PlanError(self.failure)

    def _time(self, now: float) -> None:
        if self.failure is not None:
            raise PlanError(self.failure)
        if not math.isfinite(now) or now < self.last_now:
            self._fail("Clock regressed or is invalid")
        self.last_now = now
        if now >= self.deadline or now >= self.phase_deadline:
            self._fail("Session or phase deadline expired")
        if self.pending is not None and now >= self.pending.expires_at:
            self._fail("Pending command expired")

    def _observation(self, observation: Observation, now: float) -> None:
        self._time(now)
        if any(
            type(value) is not bool
            for value in (
                observation.backend_moved,
                observation.cleanup_confirmed,
                observation.start_confirmed,
                observation.delivery_failed,
            )
        ):
            self._fail("Observation evidence flags must be booleans")
        if observation.labels != self.labels:
            self._fail("Foreign source, case or generation")
        expected = 1 if self.last is None else self.last.sequence + 1
        if type(observation.sequence) is not int or observation.sequence != expected:
            self._fail("Stale, duplicate or out-of-order observation")
        if (
            not math.isfinite(observation.observed_at)
            or not 0 <= now - observation.observed_at <= self.bounds.observation_age
            or (
                self.last is not None
                and observation.observed_at < self.last.observed_at
            )
        ):
            self._fail("Observation is stale, future or regressed")
        if observation.backend_moved and not self.started:
            self._fail("Backend moved before actual explicit Start")
        if observation.start_confirmed and not self.started:
            self._fail("Unreserved Start acknowledgement")
        if observation.capture_id != self.capture_id:
            self._fail("Missing or changed canonical capture identity")
        if observation.delivery_failed and observation.phase != Phase.RETRY_REVIEW:
            self._fail("Delivery failure evidence belongs to retained retry review")

    def _record(self, observation: Observation, now: float) -> None:
        if observation.phase != self.phase:
            self.phase = observation.phase
            interval = {
                Phase.CAPTURING: self.bounds.capturing,
                Phase.CLEANUP: self.bounds.cleanup,
            }.get(self.phase, self.bounds.phase)
            self.phase_deadline = min(self.deadline, now + interval)
        self.last = observation
        if observation.cleanup_confirmed:
            if self.phase != Phase.CLEANUP:
                self._fail("Cleanup proof must precede the terminal observation")
            self.cleanup_seen = True

    def observe(self, observation: Observation, now: float) -> None:
        """Admit spontaneous review/cleanup/terminal evidence, never action acks."""
        self._observation(observation, now)
        if self.pending is not None:
            self._fail("Pending action needs its exact complete result")
        allowed = observation.phase == self.phase and self.phase not in {
            Phase.DELIVERED,
            Phase.CANCELLED,
        }
        if self.phase == Phase.CAPTURING and observation.phase == Phase.REVIEW:
            allowed = True
        if self.phase == Phase.CLEANUP and observation.phase == Phase.RETRY_REVIEW:
            allowed = (
                self.cleanup_seen
                and self.acceptance_reserved
                and not self.cancel_reserved
                and self.capture_id is not None
                and observation.delivery_failed
            )
        if self.phase == Phase.CLEANUP and observation.phase in {
            Phase.DELIVERED,
            Phase.CANCELLED,
        }:
            allowed = self.cleanup_seen and (
                (
                    observation.phase == Phase.DELIVERED
                    and self.acceptance_reserved
                    and not self.cancel_reserved
                )
                or (observation.phase == Phase.CANCELLED and self.cancel_reserved)
            )
        if not allowed:
            self._fail("Out-of-order phase or terminal result before cleanup")
        self._record(observation, now)

    def reserve(self, action: Action, now: float) -> Command:
        """Reserve before the caller invokes its one actual semantic UI action."""
        self._time(now)
        if (
            self.last is None
            or now - self.last.observed_at > self.bounds.observation_age
        ):
            self._fail("Fresh owned observation required before action")
        if self.pending is not None:
            self._fail("Only one pending command allowed")
        if self.commands >= self.bounds.max_commands:
            self._fail("Command budget exhausted")
        permitted = {
            Phase.SELECTING: {
                Action.BEGIN_SELECTION,
                Action.ADJUST_SELECTION,
                Action.FINISH_SELECTION,
                Action.CANCEL,
            },
            Phase.CONFIRMING: {Action.START, Action.CANCEL},
            Phase.CAPTURING: {Action.STOP, Action.CANCEL},
            Phase.REVIEW: {
                Action.CHOOSE_ROWS,
                Action.ACCEPT_WHOLE,
                Action.ACCEPT_ROWS,
                Action.CANCEL,
            },
            Phase.RETRY_REVIEW: {Action.RETRY, Action.CANCEL},
        }.get(self.phase, set())
        if action not in permitted:
            self._fail("Action is not available in this phase")
        self._reserve_effects(action)
        self.commands += 1
        self.pending = Command(
            self.labels,
            f"{self.labels.case_id}:{self.commands}",
            action,
            now,
            min(self.deadline, self.phase_deadline, now + self.bounds.command),
            GOALS[action],
        )
        return self.pending

    def _reserve_effects(self, action: Action) -> None:
        if action == Action.BEGIN_SELECTION and self.selection_begun:
            self._fail("Selection already began")
        if (
            action in {Action.ADJUST_SELECTION, Action.FINISH_SELECTION}
            and not self.selection_begun
        ):
            self._fail("Selection must begin before adjustment or finish")
        if action in {Action.ACCEPT_WHOLE, Action.ACCEPT_ROWS}:
            if self.acceptance_reserved:
                self._fail("Acceptance already reserved")
            self.acceptance_reserved = True
        if action == Action.CANCEL:
            self.cancel_reserved = True
            self.cleanup_seen = False
        if action == Action.RETRY:
            if not self.cleanup_seen or self.capture_id is None:
                self._fail("Retry needs retained failure and previous cleanup")
            # New input lease must supply its own later cleanup evidence.
            self.cleanup_seen = False

    def complete(
        self,
        command_id: str,
        observation: Observation,
        now: float,
        *,
        certain: bool = True,
    ) -> None:
        """Require caller-observed real result; uncertainty never permits retry."""
        self._time(now)
        command = self.pending
        if command is None or command.command_id != command_id:
            self._fail("Stale, duplicate or foreign command result")
        if not certain:
            self._fail("Unknown action outcome; retire case without retry")
        expected = {
            Action.BEGIN_SELECTION: Phase.SELECTING,
            Action.ADJUST_SELECTION: Phase.SELECTING,
            Action.FINISH_SELECTION: Phase.CONFIRMING,
            Action.START: Phase.CAPTURING,
            Action.STOP: Phase.REVIEW,
            Action.CHOOSE_ROWS: Phase.REVIEW,
            Action.RETRY: Phase.CLEANUP,
            Action.ACCEPT_WHOLE: Phase.CLEANUP,
            Action.ACCEPT_ROWS: Phase.CLEANUP,
            Action.CANCEL: Phase.CLEANUP,
        }[command.action]
        if observation.phase != expected:
            self._fail("Action result phase does not match reserved command")
        if command.action == Action.START:
            if not observation.start_confirmed:
                self._fail("Actual explicit Start evidence required")
            self.started = True
        if command.action == Action.BEGIN_SELECTION:
            self.selection_begun = True
        if command.action in {Action.ACCEPT_WHOLE, Action.ACCEPT_ROWS}:
            if (
                not isinstance(observation.capture_id, str)
                or not observation.capture_id
            ):
                self._fail("Acceptance result requires canonical capture identity")
            self.capture_id = observation.capture_id
        self._observation(observation, now)
        self.pending = None
        self._record(observation, now)

    def unknown(self, command_id: str, now: float) -> None:
        """A failed/refused/unknown command cannot be acknowledged or retried."""
        self._time(now)
        if self.pending is None or self.pending.command_id != command_id:
            self._fail("Unknown result belongs to no pending command")
        self._fail("Unknown action outcome; retire case without retry")
