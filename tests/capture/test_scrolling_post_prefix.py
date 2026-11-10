"""One-shot observational lookahead never bypasses global full-width uniqueness."""

from collections.abc import Callable
from typing import override

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import Target, document

from verdiclip.capture.scrolling import (
    AmbiguousScrollError,
    PostPrefixBodyScrollError,
    ScrollPosition,
    ScrollTransaction,
    TopBoundaryScrollError,
    VerticalStitch,
    _HeaderFrames,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class CollapsingTarget(Target):
    """An authored visible document with one expanded-to-collapsed top transition."""

    @override
    def frame(self) -> QImage:
        image = super().frame()
        painter = QPainter(image)
        painter.fillRect(
            0,
            0,
            80,
            24 if self.offset == 0 else 12,
            QColor("blue" if self.offset == 0 else "green"),
        )
        painter.end()
        return image

    def expected(self) -> QImage:
        """Keep the original beginning without duplicating collapsed chrome."""
        image = self.document.copy()
        painter = QPainter(image)
        painter.fillRect(0, 0, 80, 24, QColor("blue"))
        painter.end()
        return image


def test_real_collapsing_prefix_lookahead_restarts_once_and_restores() -> None:
    """A real refusal leads to counted probes, exact restart and complete pixels."""
    target = CollapsingTarget()
    original = target.position()
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    result = operation.run()
    assert result.image == target.expected()
    assert result.restoration == "restored"
    assert target.position() == original
    offsets = [round(percent * 480 / 100) for percent in target.moves]
    assert offsets[:6] == [0, 60, 120, 180, 0, 60]
    assert offsets.count(0) == 2
    assert result.frames == 13


def observe_first_pair() -> tuple[
    CollapsingTarget, ScrollTransaction, ScrollPosition, QImage, TopBoundaryScrollError
]:
    """Retain the exact unique-registration top refusal and its actual observation."""
    target = CollapsingTarget(offset=0)
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    top = target.position()
    operation._expected = top
    original = operation._frame(top)
    following = operation._move(12.5)
    frames = _HeaderFrames(original, target.area, None)
    with pytest.raises(TopBoundaryScrollError) as failure:
        operation._append_step(
            VerticalStitch(original), frames, top, following, original
        )
    assert failure.value.observation is not None
    return target, operation, top, original, failure.value


@pytest.mark.parametrize("mutation", ["late_header", "original_anchor", "probe_header"])
def test_lookahead_rejects_changing_prefix_or_original_anchor(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """A learned prefix cannot be refreshed to follow later changes."""
    target = CollapsingTarget(offset=0)
    capture = target.frame

    def changed() -> QImage:
        image = capture()
        if (
            (mutation == "late_header" and target.offset >= 240)
            or (
                mutation == "original_anchor"
                and target.offset == 0
                and len(target.moves) >= 4
            )
            or (mutation == "probe_header" and target.offset == 120)
        ):
            image.setPixelColor(0, 0, QColor("red"))
        return image

    monkeypatch.setattr(target, "frame", changed)
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert len([percent for percent in target.moves if percent == 0]) <= 3


def test_probe_budget_rejects_before_additional_movement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two observations plus the exact return must all fit the existing budget."""
    target, operation, top, original, failure = observe_first_pair()
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 4)
    before = target.moves.copy()
    with pytest.raises(TopBoundaryScrollError):
        operation._learn_post_prefix(top, original, failure)
    assert target.moves == before
    assert operation._frames == 2


@pytest.mark.parametrize("stage", ["first_probe", "second_probe", "return"])
@pytest.mark.parametrize("failure_kind", ["input", "cancel", "deadline", "reserve"])
def test_probe_and_return_writes_keep_original_guards(
    monkeypatch: pytest.MonkeyPatch, stage: str, failure_kind: str
) -> None:
    """Lookahead never creates an independent deadline or write permission."""
    target, operation, top, original, failure = observe_first_pair()
    moves = {"first_probe": 1, "second_probe": 2, "return": 3}[stage]
    move = target.move

    def guarded(
        percent: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if len(target.moves) == moves:
            if failure_kind == "input":
                target.input_changed = True
                target.check()
            elif failure_kind == "cancel":
                operation._cancelled = lambda: True
            else:
                operation._clock = lambda: (
                    operation._deadline - (1 if failure_kind == "reserve" else 0)
                )
        move(percent, before_write=before_write)

    monkeypatch.setattr(target, "move", guarded)
    with pytest.raises(CaptureError):
        operation._learn_post_prefix(top, original, failure)
    assert len(target.moves) == moves


@pytest.mark.parametrize("restriction", ["edge", "header", "missing", "endpoint"])
def test_learning_is_only_first_generic_full_width_refusal(
    monkeypatch: pytest.MonkeyPatch, restriction: str
) -> None:
    """Missing observations, masked registration and native Headers cannot learn."""
    target, operation, top, original, failure = observe_first_pair()
    before = target.moves.copy()
    if restriction == "edge":
        failure.full_width = False
    elif restriction == "header":
        monkeypatch.setattr(target, "header", target.area)
    elif restriction == "missing":
        failure.observation = None
    else:
        assert failure.observation is not None
        position, image = failure.observation
        failure.observation = (
            ScrollPosition(position.horizontal, 100, position.view_size),
            image,
        )
    with pytest.raises(TopBoundaryScrollError):
        operation._learn_post_prefix(top, original, failure)
    assert target.moves == before


def test_frozen_prefix_is_checked_before_ambiguous_registration() -> None:
    """A later header change cannot be disguised as a retryable ambiguity."""
    target, operation, top, original, failure = observe_first_pair()
    prefix, _position, _image = operation._learn_post_prefix(top, original, failure)
    stitch = VerticalStitch(original, post_prefix=prefix)
    target.offset = 60
    stitch.append(target.frame(), 60)
    target.offset = 120
    image = target.frame()
    image.setPixelColor(0, 0, QColor("red"))
    with pytest.raises(CaptureError, match="post-transition prefix changed"):
        stitch.append(image, 60)
    assert stitch.finish().height() == 180


def test_top_refusal_with_wrong_provider_never_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pixel/percentage disagreement stays a failure rather than enabling lookahead."""
    target = CollapsingTarget(offset=0)
    capture = target.frame

    def wrong_pixels() -> QImage:
        if target.offset == 0:
            return capture()
        image = target.document.copy(0, target.offset + 10, 80, 120)
        painter = QPainter(image)
        painter.fillRect(0, 0, 80, 12, QColor("green"))
        painter.end()
        return image

    monkeypatch.setattr(target, "frame", wrong_pixels)
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert "disagree with its scroll position" in result.message
    assert target.moves == [12.5, 0]


def test_body_guard_rejects_mutation_without_relearning() -> None:
    """A long exact band elsewhere cannot excuse changes directly below the prefix."""
    source = document()
    first, following = source.copy(0, 0, 80, 200), source.copy(0, 60, 80, 200)
    painter = QPainter(first)
    painter.fillRect(0, 0, 80, 24, QColor("blue"))
    painter.end()
    painter = QPainter(following)
    painter.fillRect(0, 0, 80, 12, QColor("green"))
    painter.fillRect(0, 12, 80, 32, QColor("red"))
    painter.end()
    prefix = tuple(VerticalStitch._rows(following)[:12])
    stitch = VerticalStitch(first, post_prefix=prefix)
    with pytest.raises(
        PostPrefixBodyScrollError, match="body below the observed prefix"
    ):
        stitch.append(following, 60)
    assert stitch.finish() == first


def test_header_only_textured_match_cannot_prove_body() -> None:
    """A unique 32-row repeated header band is not moving-body evidence."""
    first = document(160)
    painter = QPainter(first)
    painter.drawImage(0, 48, first.copy(0, 0, 80, 32))
    painter.fillRect(0, 80, 80, 80, QColor("blue"))
    painter.end()
    second = first.copy()
    painter = QPainter(second)
    painter.fillRect(0, 80, 80, 80, QColor("red"))
    painter.end()
    prefix = tuple(VerticalStitch._rows(second)[:80])
    stitch = VerticalStitch(first, post_prefix=prefix)
    with pytest.raises(CaptureError, match="does not prove the visible body"):
        stitch.append(second, 48)


def test_clipped_band_must_have_body_texture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Texture above the frozen boundary cannot satisfy the body texture minimum."""
    image = document(200)
    painter = QPainter(image)
    painter.fillRect(0, 40, 80, 160, QColor("white"))
    painter.end()
    prefix = tuple(VerticalStitch._rows(image)[:40])
    stitch = VerticalStitch(image, post_prefix=prefix)
    monkeypatch.setattr(stitch, "_alignment", lambda *_args: (20, 5, 80))
    with pytest.raises(CaptureError, match="does not prove the visible body"):
        stitch.append(image, 20)


def test_post_mode_never_uses_edge_registration() -> None:
    """Changed edge columns cannot be cropped out of the observed-prefix proof."""
    source = document()
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 30, 80, 120)
    painter = QPainter(second)
    painter.fillRect(0, 0, 80, 12, QColor("green"))
    painter.fillRect(0, 12, 1, 108, QColor("red"))
    painter.end()
    prefix = tuple(VerticalStitch._rows(second)[:12])
    with pytest.raises(CaptureError, match="cannot be joined reliably"):
        VerticalStitch(first, post_prefix=prefix).append(second, 30)


@pytest.mark.parametrize("invalid", ["short", "tall", "row_width", "list"])
def test_post_prefix_constructor_rejects_invalid_boundaries(invalid: str) -> None:
    """Internal prefix evidence must still fit the unmasked generic viewport."""
    image = document(120)
    rows = tuple(VerticalStitch._rows(image))
    prefixes = {
        "short": rows[:1],
        "tall": rows[:90],
        "row_width": (b"x",) * 12,
        "list": rows[:12],
    }
    with pytest.raises(CaptureError, match="post-transition prefix is unsupported"):
        VerticalStitch(
            image,
            post_prefix=prefixes[invalid],
            nonrail_width=79 if invalid == "list" else None,
        )


class CollapsingRepeatTarget(Target):
    """A transition plus a later repeated snippet that disappears at a smaller step."""

    area = Rect(10, 20, 80, 568)

    def __init__(self) -> None:
        super().__init__(total=1800, offset=0)
        painter = QPainter(self.document)
        painter.drawImage(0, 900, self.document.copy(0, 752, 80, 52))
        painter.end()

    @override
    def position(self) -> ScrollPosition:
        """Use authored geometry rather than inferred pixel movement."""
        return ScrollPosition(
            self.horizontal, self.offset / 1232 * 100, 568 / 1800 * 100
        )

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Apply ordinary quantized movement with the production admission hook."""
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.offset = round(vertical * 1232 / 100)

    @override
    def frame(self) -> QImage:
        """Render the original expanded header or the frozen collapsed header."""
        image = self.document.copy(0, self.offset, 80, 568)
        painter = QPainter(image)
        painter.fillRect(
            0,
            0,
            80,
            64 if self.offset == 0 else 32,
            QColor("blue" if self.offset == 0 else "green"),
        )
        painter.end()
        return image


def test_real_learned_mode_ambiguity_requires_fresh_unique_smaller_step() -> None:
    """A relocated top guard never chooses among repeated body displacements."""
    target = CollapsingRepeatTarget()
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    top = target.position()
    operation._expected = top
    original = operation._frame(top)
    frames = _HeaderFrames(original, target.area, None)
    first_position = operation._move(284 / 1232 * 100)
    with pytest.raises(TopBoundaryScrollError) as failure:
        operation._append_step(
            VerticalStitch(original), frames, top, first_position, original
        )
    prefix, _returned, _raw = operation._learn_post_prefix(top, original, failure.value)
    assert len(prefix) == 32
    stitch = VerticalStitch(original, post_prefix=prefix)
    accepted = operation._move(284 / 1232 * 100)
    anchor = operation._frame(accepted)
    stitch.append(anchor, 284)
    following = operation._move(568 / 1232 * 100)
    rejected = operation._frame(following)
    with pytest.raises(AmbiguousScrollError):
        stitch.append(rejected, 284)
    smaller, _image = operation._append_step(
        stitch, frames, accepted, following, anchor
    )
    assert round(smaller.vertical * 1232 / 100) == 426
    expected = target.document.copy(0, 0, 80, 994)
    painter = QPainter(expected)
    painter.fillRect(0, 0, 80, 64, QColor("blue"))
    painter.end()
    assert stitch.finish() == expected


class ShortCollapsingTarget(CollapsingTarget):
    """A finite page with insufficient positions for two forward observations."""

    def __init__(self) -> None:
        super().__init__(total=240, offset=0)

    @override
    def position(self) -> ScrollPosition:
        """Report the short authored extent accurately."""
        return ScrollPosition(self.horizontal, self.offset / 120 * 100, 50)

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Retain normal write admission for the short extent."""
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.offset = round(vertical * 120 / 100)


def test_short_page_cannot_probe_past_endpoint() -> None:
    """A first-pair transition is insufficient evidence without two later views."""
    target = ShortCollapsingTarget()
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert "too short" in result.message
    assert target.moves == [50, 0]
    assert result.frames == 2


def test_probe_frame_dimensions_cannot_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """The independently observed prefix cannot come from a differently sized image."""
    target, operation, top, original, failure = observe_first_pair()
    capture = target.frame

    def resized() -> QImage:
        image = capture()
        return image.copy(0, 0, 79, 120) if target.offset == 120 else image

    monkeypatch.setattr(target, "frame", resized)
    with pytest.raises(CaptureError, match="changed size"):
        operation._learn_post_prefix(top, original, failure)
    assert len(target.moves) == 2


def test_changed_position_between_return_and_restart_aborts() -> None:
    """The exact top anchor is not permission to restart after a later movement."""
    target, operation, top, original, failure = observe_first_pair()
    learned = operation._learn_post_prefix(top, original, failure)
    before = target.moves.copy()
    target.offset = 1
    with pytest.raises(CaptureError, match="moved before capture restart"):
        operation._sequence(top, learned)
    assert target.moves == before


def test_header_appearing_after_committed_join_cannot_trigger_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the initial transition is eligible; there is no late header reset."""
    target = Target(offset=0)
    capture = target.frame

    def late_header() -> QImage:
        image = capture()
        if target.offset >= 120:
            painter = QPainter(image)
            painter.fillRect(0, 0, 80, 12, QColor("green"))
            painter.end()
        return image

    monkeypatch.setattr(target, "frame", late_header)
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert target.moves == [12.5, 25, 0]
    assert result.frames == 3


def test_initial_top_refusal_after_subdivision_retains_actual_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The still-uncommitted first join may learn from its actual smaller frame."""
    target = Target(offset=0)
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    top = target.position()
    operation._expected = top
    anchor = operation._frame(top)
    frames = _HeaderFrames(anchor, target.area, None)
    stitch = VerticalStitch(anchor)
    following = operation._move(12.5)

    def rejected(_image: QImage, expected: float | None, **_kwargs: object) -> None:
        if expected == 60:
            raise AmbiguousScrollError("repeated initial pixels")
        raise TopBoundaryScrollError("later top refusal", full_width=True, advance=45)

    monkeypatch.setattr(stitch, "append", rejected)
    with pytest.raises(TopBoundaryScrollError) as failure:
        operation._append_step(stitch, frames, top, following, anchor)
    assert failure.value.observation is not None
    observed, raw = failure.value.observation
    assert observed.vertical == 9.375
    assert raw == target.document.copy(0, 45, 80, 120)
    assert stitch.finish() == anchor
