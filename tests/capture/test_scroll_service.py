"""Capture entry, semantic UI and cleanup compose without physical hardware."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputBroker
from tests.capture.test_popup_capture import ExistingScene
from tests.capture.test_scroll_entry import EntryFixture
from tests.capture.test_scroll_workflow import WorkflowFixture
from tests.ux.test_capture_journeys import FakeDesktop

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.interaction import CapturePanel
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.workflow import WorkflowState
from verdiclip.capture.service import CaptureService
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo


class SnapshotWindows:
    """The same immutable native generation exists before the fake frame freezes."""

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowInfo]:
        """Expose no native window enumeration or process access."""
        return tuple(
            window
            for window in (WorkflowFixture.WINDOW,)
            if window.handle not in exclude
        )

    def foreground(self) -> WindowInfo:
        """Keep the frozen selection's window identity stable."""
        return WorkflowFixture.WINDOW


def feed_click(broker: ReplayInputBroker, point: Point) -> None:
    """Input enters through the service broker, never a native QWidget click."""
    for kind in (InputKind.MOVE, InputKind.PRESS, InputKind.RELEASE):
        broker.feed(SelectionInput(kind, point))


def click_control(broker: ReplayInputBroker, panel: CapturePanel, label: str) -> None:
    """Apply the real physical/logical coordinate contract to an owned control."""
    control = next(
        item for item in panel.findChildren(QPushButton) if label in item.text()
    )
    logical = control.mapToGlobal(control.rect().center())
    geometry = ScreenGeometry.from_screen(panel.screen())
    point = geometry.physical.top_left + (
        Point(logical.x(), logical.y()) - geometry.logical.top_left
    ).scaled(geometry.scale)
    feed_click(broker, point)


