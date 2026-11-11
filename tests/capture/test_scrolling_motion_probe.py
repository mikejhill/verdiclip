"""Fresh observations cross a pinned-to-flow transition safely."""

from __future__ import annotations

from collections.abc import Callable
from typing import override

import pytest
from PySide6.QtGui import QImage, QPainter
from tests.capture.test_scrolling import Target, document

from verdiclip.capture.scrolling import (
    AmbiguousScrollError,
    ScrollPosition,
    ScrollTransaction,
    VerticalStitch,
)
from verdiclip.geometry import Rect


class StickyTarget(Target):
    """A finite document whose bottom component becomes ordinary flowing content."""

    area = Rect(0, 0, 80, 608)

    def __init__(self) -> None:
        super().__init__(total=3856, offset=1202)
        self.tile = document(108)
        self.tile.invertPixels()
        self.background = self.document.copy()
        painter = QPainter(self.document)
        painter.drawImage(0, 2164, self.tile)
        painter.end()

    @override
    def position(self) -> ScrollPosition:
        """Report authored geometry independently of the pixel matcher."""
        return ScrollPosition(-1.0, self.offset / 3248 * 100, 608 / 3856 * 100)

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Honor native-style admission and quantize to integral pixels."""
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.offset = round(vertical * 3248 / 100)

    @override
    def frame(self) -> QImage:
        """Paint the component once, pinned until its natural position arrives."""
        image = self.background.copy(0, self.offset, 80, 608)
        if self.offset < 2272:
            painter = QPainter(image)
            painter.drawImage(0, min(466, 2164 - self.offset), self.tile)
            painter.end()
        return image


def test_motion_spacing_probe_produces_exact_complete_image() -> None:
    """Fresh acquisition produces complete pixels and restores the original state."""
    target = StickyTarget()
    original = target.position()
    before = target.frame()

    result = ScrollTransaction(target, lambda: False, pause=lambda _: None).run()

    assert result.image == target.document, (
        "Every output pixel must match the authored document"
    )
    assert result.restoration == "restored", result.message
    assert target.position() == original, "Original position must be restored"
    assert target.frame() == before, "Original pixels must be restored"
    assert result.frames <= 80, "Acquisition must retain the existing frame budget"
    offsets = [round(p * 3248 / 100) for p in target.moves]
    assert offsets[5:9] == [1520, 1824, 1520, 1698], (
        "A rejected pair must return exactly before probing the transition"
    )


@pytest.mark.parametrize("advance", [304, 228, 152, 76, 38])
def test_retained_transition_pattern_remains_ambiguous(advance: int) -> None:
    """Every original retry remains rejected; the hint never grants an alignment."""
    target = StickyTarget()
    target.offset = 1672
    anchor = target.frame()
    stitch = VerticalStitch(anchor)
    target.offset += advance

    with pytest.raises(AmbiguousScrollError) as caught:
        stitch.append(target.frame(), advance)

    assert caught.value.probe_advance == 26, (
        "Rival motion separation should propose a fresh 26px observation"
    )
    assert stitch.finish() == anchor, "Rejected observations must not change output"
