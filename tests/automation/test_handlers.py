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
    Deferred,
    ErrorCode,
    JsonObject,
    JsonValue,
    Request,
)
from verdiclip.document.annotations import ArrowShape, RectangleShape, TextNote
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.document.style import Color, Style
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.render.renderer import Renderer
from verdiclip.settings import EditorSettings, OutputSettings

pytestmark = pytest.mark.usefixtures("qapp")

RED = Style(stroke=Color(255, 0, 0), fill=Color(255, 0, 0), width=2)


@dataclass
class Page:
    """A tab holding one document."""

    record: DocumentRecord
    editing: bool = False


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
    result = handlers(Request(method, dict(params)))  # ty: ignore[invalid-argument-type]
    assert not isinstance(result, Deferred)
    return result


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
    with pytest.raises(
        AutomationError,
        match=(
            "apply, capture, close, copy, export, get, history, list, open, redo, "
            "render, save, select, status, targets, undo"
        ),
    ) as caught:
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
        "editing": False,
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


def test_typing_user_makes_reads_busy(
    handlers: AutomationHandlers, host: Host, records: list[DocumentRecord]
) -> None:
    """While inline text is uncommitted, get and render refuse stale content."""
    page = host.windows[0].pages[1]
    assert isinstance(page, Page)
    page.editing = True

    listed = [obj(d) for d in listed_of(call(handlers, "list"))]
    with pytest.raises(AutomationError, match="typing") as got:
        call(handlers, "get")
    with pytest.raises(AutomationError, match="typing") as rendered:
        call(handlers, "render")

    assert [d["editing"] for d in listed] == [False, True, False]
    assert got.value.code is rendered.value.code is ErrorCode.BUSY
    assert (
        call(handlers, "get", doc=records[0].identifier)["id"] == records[0].identifier
    )


def listed_of(result: JsonObject) -> list[JsonValue]:
    """Return the documents array of a list result."""
    return listed(result["documents"])


BOX: JsonObject = {"kind": "rectangle", "rect": [10, 20, 30, 40]}


def annotations_of(record: DocumentRecord) -> list[str]:
    """Return the kinds of ``record``'s annotations, bottom to top."""
    return [a.kind.value for a in record.session.document.annotations]


