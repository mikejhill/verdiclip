"""Supported Win32 primitives for a finite, nonactivating capture input lease."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from collections.abc import Callable
from typing import Final, Protocol

from verdiclip.capture.input_integrity import InputIntegrityGuard
from verdiclip.capture.input_raw import (
    WM_RAW_INPUT,
    RawPointerCallback,
    RawPointerSource,
)
from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point

HookCallback = Callable[[int, int, int], int]
INVALID_HOOK_HANDLE: Final = 1404


class MousePacket(ctypes.Structure):
    """MSLLHOOKSTRUCT uses physical per-monitor-aware desktop coordinates."""

    _fields_ = (
        ("point", w.POINT),
        ("data", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    )


class KeyboardPacket(ctypes.Structure):
    """Only virtual-key metadata is inspected; text is never decoded or recorded."""

    _fields_ = (
        ("code", w.DWORD),
        ("scan", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    )


class InputPlatform(Protocol):
    """Injectable OS boundary; deterministic drivers need no desktop or hooks."""

    def foreground(self) -> int:
        """Read the foreground owner without changing it."""

    def cursor(self) -> Point:
        """Read the physical OS cursor anchor without moving it."""

    def desktop_safe(self) -> bool:
        """Allow only the ordinary interactive desktop, never secure desktop."""

    def recipient_safe(self, foreground: int, /) -> bool:
        """Reject unknown or higher-integrity input recipients without elevation."""

    def buttons_held(self) -> bool:
        """Reject acquisition in the middle of an existing OS button pair."""

    def control_held(self) -> bool:
        """Read a modifier for virtual arrow-key movement."""

    def shift_held(self) -> bool:
        """Read Shift for backwards navigation inside owned capture controls."""

    def keys_held(self) -> frozenset[int]:
        """Snapshot pre-existing key ownership; never decode or record text."""

    def install(
        self, mouse: HookCallback, keyboard: HookCallback, raw: RawPointerCallback
    ) -> None:
        """Install callbacks on the calling dedicated message-pump thread."""

    def pump(self) -> bool:
        """Wait for one message; a finite timer must wake an otherwise idle pump."""

    def forward(self, code: int, message: int, address: int, /) -> int:
        """Preserve input outside explicit ownership."""

    def close(self) -> bool:
        """Remove only hooks/timer owned by this instance and report cleanup."""


class Win32InputPlatform:
    """No activation, capture grabs, existing-window destruction or security changes."""

    def __init__(self) -> None:
        """Load APIs on the thread that will own and retire their callbacks."""
        if sys.platform != "win32":
            raise PlatformError("Capture input preservation requires Windows")
        self._api = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM
        )
        self._api.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            self._callback_type,
            w.HINSTANCE,
            w.DWORD,
        ]
        self._api.SetWindowsHookExW.restype = w.HHOOK
        self._api.UnhookWindowsHookEx.argtypes = [w.HHOOK]
        self._api.UnhookWindowsHookEx.restype = w.BOOL
        self._api.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
        self._api.CallNextHookEx.restype = ctypes.c_ssize_t
        self._api.GetForegroundWindow.restype = w.HWND
        self._api.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        self._api.GetPhysicalCursorPos.restype = w.BOOL
        self._api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._api.GetAsyncKeyState.restype = ctypes.c_short
        self._api.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self._api.OpenInputDesktop.restype = w.HANDLE
        self._api.GetUserObjectInformationW.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        ]
        self._api.GetUserObjectInformationW.restype = w.BOOL
        self._api.CloseDesktop.argtypes = [w.HANDLE]
        self._api.CloseDesktop.restype = w.BOOL
        self._api.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
        self._api.SetTimer.restype = ctypes.c_size_t
        self._api.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
        self._api.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        self._api.GetMessageW.restype = ctypes.c_int
        self._api.PeekMessageW.argtypes = [
            ctypes.POINTER(w.MSG),
            w.HWND,
            w.UINT,
            w.UINT,
            w.UINT,
        ]
        self._api.PeekMessageW.restype = w.BOOL
        self._api.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        self._api.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        self._api.DispatchMessageW.restype = ctypes.c_ssize_t
        self._api.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self._api.DefWindowProcW.restype = ctypes.c_ssize_t
        kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel.GetModuleHandleW.restype = w.HMODULE
        self._module = kernel.GetModuleHandleW(None)
        self._raw = RawPointerSource(self._api, int(self._module or 0))
        self._integrity = InputIntegrityGuard()
        self._hooks: list[int] = []
        self._callbacks: list[object] = []
        self._timer = 0

    def foreground(self) -> int:
        """Read without SetForegroundWindow or thread-input attachment."""
        return int(self._api.GetForegroundWindow() or 0)

    def cursor(self) -> Point:
        """Read physical position without changing OS cursor or clipping."""
        point = w.POINT()
        if not self._api.GetPhysicalCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Point(point.x, point.y)

    def desktop_safe(self) -> bool:
        """Read the input desktop's name and immediately release the read handle."""
        desktop = self._api.OpenInputDesktop(0, 0, 1)
        if not desktop:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = w.DWORD()
            found = bool(
                self._api.GetUserObjectInformationW(
                    desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
                )
            )
            return found and str(name.value) == "Default"
        finally:
            self._api.CloseDesktop(desktop)

    def buttons_held(self) -> bool:
        """An existing press belongs to its original recipient."""
        return any(
            self._api.GetAsyncKeyState(code) & 0x8000 for code in (1, 2, 4, 5, 6)
        )

    def recipient_safe(self, foreground: int, /) -> bool:
        """Successful hook installation alone does not establish UIPI compatibility."""
        return self._integrity.permits(foreground)

    def control_held(self) -> bool:
        """Support keyboard selection without moving the real cursor."""
        return bool(self._api.GetAsyncKeyState(0x11) & 0x8000)

    def shift_held(self) -> bool:
        """Read a modifier without altering focus or keyboard ownership."""
        return bool(self._api.GetAsyncKeyState(0x10) & 0x8000)

    def keys_held(self) -> frozenset[int]:
        """Bounded ephemeral key-state snapshot for matched release ownership."""
        return frozenset(
            code for code in range(8, 256) if self._api.GetAsyncKeyState(code) & 0x8000
        )

    def install(
        self, mouse: HookCallback, keyboard: HookCallback, raw: RawPointerCallback
    ) -> None:
        """Create finite idle wakeups, then retain both callable pointers."""
        self._raw.start(raw)
        self._raw.discard_pending()
        self._timer = int(self._api.SetTimer(None, 0, 25, None))
        if not self._timer:
            raise ctypes.WinError(ctypes.get_last_error())
        for kind, callback in ((14, mouse), (13, keyboard)):
            procedure = self._callback_type(callback)
            self._callbacks.append(procedure)
            handle = self._api.SetWindowsHookExW(kind, procedure, self._module, 0)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hooks.append(int(handle))

    def pump(self) -> bool:
        """Use the documented installer-thread hook/message-loop relationship."""
        message = w.MSG()
        status = int(self._api.GetMessageW(ctypes.byref(message), None, 0, 0))
        if status < 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if status:
            if message.message == WM_RAW_INPUT and self._raw.owns_window(
                int(message.hWnd or 0)
            ):
                try:
                    self._raw.receive(int(message.lParam))
                finally:
                    self._api.DefWindowProcW(
                        message.hWnd, message.message, message.wParam, message.lParam
                    )
            else:
                self._api.TranslateMessage(ctypes.byref(message))
                self._api.DispatchMessageW(ctypes.byref(message))
        return status > 0

    def forward(self, code: int, message: int, address: int) -> int:
        """Pass negative hook codes and unowned user input unchanged."""
        return int(self._api.CallNextHookEx(None, code, message, address))

    def close(self) -> bool:
        """Retire on the installer thread after all of its callbacks returned."""
        failed = [
            handle
            for handle in self._hooks
            if (
                not self._api.UnhookWindowsHookEx(handle)
                and ctypes.get_last_error() != INVALID_HOOK_HANDLE
            )
        ]
        self._hooks = failed
        if self._timer:
            self._api.KillTimer(None, self._timer)
            self._timer = 0
        if not failed:
            self._callbacks.clear()
        raw_cleaned = self._raw.close()
        return not failed and raw_cleaned
