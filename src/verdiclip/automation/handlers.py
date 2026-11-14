"""Answer automation requests from the live documents in every editor window."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from verdiclip import VERSION
from verdiclip.automation.capturing import AppActions, CaptureOutcome, CaptureSpec
from verdiclip.automation.editing import EditPlanner
from verdiclip.automation.preview import (
    PreviewFolder,
    PreviewOptions,
    PreviewRenderer,
    write_png,
)
from verdiclip.automation.protocol import (
    DEFAULT_MAX_SIDE,
    PROTOCOL_VERSION,
    AutomationError,
    Deferred,
    ErrorCode,
    JsonObject,
    JsonValue,
    Request,
)
from verdiclip.document.codec import AnnotationCodec
from verdiclip.document.history import History, HistoryStep
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import ToolId
from verdiclip.exceptions import AppError
from verdiclip.geometry import Rect
from verdiclip.render.renderer import Renderer

ACTIVE = "active"

type Opener = Callable[[Path], DocumentRecord]


class HostedPage(Protocol):
    """A tab's page: its document and whether the user is typing into it."""

    record: DocumentRecord

    @property
    def editing(self) -> bool:
        """Return whether an inline editor holds uncommitted text."""


class HostWindow(Protocol):
    """An editor window: its tabs in order and the one it shows."""

    @property
    def pages(self) -> Sequence[HostedPage]:
        """Return pages in visible tab order."""

    @property
    def current_page(self) -> HostedPage | None:
        """Return the page this window shows."""


class DocumentHost(Protocol):
    """Where documents live: ``Workspace`` in the app."""

    @property
    def windows(self) -> Sequence[HostWindow]:
        """Return windows, most recently activated first."""

    @property
    def documents(self) -> Sequence[DocumentRecord]:
        """Return every open document."""


