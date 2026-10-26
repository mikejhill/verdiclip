"""Deterministic pointer tracking for an explicitly owned capture input lease.

No OS calls or input forwarding happen here. A native adapter must qualify its
movement interpretation before using this model; unknown movement is unsafe.
Button packets deliberately cannot reposition the virtual pointer: intercepted
Windows input can report the frozen OS cursor on both press and release.
"""

from __future__ import annotations

import math
from enum import Enum, auto

from verdiclip.geometry import Point, Rect


class MoveMode(Enum):
    """Interpretation explicitly established by the native input adapter."""

    ABSOLUTE = auto()
    OS_RELATIVE = auto()


class PointerTracker:
    """Track one bounded selection without depending on physical display hardware.

    Coordinates are physical desktop coordinates, including negative origins.
    DPI conversion belongs to the existing display mapping after selection.
    OS_RELATIVE consumes an OS-accelerated proposed point relative to the
    actual frozen cursor anchor; it is not a claim to support raw device deltas.
    """

    def __init__(self, initial: Point, bounds: Rect) -> None:
        """Require finite nonempty desktop bounds and an initial point inside."""
        values = (initial.x, initial.y, bounds.x, bounds.y, bounds.width, bounds.height)
        if not all(math.isfinite(value) for value in values):
            msg = "Pointer coordinates must be finite"
            raise ValueError(msg)
        if bounds.width <= 0 or bounds.height <= 0:
            msg = "Pointer bounds must be nonempty"
            raise ValueError(msg)
        self._bounds = bounds
        self._point = self._clamp(initial)
        self._origin: Point | None = None
        self._finished = False

    @property
    def point(self) -> Point:
        """Return the tracked endpoint, never a button packet's frozen point."""
        return self._point

    @property
    def finished(self) -> bool:
        """A terminal release or cancellation cannot restart the same lease."""
        return self._finished

    def move(self, proposed: Point, *, mode: MoveMode, anchor: Point) -> bool:
        """Consume a qualified move; invalid coordinates cancel safely."""
        if self._finished:
            return False
        values = (proposed.x, proposed.y, anchor.x, anchor.y)
        if not all(map(math.isfinite, values)):
            self.cancel()
            return False
        candidate = proposed
        if mode is MoveMode.OS_RELATIVE:
            candidate = self._point + (proposed - anchor)
        self._point = self._clamp(candidate)
        return True

    def press(self) -> bool:
        """Start once at the most recent move; never consume button coordinates."""
        if self._finished or self._origin is not None:
            return False
        self._origin = self._point
        return True

    def release(self) -> Rect | None:
        """Finish a matched pair once, using the tracked movement endpoint."""
        if self._finished or self._origin is None:
            return None
        self._finished = True
        return Rect.from_points(self._origin, self._point)

    def cancel(self) -> None:
        """Invalidate pending selection after Escape, interference or lease loss."""
        self._finished = True
        self._origin = None

    def _clamp(self, point: Point) -> Point:
        return Point(
            min(max(point.x, self._bounds.left), self._bounds.right),
            min(max(point.y, self._bounds.top), self._bounds.bottom),
        )
