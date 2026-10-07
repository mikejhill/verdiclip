"""Test versioned annotation serialization and malformed payloads."""

from __future__ import annotations

import pytest

from verdiclip.document.annotations import (
    Annotation,
    ArrowShape,
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
from verdiclip.document.codec import AnnotationCodec
from verdiclip.exceptions import CodecError
from verdiclip.geometry import Point, Rect


class TestAnnotationCodec:
    """Serialization of every annotation and strict JSON field types."""

    @pytest.mark.parametrize(
        "annotation",
        [
            RectangleShape(rect=Rect(1, 2, 30, 40)),
            EllipseShape(rect=Rect(1, 2, 30, 40)),
            HighlightShape(rect=Rect(1, 2, 30, 40)),
            ObfuscateShape(rect=Rect(1, 2, 30, 40)),
            LineShape(start=Point(1, 2), end=Point(3, 4)),
            ArrowShape(start=Point(1, 2), end=Point(3, 4)),
            FreehandShape(points=(Point(1, 2),)),
            TextNote(rect=Rect(1, 2, 30, 40), text="æ—¥æœ¬èªž\ntext"),
            CounterMarker(center=Point(1, 2), radius=12, label="A"),
        ],
        ids=[
            "rectangle",
            "ellipse",
            "highlight",
            "obfuscate",
            "line",
            "arrow",
            "freehand",
            "text",
            "counter",
        ],
    )
    def test_round_trip(self, annotation: Annotation) -> None:
        """Versioned envelopes retain type, identity, style, and geometry."""
        codec = AnnotationCodec()

        result = codec.decode_many(codec.encode_many([annotation]))

        assert result == [annotation]

    @pytest.mark.parametrize(
        "data",
        [
            None,
            [],
            {},
            {"version": True},
            {"version": 2},
            {"version": 1, "annotations": "bad"},
            {"version": 1, "annotations": [None]},
        ],
        ids=[
            "null",
            "list",
            "missing-version",
            "boolean-version",
            "unsupported-version",
            "not-list",
            "invalid-item",
        ],
    )
    def test_invalid_envelope(self, data: JsonValue) -> None:
        """Malformed envelopes raise CodecError with diagnostic context."""
        codec = AnnotationCodec()

        with pytest.raises(
            CodecError, match=r"object|Missing field|integer|Unsupported|list"
        ):
            codec.decode_many(data)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("kind", "unknown"),
            ("kind", 1),
            ("id", False),
            ("rect", [0, 0]),
            ("rect", [0, 0, -1, 2]),
            ("rect", [0, 0, True, 2]),
            ("style", None),
        ],
        ids=[
            "unknown-kind",
            "numeric-kind",
            "boolean-id",
            "short-rect",
            "negative-rect",
            "boolean-number",
            "null-style",
        ],
    )
    def test_invalid_annotation(self, field: str, value: JsonValue) -> None:
        """Malformed geometry and common fields cannot enter the document."""
        codec = AnnotationCodec()
        data = codec.encode(RectangleShape(rect=Rect(0, 0, 10, 10)))
        data[field] = value

        with pytest.raises(CodecError, match=r"Unknown|must be|Invalid|object"):
            codec.decode(data)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("stroke", "red"),
            ("width", 0),
            ("font_size", True),
            ("bold", 1),
            ("italic", "yes"),
            ("font_family", None),
        ],
        ids=[
            "color",
            "width",
            "boolean-font",
            "numeric-bold",
            "string-italic",
            "null-family",
        ],
    )
    def test_invalid_style(self, field: str, value: JsonValue) -> None:
        """Invalid style values raise CodecError instead of leaking ValueError."""
        codec = AnnotationCodec()
        data = codec.encode(RectangleShape(rect=Rect(0, 0, 10, 10)))
        style = data["style"]
        assert isinstance(style, dict)
        style[field] = value

        with pytest.raises(CodecError, match=r"Invalid style|must be"):
            codec.decode(data)

    @pytest.mark.parametrize(
        "points",
        [[], [None], [[1]], [[1, "bad"]]],
        ids=["empty", "null-point", "short-point", "invalid-coordinate"],
    )
    def test_invalid_freehand(self, points: list[JsonValue]) -> None:
        """Freehand requires at least one numeric coordinate pair."""
        codec = AnnotationCodec()
        data: JsonObject = codec.encode(FreehandShape(points=(Point(0, 0),)))
        data["points"] = points

        with pytest.raises(CodecError, match=r"must not be empty|must be"):
            codec.decode(data)

    def test_empty_envelope(self) -> None:
        """An empty valid envelope decodes without annotations."""
        codec = AnnotationCodec()

        decoded = codec.decode_many(codec.encode_many([]))

        assert decoded == []
