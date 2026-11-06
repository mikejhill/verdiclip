"""Experimental ordinary-window pointer admission, independent of Qt callbacks.

This does not provide atomic exclusion against a foreign process acquiring mouse
capture between a read-only check and Windows delivering a forwarded movement.
It must not be enabled as a general popup-preserving capture replacement.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum, auto

from verdiclip.geometry import Point


class PointerPhase(Enum):
    """One-way lifetime; a revoked generation can never reacquire ownership."""

    WAITING = auto()
    OWNED = auto()
    CLOSED = auto()


class NativePointerRoute:
    """Bind natural cursor admission and movement to one immutable HWND set.

    The caller owns native checks and delivery. Callbacks may reenter close;
    admission and forwarding must therefore revalidate the terminal latch.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._phase = PointerPhase.WAITING
        self._windows: frozenset[int] = frozenset()
        self._point: Point | None = None

    @property
    def phase(self) -> PointerPhase:
        """Return the request-local admission state."""
        with self._lock:
            return self._phase

    @property
    def point(self) -> Point | None:
        """Return the last admitted absolute position, never a raw delta."""
        with self._lock:
            return self._point

    def bind(self, windows: frozenset[int]) -> None:
        """Publish owned handles once, before any natural admission callback."""
        with self._lock:
            if (
                self._phase is not PointerPhase.WAITING
                or self._windows
                or not windows
                or any(type(window) is not int or window <= 0 for window in windows)
            ):
                raise ValueError("Invalid native pointer surface lifetime")
            self._windows = windows

    def admit(self, window: int, safe: Callable[[], bool]) -> bool:
        """Accept only an actual natural owned WM_SETCURSOR observation."""
        with self._lock:
            if self._phase is PointerPhase.CLOSED or window not in self._windows:
                return False
            if not self._prove(safe):
                self.close()
                return False
            if self.phase is PointerPhase.CLOSED:
                return False
            self._phase = PointerPhase.OWNED
            return True

    def move(self, point: Point, safe: Callable[[], bool]) -> bool:
        """Publish absolute hook/readback coordinates only while still admitted."""
        with self._lock:
            if self._phase is not PointerPhase.OWNED:
                return False
            if not self._prove(safe):
                self.close()
                return False
            if self.phase is not PointerPhase.OWNED:
                return False
            self._point = point
            return True

    def _prove(self, safe: Callable[[], bool]) -> bool:
        confirmed = False
        try:
            confirmed = safe() is True
            return confirmed
        finally:
            if not confirmed:
                self.close()

    def close(self) -> None:
        """Revoke before hiding surfaces or draining input; reject stale callbacks."""
        with self._lock:
            self._phase = PointerPhase.CLOSED
