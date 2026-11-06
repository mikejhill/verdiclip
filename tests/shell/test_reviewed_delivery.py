"""Headless controller admission/output policy; no desktop capture or input."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QPlainTextEdit
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_core import Documents
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture
from verdiclip.document.admission import CaptureProvenance
from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.editor import workspace as workspace_module
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.session import ToolId
from verdiclip.editor.window import EditorWindow
from verdiclip.exceptions import DeliveryError
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import CaptureDestination, CaptureSettings, Settings
from verdiclip.shell import controller as controller_module


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Reuse app composition over fake shell/hotkey/screen services only."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


def capture(image: QImage, title: str = "Reviewed") -> ReviewedCapture:
    """Construct a producer-neutral result without running a capture backend."""
    return ReviewedCapture(
        image,
        title,
        datetime(2026, 10, 25, 12, tzinfo=UTC),
        CaptureKind.SCROLLING,
        CaptureCompleteness.PARTIAL,
        "User accepted reviewed partial",
    )


@pytest.mark.parametrize(
    "mode",
    [
        (False, False, True),
        (False, True, False),
        (True, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_reviewed_result_obeys_all_existing_output_combinations(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[bool, bool, bool],
) -> None:
    """Choosing tray-only output never forces an editor window to open."""
    save, copy, edit = mode
    settings = replace(
        Settings(),
        capture=CaptureSettings(
            save_to_file=save, copy_to_clipboard=copy, open_editor=edit
        ),
    )
    app = factory(settings)
    copied: list[QImage] = []
    monkeypatch.setattr(
        ImageDelivery, "copy", lambda _self, image: copied.append(image)
    )
    result = capture(sample_image)
    host = app.controller.deliver_reviewed_capture(result)
    assert len(copied) == int(copy)
    assert len(list((tmp_path / "out").glob("*.png"))) == int(save)
    assert len(app.controller.editors) == int(edit)
    assert app.controller.pending_deliveries == ()
    if edit:
        assert host is not None
        record = app.controller.workspace.documents[0]
        assert record.provenance == CaptureProvenance.from_capture(result)
        assert record.session.history.is_delivered is (save or copy)
        assert (record.saved_path is not None) is save
    else:
        assert host is None
    # A repeated producer callback cannot create another tab, file or copy.
    assert app.controller.deliver_reviewed_capture(result) is host
    assert len(copied) == int(copy)
    assert len(app.controller.workspace.documents) == int(edit)
    assert len(list((tmp_path / "out").glob("*.png"))) == int(save)


def test_failed_copy_does_not_mark_new_capture_delivered(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enabled clipboard option is not evidence of successful delivery."""
    app = factory(replace(Settings(), capture=CaptureSettings(copy_to_clipboard=True)))

    def fail(_self: ImageDelivery, _image: QImage) -> None:
        raise DeliveryError("Clipboard unavailable")

    monkeypatch.setattr(ImageDelivery, "copy", fail)
    result = capture(sample_image)
    host = app.controller.deliver_reviewed_capture(result)
    assert host is not None
    assert not host.session.history.is_delivered
    assert app.controller.pending_deliveries == (result,)
    record = app.controller.workspace.documents[0]
    record.session.history.execute(
        AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Draw")
    )
    monkeypatch.setattr(ImageDelivery, "copy", lambda *_args: None)
    app.controller.retry_delivery(result.capture_id)
    assert app.controller.pending_deliveries == ()
    assert len(app.controller.workspace.documents) == 1
    assert not record.session.history.is_delivered
    assert record.copied


def test_failed_tray_only_output_is_retained_and_retries_current_folder(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixing output settings can complete a failed capture without opening UI."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )
    original = ImageDelivery.save

    def fail(_self: ImageDelivery, _image: QImage, _path: Path) -> Path:
        raise DeliveryError("Folder unavailable")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    result = capture(sample_image)
    assert app.controller.deliver_reviewed_capture(result) is None
    assert app.controller.editors == ()
    assert app.controller.pending_deliveries == (result,)
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.CANCEL)
    assert not app.controller._close_editors()
    repaired = tmp_path / "repaired"
    app.controller.apply_settings(
        replace(
            app.controller.settings,
            output=replace(app.controller.settings.output, directory=repaired),
        )
    )
    monkeypatch.setattr(ImageDelivery, "save", original)
    app.controller.retry_delivery(result.capture_id)
    assert len(list(repaired.glob("*.png"))) == 1
    assert app.controller.editors == ()
    assert app.controller.pending_deliveries == ()
    app.controller.retry_delivery("not-a-live-retry")


