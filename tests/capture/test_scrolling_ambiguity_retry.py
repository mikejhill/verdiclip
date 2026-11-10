"""Bounded acquisition retries preserve unique pixel proof and native guards."""

import math
from collections.abc import Callable
from typing import override

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import Target
from tests.capture.test_scrolling_fixed_prefix import frame, patterned

from verdiclip.capture.scrolling import (
    AmbiguousScrollError,
    ScrollPosition,
    ScrollTransaction,
    VerticalStitch,
    _HeaderFrames,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def setup() -> tuple[
    Target, ScrollTransaction, VerticalStitch, _HeaderFrames, ScrollPosition, QImage
]:
    """Start at a verified anchor and complete one ordinary forward acquisition."""
    target = Target(offset=0)
    transaction = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    transaction._expected = target.position()
    transaction._frames = 1
    anchor = target.frame()
    frames = _HeaderFrames(anchor, target.area, None)
    stitch = VerticalStitch(anchor)
    return target, transaction, stitch, frames, target.position(), anchor


def ambiguous(_image: QImage, _expected: float | None, **_kwargs: object) -> None:
    """Represent an atomic matcher rejection, not a provider failure."""
    raise AmbiguousScrollError("ambiguous test pixels")


def test_unique_smaller_step_after_exact_anchor_reacquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stitch changes until a smaller independently verified join succeeds."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    append = stitch.append

    def conditional(image: QImage, expected: float | None, **_kwargs: object) -> None:
        if expected == 60:
            ambiguous(image, expected)
        append(image, expected)

    monkeypatch.setattr(stitch, "append", conditional)
    result, raw = operation._append_step(stitch, frames, accepted, following, anchor)
    assert result.vertical == 9.375
    assert target.moves == [12.5, 0, 9.375]
    assert operation._frames == 4  # Initial, rejected, exact anchor, accepted.
    assert raw == target.document.copy(0, 45, 80, 120)
    assert stitch.finish() == target.document.copy(0, 0, 80, 165)


def test_four_retries_are_hard_bounded_without_stitch_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Irreducible repetition cannot extend retries or publish partial output."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    with pytest.raises(AmbiguousScrollError):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert len(target.moves) == 9  # One original plus four return/midpoint pairs.
    assert operation._frames == 10
    assert stitch.finish() == anchor
    assert stitch._fixed_prefix is None


def test_anchor_mutation_aborts_before_midpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning to the same position is insufficient if any raw pixel changed."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    target.document.setPixelColor(0, 0, QColor("red"))
    with pytest.raises(CaptureError, match="anchor pixels changed"):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert target.moves == [12.5, 0]
    assert stitch.finish() == anchor


@pytest.mark.parametrize("failure", ["provider", "mutation", "guard"])
def test_other_capture_errors_never_retry(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Only the typed ambiguous-pixel failure admits another native write."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)

    def fail(_image: QImage, _expected: float | None, **_kwargs: object) -> None:
        raise CaptureError(failure)

    monkeypatch.setattr(stitch, "append", fail)
    with pytest.raises(CaptureError, match=failure):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert target.moves == [12.5]


@pytest.mark.parametrize("route", ["header", "footer", "native", "list", "extent"])
def test_non_generic_routes_never_retry(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """A selected List capability or changed extent cannot enter generic recovery."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    if route == "header":
        target.header = Rect(10, 20, 80, 10)
    elif route == "footer":
        monkeypatch.setattr(target, "footer", Rect(10, 130, 80, 10), raising=False)
    elif route == "native":
        monkeypatch.setattr(operation, "_native", object())
    elif route == "list":
        monkeypatch.setattr(operation, "_list_steps", object())
    else:
        accepted = ScrollPosition(accepted.horizontal, accepted.vertical, 19)
    with pytest.raises(AmbiguousScrollError):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert target.moves == [12.5]


def test_retry_budget_counts_anchor_before_any_return_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insufficient space for both anchor and midpoint frames rejects before moving."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 3)
    with pytest.raises(CaptureError, match="page limit"):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert operation._frames == 2
    assert target.moves == [12.5]


@pytest.mark.parametrize("stage", ["return", "midpoint"])
@pytest.mark.parametrize("failure", ["input", "cancel", "deadline", "reserve"])
def test_retry_writes_keep_original_admission_guards(
    monkeypatch: pytest.MonkeyPatch, stage: str, failure: str
) -> None:
    """Interruption at either new write cannot slip past its existing admission."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    move = target.move
    stop_count = 1 if stage == "return" else 2

    def guarded(
        percent: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if len(target.moves) == stop_count:
            if failure == "input":
                target.input_changed = True
                target.check()
            elif failure == "cancel":
                operation._cancelled = lambda: True
            else:
                operation._clock = lambda: (
                    operation._deadline - (1 if failure == "reserve" else 0)
                )
        move(percent, before_write=before_write)

    monkeypatch.setattr(target, "move", guarded)
    with pytest.raises(CaptureError):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert len(target.moves) == stop_count
    assert stitch.finish() == anchor


def test_unique_pixels_disagreeing_with_provider_never_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An actual unique pixel displacement cannot be replaced by a nearer retry."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(
        target, "frame", lambda: target.document.copy(0, target.offset + 10, 80, 120)
    )
    with pytest.raises(CaptureError, match="disagree with its scroll position"):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert target.moves == [12.5]
    assert stitch.finish() == anchor


def test_return_requires_exact_nonterminal_anchor_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directional settling alone is insufficient to revalidate a retry anchor."""
    target, operation, _stitch, _frames, _accepted, _anchor = setup()
    accepted = operation._move(6.25)
    anchor = target.frame()
    rejected = operation._move(18.75)
    move = target.move

    def almost(
        percent: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        move(percent, before_write=before_write)
        target.offset += 1

    monkeypatch.setattr(target, "move", almost)
    with pytest.raises(CaptureError, match="anchor position"):
        operation._retry_smaller_step(accepted, rejected, anchor)
    assert len(target.moves) == 3


@pytest.mark.parametrize("landing", ["unchanged", "past_rejected", "extent"])
def test_midpoint_requires_smaller_unchanged_extent_interval(
    monkeypatch: pytest.MonkeyPatch, landing: str
) -> None:
    """Quantization and provider surprises cannot turn a retry into a large jump."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr(stitch, "append", ambiguous)
    move = target.move

    def wrong(
        percent: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        midpoint = len(target.moves) == 2
        if midpoint and landing == "unchanged":
            target.ignore_move = True
        move(percent, before_write=before_write)
        if midpoint and landing == "past_rejected":
            target.offset = 61
        if midpoint and landing == "extent":
            original = target.position
            monkeypatch.setattr(
                target,
                "position",
                lambda: ScrollPosition(original().horizontal, original().vertical, 21),
            )

    monkeypatch.setattr(target, "move", wrong)
    with pytest.raises(CaptureError):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert len(target.moves) == 3
    assert stitch.finish() == anchor


def test_real_ambiguous_pair_leaves_selected_interval_and_image_unchanged() -> None:
    """An actual repeated-body rejection cannot mutate the accepted stitch."""
    source = patterned()
    painter = QPainter(source)
    tile = source.copy(0, 64, 80, 60)
    for index, y in enumerate(range(64, 1200, 60)):
        painter.drawImage(0, y, tile)
        if index % 4 == 0:
            painter.fillRect(20, y, 20, 10, QColor(index, 200, 30))
    painter.end()
    stitch = VerticalStitch(frame(source, 0, outline=True))
    stitch.append(frame(source, 284, outline=True), 284)
    original = stitch.finish()
    interval = stitch._fixed_prefix
    with pytest.raises(AmbiguousScrollError):
        stitch.append(frame(source, 568, outline=True), 284)
    assert stitch.finish() == original
    assert stitch._fixed_prefix == interval


class RepeatedCardsTarget(Target):
    """Finite authored cards with repeated bodies and sparse unique identifiers."""

    area = Rect(10, 20, 80, 568)

    def __init__(self) -> None:
        super().__init__(total=1200, offset=100)
        painter = QPainter(self.document)
        painter.fillRect(0, 0, 3, 1200, QColor("white"))
        painter.fillRect(77, 0, 3, 1200, QColor("white"))
        tile = self.document.copy(0, 64, 80, 60)
        for index, y in enumerate(range(64, 1200, 60)):
            painter.drawImage(0, y, tile)
            if index % 4 == 0:
                painter.fillRect(20, y, 20, 10, QColor(index, 200, 30))
        painter.end()

    @override
    def position(self) -> ScrollPosition:
        """Report exact authored extent independently of bitmap registration."""
        return ScrollPosition(
            self.horizontal, self.offset / 632 * 100, 568 / 1200 * 100
        )

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Quantize native-style percentage setters to physical document rows."""
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.offset = round(vertical * 632 / 100)

    @override
    def frame(self) -> QImage:
        """Expose the real repeated pixels, header and bounded native-style outline."""
        self.count += 1
        return frame(self.document, self.offset, outline=True)

    def expected(self) -> QImage:
        """Render the authored full document without using matcher offsets or seams."""
        image = self.document.copy()
        painter = QPainter(image)
        painter.drawImage(
            0, 0, frame(self.document, 0, outline=True).copy(0, 0, 80, 64)
        )
        painter.fillRect(0, 64, 1, 1136, QColor("black"))
        painter.fillRect(79, 64, 1, 1136, QColor("black"))
        painter.drawImage(
            0, 1197, frame(self.document, 632, outline=True).copy(0, 565, 80, 3)
        )
        painter.end()
        return image


def test_real_repeated_cards_transaction_subdivides_and_restores() -> None:
    """Actual ambiguous registration drives recovery to an exact complete image."""
    target = RepeatedCardsTarget()
    # Demonstrate the normal half-viewport acquisition really is ambiguous.
    probe = VerticalStitch(frame(target.document, 0, outline=True))
    probe.append(frame(target.document, 284, outline=True), 284)
    old = probe._rows(frame(target.document, 284, outline=True))
    new = probe._rows(frame(target.document, 568, outline=True))
    assert [match[0] for match in probe._fixed_candidates(old, new, 64)] == [284, 464]
    with pytest.raises(AmbiguousScrollError):
        probe.append(frame(target.document, 568, outline=True), 284)
    original = target.position()
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    result = operation.run()
    assert result.image == target.expected()
    assert result.restoration == "restored"
    assert target.position() == original
    offsets = [round(percent * 632 / 100) for percent in target.moves]
    assert offsets[:7] == [0, 284, 568, 284, 497, 284, 426]
    assert offsets[-1] == 100
    assert result.frames == operation._frames
    assert result.frames <= 80


def test_real_indefinite_repetition_exhausts_subdivision_without_output() -> None:
    """Reacquisition never fabricates a displacement for an entirely repeated body."""
    target = RepeatedCardsTarget()
    painter = QPainter(target.document)
    tile = patterned().copy(0, 64, 80, 60)
    for y in range(64, 1200, 60):
        painter.drawImage(0, y, tile)
    painter.end()
    original = target.position()
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    result = operation.run()
    assert result.image is None
    assert "ambiguous" in result.message
    assert result.restoration == "restored"
    assert target.position() == original
    assert len(target.moves) == 11  # Top seek, failed step, four subdivisions, restore.
    assert result.frames == 10


def test_append_budget_is_checked_before_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preexisting exhausted budget cannot acquire one additional stable frame."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 1)
    before = target.count
    with pytest.raises(CaptureError, match="page limit"):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert target.count == before
    assert target.moves == [12.5]


def test_adjacent_float_positions_cannot_create_midpoint_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IEEE rounding at adjacent provider values must not produce a zero step."""
    _target, operation, _stitch, _frames, _accepted, anchor = setup()
    accepted = ScrollPosition(27, 1.0, 20)
    rejected = ScrollPosition(27, math.nextafter(1.0, math.inf), 20)
    calls: list[float] = []

    def returned(percent: float) -> ScrollPosition:
        calls.append(percent)
        return accepted

    monkeypatch.setattr(operation, "_move", returned)
    monkeypatch.setattr(operation, "_frame", lambda _position: anchor)
    with pytest.raises(CaptureError, match="no smaller forward step"):
        operation._retry_smaller_step(accepted, rejected, anchor)
    assert calls == [accepted.vertical]


def test_midpoint_result_must_stay_inside_rejected_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reacquisition boundary independently verifies its movement result."""
    _target, operation, _stitch, _frames, accepted, anchor = setup()
    rejected = ScrollPosition(accepted.horizontal, 12.5, accepted.view_size)
    calls: list[float] = []

    def moved(percent: float) -> ScrollPosition:
        calls.append(percent)
        return accepted if len(calls) == 1 else rejected

    monkeypatch.setattr(operation, "_move", moved)
    monkeypatch.setattr(operation, "_frame", lambda _position: anchor)
    with pytest.raises(CaptureError, match="smaller interval"):
        operation._retry_smaller_step(accepted, rejected, anchor)
    assert calls == [0, 6.25]
