"""A real Win32 TrackPopupMenu in the same disposable fixture child process."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os


class OwnedNativeMenu:
    """Own a text HMENU and expose only its verified same-process popup HWND."""

    def __init__(self, owner: int) -> None:
        self.owner = owner
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.CreatePopupMenu.argtypes = ()
        self.api.CreatePopupMenu.restype = w.HMENU
        self.api.AppendMenuW.argtypes = (w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
        self.api.AppendMenuW.restype = w.BOOL
        self.api.TrackPopupMenu.argtypes = (
            w.HMENU,
            w.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.HWND,
            ctypes.c_void_p,
        )
        self.api.TrackPopupMenu.restype = w.BOOL
        self.api.GetMenuItemRect.argtypes = (
            w.HWND,
            w.HMENU,
            w.UINT,
            ctypes.POINTER(w.RECT),
        )
        self.api.GetMenuItemRect.restype = w.BOOL
        self.api.FindWindowExW.argtypes = (w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
        self.api.FindWindowExW.restype = w.HWND
        self.api.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.IsWindowVisible.argtypes = (w.HWND,)
        self.api.IsWindowVisible.restype = w.BOOL
        self.api.DestroyMenu.argtypes = (w.HMENU,)
        self.api.DestroyMenu.restype = w.BOOL
        self.handle = self.api.CreatePopupMenu()
        if not self.handle:
            raise RuntimeError("CreatePopupMenu failed")
        self.window = 0
        self.running = False
        self.patch: list[int] = []
        for identifier, label in enumerate(
            (
                "OWNED NATIVE MENU 314159",
                "Synthetic fixture only",
                "No foreign code injection",
            ),
            1,
        ):
            if not self.api.AppendMenuW(self.handle, 0, identifier, label):
                self.close()
                raise RuntimeError("AppendMenuW failed")

    def show(self, x: int, y: int) -> None:
        """Enter the real native menu loop, not a fabricated or posted callback."""
        self.running = True
        try:
            # TPM_RETURNCMD: no command is sent to any application.
            self.api.TrackPopupMenu(self.handle, 0x0100, x, y, 0, self.owner, None)
        finally:
            self.running = False

    def state(self) -> tuple[int, bool, list[int]]:
        """Read only #32768 menus and accept only this fixture process's HWND."""
        if self.running and not self.window:
            candidate = self.api.FindWindowExW(None, None, "#32768", None)
            while candidate:
                pid = w.DWORD()
                self.api.GetWindowThreadProcessId(candidate, ctypes.byref(pid))
                if pid.value == os.getpid():
                    self.window = int(candidate)
                    break
                candidate = self.api.FindWindowExW(None, candidate, "#32768", None)
        visible = bool(self.window and self.api.IsWindowVisible(self.window))
        area = w.RECT()
        if visible and self.api.GetMenuItemRect(
            self.owner, self.handle, 0, ctypes.byref(area)
        ):
            self.patch = [
                area.left + 4,
                area.top + 2,
                area.right - area.left - 8,
                area.bottom - area.top - 4,
            ]
        return self.window, visible, self.patch

    def close(self) -> None:
        """End this thread's menu loop and release only the HMENU created here."""
        if self.running:
            self.api.EndMenu()
        if self.handle:
            if not self.api.DestroyMenu(self.handle):
                raise RuntimeError("Owned DestroyMenu failed")
            self.handle = None
