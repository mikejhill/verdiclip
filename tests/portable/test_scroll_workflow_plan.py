"""Pure unittest entry: run directly, without pytest/root Qt conftest loading."""
# ruff: noqa: PT009, PT027 - parent requires standalone unittest without Qt conftest

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import override

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.scroll_workflow_plan import (
    Action,
    Bounds,
    Labels,
    Observation,
    Phase,
    PlanError,
    Planner,
)


class PlanTests(unittest.TestCase):
    """Plantests."""

    @override
    def setUp(self) -> None:
        """Setup."""
        self.labels = Labels("a" * 40, "case-1", "owned-HWND:PID:start")
        self.plan = Planner(self.labels, 0)
        self.now = 0.0
        self.sequence = 0
        self.plan.observe(self.observation(Phase.SELECTING), self.now)

    def observation(
        self, phase: Phase, *, capture_id: str | None = None, **flags: bool
    ) -> Observation:
        """Observation."""
        self.sequence += 1
        return Observation(
            self.labels,
            self.sequence,
            self.now,
            phase,
            capture_id=self.plan.capture_id if capture_id is None else capture_id,
            **flags,
        )

    def act(self, action: Action, phase: Phase, **flags: bool) -> None:
        """Act."""
        command = self.plan.reserve(action, self.now)
        self.now += 0.1
        capture_id = (
            "capture-1"
            if action in {Action.ACCEPT_WHOLE, Action.ACCEPT_ROWS}
            else self.plan.capture_id
        )
        self.plan.complete(
            command.command_id,
            self.observation(phase, capture_id=capture_id, **flags),
            self.now,
        )

    def capture(self) -> None:
        """Capture."""
        self.act(Action.BEGIN_SELECTION, Phase.SELECTING)
        self.act(Action.ADJUST_SELECTION, Phase.SELECTING)
        self.act(Action.FINISH_SELECTION, Phase.CONFIRMING)
        self.act(
            Action.START, Phase.CAPTURING, start_confirmed=True, backend_moved=True
        )

    def review(self) -> None:
        """Review."""
        self.capture()
        self.now += 0.1
        self.plan.observe(self.observation(Phase.REVIEW, backend_moved=True), self.now)

    def test_complete_whole_or_rows_requires_cleanup_before_delivery(self) -> None:
        """Test complete whole or rows requires cleanup before delivery."""
        for action in (Action.ACCEPT_WHOLE, Action.ACCEPT_ROWS):
            self.setUp()
            self.review()
            if action == Action.ACCEPT_ROWS:
                self.act(Action.CHOOSE_ROWS, Phase.REVIEW)
            self.act(action, Phase.CLEANUP)
            self.assertTrue(self.plan.acceptance_reserved)
            self.assertEqual(self.plan.phase, Phase.CLEANUP)
            self.now += 0.1
            self.plan.observe(
                self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
            )
            self.now += 0.1
            self.plan.observe(self.observation(Phase.DELIVERED), self.now)
            self.assertEqual(self.plan.phase, Phase.DELIVERED)

    def test_stop_is_separate_from_acceptance(self) -> None:
        """Test stop is separate from acceptance."""
        self.capture()
        self.act(Action.STOP, Phase.REVIEW)
        self.assertFalse(self.plan.acceptance_reserved)

    def test_cancel_requires_observed_cleanup(self) -> None:
        """Test cancel requires observed cleanup."""
        self.act(Action.CANCEL, Phase.CLEANUP)
        self.now += 0.1
        self.plan.observe(
            self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
        )
        self.now += 0.1
        self.plan.observe(self.observation(Phase.CANCELLED), self.now)
        self.assertFalse(self.plan.acceptance_reserved)

    def test_delivery_before_cleanup_sticky_fails(self) -> None:
        """Test delivery before cleanup sticky fails."""
        self.review()
        self.act(Action.ACCEPT_WHOLE, Phase.CLEANUP)
        with self.assertRaises(PlanError):
            self.plan.observe(self.observation(Phase.DELIVERED), self.now)
        with self.assertRaises(PlanError):
            self.plan.observe(
                self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
            )
        self.assertEqual(self.plan.phase, Phase.FAILED)

    def test_move_before_start_or_start_without_evidence_fails(self) -> None:
        """Test move before start or start without evidence fails."""
        with self.assertRaises(PlanError):
            self.plan.observe(
                self.observation(Phase.SELECTING, backend_moved=True), self.now
            )
        self.setUp()
        self.act(Action.BEGIN_SELECTION, Phase.SELECTING)
        self.act(Action.FINISH_SELECTION, Phase.CONFIRMING)
        with self.assertRaises(PlanError):
            self.act(Action.START, Phase.CAPTURING)

    def test_finish_without_begin_or_spontaneous_start_fails(self) -> None:
        """Test finish without begin or spontaneous start fails."""
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.FINISH_SELECTION, self.now)
        self.setUp()
        with self.assertRaises(PlanError):
            self.plan.observe(
                self.observation(Phase.CAPTURING, start_confirmed=True), self.now
            )

    def test_unknown_action_result_cannot_be_retried(self) -> None:
        """Test unknown action result cannot be retried."""
        command = self.plan.reserve(Action.BEGIN_SELECTION, self.now)
        with self.assertRaises(PlanError):
            self.plan.unknown(command.command_id, self.now)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id, self.observation(Phase.SELECTING), self.now
            )

    def test_uncertain_completion_is_sticky(self) -> None:
        """Test uncertain completion is sticky."""
        command = self.plan.reserve(Action.BEGIN_SELECTION, self.now)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id,
                self.observation(Phase.SELECTING),
                self.now,
                certain=False,
            )
        self.assertEqual(self.plan.phase, Phase.FAILED)

    def test_duplicates_foreign_and_out_of_order_fail(self) -> None:
        """Test duplicates foreign and out of order fail."""
        assert self.plan.last is not None
        wrong = [
            replace(self.plan.last, sequence=1),
            replace(self.plan.last, sequence=3),
            replace(
                self.plan.last,
                sequence=2,
                labels=replace(self.labels, source_sha="b" * 40),
            ),
            replace(
                self.plan.last,
                sequence=2,
                labels=replace(self.labels, case_id="foreign"),
            ),
            replace(
                self.plan.last,
                sequence=2,
                labels=replace(self.labels, generation="restarted"),
            ),
        ]
        for observation in wrong:
            self.setUp()
            with self.assertRaises(PlanError):
                self.plan.observe(observation, 0)

    def test_command_reservation_cannot_overlap_or_complete_twice(self) -> None:
        """Test command reservation cannot overlap or complete twice."""
        command = self.plan.reserve(Action.BEGIN_SELECTION, 0)
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.CANCEL, 0)
        self.setUp()
        self.act(Action.BEGIN_SELECTION, Phase.SELECTING)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id, self.observation(Phase.SELECTING), self.now
            )

    def test_one_acceptance_only(self) -> None:
        """Test one acceptance only."""
        self.review()
        self.act(Action.ACCEPT_WHOLE, Phase.CLEANUP)
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.ACCEPT_ROWS, self.now)

    def test_deadlines_staleness_clock_and_budget_fail(self) -> None:
        """Test deadlines staleness clock and budget fail."""
        self.now = 2.1
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.BEGIN_SELECTION, self.now)
        self.setUp()
        self.plan.reserve(Action.BEGIN_SELECTION, 0)
        with self.assertRaises(PlanError):
            self.plan.complete("case-1:1", self.observation(Phase.SELECTING), 5)
        self.setUp()
        with self.assertRaises(PlanError):
            self.plan.observe(self.observation(Phase.SELECTING), 15)
        self.setUp()
        self.now = 0.1
        self.plan.observe(self.observation(Phase.SELECTING), self.now)
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.BEGIN_SELECTION, 0)
        self.setUp()
        self.plan = Planner(self.labels, 0, Bounds(max_commands=1))
        self.sequence = 0
        self.plan.observe(self.observation(Phase.SELECTING), self.now)
        self.act(Action.BEGIN_SELECTION, Phase.SELECTING)
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.ADJUST_SELECTION, self.now)

    def test_finite_bounds_and_labels(self) -> None:
        """Test finite bounds and labels."""
        for factory in (
            lambda: Bounds(total=float("inf")),
            lambda: Bounds(phase=0),
            lambda: Bounds(max_commands=0),
        ):
            with self.assertRaises(ValueError):
                factory()
        with self.assertRaises(ValueError):
            Labels("main", "case", "generation")

    def retained_failure(self) -> None:
        """Observe a known retained output failure after actual cleanup proof."""
        self.review()
        self.act(Action.ACCEPT_WHOLE, Phase.CLEANUP)
        self.plan.observe(
            self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
        )
        self.plan.observe(
            self.observation(Phase.RETRY_REVIEW, delivery_failed=True), self.now
        )

    def test_retry_same_capture_keeps_acceptance_and_needs_new_cleanup(self) -> None:
        """Explicit retry reuses the canonical artifact and a fresh cleanup gate."""
        self.retained_failure()
        capture_id = self.plan.capture_id
        self.act(Action.RETRY, Phase.CLEANUP)
        self.assertTrue(self.plan.acceptance_reserved)
        self.assertEqual(self.plan.capture_id, capture_id)
        self.assertFalse(self.plan.cleanup_seen)
        self.plan.observe(
            self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
        )
        self.plan.observe(self.observation(Phase.DELIVERED), self.now)
        self.assertEqual(self.plan.phase, Phase.DELIVERED)

    def test_retry_cannot_reuse_prior_cleanup_proof(self) -> None:
        """A newly reserved lease cannot publish using the old lease's cleanup."""
        self.retained_failure()
        self.act(Action.RETRY, Phase.CLEANUP)
        with self.assertRaises(PlanError):
            self.plan.observe(self.observation(Phase.DELIVERED), self.now)

    def test_retry_rejects_changed_capture_identity(self) -> None:
        """Both retained-failure admission and retry results bind the same ID."""
        self.retained_failure()
        command = self.plan.reserve(Action.RETRY, self.now)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id,
                self.observation(Phase.CLEANUP, capture_id="foreign"),
                self.now,
            )
        self.setUp()
        self.review()
        self.act(Action.ACCEPT_WHOLE, Phase.CLEANUP)
        self.plan.observe(
            self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
        )
        with self.assertRaises(PlanError):
            self.plan.observe(
                self.observation(
                    Phase.RETRY_REVIEW, capture_id="foreign", delivery_failed=True
                ),
                self.now,
            )

    def test_retry_requires_actual_failure_and_previous_cleanup(self) -> None:
        """A review or cleanup acknowledgement alone never authorizes retry."""
        self.review()
        with self.assertRaises(PlanError):
            self.plan.reserve(Action.RETRY, self.now)
        for cleaned, failed in ((False, True), (True, False)):
            self.setUp()
            self.review()
            self.act(Action.ACCEPT_WHOLE, Phase.CLEANUP)
            if cleaned:
                self.plan.observe(
                    self.observation(Phase.CLEANUP, cleanup_confirmed=True), self.now
                )
            with self.assertRaises(PlanError):
                self.plan.observe(
                    self.observation(Phase.RETRY_REVIEW, delivery_failed=failed),
                    self.now,
                )

    def test_retry_unknown_is_sticky_and_never_auto_retries(self) -> None:
        """Known failure can retry explicitly; unknown retry outcome cannot."""
        self.retained_failure()
        command = self.plan.reserve(Action.RETRY, self.now)
        with self.assertRaises(PlanError):
            self.plan.unknown(command.command_id, self.now)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id, self.observation(Phase.CLEANUP), self.now
            )

    def test_acceptance_result_requires_capture_id(self) -> None:
        """Never accept an artifact whose immutable canonical identity is absent."""
        self.review()
        command = self.plan.reserve(Action.ACCEPT_WHOLE, self.now)
        with self.assertRaises(PlanError):
            self.plan.complete(
                command.command_id, self.observation(Phase.CLEANUP), self.now
            )


if __name__ == "__main__":
    unittest.main()
