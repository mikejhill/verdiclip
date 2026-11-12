"""Turn automation edit operations into one undoable batch of commands."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from verdiclip.automation.protocol import AutomationError, ErrorCode
from verdiclip.document.annotations import (
    Annotation,
    AnnotationKind,
    JsonObject,
    JsonValue,
)
from verdiclip.document.codec import AnnotationCodec, StyleCodec
from verdiclip.document.commands import (
    AddAnnotations,
    Batch,
    Command,
    RemoveAnnotations,
    ReorderAnnotations,
    ReplaceAnnotations,
    SetCrop,
    TransformImage,
)
from verdiclip.document.document import Document
from verdiclip.document.style import Style
from verdiclip.document.transform import Flip, FlipAxis, Resize, Rotate
from verdiclip.exceptions import CodecError
from verdiclip.geometry import Rect

MAX_LABEL = 60
PREFIX = "Automation: "

type StyleLookup = Callable[[AnnotationKind], Style]
type Fitter = Callable[[Annotation], Annotation]

OPERATIONS = (
    "add",
    "update",
    "delete",
    "restack",
    "crop",
    "rotate",
    "flip",
    "resize",
)


@dataclass
class EditPlan:
    """The batch to execute and what it did, checked against a trial copy."""

    batch: Batch
    added: list[str] = field(default_factory=list)


class EditPlanner:
    """Validate operations one by one on a copy of the document.

    Each operation sees the result of the ones before it, so ids added earlier
    can be updated later and coordinates follow a rotate or resize. Nothing
    touches the real document until every operation has succeeded.
    """

    def __init__(self, default_style: StyleLookup, fit: Fitter | None = None) -> None:
        """``fit`` sizes auto-fit text to its text, as the editor does."""
        self._default_style = default_style
        self._fit: Fitter = fit or (lambda annotation: annotation)
        self._codec = AnnotationCodec()

    def plan(
        self, document: Document, operations: JsonValue, label: JsonValue
    ) -> EditPlan:
        """Return the batch for ``operations`` applied to ``document``.

        Raises:
            AutomationError: If any operation is malformed or does not apply.
        """
        if not isinstance(operations, list) or not operations:
            msg = "ops must be a non-empty array of operations"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        trial = document.copy()
        commands: list[Command] = []
        added: list[str] = []
        names: list[str] = []
        for number, operation in enumerate(operations, start=1):
            try:
                command, name = self._build(trial, operation, added)
                command.apply(trial)
            except (CodecError, ValueError) as err:
                msg = f"Operation {number}: {err}"
                raise AutomationError(ErrorCode.BAD_REQUEST, msg) from err
            except AutomationError as err:
                msg = f"Operation {number}: {err.message}"
                raise AutomationError(err.code, msg) from err
            commands.append(command)
            names.append(name)
        return EditPlan(Batch(commands, _description(label, names)), added)

    def _build(
        self, trial: Document, operation: JsonValue, added: list[str]
    ) -> tuple[Command, str]:
        """Return the command for one operation and a short name for it."""
        if not isinstance(operation, dict):
            msg = f"must be an object, got {operation!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        op = operation.get("op")
        builders: Mapping[str, Callable[[Document, JsonObject], Command]] = {
            "update": self._update,
            "delete": self._delete,
            "restack": self._restack,
            "crop": self._crop,
            "rotate": self._rotate,
            "flip": self._flip,
            "resize": self._resize,
        }
        if op == "add":
            annotation = self._new_annotation(trial, operation)
            added.append(annotation.id)
            return AddAnnotations([annotation], "Add"), f"add {annotation.kind}"
        builder = builders.get(op) if isinstance(op, str) else None
        if builder is None:
            msg = f"op must be one of {', '.join(OPERATIONS)}; got {op!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return builder(trial, operation), str(op)

    # Operations

    def _new_annotation(self, trial: Document, operation: JsonObject) -> Annotation:
        """Decode an ``add``; id, style fields and counter label are optional."""
        data = _object(operation, "annotation")
        kind = data.get("kind")
        try:
            kind = AnnotationKind(str(kind))
        except ValueError:
            known = ", ".join(k.value for k in AnnotationKind)
            msg = f"annotation.kind must be one of {known}; got {kind!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg) from None
        ident = data.get("id")
        if ident is None:
            ident = uuid.uuid4().hex
        elif not isinstance(ident, str) or not ident:
            msg = "annotation.id must be a non-empty string"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        elif trial.find(ident) is not None:
            msg = f"annotation id {ident!r} is already in use"
            raise AutomationError(ErrorCode.CONFLICT, msg)
        style = self._default_style(kind)
        base: JsonObject = {"style": StyleCodec.encode(style)}
        if kind is AnnotationKind.COUNTER:
            # The counter tool sizes markers from the font, as here
            base["label"] = trial.next_counter_label
            base["radius"] = float(style.font_size)
        merged = _merge(base, data) | {"id": ident, "kind": kind.value}
        return self._fit(self._codec.decode(merged))

    def _update(self, trial: Document, operation: JsonObject) -> Command:
        """Change fields of one annotation; style fields merge individually."""
        current = _existing(trial, operation.get("id"))
        changes = _object(operation, "set")
        for key in ("id", "kind"):
            if key in changes:
                msg = f"set may not change {key}"
                raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        merged = _merge(self._codec.encode(current), changes)
        changed = self._fit(self._codec.decode(merged))
        return ReplaceAnnotations([current], [changed], "Edit")

    @staticmethod
    def _delete(trial: Document, operation: JsonObject) -> Command:
        """Remove annotations by id."""
        ids = _ids(trial, operation)
        return RemoveAnnotations(ids)

    @staticmethod
    def _restack(trial: Document, operation: JsonObject) -> Command:
        """Move annotations to the top or bottom, keeping their relative order."""
        ids = _ids(trial, operation)
        to = operation.get("to")
        if to not in ("top", "bottom"):
            msg = f"to must be 'top' or 'bottom', got {to!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        order = [a.id for a in trial.annotations]
        moved = [i for i in order if i in ids]
        rest = [i for i in order if i not in ids]
        new = rest + moved if to == "top" else moved + rest
        return ReorderAnnotations(order, new, "Restack")

    @staticmethod
    def _crop(trial: Document, operation: JsonObject) -> Command:
        """Set the visible rectangle in image coordinates."""
        rect = _rect(operation.get("rect"))
        if rect.intersected(trial.image_rect).is_empty:
            msg = f"crop {operation.get('rect')!r} does not overlap the image"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return SetCrop(trial.crop, rect)

    @staticmethod
    def _rotate(trial: Document, operation: JsonObject) -> Command:
        """Turn the image a quarter turn."""
        del trial
        direction = operation.get("direction")
        if direction not in ("right", "left"):
            msg = f"direction must be 'right' or 'left', got {direction!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return TransformImage(Rotate(clockwise=direction == "right"))

    @staticmethod
    def _flip(trial: Document, operation: JsonObject) -> Command:
        """Mirror the image."""
        del trial
        axis = operation.get("axis")
        if axis not in ("horizontal", "vertical"):
            msg = f"axis must be 'horizontal' or 'vertical', got {axis!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return TransformImage(Flip(FlipAxis(axis)))

    @staticmethod
    def _resize(trial: Document, operation: JsonObject) -> Command:
        """Scale the visible image to an exact size."""
        del trial
        size: list[int] = []
        for name in ("width", "height"):
            value = operation.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                msg = f"{name} must be an integer, got {value!r}"
                raise AutomationError(ErrorCode.BAD_REQUEST, msg)
            size.append(value)
        return TransformImage(Resize(*size))


def _description(label: JsonValue, names: Sequence[str]) -> str:
    """Return "Automation: <label>", defaulting to the operations performed."""
    if label is None:
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        text = ", ".join(n if c == 1 else f"{n} x{c}" for n, c in counts.items())
    elif isinstance(label, str) and label.strip():
        text = " ".join(label.split())
    else:
        msg = "label must be a non-empty string"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    text = PREFIX + text
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


def _merge(base: JsonObject, changes: JsonObject) -> JsonObject:
    """Overlay ``changes`` on ``base``; a ``style`` object merges field by field."""
    merged = dict(base)
    for key, value in changes.items():
        old = merged.get(key)
        if key == "style" and isinstance(old, dict) and isinstance(value, dict):
            merged[key] = old | value
        else:
            merged[key] = value
    return merged


def _object(operation: JsonObject, key: str) -> JsonObject:
    """Return the object ``operation[key]``."""
    value = operation.get(key)
    if not isinstance(value, dict):
        msg = f"{key} must be an object, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _existing(trial: Document, ident: JsonValue) -> Annotation:
    """Return the annotation with id ``ident``."""
    if not isinstance(ident, str) or not ident:
        msg = "id must be a non-empty string"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    found = trial.find(ident)
    if found is None:
        msg = f"no annotation has id {ident!r}"
        raise AutomationError(ErrorCode.NOT_FOUND, msg)
    return found


def _ids(trial: Document, operation: JsonObject) -> list[str]:
    """Return the existing annotation ids listed in ``operation["ids"]``."""
    value = operation.get("ids")
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(i, str) for i in value)
    ):
        msg = "ids must be a non-empty array of annotation ids"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return [_existing(trial, str(i)).id for i in dict.fromkeys(value)]


def _rect(value: JsonValue) -> Rect:
    """Parse ``[x, y, width, height]`` with a positive size."""
    items = value if isinstance(value, list) else []
    numbers = [
        float(n)
        for n in items
        if isinstance(n, (int, float)) and not isinstance(n, bool)
    ]
    if len(numbers) != len(items) or len(numbers) != 4:  # noqa: PLR2004 - x, y, w, h
        msg = f"rect must be [x, y, width, height], got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    x, y, w, h = numbers
    if w <= 0 or h <= 0:
        msg = f"rect width and height must be positive, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return Rect(x, y, w, h)
