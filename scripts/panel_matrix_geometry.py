"""Bounded native parent/client/DPI evidence; no origin correction or selection."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any, Protocol

MAX_ANCESTORS = 8
AWARENESS_CONTEXTS = (
    (-1, "unaware"),
    (-2, "system"),
    (-3, "per-monitor"),
    (-4, "per-monitor-v2"),
    (-5, "unaware-gdi-scaled"),
)


class NativeReader(Protocol):
    """Existing guarded and watchdog-logged native call interface."""

    def call(self, name: str, *args: object) -> int:
        """Read native result only after ownership is rechecked."""
        ...


def configure_geometry_api(api: ctypes.WinDLL) -> None:
    """Declare ABI without invoking or changing native awareness settings."""
    signatures = {
        "GetParent": ([wintypes.HWND], wintypes.HWND),
        "GetWindowRect": (
            [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
            wintypes.BOOL,
        ),
        "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
        "GetThreadDpiAwarenessContext": ([], wintypes.HANDLE),
        "GetWindowDpiAwarenessContext": ([wintypes.HWND], wintypes.HANDLE),
        "AreDpiAwarenessContextsEqual": (
            [wintypes.HANDLE, wintypes.HANDLE],
            wintypes.BOOL,
        ),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result


def awareness(reader: NativeReader, context: int) -> str:
    """Retain a recognized awareness category, without setting a context."""
    if not context:
        raise RuntimeError("Native DPI awareness context unavailable")
    matches = [
        name
        for value, name in AWARENESS_CONTEXTS
        if reader.call("AreDpiAwarenessContextsEqual", context, value)
    ]
    if len(matches) != 1:
        raise RuntimeError("Native DPI awareness context ambiguous or unknown")
    return matches[0]


def window_evidence(
    reader: NativeReader, hwnd: int, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Read signed client/window coordinates and verify owned root ancestry."""
    root = manifest["root_hwnd"]
    if reader.call("GetAncestor", hwnd, 2) != root:
        raise RuntimeError("Diagnostic ancestor no longer belongs to owned root")
    pid = wintypes.DWORD()
    if (
        not reader.call("GetWindowThreadProcessId", hwnd, ctypes.byref(pid))
        or pid.value not in manifest["owned_process_ids"]
    ):
        raise RuntimeError("Diagnostic ancestor owner differs")
    name = ctypes.create_unicode_buffer(256)
    if not reader.call("GetClassNameW", hwnd, name, len(name)):
        raise RuntimeError("Diagnostic ancestor class unavailable")
    window, client = wintypes.RECT(), wintypes.RECT()
    if not reader.call("GetWindowRect", hwnd, ctypes.byref(window)) or not reader.call(
        "GetClientRect", hwnd, ctypes.byref(client)
    ):
        raise RuntimeError("Diagnostic ancestor rectangle unavailable")
    a, b = (
        wintypes.POINT(client.left, client.top),
        wintypes.POINT(client.right, client.bottom),
    )
    if not reader.call("ClientToScreen", hwnd, ctypes.byref(a)) or not reader.call(
        "ClientToScreen", hwnd, ctypes.byref(b)
    ):
        raise RuntimeError("Diagnostic ancestor screen conversion failed")
    dpi = reader.call("GetDpiForWindow", hwnd)
    if not dpi:
        raise RuntimeError("Diagnostic window DPI unavailable")
    category = awareness(reader, reader.call("GetWindowDpiAwarenessContext", hwnd))
    if reader.call("GetAncestor", hwnd, 2) != root:
        raise RuntimeError("Diagnostic ancestor root changed")
    return {
        "hwnd": hwnd,
        "pid": pid.value,
        "class": name.value,
        "window_rect": [window.left, window.top, window.right, window.bottom],
        "client_rect": [client.left, client.top, client.right, client.bottom],
        "screen_client_rect": [a.x, a.y, b.x, b.y],
        "dpi": dpi,
        "dpi_awareness": category,
    }


def parent_geometry(
    reader: NativeReader, hwnd: int, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Require a bounded exact chain to verified root, never choose an origin."""
    thread_context = awareness(reader, reader.call("GetThreadDpiAwarenessContext"))
    chain = []
    seen = set()
    root = manifest["root_hwnd"]
    for _ in range(MAX_ANCESTORS):
        if not hwnd or hwnd in seen:
            raise RuntimeError("Diagnostic parent chain missing root or cyclic")
        seen.add(hwnd)
        chain.append(window_evidence(reader, hwnd, manifest))
        if hwnd == root:
            return {
                "thread_dpi_awareness": thread_context,
                "parent_chain": chain,
                "origin_selected": False,
                "numeric_phase_qualified": False,
            }
        hwnd = reader.call("GetParent", hwnd)
    raise RuntimeError("Diagnostic parent chain exceeds fixed bound")