def test_apply_is_one_ai_undo_step(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """Every operation lands in one undo step labelled as automation."""
    history = records[1].session.history

    result = call(
        handlers,
        "apply",
        ops=[
            {"op": "add", "annotation": BOX},
            {"op": "add", "annotation": {"kind": "counter", "center": [5, 5]}},
            {"op": "add", "annotation": {"kind": "counter", "center": [9, 9]}},
        ],
        select=True,
    )

    added = listed(result["added"])
    labels = [
        a.label  # ty: ignore[unresolved-attribute]
        for a in records[1].session.document.annotations[1:]
    ]
    assert result["state"] == history.state_id == 1
    assert result["label"] == history.undo_text == "CLI: add rectangle, add counter x2"
    assert result["annotations"] == 3
    assert list(records[1].session.selection.ids) == added
    assert labels == ["1", "2"]
    history.undo()
    assert annotations_of(records[1]) == []


def test_apply_ops_see_earlier_ops(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """Later operations address ids and coordinates produced by earlier ones."""
    red = {"stroke": "#ff0000"}
    result = call(
        handlers,
        "apply",
        doc=records[0].identifier,
        label="  Mark   the\\nbug ",
        ops=[
            {"op": "add", "annotation": BOX | {"id": "box", "style": red}},
            {
                "op": "add",
                "annotation": {"kind": "arrow", "start": [0, 0], "end": [9, 9]},
            },
            {
                "op": "update",
                "id": "box",
                "set": {"text": "here", "style": {"width": 5}},
            },
            {"op": "restack", "ids": ["box"], "to": "top"},
            {"op": "crop", "rect": [0, 0, 200, 100]},
            {"op": "rotate", "direction": "right"},
            {"op": "flip", "axis": "vertical"},
            {"op": "resize", "width": 50, "height": 100},
        ],
    )

    document = records[0].session.document
    box = document.find("box")
    assert result["label"] == "CLI: Mark the\\nbug"
    assert annotations_of(records[0]) == ["arrow", "rectangle"]
    assert box is not None
    assert box.style.stroke.hex == "#ff0000"
    assert box.style.width > 0
    assert box.text == "here"  # ty: ignore[unresolved-attribute]
    assert (document.image_rect.width, document.image_rect.height) == (50, 100)
    call(
        handlers,
        "apply",
        doc=records[0].identifier,
        ops=[
            {"op": "restack", "ids": ["box"], "to": "bottom"},
            {"op": "delete", "ids": ["box", "box"]},
        ],
    )
    assert annotations_of(records[0]) == ["arrow"]


def test_rotate_left_and_flip_horizontal(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """The other rotate and flip directions are accepted."""
    call(
        handlers,
        "apply",
        ops=[
            {"op": "rotate", "direction": "left"},
            {"op": "flip", "axis": "horizontal"},
        ],
    )

    rect = records[1].session.document.image_rect
    assert (rect.width, rect.height) == (300, 400)


def test_failed_apply_changes_nothing(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """A bad operation anywhere rejects the whole batch."""
    with pytest.raises(AutomationError, match="Operation 2") as caught:
        call(
            handlers,
            "apply",
            ops=[{"op": "add", "annotation": BOX}, {"op": "delete", "ids": ["nope"]}],
        )

    assert caught.value.code is ErrorCode.NOT_FOUND
    assert annotations_of(records[1]) == []
    assert records[1].session.history.state_id == 0


def test_bad_select_is_rejected_before_any_edit(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """A bad option fails the request without applying its operations."""
    with pytest.raises(AutomationError, match="select") as caught:
        call(handlers, "apply", ops=[{"op": "add", "annotation": BOX}], select="yes")

    assert caught.value.code is ErrorCode.BAD_REQUEST
    assert annotations_of(records[1]) == []
    assert records[1].session.history.state_id == 0


def test_auto_fit_text_follows_its_text(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> None:
    """Auto-fit text is sized to its text on add and update; fixed text is not."""
    note = {"kind": "text", "rect": [10, 20, 5, 5], "text": "Hi"}
    call(
        handlers,
        "apply",
        ops=[
            {"op": "add", "annotation": note | {"id": "fit"}},
            {"op": "add", "annotation": note | {"id": "fixed", "auto_fit": False}},
        ],
    )
    document = records[1].session.document

    def note_of(ident: str) -> TextNote:
        found = document.find(ident)
        assert isinstance(found, TextNote)
        return found

    def rect_of(ident: str) -> Rect:
        return note_of(ident).rect

    short = rect_of("fit")
    width, height = Renderer.measure_text("Hi", note_of("fit").style)
    assert short == Rect(10, 20, width, height)
    assert rect_of("fixed") == Rect(10, 20, 5, 5)

    call(
        handlers,
        "apply",
        ops=[
            {"op": "update", "id": "fit", "set": {"text": "A much longer label"}},
            {"op": "update", "id": "fixed", "set": {"text": "A much longer label"}},
        ],
    )
    assert rect_of("fit").width > short.width
    assert rect_of("fixed") == Rect(10, 20, 5, 5)


def test_long_labels_are_shortened(handlers: AutomationHandlers) -> None:
    """Undo labels stay short enough for the Edit menu."""
    result = call(
        handlers, "apply", ops=[{"op": "add", "annotation": BOX}], label="x" * 80
    )

    label = str(result["label"])
    assert len(label) == 60
    assert label.endswith("…")


@pytest.mark.parametrize(
    ("ops", "code", "match"),
    [
        ([], ErrorCode.BAD_REQUEST, "non-empty array"),
        ("add", ErrorCode.BAD_REQUEST, "non-empty array"),
        (["add"], ErrorCode.BAD_REQUEST, "must be an object"),
        ([{"op": "paint"}], ErrorCode.BAD_REQUEST, "op must be one of"),
        ([{"op": 3}], ErrorCode.BAD_REQUEST, "op must be one of"),
        ([{"op": "add"}], ErrorCode.BAD_REQUEST, "annotation must be an object"),
        (
            [{"op": "add", "annotation": {"kind": "blob"}}],
            ErrorCode.BAD_REQUEST,
            "kind",
        ),
        ([{"op": "add", "annotation": BOX | {"id": ""}}], ErrorCode.BAD_REQUEST, "id"),
        (
            [{"op": "add", "annotation": {"kind": "rectangle"}}],
            ErrorCode.BAD_REQUEST,
            "rect",
        ),
        (
            [{"op": "add", "annotation": BOX | {"id": "a"}}] * 2,
            ErrorCode.CONFLICT,
            "already in use",
        ),
        (
            [{"op": "update", "id": "x", "set": {}}],
            ErrorCode.NOT_FOUND,
            "no annotation",
        ),
        ([{"op": "update", "id": 4, "set": {}}], ErrorCode.BAD_REQUEST, "id must"),
        ([{"op": "delete", "ids": []}], ErrorCode.BAD_REQUEST, "ids must"),
        ([{"op": "restack", "ids": "a"}], ErrorCode.BAD_REQUEST, "ids must"),
        ([{"op": "delete", "ids": [["a"]]}], ErrorCode.BAD_REQUEST, "ids must"),
        ([{"op": "crop", "rect": [0, 0, 0, 5]}], ErrorCode.BAD_REQUEST, "positive"),
        ([{"op": "crop", "rect": [0, 0, True, 5]}], ErrorCode.BAD_REQUEST, "rect must"),
        ([{"op": "crop", "rect": [900, 900, 5, 5]}], ErrorCode.BAD_REQUEST, "overlap"),
        ([{"op": "rotate", "direction": "up"}], ErrorCode.BAD_REQUEST, "direction"),
        ([{"op": "flip", "axis": "diagonal"}], ErrorCode.BAD_REQUEST, "axis"),
        ([{"op": "resize", "width": 5.5, "height": 5}], ErrorCode.BAD_REQUEST, "width"),
        ([{"op": "resize", "width": 5, "height": 0}], ErrorCode.BAD_REQUEST, "Size"),
    ],
)
def test_apply_rejects_bad_operations(
    handlers: AutomationHandlers, ops: JsonValue, code: ErrorCode, match: str
) -> None:
    """Malformed or inapplicable operations are reported with their number."""
    with pytest.raises(AutomationError, match=match) as caught:
        call(handlers, "apply", ops=ops)

    assert caught.value.code is code


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"id": "other"}, "may not change id"),
        ({"kind": "ellipse"}, "may not change kind"),
        ({"rect": "wide"}, "rect"),
    ],
)
def test_update_rejects_identity_changes_and_bad_fields(
    handlers: AutomationHandlers, change: JsonObject, match: str
) -> None:
    """Updates keep an annotation's id and kind and must still decode."""
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX | {"id": "b"}}])

    with pytest.raises(AutomationError, match=match):
        call(handlers, "apply", ops=[{"op": "update", "id": "b", "set": change}])


