"""Test formatted spans: normalizing, restyling, and slicing."""

from __future__ import annotations

from dataclasses import replace

import pytest

from verdiclip.document.annotations import EllipseShape, RectangleShape, TextNote
from verdiclip.document.rich_text import (
    RichText,
    TextAttr,
    TextFormat,
    TextSpan,
    utf16_offsets,
)
from verdiclip.document.style import Style
from verdiclip.geometry import Rect

PLAIN = TextFormat()
BOLD = TextFormat(bold=True)
UNDER = TextFormat(underline=True)


class TestTextSpan:
    """One overridden range."""

    @pytest.mark.parametrize(("start", "end"), [(-1, 2), (3, 3), (4, 2)])
    def test_rejects_empty_or_negative_ranges(self, start: int, end: int) -> None:
        """A span covers at least one character from 0 on."""
        with pytest.raises(ValueError, match="start < end"):
            TextSpan(start, end, bold=True)

    def test_overrides_only_what_it_names(self) -> None:
        """Unset attributes keep the base format."""
        span = TextSpan(0, 1, underline=True, italic=False)

        assert span.get(TextAttr.UNDERLINE) is True
        assert span.get(TextAttr.ITALIC) is False
        assert span.get(TextAttr.BOLD) is None
        base = TextFormat(bold=True, italic=True)
        assert span.applied(base) == TextFormat(bold=True, underline=True)


class TestTextFormat:
    """The effective format of a character."""

    def test_of_style_and_switching(self) -> None:
        """A style's flags become the format; with_attr flips one."""
        fmt = TextFormat.of(Style(bold=True, underline=True))

        assert fmt == TextFormat(bold=True, underline=True)
        assert fmt.get(TextAttr.BOLD)
        assert not fmt.get(TextAttr.ITALIC)
        assert fmt.with_attr(TextAttr.BOLD, on=False) == UNDER


class TestRichText:
    """Conversions between spans and per-character formats."""

    def test_formats_apply_later_spans_last_and_clip(self) -> None:
        """Overlapping spans stack in order; spans past the text are ignored."""
        spans = [TextSpan(0, 3, bold=True), TextSpan(2, 9, bold=False)]

        formats = RichText.formats(4, spans, Style())

        assert formats == [BOLD, BOLD, PLAIN, PLAIN]

    def test_spans_merge_runs_and_name_only_differences(self) -> None:
        """Equal neighbors merge; a span names only what differs from the style."""
        style = Style(italic=True)
        both = TextFormat(bold=True, italic=True)
        formats = [both, both, TextFormat(italic=True), TextFormat(underline=True)]

        spans = RichText.spans(formats, style)

        assert spans == (
            TextSpan(0, 2, bold=True),
            TextSpan(3, 4, italic=False, underline=True),
        )

    def test_normalized_drops_spans_that_change_nothing(self) -> None:
        """A bold span on bold text is redundant."""
        spans = (TextSpan(0, 2, bold=True), TextSpan(5, 8, underline=True))

        assert RichText.normalized(spans, 6, Style(bold=True)) == (
            TextSpan(5, 6, underline=True),
        )

    def test_runs_cover_the_whole_text(self) -> None:
        """Runs list every character once, formatted or not."""
        runs = RichText.runs("one two", (TextSpan(4, 7, underline=True),), Style())

        assert runs == [(0, 4, PLAIN), (4, 7, UNDER)]
        assert RichText.runs("", (), Style()) == []

    def test_restyled_resets_only_the_changed_attribute(self) -> None:
        """Bolding all text clears bold spans but keeps an underlined word."""
        spans = (TextSpan(0, 3, bold=True), TextSpan(4, 7, underline=True))
        old = Style()
        new = replace(old, bold=True)

        assert RichText.restyled(spans, 7, old, new) == (
            TextSpan(4, 7, underline=True),
        )
        unbolded = RichText.restyled(spans, 7, new, old)
        assert unbolded == (TextSpan(4, 7, underline=True),)

    def test_restyled_keeps_spans_for_other_changes(self) -> None:
        """A color or size change leaves the words' formats alone."""
        spans = (TextSpan(4, 7, italic=True),)

        result = RichText.restyled(spans, 7, Style(), Style(font_size=40))

        assert result == spans

    def test_sliced_shifts_to_the_start(self) -> None:
        """A slice keeps its characters' formats, counted from its start."""
        spans = (TextSpan(2, 6, bold=True),)

        assert RichText.sliced(spans, 4, 8, Style()) == (TextSpan(0, 2, bold=True),)


