"""Pure RGB seam and safety contracts, without native capture or Qt."""

from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from verdiclip.capture import scroll_stitch
from verdiclip.capture.scroll_stitch import (
    ScrollFrame,
    ScrollStitcher,
    StitchError,
    StitchStep,
)


def frame(start: int, height: int = 96, width: int = 2) -> ScrollFrame:
    """Encode each observed row distinctly across its entire width."""
    return ScrollFrame(
        width,
        height,
        b"".join(i.to_bytes(3, "big") * width for i in range(start, start + height)),
    )


@pytest.mark.parametrize(
    ("width", "height", "rgb", "message"),
    [
        (True, 1, b"abc", "integers"),
        (1, 1.0, b"abc", "integers"),
        (0, 1, b"", "positive"),
        (1, -1, b"", "positive"),
        (16_000_001, 1, b"", "pixel limit"),
        (1, 32_769, b"", "row limit"),
        (1, 1, bytearray(b"abc"), "immutable bytes"),
        (1, 1, memoryview(b"abc"), "immutable bytes"),
        (1, 1, b"ab", "length"),
        (1, 1, b"abcd", "length"),
    ],
)
def test_invalid_frame(
    width: object, height: object, rgb: object, message: str
) -> None:
    """Reject bad boundary values rather than coercing or copying them."""
    with pytest.raises(StitchError, match=message):
        ScrollFrame(cast("int", width), cast("int", height), cast("bytes", rgb))


def test_clipped_final_step_and_immutable_output() -> None:
    """Every source row appears once, including a non-page-sized final step."""
    stitcher = ScrollStitcher()
    first = frame(0)
    step = stitcher.append(first)
    assert step == StitchStep(0, 96, 96, 1)
    step = stitcher.append(frame(48))
    assert step == StitchStep(48, 48, 144, 2)
    step = stitcher.append(frame(55))
    assert step == StitchStep(89, 7, 151, 3)
    output = stitcher.finish()
    assert (output.width, output.height, output.frame_count) == (2, 151, 3)
    assert output.rgb == frame(0, 151).rgb
    for value, name in [(first, "width"), (step, "height"), (output, "height")]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, 1)


def test_empty() -> None:
    """An empty session cannot materialize an image."""
    with pytest.raises(StitchError, match="No frames"):
        ScrollStitcher().finish()


@pytest.mark.parametrize("changed", [frame(0, 95), frame(0, width=3)])
def test_geometry_change(changed: ScrollFrame) -> None:
    """Both viewport dimensions stay fixed."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0))
    with pytest.raises(StitchError, match="dimensions changed"):
        stitcher.append(changed)
    assert stitcher.finish().rgb == frame(0).rgb


@pytest.mark.parametrize("start", [0, 73, 96, 150])
def test_no_progress_or_insufficient_overlap(start: int) -> None:
    """Reject unchanged frames, short overlaps, zero overlap and gaps."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0))
    message = "Unchanged" if start == 0 else "No sufficient"
    with pytest.raises(StitchError, match=message):
        stitcher.append(frame(start))
    step = stitcher.append(frame(70))
    assert step == StitchStep(26, 70, 166, 2)


@pytest.mark.parametrize(
    ("height", "shift", "accepted"),
    [(97, 70, True), (97, 71, False), (40, 14, True), (40, 15, False)],
)
def test_overlap_minimum(height: int, shift: int, *, accepted: bool) -> None:
    """Round the quarter viewport upward and require at least 24 scanlines."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0, height))
    if accepted:
        step = stitcher.append(frame(shift, height))
        assert step.new_rows == shift
    else:
        with pytest.raises(StitchError, match="No sufficient"):
            stitcher.append(frame(shift, height))


def test_periodic_ambiguity() -> None:
    """Distinct viewports can still admit multiple plausible offsets."""
    stitcher = ScrollStitcher()
    rows = [bytes([i % 3]) * 3 for i in range(97)]
    stitcher.append(ScrollFrame(1, 96, b"".join(rows[:96])))
    with pytest.raises(StitchError, match="Ambiguous"):
        stitcher.append(ScrollFrame(1, 96, b"".join(rows[1:])))


def test_full_width_exact_matching() -> None:
    """A one-channel change at the far edge invalidates the only possible seam."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0))
    moved = frame(48)
    rgb = bytearray(moved.rgb)
    rgb[11] ^= 1
    with pytest.raises(StitchError, match="No sufficient"):
        stitcher.append(ScrollFrame(moved.width, moved.height, bytes(rgb)))


@pytest.mark.parametrize(
    ("limit", "maximum", "message"),
    [
        ("MAX_FRAMES", 2, "frame limit"),
        ("MAX_OUTPUT_ROWS", 144, "row limit"),
        ("MAX_OUTPUT_PIXELS", 288, "pixel limit"),
    ],
)
def test_limits_are_atomic(
    monkeypatch: pytest.MonkeyPatch, limit: str, maximum: int, message: str
) -> None:
    """Accept the exact bound and reject exceeding it without partial output."""
    monkeypatch.setattr(scroll_stitch, limit, maximum)
    stitcher = ScrollStitcher()
    stitcher.append(frame(0))
    stitcher.append(frame(48))
    with pytest.raises(StitchError, match=message):
        stitcher.append(frame(49))
    assert stitcher.finish().rgb == frame(0, 144).rgb
    assert stitcher.finish().frame_count == 2


