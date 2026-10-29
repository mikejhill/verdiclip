"""Controller/tab ownership composes real scrolling policy over replay boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QObject, QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.input_fixture import ReplayInputBroker
from tests.capture.test_popup_capture import ExistingScene
from tests.capture.test_scroll_binding import FixtureBackend
from tests.capture.test_scroll_entry import DiscoveryFixture, EntryFixture
from tests.capture.test_scroll_service import SnapshotWindows, click_control, feed_click
from tests.ux.test_app_journeys import App, AppFactory, make_app

from verdiclip.capture.reviewed import CaptureKind, ReviewedCapture
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.capture.scroll.workflow import ScrollWorkflow, WorkflowState
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.contracts import EditorOptions
from verdiclip.exceptions import DeliveryError
from verdiclip.geometry import Point
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import CaptureSettings, Settings


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Retain the shell factory's fake hotkeys, settings and OS registrations."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


class Combined:
    """Observe actual controller receipts without native input or provider launch."""

    def __init__(self, app: App, qtbot: QtBot, spool: Path) -> None:
        self.app, self.qtbot = app, qtbot
        self.adapter = DiscoveryFixture()
        self.backends: list[FixtureBackend] = []
        self.workflow = app.controller.configure_scrolling(
            self.adapter, self.backend, spool
        )
        self.broker = cast(ReplayInputBroker, app.controller.capture._broker)
        self.scene: ExistingScene | None = None

    def backend(self, _reference: UiaReference, _adapter: UiaAdapter) -> FixtureBackend:
        """The capture binding owns each finite synthetic pixel provider."""
        value = FixtureBackend()
        self.backends.append(value)
        return value

    def review(self, *, explicit: bool, context: str) -> ScrollReviewPanel:
        """Use semantic broker events through selection, consent and acquisition."""
        self.scene = ExistingScene(context, cast(QApplication, QApplication.instance()))
        if explicit:
            self.app.controller.capture_scrolling()
        else:
            self.app.press_hotkey("region")
        self.qtbot.waitUntil(lambda: bool(self.app.controller.capture.overlays))
        feed_click(self.broker, Point(92, 10))
        assert self.workflow.state is WorkflowState.DISCOVERING
        self.adapter.deliver(EntryFixture.discovery())
        target = self.workflow.panel
        assert isinstance(target, ScrollTargetPanel)
        click_control(self.broker, target, "Start")
        self.qtbot.waitUntil(lambda: self.workflow.state is WorkflowState.REVIEW)
        panel = self.workflow.panel
        assert isinstance(panel, ScrollReviewPanel)
        self.qtbot.addWidget(panel)
        self.scene.assert_preserved()
        return panel

    def close(self) -> None:
        """Discard only fixture capture resources after their assertions."""
        self.broker.defer_cleanup = False
        self.app.controller.capture.cancel()
        self.broker.acknowledge_cleanup()
        if self.scene is not None:
            self.scene.close()


@pytest.fixture
def combined(factory: AppFactory, qtbot: QtBot, tmp_path: Path) -> Iterator[Combined]:
    """Use clipboard-free editor admission unless a test chooses another policy."""
    app = factory(
        Settings(capture=CaptureSettings(open_editor=True)),
        windows=SnapshotWindows(),
    )
    value = Combined(app, qtbot, tmp_path / "spool")
    yield value
    value.close()


def test_native_scrolling_is_disabled_until_explicit_configuration(
    factory: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Default UI and direct invocation cannot start an unqualified native lease."""
    app = factory()
    assert not app.controller.tray.action("scrolling").isEnabled()
    app.press_hotkey("fullscreen")
    host = app.controller.editors[0]
    assert not host.action("capture_scrolling").isEnabled()
    app.controller.capture_scrolling()
    assert not app.controller.capture.is_selecting
    assert not app.controller.capture.overlays
    assert "Scrolling capture unavailable" in caplog.text


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("context", ["popup", "modal"])
def test_scrolling_delivery_waits_for_cleanup_and_admits_an_ordinary_tab(
    combined: Combined, context: str, *, explicit: bool
) -> None:
    """No document or original-context change precedes the cleanup receipt."""
    review = combined.review(explicit=explicit, context=context)
    combined.broker.defer_cleanup = True
    click_control(combined.broker, review, "whole")
    assert combined.workflow.state is WorkflowState.DELIVERING
    assert not combined.app.controller.workspace.documents
    assert combined.app.controller.pending_deliveries == ()
    assert combined.scene is not None
    combined.scene.assert_preserved()
    combined.broker.acknowledge_cleanup()
    assert combined.workflow.state is WorkflowState.IDLE
    record = combined.app.controller.workspace.documents[0]
    assert record.provenance is not None
    assert record.provenance.source_kind is CaptureKind.SCROLLING
    assert not record.session.history.is_delivered
    assert review.isVisible()
    assert combined.app.controller.editors[0].action("capture_scrolling").isEnabled()
    combined.scene.assert_preserved()


