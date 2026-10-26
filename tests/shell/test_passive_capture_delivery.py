"""Capture admission preserves focus/UI; explicit file-open intent can activate."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast, override

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QMenu,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_binding import FixtureBackend
from tests.shell.test_reviewed_delivery import capture
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.reviewed import CaptureCompleteness, ReviewedCapture
from verdiclip.capture.scroll.binding import (
    BindingOptions,
    BindingState,
    ScrollCaptureBinding,
)
from verdiclip.capture.scroll.panels import ScrollReviewPanel
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.editor.canvas import CanvasView
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import CaptureSettings, Settings


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Use fake screen/hotkey/shell services and private output only."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


class PreservedUi(QObject):
    """Observe only pre-existing widgets; teardown happens after observation."""

    def __init__(self, widgets: tuple[QWidget, ...]) -> None:
        super().__init__()
        self.widgets = widgets
        self.events: list[QEvent.Type] = []
        for widget in widgets:
            widget.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self.widgets and event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.WindowStateChange,
        }:
            self.events.append(event.type())
        return False

    def stop(self) -> None:
        """Exclude explicit test teardown from the capture preservation receipt."""
        for widget in self.widgets:
            widget.removeEventFilter(self)


@pytest.fixture
def presentation_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Track explicit focus/raise/activate calls while retaining real Qt behavior."""
    calls: list[str] = []
    focus = CanvasView.setFocus
    activate = EditorWindow.activateWindow
    raise_window = EditorWindow.raise_

    def set_focus(
        canvas: CanvasView, reason: Qt.FocusReason = Qt.FocusReason.OtherFocusReason
    ) -> None:
        calls.append("canvas focus")
        focus(canvas, reason)

    def activate_window(window: EditorWindow) -> None:
        calls.append("activate")
        activate(window)

    def raise_editor(window: EditorWindow) -> None:
        calls.append("raise")
        raise_window(window)

    monkeypatch.setattr(CanvasView, "setFocus", set_focus)
    monkeypatch.setattr(EditorWindow, "activateWindow", activate_window)
    monkeypatch.setattr(EditorWindow, "raise_", raise_editor)
    return calls


@pytest.mark.parametrize(
    ("context", "activate_editor"),
    [
        ("ordinary", False),
        ("popup", False),
        ("modal", False),
        ("popup", True),
        ("modal", True),
    ],
)
def test_new_capture_window_never_requests_activation_or_dismisses_existing_ui(  # noqa: PLR0913 - context/activation policy matrix
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    presentation_calls: list[str],
    *,
    context: str,
    activate_editor: bool,
) -> None:
    """Default capture delivery is passive even when Qt knows no foreign popup."""
    app = factory(replace(Settings(), capture=CaptureSettings(open_editor=True)))
    anchor = QWidget()
    edit = QLineEdit(anchor)
    layout = QVBoxLayout(anchor)
    layout.addWidget(edit)
    qtbot.addWidget(anchor)
    anchor.show()
    anchor.activateWindow()
    edit.setFocus()
    surface: QWidget = anchor
    if context == "popup":
        menu = QMenu(anchor)
        menu.addAction("Keep actionable")
        menu.popup(anchor.mapToGlobal(anchor.rect().center()))
        surface = menu
    elif context == "modal":
        dialog = QDialog(anchor)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        surface = dialog
    qtbot.wait(0)
    before = (
        QApplication.activeWindow(),
        QApplication.activePopupWidget(),
        QApplication.activeModalWidget(),
        QApplication.focusWidget(),
        surface.windowState(),
    )
    observer = PreservedUi((anchor, surface))
    try:
        host = app.controller.deliver_reviewed_capture(
            capture(sample_image), activate_editor=activate_editor
        )
        assert host is not None
        qtbot.wait(0)
        assert presentation_calls == []
        assert host.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert host.isVisible() is (context == "ordinary")
        assert surface.isVisible()
        assert (
            QApplication.activeWindow(),
            QApplication.activePopupWidget(),
            QApplication.activeModalWidget(),
            QApplication.focusWidget(),
            surface.windowState(),
        ) == before
        assert observer.events == []
        if isinstance(surface, QMenu):
            assert surface.actions()[0].text() == "Keep actionable"
            assert surface.actions()[0].isEnabled()
    finally:
        observer.stop()
        surface.close()
        anchor.close()
    app.controller.workspace.present_retained()
    assert host.isVisible()
    assert presentation_calls == []


