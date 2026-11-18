"""Bold, italic, and underline applied to parts of an annotation's text.

An annotation's :class:`~verdiclip.document.style.Style` sets the format of
all its text; :class:`TextSpan` values override it for ranges of characters,
so a single word can be underlined. Spans are kept normalized: sorted, not
overlapping, inside the text, and naming only attributes that differ from the
style.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import groupby
from typing import Final

from verdiclip.document.style import Style


class TextAttr(StrEnum):
    """A character format that can be switched on or off."""

    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"


ATTRS: Final = tuple(TextAttr)


@dataclass(frozen=True, slots=True)
class TextFormat:
    """The effective format of one character."""

    bold: bool = False
    italic: bool = False
    underline: bool = False

    @classmethod
    def of(cls, style: Style) -> TextFormat:
        """Return the format ``style`` gives text without spans."""
        return cls(bold=style.bold, italic=style.italic, underline=style.underline)

    def get(self, attr: TextAttr) -> bool:
        """Return whether ``attr`` is on."""
        return bool(getattr(self, attr.value))

    def with_attr(self, attr: TextAttr, *, on: bool) -> TextFormat:
        """Return a copy with ``attr`` switched ``on`` or off."""
        return replace(self, **{attr.value: on})


@dataclass(frozen=True, slots=True)
class TextSpan:
    """Characters ``start`` (inclusive) to ``end`` (exclusive) with overrides.

    ``None`` keeps the annotation style's value for that attribute.
    """

    start: int
    end: int
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None

    def __post_init__(self) -> None:
        """Reject empty or negative ranges."""
        if not 0 <= self.start < self.end:
            msg = (
                f"Text span must satisfy 0 <= start < end, got {self.start}-{self.end}"
            )
            raise ValueError(msg)

    def get(self, attr: TextAttr) -> bool | None:
        """Return the override for ``attr``, or None to use the style."""
        value = getattr(self, attr.value)
        return None if value is None else bool(value)

    def applied(self, base: TextFormat) -> TextFormat:
        """Return ``base`` with this span's overrides applied."""
        result = base
        for attr in ATTRS:
            value = self.get(attr)
            if value is not None:
                result = result.with_attr(attr, on=value)
        return result


def utf16_offsets(text: str) -> list[int]:
    """Return each character's UTF-16 offset, then the text's UTF-16 length.

    Qt counts positions in UTF-16 code units, so a character outside the
    Basic Multilingual Plane (such as most emoji) takes two.
    """
    offsets: list[int] = [0]
    position = 0
    for char in text:
        position += 2 if ord(char) > 0xFFFF else 1  # noqa: PLR2004 — BMP limit
        offsets.append(position)
    return offsets


class RichText:
    """Stateless conversions between spans and per-character formats."""

    @staticmethod
    def formats(
        length: int, spans: Iterable[TextSpan], style: Style
    ) -> list[TextFormat]:
        """Return the effective format of each of ``length`` characters."""
        base = TextFormat.of(style)
        result = [base] * length
        for span in spans:
            for i in range(span.start, min(span.end, length)):
                result[i] = span.applied(result[i])
        return result

    @staticmethod
    def spans(formats: Sequence[TextFormat], style: Style) -> tuple[TextSpan, ...]:
        """Return normalized spans that give ``formats`` over ``style``."""
        base = TextFormat.of(style)
        result: list[TextSpan] = []
        position = 0
        for fmt, run in groupby(formats):
            end = position + len(list(run))
            if fmt != base:
                overrides = {
                    attr.value: fmt.get(attr)
                    for attr in ATTRS
                    if fmt.get(attr) != base.get(attr)
                }
                result.append(TextSpan(position, end, **overrides))
            position = end
        return tuple(result)

    @staticmethod
    def normalized(
        spans: Iterable[TextSpan], length: int, style: Style
    ) -> tuple[TextSpan, ...]:
        """Return ``spans`` clipped to the text and reduced to their effect."""
        return RichText.spans(RichText.formats(length, spans, style), style)

    @staticmethod
    def runs(
        text: str, spans: Iterable[TextSpan], style: Style
    ) -> list[tuple[int, int, TextFormat]]:
        """Return ``(start, end, format)`` for each run of equal formatting."""
        result: list[tuple[int, int, TextFormat]] = []
        position = 0
        for fmt, run in groupby(RichText.formats(len(text), spans, style)):
            end = position + len(list(run))
            result.append((position, end, fmt))
            position = end
        return result

    @staticmethod
    def restyled(
        spans: Iterable[TextSpan], length: int, old: Style, new: Style
    ) -> tuple[TextSpan, ...]:
        """Return spans for ``new`` that keep each character's format.

        Only attributes whose whole-text value changed are reset to it, so
        switching bold on for all text clears bold overrides but keeps an
        underlined word underlined.
        """
        before = TextFormat.of(old)
        after = TextFormat.of(new)
        changed = [a for a in ATTRS if before.get(a) != after.get(a)]
        formats = RichText.formats(length, spans, old)
        for attr in changed:
            on = after.get(attr)
            formats = [f.with_attr(attr, on=on) for f in formats]
        return RichText.spans(formats, new)

    @staticmethod
    def with_all(
        spans: Iterable[TextSpan],
        length: int,
        style: Style,
        attr: TextAttr,
        *,
        on: bool,
    ) -> tuple[TextSpan, ...]:
        """Return spans for ``style`` after setting ``attr`` for every character."""
        formats = RichText.formats(length, spans, style)
        return RichText.spans([f.with_attr(attr, on=on) for f in formats], style)

    @staticmethod
    def sliced(
        spans: Iterable[TextSpan], start: int, end: int, style: Style
    ) -> tuple[TextSpan, ...]:
        """Return the spans for characters ``start`` to ``end``, shifted to 0."""
        formats = RichText.formats(end, spans, style)[start:end]
        return RichText.spans(formats, style)
