"""Rendered-track geometry cannot conceal changes in the scrolling content."""

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import document

from verdiclip.capture.scrolling import AmbiguousScrollError, VerticalStitch
from verdiclip.exceptions import CaptureError


def track(image: QImage, start: int, *, width: int = 8, height: int = 84) -> None:
    """Draw synthetic stationary end marks and one capsule, without app pixels."""
    painter = QPainter(image)
    x = image.width() - width
    painter.fillRect(x - 1, 0, width + 1, image.height(), QColor("white"))
    for y in (4, image.height() - 6):
        painter.fillRect(x + 1, y, width - 4, 2, QColor("gray"))
    painter.fillRect(x + 1, start, width - 4, height, QColor("gray"))
    painter.fillRect(x, start + 1, width - 2, height - 2, QColor("gray"))
    painter.end()


def pair(*, width: int = 8) -> tuple[QImage, QImage, QImage]:
    """Build repeated controls and a moving track entirely from synthetic pixels."""
    source = document(520)
    repeated = source.copy(0, 120, 80, 60)
    painter = QPainter(source)
    painter.drawImage(0, 180, repeated)
    painter.end()
    first, second = source.copy(0, 0, 80, 240), source.copy(0, 100, 80, 240)
    track(first, 12, width=width)
    track(second, 120, width=width)
    return source, first, second


def test_track_disambiguates_complete_content_without_cropping_output() -> None:
    """Track geometry resolves repeated controls while retaining every output column."""
    source, first, second = pair()
    stitch = VerticalStitch(first)
    old, new = stitch._rows(first), stitch._rows(second)
    with pytest.raises(AmbiguousScrollError):
        stitch._alignment(old, new)
    with pytest.raises(AmbiguousScrollError):
        stitch._containment_alignment(old, new)
    assert stitch._scroll_track_width(old, new) == 8
    stitch.append(second, 100)
    result = stitch.finish()
    assert result.width() == first.width()
    assert result.height() == 340
    assert result.copy(0, 0, 71, 340) == source.copy(0, 0, 71, 340)
    assert result.copy(0, 0, 80, 4) == first.copy(0, 0, 80, 4)
    assert result.copy(0, 334, 80, 6) == second.copy(0, 234, 80, 6)


@pytest.mark.parametrize("damage", ["content", "end_mark", "thumb", "extra_mark"])
def test_track_rejects_changes_outside_its_exact_geometry(damage: str) -> None:
    """Neither body corruption nor changed track components may be concealed."""
    _source, first, second = pair()
    x, y = {
        "content": (40, 90),
        "end_mark": (74, 4),
        "thumb": (74, 150),
        "extra_mark": (74, 110),
    }[damage]
    second.setPixelColor(x, y, QColor("magenta"))
    stitch = VerticalStitch(first)
    with pytest.raises(AmbiguousScrollError):
        stitch._track_alignment(stitch._rows(first), stitch._rows(second))
    assert stitch.finish() == first


@pytest.mark.parametrize(("width", "accepted"), [(8, True), (9, False)])
def test_track_width_is_bounded_before_alignment(width: int, *, accepted: bool) -> None:
    """An extra edge column cannot extend the independent geometry limit."""
    _source, first, second = pair(width=width)
    stitch = VerticalStitch(first)
    assert (
        bool(stitch._scroll_track_width(stitch._rows(first), stitch._rows(second)))
        == accepted
    )


@pytest.mark.parametrize("change", ["reverse", "resize", "no_caps", "gap"])
def test_track_requires_identical_solid_downward_thumb(change: str) -> None:
    """Malformed or split tracks cannot excuse incomplete content equality."""
    _source, first, second = pair()
    if change == "reverse":
        first, second = second, first
    elif change == "resize":
        track(second, 120, height=85)
    elif change == "no_caps":
        painter = QPainter(second)
        painter.fillRect(72, 120, 6, 84, QColor("gray"))
        painter.end()
    else:
        for image in (first, second):
            painter = QPainter(image)
            painter.fillRect(75, 0, 1, image.height(), QColor("white"))
            painter.end()
    stitch = VerticalStitch(first)
    with pytest.raises(AmbiguousScrollError):
        stitch._track_alignment(stitch._rows(first), stitch._rows(second))


@pytest.mark.parametrize("expected", [None, 100.1, float("nan")])
def test_track_requires_exact_independent_native_confirmation(
    expected: float | None,
) -> None:
    """Provider estimates cannot select or approximately confirm this fallback."""
    _source, first, second = pair()
    stitch = VerticalStitch(first)
    with pytest.raises(CaptureError, match="exact native displacement"):
        stitch.append(second, expected)
    assert stitch.finish() == first


def test_non_generic_route_cannot_use_rendered_track() -> None:
    """Explicitly excluded native routes keep their stronger original contracts."""
    _source, first, second = pair()
    stitch = VerticalStitch(first, allow_containment=False)
    with pytest.raises(AmbiguousScrollError):
        stitch.append(second, 100)


def test_no_tail_compatible_seam_is_not_replaced_by_a_crop() -> None:
    """Earlier committed pixels cannot be discarded to use a different band."""
    _source, first, second = pair()
    stitch = VerticalStitch(first)
    stitch._tail_start = 239
    with pytest.raises(CaptureError, match="consistent interior join"):
        stitch._track_alignment(stitch._rows(first), stitch._rows(second))
