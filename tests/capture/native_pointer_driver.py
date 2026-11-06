"""Finite SendInput commands scoped to one disposable hosted ordinary fixture."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
from dataclasses import dataclass

from tests.capture.hosted_fixture_input import (
    MouseInput,
    absolute_pixel_center,
    require_hosted,
)
from tests.capture.native_cursor import CursorInfo

from verdiclip.capture.input_integrity import GuiThreadInfo
from verdiclip.geometry import Point, Rect


class KeyInput(ctypes.Structure):
    """KEYBDINPUT ABI, without text or arbitrary key sequences."""

    _fields_ = (
        ("key", w.WORD),
        ("scan", w.WORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    )


class Payload(ctypes.Union):
    """Mouse payload fixes the largest native INPUT union size."""

    _fields_ = (("mouse", MouseInput), ("keyboard", KeyInput))


class Input(ctypes.Structure):
    """Native INPUT ABI checked before use."""

    _fields_ = (("kind", w.DWORD), ("payload", Payload))


@dataclass(frozen=True, repr=False)
class State:
    """Private native values; only fixed comparisons may be reported."""

    flags: int
    cursor: int
    point: Point
    foreground: int
    gui: tuple[int, ...]


class Driver:
    """No foreground transfer, cursor visibility change, capture or repair APIs."""

    def __init__(self, fixture: int, fixture_pid: int) -> None:
        require_hosted(os.environ)
        if os.environ.get("VERDICLIP_HOSTED_NATIVE_POINTER") != "1":
            raise RuntimeError("native-pointer-hosted-gate")
        self.fixture = fixture
        self.fixture_pid = fixture_pid
        self.allowed = {fixture: fixture_pid}
        self.sent = 0
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetCursorInfo": ([ctypes.POINTER(CursorInfo)], w.BOOL),
            "GetForegroundWindow": ([], w.HWND),
            "IsWindow": ([w.HWND], w.BOOL),
            "LoadImageW": (
                [
                    w.HINSTANCE,
                    ctypes.c_void_p,
                    w.UINT,
                    ctypes.c_int,
                    ctypes.c_int,
                    w.UINT,
                ],
                w.HANDLE,
            ),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetGUIThreadInfo": ([w.DWORD, ctypes.POINTER(GuiThreadInfo)], w.BOOL),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "WindowFromPoint": ([w.POINT], w.HWND),
            "GetAncestor": ([w.HWND, w.UINT], w.HWND),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
            "SendInput": ([w.UINT, ctypes.POINTER(Input), ctypes.c_int], w.UINT),
        }
        for name, (args, result) in signatures.items():
            call = getattr(self.api, name)
            call.argtypes, call.restype = args, result
        if ctypes.sizeof(Input) != 40:
            raise RuntimeError("input-abi-unsupported")

    def read(self) -> State:
        """Read once; no wait for favorable cursor/foreground state."""
        info, gui, pid = CursorInfo(), GuiThreadInfo(), w.DWORD()
        info.size, gui.size = ctypes.sizeof(info), ctypes.sizeof(gui)
        foreground = self.api.GetForegroundWindow()
        thread = self.api.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        if (
            foreground != self.fixture
            or pid.value != self.fixture_pid
            or not thread
            or not self.api.GetGUIThreadInfo(thread, ctypes.byref(gui))
            or not self.api.GetCursorInfo(ctypes.byref(info))
            or self.api.GetForegroundWindow() != foreground
        ):
            raise RuntimeError("owned-foreground-observation-unavailable")
        return State(
            int(info.flags),
            int(info.cursor or 0),
            Point(info.point.x, info.point.y),
            int(foreground),
            tuple(
                int(x or 0)
                for x in (
                    gui.flags,
                    gui.active,
                    gui.focus,
                    gui.capture,
                    gui.menu,
                    gui.move,
                )
            ),
        )

    def client(self) -> Rect:
        """Use actual native client pixels, not assumed Qt scaling."""
        bounds, origin = w.RECT(), w.POINT()
        if not self.api.GetClientRect(
            self.fixture, ctypes.byref(bounds)
        ) or not self.api.ClientToScreen(self.fixture, ctypes.byref(origin)):
            raise RuntimeError("owned-client-unavailable")
        return Rect(
            origin.x, origin.y, bounds.right - bounds.left, bounds.bottom - bounds.top
        )

    def guard(self, point: Point | None = None) -> None:
        """Revalidate before each finite injected event; no atomic-exclusion claim."""
        require_hosted(os.environ)
        state = self.read()
        if state.gui[0] & 0x1E or any(state.gui[i] for i in (3, 4, 5)):
            raise RuntimeError("foreign-gui-state-refused")
        if point is not None:
            bounds = self.client()
            if not (
                bounds.x <= point.x < bounds.right
                and bounds.y <= point.y < bounds.bottom
            ):
                raise RuntimeError("input-outside-owned-client")
            hit = self.api.WindowFromPoint(w.POINT(int(point.x), int(point.y)))
            pid = w.DWORD()
            if (
                not hit
                or not self.api.GetWindowThreadProcessId(hit, ctypes.byref(pid))
                or self.allowed.get(int(self.api.GetAncestor(hit, 2) or 0)) != pid.value
            ):
                raise RuntimeError("input-hit-not-owned")

    def send(self, packet: Input, *, point: Point | None = None) -> None:
        """At most sixteen requested packets; no failed insertion retry."""
        self.guard(point)
        if self.sent >= 16:
            raise RuntimeError("input-budget-exhausted")
        self.sent += 1
        if self.api.SendInput(1, ctypes.byref(packet), ctypes.sizeof(Input)) != 1:
            raise RuntimeError("input-insertion-unconfirmed")

    def move(self, point: Point) -> None:
        """One absolute virtual-desktop MOVE, never movement normalization."""
        desktop = Rect(*(self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)))
        x, y = absolute_pixel_center(point, desktop)
        self.send(
            Input(0, Payload(mouse=MouseInput(x, y, 0, 0xC001, 0, 0))), point=point
        )

    def button(self, *, down: bool) -> None:
        """One left-button boundary; no clicks in foreign menus or windows."""
        self.send(
            Input(0, Payload(mouse=MouseInput(0, 0, 0, 2 if down else 4, 0, 0))),
            point=self.read().point,
        )

    def key(self, key: int, *, down: bool) -> None:
        """Only Escape and Right-arrow are allowed by this finite fixture."""
        if key not in (0x1B, 0x27):
            raise ValueError("fixture-key-not-allowed")
        self.send(Input(1, Payload(keyboard=KeyInput(key, 0, 0 if down else 2, 0, 0))))

    def cross_cursor(self) -> int:
        """Match Qt6.11/6.12 shared IDC_CROSS resource loading; never destroy it."""
        handle = int(self.api.LoadImageW(None, 32515, 2, 0, 0, 0x8040) or 0)
        if not handle:
            raise RuntimeError("cross-resource-unavailable")
        return handle
