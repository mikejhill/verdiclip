"""Raw owned HWND observer; deliberately has no cursor-setting API."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from collections.abc import Callable
from uuid import uuid4

from tests.capture.cursor_admission_policy import Admission, PreservedState
from tests.capture.native_cursor import CursorInfo

from verdiclip.capture.input_integrity import GuiThreadInfo

# Native callbacks must survive any failed HWND/class retirement until process exit.
_UNRETIRED: list[OwnedSurface] = []


class NativeState:
    """Read-only full preservation snapshot and exact point ownership."""

    def __init__(self) -> None:
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetForegroundWindow.restype = w.HWND
        self.api.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
        self.api.GetGUIThreadInfo.restype = w.BOOL
        self.api.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
        self.api.GetCursorInfo.restype = w.BOOL
        self.api.WindowFromPhysicalPoint.argtypes = [w.POINT]
        self.api.WindowFromPhysicalPoint.restype = w.HWND
        self.api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.api.GetAncestor.restype = w.HWND

    def read(self) -> PreservedState:
        """Retain all foreign GUI recipients plus logical cursor state."""
        gui, cursor = GuiThreadInfo(), CursorInfo()
        gui.size, cursor.size = ctypes.sizeof(gui), ctypes.sizeof(cursor)
        if not self.api.GetGUIThreadInfo(0, ctypes.byref(gui)):
            raise OSError("Foreign GUI snapshot unavailable")
        if not self.api.GetCursorInfo(ctypes.byref(cursor)):
            raise OSError("Cursor snapshot unavailable")
        return PreservedState(
            int(self.api.GetForegroundWindow() or 0),
            tuple(
                int(getattr(gui, key) or 0)
                for key in (
                    "flags",
                    "active",
                    "focus",
                    "capture",
                    "menu",
                    "move",
                    "caret",
                )
            ),
            (cursor.flags, int(cursor.cursor or 0), cursor.point.x, cursor.point.y),
        )

    def hit(self, state: PreservedState) -> int:
        """Read the actual physical anchor, never move it to the virtual pointer."""
        return int(self.api.WindowFromPhysicalPoint(w.POINT(*state.cursor[2:])) or 0)

    def process(self, handle: int) -> int:
        """Zero/failure never establishes owned fixture identity."""
        pid = w.DWORD()
        if not self.api.GetWindowThreadProcessId(handle, ctypes.byref(pid)):
            return 0
        return pid.value


class OwnedSurface:
    """Observe natural WM_SETCURSOR without default/Qt cursor handling."""

    def __init__(
        self, state: NativeState, admission: Admission, safe: Callable[[], bool]
    ) -> None:
        self.state, self.admission, self.safe = state, admission, safe
        self.handle = 0
        self.atom = 0
        self.name = "VerdiClipCursorAdmission" + uuid4().hex
        self.api = state.api
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self.module = self.kernel.GetModuleHandleW(None)
        self.callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM
        )
        self.callback = self.callback_type(self._message)

        class WindowClass(ctypes.Structure):
            _fields_ = [
                ("style", w.UINT),
                ("procedure", ctypes.c_void_p),
                ("class_extra", ctypes.c_int),
                ("window_extra", ctypes.c_int),
                ("instance", w.HINSTANCE),
                ("icon", w.HICON),
                ("cursor", w.HANDLE),
                ("background", w.HBRUSH),
                ("menu", w.LPCWSTR),
                ("name", w.LPCWSTR),
            ]

        self.window_class = WindowClass()
        self.window_class.procedure = ctypes.cast(self.callback, ctypes.c_void_p)
        self.window_class.instance, self.window_class.name = self.module, self.name
        self.api.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        self.api.RegisterClassW.restype = w.ATOM
        self.api.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        self.api.UnregisterClassW.restype = w.BOOL
        self.api.CreateWindowExW.argtypes = [
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
        ]
        self.api.CreateWindowExW.restype = w.HWND
        self.api.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.api.DefWindowProcW.restype = ctypes.c_ssize_t
        self.api.DestroyWindow.argtypes = [w.HWND]
        self.api.DestroyWindow.restype = w.BOOL
        self.api.IsWindow.argtypes = [w.HWND]
        self.api.IsWindow.restype = w.BOOL

    def show(self) -> None:
        """Create a hittable 32px surface around the unmoved physical anchor."""
        if self.admission.baseline.gui[3]:
            raise ValueError("Existing capture ownership is unsupported")
        _UNRETIRED.append(self)
        self.atom = self.api.RegisterClassW(ctypes.byref(self.window_class))
        if not self.atom:
            raise OSError("Owned class creation failed")
        x, y = self.admission.baseline.cursor[2:]
        self.handle = int(
            self.api.CreateWindowExW(
                0x08000088,
                self.name,
                "",
                0x90000000,
                x - 16,
                y - 16,
                32,
                32,
                None,
                None,
                self.module,
                None,
            )
            or 0
        )
        if not self.handle:
            raise OSError("Owned surface creation failed")

    def _message(self, hwnd: int, message: int, wp: int, lp: int) -> int:
        if message == 0x20:
            try:
                state = self.state.read()
                self.admission.cursor_message(
                    state,
                    owned=hwnd == wp == self.handle != 0
                    and lp & 0xFFFF == 1
                    and self.state.hit(state) == self.handle,
                    safe=self.safe(),
                )
            except Exception:  # noqa: BLE001 - native callbacks cannot propagate
                self.admission.fail()
            return 1  # Never invoke default/parent/Qt cursor handling.
        if message == 0x21:
            self.admission.fail()
            return 4  # MA_NOACTIVATEANDEAT; no activation or duplicate Qt input.
        return int(self.api.DefWindowProcW(hwnd, message, wp, lp))

    def close(self) -> bool:
        """Destroy only our HWND before the broker drains; never restore a cursor."""
        destroyed = not self.handle
        if self.handle:
            destroyed = bool(
                self.api.DestroyWindow(self.handle)
            ) and not self.api.IsWindow(self.handle)
            if destroyed:
                self.handle = 0
        if self.atom and destroyed:
            if not self.api.UnregisterClassW(self.name, self.module):
                return False
            self.atom = 0
        if destroyed and self in _UNRETIRED:
            _UNRETIRED.remove(self)
        return bool(destroyed)
