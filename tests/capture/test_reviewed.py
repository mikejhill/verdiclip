"""The controller delivery boundary owns accepted pixels without editor coupling."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from typing import cast

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


def test_default_capture_identity_is_new_for_each_capture() -> None:
    """Two equivalent images are still independent captures with separate receipts."""
    image = Documents.image(height=120)
    created_at = datetime(2026, 10, 25, tzinfo=UTC)
    first = ReviewedCapture(image, "Example", created_at, "region")
    second = ReviewedCapture(image, "Example", created_at, "region")
    assert first.capture_id
    assert first.capture_id != second.capture_id
    assert first.completeness is CaptureCompleteness.COMPLETE
    assert first.stop_reason is None


def test_explicit_identity_and_provenance_survive_retry_reconstruction() -> None:
    """A retained identity lets the controller retry sinks without duplicate tabs."""
    image = Documents.image(height=120)
    created_at = datetime(2026, 10, 25, tzinfo=UTC)
    first = ReviewedCapture(
        image,
        "Example",
        created_at,
        "scrolling",
        CaptureCompleteness.PARTIAL,
        "target_changed",
        capture_id="capture-fixture-1",
    )
    retried = ReviewedCapture(
        image,
        "Example",
        created_at,
        "scrolling",
        CaptureCompleteness.PARTIAL,
        "target_changed",
        capture_id=first.capture_id,
    )
    assert retried.capture_id == first.capture_id
    assert retried.provenance == first.provenance
    assert retried.provenance.created_at == created_at


def test_document_provenance_does_not_retain_pixels() -> None:
    """Metadata admission cannot accidentally retain another full image payload."""
    result = ReviewedCapture(
        Documents.image(height=120),
        "Example",
        datetime(2026, 10, 25, tzinfo=UTC),
        "window",
    )
    assert {field.name for field in fields(result.provenance)} == {
        "capture_id",
        "title",
        "created_at",
        "source_kind",
        "completeness",
        "stop_reason",
    }
    assert result.provenance.capture_id == result.capture_id


@pytest.mark.parametrize(
    ("capture_id", "source_kind"),
    [("", "region"), (" ", "region"), ("capture", ""), ("capture", " ")],
)
def test_empty_identity_or_source_cannot_enter_delivery(
    capture_id: str, source_kind: str
) -> None:
    """Invalid routing identity fails before outputs or editor admission."""
    with pytest.raises(ValueError, match="identity and source"):
        ReviewedCapture(
            Documents.image(height=120),
            "Example",
            datetime(2026, 10, 25, tzinfo=UTC),
            source_kind,
            capture_id=capture_id,
        )


def test_unknown_completeness_is_rejected_at_the_boundary() -> None:
    """An invalid producer value cannot silently become a complete capture."""
    invalid = cast(CaptureCompleteness, cast(object, "unknown"))
    with pytest.raises(ValueError, match="completeness"):
        ReviewedCapture(
            Documents.image(height=120),
            "Example",
            datetime(2026, 10, 25, tzinfo=UTC),
            "scrolling",
            completeness=invalid,
        )
