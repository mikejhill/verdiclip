"""Read-only admission for post-capture editor activation, never a focus override."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys

from verdiclip.capture.input_integrity import GuiThreadInfo


def permits_capture_activation() -> bool:
    """Refuse unknown foreground state, native menus, capture and move/size loops."""
    if sys.platform != "win32":
        return True
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetForegroundWindow.argtypes = []
        user.GetForegroundWindow.restype = w.HWND
        user.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
        user.GetGUIThreadInfo.restype = w.BOOL
        foreground = user.GetForegroundWindow()
        info = GuiThreadInfo()
        info.size = ctypes.sizeof(info)
        if not foreground or not user.GetGUIThreadInfo(0, ctypes.byref(info)):
            return False
        # GUI_INMOVESIZE, GUI_INMENUMODE, GUI_SYSTEMMENUMODE, GUI_POPUPMENUMODE.
        return bool(
            info.active == foreground == user.GetForegroundWindow()
            and not (info.flags & 0x1E)
            and not info.capture
            and not info.menu
            and not info.move
        )
    except (OSError, ValueError, ctypes.ArgumentError):
        return False