class AutomationHandlers:
    """Dispatch requests by method name to document queries and edits."""

    def __init__(
        self,
        host: DocumentHost,
        *,
        previews: PreviewFolder | None = None,
        renderer: PreviewRenderer | None = None,
        opener: Opener | None = None,
        app: AppActions | None = None,
    ) -> None:
        """``opener`` opens an image file in an editor tab (``ctl open``).

        ``app`` captures, lists capture targets and closes tabs.
        """
        self._host = host
        self._opener = opener
        self._app = app
        self._previews = previews or PreviewFolder()
        self._renderer = renderer or PreviewRenderer()
        self._codec = AnnotationCodec()
        self._methods: dict[
            str, Callable[[JsonObject], JsonObject | Deferred[JsonObject]]
        ] = {
            "status": self.status,
            "list": self.list_documents,
            "get": self.get,
            "render": self.render,
            "apply": self.apply,
            "history": self.history,
            "undo": self.undo,
            "redo": self.redo,
            "select": self.select,
            "open": self.open,
            "save": self.save,
            "copy": self.copy,
            "export": self.export,
            "close": self.close,
            "capture": self.capture,
            "targets": self.targets,
        }

    def __call__(self, request: Request) -> JsonObject | Deferred[JsonObject]:
        """Run ``request``.

        Raises:
            AutomationError: If the method is unknown or the request fails.
        """
        method = self._methods.get(request.method)
        if method is None:
            known = ", ".join(sorted(self._methods))
            msg = f"Unknown method {request.method!r}; expected one of: {known}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return method(request.params)

    # Methods

    def status(self, params: JsonObject) -> JsonObject:
        """Report the app and protocol versions."""
        del params
        return {
            "app": "VerdiClip",
            "version": VERSION,
            "protocol": PROTOCOL_VERSION,
            "documents": len(self._host.documents),
        }

    def list_documents(self, params: JsonObject) -> JsonObject:
        """Describe every open document across all editor windows."""
        del params
        active = self._active()
        placed: dict[str, JsonObject] = {}
        for number, window in enumerate(self._host.windows, start=1):
            current = window.current_page
            for position, page in enumerate(window.pages, start=1):
                placed[page.record.identifier] = {
                    "window": number,
                    "tab": position,
                    "current": current is not None and current.record is page.record,
                    "editing": page.editing,
                }
        documents: list[JsonValue] = []
        for record in self._host.documents:
            entry = self._summary(record)
            entry |= placed.get(
                record.identifier,
                {"window": None, "tab": None, "current": False, "editing": False},
            )
            entry["active"] = active is not None and record is active
            documents.append(entry)
        return {
            "active": active.identifier if active is not None else None,
            "documents": documents,
        }

    def get(self, params: JsonObject) -> JsonObject:
        """Return the full model of one document."""
        record = self._settled(self._resolve(params))
        session = record.session
        result = self._summary(record)
        result["annotations"] = [
            self._codec.encode(a) for a in session.document.annotations
        ]
        result["selected"] = list(session.selection.ids)
        return result

    def render(self, params: JsonObject) -> JsonObject:
        """Write a PNG of one document's current state and say where."""
        record = self._settled(self._resolve(params))
        options = PreviewOptions(
            region=_optional_rect(params, "region"),
            max_side=_integer(params, "max_side", DEFAULT_MAX_SIDE),
            grid=_integer(params, "grid", 0),
            base=_boolean(params, "base"),
        )
        state = record.session.history.state_id
        preview = self._renderer.render(record.session.document, options)
        output = params.get("output")
        if output is None:
            self._previews.prune()
            path = self._previews.new_path(record.identifier, state)
        elif isinstance(output, str) and output:
            path = Path(output)
            if not path.is_absolute():
                msg = f"output must be an absolute path, got {output!r}"
                raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        else:
            msg = "output must be a non-empty string"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        written = write_png(preview.image, path)
        return {
            "id": record.identifier,
            "path": str(written),
            "width": preview.image.width(),
            "height": preview.image.height(),
            "origin": list(preview.origin),
            "scale": preview.scale,
            "grid": preview.grid or None,
            "base": options.base,
            "state": state,
        }

    def apply(self, params: JsonObject) -> JsonObject:
        """Run a batch of edit operations as one undo step labelled "CLI: …"."""
        record = self._settled(self._resolve(params))
        session = record.session
        self._expect_state(session.history, params)
        select = _boolean(params, "select")
        planner = EditPlanner(
            lambda kind: session.styles.get(ToolId(kind.value)), fit=Renderer.fit
        )
        plan = planner.plan(session.document, params.get("ops"), params.get("label"))
        session.history.execute(plan.batch)
        if select:
            session.selection.set(plan.added)
        return {
            "id": record.identifier,
            "state": session.history.state_id,
            "label": plan.batch.description,
            "added": list(plan.added),
            "annotations": len(session.document.annotations),
        }

    def history(self, params: JsonObject) -> JsonObject:
        """List the steps undo and redo would take, with their states."""
        record = self._resolve(params)
        history = record.session.history
        return {
            "id": record.identifier,
            "state": history.state_id,
            "initial_state": history.initial_state,
            "undo": [_step(s) for s in history.undo_steps],
            "redo": [_step(s) for s in history.redo_steps],
        }

    def undo(self, params: JsonObject) -> JsonObject:
        """Undo by count or back to a state; ``only_cli`` protects user work."""
        return self._travel(params, backward=True)

    def redo(self, params: JsonObject) -> JsonObject:
        """Redo steps by count or forward to a state."""
        return self._travel(params, backward=False)

    def select(self, params: JsonObject) -> JsonObject:
        """Select annotations by id (an empty list clears the selection)."""
        record = self._settled(self._resolve(params))
        session = record.session
        ids = params.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            msg = "ids must be an array of annotation ids"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        wanted = [str(i) for i in dict.fromkeys(ids)]
        missing = [i for i in wanted if session.document.find(i) is None]
        if missing:
            msg = f"No annotation has id {missing[0]!r}"
            raise AutomationError(ErrorCode.NOT_FOUND, msg)
        session.selection.set(wanted)
        return {"id": record.identifier, "selected": list(session.selection.ids)}

    # Helpers

    def open(self, params: JsonObject) -> JsonObject:
        """Open an image file in an editor tab and describe the new document."""
        path = _absolute_path(params, "path")
        if self._opener is None:
            msg = "This VerdiClip can't open files on request"
            raise AutomationError(ErrorCode.FAILED, msg)
        if not path.is_file():
            msg = f"No image file at {path}"
            raise AutomationError(ErrorCode.NOT_FOUND, msg)
        try:
            record = self._opener(path)
        except (AppError, ValueError) as err:
            raise AutomationError(ErrorCode.FAILED, str(err)) from err
        return self._summary(record)

    def save(self, params: JsonObject) -> JsonObject:
        """Write the document as Save would, to ``path`` or its usual file."""
        record = self._settled(self._resolve(params))
        history = record.session.history
        self._expect_state(history, params)
        path = (
            _absolute_path(params, "path")
            if params.get("path") is not None
            else _default_save_path(record)
        )
        state = history.state_id
        image = Renderer().flatten(record.session.document)
        try:
            written = record.delivery.save(image, path)
        except AppError as err:
            raise AutomationError(ErrorCode.FAILED, str(err)) from err
        record.saved_path = written
        history.mark_delivered(expected_state_id=state)
        return {"id": record.identifier, "path": str(written), "state": state}

    def export(self, params: JsonObject) -> JsonObject:
        """Write a copy to ``path``; the document keeps its own file."""
        record = self._settled(self._resolve(params))
        history = record.session.history
        self._expect_state(history, params)
        path = _absolute_path(params, "path")
        state = history.state_id
        image = Renderer().flatten(record.session.document)
        try:
            written = record.delivery.save(image, path)
        except AppError as err:
            raise AutomationError(ErrorCode.FAILED, str(err)) from err
        history.mark_delivered(expected_state_id=state)
        return {"id": record.identifier, "path": str(written), "state": state}

    def close(self, params: JsonObject) -> JsonObject:
        """Close a document's tab; unsaved work needs ``discard``."""
        app = self._require_app()
        record = self._settled(self._resolve(params))
        history = record.session.history
        self._expect_state(history, params)
        discard = _boolean(params, "discard")
        unsaved = not history.is_delivered
        if unsaved and not discard:
            msg = (
                "The image has changes that weren't saved or copied; save it "
                "first, or pass discard to throw them away"
            )
            raise AutomationError(ErrorCode.CONFLICT, msg)
        try:
            app.close_document(record, discard=discard)
        except AppError as err:
            raise AutomationError(ErrorCode.BUSY, str(err)) from err
        return {"id": record.identifier, "closed": True, "discarded": unsaved}

    def capture(self, params: JsonObject) -> Deferred[JsonObject]:
        """Capture and open the result in a tab; answers once it's open."""
        app = self._require_app()
        spec = CaptureSpec.from_params(params)
        return app.capture(spec).map(self._captured)

    def targets(self, params: JsonObject) -> JsonObject:
        """List the monitors and windows ``capture`` can take."""
        del params
        return self._require_app().capture_targets()

    def _captured(self, outcome: CaptureOutcome) -> JsonObject:
        """Describe a finished capture: its new document and outputs."""
        result: JsonObject = (
            self._summary(outcome.record)
            if outcome.record is not None
            else {"id": None}
        )
        result["saved"] = str(outcome.saved) if outcome.saved else None
        result["copied"] = outcome.copied
        result["warning"] = outcome.warning or None
        return result

    def _require_app(self) -> AppActions:
        """Return the app's actions.

        Raises:
            AutomationError: If this server was built without them.
        """
        if self._app is None:
            msg = "This VerdiClip can't capture or close tabs on request"
            raise AutomationError(ErrorCode.FAILED, msg)
        return self._app

    def copy(self, params: JsonObject) -> JsonObject:
        """Put the document on the clipboard as Copy would."""
        record = self._settled(self._resolve(params))
        history = record.session.history
        self._expect_state(history, params)
        state = history.state_id
        image = Renderer().flatten(record.session.document)
        try:
            record.delivery.copy(image)
        except AppError as err:
            raise AutomationError(ErrorCode.FAILED, str(err)) from err
        record.copied = True
        history.mark_delivered(expected_state_id=state)
        return {"id": record.identifier, "state": state}

    def _travel(self, params: JsonObject, *, backward: bool) -> JsonObject:
        """Move through history; validate the whole move before taking a step."""
        record = self._settled(self._resolve(params))
        history = record.session.history
        self._expect_state(history, params)
        available = history.undo_steps[::-1] if backward else history.redo_steps
        verb = "undo" if backward else "redo"
        count = _steps_to(params, available, history, verb, backward=backward)
        steps = available[:count]
        if _boolean(params, "only_cli"):
            for step in steps:
                if not step.cli:
                    msg = (
                        f"Refusing to {verb} {step.label!r}: it was made in the "
                        "editor, and only_cli is set"
                    )
                    raise AutomationError(ErrorCode.CONFLICT, msg)
        move = history.undo if backward else history.redo
        for _ in steps:
            move()
        return {
            "id": record.identifier,
            "state": history.state_id,
            "undone" if backward else "redone": [s.label for s in steps],
        }

    @staticmethod
    def _expect_state(history: History, params: JsonObject) -> None:
        """Refuse to act when the document changed since the caller looked.

        Raises:
            AutomationError: If ``expect_state`` is given and differs.
        """
        expected = params.get("expect_state")
        if expected is None:
            return
        if isinstance(expected, bool) or not isinstance(expected, int):
            msg = f"expect_state must be an integer, got {expected!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if expected != history.state_id:
            msg = (
                f"The image changed (state {history.state_id}, expected "
                f"{expected}); read it again before editing"
            )
            raise AutomationError(ErrorCode.CONFLICT, msg)

    def _summary(self, record: DocumentRecord) -> JsonObject:
        """Describe ``record`` briefly."""
        document = record.session.document
        crop = document.crop
        return {
            "id": record.identifier,
            "title": record.saved_path.name if record.saved_path else record.title,
            "path": str(record.saved_path) if record.saved_path else None,
            "unsaved": not record.session.history.is_delivered,
            "image": [document.image_rect.width, document.image_rect.height],
            "crop": [crop.x, crop.y, crop.width, crop.height],
            "annotations": len(document.annotations),
            "state": record.session.history.state_id,
        }

    def _settled(self, record: DocumentRecord) -> DocumentRecord:
        """Refuse to read or edit a document the user is in the middle of editing.

        Raises:
            AutomationError: If the user is typing or dragging in ``record``.
        """
        for window in self._host.windows:
            for page in window.pages:
                if page.record is record and page.editing:
                    msg = (
                        "The user is typing or dragging in this image; their "
                        "change isn't in the document yet. Try again shortly."
                    )
                    raise AutomationError(ErrorCode.BUSY, msg)
        return record

    def _active(self) -> DocumentRecord | None:
        """Return the current tab of the most recently used window."""
        for window in self._host.windows:
            page = window.current_page
            if page is not None:
                return page.record
        documents = self._host.documents
        return documents[0] if documents else None

    def _resolve(self, params: JsonObject) -> DocumentRecord:
        """Return the document named by ``params["doc"]`` (default: active).

        Raises:
            AutomationError: If there is no such document.
        """
        wanted = params.get("doc", ACTIVE)
        if wanted is None:
            wanted = ACTIVE
        if not isinstance(wanted, str) or not wanted:
            msg = "doc must be a document id or 'active'"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if wanted == ACTIVE:
            record = self._active()
            if record is None:
                msg = "No document is open in VerdiClip"
                raise AutomationError(ErrorCode.NOT_FOUND, msg)
            return record
        for record in self._host.documents:
            if record.identifier == wanted:
                return record
        msg = f"No open document has id {wanted!r}; run `verdiclip ctl list`"
        raise AutomationError(ErrorCode.NOT_FOUND, msg)


