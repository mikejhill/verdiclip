"""Tolerant stitching of synthetic scrolling frames."""

from __future__ import annotations

import itertools
import random

import pytest
from PySide6.QtGui import QColor, QImage

from verdiclip.capture.stitch import Frame, find_offset, stitch, view_moved

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
        scrollbar(image, header + top * body // max(1, document.height()), footer)
    return image


def scrollbar(image: QImage, position: int, footer: int = 0) -> None:
    """Paint a plain track down the right edge with a thumb at ``position``."""
    for y in range(VIEW - footer):
        for x in range(WIDTH - 6, WIDTH):
            inside = position <= y < position + 20
            image.setPixel(x, y, 0x777777 if inside else 0xEEEEEE)


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


def test_scrollbar_strip_is_left_out() -> None:
    """A thumb that moves between frames does not appear in several places."""
    document = page(600)
    result = stitch(frames(document, 120, thumb=True))
    whole = expected(document)
    assert result.image == whole.copy(0, 0, WIDTH - 6, whole.height())


def test_changed_content_does_not_widen_the_strip() -> None:
    """Rows whose content changed between frames say nothing about the strip."""
    document = page(600)
    shots = frames(document, 120, thumb=True)
    image = shots[1].image
    for y in range(140, 160):  # Away from its thumb
        for x in range(WIDTH - 10, WIDTH - 6):
            image.setPixel(x, y, 0x101010 + x)
    for y in range(160, 180):
        for x in range(WIDTH - 6):
            image.setPixel(x, y, 0x101010 + x)
    result = stitch(shots)
    assert result.image.width() == WIDTH - 6


def test_stale_first_frame_is_dropped() -> None:
    """A first frame still showing the end of the page is not put above the top."""
    document = page(700)
    shots = frames(document, 150)
    stale = shots[-1].image.copy()
    for y in range(VIEW - 12, VIEW - 4):  # A spinner moved meanwhile
        for x in range(WIDTH):
            stale.setPixel(x, y, 0x0F0F0F)
    result = stitch([Frame(stale), Frame(stale), Frame(shots[0].image), *shots[1:]])
    assert result.estimated_seams == 0
    assert result.stale_frames == 2
    assert result.image == expected(document)


def test_blank_frames_are_not_taken_for_the_same_view() -> None:
    """Mostly blank views that join nothing are all kept."""
    blank = QImage(WIDTH, VIEW, QImage.Format.Format_RGB32)
    blank.fill(QColor("white"))
    result = stitch([Frame(blank), Frame(page(VIEW)), Frame(blank.copy())])
    assert result.image.height() == 3 * VIEW


def test_narrow_frames_have_no_strip() -> None:
    """Frames too narrow for a scrollbar keep their width."""
    document = page(400).scaled(8, 400)
    shots = [Frame(document.copy(0, top, 8, VIEW)) for top in (0, 100, 200)]
    shots = [shots[0], *(Frame(f.image, 100.0) for f in shots[1:])]
    assert stitch(shots).image.width() == 8


def test_views_of_another_size_count_as_moved() -> None:
    """A resized view is never mistaken for a still one."""
    assert view_moved(page(VIEW), page(VIEW + 1), 10)


def test_sparse_view_that_shifts_counts_as_moved() -> None:
    """Two lines of text on a blank view move when they shift, few rows or not."""
    blank = QImage(WIDTH, VIEW, QImage.Format.Format_RGB32)
    blank.fill(QColor("white"))
    before, after = blank.copy(), blank.copy()
    for y in range(3):
        for x in range(WIDTH):
            before.setPixel(x, 50 + y, x)
            after.setPixel(x, 40 + y, x)
    assert view_moved(before, after, 10)
    assert not view_moved(before, before.copy(), 10)


def test_changing_right_edge_without_a_scrollbar_is_kept() -> None:
    """A timestamp changing near the edge of a short capture is not cropped."""
    document = page(260)
    first = view(document, 0)
    second = view(document, 60)
    for y in range(100, 110):  # Rows that overlap the first frame
        for x in range(WIDTH - 10, WIDTH - 6):
            second.setPixel(x, y, 0x101010 + x)
    result = stitch([Frame(first), Frame(second, 60.0)])
    assert result.image.width() == WIDTH


def test_scattered_marks_on_a_track_are_not_a_thumb() -> None:
    """Text-like runs on a plain column are content, not a thumb."""
    document = page(400)
    shots = frames(document, 100, thumb=True)
    for frame in shots:
        image = frame.image
        for y in (150, 170):
            for x in range(WIDTH - 6, WIDTH):
                image.setPixel(x, y, 0x123456)
    assert stitch(shots).image.width() == WIDTH


def test_thumb_that_moves_up_is_not_a_scrollbar() -> None:
    """A run moving against the scroll direction is not a thumb."""
    document = page(400)
    shots = frames(document, 100, thumb=True)
    for index, frame in enumerate(shots):
        scrollbar(frame.image, VIEW - 30 - 40 * index)
    assert stitch(shots).image.width() == WIDTH


def test_dropped_stale_frame_does_not_keep_the_scrollbar() -> None:
    """A stale first frame showing the thumb low does not hide the scrollbar."""
    document = page(700)
    shots = frames(document, 150, thumb=True)
    result = stitch([Frame(shots[-1].image.copy()), Frame(shots[0].image), *shots[1:]])
    whole = expected(document)
    assert result.stale_frames == 1
    assert result.image == whole.copy(0, 0, WIDTH - 6, whole.height())


def test_thumb_that_fades_out_in_some_frames_is_still_cropped() -> None:
    """An overlay scrollbar hidden in one frame still counts as a scrollbar."""
    document = page(700)
    shots = frames(document, 150, thumb=True)
    scrollbar(shots[2].image, VIEW)  # Track only: the thumb faded out
    whole = expected(document)
    assert stitch(shots).image == whole.copy(0, 0, WIDTH - 6, whole.height())
