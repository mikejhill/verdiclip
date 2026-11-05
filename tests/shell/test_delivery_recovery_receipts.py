"""User-facing recovery and explicit-host receipts over private image output."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog, QWidget
from tests.shell.test_reviewed_delivery import capture
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.document.style import Style
from verdiclip.editor.contracts import EditorOptions
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import IMAGE_FILTER, EditorWindow
from verdiclip.exceptions import DeliveryError
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import CaptureSettings, Settings
from verdiclip.shell.controller import CaptureDelivery


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Keep OS integration and output paths inside the existing fake shell."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


def test_notification_click_retries_same_original_without_resaving(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry action settles retained pixels instead of opening a folder."""
    app = factory(
        Settings(
            capture=CaptureSettings(
                open_editor=False, copy_to_clipboard=True, save_to_file=True
            )
        )
    )
    saves: list[Path] = []
    copies: list[QImage] = []
    opened: list[object] = []
    fail = True
    save = ImageDelivery.save

    def saving(delivery: ImageDelivery, image: QImage, path: Path) -> Path:
        saves.append(path)
        return save(delivery, image, path)

    def copying(_delivery: ImageDelivery, image: QImage) -> None:
        copies.append(image)
        if fail:
            raise DeliveryError("Clipboard temporarily busy")

    monkeypatch.setattr(ImageDelivery, "save", saving)
    monkeypatch.setattr(ImageDelivery, "copy", copying)
    monkeypatch.setattr(QDesktopServices, "openUrl", opened.append)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    assert app.controller.pending_deliveries == (result,)
    assert not app.controller.editors
    fail = False
    app.controller.tray.messageClicked.emit()
    assert app.controller.pending_deliveries == ()
    assert copies == [result.image, result.image]
    assert len(saves) == 1
    assert saves[0].is_file()
    assert opened == []


@pytest.mark.parametrize("origin", ["reviewed", "host_open"])
def test_explicit_non_mru_host_receives_one_document(
    factory: AppFactory, sample_image: QImage, tmp_path: Path, origin: str
) -> None:
    """An explicit target wins over MRU; host signals retain their own identity."""
    app = factory(Settings(capture=CaptureSettings(open_editor=True)))
    hosts = []
    for title in ("Requested", "Other MRU"):
        session = EditorSession(
            Document(sample_image), app.controller.settings.editor, delivered=True
        )
        hosts.append(
            app.controller.workspace.open_document(
                session,
                ImageDelivery(app.controller.settings.output),
                EditorOptions(title=title),
                new_window=True,
                activate=False,
            )
        )
    requested, other = hosts
    active = requested.pages[requested.tab_bar.currentIndex()]
    app.controller.workspace.activated(other)
    assert app.controller.workspace.windows[0] is other
    if origin == "reviewed":
        result = app.controller.deliver_reviewed_capture(
            capture(sample_image), target=requested
        )
        assert result is requested
        assert requested.pages[requested.tab_bar.currentIndex()] is active
        assert app.controller.workspace.windows[0] is other
    else:
        path = tmp_path / "opened.png"
        assert sample_image.save(str(path))
        requested.open_image_requested.emit(path)
    assert len(requested.pages) == 2
    assert len(other.pages) == 1
    assert len(app.controller.workspace.documents) == 3


