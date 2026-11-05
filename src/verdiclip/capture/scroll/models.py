"""Explicit target, frame, resource and completion contracts for scrolling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtGui import QImage

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class ScrollLimitError(CaptureError):
    """More capture pixels would exceed an explicit resource budget."""


class StopReason(StrEnum):
    """Why acquisition stopped; none implies unseen content was recovered."""

    USER_STOP = "stopped_by_user"
    NO_PROGRESS = "no_progress_detected"
    UNCERTAIN = "overlap_uncertain"
    REPORTED_END = "reached_reported_end"
    TARGET_CHANGED = "target_changed"
    LIMIT = "resource_limit"
    ERROR = "acquisition_error"


@dataclass(frozen=True, slots=True)
class ScrollLimits:
    """Independent limits checked before acquiring or committing more pixels."""

    max_seconds: float = 120.0
    max_frames: int = 100
    max_pixels: int = 150_000_000
    max_disk_bytes: int = 2 * 1024**3
    max_frame_pixels: int = 16_000_000
    max_flatten_pixels: int = 8_000_000
    max_dimension: int = 20_000
    max_materialize_bytes: int = 256 * 1024**2

    def __post_init__(self) -> None:
        """Disallow unbounded, nonfinite or nonsensical resource settings."""
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("Capture duration must be finite and positive")
        if (
            min(
                self.max_frames,
                self.max_pixels,
                self.max_disk_bytes,
                self.max_frame_pixels,
                self.max_flatten_pixels,
                self.max_dimension,
                self.max_materialize_bytes,
            )
            <= 0
        ):
            raise ValueError("Capture resource limits must be positive")


@dataclass(frozen=True, slots=True)
class ScrollTarget:
    """One verified container and its fixed physical viewport/geometry epoch."""

    identity: str
    title: str
    viewport: Rect
    geometry_epoch: int = 0

    def __post_init__(self) -> None:
        """Reject invalid container identity or nonintegral physical geometry."""
        values = (
            self.viewport.x,
            self.viewport.y,
            self.viewport.width,
            self.viewport.height,
        )
        if not self.identity or self.viewport.is_empty or self.geometry_epoch < 0:
            raise ValueError("A scrolling target needs a valid identity and viewport")
        if any(not math.isfinite(v) or int(v) != v for v in values):
            raise ValueError(
                "Scrolling viewports use finite physical pixel coordinates"
            )


@dataclass(frozen=True, slots=True)
class ScrollFrame:
    """One immutable observation identity; producers relinquish pixel mutation."""

    token: int
    sequence: int
    target_identity: str
    geometry_epoch: int
    observed_at: float
    image: QImage
    at_end: bool = False

    def __post_init__(self) -> None:
        """Validate ordering metadata without making another full pixel copy."""
        if self.token < 1 or self.sequence < 1 or not math.isfinite(self.observed_at):
            raise ValueError("A frame needs finite time and positive session/sequence")
        object.__setattr__(self, "image", QImage(self.image))


@dataclass(frozen=True, slots=True)
class ScrollResult:
    """Extent and stop evidence for the session-owned committed tile manifest."""

    width: int
    height: int
    accepted_frames: int
    stop_reason: StopReason
