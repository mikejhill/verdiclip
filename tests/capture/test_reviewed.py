"""Canonical accepted-image identity and metadata, independent of editor layout."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from PySide6.QtGui import QImage
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind, ReviewedCapture


class Examples:
    """Valid explicit producer values used by boundary and value-semantics tests."""

    @staticmethod
    def capture(image: QImage | None = None) -> ReviewedCapture:
        """Create one separately identified, complete ordinary screenshot."""
        return ReviewedCapture(
            Documents.image(height=120) if image is None else image,
            "Example",
            datetime(2026, 10, 25, tzinfo=UTC),
            CaptureKind.SCREENSHOT,
            CaptureCompleteness.COMPLETE,
        )


def test_producer_changes_do_not_change_reviewed_pixels() -> None:
    """Qt copy-on-write prevents later producer edits from corrupting delivery."""
    image = Documents.image(height=120)
    original = image.copy()
    result = Examples.capture(image)
    image.fill(0)
    assert result.image == original


def test_consumer_changes_do_not_change_producer_pixels() -> None:
    """Accepted pixel wrappers do not share mutable image identity with producers."""
    image = Documents.image(height=120)
    original = image.copy()
    result = Examples.capture(image)
    result.image.fill(0)
    assert image == original


def test_empty_pixels_cannot_cross_the_delivery_boundary() -> None:
    """An empty review result is an explicit error rather than a blank tab."""
    with pytest.raises(ValueError, match="pixels"):
        replace(Examples.capture(), image=QImage())


def test_timezone_is_required_for_delivery_metadata() -> None:
    """Ambiguous local timestamps must not enter saved-image provenance."""
    naive = datetime(2026, 10, 25, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        replace(Examples.capture(), created_at=naive)


def test_default_capture_identity_is_new_for_each_capture() -> None:
    """Equal image pixels still represent separately identified operations."""
    image = Documents.image(height=120)
    first, second = Examples.capture(image), Examples.capture(image)
    assert first.capture_id
    assert first.capture_id != second.capture_id
    assert first.stop_reason is None


def test_explicit_identity_and_metadata_survive_retry_reconstruction() -> None:
    """Retries preserve exact identity, timestamp, range and stop evidence."""
    first = replace(
        Examples.capture(),
        source_kind=CaptureKind.SCROLLING,
        completeness=CaptureCompleteness.PARTIAL,
        stop_reason="target_changed",
        capture_id="capture-fixture-1",
    )
    retried = replace(first, image=QImage(first.image))
    assert retried.capture_id == first.capture_id
    assert retried.created_at == first.created_at
    assert retried.source_kind is CaptureKind.SCROLLING
    assert retried.completeness is CaptureCompleteness.PARTIAL
    assert retried.stop_reason == "target_changed"


@pytest.mark.parametrize("capture_id", ["", " "])
def test_empty_identity_cannot_enter_delivery(capture_id: str) -> None:
    """Invalid routing identity fails before outputs or editor admission."""
    with pytest.raises(ValueError, match="identity"):
        replace(Examples.capture(), capture_id=capture_id)


@pytest.mark.parametrize("value", ["scrolling", "unknown"])
def test_source_kind_requires_an_actual_enum_member(value: str) -> None:
    """A producer string is not silently repaired into an acquisition kind."""
    invalid = cast(CaptureKind, cast(object, value))
    with pytest.raises(TypeError, match="CaptureKind"):
        replace(Examples.capture(), source_kind=invalid)


@pytest.mark.parametrize("value", ["complete", "partial", "unknown"])
def test_completeness_requires_an_actual_enum_member(value: str) -> None:
    """A string cannot silently mark a result complete."""
    invalid = cast(CaptureCompleteness, cast(object, value))
    with pytest.raises(TypeError, match="completeness"):
        replace(Examples.capture(), completeness=invalid)


@pytest.mark.parametrize("value", ["", " "])
def test_supplied_stop_reason_must_be_nonempty(value: str) -> None:
    """An absent reason is distinct from malformed supplied stop metadata."""
    with pytest.raises(ValueError, match="stop reason"):
        replace(Examples.capture(), stop_reason=value)


def test_completeness_is_required_and_identity_is_keyword_only() -> None:
    """Constructor binding cannot accidentally imply completeness or swap identity."""
    parameters = inspect.signature(ReviewedCapture).parameters
    assert parameters["completeness"].default is inspect.Parameter.empty
    assert parameters["capture_id"].kind is inspect.Parameter.KEYWORD_ONLY
