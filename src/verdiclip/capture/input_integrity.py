"""Read-only, fail-closed integrity checks for temporary capture input ownership."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from typing import Final

MAX_SID_AUTHORITIES: Final = 15


class GuiThreadInfo(ctypes.Structure):
    """GUITHREADINFO includes focus, capture and menu recipients without activation."""

    _fields_ = (
        ("size", w.DWORD),
        ("flags", w.DWORD),
        ("active", w.HWND),
        ("focus", w.HWND),
        ("capture", w.HWND),
        ("menu", w.HWND),
        ("move", w.HWND),
        ("caret", w.HWND),
        ("caret_rect", w.RECT),
    )


class MandatoryLabel(ctypes.Structure):
    """TOKEN_MANDATORY_LABEL contains SID_AND_ATTRIBUTES."""

    _fields_ = (("sid", w.LPVOID), ("attributes", w.DWORD))


class InputIntegrityGuard:
    """Never elevate or change tokens; unknown/protected recipients are unsupported."""

    def __init__(self) -> None:
        """Bind only documented read APIs; construction performs no token reads."""
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._security = ctypes.WinDLL("advapi32", use_last_error=True)
        self._user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self._user.GetWindowThreadProcessId.restype = w.DWORD
        self._user.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
        self._user.GetGUIThreadInfo.restype = w.BOOL
        self._user.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        self._user.GetPhysicalCursorPos.restype = w.BOOL
        self._user.WindowFromPhysicalPoint.argtypes = [w.POINT]
        self._user.WindowFromPhysicalPoint.restype = w.HWND
        self._user.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
        self._user.GetWindowLongW.restype = w.LONG
        self._kernel.GetCurrentProcess.restype = w.HANDLE
        self._kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self._kernel.OpenProcess.restype = w.HANDLE
        self._kernel.CloseHandle.argtypes = [w.HANDLE]
        self._kernel.CloseHandle.restype = w.BOOL
        self._security.OpenProcessToken.argtypes = [
            w.HANDLE,
            w.DWORD,
            ctypes.POINTER(w.HANDLE),
        ]
        self._security.OpenProcessToken.restype = w.BOOL
        self._security.GetTokenInformation.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        ]
        self._security.GetTokenInformation.restype = w.BOOL
        self._security.IsValidSid.argtypes = [w.LPVOID]
        self._security.IsValidSid.restype = w.BOOL
        self._security.GetSidSubAuthorityCount.argtypes = [w.LPVOID]
        self._security.GetSidSubAuthorityCount.restype = ctypes.POINTER(w.BYTE)
        self._security.GetSidSubAuthority.argtypes = [w.LPVOID, w.DWORD]
        self._security.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)

    def permits(self, foreground: int) -> bool:
        """Re-read all current GUI recipients and reject unknown or higher integrity."""
        if not foreground:
            return False
        info = GuiThreadInfo()
        info.size = ctypes.sizeof(info)
        if not self._user.GetGUIThreadInfo(0, ctypes.byref(info)):
            return False
        pointer = self._pointer_recipient(info)
        own = self._level(self._kernel.GetCurrentProcess())
        if own is None or not pointer:
            return False
        recipients = {
            foreground,
            int(info.active or 0),
            int(info.focus or 0),
            int(info.capture or 0),
            int(info.menu or 0),
            pointer,
        } - {0}
        processes = self._processes(recipients)
        if processes is None:
            return False
        return all(self._permitted_process(process_id, own) for process_id in processes)

    def _pointer_recipient(self, info: GuiThreadInfo) -> int:
        if info.capture:
            return int(info.capture)
        point = w.POINT()
        if not self._user.GetPhysicalCursorPos(ctypes.byref(point)):
            return 0
        window = int(self._user.WindowFromPhysicalPoint(point) or 0)
        process = w.DWORD()
        if not window or not self._user.GetWindowThreadProcessId(
            window, ctypes.byref(process)
        ):
            return 0
        ctypes.set_last_error(0)
        style = int(self._user.GetWindowLongW(window, -20))
        if not style and ctypes.get_last_error():
            return 0
        if style & 0x20:
            # Do not mistake an input-transparent window for the real recipient.
            # Unknown underlying ownership is unsupported, never permission.
            return 0
        return window

    def _processes(self, recipients: set[int]) -> set[int] | None:
        processes: set[int] = set()
        for window in recipients:
            process_id = w.DWORD()
            if not self._user.GetWindowThreadProcessId(
                window, ctypes.byref(process_id)
            ):
                return None
            if not process_id.value:
                return None
            processes.add(process_id.value)
        return processes

    def _permitted_process(self, process_id: int, own: int) -> bool:
        process = self._kernel.OpenProcess(0x1000, 0, process_id)
        if not process:
            return False
        try:
            level = self._level(process)
        finally:
            closed = bool(self._kernel.CloseHandle(process))
        return closed and level is not None and level <= own

    def _level(self, process: int) -> int | None:
        token = w.HANDLE()
        if not self._security.OpenProcessToken(process, 8, ctypes.byref(token)):
            return None
        try:
            level = self._token_level(token)
        finally:
            closed = bool(self._kernel.CloseHandle(token))
        return level if closed else None

    def _token_level(self, token: w.HANDLE) -> int | None:
        # A mandatory-label SID is bounded by SECURITY_MAX_SID_SIZE (68 bytes).
        buffer = ctypes.create_string_buffer(ctypes.sizeof(MandatoryLabel) + 68)
        needed = w.DWORD()
        if not self._security.GetTokenInformation(
            token, 25, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
        ):
            return None
        if not ctypes.sizeof(MandatoryLabel) <= needed.value <= ctypes.sizeof(buffer):
            return None
        label = MandatoryLabel.from_buffer(buffer)
        if not label.sid or not self._security.IsValidSid(label.sid):
            return None
        count = self._security.GetSidSubAuthorityCount(label.sid)
        if not count or not 1 <= count.contents.value <= MAX_SID_AUTHORITIES:
            return None
        authority = self._security.GetSidSubAuthority(
            label.sid, count.contents.value - 1
        )
        return int(authority.contents.value) if authority else None
