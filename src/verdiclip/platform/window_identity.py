"""Read a window's native process generation without activating or modifying it."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from typing import Final

from verdiclip.exceptions import PlatformError

FILETIME_TO_DATETIME_TICKS: Final = 504_911_232_000_000_000


class FileTime(ctypes.Structure):
    """Fixed Win32 FILETIME layout, independent of the replay host's long width."""

    _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))


class Win32WindowIdentity:
    """Read-only PID/start-time snapshot; unknown identity disables auto scrolling."""

    def __init__(self) -> None:
        """Bind limited-query APIs; do not request elevation or open a process yet."""
        if sys.platform != "win32":
            raise PlatformError("Window generation discovery requires Windows")
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self._user.GetWindowThreadProcessId.restype = w.DWORD
        self._kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self._kernel.OpenProcess.restype = w.HANDLE
        self._kernel.GetProcessTimes.argtypes = [
            w.HANDLE,
            *([ctypes.POINTER(FileTime)] * 4),
        ]
        self._kernel.GetProcessTimes.restype = w.BOOL
        self._kernel.CloseHandle.argtypes = [w.HANDLE]
        self._kernel.CloseHandle.restype = w.BOOL

    def read(self, window: int) -> str | None:
        """Bracket a limited process-time read with unchanged HWND/PID ownership."""
        process_id = self._pid(window)
        if not process_id:
            return None
        process = self._kernel.OpenProcess(0x1000, 0, process_id)
        if not process:
            return None
        created, exited, kernel, user = (FileTime() for _index in range(4))
        try:
            found = bool(
                self._kernel.GetProcessTimes(
                    process,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
            )
        finally:
            closed = bool(self._kernel.CloseHandle(process))
        if not found or not closed or self._pid(window) != process_id:
            return None
        file_ticks = (int(created.high) << 32) | int(created.low)
        if not file_ticks:
            return None
        return f"{window}:{process_id}:{file_ticks + FILETIME_TO_DATETIME_TICKS}"

    def _pid(self, window: int) -> int:
        if window < 1:
            return 0
        process = w.DWORD()
        if not self._user.GetWindowThreadProcessId(window, ctypes.byref(process)):
            return 0
        return int(process.value)
