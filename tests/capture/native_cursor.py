"""Disposable-host-only cursor experiment; never imported by product code."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
from collections.abc import Callable
from dataclasses import dataclass


class CursorInfo(ctypes.Structure):
    """Documented CURSORINFO ABI, not serialized raw handles."""

    _fields_ = (
        ("size", w.DWORD),
        ("flags", w.DWORD),
        ("cursor", w.HANDLE),
        ("point", w.POINT),
    )


@dataclass(frozen=True)
class CursorState:
    """Transient equality oracle; opaque handles never leave the process."""

    flags: int
    handle: int
    x: int
    y: int
    foreground: int

    def metadata(self) -> dict[str, object]:
        """Retain visibility and handle class, not an opaque system handle."""
        return {
            "flags": self.flags,
            "visible": bool(self.flags & 1),
            "handle_class": "non-null" if self.handle else "null",
        }


def probe_magnification_cursor(
    result: dict[str, object], preserved: Callable[[], bool]
) -> None:
    """Try one hide/show only when the original system cursor is visibly shown.

    MagShowSystemCursor is global and not reference-counted. A hidden baseline
    does not reveal its underlying state, so it cannot safely be forced visible.
    The outer owned-case process supervisor bounds native calls independently.
    No shape substitution, cursor movement, input attachment or device change.
    """
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") != "1"
    ):
        raise RuntimeError("Cursor probe requires the disposable hosted fixture")
    result.update(cleanup_confirmed=False, visibility_verdict="incomplete")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    api.GetCursorInfo.restype = w.BOOL
    api.GetForegroundWindow.argtypes = []
    api.GetForegroundWindow.restype = w.HWND

    def state() -> CursorState:
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        if not api.GetCursorInfo(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return CursorState(
            info.flags,
            int(info.cursor or 0),
            info.point.x,
            info.point.y,
            int(api.GetForegroundWindow() or 0),
        )

    before = state()
    result["before"] = before.metadata()
    assert preserved(), "Cursor probe did not start with preserved fixture"
    if not before.flags & 1:
        result.update(
            visibility_verdict="inconclusive-hidden-baseline", cleanup_confirmed=True
        )
        return

    magnification = ctypes.WinDLL("Magnification", use_last_error=True)
    magnification.MagInitialize.argtypes = []
    magnification.MagInitialize.restype = w.BOOL
    magnification.MagUninitialize.argtypes = []
    magnification.MagUninitialize.restype = w.BOOL
    magnification.MagShowSystemCursor.argtypes = [w.BOOL]
    magnification.MagShowSystemCursor.restype = w.BOOL
    if not magnification.MagInitialize():
        result.update(
            visibility_verdict="initialization-refused", cleanup_confirmed=True
        )
        raise AssertionError("Cursor magnification initialization failed")
    try:
        try:
            # Once attempted, restore even if the API reports failure: its
            # original global state is known only because the baseline is visible.
            result["hide_succeeded"] = bool(magnification.MagShowSystemCursor(0))
            during = state()
            result["during"] = during.metadata()
            result["during_preserved"] = (
                during.handle,
                during.x,
                during.y,
                during.foreground,
            ) == (before.handle, before.x, before.y, before.foreground) and preserved()
        finally:
            result["restore_succeeded"] = bool(magnification.MagShowSystemCursor(1))
    finally:
        result["uninitialize_succeeded"] = bool(magnification.MagUninitialize())
    after = state()
    result["after"] = after.metadata()
    result["after_preserved"] = after == before and preserved()
    result["cleanup_confirmed"] = (
        result["restore_succeeded"] is True
        and result["uninitialize_succeeded"] is True
        and result["after_preserved"] is True
    )
    assert result["cleanup_confirmed"], "Cursor probe restoration is unconfirmed"
    assert result["during_preserved"], "Cursor probe changed the fixture or cursor"
    assert result["hide_succeeded"], "Cursor suppression API refused the experiment"
    result["visibility_verdict"] = (
        "visible-hidden-restored" if not during.flags & 1 else "ineffective"
    )


def probe_owned_cursor_baseline(
    result: dict[str, object], owned_capture: int, preserved: Callable[[], bool]
) -> None:
    """Give an already-capturing owned popup one balanced visible-cursor attempt.

    GetCapture returns only the calling thread's capture owner. This cannot
    borrow another application's queue, capture, cursor or display count.
    """
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") != "1"
    ):
        raise RuntimeError("Cursor probe requires the disposable hosted fixture")
    result.update(cleanup_confirmed=False, visibility_verdict="incomplete")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    api.GetCursorInfo.restype = w.BOOL
    api.GetCapture.argtypes = api.GetForegroundWindow.argtypes = []
    api.GetCapture.restype = api.GetForegroundWindow.restype = w.HWND
    api.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
    api.LoadCursorW.restype = w.HANDLE
    api.SetCursor.argtypes = [w.HANDLE]
    api.SetCursor.restype = w.HANDLE
    api.ShowCursor.argtypes = [w.BOOL]
    api.ShowCursor.restype = ctypes.c_int

    api.GetForegroundWindow.argtypes = []
    api.GetForegroundWindow.restype = w.HWND

    def observe() -> tuple[int, int, int, int, int]:
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        if not api.GetCursorInfo(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            int(info.flags),
            int(info.cursor or 0),
            int(info.point.x),
            int(info.point.y),
            int(api.GetForegroundWindow() or 0),
        )

    before = observe()
    result["original"] = {
        "flags": before[0],
        "visible": bool(before[0] & 1),
        "handle_class": "non-null" if before[1] else "null",
    }
    if before[0] & 1:
        probe_magnification_cursor(result, preserved)
        return
    assert preserved(), "Cursor baseline did not start with preserved fixture"
    if not owned_capture or int(api.GetCapture() or 0) != owned_capture:
        result.update(
            visibility_verdict="inconclusive-no-owned-mouse-capture",
            cleanup_confirmed=True,
        )
        return
    arrow = api.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW, shared stock
    assert arrow, "Owned cursor baseline could not load the stock arrow"
    original_cursor = api.SetCursor(arrow)
    candidate: dict[str, object] = {}
    try:
        raised_count = api.ShowCursor(1)
        try:
            result["baseline_count"] = raised_count
            result["candidate"] = candidate
            probe_magnification_cursor(candidate, preserved)
            result["visibility_verdict"] = candidate["visibility_verdict"]
        finally:
            result["balanced_count"] = api.ShowCursor(0) == raised_count - 1
    finally:
        api.SetCursor(original_cursor)
        result["baseline_restored"] = observe() == before and preserved()
        result["cleanup_confirmed"] = (
            result.get("balanced_count") is True
            and result["baseline_restored"] is True
            and candidate.get("cleanup_confirmed") is True
        )
    assert result["cleanup_confirmed"], "Owned cursor baseline cleanup is unconfirmed"
