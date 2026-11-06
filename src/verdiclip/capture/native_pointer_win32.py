"""Read-only ownership checks and keyboard-only movement for an opt-in prototype."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import math
import os
import time
from typing import override

from verdiclip.capture.input_integrity import GuiThreadInfo, InputIntegrityGuard
from verdiclip.capture.native_pointer import NativePointerRoute, PointerPhase
from verdiclip.geometry import Point


class NativePointerSession:
    """Keep the original recipient's identity while an owned surface covers it.

    Guard/forward is not atomic with foreign capture acquisition. This class is
    an experimental ordinary-window component, not a popup safety guarantee.
    """

    def __init__(self) -> None:
        self.route = NativePointerRoute()
        self._admission_deadline: float | None = None
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetForegroundWindow.restype = w.HWND
        self.api.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.api.GetAncestor.restype = w.HWND
        self.api.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
        self.api.GetGUIThreadInfo.restype = w.BOOL
        self.api.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        self.api.GetPhysicalCursorPos.restype = w.BOOL
        self.api.WindowFromPhysicalPoint.argtypes = [w.POINT]
        self.api.WindowFromPhysicalPoint.restype = w.HWND
        self.api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.SetPhysicalCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.api.SetPhysicalCursorPos.restype = w.BOOL
        self._windows: frozenset[int] = frozenset()
        self._foreground, self._focus = self._state()
        self._original = self._hit(self.cursor())
        self._pid = self._process(self._original)
        self._integrity = OriginalRecipientIntegrity(self)
        if not self._pid or not self._integrity.permits(self._foreground):
            raise ValueError("Native pointer original recipient unavailable")

    def _process(self, window: int) -> int:
        process = w.DWORD()
        if not window or not self.api.GetWindowThreadProcessId(
            window, ctypes.byref(process)
        ):
            return 0
        return int(process.value)

    def _state(self) -> tuple[int, int]:
        before = int(self.api.GetForegroundWindow() or 0)
        info = GuiThreadInfo()
        info.size = ctypes.sizeof(info)
        if not self.api.GetGUIThreadInfo(0, ctypes.byref(info)):
            raise ValueError("Native pointer GUI state unavailable")
        after = int(self.api.GetForegroundWindow() or 0)
        if (
            not before
            or before != after
            or int(info.active or 0) != before
            or info.capture
            or info.menu
            or info.move
            or info.flags & 0x1E
        ):
            raise ValueError("Native pointer protected GUI state")
        return before, int(info.focus or 0)

    def cursor(self) -> Point:
        """Read physical coordinates without moving or warping."""
        point = w.POINT()
        if not self.api.GetPhysicalCursorPos(ctypes.byref(point)):
            raise ValueError("Native pointer position unavailable")
        return Point(point.x, point.y)

    @staticmethod
    def _valid(point: Point) -> bool:
        return all(
            type(value) in {int, float}
            and math.isfinite(value)
            and int(value) == value
            and -(2**31) <= value < 2**31
            for value in (point.x, point.y)
        )

    def _hit(self, point: Point) -> int:
        if not self._valid(point):
            raise ValueError("Native pointer coordinates invalid")
        return int(
            self.api.WindowFromPhysicalPoint(w.POINT(int(point.x), int(point.y))) or 0
        )

    def bind(self, windows: frozenset[int]) -> None:
        """Publish only handles belonging to this process."""
        if any(
            self._process(window) != os.getpid()
            or int(self.api.GetAncestor(window, 2) or 0) != window
            for window in windows
        ):
            raise ValueError("Native pointer surface is not owned")
        self.route.bind(windows)
        self._windows = windows
        self._admission_deadline = time.monotonic() + 1

    def expired(self) -> bool:
        """Bound natural ownership observation from publication of owned surfaces."""
        deadline = self._admission_deadline
        return deadline is not None and time.monotonic() >= deadline

    def safe(self, point: Point | None = None) -> bool:
        """Validate original state and exact owned hit without changing ownership."""
        try:
            return (
                self._state() == (self._foreground, self._focus)
                and self._process(self._original) == self._pid
                and self._hit(self.cursor()) in self._windows
                and (point is None or self._hit(point) in self._windows)
                and all(
                    self._process(window) == os.getpid() for window in self._windows
                )
                and self._integrity.permits(self._foreground)
            )
        except (OSError, ValueError):
            return False

    def natural_cursor(self, window: int) -> bool:
        """Called solely by the owned overlay's actual WM_SETCURSOR handler."""
        try:
            return self.route.admit(
                window, lambda: self._hit(self.cursor()) == window and self.safe()
            )
        except (OSError, ValueError):
            self.route.close()
            return False

    def move(self, point: Point) -> bool:
        """Admit one absolute coordinate under current ownership."""
        return self.route.move(point, lambda: self.safe(point))

    def _owned(self) -> bool:
        return self.route.phase is PointerPhase.OWNED

    def nudge(self, point: Point) -> bool:
        """One keyboard-authorized move; never used for admission or teardown."""
        confirmed = False
        try:
            if (
                not self._valid(point)
                or not self._owned()
                or not self.safe(point)
                or not self._owned()
            ):
                self.route.close()
                return False
            if not self.api.SetPhysicalCursorPos(int(point.x), int(point.y)):
                self.route.close()
                return False
            actual = self.cursor()
            if actual != point or not self.move(actual):
                self.route.close()
                return False
            confirmed = True
            return True
        finally:
            if not confirmed:
                self.route.close()

    def close(self) -> None:
        """Latch cancellation without cursor writes."""
        self.route.close()


class OriginalRecipientIntegrity(InputIntegrityGuard):
    """A newly owned HWND cannot hide the original foreign integrity recipient."""

    def __init__(self, session: NativePointerSession) -> None:
        super().__init__()
        self._session = session

    @override
    def _pointer_recipient(self, info: GuiThreadInfo) -> int:
        actual = super()._pointer_recipient(info)
        session = self._session
        if (
            actual in session._windows
            and session._process(session._original) == session._pid
        ):
            return session._original
        return actual
