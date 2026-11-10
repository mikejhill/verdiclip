"""Fresh generated views resolve some refusals without relaxing a pixel proof."""

from typing import override

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import Target
from tests.capture.test_scrolling_no_match_retry import (
    SideControlsTarget,
    _assert_complete_output,
)

from verdiclip.capture.scrolling import (
    AmbiguousScrollError,
    PostPrefixBodyScrollError,
    ScrollTransaction,
    VerticalStitch,
    _HeaderFrames,
)
from verdiclip.exceptions import CaptureError


class RepeatedRowsTarget(SideControlsTarget):
    """Repeated authored text and a moving track defeat repeated halving."""

    def __init__(self) -> None:
        super().__init__()
        band = self.document.copy(0, 1092, 192, 48)
        painter = QPainter(self.document)
        for row in (1240, 1536, 1832):
            painter.drawImage(0, row, band)
        painter.end()

    @override
    def render(self, offset: int) -> QImage:
        """This page has a plain left margin; only its right track stays fixed."""
        image = super().render(offset)
        painter = QPainter(image)
        painter.fillRect(0, 0, 24, 596, QColor("white"))
        painter.end()
        return image


class RepeatedSidebarTarget(SideControlsTarget):
    """A false local text match survives where the real wide step has no band."""

    def __init__(self) -> None:
        super().__init__()
        band = self.document.copy(0, 797, 192, 48)
        painter = QPainter(self.document)
        painter.drawImage(0, 1081, band)
        painter.end()

    @override
    def render(self, offset: int) -> QImage:
        """Keep each visible sidebar control, including its full painted height."""
        image = super().render(offset)
        painter = QPainter(image)
        painter.fillRect(8, 140, 8, 16, QColor("black"))
        painter.end()
        return image


def test_three_quarter_view_resolves_repetition_that_survives_halving() -> None:
    """Five real ambiguous pairs stay rejected; a different pair proves its shift."""
    target = RepeatedRowsTarget()
    anchor = target.render(988)
    prefix = tuple(VerticalStitch._rows(anchor)[:73])
    stitch = VerticalStitch(anchor, post_prefix=prefix)
    for advance in (304, 152, 76, 38, 19):
        with pytest.raises(AmbiguousScrollError):
            stitch.append(target.render(988 + advance), advance)
        assert stitch.finish() == anchor
    stitch.append(target.render(1216), 228)
    assert stitch.finish().height() == 836
    assert stitch.finish().copy(32, 73, 140, 751) == target.document.copy(
        32, 1061, 140, 751
    )


def test_repeated_rows_complete_transaction_retains_every_observed_column() -> None:
    """The generated page completes with exact body pixels, full rows and restore."""
    target = RepeatedRowsTarget()
    original = target.position()
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is not None, result.message
    assert result.restoration == "restored"
    assert target.position() == original
    _assert_complete_output(target, result.image)


def test_rejected_body_band_preserves_progress_prefix_and_reacquires() -> None:
    """A false20px band never enters output; fresh smaller full-width views can join."""
    target = RepeatedSidebarTarget()
    target.offset = 0
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    operation._expected = target.position()
    first = operation._frame(target.position())
    prefix = tuple(VerticalStitch._rows(target.render(228))[:73])
    assert all(
        tuple(VerticalStitch._rows(target.render(offset))[:73]) == prefix
        for offset in (532, 836)
    )
    stitch = VerticalStitch(first, post_prefix=prefix)
    frames = _HeaderFrames(first, target.area, None)
    previous = 0
    for offset in (228, 342):
        accepted = operation._move(offset / 1674 * 100)
        anchor = operation._frame(accepted)
        stitch.append(anchor, offset - previous)
        previous = offset
    before = stitch.finish()
    following = operation._move(646 / 1674 * 100)
    with pytest.raises(PostPrefixBodyScrollError) as failure:
        stitch.append(target.render(646), 304)
    assert failure.value.advance == 20
    assert failure.value.containment is False
    assert stitch.finish() == before
    smaller, _raw = operation._append_step(stitch, frames, accepted, following, anchor)
    assert round(smaller.vertical * 1674 / 100) == 513
    assert target.moves[-5:] == [646, 342, 570, 342, 513]
    assert stitch._post_prefix == prefix
    image = stitch.finish()
    assert image.height() == 1121
    assert image.copy(0, 0, 192, 139) == first.copy(0, 0, 192, 139)
    assert image.copy(32, 139, 140, 970) == target.document.copy(32, 139, 140, 970)


@pytest.mark.parametrize("limit", ["attempts", "frames", "anchor"])
def test_body_refusal_cannot_publish_or_escape_limits(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Typed refusal preserves acquisition limits and withholds partial output."""
    target = Target()
    original = target.position()
    attempts: list[float | None] = []

    def reject(
        _self: VerticalStitch, _image: QImage, expected: float | None, **_kwargs: object
    ) -> None:
        attempts.append(expected)
        raise PostPrefixBodyScrollError("body refused", advance=1, containment=False)

    monkeypatch.setattr(VerticalStitch, "append", reject)
    if limit == "frames":
        monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 3)
    if limit == "anchor":
        capture = target.frame

        def changed() -> QImage:
            image = capture()
            if target.offset == 0 and target.moves.count(0) > 1:
                image.setPixelColor(0, 0, QColor("red"))
            return image

        monkeypatch.setattr(target, "frame", changed)
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert result.restoration == "restored"
    assert target.position() == original
    if limit == "attempts":
        assert len(attempts) == 5
        assert result.frames == 10
        assert result.message == "body refused"
    elif limit == "frames":
        assert len(attempts) == 1
        assert result.frames == 2
        assert "page limit" in result.message
    else:
        assert len(attempts) == 1
        assert "anchor pixels changed" in result.message
        assert target.moves == [0, 12.5, 0, original.vertical]


def test_contained_body_refusal_requires_native_confirmation_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong native displacement prevents reacquisition for a contained candidate."""
    target = Target(offset=0)
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    accepted = target.position()
    operation._expected = accepted
    anchor = operation._frame(accepted)
    stitch = VerticalStitch(anchor)
    following = operation._move(12.5)

    def reject(_image: QImage, _expected: float | None, **_kwargs: object) -> None:
        raise PostPrefixBodyScrollError("body refused", advance=59, containment=True)

    monkeypatch.setattr(stitch, "append", reject)
    with pytest.raises(CaptureError, match="exact native displacement"):
        operation._append_step(
            stitch,
            _HeaderFrames(anchor, target.area, None),
            accepted,
            following,
            anchor,
        )
    assert target.moves == [12.5]
    assert stitch.finish() == anchor