class TestFormattedAnnotations:
    """Text-bearing annotations keep their spans normalized."""

    @pytest.mark.parametrize("kind", [RectangleShape, EllipseShape])
    def test_labels_clip_spans_to_their_text(
        self, kind: type[RectangleShape | EllipseShape]
    ) -> None:
        """Spans past the label's end are trimmed or dropped."""
        box = kind(
            rect=Rect(0, 0, 10, 10),
            text="abc",
            spans=(TextSpan(1, 9, underline=True), TextSpan(5, 7, bold=True)),
        )

        assert box.spans == (TextSpan(1, 3, underline=True),)
        assert box.with_text("abcdef").spans == ()
        kept = box.with_text("ab", (TextSpan(0, 1, italic=True),))
        assert kept.spans == (TextSpan(0, 1, italic=True),)

    def test_whole_text_toggle_resets_matching_spans(self) -> None:
        """with_style bolding everything clears per-word bold, keeps underline."""
        note = TextNote(
            rect=Rect(0, 0, 10, 10),
            text="one two",
            spans=(TextSpan(0, 3, bold=True), TextSpan(4, 7, underline=True)),
        )
        box = RectangleShape(rect=Rect(0, 0, 10, 10), text="ab", spans=note.spans)

        bolded = note.with_style(replace(note.style, bold=True))

        assert bolded.style.bold
        assert bolded.spans == (TextSpan(4, 7, underline=True),)
        underlined = box.with_style(replace(box.style, underline=True))
        assert underlined.spans == (TextSpan(0, 2, bold=True),)

    def test_json_lists_spans_only_when_formatted(self) -> None:
        """Plain text keeps its old JSON; formatted text adds its spans."""
        plain = TextNote(rect=Rect(0, 0, 10, 10), text="hi")
        formatted = replace(plain, spans=(TextSpan(0, 1, bold=True, italic=False),))

        assert "spans" not in plain.geometry_json()
        assert formatted.geometry_json()["spans"] == [
            {"start": 0, "end": 1, "bold": True}
        ]
        box = RectangleShape(rect=Rect(0, 0, 1, 1), text="x", spans=formatted.spans)
        assert box.geometry_json()["spans"] == [{"start": 0, "end": 1, "bold": True}]


def test_utf16_offsets_count_emoji_twice() -> None:
    """Qt positions count characters outside the BMP as two units."""
    assert utf16_offsets("a\U0001f600b") == [0, 1, 3, 4]
    assert utf16_offsets("") == [0]


def test_with_all_sets_every_character() -> None:
    """Formatting all text clears overrides even when the style already matches."""
    spans = (TextSpan(0, 2, bold=True), TextSpan(3, 4, italic=True))

    assert RichText.with_all(spans, 4, Style(), TextAttr.BOLD, on=False) == (
        TextSpan(3, 4, italic=True),
    )


def test_plain_lines_moves_spans_with_line_breaks() -> None:
    """CRLF and other breaks become one newline; formats stay on their words."""
    text, spans = RichText.plain_lines(
        "one\r\ntwo\rthree" + chr(0x2028) + "x",
        (TextSpan(5, 8, underline=True),),
        Style(),
    )

    assert text == "one\ntwo\nthree\nx"
    assert spans == (TextSpan(4, 7, underline=True),)