@pytest.mark.parametrize(
    "state",
    [
        Qt.WindowState.WindowNoState,
        Qt.WindowState.WindowMinimized,
        Qt.WindowState.WindowMaximized,
    ],
)
def test_background_tab_does_not_show_restore_or_change_existing_active_document(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
    state: Qt.WindowState,
) -> None:
    """A visible or minimized target receives a document without window operations."""
    app = factory()
    host = app.controller.deliver_reviewed_capture(capture(sample_image, "First"))
    assert host is not None
    first = host.pages[0]
    host.setWindowState(state)
    before = host.windowState()
    original_show, original_normal = host.show, host.showNormal
    shown: list[str] = []

    def show() -> None:
        shown.append("show")
        original_show()

    def normal() -> None:
        shown.append("restore")
        original_normal()

    monkeypatch.setattr(host, "show", show)
    monkeypatch.setattr(host, "showNormal", normal)
    presentation_calls.clear()
    observer = PreservedUi((host,))
    try:
        assert (
            app.controller.deliver_reviewed_capture(capture(sample_image, "Incoming"))
            is host
        )
        assert host.session is first.record.session
        assert len(host.pages) == 2
        assert host.windowState() == before
        assert shown == []
        assert presentation_calls == []
        assert observer.events == []
    finally:
        observer.stop()


@pytest.mark.parametrize("maximized", [False, True])
def test_explicit_capture_activation_and_user_file_open_can_focus_and_restore(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    presentation_calls: list[str],
    *,
    maximized: bool,
) -> None:
    """Activation policy is a caller choice, not part of the canonical image value."""
    app = factory()
    host = app.controller.deliver_reviewed_capture(
        capture(sample_image), activate_editor=True
    )
    assert host is not None
    assert "raise" in presentation_calls
    assert "activate" in presentation_calls
    assert not host.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    if maximized:
        host.showMaximized()
    host.showMinimized()
    assert host.isMinimized()
    presentation_calls.clear()
    path = tmp_path / "explicit.png"
    assert sample_image.save(str(path))
    assert app.controller.open_image(path, target=host) is host
    assert not host.isMinimized()
    assert host.isMaximized() is maximized
    assert "raise" in presentation_calls
    assert "activate" in presentation_calls


def test_passive_focus_lease_releases_after_admission_failure(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """A failed passive insertion cannot leave future explicit actions suppressed."""
    app = factory()
    host = app.controller.deliver_reviewed_capture(capture(sample_image, "First"))
    assert host is not None
    original = host.add_document

    def fail(*_args: object, **_kwargs: object) -> None:
        raise MemoryError("Injected passive admission failure")

    monkeypatch.setattr(host, "add_document", fail)
    result = capture(sample_image, "Retry")
    app.controller.deliver_reviewed_capture(result)
    assert result in app.controller.pending_deliveries
    monkeypatch.setattr(host, "add_document", original)
    presentation_calls.clear()
    explicit = app.controller.deliver_reviewed_capture(
        capture(sample_image, "Explicit"), activate_editor=True
    )
    assert explicit is host
    assert "activate" in presentation_calls


def test_deferred_editor_close_cannot_reopen_when_popup_later_ends(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
) -> None:
    """A presentation timer cannot resurrect a document explicitly closed by user."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Preserved")
    menu.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    host = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert host is not None
    assert not host.isVisible()
    app.controller.workspace.present_retained()
    assert menu.isVisible()
    assert host.close()
    assert not app.controller.workspace.documents
    menu.close()
    app.controller.workspace.present_retained()
    assert not app.controller.workspace.windows
    assert not app.controller.workspace._presentation_timer.isActive()


def test_deferred_show_failure_retains_document_for_explicit_recovery(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """Failed delayed presentation preserves pixels and releases the focus lease."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Preserved")
    menu.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    host = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert host is not None
    records = app.controller.workspace.documents
    original = host.show
    messages: list[str] = []
    app.controller.workspace.presentation_failed.connect(messages.append)

    def fail() -> None:
        raise MemoryError("Injected delayed show failure")

    monkeypatch.setattr(host, "show", fail)
    menu.close()
    app.controller.workspace.present_retained()
    assert messages
    assert app.controller.workspace.documents == records
    assert not app.controller.workspace._presentation_timer.isActive()
    assert not host.isVisible()
    monkeypatch.setattr(host, "show", original)
    app.controller.tray.messageClicked.emit()
    assert host.isVisible()
    assert presentation_calls == []


