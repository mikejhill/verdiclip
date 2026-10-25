"""Bounded, event-driven viewport settling before overlap registration."""

from __future__ import annotations

import math
from enum import StrEnum

from PySide6.QtGui import QImage

from verdiclip.capture.scroll.models import ScrollFrame, ScrollTarget


class SettleStatus(StrEnum):
    """Stable pixels are evidence of settling, never proof of document end."""

    WAITING = "waiting"
    STABLE = "stable"
    STALE = "stale"
    TARGET_CHANGED = "target_changed"
    EXHAUSTED = "exhausted"


class FrameSettler:
    """Accept consecutive identical observations within a finite request budget.

    A caller schedules observations without blocking the GUI. Every step gets a
    fresh request identity, so late results cannot satisfy a later step. This
    gate neither scrolls nor authorizes scrolling; accepted frames still need
    overlap verification and the session's independent resource checks.
    """

    MIN_OBSERVATIONS = 2

    def __init__(
        self,
        *,
        observations: int = 3,
        max_samples: int = 16,
        max_seconds: float = 2.0,
        max_frame_pixels: int = 16_000_000,
    ) -> None:
        """Require repeated evidence and bounded input before allocating copies."""
        if (
            observations < self.MIN_OBSERVATIONS
            or max_samples < observations
            or max_frame_pixels < 1
        ):
            raise ValueError("Settling requires bounded repeated observations")
        if not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("Settling duration must be finite and positive")
        self._required = observations
        self._max_samples = max_samples
        self._max_seconds = max_seconds
        self._max_pixels = max_frame_pixels
        self._request = 0
        self._target: ScrollTarget | None = None
        self._token = 0
        self._started = 0.0
        self._observed = 0.0
        self._sequence = 0
        self._samples = 0
        self._consecutive = 0
        self._previous = QImage()
        self._end = False
        self._ready: ScrollFrame | None = None

    def begin(self, target: ScrollTarget, *, token: int, now: float) -> int:
        """Invalidate the previous request and start one bounded settling step."""
        if token < 1 or not math.isfinite(now):
            raise ValueError("Settling needs a session token and finite time")
        self.cancel()
        self._target, self._token = target, token
        self._started = self._observed = now
        self._sequence = self._samples = self._consecutive = 0
        return self._request

    def cancel(self) -> None:
        """Release retained pixels and reject all pending request results."""
        self._request += 1
        self._target = None
        self._previous = QImage()
        self._ready = None

    def expire(self, request: int, *, now: float) -> SettleStatus:
        """Let the caller's deadline timer terminate even a silent producer."""
        if request != self._request or self._target is None:
            return SettleStatus.STALE
        if not math.isfinite(now) or now < self._observed:
            raise ValueError("Settling deadline needs ordered finite time")
        if now - self._started >= self._max_seconds:
            self.cancel()
            return SettleStatus.EXHAUSTED
        return SettleStatus.WAITING

    def submit(self, request: int, frame: ScrollFrame) -> SettleStatus:
        """Keep only bounded canonical pixels and consecutive ordered evidence."""
        target = self._target
        if (
            request != self._request
            or target is None
            or frame.token != self._token
            or frame.sequence <= self._sequence
            or frame.observed_at <= self._observed
            or self._ready is not None
        ):
            return SettleStatus.STALE
        if self.expire(request, now=frame.observed_at) is SettleStatus.EXHAUSTED:
            return SettleStatus.EXHAUSTED
        if (
            frame.target_identity != target.identity
            or frame.geometry_epoch != target.geometry_epoch
            or frame.image.isNull()
            or frame.image.width() != target.viewport.width
            or frame.image.height() != target.viewport.height
        ):
            self.cancel()
            return SettleStatus.TARGET_CHANGED
        if frame.image.width() * frame.image.height() > self._max_pixels:
            self.cancel()
            return SettleStatus.EXHAUSTED
        return self._observe(frame)

    def _observe(self, frame: ScrollFrame) -> SettleStatus:
        """Reset on animation or semantic movement; do not infer missing pixels."""
        current = frame.image.convertToFormat(QImage.Format.Format_ARGB32)
        if current.isNull():
            self.cancel()
            return SettleStatus.EXHAUSTED
        same = current == self._previous and frame.at_end == self._end
        self._consecutive = self._consecutive + 1 if same else 1
        self._previous, self._end = current, frame.at_end
        self._sequence, self._observed = frame.sequence, frame.observed_at
        self._samples += 1
        if self._consecutive >= self._required:
            self._ready = ScrollFrame(
                frame.token,
                frame.sequence,
                frame.target_identity,
                frame.geometry_epoch,
                frame.observed_at,
                current,
                frame.at_end,
            )
            return SettleStatus.STABLE
        if self._samples >= self._max_samples:
            self.cancel()
            return SettleStatus.EXHAUSTED
        return SettleStatus.WAITING

    def take(self, request: int) -> ScrollFrame | None:
        """Transfer one stable observation exactly once, then invalidate work."""
        if request != self._request or self._ready is None:
            return None
        ready = self._ready
        self.cancel()
        return ready
