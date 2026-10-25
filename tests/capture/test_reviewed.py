"""The controller delivery boundary owns accepted pixels without editor coupling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtGui import QImage
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture


def test_producer_changes_do_not_change_reviewed_pixels() -> None:
    """Qt copy-on-write prevents later producer edits from corrupting delivery."""
    image = Documents.image(height=120)
    original = image.copy()
    result = ReviewedCapture(
        image,
        "Example",
        datetime(2026, 10, 25, tzinfo=UTC),
        CaptureKind.SCROLLING,
        CaptureCompleteness.PARTIAL,
        "stopped_by_user",
    )
    image.fill(0)
    assert result.image == original


def test_empty_pixels_cannot_cross_the_delivery_boundary() -> None:
    """An empty review result is an explicit error rather than a blank tab."""
    with pytest.raises(ValueError, match="pixels"):
        ReviewedCapture(
            QImage(),
            "Example",
            datetime(2026, 10, 25, tzinfo=UTC),
            CaptureKind.SCROLLING,
            CaptureCompleteness.PARTIAL,
            "error",
        )


def test_timezone_is_required_for_delivery_metadata() -> None:
    """Ambiguous local timestamps must not enter saved-image provenance."""
    naive = datetime(2026, 10, 25, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        ReviewedCapture(
            Documents.image(height=120),
            "Example",
            naive,
            CaptureKind.SCROLLING,
            CaptureCompleteness.PARTIAL,
            "stopped",
        )
