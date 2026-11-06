"""Independent logical-only cursor oracle; no cursor or input mutation APIs."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from collections.abc import Callable
from dataclasses import dataclass

from tests.capture.native_cursor import CursorInfo

from verdiclip.capture.input_integrity import GuiThreadInfo


@dataclass(frozen=True, repr=False)
class Snapshot:
    """Private transient equality data, never serialized as handles or positions."""

    flags: int
    shape: int
    position: tuple[int, int]
    foreground: int
    thread: int
    process: int
    gui: tuple[int, ...]

    @property
    def visible(self) -> bool:
        """Logical visibility only, not compositor/hardware-pointer proof."""
        return bool(self.flags & 1)


def during_checks(before: Snapshot, during: Snapshot) -> dict[str, bool]:
    """Require hidden state and preserved foreign identity, not cross-owner shape."""
    return {
        "logical_hidden": during.flags == 0,
        "stationary": during.position == before.position,
        "foreground_preserved": during.foreground == before.foreground,
        "foreign_identity_preserved": (during.thread, during.process)
        == (before.thread, before.process),
        "foreign_gui_preserved": during.gui == before.gui,
    }


def observe() -> Snapshot:
    """Read independent global cursor and foreground GUI state without mutation."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    api.GetCursorInfo.restype = w.BOOL
    api.GetForegroundWindow.argtypes = []
    api.GetForegroundWindow.restype = w.HWND
    api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    api.GetWindowThreadProcessId.restype = w.DWORD
    api.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
    api.GetGUIThreadInfo.restype = w.BOOL
    info = CursorInfo()
    info.size = ctypes.sizeof(info)
    gui = GuiThreadInfo()
    gui.size = ctypes.sizeof(gui)
    foreground = api.GetForegroundWindow()
    pid = w.DWORD()
    tid = api.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    if (
        not foreground
        or not tid
        or not pid.value
        or not api.GetGUIThreadInfo(tid, ctypes.byref(gui))
        or not api.GetCursorInfo(ctypes.byref(info))
        or api.GetForegroundWindow() != foreground
    ):
        raise RuntimeError("independent-observation-unavailable")
    return Snapshot(
        int(info.flags),
        int(info.cursor or 0),
        (info.point.x, info.point.y),
        int(foreground),
        int(tid),
        int(pid.value),
        (
            int(gui.flags),
            int(gui.active or 0),
            int(gui.focus or 0),
            int(gui.capture or 0),
            int(gui.menu or 0),
            int(gui.move or 0),
        ),
    )


class FirstRetirement:
    """Latch the first post-retirement observation, including an observation error."""

    def __init__(self, baseline: Snapshot) -> None:
        self.baseline = baseline
        self.attempted = False
        self.after: Snapshot | None = None
        self.unavailable = False

    def record(self, reader: Callable[[], Snapshot]) -> None:
        """No later recovery can replace the first adverse or unknown result."""
        if self.attempted:
            raise RuntimeError("first-retirement-already-observed")
        self.attempted = True
        try:
            self.after = reader()
        except Exception:
            self.unavailable = True
            raise

    def summary(self) -> dict[str, bool | None]:
        """Expose only fixed comparisons, never raw snapshot values."""
        after = self.after
        return {
            "first_observation_available": after is not None,
            "logical_visibility_restored": None
            if after is None
            else after.flags == self.baseline.flags,
            "global_shape_restored": None
            if after is None
            else after.shape == self.baseline.shape,
            "position_unchanged": None
            if after is None
            else after.position == self.baseline.position,
            "foreign_gui_preserved": None
            if after is None
            else (after.foreground, after.thread, after.process, after.gui)
            == (
                self.baseline.foreground,
                self.baseline.thread,
                self.baseline.process,
                self.baseline.gui,
            ),
            "rendered_pointer_verified": False,
        }