@pytest.mark.parametrize("edited", [False, True])
def test_late_save_receipt_keeps_path_and_current_dirty_state(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    *,
    edited: bool,
) -> None:
    """Retrying an original save establishes its path without cleaning newer edits."""
    app = factory(
        Settings(capture=CaptureSettings(open_editor=True, save_to_file=True))
    )
    save = ImageDelivery.save
    fail = True
    paths: list[Path] = []

    def saving(delivery: ImageDelivery, image: QImage, path: Path) -> Path:
        if fail:
            raise DeliveryError("Destination temporarily unavailable")
        paths.append(path)
        return save(delivery, image, path)

    monkeypatch.setattr(ImageDelivery, "save", saving)
    result = capture(sample_image)
    host = app.controller.deliver_reviewed_capture(result)
    assert host is not None
    record = host.pages[host.tab_bar.currentIndex()].record
    assert record.saved_path is None
    assert not record.session.history.is_delivered
    if edited:
        record.session.history.execute(
            AddAnnotations(
                [RectangleShape(rect=Rect(10, 10, 40, 30), style=Style())], "Rectangle"
            )
        )
    fail = False
    app.controller.retry_delivery(result.capture_id)
    assert len(paths) == 1
    assert record.saved_path == paths[0]
    assert record.session.history.is_delivered is not edited
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: pytest.fail("Plain Save must reuse its known path"),
    )
    host.save()
    assert paths == [paths[0], paths[0]]
    assert record.session.history.is_delivered
    stored = QImage(str(paths[0])).convertToFormat(QImage.Format.Format_ARGB32)
    expected = host.flattened().convertToFormat(QImage.Format.Format_ARGB32)
    assert stored == expected


def test_initial_save_path_survives_partial_admission_failure(
    factory: AppFactory, sample_image: QImage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A view callback failure cannot erase an already successful save receipt."""
    app = factory(
        Settings(capture=CaptureSettings(open_editor=True, save_to_file=True))
    )
    original = app.controller._open_document

    def interrupted(
        image: QImage,
        options: EditorOptions,
        *,
        delivered: bool,
        target: EditorWindow | None = None,
        activate: bool = True,
    ) -> None:
        original(image, options, delivered=delivered, target=target, activate=activate)
        raise MemoryError("Presentation callback interrupted admission")

    monkeypatch.setattr(app.controller, "_open_document", interrupted)
    result = capture(sample_image)
    app.controller.deliver_reviewed_capture(result)
    assert app.controller.pending_deliveries == (result,)
    assert len(app.controller.workspace.documents) == 1
    record = app.controller.workspace.documents[0]
    path = record.saved_path
    assert path is not None
    assert path.is_file()
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: pytest.fail("Saved capture must keep Plain Save"),
    )
    host = app.controller.workspace.host_of(record.identifier)
    assert host is not None
    host.save()
    assert record.saved_path == path
    monkeypatch.setattr(app.controller, "_open_document", original)
    app.controller.retry_delivery(result.capture_id)
    assert app.controller.pending_deliveries == ()
    assert app.controller.workspace.documents == (record,)


@pytest.mark.parametrize("origin", ["saved", "automatic", "fallback"])
def test_retained_save_dialog_preserves_original_path_contract(
    factory: AppFactory,
    sample_image: QImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
) -> None:
    """Cancel keeps the original while the picker receives its correct start path."""
    app = factory()
    result = capture(sample_image, "Retained original")
    receipt = tmp_path / "existing receipt.png" if origin == "saved" else None
    attempt = CaptureDelivery(
        result,
        app.controller.settings.capture,
        app.controller.settings.output,
        target=None,
        activate_editor=False,
        saved=receipt,
    )
    automatic = tmp_path / "original timestamp.png"
    expected = (
        receipt
        if receipt
        else app.controller.settings.output.directory / "Screenshot.png"
        if origin == "fallback"
        else automatic
    )
    calls = []

    def auto_path(
        _delivery: ImageDelivery, *, title: str = "", moment: datetime | None = None
    ) -> Path:
        calls.append((title, moment))
        assert title == result.title
        assert moment == result.created_at
        if origin == "fallback":
            raise DeliveryError("No automatic filename")
        return automatic

    def choose(
        parent: QWidget | None, caption: str, directory: str, filters: str
    ) -> tuple[str, str]:
        assert parent is None
        assert caption == "Save retained capture"
        assert directory == str(expected)
        assert filters == IMAGE_FILTER
        return "", ""

    monkeypatch.setattr(ImageDelivery, "auto_path", auto_path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    saved = app.controller._save_retained_capture(attempt)
    assert not saved
    assert attempt.saved == receipt
    assert attempt.capture is result
    assert calls == ([] if receipt else [(result.title, result.created_at)])
