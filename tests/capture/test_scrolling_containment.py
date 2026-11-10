"""All original competing evidence must be explained before native confirmation."""

import math
from collections.abc import Callable
from typing import override
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import Target, document
from tests.capture.test_scrolling_ambiguity_retry import setup

from verdiclip.capture.scrolling import (
    AmbiguousScrollError,
    ScrollPosition,
    ScrollTransaction,
    TopBoundaryScrollError,
    VerticalStitch,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def evidence_rows(
    bands: dict[int, list[tuple[int, int]]], height: int = 240
) -> tuple[list[bytes], list[bytes]]:
    """Generate equality constraints without using registration or provider offsets."""
    parents: list[int] = list(range(height * 2))

    def root(index: int) -> int:
        while parents[index] != index:
            following = parents[index]
            assert isinstance(following, int)
            index = following
        return index

    for advance, spans in bands.items():
        for first, last in spans:
            for row in range(first, last):
                parents[root(row + advance)] = root(height + row)
    values = [root(index).to_bytes(4, "little") for index in range(height * 2)]
    return values[:height], values[height:]


def containment_pair() -> tuple[QImage, QImage]:
    """Repeated interior rows create a genuine local alias without complete overlap."""
    source = document(300)
    painter = QPainter(source)
    painter.drawImage(0, 140, source.copy(0, 80, source.width(), 50))
    painter.end()
    old = source.copy(0, 0, source.width(), 240)
    new = source.copy(0, 20, source.width(), 240)
    new.setPixelColor(1, 210, QColor("magenta"))
    return old, new


def test_containment_explains_all_original_support_before_placing_seam() -> None:
    """The legacy matcher stays ambiguous; independent containment selects twenty."""
    old, new = containment_pair()
    stitch = VerticalStitch(old)
    before, after = stitch._rows(old), stitch._rows(new)
    with pytest.raises(AmbiguousScrollError):
        stitch._alignment(before, after)
    match, contained = stitch._full_width_proof(before, after)
    assert contained
    assert match == (20, 0, 210)
    stitch.append(new, 20)
    result = stitch.finish()
    assert result.height() == 260
    assert result.copy(0, 0, old.width(), 50) == old.copy(0, 0, old.width(), 50)
    assert result.copy(0, 50, old.width(), 210) == new.copy(0, 30, old.width(), 210)


@pytest.mark.parametrize(
    "bands",
    [
        {20: [(0, 90)], 80: [(100, 150)]},
        {100: [(0, 100)], 20: [(20, 60), (160, 200)]},
        {120: [(0, 90)], 20: [(170, 210)]},
        {20: [(0, 140)], 80: [(20, 120)]},
    ],
    ids=["disjoint-motion", "hidden-tail", "rival-loses-domain", "short-exclusive"],
)
def test_containment_rejects_incomparable_evidence(
    bands: dict[int, list[tuple[int, int]]],
) -> None:
    """No local dominance can erase another qualifying original band."""
    old, new = evidence_rows(bands)
    with pytest.raises(AmbiguousScrollError):
        VerticalStitch._containment_alignment(old, new)


def test_equal_periodic_support_remains_ambiguous() -> None:
    """Equal repeated support has no exclusive textured evidence."""
    old = [bytes([row % 64]) for row in range(240)]
    new = [bytes([(row + 20) % 64]) for row in range(240)]
    with pytest.raises(AmbiguousScrollError):
        VerticalStitch._containment_alignment(old, new)


def test_flat_exclusive_rows_are_not_an_independent_witness() -> None:
    """Forty exclusive rows of one color do not provide eight distinct rows."""
    old, new = evidence_rows({20: [(0, 150)], 80: [(40, 140)]})
    for row in range(40):
        old[row + 20] = new[row] = b"flat"
    with pytest.raises(AmbiguousScrollError):
        VerticalStitch._containment_alignment(old, new)


@pytest.mark.parametrize(
    "expected", [None, math.nan, math.inf, -math.inf, 20.01, 21, 19]
)
def test_containment_requires_exact_native_confirmation_without_committing(
    expected: float | None,
) -> None:
    """The old two-percent tolerance cannot authorize this new proof."""
    old, new = containment_pair()
    stitch = VerticalStitch(old)
    verify = Mock()
    with pytest.raises(CaptureError, match="exact native"):
        stitch.append(new, expected, verify=verify)
    assert stitch.finish() == old
    assert stitch._previous == old
    assert stitch._fixed_prefix is None
    verify.assert_not_called()
    stitch.append(new, 20)


def test_native_float_roundoff_is_allowed_after_independent_selection() -> None:
    """Only sub-micropixel arithmetic noise is accepted, not approximate movement."""
    old, new = containment_pair()
    VerticalStitch(old).append(new, 20 + 1e-8)


def test_legacy_entire_overlap_wins_without_containment() -> None:
    """Existing whole-overlap resolution and its native tolerance stay unchanged."""
    source = document(300)
    old, new = (
        source.copy(0, 0, source.width(), 240),
        source.copy(0, 20, source.width(), 240),
    )
    stitch = VerticalStitch(old)
    assert stitch._full_width_proof(stitch._rows(old), stitch._rows(new))[1] is False
    stitch.append(new, 20.1)


def test_top_refusal_carries_provenance_before_any_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No broad fixed-proof fallback can bypass native confirmation of this error."""
    old, new = containment_pair()
    stitch = VerticalStitch(old)

    def refuse(
        _old: list[bytes],
        _new: list[bytes],
        advance: int,
        *,
        full_width: bool,
        containment: bool = False,
    ) -> None:
        raise TopBoundaryScrollError(
            "top refusal",
            advance=advance,
            full_width=full_width,
            containment=containment,
        )

    monkeypatch.setattr(stitch, "_check_top", refuse)
    fixed = Mock(return_value=(20, 0, 60, (1, 1)))
    monkeypatch.setattr(stitch, "_fixed_alignment", fixed)
    with pytest.raises(CaptureError, match="exact native"):
        stitch.append(new, 20.1)
    with pytest.raises(TopBoundaryScrollError) as caught:
        stitch.append(new, 20)
    assert caught.value.containment
    fixed.assert_not_called()
    assert caught.value.observation is None
    assert stitch.finish() == old


def test_failed_late_verification_keeps_all_stitch_state() -> None:
    """Deadlines or cancellation at final verification leave no tentative proof."""
    old, new = containment_pair()
    stitch = VerticalStitch(old)

    def expired(_advance: int) -> None:
        raise CaptureError("deadline")

    with pytest.raises(CaptureError, match="deadline"):
        stitch.append(new, 20, verify=expired)
    assert stitch.finish() == old
    assert stitch._tail_start == 0
    assert stitch._fixed_prefix is None


class ContainmentTarget(Target):
    """Finite native-style target with one repeated band and changing bottom pixel."""

    area = Rect(10, 20, 80, 240)

    def __init__(self, *, extent: float = 260) -> None:
        super().__init__(total=260, offset=0)
        self.extent = extent
        painter = QPainter(self.document)
        painter.drawImage(0, 140, self.document.copy(0, 80, 80, 50))
        painter.end()

    @override
    def position(self) -> ScrollPosition:
        return ScrollPosition(
            self.horizontal, self.offset * 5.0, 240 / self.extent * 100
        )

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.offset = round(vertical / 5)

    @override
    def frame(self) -> QImage:
        image = self.document.copy(0, self.offset, 80, 240)
        if self.offset:
            image.setPixelColor(1, 230 - self.offset, QColor("magenta"))
        return image


def test_real_containment_transaction_outputs_exact_image_and_restores() -> None:
    """No mocked matcher: repeated local aliases are resolved by complete support."""
    target = ContainmentTarget()
    original = target.frame()
    result = ScrollTransaction(target, lambda: False, pause=lambda _: None).run()
    expected = target.document.copy()
    expected.setPixelColor(1, 230, QColor("magenta"))
    assert result.image == expected
    assert result.restoration == "restored"
    assert target.moves == [100, 0]
    assert target.frame() == original


def test_native_disagreement_returns_no_image_and_restores() -> None:
    """A native error within the old tolerance still rejects the new proof."""
    target = ContainmentTarget(extent=260.2)
    original = target.frame()
    result = ScrollTransaction(target, lambda: False, pause=lambda _: None).run()
    assert result.image is None
    assert "exact native" in result.message
    assert result.restoration == "restored"
    assert target.frame() == original
    assert target.moves == [100, 0]


def test_containment_provenance_checked_before_top_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A top error cannot attach a learnable observation before exact confirmation."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    error = TopBoundaryScrollError("top", advance=61, full_width=True, containment=True)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(stitch, "append", refuse)
    with pytest.raises(CaptureError, match="exact native"):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert error.observation is None
    assert target.moves == [12.5]
    assert stitch.finish() == anchor


def test_original_band_seam_respects_tail_without_changing_winner() -> None:
    """Original winner bands supply the seam after exclusive evidence selects it."""
    old, new = evidence_rows({20: [(0, 200)], 80: [(60, 110)]})
    assert VerticalStitch._containment_alignment(old, new, tail_start=120) == (
        20,
        0,
        200,
    )
    with pytest.raises(CaptureError, match="interior join"):
        VerticalStitch._containment_alignment(old, new, tail_start=220)


def test_empty_or_unique_support_is_not_a_containment_proof() -> None:
    """This fallback only classifies actual competing candidate evidence."""
    old, new = evidence_rows({20: [(0, 100)]})
    with pytest.raises(AmbiguousScrollError, match="no containment"):
        VerticalStitch._containment_alignment(old, new)


def test_specialized_full_width_route_cannot_use_containment() -> None:
    """A native route without a narrow rail still excludes the generic fallback."""
    old, new = containment_pair()
    stitch = VerticalStitch(old, allow_containment=False)
    with pytest.raises(AmbiguousScrollError):
        stitch.append(new, 20)
    assert stitch.finish() == old


def test_transaction_deadline_after_analysis_returns_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Analysis cannot publish after the immutable deadline or issue another write."""
    target = ContainmentTarget()
    now = [0.0]
    operation = ScrollTransaction(
        target, lambda: False, pause=lambda _: None, clock=lambda: now[0]
    )
    analysis = VerticalStitch._containment_alignment

    def expired(
        old: list[bytes], new: list[bytes], *, tail_start: int = 0
    ) -> tuple[int, int, int]:
        result = analysis(old, new, tail_start=tail_start)
        now[0] = operation._deadline
        return result

    monkeypatch.setattr(VerticalStitch, "_containment_alignment", staticmethod(expired))
    result = operation.run()
    assert result.image is None
    assert "time limit" in result.message
    assert target.moves == [100]
    assert "time limit" in result.restoration


def test_late_containment_top_refusal_cannot_seed_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact native confirmation does not turn a later join into an initial probe."""
    target, operation, stitch, frames, accepted, anchor = setup()
    following = operation._move(12.5)
    accepted, anchor = operation._append_step(
        stitch, frames, accepted, following, anchor
    )
    prior = stitch.finish()
    following = operation._move(25)
    error = TopBoundaryScrollError(
        "late top", advance=60, full_width=True, containment=True
    )

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(stitch, "append", refuse)
    with pytest.raises(TopBoundaryScrollError):
        operation._append_step(stitch, frames, accepted, following, anchor)
    assert error.observation is None
    assert stitch.finish() == prior
    assert target.moves == [12.5, 25]


def test_exclusive_evidence_above_tail_allows_original_band_join() -> None:
    """Displacement proof may be above the retained tail while a later seam is exact."""
    old, new = evidence_rows(
        {76: [(179, 224), (322, 520)], 224: [(322, 355)]}, height=608
    )
    with pytest.raises(AmbiguousScrollError):
        VerticalStitch._alignment(old, new)
    advance, first, last = VerticalStitch._containment_alignment(
        old, new, tail_start=412
    )
    assert (advance, first, last) == (76, 322, 520)
    assert old[advance + first : advance + last] == new[first:last]
    with pytest.raises(CaptureError, match="interior join"):
        VerticalStitch._containment_alignment(old, new, tail_start=596)
