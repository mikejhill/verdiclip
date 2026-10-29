"""Pixel-free document metadata projected from the canonical reviewed value."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdiclip.capture.reviewed import (
        CaptureCompleteness,
        CaptureKind,
        ReviewedCapture,
    )


@dataclass(frozen=True, slots=True)
class CaptureProvenance:
    """Small document metadata that does not retain another pixel payload."""

    capture_id: str
    title: str
    created_at: datetime
    source_kind: CaptureKind
    completeness: CaptureCompleteness
    stop_reason: str | None

    @classmethod
    def from_capture(cls, capture: ReviewedCapture) -> CaptureProvenance:
        """Project accepted evidence without rebuilding its identity or image."""
        return cls(
            capture.capture_id,
            capture.title,
            capture.created_at,
            capture.source_kind,
            capture.completeness,
            capture.stop_reason,
        )
