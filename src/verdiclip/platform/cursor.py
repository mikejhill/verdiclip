"""Unqualified, session-scoped Magnification cursor suppression candidate.

This global API has no state getter or ownership token. CURSOR_SHOWING does not
prove its current state, and restoration can override another client's hide.
Explicit teardown covers ordinary retirement, not abrupt process termination.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging

from verdiclip.exceptions import PlatformError

logger = logging.getLogger(__name__)


class CursorInfo(ctypes.Structure):
    """Documented CURSORINFO layout; flags are eligibility, not presentation proof."""

    _fields_ = (
        ("size", w.DWORD),
        ("flags", w.DWORD),
        ("cursor", w.HANDLE),
        ("point", w.POINT),
    )


class CursorVisibilityLease:
    """Attempt one hide and explicitly show again on the same native owner thread.

    No cursor shape, display counter, input ownership or saved preference changes.
    A clean receipt confirms API teardown only, never prior-state restoration or
    compositor presentation. This candidate is not qualified for release.
    """

    def __init__(self) -> None:
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._user.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
        self._user.GetCursorInfo.restype = w.BOOL
        self._magnification: ctypes.CDLL | None = None
        self._started = self._closed = self._cleaned = False

    def acquire(self) -> None:
        """Skip hidden/suppressed flags without asserting the global API's state."""
        if self._started or self._closed:
            raise PlatformError("Capture cursor lease cannot be reused")
        self._started = True
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        if not self._user.GetCursorInfo(ctypes.byref(info)):
            raise PlatformError("Could not read capture cursor eligibility")
        if info.flags != 1:  # CURSOR_SHOWING only; never force a hidden baseline.
            return
        api = ctypes.WinDLL("Magnification", use_last_error=True)
        api.MagInitialize.argtypes = []
        api.MagInitialize.restype = w.BOOL
        api.MagShowSystemCursor.argtypes = [w.BOOL]
        api.MagShowSystemCursor.restype = w.BOOL
        api.MagUninitialize.argtypes = []
        api.MagUninitialize.restype = w.BOOL
        if not api.MagInitialize():
            raise PlatformError("Could not initialize capture cursor suppression")
        # Publishing this initialized API records an obligation to show again,
        # including if the following hide reports failure or raises.
        self._magnification = api
        if not api.MagShowSystemCursor(0):
            raise PlatformError("Could not suppress the capture cursor")

    @property
    def suppression_attempted(self) -> bool:
        """Report the actual API path, not logical visibility or rendered pixels."""
        return self._magnification is not None

    def close(self) -> bool:
        """Attempt explicit restoration and uninitialization once, even on error."""
        if self._closed:
            return self._cleaned
        self._closed = True
        api = self._magnification
        if api is None:
            self._cleaned = True
            return True
        restored = False
        try:
            restored = bool(api.MagShowSystemCursor(1))
        finally:
            uninitialized = bool(api.MagUninitialize())
        self._cleaned = restored and uninitialized
        if not self._cleaned:
            logger.error("Capture cursor API teardown could not be confirmed")
        return self._cleaned
