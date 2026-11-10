"""Generated pixel acquisition regressions, not live Google qualification."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from verdiclip.capture.scrolling import (
    NoAlignmentScrollError,
    ScrollPosition,
    ScrollTransaction,
    TopBoundaryScrollError,
    VerticalStitch,
    _HeaderFrames,
)
from verdiclip.geometry import Rect

_WIDTH = 192
_VIEW = 608
_TOTAL = 2282
_LEFT = 24
_RIGHT = 8


class SideControlsTarget:
    """A deterministic textured page, fixed controls and moving scrollbar paint."""

    area = Rect(0, 0, _WIDTH, _VIEW)
    header = None
    registration_dpi = 96

    def __init__(self, *, collapsed: int = 73, persistent: bool = False) -> None:
        self.collapsed = collapsed
        self.persistent = persistent
        self.offset = 619
        self.moves: list[int] = []
        self.samples: list[tuple[int, QImage]] = []
        self.mutate_anchor = False
        self.document = QImage(_WIDTH, _TOTAL, QImage.Format.Format_RGB32)
        self.document.fill(QColor("white"))
        painter = QPainter(self.document)
        for row in range(_TOTAL):
            painter.fillRect(32, row, 140, 1, QColor(row % 251, row // 251, 125))
        painter.end()

    def check(self) -> None:
        """The generated viewport and input ownership stay unchanged."""

    def position(self) -> ScrollPosition:
        """Report exact integer-pixel offsets using ordinary UIA percentages."""
        return ScrollPosition(
            -1, self.offset / (_TOTAL - _VIEW) * 100, _VIEW / _TOTAL * 100
        )

    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Honor production admission and record every acquired offset."""
        if before_write is not None:
            before_write()
        self.offset = round(vertical / 100 * (_TOTAL - _VIEW))
        self.moves.append(self.offset)

    def render(self, offset: int) -> QImage:
        """Paint fixed side buttons and a thumb independent of registration."""
        image = self.document.copy(0, offset, _WIDTH, _VIEW)
        painter = QPainter(image)
        painter.fillRect(0, 0, _LEFT, _VIEW, QColor("white"))
        for top, height in ((37, 30), (104, 8), (144, 8), (563, 18)):
            painter.fillRect(8, top, 8, height, QColor("black"))
        painter.fillRect(_WIDTH - _RIGHT, 0, _RIGHT, _VIEW, QColor("white"))
        painter.fillRect(_WIDTH - 6, 18 + offset // 4, 4, 152, QColor("gray"))
        painter.fillRect(
            _LEFT,
            0,
            _WIDTH - _LEFT - _RIGHT,
            139 if offset == 0 else self.collapsed,
            QColor("blue" if offset == 0 else "green"),
        )
        # A real viewport boundary, not an invented match exclusion.
        painter.fillRect(0, _VIEW - 12, _WIDTH, 12, QColor("black"))
        if self.persistent and offset:
            painter.fillRect(30, 0, 1, _VIEW, QColor("red"))
        if self.mutate_anchor and offset == 0 and self.moves.count(0) > 1:
            painter.fillRect(35, 200, 1, 1, QColor("red"))
        painter.end()
        return image

    def frame(self) -> QImage:
        """Record raw full-width samples without access to stitcher decisions."""
        image = self.render(self.offset)
        self.samples.append((self.offset, image.copy()))
        return image


def test_generated_large_pair_has_no_match_but_half_step_is_unique() -> None:
    """A 304px step's full-width bands are too short; 152px has a unique band."""
    target = SideControlsTarget()
    first = target.render(0)
    with pytest.raises(NoAlignmentScrollError):
        VerticalStitch(first).append(target.render(304), 304)
    with pytest.raises(TopBoundaryScrollError) as failure:
        VerticalStitch(first).append(target.render(152), 152)
    assert failure.value.full_width is True
    assert failure.value.advance == 152


def _assert_complete_output(target: SideControlsTarget, image: QImage) -> None:
    """Check every authored body pixel and full-width native-row provenance.

    There is no chosen product seam in this oracle. Any legal seam must keep
    each source coordinate once, preserve the original top/final bottom, and
    use an actual complete observed row, including both controls and scrollbar.
    """
    assert image.size() == target.document.size()
    assert image.copy(32, 139, 140, _TOTAL - 139 - 12) == target.document.copy(
        32, 139, 140, _TOTAL - 139 - 12
    )
    assert image.copy(0, 0, _WIDTH, 139) == target.render(0).copy(0, 0, _WIDTH, 139)
    assert image.copy(0, _TOTAL - 12, _WIDTH, 12) == target.render(_TOTAL - _VIEW).copy(
        0, _VIEW - 12, _WIDTH, 12
    )
    observed: dict[int, set[bytes]] = {}
    for offset, sample in target.samples:
        raw = bytes(sample.constBits())
        for row in range(_VIEW):
            if offset and row < target.collapsed:
                continue
            if offset != _TOTAL - _VIEW and row >= _VIEW - 12:
                continue
            observed.setdefault(offset + row, set()).add(
                raw[row * _WIDTH * 4 : (row + 1) * _WIDTH * 4]
            )
    raw = bytes(image.constBits())
    for row in range(_TOTAL):
        assert raw[row * _WIDTH * 4 : (row + 1) * _WIDTH * 4] in observed[row]


def test_three_quarter_probe_preserves_measured_long_prefix() -> None:
    """73px modeled header survives228px lookahead, unlike the152px thumb onset."""
    target = SideControlsTarget(collapsed=73)
    target.offset = 0
    operation = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)
    top = target.position()
    operation._expected = top
    original = operation._frame(top)
    following = operation._move(228 / (_TOTAL - _VIEW) * 100)
    with pytest.raises(TopBoundaryScrollError) as failure:
        operation._append_step(
            VerticalStitch(original),
            _HeaderFrames(original, target.area, None),
            top,
            following,
            original,
        )
    prefix, returned, restored = operation._learn_post_prefix(
        top, original, failure.value
    )
    assert len(prefix) == 73
    assert returned == top
    assert restored == original