def test_update_needs_a_set_object(handlers: AutomationHandlers) -> None:
    """An update without ``set`` is a usage error."""
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX | {"id": "b"}}])

    with pytest.raises(AutomationError, match="set must be an object"):
        call(handlers, "apply", ops=[{"op": "update", "id": "b"}])


@pytest.mark.parametrize(
    ("to", "match"), [("middle", "'top' or 'bottom'"), (None, "'top' or 'bottom'")]
)
def test_restack_needs_a_direction(
    handlers: AutomationHandlers, to: object, match: str
) -> None:
    """Restack moves to the top or the bottom only."""
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX | {"id": "b"}}])

    with pytest.raises(AutomationError, match=match):
        call(handlers, "apply", ops=[{"op": "restack", "ids": ["b"], "to": to}])


@pytest.mark.parametrize("label", ["", "   ", 5])
def test_apply_rejects_bad_labels(handlers: AutomationHandlers, label: object) -> None:
    """A label, when given, must have text."""
    with pytest.raises(AutomationError, match="label"):
        call(handlers, "apply", ops=[{"op": "add", "annotation": BOX}], label=label)


@pytest.mark.parametrize(
    ("expected", "code"),
    [
        (5, ErrorCode.CONFLICT),
        ("0", ErrorCode.BAD_REQUEST),
        (True, ErrorCode.BAD_REQUEST),
    ],
)
def test_expect_state_guards_edits(
    handlers: AutomationHandlers, expected: object, code: ErrorCode
) -> None:
    """Edits made against a stale read are refused."""
    with pytest.raises(AutomationError) as caught:
        call(
            handlers,
            "apply",
            ops=[{"op": "add", "annotation": BOX}],
            expect_state=expected,
        )

    assert caught.value.code is code


def test_expect_state_matching_proceeds(handlers: AutomationHandlers) -> None:
    """The current state passes the guard."""
    assert call(
        handlers, "apply", ops=[{"op": "add", "annotation": BOX}], expect_state=0
    )


def user_step(record: DocumentRecord) -> None:
    """Make an edit the way the user would."""
    box = RectangleShape(rect=Rect(1, 1, 5, 5), style=RED)
    record.session.history.execute(AddAnnotations([box], "Rectangle"))


def cli_step(handlers: AutomationHandlers, label: str) -> None:
    """Make an edit through automation."""
    call(handlers, "apply", ops=[{"op": "add", "annotation": BOX}], label=label)


@pytest.fixture
def mixed(
    handlers: AutomationHandlers, records: list[DocumentRecord]
) -> DocumentRecord:
    """The active document after a user step and two CLI steps (states 1-3)."""
    user_step(records[1])
    cli_step(handlers, "one")
    cli_step(handlers, "two")
    return records[1]