def test_failed_delivery_resumes_same_capture_and_does_not_repeat_saved_sink(
    factory: AppFactory, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paused review retries the same image identity and preserves save receipts."""
    app = factory(
        Settings(
            capture=CaptureSettings(
                open_editor=False, copy_to_clipboard=True, save_to_file=True
            )
        ),
        windows=SnapshotWindows(),
    )
    run = Combined(app, qtbot, tmp_path / "spool")
    attempted: list[int] = []
    fail = True

    def copy(_delivery: ImageDelivery, image: object) -> None:
        attempted.append(id(image))
        if fail:
            raise DeliveryError("Clipboard busy")

    monkeypatch.setattr(ImageDelivery, "copy", copy)
    try:
        review = run.review(explicit=True, context="popup")
        click_control(run.broker, review, "whole")
        assert run.workflow.state is WorkflowState.PAUSED
        original: ReviewedCapture = app.controller.pending_deliveries[0]
        saved = list((tmp_path / "out").glob("*.png"))
        assert len(saved) == 1
        before = saved[0].read_bytes()
        stamp = saved[0].stat().st_mtime_ns
        fail = False
        app.press_hotkey("region")
        qtbot.waitUntil(lambda: run.workflow.state is WorkflowState.REVIEW)
        assert run.workflow.panel is review
        click_control(run.broker, review, "Retry")
        assert run.workflow.state is WorkflowState.IDLE
        assert app.controller.pending_deliveries == ()
        assert not app.controller.editors
        assert attempted == [id(original.image), id(original.image)]
        assert saved[0].read_bytes() == before
        assert saved[0].stat().st_mtime_ns == stamp
        assert len(list((tmp_path / "out").glob("*.png"))) == 1
        assert run.scene is not None
        run.scene.assert_preserved()
    finally:
        fail = False
        for pending in app.controller.pending_deliveries:
            app.controller.retry_delivery(pending.capture_id)
        run.close()


def test_configuration_rejects_replacement_without_disturbing_live_owner(
    combined: Combined, tmp_path: Path
) -> None:
    """Repeated configuration neither reparents nor disposes caller resources."""
    replacement = DiscoveryFixture()
    with pytest.raises(ValueError, match="once"):
        combined.app.controller.configure_scrolling(
            replacement, combined.backend, tmp_path / "replacement"
        )
    assert replacement.parent() is None
    assert isValid(replacement)
    assert combined.adapter.parent() is combined.workflow
    assert combined.app.controller.tray.action("scrolling").isEnabled()
    replacement.deleteLater()


def test_attach_failure_returns_adapter_and_keeps_commands_disabled(
    factory: AppFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected attachment has no orphan ownership or enabled launch path."""
    app = factory()
    adapter = DiscoveryFixture()

    def reject(_workflow: ScrollWorkflow) -> None:
        raise ValueError("Fixture attach failed")

    monkeypatch.setattr(app.controller.capture, "set_scroll_workflow", reject)
    with pytest.raises(ValueError, match="attach failed"):
        app.controller.configure_scrolling(
            adapter, lambda _reference, _adapter: FixtureBackend(), tmp_path / "spool"
        )
    assert adapter.parent() is None
    assert isValid(adapter)
    assert not app.controller.tray.action("scrolling").isEnabled()
    adapter.deleteLater()


def test_configuration_during_selection_does_not_take_caller_ownership(
    factory: AppFactory, tmp_path: Path
) -> None:
    """A current input owner cannot be replaced during configuration."""
    app = factory()
    app.press_hotkey("region")
    adapter = DiscoveryFixture()
    try:
        with pytest.raises(ValueError, match="idle"):
            app.controller.configure_scrolling(
                adapter, lambda _ref, _adapter: FixtureBackend(), tmp_path / "spool"
            )
        assert adapter.parent() is None
        assert isValid(adapter)
        assert app.controller.capture.is_selecting
        assert not app.controller.tray.action("scrolling").isEnabled()
    finally:
        app.controller.capture.cancel()
        adapter.deleteLater()


@pytest.mark.parametrize("ownership", ["busy", "parented"])
def test_configuration_cannot_steal_an_existing_provider_scope(
    factory: AppFactory, tmp_path: Path, ownership: str
) -> None:
    """Busy or differently owned providers retain their original lifetime."""
    app = factory()
    adapter, owner = DiscoveryFixture(), QObject()
    if ownership == "busy":
        adapter.pending = True
    else:
        adapter.setParent(owner)
    try:
        with pytest.raises(ValueError, match="unowned"):
            app.controller.configure_scrolling(
                adapter, lambda _ref, _adapter: FixtureBackend(), tmp_path / "spool"
            )
        assert adapter.parent() is (owner if ownership == "parented" else None)
        assert not (tmp_path / "spool").exists()
        assert not app.controller.tray.action("scrolling").isEnabled()
    finally:
        adapter.pending = False
        adapter.deleteLater()
        owner.deleteLater()


def test_capability_enable_preserves_open_tray_menu_and_existing_editor(
    factory: AppFactory, qtbot: QtBot, tmp_path: Path
) -> None:
    """Enabling changes an action in place, preserving menu identity and selection."""
    app = factory()
    app.press_hotkey("fullscreen")
    host = app.controller.editors[0]
    menu = app.controller.tray.menu
    action = app.controller.tray.action("region")
    menu.popup(QPoint(40, 40))
    menu.setActiveAction(action)
    adapter = DiscoveryFixture()
    try:
        qtbot.waitUntil(menu.isVisible)
        app.controller.configure_scrolling(
            adapter, lambda _ref, _adapter: FixtureBackend(), tmp_path / "spool"
        )
        assert menu.isVisible()
        assert QApplication.activePopupWidget() is menu
        assert menu.activeAction() is action
        assert app.controller.tray.action("region") is action
        assert app.controller.tray.action("scrolling").isEnabled()
        assert host.action("capture_scrolling").isEnabled()
        assert not (tmp_path / "spool").exists()
    finally:
        menu.close()


def test_paused_hidden_review_prevents_global_exit(
    combined: Combined, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed input lease is not permission to discard unreviewed pixels."""
    panel = combined.review(explicit=True, context="main")
    combined.broker.interrupt("Synthetic lease expired")
    assert combined.workflow.state is WorkflowState.PAUSED
    assert not combined.broker.active
    panel.hide()
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))
    combined.app.controller.request_exit()
    assert quits == []
    assert combined.workflow.state is WorkflowState.PAUSED
    assert isValid(panel)
    assert combined.app.controller.capture.is_selecting


def test_late_capture_rolls_back_close_and_keeps_one_host_routing(
    combined: Combined,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detachment callback preserves work without duplicating host routes."""
    controller = combined.app.controller
    host = controller._open_document(
        sample_image, EditorOptions(title="Existing"), delivered=True, activate=False
    )
    original = host.pages[0].record
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))

    def arriving(_window: object) -> None:
        controller.capture_scrolling()

    host.tab_bar.currentChanged.connect(arriving)
    try:
        controller.request_exit()
    finally:
        host.tab_bar.currentChanged.disconnect(arriving)
    assert quits == []
    assert controller.workspace.documents == (original,)
    assert controller.editors == (host,)
    assert controller.capture.is_selecting
    controller.capture.cancel()
    picture = tmp_path / "opened.png"
    assert sample_image.save(str(picture))
    host.open_image_requested.emit(picture)
    assert len(controller.workspace.documents) == 2
    assert len(host.pages) == 2


def test_new_capture_from_final_review_close_vetoes_process_exit(
    combined: Combined, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final producer-panel callback cannot make a new capture disappear on quit."""
    panel = combined.review(explicit=True, context="main")
    click_control(combined.broker, panel, "whole")
    assert combined.workflow.state is WorkflowState.IDLE
    for record in combined.app.controller.workspace.documents:
        record.session.history.mark_delivered()
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.DISCARD)
    arriving = combined.app.controller.capture_scrolling
    panel.capture_closed.connect(arriving)
    try:
        combined.app.controller.request_exit()
    finally:
        panel.capture_closed.disconnect(arriving)
    assert combined.app.controller.capture.is_selecting
    assert quits == []