def test_late_deferred_presentation_cannot_undo_a_user_window_state_change(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """A user-presented/minimized editor wins over an older queued passive show."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Preserved")
    menu.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    host = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert host is not None
    menu.close()
    # Explicit user presentation precedes the queued passive presentation tick.
    host.show()
    host.showMinimized()
    state = host.windowState()
    calls: list[bool] = []
    monkeypatch.setattr(host, "show", lambda: calls.append(True))
    presentation_calls.clear()
    app.controller.workspace.present_retained()
    assert calls == []
    assert host.windowState() == state
    assert presentation_calls == []


def test_initial_show_failure_retry_presents_same_document_without_activating(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """A deduplicated retry retries presentation instead of silently stranding it."""
    app = factory()
    original = EditorWindow.show

    def fail(_window: EditorWindow) -> None:
        raise MemoryError("Injected initial show failure")

    monkeypatch.setattr(EditorWindow, "show", fail)
    result = capture(sample_image)
    host = app.controller.deliver_reviewed_capture(result)
    assert host is not None
    assert not host.isVisible()
    assert app.controller.pending_deliveries == (result,)
    records = app.controller.workspace.documents
    monkeypatch.setattr(EditorWindow, "show", original)
    app.controller.retry_delivery(result.capture_id)
    assert app.controller.pending_deliveries == ()
    assert app.controller.workspace.documents == records
    assert host.isVisible()
    assert presentation_calls == []


def test_close_rollback_keeps_deferred_presentation_for_late_edited_document(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    presentation_calls: list[str],
) -> None:
    """A presenter tick during transient zero tabs cannot consume the show lease."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Preserved")
    menu.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    host = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert host is not None
    page = host.pages[0]
    menu.close()
    edited: list[bool] = []

    def during_detach(_index: int) -> None:
        if not host.pages and not edited:
            app.controller.workspace.present_retained()
            edited.append(True)
            page.record.session.history.execute(
                AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Late edit")
            )

    host.tab_bar.currentChanged.connect(during_detach)
    try:
        assert not host.close_tab(0)
        assert edited == [True]
        assert host.pages == (page,)
        assert not host.isVisible()
        app.controller.workspace.present_retained()
        assert host.isVisible()
        assert len(page.record.session.document.annotations) == 1
        assert not page.record.session.history.is_delivered
        assert presentation_calls == []
    finally:
        host.tab_bar.currentChanged.disconnect(during_detach)


def test_popup_opened_by_first_deferred_show_protects_remaining_windows(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """Every queued show revalidates UI after synchronous earlier callbacks."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    initial = QMenu(anchor)
    initial.addAction("Initial")
    initial.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    first = app.controller.deliver_reviewed_capture(capture(sample_image, "First"))
    assert first is not None
    first.begin_close_plan()
    try:
        second = app.controller.deliver_reviewed_capture(
            capture(sample_image, "Second")
        )
    finally:
        first.end_close_plan()
    assert second is not None
    assert second is not first
    late = QMenu(anchor)
    late.addAction("Opened by show callback")
    observers: list[PreservedUi] = []
    shown: list[EditorWindow] = []
    original_show = EditorWindow.show

    def show(window: EditorWindow) -> None:
        original_show(window)
        shown.append(window)
        if len(shown) == 1:
            late.popup(anchor.mapToGlobal(anchor.rect().center()))
            observers.append(PreservedUi((late,)))

    monkeypatch.setattr(EditorWindow, "show", show)
    initial.close()
    try:
        app.controller.workspace.present_retained()
        assert len(shown) == 1
        assert late.isVisible()
        assert observers[0].events == []
        assert sum(host.isVisible() for host in (first, second)) == 1
        assert presentation_calls == []
    finally:
        for observer in observers:
            observer.stop()
        late.close()
    app.controller.workspace.present_retained()
    assert first.isVisible()
    assert second.isVisible()
    assert presentation_calls == []


def test_explicit_activation_rechecks_popup_created_by_admission_callback(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    presentation_calls: list[str],
) -> None:
    """Explicit intent cannot override a popup created during tab admission."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Late admission popup")

    def added(window: EditorWindow) -> None:
        window.tab_bar.currentChanged.connect(
            lambda _index: menu.popup(anchor.mapToGlobal(anchor.rect().center()))
        )

    app.controller.workspace.window_added.connect(added)
    host = app.controller.deliver_reviewed_capture(
        capture(sample_image), activate_editor=True
    )
    try:
        assert host is not None
        assert not host.isVisible()
        assert menu.isVisible()
        assert presentation_calls == []
    finally:
        app.controller.workspace.window_added.disconnect(added)
        menu.close()
    app.controller.workspace.present_retained()
    assert host.isVisible()
    assert presentation_calls == []


