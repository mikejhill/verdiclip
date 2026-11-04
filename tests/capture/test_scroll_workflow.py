"""Full scrolling interaction uses replay providers and synthetic displays."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.test_capture_interaction import click
from tests.capture.test_scroll_binding import FixtureBackend
from tests.capture.test_scroll_entry import (
    DiscoveryFixture,
    EntryFixture,
    scrollbar_child_discovery,
)
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.reviewed import ReviewedCapture
from verdiclip.capture.scroll.acquisition import WorkKind
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.session import ScrollSession
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.capture.scroll.workflow import (
    ScrollFactories,
    ScrollWorkflow,
    WorkflowState,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo


class WorkflowFixture:
    """Record canonical owner callbacks and keep all hardware boundaries fake."""

    WINDOW = WindowInfo(
        1, "Example", Rect(0, 0, 120, 80), window_identity=Payload.WINDOW
    )
    SCREEN = ScreenGeometry("synthetic", Rect(0, 0, 800, 800), Rect(0, 0, 800, 800), 1)

    def __init__(
        self, parent: Path, *, input_active: Callable[[], bool] | None = None
    ) -> None:
        self.adapter = DiscoveryFixture()
        self.active = True
        self.backends: list[FixtureBackend] = []
        self.sessions: list[ScrollSession] = []
        self.captures: list[ReviewedCapture] = []
        self.attempts: list[ReviewedCapture] = []
        self.fail_delivery = False
        self.panels: list[ScrollReviewPanel] = []
        self.failures: list[str] = []
        self.releases: list[bool] = []
        self.workflow = ScrollWorkflow(
            self.adapter,
            factories=ScrollFactories(self.backend, self.review),
            spool_parent=parent,
            input_active=input_active
            if input_active is not None
            else lambda: self.active,
        )
        self.workflow.failed.connect(self.failures.append)
        self.workflow.release_requested.connect(lambda: self.releases.append(True))

    def backend(self, _reference: UiaReference, _adapter: UiaAdapter) -> FixtureBackend:
        """Construct a finite patterned document with asynchronous real bindings."""
        backend = FixtureBackend()
        self.backends.append(backend)
        return backend

    def review(self, session: ScrollSession) -> ScrollReviewPanel:
        """Model the existing controller-owned review and successful ID ack."""
        self.sessions.append(session)
        panel = ScrollReviewPanel(session)
        self.panels.append(panel)

        def deliver(capture: ReviewedCapture) -> None:
            self.attempts.append(capture)
            if self.fail_delivery:
                panel.delivery_failed(capture.capture_id, "Fixture output unavailable")
                return
            self.captures.append(capture)
            panel.acknowledge_delivery(capture.capture_id)
            session.close()

        panel.reviewed_capture.connect(deliver)
        panel.cancelled.connect(session.close)
        return panel

    def offer(
        self, *, explicit: bool = False, viewport: Rect | None = None
    ) -> ScrollTargetPanel:
        """Resolve one real-shaped semantic scrollbar hit, without UIA or input."""
        started = self.workflow.begin(
            self.WINDOW,
            Point(92, 10),
            (self.SCREEN,),
            explicit=explicit,
            viewport=viewport if viewport is not None else Rect(0, 0, 90, 48),
        )
        assert started
        self.adapter.deliver(EntryFixture.discovery())
        panel = self.workflow.panel
        assert isinstance(panel, ScrollTargetPanel)
        return panel

    def start(self, qtbot: QtBot) -> ScrollReviewPanel:
        """Advance through confirmation/acquisition/restoration into review."""
        panel = self.offer()
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        qtbot.waitUntil(
            lambda: self.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        review = self.workflow.panel
        assert isinstance(review, ScrollReviewPanel)
        return review

    def close(self) -> None:
        """Discard only this fixture's remaining capture resources."""
        self.workflow.cancel()
        for session in self.sessions:
            session.close()
        for panel in self.panels:
            if isValid(panel):
                panel.close()
                panel.deleteLater()
        self.workflow.deleteLater()


