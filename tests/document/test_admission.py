"""Document metadata preserves the single canonical reviewed result's evidence."""

from dataclasses import fields
from datetime import UTC, datetime

import pytest
from PySide6.QtGui import QImage

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture
from verdiclip.document.admission import CaptureProvenance


@pytest.mark.parametrize("kind", list(CaptureKind))
@pytest.mark.parametrize("completeness", list(CaptureCompleteness))
@pytest.mark.parametrize("reason", [None, "reached_reported_end"])
def test_provenance_preserves_canonical_evidence_without_pixels(
    sample_image: QImage,
    kind: CaptureKind,
    completeness: CaptureCompleteness,
    reason: str | None,
) -> None:
    """Projection retains IDs/enums/time/reason and never retains the image."""
    created_at = datetime(2026, 10, 25, 12, tzinfo=UTC)
    capture = ReviewedCapture(
        sample_image, "Title", created_at, kind, completeness, reason
    )
    provenance = CaptureProvenance.from_capture(capture)
    assert provenance.capture_id == capture.capture_id
    assert provenance.title == capture.title
    assert provenance.created_at is created_at
    assert provenance.source_kind is kind
    assert provenance.completeness is completeness
    assert provenance.stop_reason == reason
    assert "image" not in {field.name for field in fields(provenance)}
    assert CaptureProvenance.from_capture(capture) == provenance
