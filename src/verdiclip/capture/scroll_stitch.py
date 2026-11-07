"""Exact, bounded RGB stitching; the caller verifies endpoints and providers."""

from dataclasses import dataclass

MAX_VIEWPORT_PIXELS = 16_000_000
MAX_OUTPUT_PIXELS = 64_000_000
MAX_OUTPUT_ROWS = 32_768
MAX_FRAMES = 128
MIN_OVERLAP_ROWS = 24
_CHANNELS = 3


class StitchError(ValueError):
    """A frame cannot safely contribute to a scrolling composite."""


@dataclass(frozen=True)
class ScrollFrame:
    """An owned immutable, tightly packed, top-down RGB viewport."""

    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        """Reject malformed or oversized buffers before matching allocations."""
        if type(self.width) is not int or type(self.height) is not int:
            raise StitchError("Viewport dimensions must be integers")
        if self.width <= 0 or self.height <= 0:
            raise StitchError("Viewport dimensions must be positive")
        if self.width * self.height > MAX_VIEWPORT_PIXELS:
            raise StitchError("Viewport pixel limit exceeded")
        if self.height > MAX_OUTPUT_ROWS:
            raise StitchError("Viewport row limit exceeded")
        if type(self.rgb) is not bytes:
            raise StitchError("Viewport RGB buffer must be immutable bytes")
        if len(self.rgb) != self.width * self.height * _CHANNELS:
            raise StitchError("Viewport RGB buffer length does not match dimensions")


@dataclass(frozen=True)
class StitchStep:
    """Accepted geometry, not evidence that the panel endpoint was reached."""

    overlap_rows: int
    new_rows: int
    height: int
    frame_count: int


@dataclass(frozen=True)
class StitchedImage:
    """Observed RGB rows only; caller must verify completion before delivery."""

    width: int
    height: int
    rgb: bytes
    frame_count: int


class ScrollStitcher:
    """Accumulate exact rows without repeatedly copying the growing image.

    KMP compares full scanline bytes, never hashes or approximate pixels. Its
    failure chain enumerates every eligible interior overlap in linear row work.
    Only the first and last overlap scanlines are excluded from alignment;
    an interior midpoint splice keeps both viewport-clipped edges out of the seam.
    Row storage is bounded by two viewports; output chunks are joined once on
    finish. Failed appends leave all accepted state unchanged.
    """

    def __init__(self) -> None:
        self._previous: ScrollFrame | None = None
        self._rows: list[bytes] = []
        self._chunks: list[bytes] = []
        self._height = 0

    def append(self, frame: ScrollFrame) -> StitchStep:
        """Accept one viewport or fail closed on geometry, limits or a seam."""
        if len(self._chunks) >= MAX_FRAMES:
            raise StitchError("Accepted frame limit exceeded")
        previous = self._previous
        if previous is not None:
            if (frame.width, frame.height) != (previous.width, previous.height):
                raise StitchError("Viewport dimensions changed")
            if frame.rgb == previous.rgb:
                raise StitchError(
                    "Unchanged frame is not progress or endpoint evidence"
                )
        stride = frame.width * _CHANNELS
        rows = [frame.rgb[i : i + stride] for i in range(0, len(frame.rgb), stride)]
        overlap = 0 if previous is None else self._overlap(rows)
        added = frame.height - overlap
        height = self._height + added
        if height > MAX_OUTPUT_ROWS:
            raise StitchError("Output row limit exceeded")
        if height * frame.width > MAX_OUTPUT_PIXELS:
            raise StitchError("Output pixel limit exceeded")
        seam = overlap // 2
        chunk = frame.rgb[seam * stride :]
        if previous is not None:
            # The next midpoint lies in the last chunk, even for a clipped step.
            # Prepare both bounded slices before mutating accepted state.
            retained = self._chunks[-1][: -(overlap - seam) * stride]
            self._chunks[-1] = retained
        self._chunks.append(chunk)
        self._previous = frame
        self._rows = rows
        self._height = height
        return StitchStep(overlap, added, height, len(self._chunks))

    def finish(self) -> StitchedImage:
        """Materialize accepted rows; this does not certify a complete capture."""
        if self._previous is None:
            raise StitchError("No frames have been accepted")
        return StitchedImage(
            self._previous.width,
            self._height,
            b"".join(self._chunks),
            len(self._chunks),
        )

    def _overlap(self, rows: list[bytes]) -> int:
        minimum = max(MIN_OVERLAP_ROWS, (len(rows) + 3) // 4)
        rows = rows[1:-1]
        if len(rows) <= minimum:
            raise StitchError("No sufficient exact full-width interior overlap")
        failure = self._prefix_lengths(rows)
        matched: int = 0
        for row in self._rows[1:-1]:
            while matched and row != rows[matched]:
                matched = int(failure[matched - 1])
            if row == rows[matched]:
                matched += 1
        if matched == len(rows):
            raise StitchError("Unchanged interior is not progress")
        if matched < minimum:
            raise StitchError("No sufficient exact full-width overlap")
        if failure[matched - 1] >= minimum:
            raise StitchError("Ambiguous overlap: multiple plausible displacements")
        return matched + 2

    @staticmethod
    def _prefix_lengths(rows: list[bytes]) -> list[int]:
        failure: list[int] = [0] * len(rows)
        matched: int = 0
        for index in range(1, len(rows)):
            while matched and rows[index] != rows[matched]:
                matched = int(failure[matched - 1])
            if rows[index] == rows[matched]:
                matched += 1
            failure[index] = matched
        return failure