def test_full_workflow_publishes_only_after_cleanup_and_retains_canonical_identity(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Semantic targeting reaches real stitching/review and a matching delivery ack."""
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.start(qtbot)
        assert fixture.backends[0].moves == 2
        assert fixture.backends[0].top == 0
        whole = next(
            item for item in panel.findChildren(QPushButton) if "whole" in item.text()
        )
        click(panel, whole)
        assert fixture.workflow.state is WorkflowState.DELIVERING
        assert not fixture.captures
        assert fixture.releases == [True]
        fixture.active = False
        handled = fixture.workflow.input_closed(cleaned=True, message="")
        assert handled
        assert len(fixture.captures) == 1
        assert fixture.captures[0].image.size().width() == 90
        assert fixture.captures[0].image.size().height() == 96
        assert fixture.workflow.state is WorkflowState.IDLE
        assert not fixture.failures
    finally:
        fixture.close()


def test_finite_review_input_expiry_keeps_pixels_for_explicit_resume(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Review can outlive a temporary hook lease without silently losing its image."""
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.start(qtbot)
        fixture.active = False
        fixture.workflow.input_closed(cleaned=True, message="Fixture input deadline")
        assert fixture.workflow.paused_review
        assert panel.isVisible()
        assert not fixture.captures
        fixture.workflow.resume_review()
        assert fixture.workflow.paused_review
        fixture.active = True
        fixture.workflow.resume_review()
        assert fixture.workflow.state is WorkflowState.REVIEW
        whole = next(
            item for item in panel.findChildren(QPushButton) if "whole" in item.text()
        )
        click(panel, whole)
        fixture.active = False
        fixture.workflow.input_closed(cleaned=True, message="")
        assert len(fixture.captures) == 1
    finally:
        fixture.close()


def test_progress_cannot_occlude_the_selected_content(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """An unavailable safe control location refuses Start before any movement."""
    fixture = WorkflowFixture(tmp_path)
    fixture.SCREEN = ScreenGeometry("tiny", Rect(0, 0, 96, 48), Rect(0, 0, 96, 48), 1)
    try:
        panel = fixture.offer()
        qtbot.addWidget(panel)
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        assert fixture.workflow.state is WorkflowState.CONFIRMING
        assert not fixture.backends
        assert start.isEnabled()
        assert panel.isVisible()
    finally:
        fixture.close()


def test_user_cancel_discards_only_workflow_and_late_discovery_is_ignored(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """Escape from target confirmation never invokes a provider step."""
    fixture = WorkflowFixture(tmp_path)
    try:
        fixture.offer()
        fixture.workflow.receive_input(
            SelectionInput(InputKind.CANCEL, Point(92, 10), "escape")
        )
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert fixture.workflow.state is WorkflowState.IDLE
        assert fixture.releases == [True]
        assert not fixture.backends
        fixture.adapter.discovered.emit(
            fixture.adapter.requests[0], EntryFixture.discovery()
        )
        assert fixture.workflow.panel is None
    finally:
        fixture.close()


def test_explicit_roi_is_never_expanded_outside_supported_container(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """A cross-container user drag is a clear refusal, not a larger capture."""
    fixture = WorkflowFixture(tmp_path)
    try:
        fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 120, 80),
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert fixture.failures
        assert not fixture.backends
        assert fixture.workflow.state is WorkflowState.IDLE
    finally:
        fixture.close()


def test_transferred_review_close_does_not_cancel_a_new_capture(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A previously delivered panel no longer owns workflow cancellation."""
    fixture = WorkflowFixture(tmp_path)
    try:
        review = fixture.start(qtbot)
        whole = next(
            item for item in review.findChildren(QPushButton) if "whole" in item.text()
        )
        click(review, whole)
        fixture.active = False
        fixture.workflow.input_closed(cleaned=True, message="")
        fixture.adapter.scope_closed.emit()
        fixture.active = True
        started = fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 90, 48),
        )
        assert started
        review.close()
        assert fixture.workflow.state is WorkflowState.DISCOVERING
        fixture.adapter.deliver(EntryFixture.discovery())
        assert fixture.workflow.state is WorkflowState.CONFIRMING
    finally:
        fixture.close()


def test_published_failed_capture_remains_owned_when_review_closes(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Closing retry UI cannot discard an accepted capture owned by delivery policy."""
    fixture = WorkflowFixture(tmp_path)
    try:
        fixture.fail_delivery = True
        review = fixture.start(qtbot)
        whole = next(
            item for item in review.findChildren(QPushButton) if "whole" in item.text()
        )
        click(review, whole)
        fixture.active = False
        fixture.workflow.input_closed(cleaned=True, message="")
        assert fixture.workflow.paused_review
        assert any(
            "capture retained" in item.text()
            for item in review.findChildren(QPushButton)
        )
        cancelled: list[bool] = []
        review.cancelled.connect(lambda: cancelled.append(True))
        review.close()
        assert not cancelled
        assert fixture.workflow.state is WorkflowState.IDLE
        retained = fixture.sessions[0].accept()
        assert retained is fixture.attempts[0]
    finally:
        fixture.close()


def test_cancel_during_acquisition_transition_does_not_start_backend(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Synchronous cancellation from an observer wins before provider dispatch."""
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.offer()

        def cancel_on_capture() -> None:
            if fixture.workflow.state is WorkflowState.CAPTURING:
                fixture.workflow.cancel()

        fixture.workflow.work_changed.connect(cancel_on_capture)
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert fixture.backends[0].moves == 0
        assert not fixture.panels
    finally:
        fixture.close()


def test_broken_discovery_refuses_retry_without_stuck_checking(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A failed helper cleanup never creates an unfinishable discovery state."""
    fixture = WorkflowFixture(tmp_path)
    try:
        fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 90, 48),
        )
        fixture.adapter.defer_cleanup = True
        fixture.workflow.cancel()
        fixture.adapter.cleanup_failed.emit("Fixture cleanup unavailable")
        fixture.adapter.pending = False
        fixture.adapter.scope_closed.emit()
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        fixture.adapter.defer_cleanup = False
        previous = len(fixture.adapter.requests)
        started = fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 90, 48),
        )
        assert not started
        assert fixture.workflow.state is WorkflowState.IDLE
        assert len(fixture.adapter.requests) == previous
        assert "unavailable" in fixture.failures[-1]
    finally:
        fixture.adapter.defer_cleanup = False
        fixture.close()


@pytest.mark.parametrize("explicit", [False, True])
def test_unknown_window_generation_has_no_semantic_side_effect(
    qtbot: QtBot, tmp_path: Path, *, explicit: bool
) -> None:
    """Ordinary selection falls back while explicit scrolling explains refusal."""
    fixture = WorkflowFixture(tmp_path)
    fallbacks: list[object] = []
    fixture.workflow.fallback.connect(lambda bounds, _title: fallbacks.append(bounds))
    try:
        started = fixture.workflow.begin(
            replace(fixture.WINDOW, window_identity=None),
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=explicit,
        )
        assert not started
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert bool(fixture.failures) is explicit
        assert bool(fallbacks) is not explicit
        assert not fixture.adapter.requests
    finally:
        fixture.close()


@pytest.mark.parametrize("blocker", ["input", "adapter", "workflow"])
def test_busy_entry_cannot_replace_existing_work(
    qtbot: QtBot, tmp_path: Path, blocker: str
) -> None:
    """Repeated capture requests cannot create a second discovery scope."""
    fixture = WorkflowFixture(tmp_path)
    try:
        if blocker == "input":
            fixture.active = False
        elif blocker == "adapter":
            fixture.adapter.pending = True
        else:
            fixture.offer()
        previous = len(fixture.adapter.requests)
        started = fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 90, 48),
        )
        assert not started
        assert len(fixture.adapter.requests) == previous
        qtbot.waitUntil(lambda: fixture.workflow.panel is None or blocker == "workflow")
    finally:
        fixture.close()