def test_generated_end_to_end_three_quarter_backoff_restores() -> None:
    """Smaller acquisitions retain full-width pixels and restore the real anchor."""
    target = SideControlsTarget()
    original = target.position()
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is not None, (result.message, target.moves)
    assert result.restoration == "restored"
    assert target.position() == original
    assert target.moves[:4] == [0, 304, 0, 228]
    _assert_complete_output(target, result.image)


@pytest.mark.parametrize("failure", ["persistent", "anchor"])
def test_true_no_match_or_changed_anchor_never_publishes(failure: str) -> None:
    """Another acquisition cannot conceal irreducible content or changed anchor."""
    target = SideControlsTarget(persistent=failure == "persistent")
    target.mutate_anchor = failure == "anchor"
    original = target.position()
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is None
    assert result.restoration == "restored"
    assert target.position() == original
    if failure == "anchor":
        assert "anchor pixels changed" in result.message
        assert target.moves == [0, 304, 0, 619]
    else:
        assert "cannot be joined reliably" in result.message
        assert len(target.moves) <= 11


@pytest.mark.parametrize("location", [(80, 900), (0, 900), (0, 0)])
def test_independent_full_row_oracle_rejects_pixel_corruption(
    location: tuple[int, int],
) -> None:
    """Body, side-column and original-boundary pixels all remain accountable."""
    target = SideControlsTarget()
    result = ScrollTransaction(target, lambda: False, pause=lambda _seconds: None).run()
    assert result.image is not None
    altered = result.image.copy()
    altered.setPixelColor(*location, QColor("red"))
    with pytest.raises(AssertionError):
        _assert_complete_output(target, altered)
