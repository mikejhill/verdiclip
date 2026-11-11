"""Answer automation requests from the live documents in every editor window."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from verdiclip import VERSION
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
    ErrorCode,
    JsonObject,
    JsonValue,
    Request,
)
from verdiclip.document.codec import AnnotationCodec
from verdiclip.editor.page import DocumentRecord
from verdiclip.geometry import Rect

ACTIVE = "active"


class HostedPage(Protocol):
    """A tab's page; only its record matters here."""

    record: DocumentRecord


class HostWindow(Protocol):
    """An editor window: its tabs in order and the one it shows."""

    @property
    def pages(self) -> Sequence[HostedPage]:
        """Return pages in visible tab order."""
        ...

    @property
    def current_page(self) -> HostedPage | None:
        """Return the page this window shows."""
        ...


class DocumentHost(Protocol):
    """Where documents live: ``Workspace`` in the app."""

    @property
    def windows(self) -> Sequence[HostWindow]:
        """Return windows, most recently activated first."""
        ...

    @property
    def documents(self) -> Sequence[DocumentRecord]:
        """Return every open document."""
        ...


class AutomationHandlers:
    """Dispatch requests by method name to read-only document queries."""

    def __init__(
        self,
        host: DocumentHost,
        *,
        previews: PreviewFolder | None = None,
        renderer: PreviewRenderer | None = None,
    ) -> None:
        self._host = host
        self._previews = previews or PreviewFolder()
        self._renderer = renderer or PreviewRenderer()
        self._codec = AnnotationCodec()
        self._methods: dict[str, Callable[[JsonObject], JsonObject]] = {
            "status": self.status,
            "list": self.list_documents,
            "get": self.get,
            "render": self.render,
        }

    def __call__(self, request: Request) -> JsonObject:
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
                }
        documents: list[JsonValue] = []
        for record in self._host.documents:
            entry = self._summary(record)
            entry |= placed.get(
                record.identifier, {"window": None, "tab": None, "current": False}
            )
            entry["active"] = active is not None and record is active
            documents.append(entry)
        return {
            "active": active.identifier if active is not None else None,
            "documents": documents,
        }

    def get(self, params: JsonObject) -> JsonObject:
        """Return the full model of one document."""
        record = self._resolve(params)
        session = record.session
        result = self._summary(record)
        result["annotations"] = [
            self._codec.encode(a) for a in session.document.annotations
        ]
        result["selected"] = list(session.selection.ids)
        return result

    def render(self, params: JsonObject) -> JsonObject:
        """Write a PNG of one document's current state and say where."""
        record = self._resolve(params)
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

    # Helpers

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
