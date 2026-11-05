"""Test-owned external TrackPopupMenu peer. Default invocation is plan-only."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import sys
import threading
from collections.abc import Callable


class GuiThreadInfo(ctypes.Structure):
    """Documented GUITHREADINFO, including pointer-sized owner handles."""

    _fields_ = (
        ("size", w.DWORD),
        ("flags", w.DWORD),
        ("active", w.HWND),
        ("focus", w.HWND),
        ("capture", w.HWND),
        ("menu_owner", w.HWND),
        ("move_size", w.HWND),
        ("caret", w.HWND),
        ("caret_rect", w.RECT),
    )


def _apis() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Bind pointer widths once before creating a test-owned GUI."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api.SetProcessDpiAwarenessContext.argtypes = (w.HANDLE,)
    api.SetProcessDpiAwarenessContext.restype = w.BOOL
    if not api.SetProcessDpiAwarenessContext(w.HANDLE(-4)):
        raise OSError("Could not establish fixture physical coordinates")
    api.CreateWindowExW.argtypes = (
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        w.LPVOID,
    )
    api.CreateWindowExW.restype = w.HWND
    api.CreatePopupMenu.restype = w.HMENU
    api.AppendMenuW.argtypes = (w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
    api.AppendMenuW.restype = w.BOOL
    api.TrackPopupMenuEx.argtypes = (
        w.HMENU,
        w.UINT,
        ctypes.c_int,
        ctypes.c_int,
        w.HWND,
        w.LPVOID,
    )
    api.TrackPopupMenuEx.restype = w.BOOL
    api.GetGUIThreadInfo.argtypes = (w.DWORD, ctypes.POINTER(GuiThreadInfo))
    api.GetGUIThreadInfo.restype = w.BOOL
    api.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
    api.GetWindowRect.restype = w.BOOL
    api.IsWindowVisible.argtypes = (w.HWND,)
    api.IsWindowVisible.restype = w.BOOL
    api.IsWindow.argtypes = (w.HWND,)
    api.IsWindow.restype = w.BOOL
    api.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    api.PostMessageW.restype = w.BOOL
    api.SetForegroundWindow.argtypes = (w.HWND,)
    api.SetForegroundWindow.restype = w.BOOL
    api.GetForegroundWindow.restype = w.HWND
    api.DestroyMenu.argtypes = (w.HMENU,)
    api.DestroyWindow.argtypes = (w.HWND,)
    api.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
    api.TranslateMessage.argtypes = (ctypes.POINTER(w.MSG),)
    api.DispatchMessageW.argtypes = (ctypes.POINTER(w.MSG),)
    api.DispatchMessageW.restype = w.LPARAM
    kernel.GetCurrentThreadId.restype = w.DWORD
    return api, kernel


def _commands(report: Callable[[int], None], close_owned: Callable[[], None]) -> None:
    for line in sys.stdin:
        if line.strip() == "close-owned":
            close_owned()
            return
        try:
            command = json.loads(line)
            sequence = command["sequence"]
            if (
                command["op"] != "snapshot"
                or type(sequence) is not int
                or not 0 < sequence < 2**64
            ):
                close_owned()
                return
            report(sequence)
        except (ValueError, TypeError, KeyError):
            close_owned()
            return
    close_owned()


def execute(nonce: str) -> int:
    """Create only this process's owner/menu after an external test handoff."""
    if sys.platform != "win32":
        raise ValueError("External menu fixture requires Windows")
    api, kernel = _apis()
    owner = api.CreateWindowExW(
        0,
        "STATIC",
        "VerdiClip owned native-menu fixture",
        0x10CF0000,
        0,
        0,
        640,
        480,
        None,
        None,
        None,
        None,
    )
    if not owner:
        raise OSError("Could not create native-menu fixture owner")
    menu = api.CreatePopupMenu()
    lock = threading.Lock()
    tid = kernel.GetCurrentThreadId()

    def report(sequence: int = 0) -> None:
        info = GuiThreadInfo()
        info.size = ctypes.sizeof(info)
        rect = w.RECT()
        valid = bool(api.GetGUIThreadInfo(tid, ctypes.byref(info)))
        geometry = bool(api.GetWindowRect(owner, ctypes.byref(rect)))
        data = {
            "nonce": nonce,
            "sequence": sequence,
            "owner": int(owner),
            "valid": valid and geometry,
            "visible": bool(api.IsWindowVisible(owner)),
            "flags": int(info.flags),
            "menu_owner": int(info.menu_owner or 0),
            "foreground": int(api.GetForegroundWindow() or 0),
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
        }
        with lock:
            sys.stdout.write(json.dumps(data) + chr(10))
            sys.stdout.flush()

    def close_owned() -> None:
        api.PostMessageW(owner, 0x001F, 0, 0)  # WM_CANCELMODE: only our owner.
        api.PostMessageW(owner, 0x0010, 0, 0)  # WM_CLOSE: only our owner.

    watchdog = threading.Timer(45, close_owned)
    watchdog.daemon = True
    try:
        if not menu or not api.AppendMenuW(menu, 0, 1, "Owned fixture item"):
            raise OSError("Could not create native fixture menu")
        api.SetForegroundWindow(owner)
        if api.GetForegroundWindow() != owner:
            raise OSError("Fixture did not acquire foreground during setup")
        threading.Thread(
            target=_commands, args=(report, close_owned), daemon=True
        ).start()
        watchdog.start()
        api.TrackPopupMenuEx(menu, 0x0102, 220, 180, owner, None)
        report()
        message = w.MSG()
        while (
            api.IsWindow(owner)
            and api.GetMessageW(ctypes.byref(message), None, 0, 0) > 0
        ):
            api.TranslateMessage(ctypes.byref(message))
            api.DispatchMessageW(ctypes.byref(message))
    finally:
        watchdog.cancel()
        if menu:
            api.DestroyMenu(menu)
        if api.IsWindow(owner):
            api.DestroyWindow(owner)
    return 0


def main() -> int:
    """No native APIs are invoked without the explicit fixture execution flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-after-handoff", action="store_true")
    parser.add_argument("--nonce", default="")
    args = parser.parse_args()
    if not args.execute_after_handoff:
        sys.stdout.write(
            json.dumps(
                {
                    "execute": False,
                    "windows": ["owned owner", "owned popup"],
                    "watchdog_seconds": 45,
                }
            )
            + "\n"
        )
        return 0
    if len(args.nonce) != 32 or any(c not in "0123456789abcdef" for c in args.nonce):
        raise ValueError("Fixture requires its parent's correlation nonce")
    return execute(args.nonce)


if __name__ == "__main__":
    raise SystemExit(main())
