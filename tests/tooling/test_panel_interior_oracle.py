"""Independent exhaustive boundary-aware overlap oracle."""

from __future__ import annotations

import pytest
from scripts.panel_probe_oracle import (
    Pixels,
    interior_overlap_displacements,
    overlap_displacements,
)


def frame(rows: list[int]) -> Pixels:
    """Build independently identifiable full-width rows."""
    return Pixels(1, len(rows), b"".join(bytes((row, row, row)) for row in rows))


def test_one_clipped_boundary_scanline_is_recorded_separately() -> None:
    """Keep old exact diagnostics while checking the interior."""
    first = frame(list(range(100)))
    second = frame([250, *range(41, 140)])
    assert overlap_displacements(first, second) == []
    assert interior_overlap_displacements(first, second) == [40]


def test_adjacent_interior_error_is_not_clipping() -> None:
    """The second row remains mandatory exact evidence."""
    first = frame(list(range(100)))
    second = frame([250, 251, *range(42, 140)])
    assert interior_overlap_displacements(first, second) == []


def test_repetition_does_not_pick_one_candidate() -> None:
    """Every plausible periodic shift remains visible to admission."""
    first = frame([0, 1] * 50)
    second = frame([1, 0] * 50)
    shifts = interior_overlap_displacements(first, second)
    assert len(shifts) > 1
    assert shifts == list(range(1, 74, 2))


def test_minimum_applies_after_excluded_rows() -> None:
    """The evidence minimum excludes both boundary rows."""
    first = frame(list(range(100)))
    assert interior_overlap_displacements(first, frame(list(range(73, 173)))) == [73]
    assert interior_overlap_displacements(first, frame(list(range(74, 174)))) == []


def test_dimension_change_rejected() -> None:
    """Geometry changes never enter matching."""
    with pytest.raises(ValueError, match="dimensions changed"):
        interior_overlap_displacements(frame([1] * 100), frame([1] * 99))
