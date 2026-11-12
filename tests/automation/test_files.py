"""Open, save and copy through the control pipe, and the set_image operation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage

from verdiclip.automation.handlers import AutomationHandlers, Opener
from verdiclip.automation.preview import PreviewFolder
from verdiclip.automation.protocol import (
    AutomationError,
    ErrorCode,
    JsonObject,
    Request,
)
from verdiclip.document.document import Document, DocumentState
from verdiclip.document.transform import ReplaceImage
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.exceptions import DeliveryError
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings

pytestmark = pytest.mark.usefixtures("qapp")

BOX = {"kind": "rectangle", "rect": [10, 10, 50, 40]}


@dataclass
class Page:
    """A tab holding one document."""

    record: DocumentRecord
    editing: bool = False


@dataclass
class Window:
    """An editor window showing its only tab."""

    pages: Sequence[Page]
    current_page: Page | None


@dataclass
class Host:
    """Open windows and documents; ``open`` adds a tab like the app does."""

    windows: list[Window] = field(default_factory=list)
    documents: list[DocumentRecord] = field(default_factory=list)


def make_record(image: QImage, folder: Path) -> DocumentRecord:
    """Return an unsaved capture whose automatic saves go to ``folder``."""
    session = EditorSession(Document(image), EditorSettings(), delivered=False)
    return DocumentRecord(
        session, ImageDelivery(OutputSettings(directory=folder)), title="Shot"
    )


@pytest.fixture
def record(sample_image: QImage, tmp_path: Path) -> DocumentRecord:
    """The one open document."""
    return make_record(sample_image, tmp_path / "out")


@pytest.fixture
def host(record: DocumentRecord) -> Host:
    """One window showing ``record``."""
    page = Page(record)
    return Host([Window([page], page)], [record])


def opener_for(host: Host, folder: Path) -> Opener:
    """Return an opener that reads the file and adds it as a new front tab."""

    def opener(path: Path) -> DocumentRecord:
        image = QImage(str(path))
        if image.isNull():
            msg = f"{path.name}: not an image"
            raise ValueError(msg)
        record = make_record(image, folder)
        record.title = path.stem
        record.saved_path = path
        record.session.history.mark_delivered()
        page = Page(record)
        host.windows.insert(0, Window([page], page))
        host.documents.append(record)
        return record

    return opener


@pytest.fixture
def handlers(host: Host, tmp_path: Path) -> AutomationHandlers:
    """Handlers that can open files."""
    return AutomationHandlers(
        host,
        previews=PreviewFolder(tmp_path / "previews"),
        opener=opener_for(host, tmp_path / "out"),
    )


def call(handlers: AutomationHandlers, method: str, **params: object) -> JsonObject:
    """Run one request."""
    return handlers(Request(method, dict(params)))  # ty: ignore[invalid-argument-type]


def write_png(path: Path, width: int, height: int, color: str) -> Path:
    """Write a solid PNG and return its path."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path))
    return path


# open


def test_open_adds_a_tab_and_describes_it(
    handlers: AutomationHandlers, host: Host, tmp_path: Path
) -> None:
    """Open returns the new document, which becomes the active one."""
    path = write_png(tmp_path / "pic.png", 30, 20, "blue")

    opened = call(handlers, "open", path=str(path))

    assert opened["title"] == "pic.png"
    assert opened["image"] == [30, 20]
    assert opened["unsaved"] is False
    assert call(handlers, "get")["id"] == opened["id"]
    assert len(host.documents) == 2


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (None, ErrorCode.BAD_REQUEST),
        ("relative.png", ErrorCode.BAD_REQUEST),
        ("MISSING", ErrorCode.NOT_FOUND),
        ("NOT_IMAGE", ErrorCode.FAILED),
    ],
)
def test_open_rejects_bad_paths(
    handlers: AutomationHandlers, tmp_path: Path, path: str | None, code: ErrorCode
) -> None:
    """Paths must be absolute, exist and hold an image."""
    junk = tmp_path / "junk.png"
    junk.write_text("not a png", encoding="utf-8")
    given = {
        "MISSING": str(tmp_path / "missing.png"),
        "NOT_IMAGE": str(junk),
    }.get(str(path), path)

    with pytest.raises(AutomationError) as caught:
        call(handlers, "open", path=given)

    assert caught.value.code is code


def test_open_needs_an_opener(host: Host, tmp_path: Path) -> None:
    """Without the app's opener, open fails plainly."""
    handlers = AutomationHandlers(host, previews=PreviewFolder(tmp_path))
    path = write_png(tmp_path / "pic.png", 4, 4, "red")

    with pytest.raises(AutomationError, match="can't open files") as caught:
        call(handlers, "open", path=str(path))

    assert caught.value.code is ErrorCode.FAILED


# save and copy


def test_save_writes_the_flattened_image_and_clears_unsaved(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """Save writes what the editor's Save would, then the tab shows saved."""
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX}])
    target = tmp_path / "result.png"

    saved = call(handlers, "save", path=str(target), expect_state=1)

    written = QImage(str(target))
    assert saved == {"id": record.identifier, "path": str(target), "state": 1}
    assert (written.width(), written.height()) == (400, 300)
    assert record.saved_path == target
    assert record.session.history.is_delivered


