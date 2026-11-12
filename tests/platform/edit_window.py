"""An owned Win32 window with a scrolling multi-line EDIT control.

Run as a separate process so UI Automation calls from the test never wait on
the test's own thread. Prints the EDIT rectangle and a non-scrolling point,
then pumps messages until the test kills it.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import sys

LINES = 400
_WS_OVERLAPPEDWINDOW = 0x00CF0000
_WS_VISIBLE = 0x10000000
_WS_CHILD = 0x40000000
_WS_VSCROLL = 0x00200000
_ES_MULTILINE = 0x0004
_ES_READONLY = 0x0800
_WM_SETFONT = 0x0030


def main() -> None:
    """Create the window, report its handles, and serve messages."""
    user = c.WinDLL("user32", use_last_error=True)
    gdi = c.WinDLL("gdi32")
    user.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    user.CreateWindowExW.restype = w.HWND
    user.CreateWindowExW.argtypes = (
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        c.c_int,
        c.c_int,
        c.c_int,
        c.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        w.LPVOID,
    )
    user.SendMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    frame = user.CreateWindowExW(
        # WS_EX_TOPMOST | WS_EX_NOACTIVATE: stay visible above other windows
        # without taking the foreground from the test process.
        0x00000008 | 0x08000000,
        "#32770",  # Dialog class: unlike STATIC it is not hit-test transparent
        "VerdiClip scroll fixture",
        _WS_OVERLAPPEDWINDOW | _WS_VISIBLE,
        100,
        100,
        640,
        480,
        None,
        None,
        None,
        None,
    )
    text = "\r\n".join(
        f"Line {n:04d} " + "abcdefghijklmnopqrstuvwxyz"[n % 26] * (n % 40 + 5)
        for n in range(LINES)
    )
    edit = user.CreateWindowExW(
        0,
        "EDIT",
        text,
        _WS_CHILD | _WS_VISIBLE | _WS_VSCROLL | _ES_MULTILINE | _ES_READONLY,
        0,
        0,
        600,
        420,
        frame,
        None,
        None,
        None,
    )
    font = gdi.GetStockObject(16)  # SYSTEM_FIXED_FONT
    user.SendMessageW(edit, _WM_SETFONT, font, 1)
    rect = w.RECT()
    user.GetWindowRect(edit, c.byref(rect))
    client = w.RECT()
    user.GetClientRect(frame, c.byref(client))
    # Frame client area below the EDIT control, from the real client height.
    blank = w.POINT(300, (420 + client.bottom) // 2)
    user.ClientToScreen(frame, c.byref(blank))
    sys.stdout.write(
        f"{rect.left} {rect.top} {rect.right} {rect.bottom} {blank.x} {blank.y}\n"
    )
    sys.stdout.flush()

    message = w.MSG()
    while user.GetMessageW(c.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(c.byref(message))
        user.DispatchMessageW(c.byref(message))


if __name__ == "__main__":
    main()
