"""One simulated owned-client MOVE before the stationary component interval."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import time
from collections.abc import Callable

from tests.capture.hosted_fixture_input import (
    Input,
    InputPayload,
    MouseInput,
    absolute_pixel_center,
    require_hosted,
)
from tests.capture.owned_cursor_observer import Snapshot, observe
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point, Rect


def eligible_baseline(value: Snapshot) -> bool:
    """Unknown/suppressed cursor state or absent shape cannot qualify setup."""
    return value.flags == 1 and bool(value.shape)


def baseline_summary(value: Snapshot) -> dict[str, bool | str]:
    """No positions, handles, process identities or environment values escape."""
    return {
        "injected_setup": True,
        "cursor_state": {0: "hidden", 1: "showing", 2: "suppressed"}.get(
            value.flags, "unknown"
        ),
        "handle_present": bool(value.shape),
        "physical_input_verified": False,
        "rendered_pointer_verified": False,
    }


class OneMove:
    """Single-use preparation; no method can repair a component teardown."""

    def __init__(self, peer: ScrollPeer) -> None:
        require_hosted(os.environ)
        self.peer = peer
        self.attempted = False
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "WindowFromPoint": ([w.POINT], w.HWND),
            "GetAncestor": ([w.HWND, w.UINT], w.HWND),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
            "SendInput": ([w.UINT, ctypes.POINTER(Input), ctypes.c_int], w.UINT),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        if ctypes.sizeof(Input) != 40:
            raise RuntimeError("setup-input-layout")

    def guard(self, point: Point | None = None) -> Point:
        """Independently bind destination to the live owned foreground client."""
        require_hosted(os.environ)
        peer = self.peer
        if not peer.poll() or peer.process is None or peer.identity is None:
            raise RuntimeError("setup-fixture-unavailable")
        root = peer.handle
        if peer.identity[0] != peer.process.pid or peer.identity[2] != root:
            raise RuntimeError("setup-fixture-identity")
        title = ctypes.create_unicode_buffer(160)
        self.api.GetWindowTextW(root, title, len(title))
        if title.value != f"VerdiClip synthetic scroll fixture {peer.nonce}":
            raise RuntimeError("setup-fixture-title")
        state = observe()
        if (
            state.foreground != root
            or state.process != peer.process.pid
            or state.gui[0] & 0x1E
            or any(state.gui[index] for index in (3, 4, 5))
            or not self.api.IsWindowVisible(root)
        ):
            raise RuntimeError("setup-foreign-gui-state")
        client = w.RECT()
        if not self.api.GetClientRect(root, ctypes.byref(client)):
            raise RuntimeError("setup-client-unavailable")
        if client.right <= client.left or client.bottom <= client.top:
            raise RuntimeError("setup-client-empty")
        center = w.POINT(
            (client.left + client.right) // 2, (client.top + client.bottom) // 2
        )
        if not self.api.ClientToScreen(root, ctypes.byref(center)):
            raise RuntimeError("setup-client-unavailable")
        current = Point(center.x, center.y)
        if point is not None and current != point:
            raise RuntimeError("setup-client-moved")
        hit = self.api.WindowFromPoint(center)
        pid = w.DWORD()
        if (
            not hit
            or not self.api.GetWindowThreadProcessId(hit, ctypes.byref(pid))
            or pid.value != peer.process.pid
            or self.api.GetAncestor(hit, 2) != root
        ):
            raise RuntimeError("setup-destination-not-owned")
        return current

    def prepare(self, wait: Callable[[int], None]) -> Snapshot:
        """Insert once, await one correlated fixture ack, then sample once."""
        if self.attempted or self.peer.snapshot_request is not None:
            raise RuntimeError("setup-already-attempted")
        self.attempted = True
        point = self.guard()
        desktop = Rect(*(self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)))
        x, y = absolute_pixel_center(point, desktop)
        packets = (Input * 1)(Input(0, InputPayload(MouseInput(x, y, 0, 0xC001, 0, 0))))
        self.guard(point)
        if self.api.SendInput(1, packets, ctypes.sizeof(Input)) != 1:
            raise RuntimeError("setup-move-insertion-refused")
        self.peer.request_snapshot()
        deadline = time.monotonic() + 2
        while True:
            acknowledged = self.peer.poll_snapshot()
            if time.monotonic() >= deadline:
                raise TimeoutError("setup-fixture-ack-timeout")
            if acknowledged:
                break
            wait(5)
        # Ack proves fixture liveness, not that SendInput was delivered/rendered.
        # This sole post-ack sample is the baseline; never poll until visible.
        baseline = observe()
        if baseline.position != (int(point.x), int(point.y)):
            raise RuntimeError("setup-destination-unconfirmed")
        if (
            self.peer.identity is None
            or baseline.foreground != self.peer.identity[2]
            or baseline.process != self.peer.identity[0]
            or baseline.gui[0] & 0x1E
            or any(baseline.gui[index] for index in (3, 4, 5))
        ):
            raise RuntimeError("setup-gui-preservation-unconfirmed")
        return baseline
