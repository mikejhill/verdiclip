"""Fixture-only exact seam-corridor experiment, never product qualification.

For every displacement from one row through half a viewport, compare a fixed
full-width corridor centered on that candidate's midpoint splice. Its length is
max(24, ceil(height / 4)). Search all candidates without provider hints, require
unique exact equality and observable motion, then use the unchanged stitcher's
midpoint splice and limits. Native displacement agreement remains mandatory
outside this module. No tolerance, crop, padding, or adaptive corridor is used.

A valid seam does not prove that the whole scene was static. Sticky content or
changes outside the corridor can be retained, duplicated, or missed. Exact row
provenance does not establish semantic completeness. This candidate is confined
to small owned-fixture frames until independent negative/native qualification.
"""

from __future__ import annotations

from typing import override

from scripts.panel_full_sweep import FullSweep

from verdiclip.capture.scroll_stitch import (
    MIN_OVERLAP_ROWS,
    ScrollFrame,
    ScrollStitcher,
    StitchError,
    StitchStep,
)

# Fixed work ceiling: at most 256 candidates * 128 rows * 3072 bytes per pair.
MAX_CANDIDATE_WIDTH = 1024
MAX_CANDIDATE_HEIGHT = 512


class SeamCandidate(ScrollStitcher):
    """Reuse atomic output assembly; replace only displacement evidence."""

    @override
    def append(self, frame: ScrollFrame) -> StitchStep:
        """Bound experimental search before the inherited method changes state."""
        if frame.width > MAX_CANDIDATE_WIDTH or frame.height > MAX_CANDIDATE_HEIGHT:
            raise StitchError("Seam candidate viewport exceeds experimental limit")
        if frame.height < 2 * MIN_OVERLAP_ROWS:
            raise StitchError("Seam candidate viewport is too short")
        return super().append(frame)

    @override
    def _overlap(self, rows: list[bytes]) -> int:
        height = len(rows)
        length = max(MIN_OVERLAP_ROWS, (height + 3) // 4)
        candidates = []
        for displacement in range(1, height // 2 + 1):
            midpoint = (height - displacement) // 2
            start = midpoint - length // 2
            end = start + length
            if self._rows[start + displacement : end + displacement] == rows[start:end]:
                candidates.append((displacement, start, end))
        if len(candidates) != 1:
            raise StitchError("Seam corridor displacement is absent or ambiguous")
        displacement, start, end = candidates[0]
        if self._rows[start:end] == rows[start:end]:
            raise StitchError("Stationary seam corridor does not establish motion")
        return height - displacement


class SeamSweep(FullSweep):
    """Fixture-only alternative retaining every native full-sweep completion gate."""

    @override
    def _make_stitcher(self) -> ScrollStitcher:
        """Select corridor evidence without changing endpoint or restoration checks."""
        return SeamCandidate()