def _step(step: HistoryStep) -> JsonObject:
    """Describe one history step."""
    return {"label": step.label, "state": step.state, "cli": step.cli}


def _steps_to(
    params: JsonObject,
    available: Sequence[HistoryStep],
    history: History,
    verb: str,
    *,
    backward: bool,
) -> int:
    """Return how many of ``available`` steps reach ``steps`` or state ``to``."""
    target = params.get("to")
    if target is not None and params.get("steps") is not None:
        msg = "Give steps or to, not both"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    if target is None:
        count = _integer(params, "steps", 1)
        if count < 1:
            msg = f"steps must be at least 1, got {count}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if count > len(available):
            msg = f"Only {len(available)} step(s) to {verb}"
            raise AutomationError(ErrorCode.CONFLICT, msg)
        return count
    if isinstance(target, bool) or not isinstance(target, int):
        msg = f"to must be a state number, got {target!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    if target == history.state_id:
        return 0
    # Undoing step i lands on the state before it; redoing lands on its own
    landing = [s.state for s in available]
    if backward:
        landing = [*landing[1:], history.initial_state]
    if target not in landing:
        msg = f"State {target} can't be reached by {verb}; see `verdiclip ctl history`"
        raise AutomationError(ErrorCode.NOT_FOUND, msg)
    return landing.index(target) + 1


