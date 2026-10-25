"""Accepted capture pixels and provenance, independent of editor/window layout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from PySide6.QtGui import QImage


class CaptureKind(StrEnum):
    """The acquisition mechanism, without inventing a desktop rectangle."""

    SCREENSHOT = "screenshot"
    SCROLLING = "scrolling"


class CaptureCompleteness(StrEnum):
    """Evidence about the selected range, never a whole-document guarantee."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class CaptureProvenance:
    """Small document metadata that retains no image or acquisition resources."""

    capture_id: str
    title: str
    created_at: datetime
    source_kind: str
    completeness: CaptureCompleteness
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class ReviewedCapture:
    """Canonical reviewed-image delivery payload with stable retry identity.

    Producers retain this object or explicitly preserve ``capture_id`` across
    retries. Completeness concerns the requested range, not unseen content.
    Plans, tiles, input leases and editor/window identities never cross here.
    """

    image: QImage
    title: str
    created_at: datetime
    source_kind: str
    completeness: CaptureCompleteness = CaptureCompleteness.COMPLETE
    stop_reason: str | None = None
    capture_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        """Detach producer ownership cheaply using Qt's copy-on-write image."""
        if self.image.isNull():
            raise ValueError("A reviewed capture must contain pixels")
        if self.created_at.utcoffset() is None:
            raise ValueError("Capture timestamps must include a timezone")
        if not self.capture_id.strip() or not self.source_kind.strip():
            raise ValueError("A reviewed capture needs identity and source kind")
        if self.completeness not in set(CaptureCompleteness):
            raise ValueError("Unknown capture completeness")
        object.__setattr__(self, "completeness", CaptureCompleteness(self.completeness))
        object.__setattr__(self, "image", QImage(self.image))

    @property
    def provenance(self) -> CaptureProvenance:
        """Project accepted metadata without copying or retaining image pixels."""
        return CaptureProvenance(
            self.capture_id,
            self.title,
            self.created_at,
            self.source_kind,
            self.completeness,
            self.stop_reason,
        )
