"""Open, save and copy through the control pipe, and the set_image operation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage

from verdiclip.automation.capturing import CaptureOutcome, CaptureSpec
from verdiclip.automation.handlers import AutomationHandlers, Opener
from verdiclip.automation.preview import PreviewFolder
from verdiclip.automation.protocol import (
    AutomationError,
    Deferred,
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
    result = handlers(Request(method, dict(params)))  # ty: ignore[invalid-argument-type]
    assert not isinstance(result, Deferred)
    return result


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


# export, close, capture and targets


@dataclass
class FakeApp:
    """The app's actions: a canned capture, targets and closing tabs."""

    host: Host
    answer: Deferred[CaptureOutcome] = field(default_factory=Deferred)
    specs: list[CaptureSpec] = field(default_factory=list)
    closed: list[tuple[str, bool]] = field(default_factory=list)
    stuck: bool = False

    def capture(self, spec: CaptureSpec) -> Deferred[CaptureOutcome]:
        """Record the request and return the pending answer."""
        self.specs.append(spec)
        return self.answer

    def capture_targets(self) -> JsonObject:
        """Return a fixed layout."""
        return {"monitors": [], "windows": []}

    def close_document(self, record: DocumentRecord, *, discard: bool) -> None:
        """Remove the document, or refuse while it's busy."""
        if self.stuck:
            msg = "The tab can't close right now"
            raise DeliveryError(msg)
        self.closed.append((record.identifier, discard))
        self.host.documents.remove(record)


@pytest.fixture
def app(host: Host) -> FakeApp:
    """The app's actions over ``host``."""
    return FakeApp(host)


@pytest.fixture
def app_handlers(host: Host, app: FakeApp, tmp_path: Path) -> AutomationHandlers:
    """Handlers that can capture and close tabs."""
    return AutomationHandlers(host, previews=PreviewFolder(tmp_path), app=app)


def test_export_writes_a_copy_and_keeps_the_documents_file(
    handlers: AutomationHandlers, record: DocumentRecord, tmp_path: Path
) -> None:
    """Export writes the image elsewhere; Save still targets the image's file."""
    record.saved_path = tmp_path / "own.png"
    target = tmp_path / "copy.jpg"

    exported = call(handlers, "export", path=str(target), expect_state=0)

    assert exported == {"id": record.identifier, "path": str(target), "state": 0}
    assert QImage(str(target)).width() == 400
    assert record.saved_path == tmp_path / "own.png"
    assert record.session.history.is_delivered


def test_export_needs_a_path_and_reports_write_failures(
    handlers: AutomationHandlers, tmp_path: Path
) -> None:
    """Export without an absolute path, or to an unknown format, fails."""
    with pytest.raises(AutomationError) as missing:
        call(handlers, "export")
    with pytest.raises(AutomationError) as unknown:
        call(handlers, "export", path=str(tmp_path / "x.unknown"))

    assert missing.value.code is ErrorCode.BAD_REQUEST
    assert unknown.value.code is ErrorCode.FAILED


def test_close_refuses_unsaved_work_unless_discarded(
    app_handlers: AutomationHandlers, app: FakeApp, record: DocumentRecord
) -> None:
    """Unsaved changes exit 4 unless discard; the reply says what was lost."""
    with pytest.raises(AutomationError, match="discard") as refused:
        call(app_handlers, "close")

    closed = call(app_handlers, "close", discard=True, expect_state=0)

    assert refused.value.code is ErrorCode.CONFLICT
    assert closed == {"id": record.identifier, "closed": True, "discarded": True}
    assert app.closed == [(record.identifier, True)]


def test_close_of_saved_work_needs_no_discard(
    app_handlers: AutomationHandlers, app: FakeApp, record: DocumentRecord
) -> None:
    """A saved or copied image closes without discard."""
    record.session.history.mark_delivered()

    closed = call(app_handlers, "close", doc=record.identifier)

    assert closed["discarded"] is False
    assert app.closed == [(record.identifier, False)]


def test_close_reports_a_busy_tab(
    app_handlers: AutomationHandlers, app: FakeApp
) -> None:
    """A tab that is saving or moving answers busy."""
    app.stuck = True

    with pytest.raises(AutomationError) as caught:
        call(app_handlers, "close", discard=True)

    assert caught.value.code is ErrorCode.BUSY


def test_capture_answers_when_the_capture_is_open(
    app_handlers: AutomationHandlers, app: FakeApp, host: Host, tmp_path: Path
) -> None:
    """The reply arrives once the app opens the capture, with its outputs."""
    later = app_handlers(Request("capture", {"mode": "fullscreen", "monitor": 1}))
    assert isinstance(later, Deferred)
    replies: list[JsonObject | None] = []
    later.then(lambda value, _error: replies.append(value))
    shot = make_record(QImage(20, 10, QImage.Format.Format_RGB32), tmp_path)
    host.documents.append(shot)

    assert replies == []
    app.answer.resolve(
        CaptureOutcome(shot, saved=tmp_path / "s.png", copied=True, warning="w")
    )

    assert app.specs[0].monitor == 1
    reply = replies[0]
    assert reply is not None
    assert (reply["id"], reply["image"]) == (shot.identifier, [20, 10])
    assert (reply["saved"], reply["copied"], reply["warning"]) == (
        str(tmp_path / "s.png"),
        True,
        "w",
    )


def test_capture_checks_the_request_first(app_handlers: AutomationHandlers) -> None:
    """A malformed capture is refused before anything happens."""
    with pytest.raises(AutomationError) as caught:
        app_handlers(Request("capture", {"mode": "window", "monitor": 2}))

    assert caught.value.code is ErrorCode.BAD_REQUEST


def test_targets_come_from_the_app(app_handlers: AutomationHandlers) -> None:
    """The targets reply is what the app reports."""
    assert call(app_handlers, "targets") == {"monitors": [], "windows": []}


@pytest.mark.parametrize(
    ("method", "params"),
    [("capture", {"mode": "universal"}), ("targets", {}), ("close", {"discard": True})],
)
def test_app_actions_need_the_app(
    handlers: AutomationHandlers, method: str, params: JsonObject
) -> None:
    """Without the app's actions these fail plainly."""
    with pytest.raises(AutomationError, match="can't capture") as caught:
        handlers(Request(method, params))

    assert caught.value.code is ErrorCode.FAILED
