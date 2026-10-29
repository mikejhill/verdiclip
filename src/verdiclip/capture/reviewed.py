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
class ReviewedCapture:
    """Canonical reviewed-image delivery payload with stable retry identity.

    Producers retain this object or explicitly preserve ``capture_id`` across
    retries. Completeness concerns the requested range, not unseen content.
    Plans, tiles, input leases and editor/window identities never cross here.
    """

    image: QImage
    title: str
    created_at: datetime
    source_kind: CaptureKind
    completeness: CaptureCompleteness
    stop_reason: str | None = None
    capture_id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)

    def __post_init__(self) -> None:
        """Detach producer ownership cheaply using Qt's copy-on-write image."""
        if self.image.isNull():
            raise ValueError("A reviewed capture must contain pixels")
        if self.created_at.utcoffset() is None:
            raise ValueError("Capture timestamps must include a timezone")
        if not self.capture_id.strip():
            raise ValueError("A reviewed capture needs a nonempty capture identity")
        if not isinstance(self.source_kind, CaptureKind):
            raise TypeError("Capture source kind must be an actual CaptureKind member")
        if not isinstance(self.completeness, CaptureCompleteness):
            raise TypeError("Capture completeness must be an actual enum member")
        if self.stop_reason is not None and not self.stop_reason.strip():
            raise ValueError("A supplied stop reason must be nonempty")
        object.__setattr__(self, "image", QImage(self.image))
