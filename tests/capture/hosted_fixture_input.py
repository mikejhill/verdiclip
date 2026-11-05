"""Guarded native fixture preparation and click on disposable GitHub Windows only.

Not a Computer Use alternative for user desktops. No activation, access changes,
UIPI workaround, arbitrary input sequence, or click retry is provided.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

from tests.capture.native_cursor import CursorInfo
from tests.capture.production_matrix import DriverJournal
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point, Rect


def require_hosted(environment: Mapping[str, str]) -> None:
    """Reject local, self-hosted, foreign-repository and unpinned execution."""
    required = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
        "GITHUB_REPOSITORY": "mikejhill/verdiclip",
        "VERDICLIP_HOSTED_SCROLLBAR": "1",
    }
    if any(environment.get(key) != value for key, value in required.items()):
        raise RuntimeError("Native fixture input requires the explicit hosted-only job")
    head = environment.get("VERDICLIP_QUALIFICATION_HEAD", "")
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise RuntimeError("Hosted fixture source identity missing")
    if environment.get("VERDICLIP_HOSTED_SOURCE") != head:
        raise RuntimeError("Hosted fixture source identity mismatch")


def absolute_point(point: Point, desktop: Rect) -> tuple[int, int]:
    """Map an integral pixel to Windows absolute virtual-desktop coordinates."""
    if (
        desktop.width < 2
        or desktop.height < 2
        or not (
            desktop.x <= point.x < desktop.right
            and desktop.y <= point.y < desktop.bottom
        )
    ):
        raise ValueError("Click is outside the desktop")
    return (
        round((point.x - desktop.x) * 65535 / (desktop.width - 1)),
        round((point.y - desktop.y) * 65535 / (desktop.height - 1)),
    )


def absolute_pixel_center(point: Point, desktop: Rect) -> tuple[int, int]:
    """Choose the middle of a pixel bin, avoiding absolute-input edge rounding."""
    absolute_point(point, desktop)  # Retain the exact desktop bounds check.
    return (
        min(65535, int((point.x - desktop.x + 0.5) * 65536 / desktop.width)),
        min(65535, int((point.y - desktop.y + 0.5) * 65536 / desktop.height)),
    )


class MouseInput(ctypes.Structure):
    """Documented x64 MOUSEINPUT layout; checked before use."""

    _fields_ = [
        ("dx", w.LONG),
        ("dy", w.LONG),
        ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class InputPayload(ctypes.Union):
    """Mouse payload is the largest INPUT union member on Windows x64."""

    _fields_ = [("mouse", MouseInput)]


class Input(ctypes.Structure):
    """The Windows input discriminant plus correctly aligned union."""

    _fields_ = [("type", w.DWORD), ("payload", InputPayload)]


@dataclass(frozen=True)
class CursorPreparation:
    """Original preparation budget and live owned-fixture checks."""

    owned_capture: int
    preserved: Callable[[], bool]
    broker_idle: Callable[[], bool]
    expires_at: float


class HostedFixtureClick:
    """Discover exact owned windows, then send one native packet batch."""

    def __init__(self, peer: ScrollPeer, journal: DriverJournal) -> None:
        require_hosted(os.environ)
        self.peer, self.journal = peer, journal
        self.sent = False
        self.preparation_attempted = False
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.GetForegroundWindow.restype = w.HWND
        self.api.GetCapture.argtypes = ()
        self.api.GetCapture.restype = w.HWND
        self.api.GetCursorInfo.argtypes = (ctypes.POINTER(CursorInfo),)
        self.api.GetCursorInfo.restype = w.BOOL
        self.api.IsChild.argtypes = (w.HWND, w.HWND)
        self.api.IsChild.restype = w.BOOL
        self.api.IsWindowVisible.argtypes = (w.HWND,)
        self.api.IsWindowVisible.restype = w.BOOL
        self.api.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
        self.api.GetWindowTextW.restype = ctypes.c_int
        self.api.WindowFromPoint.argtypes = (w.POINT,)
        self.api.WindowFromPoint.restype = w.HWND
        self.api.SendInput.argtypes = (w.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.api.SendInput.restype = w.UINT
        self.api.GetSystemMetrics.argtypes = (ctypes.c_int,)
        self.api.GetSystemMetrics.restype = ctypes.c_int
        if ctypes.sizeof(Input) != 40:
            raise RuntimeError("Unexpected native INPUT layout")

    def process_id(self, handle: int) -> int:
        """Read ownership without opening a process or changing access."""
        pid = w.DWORD()
        if not self.api.GetWindowThreadProcessId(handle, ctypes.byref(pid)):
            raise RuntimeError("Owned window no longer exists")
        return pid.value

    def verify(self, target: Rect) -> None:
        """Every native operation remains inside this job's declared fixtures."""
        require_hosted(os.environ)
        if self.journal.retired or not self.peer.poll() or self.peer.target() != target:
            raise RuntimeError("Fixture retired or changed geometry")
        process = self.peer.process
        assert process is not None
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def collect(handle: int, _parameter: int) -> bool:
            if handle == self.peer.handle:
                found.append(handle)
            return True

        self.api.EnumWindows.argtypes = (callback_type, w.LPARAM)
        self.api.EnumWindows.restype = w.BOOL
        if not self.api.EnumWindows(callback_type(collect), 0) or found != [
            self.peer.handle
        ]:
            raise RuntimeError("Owned fixture not discovered by native enumeration")
        if self.process_id(
            self.peer.handle
        ) != process.pid or not self.api.IsWindowVisible(self.peer.handle):
            raise RuntimeError("Owned fixture visibility or process changed")
        title = ctypes.create_unicode_buffer(160)
        self.api.GetWindowTextW(self.peer.handle, title, len(title))
        if title.value != f"VerdiClip synthetic scroll fixture {self.peer.nonce}":
            raise RuntimeError("Owned fixture title identity changed")
        foreground = int(self.api.GetForegroundWindow() or 0)
        if foreground not in self.journal.owned_handles:
            raise RuntimeError("Foreground is not an owned fixture")
        point = target.center
        hit = int(self.api.WindowFromPoint(w.POINT(int(point.x), int(point.y))) or 0)
        if self.process_id(hit) not in (os.getpid(), process.pid):
            raise RuntimeError("Click point is covered by a foreign process")

    def prepare_cursor(
        self,
        target: Rect,
        *,
        scope: CursorPreparation,
        process_events: Callable[[], None],
        result: dict[str, object],
    ) -> None:
        """One owned-destination MOVE before binding, never a visibility claim."""
        result.update(outcome="not-inserted", preserved=False)
        if (
            self.preparation_attempted
            or self.sent
            or self.journal.binding is not None
            or self.journal.requests
        ):
            raise RuntimeError("Cursor preparation is not a fresh pre-binding step")
        point = Point(int(target.center.x), int(target.center.y))
        foreground = int(self.api.GetForegroundWindow() or 0)

        def guard() -> None:
            self.verify(target)
            hit = int(
                self.api.WindowFromPoint(w.POINT(int(point.x), int(point.y))) or 0
            )
            if not hit or not any(
                hit == root or self.api.IsChild(root, hit)
                for root in self.journal.owned_handles
            ):
                raise RuntimeError("Cursor destination is not an exact owned tree")
            if (
                scope.owned_capture not in self.journal.owned_handles
                or self.process_id(scope.owned_capture) != os.getpid()
                or int(self.api.GetCapture() or 0) != scope.owned_capture
                or int(self.api.GetForegroundWindow() or 0) != foreground
                or not scope.preserved()
                or not scope.broker_idle()
                or self.journal.binding is not None
                or self.journal.requests
            ):
                raise RuntimeError("Cursor preparation ownership or idle state changed")
            if (
                not math.isfinite(scope.expires_at)
                or not 0 < scope.expires_at - time.monotonic() <= 50
            ):
                raise RuntimeError("Original cursor preparation deadline expired")

        def observe() -> CursorInfo:
            info = CursorInfo()
            info.size = ctypes.sizeof(info)
            if not self.api.GetCursorInfo(ctypes.byref(info)):
                raise RuntimeError("Cursor preparation observation failed")
            return info

        guard()
        result["before_flags"] = int(observe().flags)
        desktop = Rect(*(self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)))
        x, y = absolute_pixel_center(point, desktop)
        packets = (Input * 1)(Input(0, InputPayload(MouseInput(x, y, 0, 0xC001, 0, 0))))
        guard()
        self.preparation_attempted = True
        result["outcome"] = "insertion-unconfirmed"
        inserted = self.api.SendInput(1, packets, ctypes.sizeof(Input))
        result["inserted"] = int(inserted)
        if inserted != 1:
            result["outcome"] = "insertion-refused"
            raise RuntimeError("Cursor preparation MOVE refused; no retry")
        result["outcome"] = "inserted-unverified"
        process_events()
        guard()
        after = observe()
        result["after_flags"] = int(after.flags)
        if (after.point.x, after.point.y) != (int(point.x), int(point.y)):
            raise RuntimeError("Cursor preparation did not reach its owned destination")
        result.update(outcome="inserted-preserved", preserved=True)

    def bind(self, target: Rect) -> None:
        """Native discovery supplies this hosted driver's acknowledgement."""
        self.verify(target)
        binding = self.journal.binding
        if binding is None:
            raise RuntimeError("No binding was offered")
        acknowledgement = dict(binding, phase="bound-no-input")
        (self.journal.directory / "driver-bound.json").write_text(
            json.dumps(acknowledgement), encoding="utf-8"
        )

    def click(
        self, target: Rect, *, lease_valid: Callable[[], bool], expires_at: float
    ) -> None:
        """No activation; denied or partial insertion fails without retry."""
        self.verify(target)
        if (
            self.sent
            or not self.journal.binding_ready(target)
            or len(self.journal.requests) != 1
        ):
            raise RuntimeError("Click is not a fresh armed instruction")
        request = self.journal.requests[0]
        if (
            request["phase"] != "input-now"
            or request["outcome"] != "scrollbar-click"
            or request["physical_rectangle"] != asdict(target)
        ):
            raise RuntimeError("Click instruction changed")
        self.sent = True
        desktop = Rect(*(self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)))
        x, y = absolute_point(target.center, desktop)
        packets = (Input * 3)(
            Input(0, InputPayload(MouseInput(x, y, 0, 0xC001, 0, 0))),
            Input(0, InputPayload(MouseInput(0, 0, 0, 0x0002, 0, 0))),
            Input(0, InputPayload(MouseInput(0, 0, 0, 0x0004, 0, 0))),
        )
        if (
            not math.isfinite(expires_at)
            or not 0 < expires_at - time.monotonic() <= 50
            or not lease_valid()
        ):
            raise RuntimeError("Original fixture deadline or live input lease expired")
        inserted = self.api.SendInput(3, packets, ctypes.sizeof(Input))
        if inserted == 2:
            release = Input(0, InputPayload(MouseInput(0, 0, 0, 0x0004, 0, 0)))
            if self.api.SendInput(1, ctypes.byref(release), ctypes.sizeof(Input)) != 1:
                raise RuntimeError("Partial input: button release unconfirmed")
        if inserted != 3:
            raise RuntimeError(
                f"Native input insertion failed ({inserted}/3); no click retry"
            )
