"""Conservative vertical registration that abstains instead of guessing seams."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtGui import QImage

from verdiclip.exceptions import CaptureError


class MatchStatus(StrEnum):
    """Registration verdicts are separate from evidence of document extent."""

    VERIFIED = "verified"
    STATIONARY = "stationary"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    GEOMETRY_CHANGED = "geometry_changed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class Registration:
    """Only a unique verified overlap supplies an appendable displacement."""

    status: MatchStatus
    shift: int | None = None


class PixelRows:
    """Canonical full-width pixels and collision-resistant row fingerprints."""

    def __init__(self, image: QImage) -> None:
        """Own a canonical byte buffer independent of QImage row alignment."""
        if image.isNull():
            raise CaptureError("Cannot register an empty viewport")
        canonical = image.convertToFormat(QImage.Format.Format_ARGB32)
        self.width = canonical.width()
        self.height = canonical.height()
        self.stride = canonical.bytesPerLine()
        self.data = bytes(canonical.constBits())
        self.hashes = tuple(self._hash(self.row(y)) for y in range(self.height))

    def row(self, y: int) -> bytes:
        """Return actual pixel bytes, excluding any row padding."""
        start = y * self.stride
        return self.data[start : start + self.width * 4]

    @staticmethod
    def _hash(data: bytes) -> bytes:
        """Fingerprint only to find candidates; exact pixels confirm the match."""
        return hashlib.blake2b(data, digest_size=16).digest()

    def textured(self, rows: int) -> bool:
        """Require vertical detail across three separate width bands."""
        if len(set(self.hashes[:rows])) < min(8, rows):
            return False
        for band in range(3):
            left, right = self.width * band // 3, self.width * (band + 1) // 3
            detail = {
                self._hash(self.row(y)[left * 4 : right * 4]) for y in range(rows)
            }
            if len(detail) < min(4, rows):
                return False
        return True


class OverlapMatcher:
    """Verify exact, unique vertical overlap; unsupported motion is refused.

    This bounded MVP does not implement tolerant/warped registration or sticky
    element inference. Callers must crop fixed chrome and supply stable frames.
    """

    def compare(self, previous: QImage, current: QImage) -> Registration:
        """Return a displacement only when full-resolution pixels prove it."""
        if previous.size() != current.size():
            return Registration(MatchStatus.GEOMETRY_CHANGED)
        old, new = PixelRows(previous), PixelRows(current)
        if old.data == new.data:
            return Registration(MatchStatus.STATIONARY)
        minimum = max(8, (old.height + 3) // 4)
        candidates = self._overlaps(new.hashes, old.hashes, minimum)
        if not candidates:
            return Registration(MatchStatus.UNMATCHED)
        if len(candidates) != 1:
            return Registration(MatchStatus.AMBIGUOUS)
        overlap = candidates[0]
        shift = old.height - overlap
        if any(old.row(shift + y) != new.row(y) for y in range(overlap)):
            return Registration(MatchStatus.UNMATCHED)
        return (
            Registration(MatchStatus.VERIFIED, shift)
            if new.textured(overlap)
            else Registration(MatchStatus.AMBIGUOUS)
        )

    @staticmethod
    def _overlaps(
        prefix: tuple[bytes, ...], suffix: tuple[bytes, ...], minimum: int
    ) -> list[int]:
        """Find every valid overlap in linear row-count time using KMP borders."""
        # An empty fingerprint cannot collide with the fixed-width row digests.
        values = (*prefix, b"", *suffix)
        borders = [0] * len(values)
        for index in range(1, len(values)):
            length = borders[index - 1]
            while length and values[index] != values[length]:
                length = borders[length - 1]
            if values[index] == values[length]:
                length += 1
            borders[index] = length
        length = borders[-1]
        candidates: list[int] = []
        while length >= minimum:
            if length < len(prefix):
                candidates.append(length)
            length = borders[length - 1]
        return candidates
