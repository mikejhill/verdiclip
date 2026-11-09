"""The independent native-reference plan covers every authored pixel once."""

from __future__ import annotations

import pytest
from scripts import scrolling_owned_layouts as layouts


@pytest.mark.parametrize("viewport", [240, 400, 501, 777, 900])
@pytest.mark.parametrize("scale", [1, 1.25, 2])
def test_reference_tiles_cover_every_row(viewport: int, scale: float) -> None:
    """Reference assembly does not depend on matcher steps, seams or overlaps."""
    total = round((layouts._HEADER + layouts._ROWS * layouts._ROW) * scale)
    header = round(layouts._HEADER * scale)
    plan = layouts.tile_plan(total, viewport, header)
    seen = []
    for index, (offset, source, count) in enumerate(plan):
        assert 0 <= offset <= total - viewport
        assert 0 <= source < source + count <= viewport
        if index:
            assert source >= header
        seen.extend(range(offset + source, offset + source + count))
    assert seen == list(range(total))
    assert plan[-1][0] == total - viewport


@pytest.mark.parametrize(
    ("total", "viewport", "header"),
    [(100, 100, 10), (50, 100, 10), (300, 100, 100), (300, 100, 0)],
)
def test_invalid_reference_geometry(total: int, viewport: int, header: int) -> None:
    """A changed provider viewport cannot create fabricated expected pixels."""
    with pytest.raises(ValueError, match="geometry"):
        layouts.tile_plan(total, viewport, header)


def test_owned_documents_stay_distinct_from_live_sites() -> None:
    """Both are authored fixtures with explicit finite dimensions and no scripts."""
    for case in layouts._CASES:
        document = layouts.document(case)
        assert document.count('class="row"') == layouts._ROWS
        assert "position:sticky" in document
        assert "scrollbar-width:none" in document
        assert "<script" not in document
        assert "https://" not in document
    assert layouts.document(layouts._CASES[0]) != layouts.document(layouts._CASES[1])