def test_scrollbar_click_runs_full_service_flow_without_dismissing_existing_popup(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """One ordinary click proposes scrolling; explicit Use waits for input cleanup."""
    scene = ExistingScene("popup", qapp)
    broker = ReplayInputBroker(qapp)
    service = CaptureService(FakeDesktop(), SnapshotWindows(), input_broker=broker)
    fixture = WorkflowFixture(tmp_path, input_active=lambda: service.input_active)
    service.set_scroll_workflow(fixture.workflow)
    screenshots: list[object] = []
    service.captured.connect(screenshots.append)
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        feed_click(broker, Point(92, 10))
        assert fixture.workflow.state is WorkflowState.DISCOVERING
        assert not screenshots
        fixture.adapter.deliver(EntryFixture.discovery())
        target = fixture.workflow.panel
        assert isinstance(target, ScrollTargetPanel)
        assert not service.overlays
        scene.assert_preserved()
        click_control(broker, target, "Start")
        qtbot.waitUntil(
            lambda: fixture.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        review = fixture.workflow.panel
        assert isinstance(review, ScrollReviewPanel)
        scene.assert_preserved()
        broker.defer_cleanup = True
        click_control(broker, review, "whole")
        assert service.is_selecting
        assert not fixture.captures
        broker.acknowledge_cleanup()
        assert len(fixture.captures) == 1
        assert not service.is_selecting
        assert not screenshots
        scene.assert_preserved()
    finally:
        broker.defer_cleanup = False
        service.cancel()
        broker.acknowledge_cleanup()
        fixture.close()
        service.deleteLater()
        broker.deleteLater()
        scene.close()


def test_explicit_mode_without_qualified_workflow_has_a_visible_failure(
    qtbot: QtBot, qapp: QApplication
) -> None:
    """An unsupported development capability is never a successful screenshot."""
    broker = ReplayInputBroker(qapp)
    service = CaptureService(FakeDesktop(), SnapshotWindows(), input_broker=broker)
    failures: list[str] = []
    captures: list[object] = []
    service.failed.connect(failures.append)
    service.captured.connect(captures.append)
    service.capture_scrolling()
    qtbot.waitUntil(lambda: bool(failures))
    assert "not qualified" in failures[0]
    assert not captures
    assert not service.is_selecting
    service.deleteLater()
    broker.deleteLater()


class ServiceWorkflow:
    """Always exercise workflow failures through the real service/broker seam."""

    def __init__(self, qapp: QApplication, parent: Path) -> None:
        self.broker = ReplayInputBroker(qapp)
        self.desktop = FakeDesktop()
        self.service = CaptureService(
            self.desktop, SnapshotWindows(), input_broker=self.broker
        )
        self.fixture = WorkflowFixture(
            parent, input_active=lambda: self.service.input_active
        )
        self.service.set_scroll_workflow(self.fixture.workflow)

    def review(self, qtbot: QtBot) -> ScrollReviewPanel:
        """Reach a review using only semantic events and the existing frame fixture."""
        self.service.capture_region()
        qtbot.waitUntil(lambda: bool(self.service.overlays))
        feed_click(self.broker, Point(92, 10))
        self.fixture.adapter.deliver(EntryFixture.discovery())
        panel = self.fixture.workflow.panel
        assert isinstance(panel, ScrollTargetPanel)
        click_control(self.broker, panel, "Start")
        qtbot.waitUntil(
            lambda: self.fixture.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        panel = self.fixture.workflow.panel
        assert isinstance(panel, ScrollReviewPanel)
        return panel

    def close(self) -> None:
        """Release only capture resources created by this test."""
        self.broker.defer_cleanup = False
        self.service.cancel()
        self.broker.acknowledge_cleanup()
        self.fixture.close()
        self.service.deleteLater()
        self.broker.deleteLater()


def test_spontaneous_service_lease_end_pauses_then_resumes_same_review(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """No overlays remain at review, but the broker receipt must still be handled."""
    run = ServiceWorkflow(qapp, tmp_path)
    try:
        review = run.review(qtbot)
        frames = run.desktop.frames
        run.broker.interrupt("Fixture input deadline")
        assert run.fixture.workflow.paused_review
        assert review.isVisible()
        assert not run.fixture.captures
        run.service.capture_region()
        qtbot.waitUntil(lambda: run.fixture.workflow.state is WorkflowState.REVIEW)
        assert run.broker.starts == 2
        assert run.desktop.frames == frames
        assert run.fixture.workflow.panel is review
        click_control(run.broker, review, "whole")
        assert len(run.fixture.captures) == 1
        assert not run.service.is_selecting
    finally:
        run.close()


def test_failed_delivery_keeps_review_and_retries_same_image_after_new_cleanup(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """Synchronous output failure cannot retire the retry UI or change image ID."""
    run = ServiceWorkflow(qapp, tmp_path)
    try:
        review = run.review(qtbot)
        run.fixture.fail_delivery = True
        click_control(run.broker, review, "whole")
        assert run.fixture.workflow.paused_review
        assert run.fixture.workflow.panel is review
        assert review.isVisible()
        assert review.delivery_retry_needed
        assert len(run.fixture.attempts) == 1
        assert not run.fixture.captures
        run.fixture.fail_delivery = False
        run.service.capture_region()
        qtbot.waitUntil(lambda: run.fixture.workflow.state is WorkflowState.REVIEW)
        run.broker.defer_cleanup = True
        click_control(run.broker, review, "Retry")
        assert len(run.fixture.attempts) == 1
        run.broker.acknowledge_cleanup()
        assert len(run.fixture.captures) == 1
        assert run.fixture.attempts[0] is run.fixture.attempts[1]
        assert review.isVisible()
        assert not review.external_input
        assert run.fixture.workflow.panel is None
    finally:
        run.close()


def test_ordinary_content_click_falls_back_to_frozen_window_capture(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """Semantic inspection never changes the existing ordinary window gesture."""
    run = ServiceWorkflow(qapp, tmp_path)
    captures: list[object] = []
    run.service.captured.connect(captures.append)
    try:
        run.service.capture_region()
        qtbot.waitUntil(lambda: bool(run.service.overlays))
        feed_click(run.broker, Point(92, 10))
        discovery = EntryFixture.discovery()
        run.fixture.adapter.deliver(
            replace(discovery, hit_identity=discovery.nodes[1].identity)
        )
        assert len(captures) == 1
        assert not run.service.is_selecting
        assert not run.fixture.backends
    finally:
        run.close()


def test_explicit_region_preserves_roi_and_repeat_requires_new_confirmation(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """The explicit command and Repeat enter consent UI before any new movement."""
    run = ServiceWorkflow(qapp, tmp_path)
    try:
        run.service.capture_scrolling()
        qtbot.waitUntil(lambda: bool(run.service.overlays))
        run.service.overlays[0].region_chosen.emit(Rect(0, 0, 90, 48))
        assert run.fixture.adapter.points == [Point(45, 24)]
        discovery = EntryFixture.discovery()
        run.fixture.adapter.deliver(
            replace(discovery, hit_identity=discovery.nodes[1].identity)
        )
        panel = run.fixture.workflow.panel
        assert isinstance(panel, ScrollTargetPanel)
        click_control(run.broker, panel, "Start")
        qtbot.waitUntil(
            lambda: run.fixture.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        review = run.fixture.workflow.panel
        assert isinstance(review, ScrollReviewPanel)
        click_control(run.broker, review, "whole")
        assert len(run.fixture.captures) == 1
        run.fixture.adapter.scope_closed.emit()
        previous = len(run.fixture.backends)
        run.service.repeat_last()
        qtbot.waitUntil(lambda: bool(run.service.overlays))
        assert len(run.fixture.backends) == previous
        assert not run.fixture.workflow.has_work
    finally:
        run.close()


def test_explicit_region_without_a_window_reports_failure_without_discovery(
    qtbot: QtBot, qapp: QApplication, tmp_path: Path
) -> None:
    """A drag on the desktop cannot scroll some other application's window."""
    run = ServiceWorkflow(qapp, tmp_path)
    failures: list[str] = []
    run.service.failed.connect(failures.append)
    try:
        run.service.capture_scrolling()
        qtbot.waitUntil(lambda: bool(run.service.overlays))
        run.service.overlays[0].region_chosen.emit(Rect(300, 300, 90, 48))
        assert failures
        assert "No supported window" in failures[0]
        assert not run.fixture.adapter.requests
        assert not run.service.is_selecting
    finally:
        run.close()
