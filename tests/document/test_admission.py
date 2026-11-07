"""Ordinary tab provenance retains metadata, never a second image payload."""

from dataclasses import fields
from datetime import UTC, datetime

from PySide6.QtGui import QImage

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.document.admission import CaptureProvenance
from verdiclip.geometry import Rect


def test_ordinary_capture_metadata_and_distinct_tab_identity(
    sample_image: QImage,
) -> None:
    """Each tab gets a distinct ID and preserves only ordinary capture metadata."""
    capture = Capture(
        sample_image,
        CaptureMode.REGION,
        Rect(0, 0, 400, 300),
        "Owned screenshot",
        datetime.now(UTC),
    )
    first = CaptureProvenance.from_capture(capture)
    second = CaptureProvenance.from_capture(capture)
    assert first.title == capture.title
    assert first.created_at is capture.taken_at
    assert len(first.capture_id) == 32
    assert first.capture_id != second.capture_id
    assert {field.name for field in fields(first)} == {
        "capture_id",
        "title",
        "created_at",
    }