def _absolute_path(params: JsonObject, key: str) -> Path:
    """Read a required absolute path parameter."""
    value = params.get(key)
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        msg = f"{key} must be an absolute path, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return Path(value)


def _default_save_path(record: DocumentRecord) -> Path:
    """Return where Save writes: the document's file, or a new automatic name."""
    path = record.saved_path
    if path is not None:
        try:
            record.delivery.format_for(path)
        except AppError:
            path = None
    return path or record.delivery.auto_path(title=record.title)


def _integer(params: JsonObject, key: str, default: int) -> int:
    """Read an optional integer parameter."""
    value = params.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _boolean(params: JsonObject, key: str) -> bool:
    """Read an optional boolean parameter."""
    value = params.get(key, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        msg = f"{key} must be true or false, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _optional_rect(params: JsonObject, key: str) -> Rect | None:
    """Read an optional ``[x, y, width, height]`` parameter."""
    value = params.get(key)
    if value is None:
        return None
    numbers = value if isinstance(value, list) else []
    valid = len(numbers) == 4 and all(  # noqa: PLR2004 - x, y, width, height
        isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers
    )
    if not valid:
        msg = f"{key} must be [x, y, width, height], got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    x, y, w, h = (float(n) for n in numbers)  # ty: ignore[invalid-argument-type]
    if w <= 0 or h <= 0:
        msg = f"{key} width and height must be positive, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return Rect(x, y, w, h)