def test_history_lists_steps_with_their_origin(
    handlers: AutomationHandlers, mixed: DocumentRecord
) -> None:
    """History shows undo steps oldest first and redo steps next first."""
    mixed.session.history.undo()

    result = call(handlers, "history")

    assert result == {
        "id": mixed.identifier,
        "state": 2,
        "initial_state": 0,
        "undo": [
            {"label": "Rectangle", "state": 1, "cli": False},
            {"label": "CLI: one", "state": 2, "cli": True},
        ],
        "redo": [{"label": "CLI: two", "state": 3, "cli": True}],
    }


@pytest.mark.usefixtures("mixed")
def test_undo_and_redo_by_steps(handlers: AutomationHandlers) -> None:
    """Steps count from the current position."""
    undone = call(handlers, "undo", steps=2, only_cli=True)
    redone = call(handlers, "redo")

    assert (undone["state"], undone["undone"]) == (
        1,
        ["CLI: two", "CLI: one"],
    )
    assert (redone["state"], redone["redone"]) == (2, ["CLI: one"])


def test_undo_and_redo_to_a_state(
    handlers: AutomationHandlers, mixed: DocumentRecord
) -> None:
    """``to`` names the state to land on, including the initial one."""
    assert call(handlers, "undo", to=0)["undone"] == [
        "CLI: two",
        "CLI: one",
        "Rectangle",
    ]
    assert call(handlers, "redo", to=2)["state"] == 2
    assert call(handlers, "redo", to=2)["redone"] == []
    assert mixed.session.history.state_id == 2


def test_only_cli_protects_the_users_steps(
    handlers: AutomationHandlers, mixed: DocumentRecord
) -> None:
    """Nothing is undone when the range would include a user step."""
    with pytest.raises(AutomationError, match="Rectangle") as caught:
        call(handlers, "undo", steps=3, only_cli=True)

    assert caught.value.code is ErrorCode.CONFLICT
    assert mixed.session.history.state_id == 3


@pytest.mark.parametrize(
    ("method", "params", "code"),
    [
        ("undo", {"steps": 4}, ErrorCode.CONFLICT),
        ("redo", {}, ErrorCode.CONFLICT),
        ("undo", {"steps": 0}, ErrorCode.BAD_REQUEST),
        ("undo", {"steps": 1, "to": 1}, ErrorCode.BAD_REQUEST),
        ("undo", {"to": "1"}, ErrorCode.BAD_REQUEST),
        ("undo", {"to": 99}, ErrorCode.NOT_FOUND),
        ("redo", {"to": 1}, ErrorCode.NOT_FOUND),
        ("undo", {"expect_state": 1}, ErrorCode.CONFLICT),
    ],
)
def test_bad_history_moves(
    handlers: AutomationHandlers,
    mixed: DocumentRecord,
    method: str,
    params: JsonObject,
    code: ErrorCode,
) -> None:
    """Impossible moves are refused before any step is taken."""
    with pytest.raises(AutomationError) as caught:
        call(handlers, method, **params)

    assert caught.value.code is code
    assert mixed.session.history.state_id == 3


def test_select_sets_and_clears(
    handlers: AutomationHandlers, mixed: DocumentRecord
) -> None:
    """Select replaces the selection; an empty list clears it."""
    first, *_ = (a.id for a in mixed.session.document.annotations)

    assert call(handlers, "select", ids=[first, first])["selected"] == [first]
    assert call(handlers, "select", ids=[])["selected"] == []


@pytest.mark.parametrize(
    ("ids", "code"),
    [
        (["nope"], ErrorCode.NOT_FOUND),
        ("a", ErrorCode.BAD_REQUEST),
        ([3], ErrorCode.BAD_REQUEST),
    ],
)
def test_select_rejects_bad_ids(
    handlers: AutomationHandlers, ids: object, code: ErrorCode
) -> None:
    """Selection ids must name annotations in the document."""
    with pytest.raises(AutomationError) as caught:
        call(handlers, "select", ids=ids)

    assert caught.value.code is code


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("apply", {"ops": [{"op": "add", "annotation": BOX}]}),
        ("undo", {}),
        ("redo", {}),
        ("select", {"ids": []}),
    ],
)
def test_edits_wait_for_the_user(
    handlers: AutomationHandlers, host: Host, method: str, params: JsonObject
) -> None:
    """Nothing changes a document while the user is mid-edit in it."""
    page = host.windows[0].pages[1]
    assert isinstance(page, Page)
    page.editing = True

    with pytest.raises(AutomationError) as caught:
        call(handlers, method, **params)

    assert caught.value.code is ErrorCode.BUSY
