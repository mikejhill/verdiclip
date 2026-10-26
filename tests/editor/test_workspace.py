"""Document preservation across real tab widgets, close plans and failures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QWidget,
)
from pytestqt.qtbot import QtBot

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.document.style import Style
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.jobs import JobKind, JobOutcome
from verdiclip.editor.page import DocumentPage, DocumentRecord
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.window import EditorOptions, EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings


@pytest.fixture
def workspace(qtbot: QtBot) -> Iterator[Workspace]:
    """Use only test-owned sessions/windows and explicitly approve teardown."""
    owner = Workspace()
    yield owner
    for window in owner.windows:
        window.set_confirm_close(confirm=False)
    owner.close_all()
    owner.parking.deleteLater()
    qtbot.wait(0)


def open_document(  # noqa: PLR0913 - explicit test scenarios
    owner: Workspace,
    image: QImage,
    tmp_path: Path,
    *,
    title: str = "A",
    new: bool = False,
    delivered: bool = True,
) -> EditorWindow:
    """Open an independent session through the producer-neutral seam."""
    session = EditorSession(Document(image), EditorSettings(), delivered=delivered)
    return owner.open_document(
        session,
        ImageDelivery(OutputSettings(directory=tmp_path)),
        EditorOptions(title=title),
        new_window=new,
    )


def add_rectangle(page: DocumentPage) -> None:
    """Create a real undoable edit whose pixels/selection can survive moves."""
    rectangle = RectangleShape(rect=Rect(30, 40, 50, 60), style=Style())
    page.record.session.history.execute(AddAnnotations([rectangle], "Rectangle"))
    page.record.session.selection.set([rectangle.id])


def test_recover_action_handles_attached_and_parked_documents_independently(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed page must not strand healthy parked work behind attached tabs."""
    host = open_document(workspace, sample_image, tmp_path)
    open_document(workspace, sample_image, tmp_path, title="Retry later")
    open_document(workspace, sample_image, tmp_path, title="Healthy retained")
    attached, blocked, healthy = host.pages
    for page in host.pages:
        add_rectangle(page)
    states = {
        p.record.identifier: p.record.session.history.state_id for p in host.pages
    }
    host.take_page(blocked)
    host.take_page(healthy)
    attach = EditorWindow.attach_page

    def attaching(window: EditorWindow, page: DocumentPage, index: int) -> None:
        if page is blocked:
            raise ValueError("One view could not be attached")
        attach(window, page, index)

    monkeypatch.setattr(EditorWindow, "attach_page", attaching)
    menu = QMenu(host)
    workspace.populate_window_menu(menu, host)
    recover = next(a for a in menu.actions() if a.text() == "Recover retained tabs")
    recover.trigger()
    assert workspace.host_of(attached.record.identifier) is host
    assert workspace.host_of(blocked.record.identifier) is None
    recovered = workspace.host_of(healthy.record.identifier)
    assert recovered is not None
    assert recovered.pages == (healthy,)
    assert len(workspace.windows) == 2
    for record in workspace.documents:
        assert record.session.history.state_id == states[record.identifier]
        assert record.session.document.annotations
    monkeypatch.setattr(EditorWindow, "attach_page", attach)
    recover.trigger()
    assert all(workspace.host_of(r.identifier) is not None for r in workspace.documents)
    assert workspace.host_of(healthy.record.identifier) is recovered


def test_recovery_preserves_existing_popup_until_user_dismisses_it(
    workspace: Workspace, sample_image: QImage, tmp_path: Path, qtbot: QtBot
) -> None:
    """Retrying an orphan presentation must use the same passive admission policy."""
    host = open_document(workspace, sample_image, tmp_path)
    page = host.pages[0]
    host.take_page(page)
    menu = QMenu(host)
    action = menu.addAction("Keep this context")
    hidden = QSignalSpy(menu.aboutToHide)
    menu.popup(host.mapToGlobal(QPoint(20, 20)))
    menu.setActiveAction(action)
    qtbot.waitUntil(menu.isVisible)
    try:
        assert workspace.recover_retained()
        recovered = workspace.host_of(page.record.identifier)
        assert recovered is not None
        assert menu.isVisible()
        assert QApplication.activePopupWidget() is menu
        assert menu.activeAction() is action
        assert hidden.count() == 0
    finally:
        menu.close()
    qtbot.waitUntil(recovered.isVisible)