def test_copy_still_runs_after_save_failure(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent after-capture sinks must not suppress each other's attempts."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(
                open_editor=True, save_to_file=True, copy_to_clipboard=True
            ),
        )
    )
    copied: list[QImage] = []

    def fail(_self: ImageDelivery, _image: QImage, _path: Path) -> Path:
        raise DeliveryError("Disk full")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    monkeypatch.setattr(
        ImageDelivery, "copy", lambda _self, image: copied.append(image)
    )
    result = capture(sample_image)
    host = app.controller.deliver_reviewed_capture(result)
    assert len(copied) == 1
    assert host is not None
    assert host.session.history.is_delivered
    assert app.controller.pending_deliveries == (result,)


def test_capture_grouping_detachment_and_global_cancel(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detached hosts remain controller-owned; global Cancel retains every tab."""
    app = factory()
    first = app.controller.deliver_reviewed_capture(capture(sample_image, "One"))
    second = app.controller.deliver_reviewed_capture(capture(sample_image, "Two"))
    assert first is not None
    assert second is first
    assert len(first.pages) == 2
    page = first.pages[1]
    assert app.controller.workspace.move(first, page)
    assert len(app.controller.editors) == 2
    detached = next(host for host in app.controller.editors if host is not first)
    assert detached.pages == (page,)
    answers = iter([CloseChoice.DISCARD, CloseChoice.CANCEL])
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: next(answers))
    assert not app.controller._close_editors()
    assert len(app.controller.workspace.documents) == 2
    assert all(host.isVisible() for host in app.controller.editors)


def test_admission_retry_does_not_duplicate_partially_attached_document(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late show/attachment failure keeps the already registered session."""
    app = factory()
    original = app.controller.workspace.open_document
    original_document = controller_module.Document
    allocations: list[QImage] = []

    def allocate(image: QImage) -> Document:
        allocations.append(image)
        return original_document(image)

    monkeypatch.setattr(controller_module, "Document", allocate)
    attempts = 0

    def fail_after(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        host = original(*args, **kwargs)  # ty: ignore[invalid-argument-type] - injected factory boundary
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Injected after admission")
        return host

    monkeypatch.setattr(app.controller.workspace, "open_document", fail_after)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    assert app.controller.pending_deliveries == (result,)
    assert len(app.controller.workspace.documents) == 1
    app.controller.retry_delivery(result.capture_id)
    assert len(app.controller.workspace.documents) == 1
    assert len(app.controller.editors) == 1
    assert app.controller.pending_deliveries == ()
    assert len(allocations) == 1


def test_scrolling_acceptance_reuses_identity_through_delivery_and_move(
    factory: AppFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual scrolling producer crosses the same document delivery seam."""
    session, _source = Documents.start(tmp_path / "spool")
    copied: list[QImage] = []
    monkeypatch.setattr(
        ImageDelivery, "copy", lambda _self, image: copied.append(image)
    )
    app = factory(replace(Settings(), capture=CaptureSettings(copy_to_clipboard=True)))
    try:
        session.stop()
        accepted = session.accept()
        assert session.accept() is accepted
        host = app.controller.deliver_reviewed_capture(accepted)
        assert host is not None
        page = host.pages[0]
        assert page.record.provenance == CaptureProvenance.from_capture(accepted)
        assert app.controller.workspace.move(host, page)
        moved = app.controller.workspace.host_of(page.record.identifier)
        assert moved is not None
        assert app.controller.deliver_reviewed_capture(session.accept()) is moved
        assert len(copied) == 1
        assert len(app.controller.workspace.documents) == 1
        session.close()
        assert (
            moved.flattened().convertToFormat(accepted.image.format()) == accepted.image
        )
        assert page.record.provenance.completeness is CaptureCompleteness.PARTIAL
    finally:
        session.close()


def test_equal_pixels_with_distinct_capture_ids_remain_separate_documents(
    factory: AppFactory, sample_image: QImage
) -> None:
    """Content equality never substitutes for producer acceptance identity."""
    app = factory()
    first = capture(sample_image)
    second = capture(sample_image)
    assert first.capture_id != second.capture_id
    app.controller.deliver_reviewed_capture(first)
    app.controller.deliver_reviewed_capture(second)
    assert len(app.controller.workspace.documents) == 2
    assert len(app.controller.workspace.windows) == 1


def test_ordinary_capture_adapter_uses_canonical_complete_screenshot(
    factory: AppFactory, sample_image: QImage
) -> None:
    """Ordinary capture metadata migrates without importing scrolling plans."""
    app = factory()
    created_at = datetime(2026, 10, 25, 12, tzinfo=UTC)
    ordinary = Capture(
        sample_image, CaptureMode.REGION, Rect(0, 0, 400, 300), "Region", created_at
    )
    app.controller._on_captured(ordinary)
    record = app.controller.workspace.documents[0]
    assert record.provenance is not None
    assert record.provenance.source_kind is CaptureKind.SCREENSHOT
    assert record.provenance.completeness is CaptureCompleteness.COMPLETE
    assert record.provenance.created_at is created_at
    assert record.provenance.stop_reason is None


@pytest.mark.parametrize("callback", ["retry", "duplicate", "close"])
def test_reentrant_capture_delivery_is_reserved_once(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    callback: str,
) -> None:
    """Synchronous clipboard callbacks cannot repeat or abandon active output."""
    app = factory(replace(Settings(), capture=CaptureSettings(copy_to_clipboard=True)))
    result = capture(sample_image)
    clipboard = QGuiApplication.clipboard()
    copied: list[QImage] = []
    original = ImageDelivery.copy
    connected = True
    close_results: list[bool] = []

    def count_copy(delivery: ImageDelivery, image: QImage) -> None:
        copied.append(image)
        original(delivery, image)

    def reenter() -> None:
        nonlocal connected
        clipboard.dataChanged.disconnect(reenter)
        connected = False
        if callback == "retry":
            app.controller.retry_delivery(result.capture_id)
        elif callback == "duplicate":
            app.controller.deliver_reviewed_capture(result)
        else:
            close_results.append(app.controller._close_editors())

    monkeypatch.setattr(ImageDelivery, "copy", count_copy)
    clipboard.dataChanged.connect(reenter)
    try:
        host = app.controller.deliver_reviewed_capture(result)
        assert host is not None
        assert len(copied) == 1
        assert len(app.controller.workspace.documents) == 1
        assert app.controller.pending_deliveries == ()
        assert host.session.history.is_delivered
        assert close_results == ([False] if callback == "close" else [])
    finally:
        if connected:
            clipboard.dataChanged.disconnect(reenter)


def test_pending_discard_waits_for_all_editor_close_decisions(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later document Cancel retains even a pending capture approved earlier."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )

    def fail(*_args: object) -> Path:
        raise DeliveryError("Unavailable output")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    host = app.controller.open_editor(sample_image, delivered=False)
    answers = iter([CloseChoice.DISCARD, CloseChoice.CANCEL])
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: next(answers))
    assert not app.controller._close_editors()
    assert app.controller.pending_deliveries == (result,)
    assert host.isVisible()
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.DISCARD)
    assert app.controller._close_editors()
    assert app.controller.pending_deliveries == ()
    assert not app.controller.workspace.has_work
    # An explicit discard cannot be undone by a late duplicate producer callback.
    app.controller.deliver_reviewed_capture(result)
    assert app.controller.pending_deliveries == ()


@pytest.mark.parametrize("outcome", ["cancel", "failed", "saved"])
def test_pending_close_can_save_or_preserve_original_on_failure(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Exit offers a real save path without forcing an editor or silent discard."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )
    original = ImageDelivery.save

    def fail(*_args: object) -> Path:
        raise DeliveryError("Unavailable output")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    destination = tmp_path / "explicit.png"
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.SAVE)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args: ("" if outcome == "cancel" else str(destination), "PNG"),
    )
    if outcome == "saved":
        monkeypatch.setattr(ImageDelivery, "save", original)
    assert app.controller._close_editors() is (outcome == "saved")
    assert app.controller.pending_deliveries == (
        () if outcome == "saved" else (result,)
    )
    assert destination.exists() is (outcome == "saved")
    assert app.controller.editors == ()


def test_new_pending_capture_during_close_invalidates_the_entire_plan(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tray-only admissions also veto disposal before any approved tab is lost."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )

    def fail(*_args: object) -> Path:
        raise DeliveryError("Unavailable output")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    first = capture(sample_image)
    incoming = capture(sample_image, "Incoming")
    app.controller.deliver_reviewed_capture(first)
    host = app.controller.open_editor(sample_image, delivered=False)
    calls = 0

    def decide(*_args: object) -> CloseChoice:
        nonlocal calls
        calls += 1
        if calls == 2:
            app.controller.deliver_reviewed_capture(incoming)
        return CloseChoice.DISCARD

    monkeypatch.setattr(ClosePrompt, "ask", decide)
    assert not app.controller._close_editors()
    assert app.controller.pending_deliveries == (first, incoming)
    assert host.isVisible()
    assert len(app.controller.workspace.documents) == 1


def test_pending_close_reentrant_exit_is_rejected(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parentless prompt cannot start a second overlapping exit transaction."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )

    def fail(*_args: object) -> Path:
        raise DeliveryError("Unavailable output")

    monkeypatch.setattr(ImageDelivery, "save", fail)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)

    def cancel(*_args: object) -> CloseChoice:
        assert not app.controller._close_editors()
        return CloseChoice.CANCEL

    monkeypatch.setattr(ClosePrompt, "ask", cancel)
    assert not app.controller._close_editors()
    assert app.controller.pending_deliveries == (result,)


def test_pending_manual_save_works_even_when_automatic_naming_is_broken(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit recovery path must not require repairing automatic naming first."""
    app = factory(
        replace(
            Settings(), capture=CaptureSettings(open_editor=False, save_to_file=True)
        )
    )

    def broken_name(*_args: object, **_kwargs: object) -> Path:
        raise DeliveryError("Automatic naming unavailable")

    monkeypatch.setattr(ImageDelivery, "auto_path", broken_name)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    destination = tmp_path / "manual.png"
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.SAVE)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_args: (str(destination), "PNG")
    )
    assert app.controller._close_editors()
    assert destination.is_file()
    assert app.controller.pending_deliveries == ()


def test_successful_reviewed_delivery_clears_retained_capture_failure(
    factory: AppFactory,
    sample_image: QImage,
) -> None:
    """A successful scrolling retry clears status without a captured signal."""
    app = factory(Settings())
    app.controller.tray.set_capture_error("Input was unavailable")
    assert app.controller.tray.action("capture_error").isVisible()
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    assert result.capture_id in app.controller._completed_deliveries
    assert not app.controller.tray.action("capture_error").isVisible()


def test_successive_captures_select_the_new_image(
    factory: AppFactory, sample_image: QImage
) -> None:
    """A second real delivery becomes visible without replacing the first document."""
    app = factory(Settings())
    first = capture(sample_image, "First screenshot")
    second_image = sample_image.copy()
    second_image.fill(0xFF123456)
    second = capture(second_image, "Second screenshot")
    host = app.controller.deliver_reviewed_capture(first)
    assert host is not None
    first_page = host.pages[host.tab_bar.currentIndex()]
    assert app.controller.deliver_reviewed_capture(second) is host
    assert len(host.pages) == 2
    selected = host.pages[host.tab_bar.currentIndex()]
    assert selected is not first_page
    assert selected.record.provenance is not None
    assert selected.record.provenance.capture_id == second.capture_id
    assert selected.record.session.document.image == second_image
    assert first_page.record.provenance is not None
    assert first_page.record.provenance.capture_id == first.capture_id


@pytest.mark.parametrize("destination", list(CaptureDestination))
def test_capture_destination_preserves_documents_and_retry_identity(
    factory: AppFactory, sample_image: QImage, destination: CaptureDestination
) -> None:
    """Each new capture follows placement policy; retry never duplicates a window."""
    app = factory(Settings(capture=CaptureSettings(destination=destination)))
    first = app.controller.deliver_reviewed_capture(capture(sample_image, "First"))
    result = capture(sample_image, "Second")
    second = app.controller.deliver_reviewed_capture(result)
    assert first is not None
    assert second is not None
    assert (first is second) == (destination is CaptureDestination.TABS)
    assert len(app.controller.workspace.documents) == 2
    assert second.session is second.pages[-1].record.session
    assert app.controller.deliver_reviewed_capture(result) is second
    assert len(app.controller.editors) == (
        1 if destination is CaptureDestination.TABS else 2
    )


@pytest.mark.parametrize("destination", list(CaptureDestination))
def test_repeated_window_hotkey_delivers_fresh_selected_pixels(
    factory: AppFactory, qtbot: QtBot, destination: CaptureDestination
) -> None:
    """Three real controller/service requests stay usable with an editor open."""
    app = factory(Settings(capture=CaptureSettings(destination=destination)))
    images: list[QImage] = []
    for index in range(3):
        app.press_hotkey("window")
        qtbot.waitUntil(
            lambda count=index + 1: len(app.controller.workspace.documents) == count
        )
        host = app.controller.editors[-1]
        images.append(host.session.document.image.copy())
        assert host.session is host.pages[-1].record.session
        assert not app.controller.capture.is_selecting
    assert images[0] != images[1]
    assert images[1] != images[2]
    assert len(app.controller.editors) == (
        1 if destination is CaptureDestination.TABS else 3
    )


def test_new_capture_preserves_open_inline_editor(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select the incoming capture in another host instead of closing live typing."""
    app = factory(Settings())
    original = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert original is not None
    page = original.pages[0]
    page.canvas.editors.edit_text(None, Point(20, 20))
    editor = page.canvas.editors.widget
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("Uncommitted typing")
    focus_requests: list[bool] = []
    monkeypatch.setattr(page.canvas, "setFocus", lambda: focus_requests.append(True))
    incoming = app.controller.deliver_reviewed_capture(
        capture(sample_image, "Incoming")
    )
    assert incoming is not None
    assert incoming is not original
    assert original.session is page.record.session
    assert page.canvas.editors.widget is editor
    assert editor.isVisible()
    assert editor.toPlainText() == "Uncommitted typing"
    assert focus_requests == []
    assert len(app.controller.workspace.documents) == 2


@pytest.mark.parametrize("failure", ["record", "page"])
def test_new_window_pre_admission_failure_retires_only_empty_hosts(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    failure: str,
) -> None:
    """Repeated allocation failures do not accumulate orphan windows or lose data."""
    app = factory(
        Settings(capture=CaptureSettings(destination=CaptureDestination.WINDOWS))
    )
    original = app.controller.deliver_reviewed_capture(
        capture(sample_image, "Original")
    )
    assert original is not None
    result = capture(sample_image, "Retry")
    add = EditorWindow.add_document

    def fail(*_args: object, **_kwargs: object) -> None:
        raise MemoryError("Before document registration")

    record_type = workspace_module.DocumentRecord
    if failure == "record":
        monkeypatch.setattr(workspace_module, "DocumentRecord", fail)
    else:
        monkeypatch.setattr(EditorWindow, "add_document", fail)
    for _attempt in range(2):
        app.controller.deliver_reviewed_capture(result)
        qtbot.waitUntil(lambda: app.controller.editors == (original,))
        assert app.controller.workspace.windows == (original,)
        assert len(app.controller.workspace.documents) == 1
        assert app.controller.pending_deliveries == (result,)
    monkeypatch.setattr(EditorWindow, "add_document", add)
    monkeypatch.setattr(workspace_module, "DocumentRecord", record_type)
    second = app.controller.deliver_reviewed_capture(result)
    assert second is not None
    assert second is not original
    assert len(app.controller.editors) == 2
    assert len(app.controller.workspace.documents) == 2
    assert app.controller.pending_deliveries == ()


def test_capture_preserves_editor_owned_combo_popup(
    factory: AppFactory, sample_image: QImage
) -> None:
    """Changing capture tabs must not dismiss an already-open editor control."""
    app = factory(Settings())
    host = app.controller.deliver_reviewed_capture(capture(sample_image))
    assert host is not None
    host.resize(1400, 900)
    host.activate_tool(ToolId.TEXT)
    QApplication.processEvents()
    combo = next(item for item in host.findChildren(QComboBox) if item.isVisible())
    combo.showPopup()
    popup = QApplication.activePopupWidget()
    assert popup is not None
    selected = combo.currentIndex()
    assert not host.accepts_capture
    try:
        incoming = app.controller.deliver_reviewed_capture(capture(sample_image, "New"))
        assert incoming is not None
        assert QApplication.activePopupWidget() is popup
        assert popup.isVisible()
        assert combo.currentIndex() == selected
        assert incoming.session is incoming.pages[-1].record.session
    finally:
        combo.hidePopup()
