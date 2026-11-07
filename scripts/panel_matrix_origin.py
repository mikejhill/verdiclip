"""Read-only owned reference viewport evidence, never acquisition geometry."""

from __future__ import annotations

import ctypes
import math
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, Protocol

MAX_WINDOWS = 64
MARKERS = (
    (4, 4, (241, 31, 117), (23, 213, 83)),
    (20, 4, (37, 89, 239), (249, 197, 17)),
)


class Guard(Protocol):
    """Ownership check contract."""

    def check(self) -> None:
        """Verify owned foreground and geometry."""
        ...


class ViewportOrigin:
    """Bounded native child inspection with ownership around every call."""

    def __init__(self, guard: Guard, emit: Callable[[str, str], None]) -> None:
        self.guard, self.emit = guard, emit
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindowThreadProcessId": (
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            "GetClassNameW": (
                [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int],
                ctypes.c_int,
            ),
            "GetClientRect": (
                [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
                wintypes.BOOL,
            ),
            "ClientToScreen": (
                [wintypes.HWND, ctypes.POINTER(wintypes.POINT)],
                wintypes.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def call(self, name: str, *args: object) -> int:
        """Watch native calls and guard before consuming returned data."""
        self.emit("call_start", "reference.origin." + name)
        self.guard.check()
        result = getattr(self.api, name)(*args)
        self.guard.check()
        self.emit("call_end", "reference.origin." + name)
        return int(result or 0)

    def observe(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Identify exactly one visible owned widget within a fixed traversal."""
        root = manifest["root_hwnd"]
        pending = [self.call("GetWindow", root, 5)]
        visited: set[int] = set()
        candidates = []
        while pending:
            hwnd = pending.pop()
            if not hwnd:
                continue
            if hwnd in visited or len(visited) >= MAX_WINDOWS:
                raise RuntimeError(
                    "Owned child traversal exceeded bound or repeated HWND"
                )
            visited.add(hwnd)
            pending.extend(
                (self.call("GetWindow", hwnd, 2), self.call("GetWindow", hwnd, 5))
            )
            name = ctypes.create_unicode_buffer(256)
            if not self.call("GetClassNameW", hwnd, name, len(name)):
                raise RuntimeError("Owned child class unavailable")
            if name.value != "Chrome_RenderWidgetHostHWND" or not self.call(
                "IsWindowVisible", hwnd
            ):
                continue
            candidates.append(self.inspect(hwnd, manifest))
        if len(candidates) != 1:
            raise RuntimeError("Expected exactly one visible owned render widget")
        return candidates[0]

    def inspect(self, hwnd: int, manifest: dict[str, Any]) -> dict[str, Any]:
        """Read and recheck candidate ownership and physical client corners."""
        root = manifest["root_hwnd"]
        pid = wintypes.DWORD()
        if not self.call("GetWindowThreadProcessId", hwnd, ctypes.byref(pid)):
            raise RuntimeError("Render widget owner unavailable")
        if (
            pid.value not in manifest["owned_process_ids"]
            or self.call("GetAncestor", hwnd, 2) != root
        ):
            raise RuntimeError("Render widget ownership differs")
        rect = wintypes.RECT()
        if not self.call("GetClientRect", hwnd, ctypes.byref(rect)):
            raise RuntimeError("Render widget client rectangle unavailable")
        a, b = (
            wintypes.POINT(rect.left, rect.top),
            wintypes.POINT(rect.right, rect.bottom),
        )
        if not self.call("ClientToScreen", hwnd, ctypes.byref(a)) or not self.call(
            "ClientToScreen", hwnd, ctypes.byref(b)
        ):
            raise RuntimeError("Render widget screen origin unavailable")
        bounds = [a.x, a.y, b.x, b.y]
        outer = manifest["allowed_rect"]
        if (
            not outer[0] <= a.x < b.x <= outer[2]
            or not outer[1] <= a.y < b.y <= outer[3]
        ):
            raise RuntimeError("Render widget not contained in owned root")
        if self.call("GetAncestor", hwnd, 2) != root:
            raise RuntimeError("Render widget ancestry changed")
        return {
            "hwnd": hwnd,
            "pid": pid.value,
            "root_hwnd": root,
            "screen_client_rect": bounds,
        }


def verify_calibration(
    rgb: bytes, root: list[int], origin: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Check only predetermined marker pixels, without scanning or fitting."""
    viewport = state["viewport"]
    if (
        viewport["scroll"] != [0, 0]
        or viewport["visual_offset"] != [0, 0]
        or viewport["visual_scale"] != 1
    ):
        raise RuntimeError("Unsupported authored viewport transform")
    dpr = state["dpr"]
    if dpr not in (1, 1.25):
        raise RuntimeError("Unsupported calibration DPR")
    client = origin["screen_client_rect"]
    width, height = root[2] - root[0], root[3] - root[1]
    if len(rgb) != width * height * 3:
        raise RuntimeError("Calibration raster size differs")
    for index, (x, y, left, right) in enumerate(MARKERS):
        if state["calibration"][index] != [x, y, x + 8, y + 8]:
            raise RuntimeError("Authored calibration placement differs")
        x0, y0 = client[0] - root[0] + int(x * dpr), client[1] - root[1] + int(y * dpr)
        size, half = int(8 * dpr), int(4 * dpr)
        if not 0 <= x0 < x0 + size <= width or not 0 <= y0 < y0 + size <= height:
            raise RuntimeError("Calibration outside root")
        for row in range(size):
            for column in range(size):
                offset = ((y0 + row) * width + x0 + column) * 3
                if rgb[offset : offset + 3] != bytes(left if column < half else right):
                    raise RuntimeError("Predicted calibration pixels differ")
    # Exact telemetry projection only; float representation remains unqualified.
    rect = state["state"]["target"]["rect"]
    edges = [client[i % 2] + rect[i] * dpr for i in range(4)]
    return {
        "verified_marker_origin": client[:2],
        "native_client_extent": [client[2] - client[0], client[3] - client[1]],
        "authored_viewport": viewport,
        "projected_outer_edges": edges,
        "outward_inclusive_outer": [
            math.floor(edges[0]),
            math.floor(edges[1]),
            math.ceil(edges[2]),
            math.ceil(edges[3]),
        ],
        "numeric_phase_qualified": False,
        "feature_zoom_assumptions_qualified": False,
    }
