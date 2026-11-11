"""Requests resolve documents across windows and report their live state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from verdiclip import VERSION
from verdiclip.automation.handlers import AutomationHandlers
from verdiclip.automation.preview import PreviewFolder
from verdiclip.automation.protocol import (
    PROTOCOL_VERSION,
    AutomationError,
    ErrorCode,
    JsonObject,
    JsonValue,
    Request,
)
from verdiclip.document.annotations import ArrowShape, RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.document.style import Color, Style
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings

pytestmark = pytest.mark.usefixtures("qapp")

RED = Style(stroke=Color(255, 0, 0), fill=Color(255, 0, 0), width=2)


@dataclass
class Page:
    """A tab holding one document."""

    record: DocumentRecord


@dataclass
class Window:
    """An editor window: tabs and the one it shows."""

    pages: Sequence[Page]
    current_page: Page | None


@dataclass
class Host:
    """Windows in most-recently-used order plus every document."""

    windows: Sequence[Window] = ()
    documents: Sequence[DocumentRecord] = field(default_factory=list)


def make_record(image: QImage, title: str, tmp_path: Path) -> DocumentRecord:
    """Return a fresh, unsaved capture."""
    session = EditorSession(Document(image), EditorSettings(), delivered=False)
    return DocumentRecord(
        session, ImageDelivery(OutputSettings(directory=tmp_path)), title=title
    )


@pytest.fixture
def records(sample_image: QImage, tmp_path: Path) -> list[DocumentRecord]:
    """Three documents: two tabs in the front window, one in the back window."""
    made = [make_record(sample_image, f"Shot {n}", tmp_path) for n in (1, 2, 3)]
    made[2].saved_path = tmp_path / "saved.png"
    made[2].session.history.mark_delivered()
    return made


@pytest.fixture
def host(records: list[DocumentRecord]) -> Host:
    """Front window shows Shot 2 of [Shot 1, Shot 2]; back window shows Shot 3."""
    front = Window([Page(records[0]), Page(records[1])], None)
    front.current_page = front.pages[1]
    back = Window([Page(records[2])], None)
    back.current_page = back.pages[0]
    return Host([front, back], list(records))


@pytest.fixture
def handlers(host: Host, tmp_path: Path) -> AutomationHandlers:
    """Handlers writing default previews into a temporary folder."""
    return AutomationHandlers(host, previews=PreviewFolder(tmp_path / "previews"))


def call(handlers: AutomationHandlers, method: str, **params: object) -> JsonObject:
    """Run one request."""
    return handlers(Request(method, dict(params)))  # ty: ignore[invalid-argument-type]


def obj(value: JsonValue) -> JsonObject:
    """Narrow a JSON value to an object."""
    assert isinstance(value, dict)
    return value


def listed(value: JsonValue) -> list[JsonValue]:
    """Narrow a JSON value to an array."""
    assert isinstance(value, list)
    return value


def test_status(handlers: AutomationHandlers) -> None:
    """Status confirms the app, its version and the protocol."""
    assert call(handlers, "status") == {
        "app": "VerdiClip",
        "version": VERSION,
        "protocol": PROTOCOL_VERSION,
        "documents": 3,
    }


def test_unknown_method(handlers: AutomationHandlers) -> None:
    """Unknown methods list the supported ones."""
    with pytest.raises(AutomationError, match="get, list, render, status") as caught:
        call(handlers, "paint")

    assert caught.value.code is ErrorCode.BAD_REQUEST


def test_list_places_every_document(
    handlers: AutomationHandlers, records: list[DocumentRecord], tmp_path: Path
) -> None:
    """Each document reports its window, tab, and whether it is active."""
    result = call(handlers, "list")

    documents = result["documents"]
    assert isinstance(documents, list)
    assert result["active"] == records[1].identifier
    first, second, third = (d for d in documents if isinstance(d, dict))
    assert first == {
        "id": records[0].identifier,
        "title": "Shot 1",
        "path": None,
        "unsaved": True,
        "image": [400, 300],
        "crop": [0, 0, 400, 300],
        "annotations": 0,
        "state": 0,
        "window": 1,
        "tab": 1,
        "current": False,
        "active": False,
    }
    assert (second["window"], second["tab"], second["current"]) == (1, 2, True)
    assert second["active"] is True
    assert third["title"] == "saved.png"
    assert third["path"] == str(tmp_path / "saved.png")
    assert third["unsaved"] is False
    assert (third["window"], third["tab"], third["current"]) == (2, 1, True)
    assert third["active"] is False


def test_document_being_moved_is_listed_without_a_window(
    records: list[DocumentRecord],
) -> None:
    """A document between windows is still listed and addressable."""
    handlers = AutomationHandlers(Host([], records[:1]))

    entry = obj(listed(call(handlers, "list")["documents"])[0])

    assert (entry["window"], entry["tab"], entry["current"]) == (None, None, False)
    assert call(handlers, "list")["active"] == records[0].identifier
    assert call(handlers, "get")["id"] == records[0].identifier


def test_active_skips_windows_without_a_current_tab(
    records: list[DocumentRecord],
) -> None:
    """The most recent window that shows a document defines "active"."""
    empty = Window([], None)
    back = Window([Page(records[2])], Page(records[2]))
    handlers = AutomationHandlers(Host([empty, back], records))

    assert call(handlers, "list")["active"] == records[2].identifier


def test_nothing_open(tmp_path: Path) -> None:
    """With no documents, list is empty and get reports not_found."""
    handlers = AutomationHandlers(Host(), previews=PreviewFolder(tmp_path))

    assert call(handlers, "list") == {"active": None, "documents": []}
    with pytest.raises(AutomationError, match="No document") as caught:
        call(handlers, "get")
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_get_includes_annotations_selection_and_state(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """Get returns the unsaved model by id, with what the user selected."""
    session = records[0].session
    box = RectangleShape(rect=Rect(10, 20, 30, 40), style=RED)
    arrow = ArrowShape(start=Point(1, 2), end=Point(50, 60))
    session.history.execute(AddAnnotations([box, arrow], "Add"))
    session.selection.set([arrow.id])

    result = call(handlers, "get", doc=records[0].identifier)

    first, second = (obj(a) for a in listed(result["annotations"]))
    assert (first["id"], second["id"]) == (box.id, arrow.id)
    assert (first["kind"], first["rect"]) == ("rectangle", [10, 20, 30, 40])
    assert (second["start"], second["end"]) == ([1, 2], [50, 60])
    assert result["selected"] == [arrow.id]
    assert result["state"] == session.history.state_id == 1
    assert result["unsaved"] is True


@pytest.mark.parametrize(
    ("doc", "code"),
    [
        ("nope", ErrorCode.NOT_FOUND),
        ("", ErrorCode.BAD_REQUEST),
        (3, ErrorCode.BAD_REQUEST),
    ],
)
def test_bad_document_ids(
    handlers: AutomationHandlers, doc: object, code: ErrorCode
) -> None:
    """Unknown or malformed ids are reported, never guessed."""
    with pytest.raises(AutomationError) as caught:
        call(handlers, "get", doc=doc)

    assert caught.value.code is code


def test_null_doc_means_active(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """A null doc parameter targets the active document."""
    assert call(handlers, "get", doc=None)["id"] == records[1].identifier
    assert call(handlers, "get", doc="active")["id"] == records[1].identifier


def test_render_default_writes_preview_with_unsaved_edits(
    handlers: AutomationHandlers, records: list[DocumentRecord], tmp_path: Path
) -> None:
    """Render writes the live image to the preview folder and reports mapping."""
    box = RectangleShape(rect=Rect(100, 100, 40, 40), style=RED)
    records[1].session.history.execute(AddAnnotations([box], "Add"))

    result = call(handlers, "render")

    path = Path(str(result["path"]))
    assert path.parent == (tmp_path / "previews").resolve()
    assert QImage(str(path)).pixelColor(120, 120) == QColor(255, 0, 0)
    assert result == result | {
        "id": records[1].identifier,
        "width": 400,
        "height": 300,
        "origin": [0, 0],
        "scale": 1.0,
        "grid": None,
        "base": False,
        "state": 1,
    }


def test_render_options(
    handlers: AutomationHandlers, records: list[DocumentRecord], tmp_path: Path
) -> None:
    """Region, scale, grid, base and output are honored."""
    target = tmp_path / "out" / "view.png"

    result = call(
        handlers,
        "render",
        doc=records[0].identifier,
        output=str(target),
        region=[100, 50, 200, 100],
        max_side=100,
        grid=50,
        base=True,
    )

    assert result["path"] == str(target.resolve())
    assert (result["width"], result["height"]) == (100, 50)
    assert result["origin"] == [100, 50]
    assert result["scale"] == 0.5
    assert result["grid"] == 100
    assert result["base"] is True
    assert target.exists()


def test_render_accepts_nulls_as_defaults(handlers: AutomationHandlers) -> None:
    """Null options fall back to their defaults."""
    result = call(handlers, "render", max_side=None, grid=None, base=None, region=None)

    assert (result["width"], result["grid"], result["base"]) == (400, None, False)


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({"output": "relative.png"}, "absolute"),
        ({"output": ""}, "non-empty"),
        ({"output": 5}, "non-empty"),
        ({"max_side": "big"}, "max_side must be an integer"),
        ({"max_side": True}, "max_side must be an integer"),
        ({"grid": 1.5}, "grid must be an integer"),
        ({"base": "yes"}, "base must be true or false"),
        ({"region": [1, 2, 3]}, "region must be"),
        ({"region": "1,2,3,4"}, "region must be"),
        ({"region": [1, 2, True, 4]}, "region must be"),
        ({"region": [1, 2, 0, 4]}, "positive"),
    ],
)
def test_render_rejects_bad_params(
    handlers: AutomationHandlers, params: dict[str, object], match: str
) -> None:
    """Invalid options are usage errors with a reason."""
    with pytest.raises(AutomationError, match=match) as caught:
        call(handlers, "render", **params)

    assert caught.value.code is ErrorCode.BAD_REQUEST
