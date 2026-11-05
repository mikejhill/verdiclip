"""Self-driving full UI workflow over declared replay input and pixel providers.

Nothing here installs hooks, enumerates native windows, accesses the clipboard or
launches UIA. Real service, target/review controls, storage and delivery remain.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override
from uuid import uuid4

from PySide6.QtWidgets import QApplication, QSpinBox, QWidget
from scripts.scroll_workflow_plan import Action, Labels, Observation, Phase, Planner
from shiboken6 import isValid
from tests.capture.input_fixture import ReplayInputBroker
from tests.capture.test_popup_capture import ExistingScene
from tests.capture.test_scroll_binding import FixtureBackend
from tests.capture.test_scroll_entry import DiscoveryFixture, EntryFixture
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.scroll.acquisition import AcquisitionWork, WorkKind
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery
from verdiclip.capture.scroll.workflow import WorkflowState
from verdiclip.geometry import Point, Rect
from verdiclip.startup_lifetime import StartupLifetime

if TYPE_CHECKING:
    from pathlib import Path

    from pytestqt.qtbot import QtBot
    from tests.ux.test_app_journeys import App

    from verdiclip.capture.reviewed import ReviewedCapture
    from verdiclip.capture.scroll.uia_adapter import UiaAdapter
    from verdiclip.capture.scroll.uia_protocol import UiaReference


_ORIGIN = Point(0, 0)


@dataclass(frozen=True)
class JourneyCase:
    """Explicit synthetic scene/provider configuration and immutable source label."""

    source_sha: str
    scene: str = "popup"
    hold_move: bool = False

    def __post_init__(self) -> None:
        """Validate recipe identity before constructing any fixture windows."""
        Labels(self.source_sha, "configuration", "replay-configuration")
        if self.scene not in {"popup", "modal"}:
            raise ValueError("Only declared synthetic scenes are supported")


class ControlledProvider(FixtureBackend):
    """Hold the first fake move so Stop is independent of machine scheduling."""

    def __init__(self, *, hold_move: bool) -> None:
        super().__init__()
        self.oracle = self.source.copy()
        self.hold_move = hold_move
        self.held: AcquisitionWork | None = None

    @override
    def execute(self, work: AcquisitionWork) -> None:
        if self.hold_move and work.kind is WorkKind.SCROLL:
            if self.held is not None:
                raise AssertionError("Only one provider operation may be outstanding")
            self.held = work
        else:
            super().execute(work)

    @override
    def finish(self, request_id: int, *, restore: bool) -> None:
        self.held = None
        super().finish(request_id, restore=restore)

    @override
    def cancel(self) -> None:
        """Drive Escape through the live route and verify no document admission."""
        self.held = None
        super().cancel()


class ScrollJourney:
    """Drive actual controls via the same semantic ingress as the service broker."""

    ROI = Rect(0, 0, 90, 48)
    SELECTION_TOP = 8
    SELECTION_ROWS = 24

    def __init__(
        self,
        app: App,
        qtbot: QtBot,
        spool: Path,
        case: JourneyCase,
    ) -> None:
        self.app, self.qtbot, self.spool = app, qtbot, spool
        self.adapter = DiscoveryFixture()
        self.hold_move = case.hold_move
        self.providers: list[ControlledProvider] = []
        self.accepted: list[ReviewedCapture] = []
        self.requested: list[ReviewedCapture] = []
        self.ordinary_captures: list[object] = []
        self.review: ScrollReviewPanel | None = None
        self.workflow = app.controller.configure_scrolling(
            self.adapter, self._provider, spool
        )
        broker = app.controller.capture._broker
        if not isinstance(broker, ReplayInputBroker):
            raise TypeError("This fixture requires explicit replay input")
        self.broker = broker
        self.scene = ExistingScene(
            case.scene, cast(QApplication, QApplication.instance())
        )
        app.controller.capture.captured.connect(self.ordinary_captures.append)
        nonce = uuid4().hex
        self.labels = Labels(case.source_sha, nonce, f"replay-generation:{nonce}")
        self.plan = Planner(self.labels, time.monotonic())
        self.sequence = 0
        self.command_trace: list[str] = []
        self.cleanup_attempted = self.cleanup_confirmed = False
        self.cleanup_attempts = 0

    def observation(
        self, phase: Phase, *, cleaned: bool = False, delivery_failed: bool = False
    ) -> Observation:
        """Read actual replay-fixture state; it is never a native ownership receipt."""
        states = {
            Phase.SELECTING: {WorkflowState.IDLE},
            Phase.CONFIRMING: {WorkflowState.CONFIRMING},
            Phase.CAPTURING: {WorkflowState.CAPTURING},
            Phase.REVIEW: {WorkflowState.REVIEW},
            Phase.CLEANUP: {
                WorkflowState.IDLE,
                WorkflowState.DELIVERING,
                WorkflowState.PAUSED,
            },
            Phase.RETRY_REVIEW: {WorkflowState.PAUSED},
            Phase.DELIVERED: {WorkflowState.IDLE},
            Phase.CANCELLED: {WorkflowState.IDLE},
        }
        assert self.workflow.state in states[phase]
        if phase is Phase.SELECTING:
            assert self.app.controller.capture.overlays
            assert self.broker.active
        if phase is Phase.CLEANUP and not cleaned:
            assert self.broker.busy
            assert not self.broker.active
        if cleaned or phase in {Phase.DELIVERED, Phase.CANCELLED, Phase.RETRY_REVIEW}:
            assert not self.broker.busy
            assert not self.broker.active
            assert all(provider.held is None for provider in self.providers)
        if phase is Phase.RETRY_REVIEW:
            assert delivery_failed
            assert self.app.controller.pending_deliveries
            assert self.review is not None
            assert self.review.delivery_retry_needed
        if phase is Phase.DELIVERED:
            assert len(self.app.controller.workspace.documents) == 1
        if phase is Phase.CANCELLED:
            assert not self.app.controller.workspace.documents
        self.sequence += 1
        return Observation(
            self.labels,
            self.sequence,
            time.monotonic(),
            phase,
            backend_moved=any(provider.moves > 0 for provider in self.providers),
            cleanup_confirmed=cleaned,
            start_confirmed=bool(self.providers),
            capture_id=self.requested[-1].capture_id if self.requested else None,
            delivery_failed=delivery_failed,
        )

    def command(
        self, action: Action, operation: Callable[[], None], phase: Phase
    ) -> None:
        """Reserve before UI ingress and acknowledge only after observed assertions."""
        command = self.plan.reserve(action, time.monotonic())
        self.command_trace.append(action.value)
        operation()
        evidence = self.observation(phase)
        self.plan.complete(command.command_id, evidence, time.monotonic())

    def _provider(
        self, _reference: UiaReference, _adapter: UiaAdapter
    ) -> ControlledProvider:
        provider = ControlledProvider(hold_move=self.hold_move)
        self.providers.append(provider)
        return provider

    def feed(
        self,
        kind: InputKind,
        *,
        point: Point = _ORIGIN,
        reason: str = "",
        amount: int = 1,
    ) -> None:
        """Replay one command without QTest input, cursor changes or native keys."""
        if not self.broker.active:
            raise AssertionError("Input commands require this fixture's active lease")
        self.broker.feed(SelectionInput(kind, point, reason, amount))
        self.scene.assert_preserved()

    def select(self, entry: str) -> ScrollTargetPanel:
        """Use normal scrollbar convenience, dragged ROI or Enter-based ROI."""
        if entry not in {"scrollbar", "rectangle", "enter-region"}:
            raise ValueError("Unknown deterministic selection recipe")
        if entry == "scrollbar":
            self.app.press_hotkey("region")
        else:
            self.app.controller.capture_scrolling()
        self.qtbot.waitUntil(lambda: bool(self.app.controller.capture.overlays))
        self.plan.observe(self.observation(Phase.SELECTING), time.monotonic())
        if entry == "scrollbar":

            def begin_click() -> None:
                self.feed(InputKind.MOVE, point=Point(92, 10))
                self.feed(InputKind.PRESS, point=Point(92, 10))

            self.command(
                Action.BEGIN_SELECTION,
                begin_click,
                Phase.SELECTING,
            )
            last = InputKind.RELEASE
            end = Point(92, 10)
        else:
            first, last = (
                (InputKind.ENTER, InputKind.ENTER)
                if entry == "enter-region"
                else (InputKind.PRESS, InputKind.RELEASE)
            )
            self.command(
                Action.BEGIN_SELECTION, lambda: self.feed(first), Phase.SELECTING
            )
            end = Point(90, 48)
            self.command(
                Action.ADJUST_SELECTION,
                lambda: self.feed(InputKind.MOVE, point=end),
                Phase.SELECTING,
            )

        def finish() -> None:
            self.feed(last, point=end)
            assert self.workflow.state is WorkflowState.DISCOVERING
            assert not self.providers
            assert not self.ordinary_captures
            if entry == "scrollbar":
                assert self.adapter.points[-1] == end
                discovery = EntryFixture.discovery()
            else:
                # An explicit region discovers its center's content ancestry,
                # not the scrollbar ancestry of the separate convenience click.
                assert self.adapter.points[-1] == self.ROI.center
                discovery = UiaDiscovery.decode(
                    {
                        "result": {
                            "window_identity": Payload.WINDOW,
                            "hit_identity": Payload.WINDOW + "/42.7",
                            "nodes": Payload.nodes()[1:],
                        }
                    }
                )
            self.adapter.deliver(discovery)

        self.command(Action.FINISH_SELECTION, finish, Phase.CONFIRMING)
        panel = self.workflow.panel
        assert isinstance(panel, ScrollTargetPanel)
        assert panel.isVisible()
        assert panel.external_input
        assert not self.app.controller.capture.overlays
        if entry != "scrollbar":
            fields = {
                field.accessibleName(): field.value()
                for field in panel.findChildren(QSpinBox)
            }
            assert fields["Content left in physical pixels"] == self.ROI.x
            assert fields["Content top in physical pixels"] == self.ROI.y
            assert fields["Content width in physical pixels"] == self.ROI.width
            assert fields["Content height in physical pixels"] == self.ROI.height
        self.scene.assert_preserved()
        return panel

    def confirm(self) -> None:
        """Start is an actual keyboard-reachable panel action, never a direct signal."""
        assert self.workflow.state is WorkflowState.CONFIRMING
        assert not self.providers

        def start() -> None:
            self.feed(InputKind.NEXT, reason="previous")
            self.feed(InputKind.ENTER)
            assert len(self.providers) == 1
            assert self.workflow.state is WorkflowState.CAPTURING

        self.command(Action.START, start, Phase.CAPTURING)

    def await_review(self) -> ScrollReviewPanel:
        """Observe real review widgets after acquisition and provider finish."""
        self.qtbot.waitUntil(
            lambda: self.workflow.state is WorkflowState.REVIEW, timeout=5000
        )
        panel = self.workflow.panel
        assert isinstance(panel, ScrollReviewPanel)
        assert panel.isVisible()
        assert panel.external_input
        if self.review is None:
            self.review = panel
            panel.reviewed_capture.connect(self.accepted.append)
            panel.capture_accept_requested.connect(self.requested.append)
        assert not self.accepted
        assert not self.app.controller.workspace.documents
        self.scene.assert_preserved()
        self.plan.observe(self.observation(Phase.REVIEW), time.monotonic())
        return panel

    def stop(self) -> None:
        """Hold a fake provider step; Enter activates the real Stop control."""
        self.qtbot.waitUntil(lambda: self.providers[0].held is not None)
        assert self.providers[0].samples > 0

        def stop() -> None:
            self.feed(InputKind.ENTER)
            self.qtbot.waitUntil(lambda: self.workflow.state is WorkflowState.REVIEW)

        self.command(Action.STOP, stop, Phase.REVIEW)

    def accept(self, *, selected: bool) -> None:
        """Operate visible review fields and reserve one cleanup-gated acceptance."""
        assert self.workflow.state is WorkflowState.REVIEW
        if selected:

            def choose_rows() -> None:
                # The visible keyboard order starts at top row, then row count.
                self.feed(
                    InputKind.MOVE, reason="arrow:right", amount=self.SELECTION_TOP
                )
                self.feed(InputKind.NEXT)
                self.feed(InputKind.MOVE, reason="arrow:left", amount=64)
                assert self.review is not None
                fields = {
                    field.accessibleName(): field.value()
                    for field in self.review.findChildren(QSpinBox)
                }
                assert fields["First selected row, zero based"] == self.SELECTION_TOP
                assert fields["Number of selected rows"] == self.SELECTION_ROWS
                self.feed(InputKind.NEXT)
                self.feed(InputKind.NEXT)

            self.command(Action.CHOOSE_ROWS, choose_rows, Phase.REVIEW)
        self.broker.defer_cleanup = True

        def accept() -> None:
            if not selected:
                self.feed(InputKind.NEXT, reason="previous")
            self.feed(InputKind.ENTER)

        self.command(
            Action.ACCEPT_ROWS if selected else Action.ACCEPT_WHOLE,
            accept,
            Phase.CLEANUP,
        )
        assert self.workflow.state is WorkflowState.DELIVERING
        assert self.broker.busy
        assert not self.broker.active
        assert not self.accepted
        assert not self.app.controller.workspace.documents

    def acknowledge_input(self) -> None:
        """Inject an explicit fake cleanup receipt and separately verify its effect."""
        self.broker.acknowledge_cleanup()
        assert not self.broker.busy
        assert not self.broker.active
        assert not self.adapter.busy
        assert all(provider.held is None for provider in self.providers)
        self.plan.observe(
            self.observation(Phase.CLEANUP, cleaned=True), time.monotonic()
        )

    def delivered(self) -> None:
        """Require output admission and retirement before terminal success."""
        self.assert_retired()
        assert len(self.app.controller.workspace.documents) == 1
        self.plan.observe(self.observation(Phase.DELIVERED), time.monotonic())

    def cancel(self) -> None:
        """Drive Escape and require cleanup before claiming no-output cancellation."""
        self.broker.defer_cleanup = True

        def cancel() -> None:
            self.feed(InputKind.CANCEL, reason="escape")
            self.qtbot.waitUntil(lambda: self.workflow.state is WorkflowState.IDLE)

        self.command(Action.CANCEL, cancel, Phase.CLEANUP)
        assert self.broker.busy
        assert not self.broker.active
        assert not self.accepted
        self.acknowledge_input()
        self.assert_retired()
        assert not self.app.controller.workspace.documents
        self.plan.observe(self.observation(Phase.CANCELLED), time.monotonic())

    def retry(self) -> None:
        """Require a retained known failure and reacquire one explicit input lease."""
        assert self.workflow.state is WorkflowState.PAUSED
        assert self.app.controller.pending_deliveries
        self.plan.observe(
            self.observation(Phase.RETRY_REVIEW, delivery_failed=True), time.monotonic()
        )

        def retry() -> None:
            self.app.press_hotkey("region")
            self.qtbot.waitUntil(lambda: self.workflow.state is WorkflowState.REVIEW)
            assert self.workflow.panel is self.review
            self.feed(InputKind.ENTER)
            assert self.workflow.state is WorkflowState.DELIVERING

        self.command(Action.RETRY, retry, Phase.CLEANUP)

    def assert_retired(self, *, scene_visible: bool = True) -> None:
        """Observe cleanup separately from canonical output and file success."""
        assert self.workflow.state is WorkflowState.IDLE
        assert not self.broker.busy
        assert not self.broker.active
        assert not self.adapter.busy
        assert not self.app.controller.capture.overlays
        assert all(provider.held is None for provider in self.providers)
        assert not self.ordinary_captures
        assert all(provider.source == provider.oracle for provider in self.providers)
        assert not list(self.spool.glob("**/*"))
        assert not self.app.controller.capture.is_selecting
        if scene_visible:
            self.scene.assert_preserved()

    def close(self) -> None:
        """Close only fixture-owned capture/review resources; never foreign windows."""
        self.cleanup_attempted = True
        self.cleanup_attempts += 1
        self.cleanup_confirmed = False
        self.broker.defer_cleanup = False
        with StartupLifetime() as owners:
            owners.callback(self._close_scene)
            owners.callback(self._close_review)
            owners.callback(self.broker.acknowledge_cleanup)
            owners.callback(self.app.controller.capture.cancel)
        self.assert_retired(scene_visible=False)
        assert (
            self.review is None
            or not isValid(self.review)
            or not self.review.isVisible()
        )
        assert all(
            not isValid(window) or not window.isVisible()
            for window in self.scene.windows
        )
        self.cleanup_confirmed = True

    def _close_review(self) -> None:
        if self.review is not None and isValid(self.review):
            self.review.close()

    def _close_scene(self) -> None:
        with StartupLifetime() as windows:
            for window in self.scene.windows:
                windows.callback(self._close_owned_window, window)
            windows.callback(self.scene.qapp.removeEventFilter, self.scene.trace)

    @staticmethod
    def _close_owned_window(window: QWidget) -> None:
        if isValid(window):
            window.close()