def test_save_defaults_to_the_documents_file_then_an_automatic_name(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """Without a path, Save reuses the saved file, else picks a new name."""
    first = Path(str(call(handlers, "save")["path"]))
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX}])
    second = Path(str(call(handlers, "save")["path"]))
    record.saved_path = tmp_path / "notes.txt"
    third = Path(str(call(handlers, "save")["path"]))

    assert first.parent == tmp_path / "out"
    assert first.is_file()
    assert second == first
    assert third.parent == tmp_path / "out"
    assert third.suffix == first.suffix


def test_save_failures(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """Bad paths, unknown formats and stale states fail without marking saved."""
    for path, code in (
        ("out.png", ErrorCode.BAD_REQUEST),
        (str(tmp_path / "out.xyz"), ErrorCode.FAILED),
    ):
        with pytest.raises(AutomationError) as caught:
            call(handlers, "save", path=path)
        assert caught.value.code is code
    with pytest.raises(AutomationError) as stale:
        call(handlers, "save", path=str(tmp_path / "a.png"), expect_state=5)

    assert stale.value.code is ErrorCode.CONFLICT
    assert not record.session.history.is_delivered


def test_copy_puts_the_image_on_the_clipboard(
    handlers: AutomationHandlers, record: DocumentRecord
) -> None:
    """Copy fills the clipboard and counts as delivering the image."""
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.clear()

    copied = call(handlers, "copy", expect_state=0)

    assert copied == {"id": record.identifier, "state": 0}
    assert clipboard.image().size().width() == 400
    assert record.copied
    assert record.session.history.is_delivered


def test_copy_failure_is_reported(
    handlers: AutomationHandlers,
    record: DocumentRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clipboard error fails the request and leaves the image unsaved."""

    def broken(_image: QImage) -> None:
        msg = "The system clipboard is not available"
        raise DeliveryError(msg)

    monkeypatch.setattr(record.delivery, "copy", broken)

    with pytest.raises(AutomationError, match="clipboard") as caught:
        call(handlers, "copy")

    assert caught.value.code is ErrorCode.FAILED
    assert not record.copied
    assert not record.session.history.is_delivered


def test_save_and_copy_wait_for_the_user(
    handlers: AutomationHandlers, host: Host, tmp_path: Path
) -> None:
    """Neither writes while the user is typing or dragging in the image."""
    host.windows[0].pages[0].editing = True

    for method, params in (("save", {"path": str(tmp_path / "x.png")}), ("copy", {})):
        with pytest.raises(AutomationError) as caught:
            call(handlers, method, **params)
        assert caught.value.code is ErrorCode.BUSY


# set_image


def test_set_image_same_size_keeps_crop_and_annotations(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """New pixels of the same size slot in under the annotations, one undo step."""
    path = write_png(tmp_path / "new.png", 400, 300, "green")
    call(
        handlers,
        "apply",
        ops=[
            {"op": "add", "annotation": BOX},
            {"op": "crop", "rect": [0, 0, 200, 100]},
        ],
    )

    result = call(handlers, "apply", ops=[{"op": "set_image", "path": str(path)}])

    document = record.session.document
    assert result["label"] == "CLI: set_image"
    assert document.image.pixelColor(5, 5) == QColor("green")
    assert document.crop == Rect(0, 0, 200, 100)
    assert len(document.annotations) == 1
    record.session.history.undo()
    assert document.image.pixelColor(5, 5) == QColor("white")


def test_set_image_new_size_shows_the_whole_image(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """A different size resets the crop to the new image."""
    path = write_png(tmp_path / "wide.png", 640, 120, "red")
    call(handlers, "apply", ops=[{"op": "crop", "rect": [0, 0, 50, 50]}])

    call(handlers, "apply", ops=[{"op": "set_image", "path": str(path)}])

    document = record.session.document
    assert document.image_rect == Rect(0, 0, 640, 120)
    assert document.crop == Rect(0, 0, 640, 120)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (3, ErrorCode.BAD_REQUEST),
        ("rel.png", ErrorCode.BAD_REQUEST),
        ("MISSING", ErrorCode.NOT_FOUND),
        ("NOT_IMAGE", ErrorCode.BAD_REQUEST),
    ],
)
def test_set_image_rejects_bad_files(
    handlers: AutomationHandlers,
    record: DocumentRecord,
    tmp_path: Path,
    path: object,
    code: ErrorCode,
) -> None:
    """The file must be an absolute path to a readable image."""
    junk = tmp_path / "junk.png"
    junk.write_text("nope", encoding="utf-8")
    given = {"MISSING": str(tmp_path / "gone.png"), "NOT_IMAGE": str(junk)}.get(
        str(path), path
    )

    with pytest.raises(AutomationError, match="Operation 1") as caught:
        call(handlers, "apply", ops=[{"op": "set_image", "path": given}])

    assert caught.value.code is code
    assert record.session.history.state_id == 0


def test_replace_image_rejects_empty_and_oversized_images() -> None:
    """The transform refuses images the document can't hold."""
    with pytest.raises(ValueError, match="empty"):
        ReplaceImage(QImage())
    with pytest.raises(ValueError, match="Size must be"):
        ReplaceImage(QImage(20_001, 1, QImage.Format.Format_ARGB32))


def test_replace_image_describes_itself(sample_image: QImage) -> None:
    """The step is named for the Edit menu and keeps annotations untouched."""
    replace = ReplaceImage(sample_image)
    state = DocumentState(sample_image, Rect(0, 0, 10, 10), ())

    assert replace.description == "Replace image"
    assert replace.apply(state).crop == Rect(0, 0, 10, 10)
