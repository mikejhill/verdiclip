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
    """The joined image and how many seams fell back to the provider estimate."""

    image: QImage
    estimated_seams: int


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
    best: tuple[float, float, int] | None = None
    for offset, count in votes.items():
        comparable = known[height - offset]
        if (
            count < _MIN_VOTES
            or comparable == 0
            or count < MIN_COVERAGE * informative[height - offset]
        ):
            continue
        distance = abs(offset - expected) if expected is not None else 0.0
        candidate = (count / comparable, -distance, offset)
        if best is None or candidate > best:
            best = candidate
    if best is None or best[0] < MIN_SCORE:
        return None
    return best[2]


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

    # (frame index, first body row to keep) for each frame after the first.
    pieces: list[tuple[int, int]] = [(0, 0)]
    estimated = 0
    for index in range(1, len(frames)):
        moved = frames[index].moved
        offset = find_offset(keys[index - 1], keys[index], moved)
        if offset is None:
            estimated += 1
            offset = body if moved is None else round(moved)
        offset = max(0, min(body, offset))
        if offset:
            pieces.append((index, body - offset))

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
    return StitchResult(result, estimated)
