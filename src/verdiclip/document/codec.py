"""Encode and decode annotations as versioned JSON (element copy/paste)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from verdiclip.document.annotations import (
    Annotation,
    AnnotationKind,
    ArrowShape,
    BoxAnnotation,
    CounterMarker,
    EllipseShape,
    FreehandShape,
    HighlightShape,
    JsonObject,
    JsonValue,
    LineShape,
    ObfuscateShape,
    RectangleShape,
    TextNote,
)
from verdiclip.document.style import Color, Style
from verdiclip.exceptions import CodecError
from verdiclip.geometry import Point, Rect


class StyleCodec:
    """Convert styles to and from JSON-compatible dictionaries."""

    @staticmethod
    def encode(style: Style) -> JsonObject:
        """Return a JSON-compatible dictionary for ``style``."""
        return {
            "stroke": style.stroke.hex,
            "fill": style.fill.hex,
            "width": style.width,
            "font_family": style.font_family,
            "font_size": style.font_size,
            "bold": style.bold,
            "italic": style.italic,
        }

    @staticmethod
    def decode(data: JsonValue) -> Style:
        """Parse a style dictionary.

        Raises:
            CodecError: If fields are missing or invalid.
        """
        s = _JsonReader(data, "style")
        try:
            return Style(
                stroke=Color.from_hex(s.string("stroke")),
                fill=Color.from_hex(s.string("fill")),
                width=s.number("width"),
                font_family=s.string("font_family"),
                font_size=s.integer("font_size"),
                bold=s.boolean("bold"),
                italic=s.boolean("italic"),
            )
        except ValueError as err:
            msg = f"Invalid style: {err}"
            raise CodecError(msg) from err


class AnnotationCodec:
    """Convert annotations to and from JSON-compatible dictionaries."""

    FORMAT_VERSION: ClassVar[int] = 1

    def encode(self, annotation: Annotation) -> JsonObject:
        """Return a JSON-compatible dictionary for ``annotation``."""
        return {
            "kind": annotation.kind.value,
            "id": annotation.id,
            "style": StyleCodec.encode(annotation.style),
            **annotation.geometry_json(),
        }

    def encode_many(self, annotations: list[Annotation]) -> JsonObject:
        """Return a versioned envelope holding ``annotations``."""
        return {
            "version": self.FORMAT_VERSION,
            "annotations": [self.encode(a) for a in annotations],
        }

    def decode_many(self, data: JsonValue) -> list[Annotation]:
        """Parse an envelope produced by ``encode_many``.

        Raises:
            CodecError: If the data is not a valid envelope.
        """
        envelope = _JsonReader(data, "envelope")
        if envelope.integer("version") != self.FORMAT_VERSION:
            msg = f"Unsupported annotation format version: {envelope.raw('version')}"
            raise CodecError(msg)
        return [self.decode(item) for item in envelope.items("annotations")]

    def decode(self, data: JsonValue) -> Annotation:
        """Parse one annotation dictionary.

        Raises:
            CodecError: If required fields are missing or malformed.
        """
        reader = _JsonReader(data, "annotation")
        try:
            kind = AnnotationKind(reader.string("kind"))
        except ValueError as err:
            msg = f"Unknown annotation kind: {reader.raw('kind')!r}"
            raise CodecError(msg) from err
        common = {
            "id": reader.string("id"),
            "style": StyleCodec.decode(reader.raw("style")),
        }
        try:
            return self._build(kind, reader, common)
        except ValueError as err:
            msg = f"Invalid {kind.value} annotation: {err}"
            raise CodecError(msg) from err

    @staticmethod
    def _build(
        kind: AnnotationKind, r: _JsonReader, common: Mapping[str, str | Style]
    ) -> Annotation:
        """Construct the annotation class for ``kind``."""
        ident = str(common["id"])
        style = common["style"]
        if not isinstance(style, Style):  # pragma: no cover - guaranteed by caller
            raise TypeError(style)
        box_types: dict[AnnotationKind, type[BoxAnnotation]] = {
            AnnotationKind.HIGHLIGHT: HighlightShape,
            AnnotationKind.OBFUSCATE: ObfuscateShape,
        }
        if kind in (AnnotationKind.RECTANGLE, AnnotationKind.ELLIPSE):
            labeled = (
                RectangleShape if kind is AnnotationKind.RECTANGLE else EllipseShape
            )
            return labeled(
                id=ident,
                style=style,
                rect=r.rect("rect"),
                text=r.optional_string("text"),
            )
        if kind in box_types:
            return box_types[kind](id=ident, style=style, rect=r.rect("rect"))
        if kind in (AnnotationKind.LINE, AnnotationKind.ARROW):
            line_type = ArrowShape if kind is AnnotationKind.ARROW else LineShape
            return line_type(
                id=ident, style=style, start=r.point("start"), end=r.point("end")
            )
        if kind is AnnotationKind.FREEHAND:
            points = tuple(r.point_list("points"))
            return FreehandShape(id=ident, style=style, points=points)
        if kind is AnnotationKind.TEXT:
            return TextNote(
                id=ident, style=style, rect=r.rect("rect"), text=r.string("text")
            )
        return CounterMarker(
            id=ident,
            style=style,
            center=r.point("center"),
            radius=r.number("radius"),
            label=r.string("label"),
        )


class _JsonReader:
    """Typed accessors over an untrusted JSON object, raising CodecError."""

    def __init__(self, data: JsonValue, context: str) -> None:
        if not isinstance(data, dict):
            msg = f"Expected a JSON object for {context}, got {type(data).__name__}"
            raise CodecError(msg)
        self._data = data
        self._context = context

    def raw(self, key: str) -> JsonValue:
        """Return the raw value or raise if missing."""
        if key not in self._data:
            msg = f"Missing field {key!r} in {self._context}"
            raise CodecError(msg)
        return self._data[key]

    def child(self, key: str) -> _JsonReader:
        """Return a reader for a nested object."""
        return _JsonReader(self.raw(key), f"{self._context}.{key}")

    def string(self, key: str) -> str:
        """Return a string field."""
        value = self.raw(key)
        if not isinstance(value, str):
            raise self._type_error(key, "a string", value)
        return value

    def optional_string(self, key: str) -> str:
        """Return a string field, or "" when absent (older clipboard data)."""
        return self.string(key) if key in self._data else ""

    def boolean(self, key: str) -> bool:
        """Return a boolean field."""
        value = self.raw(key)
        if not isinstance(value, bool):
            raise self._type_error(key, "a boolean", value)
        return value

    def integer(self, key: str) -> int:
        """Return an integer field."""
        value = self.raw(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._type_error(key, "an integer", value)
        return value

    def number(self, key: str) -> float:
        """Return a numeric field as float."""
        return self._as_number(key, self.raw(key))

    def items(self, key: str) -> list[JsonValue]:
        """Return a list field."""
        value = self.raw(key)
        if not isinstance(value, list):
            raise self._type_error(key, "a list", value)
        return value

    def point(self, key: str) -> Point:
        """Return a ``[x, y]`` field as a Point."""
        return self._point_from(key, self.raw(key))

    def point_list(self, key: str) -> list[Point]:
        """Return a list of ``[x, y]`` pairs."""
        points = [self._point_from(key, item) for item in self.items(key)]
        if not points:
            msg = f"Field {key!r} in {self._context} must not be empty"
            raise CodecError(msg)
        return points

    def rect(self, key: str) -> Rect:
        """Return a ``[x, y, w, h]`` field as a Rect."""
        values = self.items(key)
        if len(values) != 4:  # noqa: PLR2004 — x, y, w, h
            raise self._type_error(key, "[x, y, width, height]", values)
        x, y, w, h = (self._as_number(key, v) for v in values)
        return Rect(x, y, w, h)

    def _point_from(self, key: str, value: JsonValue) -> Point:
        """Convert ``[x, y]`` to a Point."""
        if not isinstance(value, list) or len(value) != 2:  # noqa: PLR2004 — x, y
            raise self._type_error(key, "[x, y]", value)
        return Point(self._as_number(key, value[0]), self._as_number(key, value[1]))

    def _as_number(self, key: str, value: JsonValue) -> float:
        """Convert an int or float JSON value to float."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._type_error(key, "a number", value)
        return float(value)

    def _type_error(self, key: str, expected: str, value: JsonValue) -> CodecError:
        """Build a descriptive type error."""
        msg = f"Field {key!r} in {self._context} must be {expected}, got {value!r}"
        return CodecError(msg)