def test_repeated_prefix_fallback_with_unique_seam() -> None:
    """KMP backs up through repeated row prefixes without rejecting a unique seam."""
    rows = [b"abc", b"abc", b"def"] * 8 + [b"ghi"] * 24
    first = ScrollFrame(1, 96, frame(100, 48, 1).rgb + b"".join(rows))
    second = ScrollFrame(1, 96, b"".join(rows) + frame(200, 48, 1).rgb)
    stitcher = ScrollStitcher()
    stitcher.append(first)
    step = stitcher.append(second)
    assert step.overlap_rows == 48


def test_partial_prefix_is_not_a_seam() -> None:
    """A matching prefix followed by a different row must fall back safely."""
    stitcher = ScrollStitcher()
    first = frame(0)
    second = ScrollFrame(2, 96, first.rgb[:12] + frame(200, 94).rgb)
    stitcher.append(first)
    with pytest.raises(StitchError, match="No sufficient"):
        stitcher.append(second)


@given(
    st.lists(st.integers(0, 255), min_size=192, max_size=192),
    st.integers(0, 96),
)
def test_matches_exhaustive_overlap_oracle(values: list[int], shift: int) -> None:
    """Linear matching agrees with exhaustive exact suffix/prefix comparison."""
    first = b"".join(bytes([value]) * 3 for value in values[:96])
    second = b"".join(bytes([value]) * 3 for value in values[shift : shift + 96])
    candidates = [
        overlap
        for overlap in range(26, 96)
        if first[(-overlap + 1) * 3 : -3] == second[3 : (overlap - 1) * 3]
    ]
    stitcher = ScrollStitcher()
    stitcher.append(ScrollFrame(1, 96, first))
    if first[3:-3] == second[3:-3] or len(candidates) != 1:
        with pytest.raises(StitchError):
            stitcher.append(ScrollFrame(1, 96, second))
    else:
        overlap = candidates[0]
        step = stitcher.append(ScrollFrame(1, 96, second))
        assert step.overlap_rows == overlap
        seam = overlap // 2
        assert stitcher.finish().rgb == (
            first[: (96 - overlap + seam) * 3] + second[seam * 3 :]
        )


def changed_rows(source: ScrollFrame, indexes: list[int]) -> ScrollFrame:
    """Change all channels of chosen scanlines while preserving geometry."""
    data = bytearray(source.rgb)
    stride = source.width * 3
    for index in indexes:
        data[index * stride : (index + 1) * stride] = b"\xff" * stride
    return ScrollFrame(source.width, source.height, bytes(data))


@pytest.mark.parametrize("shift", [1, 7, 48, 70])
def test_boundary_pixels_are_excluded_from_seam(shift: int) -> None:
    """Both clipped viewport edges disappear into the exact interior splice."""
    stitcher = ScrollStitcher()
    first = changed_rows(frame(0), [95])
    second = changed_rows(frame(shift), [0])
    stitcher.append(first)
    result = stitcher.append(second)
    assert result.new_rows == shift
    assert result.height == 96 + shift
    assert stitcher.finish().rgb == frame(0, 96 + shift).rgb


@pytest.mark.parametrize(
    ("old_rows", "new_rows"), [([94, 95], [0]), ([95], [0, 1]), ([95], [0, 24])]
)
def test_two_row_or_interior_change_is_rejected(
    old_rows: list[int], new_rows: list[int]
) -> None:
    """The boundary allowance never extends to a second or interior scanline."""
    stitcher = ScrollStitcher()
    first = changed_rows(frame(0), old_rows)
    stitcher.append(first)
    with pytest.raises(StitchError, match="No sufficient"):
        stitcher.append(changed_rows(frame(48), new_rows))
    assert stitcher.finish().rgb == first.rgb
    assert stitcher.finish().frame_count == 1


def test_boundary_only_changes_are_not_progress() -> None:
    """Animating only ignored edges cannot pretend the content moved."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0))
    with pytest.raises(StitchError, match="Unchanged interior"):
        stitcher.append(changed_rows(frame(0), [0, 95]))


@pytest.mark.parametrize("height", [1, 2, 24, 26])
def test_too_short_for_forward_overlap(height: int) -> None:
    """A viewport must allow 24 interior rows plus two edges and progress."""
    stitcher = ScrollStitcher()
    stitcher.append(frame(0, height))
    with pytest.raises(StitchError, match="No sufficient"):
        stitcher.append(frame(1, height))


def test_midpoint_provenance_across_clipped_steps() -> None:
    """Keep real source bytes at external edges and replace only overlapped tails."""
    stitcher = ScrollStitcher()
    expected = b""
    previous_start = 0
    for index, start in enumerate([0, 1, 71, 78, 79]):
        current = changed_rows(frame(start), [0, 95])
        stitcher.append(current)
        if index == 0:
            expected = current.rgb
        else:
            overlap = 96 - (start - previous_start)
            seam = overlap // 2
            expected = expected[: -(overlap - seam) * 6] + current.rgb[seam * 6 :]
        output = stitcher.finish()
        assert output.rgb == expected
        assert output.height == start + 96
        assert len(output.rgb) == (start + 96) * 6
        previous_start = start
