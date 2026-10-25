"""Accepted capture pixels and provenance, independent of editor/window layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from PySide6.QtGui import QImage


class CaptureKind(StrEnum):
    """The acquisition mechanism, without inventing a desktop rectangle."""

    SCREENSHOT = "screenshot"
    SCROLLING = "scrolling"


class CaptureCompleteness(StrEnum):
    """Evidence about the selected range, never a whole-document guarantee."""

    SELECTED_RANGE = "selected_range"
    REPORTED_END = "reported_end"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class ReviewedCapture:
    """A reviewed, size-checked image ready for controller-owned delivery."""

    image: QImage
    title: str
    taken_at: datetime
    source_kind: CaptureKind
    completeness: CaptureCompleteness
    stop_reason: str

    def __post_init__(self) -> None:
        """Detach producer ownership cheaply using Qt's copy-on-write image."""
        if self.image.isNull():
            raise ValueError("A reviewed capture must contain pixels")
        if self.taken_at.utcoffset() is None:
            raise ValueError("Capture timestamps must include a timezone")
        object.__setattr__(self, "image", QImage(self.image))