def test_explicit_activation_rechecks_popup_created_by_show_callback(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """No raise/activation may follow a show callback that creates protected UI."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Late show popup")
    original = EditorWindow.show

    def show(window: EditorWindow) -> None:
        original(window)
        menu.popup(anchor.mapToGlobal(anchor.rect().center()))

    monkeypatch.setattr(EditorWindow, "show", show)
    host = app.controller.deliver_reviewed_capture(
        capture(sample_image), activate_editor=True
    )
    try:
        assert host is not None
        assert host.isVisible()
        assert menu.isVisible()
        assert "raise" not in presentation_calls
        assert "activate" not in presentation_calls
    finally:
        menu.close()


def test_explicit_restore_rechecks_popup_created_by_window_state_callback(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    presentation_calls: list[str],
) -> None:
    """Restoration callbacks may open UI before a following show/activation."""
    app = factory()
    host = app.controller.deliver_reviewed_capture(capture(sample_image, "First"))
    assert host is not None
    host.showMinimized()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Opened during restore")

    class OnRestore(QObject):
        @override
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if (
                watched is host
                and event.type() == QEvent.Type.WindowStateChange
                and not host.isMinimized()
            ):
                menu.popup(anchor.mapToGlobal(anchor.rect().center()))
            return False

    observer = OnRestore()
    host.installEventFilter(observer)
    presentation_calls.clear()
    try:
        app.controller.deliver_reviewed_capture(
            capture(sample_image, "Incoming"), activate_editor=True
        )
        assert menu.isVisible()
        assert "raise" not in presentation_calls
        assert "activate" not in presentation_calls
    finally:
        host.removeEventFilter(observer)
        menu.close()


def test_explicit_activation_rechecks_popup_created_by_raise_callback(
    factory: AppFactory,
    qtbot: QtBot,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
) -> None:
    """A raise callback cannot leave a stale permission to activate afterward."""
    app = factory()
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Opened during raise")
    original = EditorWindow.raise_

    def raise_editor(window: EditorWindow) -> None:
        original(window)
        menu.popup(anchor.mapToGlobal(anchor.rect().center()))

    monkeypatch.setattr(EditorWindow, "raise_", raise_editor)
    try:
        app.controller.deliver_reviewed_capture(
            capture(sample_image), activate_editor=True
        )
        assert menu.isVisible()
        assert "activate" not in presentation_calls
    finally:
        menu.close()


@pytest.mark.parametrize("edit", [False, True])
def test_stable_capture_binding_delivers_canonical_result_without_disturbing_popup(  # noqa: PLR0913 - cross-feature matrix fixtures
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
    *,
    edit: bool,
) -> None:
    """New capture foundation -> review -> ack -> passive tabs/tray is one seam."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(open_editor=edit, copy_to_clipboard=not edit),
        )
    )
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    menu = QMenu(anchor)
    menu.addAction("Preserved action")
    menu.popup(anchor.mapToGlobal(anchor.rect().center()))
    qtbot.wait(0)
    observer = PreservedUi((anchor, menu))
    backend = FixtureBackend()
    session = ScrollSession(tmp_path / "binding-spool")
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    panels: list[ScrollReviewPanel] = []

    def review_ready(stopped: ScrollSession, _note: str) -> None:
        panel = app.controller.review_scrolling_capture(stopped)
        qtbot.addWidget(panel)
        panels.append(panel)

    binding.review_ready.connect(review_ready)
    copied: list[QImage] = []
    monkeypatch.setattr(
        ImageDelivery, "copy", lambda _self, image: copied.append(image)
    )
    try:
        with qtbot.waitSignal(binding.review_ready, timeout=5000):
            assert binding.start_confirmed(backend.TARGET)
        assert binding.state is BindingState.REVIEW
        assert menu.isVisible()
        values: list[ReviewedCapture] = []
        panels[0].reviewed_capture.connect(values.append)
        from tests.capture.test_scroll_panels import Controls  # noqa: PLC0415

        Controls.button(panels[0], "whole").click()
        assert session.state is ScrollState.CLOSED
        assert values[0].completeness is CaptureCompleteness.COMPLETE
        assert values[0].image == backend.source.copy(0, 0, 96, 96)
        assert len(copied) == int(not edit)
        assert len(app.controller.workspace.documents) == int(edit)
        assert not app.controller.pending_deliveries
        assert menu.isVisible()
        assert menu.actions()[0].isEnabled()
        assert observer.events == []
        assert presentation_calls == []
    finally:
        observer.stop()
        menu.close()
        binding.cancel()
    app.controller.workspace.present_retained()
    assert len(app.controller.editors) == int(edit)
