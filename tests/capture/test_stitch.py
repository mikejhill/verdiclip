"""Tolerant stitching of synthetic scrolling frames."""

from __future__ import annotations

import itertools
import random

import pytest
from PySide6.QtGui import QColor, QImage

from verdiclip.capture.stitch import Frame, find_offset, stitch

WIDTH = 120
VIEW = 200


def page(height: int, seed: int = 1) -> QImage:
    """A document whose every row is distinct, like text lines."""
    rng = random.Random(seed)  # noqa: S311 - test data
    image = QImage(WIDTH, height, QImage.Format.Format_RGB32)
    for y in range(height):
        base = rng.randrange(1 << 24)
        for x in range(WIDTH):
            image.setPixel(x, y, (base + x * 2654435761) & 0xFFFFFF)
    return image


def view(
    document: QImage,
    top: int,
    *,
    header: int = 0,
    footer: int = 0,
    thumb: bool = False,
) -> QImage:
    """The viewport scrolled to ``top``, with fixed bands and a moving thumb."""
    image = QImage(WIDTH, VIEW, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    body = VIEW - header - footer
    for y in range(body):
        for x in range(WIDTH):
            image.setPixel(x, header + y, document.pixel(x, top + y))
    for y in range(header):
        for x in range(WIDTH):
            image.setPixel(x, y, 0x203040 + y * 7 + x)
    for y in range(footer):
        for x in range(WIDTH):
            image.setPixel(x, VIEW - footer + y, 0x405060 + y * 5 + x)
    if thumb:
        position = header + top * body // max(1, document.height())
        for y in range(position, min(VIEW - footer, position + 20)):
            for x in range(WIDTH - 6, WIDTH):
                image.setPixel(x, y, 0x777777)
    return image


def expected(document: QImage, *, header: int = 0, footer: int = 0) -> QImage:
    """Expected."""
    first = view(document, 0, header=header, footer=footer)
    body = VIEW - header - footer
    total = header + document.height() + footer
    image = QImage(WIDTH, total, QImage.Format.Format_RGB32)
    for y in range(header):
        for x in range(WIDTH):
            image.setPixel(x, y, first.pixel(x, y))
    for y in range(document.height()):
        for x in range(WIDTH):
            image.setPixel(x, header + y, document.pixel(x, y))
    for y in range(footer):
        for x in range(WIDTH):
            image.setPixel(x, total - footer + y, first.pixel(x, header + body + y))
    return image


def frames(
    document: QImage, step: int, *, header: int = 0, footer: int = 0, **kw: bool
) -> list[Frame]:
    """Frames."""
    body = VIEW - header - footer
    last = document.height() - body
    tops = [*range(0, last, step), last]
    result = [Frame(view(document, 0, header=header, footer=footer, **kw))]
    for before, top in itertools.pairwise(tops):
        image = view(document, top, header=header, footer=footer, **kw)
        result.append(Frame(image, float(top - before)))
    return result


def test_plain_document_is_reassembled_exactly() -> None:
    """Plain document is reassembled exactly."""
    document = page(700)
    result = stitch(frames(document, 150))
    assert result.estimated_seams == 0
    assert result.image == expected(document)


def test_fixed_header_and_footer_appear_once() -> None:
    """Fixed header and footer appear once."""
    document = page(600)
    result = stitch(frames(document, 90, header=24, footer=16))
    assert result.estimated_seams == 0
    assert result.image == expected(document, header=24, footer=16)


def test_moving_scrollbar_thumb_does_not_break_alignment() -> None:
    """Moving scrollbar thumb does not break alignment."""
    document = page(600)
    result = stitch(frames(document, 120, thumb=True))
    assert result.estimated_seams == 0
    assert result.image.height() == document.height()


def test_wrong_provider_distance_is_overridden_by_pixels() -> None:
    """Wrong provider distance is overridden by pixels."""
    document = page(500)
    shots = frames(document, 100)
    shots = [Frame(f.image, None if f.moved is None else f.moved + 13) for f in shots]
    result = stitch(shots)
    assert result.estimated_seams == 0
    assert result.image == expected(document)


def test_unmatchable_frames_fall_back_to_provider_distance() -> None:
    """Unmatchable frames fall back to provider distance."""
    first, second = page(VIEW, seed=1), page(VIEW, seed=2)
    result = stitch([Frame(first), Frame(second, 50.0)])
    assert result.estimated_seams == 1
    assert result.image.height() == VIEW + 50


def test_unmatchable_frames_without_distance_are_appended_whole() -> None:
    """Unmatchable frames without distance are appended whole."""
    result = stitch([Frame(page(VIEW, seed=1)), Frame(page(VIEW, seed=2))])
    assert result.estimated_seams == 1
    assert result.image.height() == 2 * VIEW


def test_single_frame_is_returned_unchanged() -> None:
    """Single frame is returned unchanged."""
    image = page(VIEW)
    assert stitch([Frame(image)]).image == image


def test_mismatched_sizes_and_empty_input_are_rejected() -> None:
    """Mismatched sizes and empty input are rejected."""
    with pytest.raises(ValueError, match="Nothing"):
        stitch([])
    with pytest.raises(ValueError, match="size"):
        stitch([Frame(page(VIEW)), Frame(page(VIEW + 1))])


def test_ties_prefer_the_provider_distance() -> None:
    """Ties prefer the provider distance."""
    keys = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    assert find_offset(keys, keys, 10.0) == 10
    assert find_offset(keys, keys, 5.0) == 5


def test_blank_rows_do_not_vote() -> None:
    """Blank rows do not vote."""
    assert find_offset([7] * 40, [7] * 40, 5.0) is None


def test_sparse_coincidences_do_not_override_the_provider() -> None:
    """Four matching rows among many disagreeing ones are not trusted."""
    previous = list(range(200))
    following = [150, 151, 152, 153, *range(1000, 1196)]
    assert find_offset(previous, following, 50.0) is None


def test_a_few_coincidences_do_not_beat_a_supported_offset() -> None:
    """Four rows matching far away lose to 146 rows matching the provider."""
    previous = list(range(200))
    following = list(range(50, 250))
    following[:4] = range(196, 200)
    assert find_offset(previous, following, 50.0) == 50