@pytest.mark.parametrize("boundary", ["discovery", "confirmation", "takeover"])
def test_observer_cancellation_prevents_late_confirmation_ui(
    qtbot: QtBot, tmp_path: Path, boundary: str
) -> None:
    """Public signal observers may cancel synchronously without resurrecting UI."""
    fixture = WorkflowFixture(tmp_path)
    try:
        if boundary == "takeover":
            fixture.workflow.takeover.connect(fixture.workflow.cancel)
        else:
            state = (
                WorkflowState.DISCOVERING
                if boundary == "discovery"
                else WorkflowState.CONFIRMING
            )

            def cancel_at_boundary() -> None:
                if fixture.workflow.state is state:
                    fixture.workflow.cancel()

            fixture.workflow.work_changed.connect(cancel_at_boundary)
        fixture.workflow.begin(
            fixture.WINDOW,
            Point(92, 10),
            (fixture.SCREEN,),
            explicit=True,
            viewport=Rect(0, 0, 90, 48),
        )
        if fixture.adapter.requests:
            fixture.adapter.deliver(EntryFixture.discovery())
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert fixture.workflow.state is WorkflowState.IDLE
        assert fixture.workflow.panel is None
        assert not fixture.backends
    finally:
        fixture.close()


def test_private_storage_cleanup_failure_can_be_retried_without_target_action(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed private cleanup blocks replacement until a successful safe retry."""
    original_close = ScrollSession.close
    close_count = 0

    def unavailable_backend(
        _self: WorkflowFixture, _reference: UiaReference, _adapter: UiaAdapter
    ) -> FixtureBackend:
        raise CaptureError("Fixture acquisition unavailable")

    def interrupted_cleanup(session: ScrollSession) -> None:
        nonlocal close_count
        close_count += 1
        if close_count == 1:
            raise CaptureError("Fixture private cleanup unavailable")
        original_close(session)

    monkeypatch.setattr(WorkflowFixture, "backend", unavailable_backend)
    monkeypatch.setattr(ScrollSession, "close", interrupted_cleanup)
    fixture = WorkflowFixture(tmp_path)
    try:
        assert not fixture.workflow.retry_cleanup()
        panel = fixture.offer()
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        assert fixture.workflow.state is WorkflowState.CLEANUP_FAILED
        assert not fixture.backends
        assert fixture.workflow.retry_cleanup()
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert close_count == 2
        assert fixture.workflow.state is WorkflowState.IDLE
        assert any("cleanup unavailable" in error for error in fixture.failures)
    finally:
        fixture.close()


def test_review_construction_failure_finishes_without_false_delivery(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed review owner cannot strand progress or publish an unreviewed image."""

    def unavailable_review(
        _self: WorkflowFixture, _session: ScrollSession
    ) -> ScrollReviewPanel:
        raise CaptureError("Fixture review unavailable")

    monkeypatch.setattr(WorkflowFixture, "review", unavailable_review)
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.offer()
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert not fixture.captures
        assert fixture.failures == ["Fixture review unavailable"]
    finally:
        fixture.close()


def test_cancellation_inside_backend_factory_prevents_acquisition(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dependency construction may synchronously cancel without starting work."""
    original = WorkflowFixture.backend

    def cancel_factory(
        fixture: WorkflowFixture, reference: UiaReference, adapter: UiaAdapter
    ) -> FixtureBackend:
        backend = original(fixture, reference, adapter)
        fixture.workflow.cancel()
        return backend

    monkeypatch.setattr(WorkflowFixture, "backend", cancel_factory)
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.offer()
        start = next(
            item for item in panel.findChildren(QPushButton) if "Start" in item.text()
        )
        click(panel, start)
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert fixture.backends[0].moves == 0
        assert not fixture.captures
    finally:
        fixture.close()


def test_negative_input_cleanup_keeps_unpublished_review(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A negative cleanup receipt never reaches canonical delivery."""
    fixture = WorkflowFixture(tmp_path)
    try:
        panel = fixture.start(qtbot)
        whole = next(
            item for item in panel.findChildren(QPushButton) if "whole" in item.text()
        )
        click(panel, whole)
        fixture.active = False
        fixture.workflow.input_closed(cleaned=False, message="")
        assert fixture.workflow.paused_review
        assert fixture.workflow.panel is panel
        assert not fixture.attempts
    finally:
        fixture.close()


def test_native_scrollbar_part_automatically_captures_and_delivers(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic capture must not create/dispose an unused native popup owner."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Automatic capture constructed confirmation UI")

    monkeypatch.setattr(ScrollTargetPanel, "__init__", forbidden)
    fixture = WorkflowFixture(tmp_path)
    try:
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(scrollbar_child_discovery())
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.DELIVERING)
        assert not fixture.captures
        fixture.active = False
        fixture.workflow.input_closed(cleaned=True, message="")
        assert len(fixture.captures) == 1
        assert fixture.workflow.state is WorkflowState.IDLE
    finally:
        fixture.close()


@pytest.mark.parametrize("failure", ["delivery", "cleanup"])
def test_automatic_delivery_failure_exposes_retained_review(
    qtbot: QtBot, tmp_path: Path, failure: str
) -> None:
    """The implicit success path must not leave failed output in an invisible panel."""
    fixture = WorkflowFixture(tmp_path)
    try:
        fixture.fail_delivery = failure == "delivery"
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        qtbot.waitUntil(
            lambda: fixture.workflow.state is WorkflowState.DELIVERING, timeout=5000
        )
        assert not fixture.captures
        fixture.active = False
        fixture.workflow.input_closed(cleaned=failure != "cleanup", message="")
        assert fixture.workflow.state is WorkflowState.PAUSED
        assert fixture.workflow.panel is not None
        assert fixture.workflow.panel.isVisible()
        assert not fixture.captures
        assert len(fixture.sessions) == 1
    finally:
        fixture.close()


def test_scrollbar_stop_retains_partial_review_without_automatic_delivery(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Stop after committed rows is not a complete result and never auto-publishes."""
    fixture = WorkflowFixture(tmp_path)
    try:
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        backend = fixture.backends[0]
        backend.interrupt_kind = WorkKind.SCROLL
        backend.interrupt = fixture.workflow.stop
        qtbot.waitUntil(
            lambda: fixture.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        assert not fixture.releases
        assert not fixture.captures
        assert fixture.workflow.panel is not None
        assert fixture.workflow.panel.isVisible()
        assert fixture.sessions[0].result is not None
        assert fixture.sessions[0].result.stop_reason.value == "stopped_by_user"
    finally:
        fixture.close()


def test_autoaccept_reentrant_replacement_does_not_reshow_old_review(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Cleanup callbacks may synchronously replace work, even with the same state."""
    fixture = WorkflowFixture(tmp_path)
    replaced: list[bool] = []

    def replace_after_cleanup() -> None:
        fixture.workflow.release_requested.disconnect(replace_after_cleanup)
        fixture.workflow.input_closed(cleaned=True, message="")
        fixture.adapter.scope_closed.emit()
        replacement = fixture.offer()
        start = next(
            item
            for item in replacement.findChildren(QPushButton)
            if "Start" in item.text()
        )
        click(replacement, start)
        qtbot.waitUntil(
            lambda: fixture.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        replaced.append(True)

    fixture.workflow.release_requested.connect(replace_after_cleanup)
    try:
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        qtbot.waitUntil(lambda: bool(replaced), timeout=5000)
        assert len(fixture.captures) == 1
        assert not fixture.panels[0].isVisible()
        assert fixture.workflow.panel is not fixture.panels[0]
    finally:
        fixture.close()


def test_automatic_materialization_failure_keeps_bounded_review(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete but oversized image stays reviewable instead of losing rows."""
    fixture = WorkflowFixture(tmp_path)

    def refuse(_session: ScrollSession, **_options: object) -> ReviewedCapture:
        raise CaptureError("Fixture image exceeds materialization budget")

    monkeypatch.setattr(ScrollSession, "accept", refuse)
    try:
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        qtbot.waitUntil(lambda: bool(fixture.panels), timeout=5000)
        assert fixture.workflow.state is WorkflowState.REVIEW
        assert fixture.panels[0].isVisible()
        assert not fixture.releases
        assert not fixture.captures
        assert fixture.sessions[0].result is not None
    finally:
        fixture.close()


@pytest.mark.parametrize("interrupt", [None, InputKind.ENTER, InputKind.CANCEL])
def test_full_display_scrollbar_capture_needs_no_occluding_controls(
    qtbot: QtBot, tmp_path: Path, interrupt: InputKind | None
) -> None:
    """A full-display pane still captures automatically; Enter stops, Escape cancels."""
    fixture = WorkflowFixture(tmp_path)
    screen = ScreenGeometry("tiny", Rect(0, 0, 96, 48), Rect(0, 0, 96, 48), 1)
    try:
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (screen,), explicit=True
        )
        fixture.adapter.deliver(EntryFixture.discovery())
        assert fixture.workflow.state is WorkflowState.CAPTURING
        assert fixture.workflow.panel is None
        if interrupt is not None:
            backend = fixture.backends[0]
            backend.interrupt_kind = WorkKind.SCROLL
            backend.interrupt = lambda: fixture.workflow.receive_input(
                SelectionInput(interrupt, Point(92, 10))
            )
        expected = (
            WorkflowState.IDLE
            if interrupt is InputKind.CANCEL
            else WorkflowState.REVIEW
            if interrupt is InputKind.ENTER
            else WorkflowState.DELIVERING
        )
        qtbot.waitUntil(lambda: fixture.workflow.state is expected, timeout=5000)
        assert not fixture.captures
        if interrupt is None:
            fixture.workflow.input_closed(cleaned=True, message="")
            assert len(fixture.captures) == 1
            assert fixture.captures[0].image == fixture.backends[0].source.copy(
                0, 0, 90, 96
            )
        else:
            assert not fixture.releases or interrupt is InputKind.CANCEL
    finally:
        fixture.close()


@pytest.mark.parametrize("signal", ["takeover", "controls_mode"])
def test_automatic_offer_cancelled_during_callbacks_never_starts(
    tmp_path: Path, signal: str
) -> None:
    """No confirmation-widget identity is needed to reject a retired epoch."""
    fixture = WorkflowFixture(tmp_path)
    try:
        getattr(fixture.workflow, signal).connect(lambda *_: fixture.workflow.cancel())
        assert fixture.workflow.begin(
            fixture.WINDOW, Point(92, 10), (fixture.SCREEN,), explicit=True
        )
        fixture.adapter.deliver(scrollbar_child_discovery())
        assert not fixture.backends
        assert not fixture.captures
        assert fixture.workflow.state is not WorkflowState.CAPTURING
    finally:
        fixture.close()
