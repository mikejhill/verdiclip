"""The independent native-reference plan covers every authored pixel once."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QPainter
from scripts import scrolling_owned_layouts as layouts


@pytest.mark.parametrize("viewport", [240, 400, 501, 568, 777, 900])
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
        if index < len(plan) - 1:
            assert source + count <= viewport - layouts._INTERIOR_GUARD
        seen.extend(range(offset + source, offset + source + count))
    assert seen == list(range(total))
    assert plan[-1][0] == total - viewport


@pytest.mark.parametrize(
    ("total", "viewport", "header"),
    [(100, 100, 10), (50, 100, 10), (300, 100, 100), (300, 100, 0), (300, 100, 68)],
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


def test_native_bottom_artifacts_are_retained_only_at_final_boundary() -> None:
    """Reproduce the observed 568px viewport's bottom three non-document rows."""
    total, viewport, header, width = 2464, 568, 64, 3
    border_height = 3
    border = 0xFFFF00FF
    expected = QImage(width, total, QImage.Format.Format_RGB32)
    for row in range(total):
        for column in range(width):
            expected.setPixel(column, row, 0xFF000000 | row)
    for row in range(total - border_height, total):
        for column in range(width):
            expected.setPixel(column, row, border)
    actual = QImage(width, total, QImage.Format.Format_RGB32)
    actual.fill(0)
    painter = QPainter(actual)
    destination = 0
    try:
        for offset, source, count in layouts.tile_plan(total, viewport, header):
            screenshot = QImage(width, viewport, QImage.Format.Format_RGB32)
            for row in range(viewport):
                value = row if row < header else offset + row
                pixel = (
                    border if row >= viewport - border_height else 0xFF000000 | value
                )
                for column in range(width):
                    screenshot.setPixel(column, row, pixel)
            painter.drawImage(0, destination, screenshot.copy(0, source, width, count))
            destination += count
    finally:
        painter.end()
    assert destination == total
    assert actual == expected
    assert actual.pixel(0, 565) == 0xFF000000 | 565
    assert actual.pixel(0, 567) == 0xFF000000 | 567
