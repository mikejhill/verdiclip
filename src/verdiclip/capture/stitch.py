"""Join same-size viewport frames, captured top to bottom, into one tall image.

The policy is "best image possible": each pair of frames is aligned by the
offset that explains the most rows. When nothing explains enough rows, the
scroll distance the provider reported is used instead and the result is
flagged, rather than discarding the capture.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPainter

# Fraction of comparable rows an offset must explain to be trusted.
MIN_SCORE = 0.6
# Fraction of all informative overlap rows it must explain. Lower than
# MIN_SCORE, so an overlay that appears in only some frames (a sticky header)
# does not reject a good join, while a few coincidences still do.
MIN_COVERAGE = 0.3
# A row seen more often than this in one frame (blank space, repeated lines)
# says little about alignment and is left out of the vote.
_MAX_REPEATS = 8
# At least this many rows must agree on an offset.
_MIN_VOTES = 4
# Header/footer bands never take more than this share of the viewport.
_MAX_BAND = 1 / 3
# A scrollbar strip is looked for within this many columns of the right edge.
_MAX_STRIP = 40
# A scrollbar column is at least this share of plain track.
_MIN_TRACK = 0.3
# A thumb's run of pixels may have gaps this small (anti-aliased edges).
_THUMB_GAP = 2
# Two frames sharing this share of their rows in place show the same view.
_SAME_VIEW = 0.9


@dataclass(frozen=True, slots=True)
class Frame:
    """One settled viewport image and how far the provider says it moved.

    ``moved`` is the scroll distance in pixels from the previous frame as
    reported by the provider, or ``None`` when it is unknown.
    """

    image: QImage
    moved: float | None = None


@dataclass(frozen=True, slots=True)
class StitchResult:
    """The joined image, seams that fell back to the estimate, frames dropped."""

    image: QImage
    estimated_seams: int
    stale_frames: int = 0


def _rows(image: QImage) -> list[bytes]:
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    width, stride = image.width() * 4, image.bytesPerLine()
    data = bytes(image.constBits())
    return [data[y * stride : y * stride + width] for y in range(image.height())]


def _uniform(row: bytes) -> bool:
    return row == row[:4] * (len(row) // 4)


def _band(frames: list[list[bytes]], *, top: bool) -> int:
    """Rows identical in every frame at the top (or bottom), minus blank edges."""
    if len(frames) < 2:  # noqa: PLR2004 - a band needs two observations
        return 0
    height = len(frames[0])
    limit = int(height * _MAX_BAND)
    size = 0
    while size < limit:
        index = size if top else height - 1 - size
        row = frames[0][index]
        if any(rows[index] != row for rows in frames[1:]):
            break
        size += 1
    # Solid rows at the inner edge look the same as page background, so they
    # cannot be told apart from content. Leave them to the moving body.
    while size:
        index = size - 1 if top else height - size
        if not _uniform(frames[0][index]):
            break
        size -= 1
    return size


def view_moved(first: QImage, second: QImage, animation: int) -> bool:
    """Whether ``second`` shows more change than an animation of a few rows.

    A move changes most rows that hold anything; a spinner, caret or ticking
    timer changes up to ``animation`` rows, wherever it is.
    """
    if first.size() != second.size():
        return True
    old, new = _rows(first), _rows(second)
    changed = sum(a != b for a, b in zip(old, new, strict=True))
    detailed = sum(not _uniform(row) for row in old)
    return changed > animation or 2 * changed > detailed > 0


def fixed_rows(first: QImage, second: QImage) -> int:
    """Rows of header and footer that stayed put between two same-size frames."""
    rows = [_rows(first), _rows(second)]
    return _band(rows, top=True) + _band(rows, top=False)


def _keys(rows: list[bytes], left: int, right: int) -> list[int]:
    return [hash(row[left * 4 : right * 4]) for row in rows]


def _votes(
    previous: Sequence[int], following: Sequence[int]
) -> tuple[Counter[int], list[int], list[int]]:
    """Count offsets supported by matching rows, with two prefix sums.

    ``known`` counts rows of ``following`` that also appear in ``previous``;
    ``informative`` counts every row that does not repeat too often in its own
    frame, so rows that match nothing still weigh against a few coincidences.
    """
    counts = Counter(previous)
    positions: dict[int, list[int]] = defaultdict(list)
    for index, key in enumerate(previous):
        if counts[key] <= _MAX_REPEATS:
            positions[key].append(index)
    own = Counter(following)
    known: list[int] = [0]
    informative: list[int] = [0]
    for key in following:
        useful = own[key] <= _MAX_REPEATS
        known.append(known[-1] + int(useful and key in positions))
        informative.append(informative[-1] + int(useful))
    votes: Counter[int] = Counter()
    for index, key in enumerate(following):
        if own[key] > _MAX_REPEATS:
            continue
        for match in positions.get(key, ()):
            if match > index:
                votes[match - index] += 1
    return votes, known, informative


def find_offset(
    previous: Sequence[int], following: Sequence[int], expected: float | None
) -> int | None:
    """Return how many rows ``following`` moved past ``previous``, if trusted.

    Both sequences are row keys of equal length. The answer ``d`` means row
    ``j`` of ``following`` shows row ``j + d`` of ``previous``.
    """
    height = len(previous)
    votes, known, informative = _votes(previous, following)
    trusted: list[tuple[int, float, int]] = []  # (votes, score, offset)
    for offset, count in votes.items():
        comparable = known[height - offset]
        if (
            count < _MIN_VOTES
            or comparable == 0
            or count < MIN_COVERAGE * informative[height - offset]
            or count / comparable < MIN_SCORE
        ):
            continue
        trusted.append((count, count / comparable, offset))
    if not trusted:
        return None
    # A handful of coincidences must not beat a well-supported offset, but
    # comparably supported ones (repeating content) go to the provider's.
    strongest = max(count for count, _, _ in trusted)
    finalists = [t for t in trusted if 2 * t[0] >= strongest]

    def rank(candidate: tuple[int, float, int]) -> tuple[float, float, int]:
        count, score, offset = candidate
        distance = abs(offset - expected) if expected is not None else 0.0
        return (-distance, score, count)

    return max(finalists, key=rank)[2]


def _same_view(first: Sequence[int], second: Sequence[int]) -> bool:
    """Whether two frames show the same view, apart from small animations.

    Only informative rows count, so two mostly blank views are not the same.
    """
    counts = Counter(first)
    pairs = [(a, b) for a, b in zip(first, second, strict=True) if counts[a] == 1]
    same = sum(a == b for a, b in pairs)
    return len(pairs) >= _MIN_VOTES and same >= _SAME_VIEW * len(pairs)


def _strip(
    rows: list[list[bytes]], seams: list[tuple[int, int]], top: int, body: int
) -> int:
    """Columns at the right edge that did not scroll with the content.

    A scrollbar stays put while the page moves, so once two frames are aligned
    its thumb disagrees with itself in rows whose content agrees. ``seams`` are
    the aligned (frame index, offset) pairs; the answer is the strip's width.
    """
    width = len(rows[0][0]) // 4
    window = min(_MAX_STRIP, width // 10)
    if not window or not seams:
        return 0
    edge = (width - window) * 4
    # Seams at which each column disagreed: a thumb moves at nearly every
    # seam, while content that changed (a timestamp) does so only now and then.
    seen = [0] * window
    for index, offset in seams:
        previous, following = rows[index - 1], rows[index]
        misses = [0] * window
        for y in range(top, top + body - offset):
            old, new = previous[y + offset], following[y]
            if old == new or old[:edge] != new[:edge]:
                continue  # Agrees, or the content itself changed here
            for x in range(window):
                at = edge + x * 4
                misses[x] += old[at : at + 4] != new[at : at + 4]
        for x, count in enumerate(misses):
            seen[x] += count >= _MIN_VOTES
    needed = (len(seams) + 1) // 2
    first = next((x for x, count in enumerate(seen) if count >= needed > 0), window)
    # Cropping loses pixels for good: insist on a thumb, not just change.
    # From the first aligned frame: a dropped stale frame shows the thumb low.
    used = [rows[seams[0][0] - 1], *(rows[index] for index, _ in seams)]
    if not any(_thumb(used, edge + x * 4, top, body) for x in range(first, window)):
        return 0
    return window - first


def _thumb(frames: list[list[bytes]], at: int, top: int, body: int) -> bool:
    """Whether one column shows a scrollbar thumb moving down a plain track.

    In each frame the column must be mostly one track colour with a single
    run of other pixels, and that run must move down from frame to frame.
    Text, timestamps and pictures leave several runs or no common track.
    """
    columns = [[r[y][at : at + 4] for y in range(top, top + body)] for r in frames]
    track, count = Counter(p for column in columns for p in column).most_common(1)[0]
    if count < _MIN_TRACK * body * len(columns):
        return False
    centres: list[float] = []
    for column in columns:
        marks = [y for y, pixel in enumerate(column) if pixel != track]
        if not marks:
            continue  # Overlay scrollbars fade out
        runs = 1 + sum(b - a > _THUMB_GAP for a, b in pairwise(marks))
        if runs > 1 or len(marks) < _MIN_VOTES:
            return False
        centres.append((marks[0] + marks[-1]) / 2)
    return (
        len(centres) > 1
        and all(b >= a for a, b in pairwise(centres))
        and centres[-1] > centres[0]
    )


def stitch(frames: Sequence[Frame]) -> StitchResult:
    """Join ``frames`` (all the same size) into one image."""
    if not frames:
        raise ValueError("Nothing to stitch")
    first = frames[0].image
    width, height = first.width(), first.height()
    if any(f.image.width() != width or f.image.height() != height for f in frames):
        raise ValueError("Frames differ in size")
    rows = [_rows(f.image) for f in frames]
    header = _band(rows, top=True)
    footer = _band(rows, top=False)
    body = height - header - footer
    margin = min(width // 10, 32)
    keys = [_keys(r[header : height - footer], margin, width - margin) for r in rows]
    # Frames taken before the panel reached the top show a later view again;
    # they join nothing and would repeat that content above the page.
    lead = 0
    while (
        lead + 1 < len(frames)
        and find_offset(keys[lead], keys[lead + 1], None) is None
        and any(_same_view(keys[lead], later) for later in keys[lead + 1 :])
    ):
        lead += 1

    # (frame index, first body row to keep) for each frame after the first.
    pieces: list[tuple[int, int]] = [(lead, 0)]
    seams: list[tuple[int, int]] = []  # (frame index, offset) aligned by pixels
    estimated = 0
    for index in range(lead + 1, len(frames)):
        moved = frames[index].moved
        offset = find_offset(keys[index - 1], keys[index], moved)
        if offset is None:
            estimated += 1
            offset = body if moved is None else round(moved)
        else:
            seams.append((index, offset))
        offset = max(0, min(body, offset))
        if offset:
            pieces.append((index, body - offset))
    # Leave out the scrollbar: each frame would show its thumb somewhere else.
    width -= _strip(rows, seams, header, body)
    first = frames[lead].image

    total = header + sum(body - start for _, start in pieces) + footer
    result = QImage(width, total, QImage.Format.Format_RGB32)
    painter = QPainter(result)
    try:
        painter.drawImage(QPoint(0, 0), first, QRect(0, 0, width, header))
        y = header
        for index, start in pieces:
            span = body - start
            source = QRect(0, header + start, width, span)
            painter.drawImage(QPoint(0, y), frames[index].image, source)
            y += span
        if footer:
            last = frames[-1].image
            painter.drawImage(
                QPoint(0, y), last, QRect(0, height - footer, width, footer)
            )
    finally:
        painter.end()
    return StitchResult(result, estimated, lead)
