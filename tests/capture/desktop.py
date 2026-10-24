"""Read-only prerequisites for the real Windows desktop integration test."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from dataclasses import dataclass


class SessionDetails(ctypes.Structure):
    """The documented WTSINFOEX_LEVEL1_W layout on supported Windows versions."""

    _fields_ = (
        ("session_id", ctypes.wintypes.ULONG),
        ("state", ctypes.c_int),
        ("flags", ctypes.wintypes.LONG),
        ("station", ctypes.wintypes.WCHAR * 33),
        ("user", ctypes.wintypes.WCHAR * 21),
        ("domain", ctypes.wintypes.WCHAR * 18),
        ("times", ctypes.c_longlong * 5),
        ("counts", ctypes.wintypes.DWORD * 6),
    )


class SessionData(ctypes.Union):
    """The level-one payload of WTSINFOEXW."""

    _fields_ = (("details", SessionDetails),)


class SessionInfo(ctypes.Structure):
    """The WTSINFOEXW header and aligned union."""

    _fields_ = (("level", ctypes.wintypes.DWORD), ("data", SessionData))


@dataclass(frozen=True, slots=True)
class DesktopSession:
    """Only positively known lock/disconnection states justify a test skip."""

    state: int
    flags: int

    def unavailable_reason(self) -> str | None:
        """Explain a known unsupported desktop; unknown states still run capture."""
        if self.state == 4:  # WTSDisconnected
            return "Windows session is disconnected; reconnect to its desktop"
        if self.state == 0 and self.flags == 0:  # WTSActive, WTS_SESSIONSTATE_LOCK
            return "Windows session is locked; unlock its interactive desktop"
        return None

    @classmethod
    def current(cls) -> DesktopSession | None:
        """Read WTSSessionInfoEx; a failed/unknown query never suppresses capture."""
        api = ctypes.WinDLL("wtsapi32", use_last_error=True)
        query = api.WTSQuerySessionInformationW
        query.argtypes = (
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.wintypes.DWORD),
        )
        query.restype = ctypes.wintypes.BOOL
        api.WTSFreeMemory.argtypes = (ctypes.c_void_p,)
        api.WTSFreeMemory.restype = None
        buffer = ctypes.c_void_p()
        size = ctypes.wintypes.DWORD()
        # WTS_CURRENT_SESSION, WTSSessionInfoEx; no desktop/session changes.
        if not query(None, 0xFFFFFFFF, 25, ctypes.byref(buffer), ctypes.byref(size)):
            return None
        try:
            if size.value < ctypes.sizeof(SessionInfo):
                return None
            info = ctypes.cast(buffer, ctypes.POINTER(SessionInfo)).contents
            if info.level != 1:
                return None
            details = info.data.details
            return cls(int(details.state), int(details.flags))
        finally:
            api.WTSFreeMemory(buffer)