def test_recovery_tolerates_new_document_arriving_during_attachment(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reentrant admission must not invalidate registry iteration or lose work."""
    host = open_document(workspace, sample_image, tmp_path)
    page = host.pages[0]
    host.take_page(page)
    attach = EditorWindow.attach_page
    arrived = False

    def attaching(window: EditorWindow, value: DocumentPage, index: int) -> None:
        nonlocal arrived
        attach(window, value, index)
        if value is page and not arrived:
            arrived = True
            open_document(workspace, sample_image, tmp_path, title="Late arrival")

    monkeypatch.setattr(EditorWindow, "attach_page", attaching)
    assert workspace.recover_retained()
    assert arrived
    assert len(workspace.documents) == 2
    assert all(workspace.host_of(r.identifier) is not None for r in workspace.documents)
    assert workspace.host_of(page.record.identifier) is not None


def test_partial_recovery_attachment_keeps_a_passive_presentation_lease(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception after attachment cannot strand an invisible, owned document."""
    host = open_document(workspace, sample_image, tmp_path)
    page = host.pages[0]
    add_rectangle(page)
    state = page.record.session.history.state_id
    host.take_page(page)
    menu = QMenu(host)
    action = menu.addAction("Keep this popup")
    hidden = QSignalSpy(menu.aboutToHide)
    menu.popup(host.mapToGlobal(QPoint(20, 20)))
    menu.setActiveAction(action)
    attach = EditorWindow.attach_page

    def attaching(window: EditorWindow, value: DocumentPage, index: int) -> None:
        attach(window, value, index)
        raise MemoryError("Failure after successful attachment")

    monkeypatch.setattr(EditorWindow, "attach_page", attaching)
    try:
        assert not workspace.recover_retained()
        recovered = workspace.host_of(page.record.identifier)
        assert recovered is not None
        monkeypatch.setattr(EditorWindow, "attach_page", attach)
        assert workspace.recover_retained()
        assert menu.isVisible()
        assert menu.activeAction() is action
        assert hidden.count() == 0
        assert page.record.session.history.state_id == state
    finally:
        menu.close()
    qtbot.waitUntil(recovered.isVisible)
    assert recovered.pages == (page,)


@pytest.mark.parametrize("kind", ["document", "page"])
def test_duplicate_identity_cannot_replace_live_document_or_view(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    kind: str,
) -> None:
    """Repeated registration is idempotent; a different owner cannot overwrite it."""
    host = open_document(workspace, sample_image, tmp_path)
    page = host.pages[0]
    add_rectangle(page)
    record = page.record
    workspace.register_document(record)
    workspace.register_page(page)
    duplicate_page = None
    try:
        if kind == "document":
            other = DocumentRecord(
                EditorSession(Document(sample_image), EditorSettings()),
                record.delivery,
                identifier=record.identifier,
            )
            with pytest.raises(ValueError, match=r"\S"):
                workspace.register_document(other)
        else:
            duplicate_page = DocumentPage(record)
            qtbot.addWidget(duplicate_page)
            with pytest.raises(ValueError, match=r"\S"):
                workspace.register_page(duplicate_page)
        assert workspace.documents == (record,)
        assert workspace.host_of(record.identifier) is host
        assert host.pages == (page,)
        assert record.session.document.annotations
    finally:
        if duplicate_page is not None:
            duplicate_page.dispose()


def test_grouping_keeps_independent_history_selection_and_zoom(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Switching the shared host must resolve every command against its page."""
    window = open_document(workspace, sample_image, tmp_path)
    first = window.pages[0]
    add_rectangle(first)
    first.canvas.set_zoom(2)
    same = open_document(workspace, sample_image, tmp_path, title="B")
    second = window.pages[1]
    assert same is window
    assert window.session is second.record.session
    assert not window.action("undo").isEnabled()
    window.action("zoom_out").trigger()
    assert second.canvas.zoom < 1
    window.activate_page(first)
    assert window.canvas.zoom == 2
    assert len(window.session.selection) == 1
    window.action("undo").trigger()
    assert window.session.document.annotations == ()
    assert second.record.session.document.annotations == ()
    assert first.record.session.history.can_redo


def test_detach_reattach_preserves_identity_edits_output_and_tool(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Grouping keeps independent history selection and zoom."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    add_rectangle(page)
    source.activate_tool(ToolId.ARROW)
    page.canvas.set_zoom(1.75)
    page.record.saved_path = tmp_path / "before.png"
    before = source.flattened()
    identity = page.record.identifier
    assert workspace.move(source, page)
    target = workspace.windows[0]
    assert target is not source
    assert not source.isVisible()
    assert target.pages == (page,)
    assert target.session is page.record.session
    assert target.saved_path == tmp_path / "before.png"
    assert target.flattened() == before
    assert target.canvas.zoom == 1.75
    assert target.action("tool_arrow").isChecked()
    assert target.save()
    assert (
        QImage(str(tmp_path / "before.png")).convertToFormat(before.format()) == before
    )
    other = open_document(workspace, sample_image, tmp_path, title="Other", new=True)
    assert workspace.move(target, page, other, 0)
    assert other.pages[0] is page
    assert page.record.identifier == identity
    assert len(workspace.documents) == 2
    other.activate_page(page)
    other.action("undo").trigger()
    assert not page.record.session.document.annotations
    assert page.record.session.history.can_redo


def test_failure_after_destination_attachment_restores_source(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure after destination attachment restores source."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    add_rectangle(page)
    target = open_document(workspace, sample_image, tmp_path, title="B", new=True)
    original = target.attach_page
    before = source.flattened()

    def failing_attach(current: DocumentPage, index: int) -> None:
        original(current, index)
        raise RuntimeError("Injected post-attachment failure")

    monkeypatch.setattr(target, "attach_page", failing_attach)
    failures: list[str] = []
    workspace.transfer_failed.connect(failures.append)
    assert not workspace.move(source, page, target)
    assert source.pages == (page,)
    assert len(target.pages) == 1
    assert source.flattened() == before
    assert len(source.session.selection) == 1
    assert not source.session.history.is_delivered
    assert len(workspace.documents) == 2
    assert len(failures) == 1
    assert not workspace.transferring
    previous_status = [
        label.text() for label in target.statusBar().findChildren(QLabel)
    ]
    page.canvas.cursor_moved.emit(Point(17, 23))
    assert [
        label.text() for label in target.statusBar().findChildren(QLabel)
    ] == previous_status
    assert any(
        "17" in label.text() for label in source.statusBar().findChildren(QLabel)
    )


def test_reentrant_close_during_transfer_cannot_dispose_document(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reentrant close during transfer cannot dispose document."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    target = open_document(workspace, sample_image, tmp_path, new=True)
    original = target.attach_page

    def during_attach(current: DocumentPage, index: int) -> None:
        closed = source.close()
        assert not closed
        assert not workspace.close_all()
        original(current, index)

    monkeypatch.setattr(target, "attach_page", during_attach)
    assert workspace.move(source, page, target)
    assert page in target.pages
    assert page.record in workspace.documents
    assert workspace.windows == (target,)


def test_window_cancel_retains_every_tab(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Window cancel retains every tab."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    open_document(workspace, sample_image, tmp_path, title="B", delivered=False)
    before = window.pages
    answers = iter([CloseChoice.DISCARD, CloseChoice.CANCEL])
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_: next(answers))
    closed = window.close()
    assert not closed
    assert window.isVisible()
    assert window.pages == before
    assert len(workspace.documents) == 2


def test_exit_cancel_does_not_partially_close_other_window(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit cancel does not partially close other window."""
    first = open_document(workspace, sample_image, tmp_path, delivered=False)
    second = open_document(workspace, sample_image, tmp_path, delivered=False, new=True)
    answers = iter([CloseChoice.DISCARD, CloseChoice.CANCEL])
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_: next(answers))
    assert not workspace.close_all()
    assert first.isVisible()
    assert second.isVisible()
    assert len(workspace.documents) == 2


def test_incoming_capture_during_exit_prevents_unapproved_quit(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incoming capture during exit prevents unapproved quit."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)

    def capture_arrives(*_args: object) -> CloseChoice:
        target = open_document(
            workspace, sample_image, tmp_path, title="Incoming", delivered=False
        )
        assert target is not window
        return CloseChoice.DISCARD

    monkeypatch.setattr(ClosePrompt, "ask", capture_arrives)
    assert not workspace.close_all()
    assert len(workspace.documents) == 2
    assert all(window.isVisible() for window in workspace.windows)


def test_save_as_is_pinned_when_selection_changes_reentrantly(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Save as is pinned when selection changes reentrantly."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    first = window.pages[0]
    open_document(workspace, sample_image, tmp_path, title="B", delivered=False)
    second = window.pages[1]
    window.activate_page(first)
    output = tmp_path / "first.png"

    def choose(*_args: object) -> tuple[str, str]:
        window.activate_page(second)
        assert window.session is first.record.session
        assert not workspace.move(window, first)
        assert not window.close_tab()
        return str(output), "PNG"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    assert window.save_as()
    assert first.record.saved_path == output
    assert second.record.saved_path is None
    assert not second.record.session.history.is_delivered
    assert output.is_file()


def test_last_tab_close_disposes_only_approved_work(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Last tab close disposes only approved work."""
    window = open_document(workspace, sample_image, tmp_path)
    assert workspace.has_work
    assert window.close_tab()
    assert not workspace.has_work
    assert not window.isVisible()
    assert not workspace.windows


def test_reorder_uses_page_identity_and_close_keeps_neighbor(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Reorder uses page identity and close keeps neighbor."""
    window = open_document(workspace, sample_image, tmp_path)
    first = window.pages[0]
    for title in ("B", "C"):
        open_document(workspace, sample_image, tmp_path, title=title)
    assert workspace.move(window, first, window, 2)
    assert window.pages[2] is first
    assert window.close_tab(1)
    assert len(window.pages) == 2
    assert first in window.pages
    assert window.tab_bar.tabData(window.pages.index(first)) == first.record.identifier


def test_incoming_document_preserves_visible_popup_and_active_tab(
    workspace: Workspace, sample_image: QImage, tmp_path: Path, qtbot: QtBot
) -> None:
    """Admission during preserved screenshot context must not dismiss a menu."""
    window = open_document(workspace, sample_image, tmp_path)
    first = window.pages[0]
    menu = QMenu(window)
    menu.addAction("Keep this popup open")
    menu.popup(window.mapToGlobal(window.rect().center()))
    qtbot.wait(0)
    assert QApplication.activePopupWidget() is menu
    assert menu.isVisible()
    target = open_document(workspace, sample_image, tmp_path, title="Incoming")
    assert target is window
    assert menu.isVisible()
    assert QApplication.activePopupWidget() is menu
    assert window.session is first.record.session
    assert len(window.pages) == 2
    workspace.cancel_drag()
    assert menu.isVisible()
    menu.close()


def test_output_job_follows_transfer_and_blocks_close_until_acknowledged(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Moving a document cannot orphan a worker or permit premature disposal."""
    source = open_document(workspace, sample_image, tmp_path, delivered=False)
    page = source.pages[0]
    ticket = page.record.jobs.begin(JobKind.OUTPUT)
    assert workspace.move(source, page)
    target = workspace.host_of(page.record.identifier)
    assert target is not None
    assert not target.close_tab()
    assert not workspace.close_all()
    assert page.record.jobs.request_cancel(ticket)
    closed = target.close()
    assert not closed
    assert page.record.jobs.acknowledge_cancel(ticket)
    assert target.close_tab()
    assert page.record.jobs.finish(ticket) is JobOutcome.UNKNOWN
    assert not workspace.has_work


def test_cancel_close_defers_async_edit_and_then_allows_it(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result cannot mutate the image while its close prompt is unanswered."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    record = window.pages[0].record
    ticket = record.jobs.begin(JobKind.EDIT)
    command = AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Job edit")

    def cancel(*_args: object) -> CloseChoice:
        assert record.jobs.apply(ticket, command) is JobOutcome.DEFERRED
        return CloseChoice.CANCEL

    monkeypatch.setattr(ClosePrompt, "ask", cancel)
    closed = window.close()
    assert not closed
    assert record.session.document.annotations == ()
    assert record.jobs.apply(ticket, command) is JobOutcome.CURRENT
    assert len(record.session.document.annotations) == 1


@pytest.mark.parametrize("after_removal", [False, True])
def test_failed_source_removal_retains_document(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    after_removal: bool,
) -> None:
    """A source-side exception before or after mutation cannot orphan edits."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    add_rectangle(page)
    target = open_document(workspace, sample_image, tmp_path, new=True)
    original = source.take_page

    def failing_take(current: DocumentPage) -> int:
        if after_removal:
            original(current)
        raise RuntimeError("Injected source removal failure")

    monkeypatch.setattr(source, "take_page", failing_take)
    assert not workspace.move(source, page, target)
    assert page in source.pages
    assert len(page.record.session.document.annotations) == 1
    assert page.record in workspace.documents
    monkeypatch.setattr(source, "take_page", original)


def test_double_attachment_failure_retains_and_recovers_document(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed destination and rollback keep work alive even if recovery fails."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    add_rectangle(page)
    target = open_document(workspace, sample_image, tmp_path, new=True)
    original = EditorWindow.attach_page

    def fail(*_args: object) -> None:
        raise MemoryError("Injected attachment allocation failure")

    monkeypatch.setattr(EditorWindow, "attach_page", fail)
    assert not workspace.move(source, page, target)
    assert workspace.host_of(page.record.identifier) is None
    assert page.record in workspace.documents
    assert page.parentWidget() is workspace.parking
    assert not workspace.close_all()
    assert len(workspace.documents) == 2
    monkeypatch.setattr(EditorWindow, "attach_page", original)
    assert workspace.recover_retained()
    host = workspace.host_of(page.record.identifier)
    assert host is not None
    assert page in host.pages
    assert len(page.record.session.document.annotations) == 1
    assert not page.record.session.history.is_delivered


@pytest.mark.parametrize("cancel", [False, True])
def test_queued_drop_commits_once_or_cancellation_vetoes_it(  # noqa: PLR0913 - injected event loop and policy
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cancel: bool,
) -> None:
    """A delivered native release cannot outlive capture quiescence."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    target = open_document(workspace, sample_image, tmp_path, new=True)
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: target.tab_bar)
    qtbot.wait(0)
    point = target.tab_bar.mapToGlobal(target.tab_bar.rect().center())
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_moved.emit(point)
    source.tab_bar.drag_released.emit(point)
    if cancel:
        workspace.cancel_drag()
    qtbot.wait(10)
    expected = source if cancel else target
    assert workspace.host_of(page.record.identifier) is expected
    assert len(workspace.documents) == 2


def test_queued_drop_aborts_when_dragged_tab_closed_before_commit(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued release cannot index a tab closed by an earlier callback."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Neighbor")
    neighbor = source.pages[1]
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: source.tab_bar)
    point = source.tab_bar.mapToGlobal(source.tab_bar.tabRect(0).topLeft())
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_released.emit(point)
    assert source.close_tab(source.pages.index(page))
    with qtbot.captureExceptions() as errors:
        qtbot.wait(10)
    assert errors == []
    assert source.pages == (neighbor,)
    assert workspace.documents == (neighbor.record,)


def test_queued_drop_cannot_steal_page_moved_by_earlier_callback(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit move wins over an older release queued for the former host."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    add_rectangle(page)
    open_document(workspace, sample_image, tmp_path, title="Neighbor")
    neighbor = source.pages[1]
    target = open_document(workspace, sample_image, tmp_path, new=True, title="Target")
    target_page = target.pages[0]
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: source.tab_bar)
    point = source.tab_bar.mapToGlobal(source.tab_bar.tabRect(0).topLeft())
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_released.emit(point)
    assert workspace.move(source, page, target)
    with qtbot.captureExceptions() as errors:
        qtbot.wait(10)
    assert errors == []
    assert source.pages == (neighbor,)
    assert target.pages == (target_page, page)
    assert workspace.host_of(page.record.identifier) is target
    assert len(page.record.session.document.annotations) == 1
    assert len(workspace.documents) == 3


@pytest.mark.parametrize("destination", ["same_slot", "interior", "other_host"])
def test_queued_drop_preserves_exact_interior_order(  # noqa: PLR0913 - event loop and explicit policy
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    destination: str,
) -> None:
    """Interior insertion and dropping beside oneself differ from end clamping."""
    source = open_document(workspace, sample_image, tmp_path)
    for title in ("B", "C", "D"):
        open_document(workspace, sample_image, tmp_path, title=title)
    before = source.pages
    page = before[1]
    target = source
    index = 1 if destination == "same_slot" else 3
    expected = (
        before
        if destination == "same_slot"
        else (before[0], before[2], before[1], before[3])
    )
    if destination == "other_host":
        target = open_document(workspace, sample_image, tmp_path, new=True, title="X")
        workspace.activated(target)
        for title in ("Y", "Z"):
            assert (
                open_document(workspace, sample_image, tmp_path, title=title) is target
            )
        index = 2
        expected = (*target.pages[:2], page, *target.pages[2:])
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: target.tab_bar)
    monkeypatch.setattr(target.tab_bar, "insertion_at", lambda _point: index)
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_released.emit(QPoint(50, 50))
    qtbot.wait(10)
    assert target.pages == expected
    assert workspace.host_of(page.record.identifier) is target
    assert len(workspace.documents) == (7 if destination == "other_host" else 4)
    if target is not source:
        assert source.pages == (before[0], before[2], before[3])


def test_drop_on_actual_tab_close_child_targets_its_host(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close-button child under the pointer still belongs to the tab strip."""
    host = open_document(workspace, sample_image, tmp_path)
    bar = host.tab_bar
    buttons = (
        cast(QWidget | None, bar.tabButton(0, side))
        for side in (bar.ButtonPosition.LeftSide, bar.ButtonPosition.RightSide)
    )
    button = next((widget for widget in buttons if widget is not None), None)
    assert button is not None
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: button)
    assert workspace._target_at(QPoint(50, 50)) is host


def test_presentation_timer_stops_with_destroyed_workspace(qtbot: QtBot) -> None:
    """An active retry cannot outlive its owning workspace during teardown."""
    owner = Workspace()
    qtbot.addWidget(owner.parking)
    destroyed = QSignalSpy(owner._presentation_timer.destroyed)
    owner._presentation_timer.start(5000)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed.count() == 1


def test_recovery_constructor_failure_retains_original_ownership(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure before a new host is returned leaves parked work retryable."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    source.take_page(page)
    windows = workspace.windows

    def allocation_failed(*_args: object, **_kwargs: object) -> None:
        raise MemoryError("Host constructor allocation failed")

    with monkeypatch.context() as patch:
        patch.setattr(EditorWindow, "__init__", allocation_failed)
        assert not workspace.recover_retained()
    assert workspace.documents == (page.record,)
    assert workspace.windows == windows
    assert workspace.host_of(page.record.identifier) is None
    assert workspace.recover_retained()
    recovered = workspace.host_of(page.record.identifier)
    assert recovered is not None
    assert recovered.pages == (page,)


def test_detach_drop_clamps_to_screen_and_reorder_drop_keeps_identity(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside drops create a usable host; a same-bar drop only reorders."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Neighbor")
    qtbot.wait(0)
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: source.tab_bar)
    point = source.tab_bar.mapToGlobal(QPoint(source.tab_bar.width() - 1, 0))
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_released.emit(point)
    qtbot.wait(10)
    assert source.pages[-1] is page
    monkeypatch.setattr(QApplication, "widgetAt", lambda _point: None)
    source.tab_bar.drag_started.emit(page.record.identifier)
    source.tab_bar.drag_moved.emit(QPoint(-100000, -100000))
    source.tab_bar.drag_released.emit(QPoint(-100000, -100000))
    qtbot.wait(10)
    host = workspace.host_of(page.record.identifier)
    assert host is not None
    assert host is not source
    assert host.screen().availableGeometry().contains(host.geometry())


def test_context_menu_move_and_close_resolve_stable_page(
    workspace: Workspace, sample_image: QImage, tmp_path: Path, qtbot: QtBot
) -> None:
    """Keyboard context actions follow the chosen page after current-tab changes."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Other")
    target = open_document(workspace, sample_image, tmp_path, new=True)
    workspace.show_tab_menu(source, page, source.mapToGlobal(QPoint(10, 10)))
    # Resolve the actual popup rather than an incidental child-menu index.
    active = QApplication.activePopupWidget()
    assert isinstance(active, QMenu)
    menu = active
    move = next(
        action.menu() for action in menu.actions() if action.text() == "Move to window"
    )
    assert isinstance(move, QMenu)
    move.actions()[0].trigger()
    menu.close()
    qtbot.wait(0)
    assert workspace.host_of(page.record.identifier) is target
    workspace.show_tab_menu(target, page, object())
    menu = [
        item
        for item in target.findChildren(QMenu)
        if any(a.text() == "Close tab" for a in item.actions())
    ][-1]
    menu.actions()[0].trigger()
    assert page.record not in workspace.documents


def test_window_menu_refresh_has_no_duplicate_targets(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Reopening the menu exposes current tabs/hosts and one recovery command."""
    source = open_document(workspace, sample_image, tmp_path)
    target = open_document(workspace, sample_image, tmp_path, new=True)
    menu = QMenu(source)
    for label in ("Next", "Previous", "Left", "Right", "Detach"):
        menu.addAction(label)
    for _ in range(2):
        workspace.populate_window_menu(menu, source)
        assert sum(a.text() == "Recover retained tabs" for a in menu.actions()) == 1
        move = next(
            a.menu() for a in menu.actions() if a.text() == "Move tab to window"
        )
        assert isinstance(move, QMenu)
        assert len(move.actions()) == 1
    move.actions()[0].trigger()
    assert len(target.pages) == 2


def test_stale_drag_and_ineligible_hosts_cannot_mutate_ownership(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Closed/busy hosts and unknown IDs never start a structural operation."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    target = open_document(workspace, sample_image, tmp_path, new=True)
    source.tab_bar.drag_started.emit("unknown-document")
    target.begin_close_plan()
    try:
        assert not workspace.move(source, page, target)
    finally:
        target.end_close_plan()
    source.begin_close_plan()
    try:
        source.tab_bar.drag_started.emit(page.record.identifier)
        assert not workspace.move(source, page, target)
    finally:
        source.end_close_plan()
    assert workspace.host_of(page.record.identifier) is source


@pytest.mark.parametrize(
    "scenario", [(False, False), (True, False), (False, True), (True, True)]
)
def test_failed_transfer_restores_both_active_tabs_and_host_registry(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: tuple[bool, bool],
) -> None:
    """Rollback restores presentation as well as the moved page's content."""
    new_target, active_moved = scenario
    from verdiclip.editor.transfer import TabTransfer  # noqa: PLC0415 - failure seam

    source = open_document(workspace, sample_image, tmp_path)
    moved = source.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Source active")
    source_active = moved if active_moved else source.pages[1]
    source.activate_page(source_active)
    target = None
    target_active = None
    if not new_target:
        target = open_document(workspace, sample_image, tmp_path, new=True)
        workspace.activated(target)
        open_document(workspace, sample_image, tmp_path, title="Target active")
        target_active = target.pages[-1]
    expected_windows = set(workspace.windows)

    def fail(*_args: object) -> None:
        raise RuntimeError("Injected verification failure")

    monkeypatch.setattr(TabTransfer, "_verify", fail)
    failures: list[str] = []
    workspace.transfer_failed.connect(failures.append)
    assert not workspace.move(source, moved, target, 0, QPoint(-1000, -1000))
    assert source.pages[0] is moved
    assert source.session is source_active.record.session
    if target is not None:
        assert target_active is not None
        assert target.session is target_active.record.session
        assert moved not in target.pages
    assert set(workspace.windows) == expected_windows
    assert all(
        workspace.host_of(record.identifier) is not None
        for record in workspace.documents
    )
    assert failures
    assert "document is retained" in failures[0]
    assert source.statusBar().currentMessage() == failures[0]


def test_move_selects_transferred_page_and_preserves_close_preference(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """A successful move exposes the moved work and preserves disabled prompts."""
    source = open_document(workspace, sample_image, tmp_path)
    page = source.pages[0]
    target = open_document(workspace, sample_image, tmp_path, new=True)
    assert workspace.move(source, page, target, 0)
    assert target.session is page.record.session
    target.set_confirm_close(confirm=False)
    assert workspace.move(target, page)
    detached = workspace.host_of(page.record.identifier)
    assert detached is not None
    assert detached.confirm_close is False


def test_detached_placement_clamps_both_far_edges_exactly(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requested logical bounds must fit the selected screen's far edges."""
    from verdiclip.editor.transfer import TabTransfer  # noqa: PLC0415 - geometry policy

    source = open_document(workspace, sample_image, tmp_path)
    target = open_document(workspace, sample_image, tmp_path, new=True)
    area = source.screen().availableGeometry()
    requested: list[QRect] = []
    original = target.move

    def record_position(x: int, y: int) -> None:
        requested.append(QRect(x, y, target.width(), target.height()))
        original(x, y)

    monkeypatch.setattr(target, "move", record_position)
    transfer = TabTransfer(source, source.pages[0], target, index=None, position=None)
    transfer._position(target, QPoint(area.right() + 10000, area.bottom() + 10000))
    assert requested[-1].right() == area.right()
    assert requested[-1].bottom() == area.bottom()
    assert area.contains(requested[-1])


@pytest.mark.parametrize("inline", [False, True])
def test_global_close_rejects_edits_after_an_earlier_approval(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    inline: bool,
) -> None:
    """A later modal prompt cannot authorize newly changed earlier work."""
    open_document(workspace, sample_image, tmp_path, delivered=False)
    open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    first = workspace.windows[0].pages[0]
    calls = 0

    def decide(*_args: object) -> CloseChoice:
        nonlocal calls
        calls += 1
        if calls == 2:
            if inline:
                first.edit_text(None, Point(5, 5))
                widget = first.canvas.editors.widget
                assert isinstance(widget, QPlainTextEdit)
                widget.setPlainText("Late unsaved typing")
                assert first.record.session.history.state_id == 0
            else:
                add_rectangle(first)
        return CloseChoice.DISCARD

    monkeypatch.setattr(ClosePrompt, "ask", decide)
    assert not workspace.close_all()
    assert len(workspace.documents) == 2
    assert all(host.isVisible() for host in workspace.windows)
    assert first.record.session.document.annotations
    assert not first.record.session.history.is_delivered


def test_host_and_tab_close_reject_changed_prompt_states(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-host and single-tab paths also validate the discarded version."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    page = window.pages[0]

    def edit_then_discard(*_args: object) -> CloseChoice:
        add_rectangle(page)
        return CloseChoice.DISCARD

    monkeypatch.setattr(ClosePrompt, "ask", edit_then_discard)
    assert not window.close_tab()
    closed = window.close()
    assert not closed
    assert window.pages == (page,)
    assert len(page.record.session.document.annotations) == 2


def test_copy_receipt_stays_with_origin_during_clipboard_reentrancy(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """Clipboard dataChanged cannot mark an unrelated tab copied/delivered."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    first = window.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Other", delivered=False)
    second = window.pages[1]
    window.activate_page(first)
    clipboard = QGuiApplication.clipboard()

    def select_other() -> None:
        window.activate_page(second)

    clipboard.dataChanged.connect(select_other)
    try:
        assert window.copy_image()
        assert first.record.session.history.is_delivered
        assert first.record.copied
        assert not second.record.session.history.is_delivered
        assert not second.record.copied
    finally:
        clipboard.dataChanged.disconnect(select_other)


def test_enter_closes_origin_even_when_copy_status_selects_another_tab(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """A post-delivery status callback cannot redirect Enter's close action."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    first = window.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Other", delivered=False)
    second = window.pages[1]
    window.activate_page(first)

    def select_other(_message: str) -> None:
        window.activate_page(second)

    window.statusBar().messageChanged.connect(select_other)
    try:
        window._on_enter()
        assert window.pages == (second,)
        assert first.record not in workspace.documents
        assert not second.record.session.history.is_delivered
    finally:
        window.statusBar().messageChanged.disconnect(select_other)


def test_saved_snapshot_cannot_clean_edits_created_during_encoding(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reentrant encoder result belongs to the encoded state, not later edits."""
    window = open_document(workspace, sample_image, tmp_path, delivered=False)
    page = window.pages[0]
    original = ImageDelivery.save

    def save_then_edit(delivery: ImageDelivery, image: QImage, path: Path) -> Path:
        result = original(delivery, image, path)
        add_rectangle(page)
        return result

    monkeypatch.setattr(ImageDelivery, "save", save_then_edit)
    assert window.save()
    assert page.record.saved_path is not None
    assert not page.record.session.history.is_delivered
    assert page.record.session.document.annotations


def test_global_close_cancel_stops_before_any_later_host_prompt(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel ends preflight immediately without asking unrelated remaining work."""
    open_document(workspace, sample_image, tmp_path, delivered=False)
    open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    calls: list[bool] = []

    def cancel(*_args: object) -> CloseChoice:
        calls.append(True)
        assert len(calls) == 1
        return CloseChoice.CANCEL

    monkeypatch.setattr(ClosePrompt, "ask", cancel)
    assert not workspace.close_all()
    assert len(workspace.documents) == 2


@pytest.mark.parametrize("scope", ["global", "host", "tab"])
@pytest.mark.parametrize("inline", [False, True])
def test_close_detachment_callbacks_preserve_newly_committed_edits(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    scope: str,
    *,
    inline: bool,
) -> None:
    """All selected pages stage before detach signals can authorize disposal."""
    host = open_document(workspace, sample_image, tmp_path, delivered=False)
    first = host.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Neighbor", delivered=False)
    original_pages = host.pages
    original_active = host.session
    if scope == "global":
        open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    original_records = workspace.documents
    changed: list[bool] = []

    def late_edit(_index: int) -> None:
        if (
            not changed
            and first not in host.pages
            and first.record in workspace.documents
        ):
            changed.append(True)
            if inline:
                first.edit_text(None, Point(5, 5))
                widget = first.canvas.editors.widget
                assert isinstance(widget, QPlainTextEdit)
                widget.setPlainText("Typing created during detachment")
            else:
                add_rectangle(first)

    host.tab_bar.currentChanged.connect(late_edit)
    try:
        if scope == "global":
            closed = workspace.close_all()
        elif scope == "host":
            closed = host.close()
        else:
            closed = host.close_tab(0)
        assert not closed
        assert changed == [True]
        assert workspace.documents == original_records
        assert host.pages == original_pages
        assert host.session is original_active
        assert first.record.session.document.annotations
        assert not first.record.session.history.is_delivered
        assert all(
            workspace.host_of(record.identifier) is not None
            for record in original_records
        )
    finally:
        host.tab_bar.currentChanged.disconnect(late_edit)


def test_global_close_rechecks_external_guard_after_detachment(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """New retained output during detachment vetoes the entire close commit."""
    host = open_document(workspace, sample_image, tmp_path, delivered=False)
    open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    records = workspace.documents
    external_work: list[bool] = []

    def add_work(_index: int) -> None:
        if not host.pages:
            external_work.append(True)

    host.tab_bar.currentChanged.connect(add_work)
    try:
        assert not workspace.close_all(can_commit=lambda: not external_work)
        assert external_work
        assert workspace.documents == records
        assert all(
            workspace.host_of(record.identifier) is not None for record in records
        )
    finally:
        host.tab_bar.currentChanged.disconnect(add_work)


def test_global_close_stages_all_hosts_before_any_disposal(
    workspace: Workspace, sample_image: QImage, tmp_path: Path
) -> None:
    """A later host's detach callback can still preserve work from an earlier host."""
    first = open_document(workspace, sample_image, tmp_path, delivered=False)
    second = open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    earlier, later = workspace.windows
    earlier_page = earlier.pages[0]
    records = workspace.documents
    changed: list[bool] = []

    def late_edit(_index: int) -> None:
        if not changed and not later.pages:
            assert earlier_page.record in workspace.documents
            changed.append(True)
            add_rectangle(earlier_page)

    later.tab_bar.currentChanged.connect(late_edit)
    try:
        assert not workspace.close_all()
        assert changed == [True]
        assert workspace.documents == records
        assert first.isVisible()
        assert second.isVisible()
        assert earlier_page.record.session.document.annotations
    finally:
        later.tab_bar.currentChanged.disconnect(late_edit)


@pytest.mark.parametrize("after_removal", [False, True])
def test_failed_close_staging_restores_every_page_without_disposal(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    after_removal: bool,
) -> None:
    """An allocation/removal failure never partially closes approved documents."""
    first = open_document(workspace, sample_image, tmp_path, delivered=False)
    open_document(workspace, sample_image, tmp_path, new=True, delivered=False)
    later = workspace.windows[-1]
    original = later.take_page
    records = workspace.documents

    def failing_take(page: DocumentPage) -> int:
        if after_removal:
            original(page)
        raise MemoryError("Injected close staging failure")

    monkeypatch.setattr(later, "take_page", failing_take)
    assert not workspace.close_all()
    assert workspace.documents == records
    assert not workspace.transferring
    assert all(workspace.host_of(record.identifier) is not None for record in records)
    assert first.isVisible()
    monkeypatch.setattr(later, "take_page", original)


def test_close_rollback_recovery_retains_documents_and_releases_job_reservations(
    workspace: Workspace,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even failed restoration keeps work recoverable and live jobs unfrozen."""
    host = open_document(workspace, sample_image, tmp_path, delivered=False)
    page = host.pages[0]
    open_document(workspace, sample_image, tmp_path, title="Neighbor", delivered=False)
    records = workspace.documents
    original = EditorWindow.attach_page
    edited: list[bool] = []

    def late_edit(_index: int) -> None:
        if not edited and page not in host.pages:
            edited.append(True)
            add_rectangle(page)

    def fail(*_args: object) -> None:
        raise MemoryError("Injected close restoration allocation failure")

    host.tab_bar.currentChanged.connect(late_edit)
    monkeypatch.setattr(EditorWindow, "attach_page", fail)
    try:
        assert not workspace.close_all()
        assert workspace.documents == records
        assert not host.pages
        assert all(workspace.host_of(record.identifier) is None for record in records)
        menu = QMenu(host)
        for number in range(5):
            menu.addAction(str(number))
        workspace.populate_window_menu(menu, host)
        assert any(
            action.text() == "Recover retained tabs" for action in menu.actions()
        )
        for record in records:
            ticket = record.jobs.begin(JobKind.ANALYSIS)
            assert record.jobs.finish(ticket) is JobOutcome.CURRENT
        monkeypatch.setattr(EditorWindow, "attach_page", original)
        assert workspace.recover_retained()
        assert all(
            workspace.host_of(record.identifier) is not None for record in records
        )
        assert page.record.session.document.annotations
    finally:
        monkeypatch.setattr(EditorWindow, "attach_page", original)
        host.tab_bar.currentChanged.disconnect(late_edit)
