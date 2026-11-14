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
# Differing pixels sampled per row to tell where an overlay sits.
_SAMPLES = 64
# A see-through band along the bottom may skip this many rows (rounding).
_FADE_GAP = 2
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


def pixel_offset(first: QImage, second: QImage, expected: float | None) -> int | None:
    """How many rows ``second`` moved past ``first``, by their pixels, if trusted."""
    rows = [_rows(first), _rows(second)]
    header, footer = _band(rows, top=True), _band(rows, top=False)
    width, height = first.width(), first.height()
    margin = min(width // 10, 32)
    keys = [_keys(r[header : height - footer], margin, width - margin) for r in rows]
    return find_offset(keys[0], keys[1], expected)


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
    # Cropping loses pixels for good: insist on a scrollbar, not just change.
    # From the first aligned frame: a dropped stale frame shows the thumb low.
    used = [rows[seams[0][0] - 1], *(rows[index] for index, _ in seams)]
    if not any(
        _thumb(used, edge + x * 4, top, body)
        or (seen[x] >= needed and _frozen(used, edge + x * 4, top, body))
        for x in range(first, window)
    ):
        return 0
    return window - first


def _frozen(frames: list[list[bytes]], at: int, top: int, body: int) -> bool:
    """Whether a column with something drawn in it is the same in every frame.

    Some overlay scrollbars do not redraw while scrolled from outside: the
    strip stays put in every frame while the page moves under it, which
    content never does. It may fade or catch a redraw in some frames, so
    about half must agree. A plain column or a fixed toolbar stays put too,
    so it must look like a thumb.
    """
    columns = Counter(
        b"".join(row[at : at + 4] for row in frame[top : top + body])
        for frame in frames
    )
    column, count = columns.most_common(1)[0]
    if count < max(2, (len(frames) + 1) // 2):
        return False
    # A thumb on a plain track: one run off the track colour. A plain column
    # has no run, and a toolbar of icons has several.
    pixels = [column[i : i + 4] for i in range(0, len(column), 4)]
    track = Counter(pixels).most_common(1)[0][0]
    marks = [y for y, pixel in enumerate(pixels) if pixel != track]
    runs = 1 + sum(b - a > _THUMB_GAP for a, b in pairwise(marks))
    return len(marks) >= _MIN_VOTES and runs == 1


def _thumb(frames: list[list[bytes]], at: int, top: int, body: int) -> bool:
    """Whether one column shows a scrollbar thumb moving down a plain track.

    In each frame the thumb must be a single run, either of one common colour
    or of everything but a common track colour, and it must move down from
    frame to frame. Text and pictures leave several runs, and content moves
    up the viewport as the capture moves down.
    """
    columns = [[r[y][at : at + 4] for y in range(top, top + body)] for r in frames]
    least = _MIN_TRACK * body * len(columns)
    colours = Counter(p for column in columns for p in column).most_common(3)
    # The common colour is the track, or (a short page's long thumb) the thumb.
    return any(
        _moves_down(columns, colour, thumb=thumb)
        for colour, count in colours
        for thumb in (False, True)
        if thumb or count >= least
    )


def _moves_down(columns: list[list[bytes]], colour: bytes, *, thumb: bool) -> bool:
    """Whether the thumb (``colour``, or all but it) is one run moving down."""
    centres: list[float] = []
    for column in columns:
        marks = [y for y, pixel in enumerate(column) if (pixel == colour) == thumb]
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


def _overlay(
    rows: list[list[bytes]],
    seams: list[tuple[int, int]],
    band: tuple[int, int],
    columns: tuple[int, int],
) -> set[int]:
    """Body rows covered by something that stays put while the page scrolls.

    ``band`` is the body's (top, height) and ``columns`` the (left, right)
    pixels compared. A floating button over the text makes aligned frames
    disagree where it is, and it looks the same in every frame at that place
    in the viewport, while the text under it does not. Rows must disagree at
    about half the seams, so an occasional animation or changed line does not.
    """
    if not seams:
        return set()
    (top, body), (left, right) = band, (columns[0] * 4, columns[1] * 4)
    kept = [rows[seams[0][0] - 1], *(rows[index] for index, _ in seams)]
    fixed: Counter[int] = Counter()
    faded: Counter[int] = Counter()
    for index, offset in seams:
        previous, following = rows[index - 1], rows[index]
        found: set[int] = set()
        fading: set[int] = set()
        for y in range(body - offset):
            old = previous[top + y + offset][left:right]
            new = following[top + y][left:right]
            if old == new:
                continue
            # Over the old frame, or the new one: where the differing pixels
            # are the same in every frame, something sits still over the page.
            differing = [
                x for x in range(0, len(old), 4) if old[x : x + 4] != new[x : x + 4]
            ]
            sample = differing[:: max(1, len(differing) // _SAMPLES)]
            scores = [
                sum(
                    len({frame[top + place][left + x : left + x + 4] for frame in kept})
                    == 1
                    for x in sample
                )
                for place in (y + offset, y)
            ]
            if scores[1] > scores[0]:
                found.add(y)
            elif scores[0]:
                found.add(y + offset)
            else:
                fading.add(y + offset)
        fixed.update(found)
        faded.update(fading)
    needed = (len(seams) + 1) // 2
    overlay = {place for place, count in fixed.items() if count >= needed}
    # A see-through band (a fade above a chat's input box) is never the same
    # twice, so it cannot be told from text changing by its pixels. Trust it
    # only along the bottom edge, where such bands sit; elsewhere it could be
    # an overlay over the new frame instead.
    row, gap = body - 1, 0
    while row >= 0 and gap <= _FADE_GAP:
        if faded[row] >= needed:
            overlay.add(row)
            gap = 0
        else:
            gap += 1
        row -= 1
    return overlay


def _avoid_overlay(pieces: list[list[int]], overlay: set[int]) -> None:
    """Move seams above a floating overlay, so the next frame shows what it hid.

    Each frame then ends just above the overlay, and the next frame, which
    shows those rows higher up, supplies them. Only the last frame keeps it.
    """
    if not overlay:
        return
    for previous, current in pairwise(pieces):
        if current[0] != previous[0] + 1:
            continue  # A frame that did not move sits between them
        offset = previous[2] - current[1]  # How far the current frame moved
        below = [row for row in overlay if offset <= row < previous[2]]
        if below and min(below) > previous[1]:
            cut = min(below)
            previous[2], current[1] = cut, cut - offset


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

    # [frame index, first body row to keep, end row] for each kept frame.
    pieces: list[list[int]] = [[lead, 0, body]]
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
            pieces.append([index, body - offset, body])
    # Leave out the scrollbar: each frame would show its thumb somewhere else.
    width -= _strip(rows, seams, header, body)
    _avoid_overlay(pieces, _overlay(rows, seams, (header, body), (0, width)))
    first = frames[lead].image

    total = header + sum(end - start for _, start, end in pieces) + footer
    result = QImage(width, total, QImage.Format.Format_RGB32)
    painter = QPainter(result)
    try:
        painter.drawImage(QPoint(0, 0), first, QRect(0, 0, width, header))
        y = header
        for index, start, end in pieces:
            span = end - start
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
