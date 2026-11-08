"""Research-only EDIT adapter reusing reviewed native ownership primitives.

No WPF constructor/discovery is called. No scrolling or restoration wrappers.
The private base ABI/resource helpers remain unchanged in production source.
"""

from __future__ import annotations

import ctypes as c
import math
import sys
import threading
import time
from dataclasses import asdict
from typing import Any, Never, cast, override

from verdiclip.capture.edit_geometry import DPI, ClientEvidence
from verdiclip.capture.edit_geometry import Rect as Rectangle
from verdiclip.capture.observe_native import (
    LastInput,
    NativeObservation,
    Point,
    Rect,
)
from verdiclip.capture.observe_protocol import ObserveTarget

CALL_SECONDS = 3
MAX_CHILDREN = 256


class ScrollInfo(c.Structure):
    """SDK SCROLLINFO: 32-bit scalar fields on Windows x64."""

    _fields_ = [
        ("size", c.c_uint32),
        ("mask", c.c_uint32),
        ("minimum", c.c_int32),
        ("maximum", c.c_int32),
        ("page", c.c_uint32),
        ("position", c.c_int32),
        ("track", c.c_int32),
    ]


class ScrollBarInfo(c.Structure):
    """SDK SCROLLBARINFO includes six state words."""

    _fields_ = [
        ("size", c.c_uint32),
        ("rect", Rect),
        ("line", c.c_int32),
        ("top", c.c_int32),
        ("bottom", c.c_int32),
        ("reserved", c.c_int32),
        ("state", c.c_uint32 * 6),
    ]


class ProbeBudget:
    """Strict supplied deadline and permanent first-failure latch."""

    def __init__(self, deadline: float, emit: Any) -> None:  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
        self.deadline, self.emit = deadline, emit
        self.failure: Exception | None = None
        self.last = time.monotonic()
        if (
            type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or not self.last < deadline <= self.last + 30
        ):
            raise ValueError("Invalid probe deadline")

    def fail(self, error: Exception) -> Never:
        """Preserve and raise the first failure."""
        if self.failure is None:
            self.failure = error
        raise self.failure

    def check(self) -> None:
        """Never admit work at/after the original deadline."""
        if self.failure is not None:
            raise self.failure
        now = time.monotonic()
        if not self.last <= now < self.deadline:
            self.fail(RuntimeError("Probe deadline/regressed clock"))
        self.last = now


class EditObservation(NativeObservation):
    """Own one exact child HWND and COM element for a fixed first-frame proof."""

    def __init__(self, target: ObserveTarget, child: int, budget: Any) -> None:  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
        self.target, self.child, self.budget = target, child, budget
        self.edit_emit = budget.emit
        self._thread = threading.get_ident()
        self._owners = []
        self._closed = False
        try:
            if sys.platform != "win32":
                raise OSError("EDIT research requires Windows")  # noqa: TRY301 - retain failure through mandatory cleanup
            self._apis()
            self._edit_apis()
            self._dpi()
            self.process = self.raw(self.kernel.OpenProcess, 0x00101000, 0, target.pid)
            if self.process:
                self.own(lambda: self._close_handle(self.process))
            self.boolean(self.process)
            self._process_guard()
            self._com_init()
            self.root = self._interface(self.uia, 6, (c.c_void_p,), target.hwnd)
            self.walker = self._interface(self.uia, 16, ())
            self.panel = self._interface(self.uia, 7, (Point,), Point(*target.point))
            self.by_handle = self._interface(self.uia, 6, (c.c_void_p,), child)
            if not self._same(self.panel, self.by_handle):
                raise ValueError("Selected element is not child HWND element")  # noqa: TRY301 - retain failure through mandatory cleanup
            self._association(self.panel)
            self.root_id, self.panel_id = (
                self.runtime_id(self.root),
                self.runtime_id(self.panel),
            )
            self.root_baseline, self.panel_baseline = (
                self.structure(self.root),
                self.structure(self.panel),
            )
            if (
                self.root_baseline["pid"] != target.pid
                or self.root_baseline["hwnd"] != target.hwnd
                or self.root_baseline["offscreen"]
                or self.panel_baseline["offscreen"]
            ):
                raise ValueError("UIA root/visibility association disagrees")  # noqa: TRY301 - initialization retains and releases every COM owner
            self.pattern = self._pattern(self.panel)  # exactly one capability lookup
            self.baseline = self.snapshot()
        except BaseException as error:
            self.close(error)
            raise

    @override
    def raw(self, function: Any, *args: object) -> Any:
        """Log each call; retain acquired owners before post-call admission.

        A slow call latches failure but returns its output so the caller can own
        it before boolean/HRESULT/the next call raises. No subsequent native
        read is admitted. Cleanup bypasses acquisition gates as in the base.
        """
        self.budget.check()
        started = time.monotonic()
        self.edit_emit(
            {"event": "call_start", "name": getattr(function, "__name__", "COM")}
        )
        self.budget.check()
        if time.monotonic() - started >= CALL_SECONDS:
            self.budget.fail(RuntimeError("Native start logging >=3s"))
        result = super().raw(function, *args)
        elapsed = time.monotonic() - started
        try:
            self.edit_emit({"event": "call_end", "seconds": elapsed})
        except Exception as error:  # noqa: BLE001 - preserve first failure and attempt all cleanup
            self.budget.failure = self.budget.failure or error
        elapsed = max(elapsed, time.monotonic() - started)
        if elapsed >= CALL_SECONDS:
            self.budget.failure = self.budget.failure or RuntimeError(
                "Native call >=3s"
            )
        return result

    def _edit_apis(self) -> None:
        for name, args, result in (
            ("GetClassNameW", [c.c_void_p, c.c_wchar_p, c.c_int32], c.c_int32),
            ("GetWindowLongPtrW", [c.c_void_p, c.c_int32], c.c_ssize_t),
            ("GetParent", [c.c_void_p], c.c_void_p),
            (
                "GetScrollInfo",
                [c.c_void_p, c.c_int32, c.POINTER(ScrollInfo)],
                c.c_int32,
            ),
            (
                "GetScrollBarInfo",
                [c.c_void_p, c.c_int32, c.POINTER(ScrollBarInfo)],
                c.c_int32,
            ),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result

    def _window_long(self, index: int) -> int:
        """Zero is valid for ex-style only when the native last-error is clear."""
        c.set_last_error(0)
        value = self.raw(self.user.GetWindowLongPtrW, self.child, index)
        error = c.get_last_error()
        if value == 0 and error:
            raise c.WinError(error)
        self.budget.check()
        return int(value)

    @override
    def _child_windows(self, panel: list[int]) -> None:
        """Only the selected exact child may intersect the capture region."""
        seen, errors = set(), []

        def visit(hwnd: int, _data: int) -> int:
            try:
                if hwnd in seen or len(seen) >= MAX_CHILDREN:
                    raise ValueError("Child enumeration bound/cycle")  # noqa: TRY301 - retain failure through mandatory cleanup
                seen.add(hwnd)
                if (
                    hwnd != self.child
                    and self._intersects(hwnd, panel)
                    and self.raw(self.user.IsWindowVisible, hwnd)
                ):
                    raise ValueError("Intersecting child overlay")  # noqa: TRY301 - retain failure through mandatory cleanup
            except Exception as error:  # noqa: BLE001 - preserve first failure and attempt all cleanup
                errors.append(error)
                return 0
            return 1

        self.raw(
            self.user.EnumChildWindows, self.target.hwnd, self.window_callback(visit), 0
        )
        if errors:
            self.budget.fail(errors[0])
        self.budget.check()

    @override
    def snapshot(self) -> dict[str, Any]:  # noqa: C901 - single bounded ownership lifecycle
        """Guard complete own-client geometry and retained identity, no writes."""
        self._process_guard()
        self._association(self.panel)
        if (
            self.runtime_id(self.panel) != self.panel_id
            or self.runtime_id(self.root) != self.root_id
            or self.structure(self.panel) != self.panel_baseline
            or self.structure(self.root) != self.root_baseline
        ):
            raise ValueError("Retained UIA identity changed")
        if self.raw(
            self.user.GetForegroundWindow
        ) != self.target.hwnd or not self._visible(self.target.hwnd):
            raise ValueError("Root foreground/visibility changed")
        if self.raw(self.user.GetDpiForWindow, self.target.hwnd) != DPI:
            raise ValueError("Root DPI changed")
        pid = c.c_uint32()
        self.boolean(
            self.raw(self.user.GetWindowThreadProcessId, self.child, c.byref(pid))
        )
        if pid.value != self.target.pid:
            raise ValueError("Child owner changed")
        name = c.create_unicode_buffer(64)
        self.boolean(self.raw(self.user.GetClassNameW, self.child, name, len(name)))
        local, window = Rect(), Rect()
        self.boolean(self.raw(self.user.GetClientRect, self.child, c.byref(local)))
        self.boolean(self.raw(self.user.GetWindowRect, self.child, c.byref(window)))
        first, last = Point(local.left, local.top), Point(local.right, local.bottom)
        for point in (first, last):
            self.boolean(self.raw(self.user.ClientToScreen, self.child, c.byref(point)))
        bars, scroll = [], []
        for object_id, axis in ((-6, 0), (-5, 1)):
            bar = ScrollBarInfo()
            bar.size = c.sizeof(bar)
            self.boolean(
                self.raw(
                    self.user.GetScrollBarInfo, self.child, object_id, c.byref(bar)
                )
            )
            if bar.state[0] & (0x8000 | 0x10000):
                raise ValueError("Invisible/offscreen standard scrollbar")
            info = ScrollInfo()
            info.size, info.mask = c.sizeof(info), 0x17  # fixed SIF_ALL
            self.boolean(
                self.raw(self.user.GetScrollInfo, self.child, axis, c.byref(info))
            )
            if info.position != 0:
                raise ValueError("Fixture initial scroll pose is not top-left")
            bars.append(tuple(bar.rect.values()))
            scroll.append(
                [
                    info.minimum,
                    info.maximum,
                    info.page,
                    info.position,
                    info.track,
                    *bar.state,
                ]
            )
        evidence = ClientEvidence(
            self.child,
            self.target.hwnd,
            pid.value,
            self.target.process_created_100ns,
            self.panel_baseline["hwnd"],
            self.panel_baseline["pid"],
            self._same(self.panel, self.by_handle),
            self.raw(self.user.GetParent, self.child),
            name.value,
            self._window_long(-16),
            self._window_long(-20),
            self.raw(self.user.GetDpiForWindow, self.child),
            cast("Rectangle", tuple(local.values())),
            (first.x, first.y, last.x, last.y),
            cast("Rectangle", tuple(window.values())),
            cast("Rectangle", tuple(self._client_rect())),
            bars[0],
            bars[1],
        )
        client = evidence.admit()
        self._occlusion(list(client))
        for x, y in (
            (client[0], client[1]),
            (client[2] - 1, client[3] - 1),
            self.target.point,
        ):
            if self.raw(self.user.WindowFromPoint, Point(x, y)) != self.child:
                raise ValueError("Client point ownership changed")
        tick = LastInput(c.sizeof(LastInput), 0)
        self.boolean(self.raw(self.user.GetLastInputInfo, c.byref(tick)))
        pattern = (
            None
            if self.pattern is None
            else [self.pattern.read(slot, c.c_double).value for slot in range(5, 9)]
            + [self.pattern.read(slot, c.c_int32).value for slot in (9, 10)]
        )
        if pattern is not None and (
            any(not math.isfinite(value) for value in pattern[:4])
            or any(value not in (0, 1) for value in pattern[4:])
        ):
            raise ValueError("Invalid ScrollPattern capability state")
        result = {
            "native": asdict(evidence),
            "scroll": scroll,
            "scroll_pattern": pattern,
            "input_tick": tick.tick,
            "monitors": self._monitors(),
        }
        self.budget.check()
        if hasattr(self, "baseline") and result != self.baseline:
            raise ValueError("EDIT guard/layout/state changed")
        return result


class RootReference(EditObservation):
    """Independent full-root Win32 reference: no UIA or content geometry lookup."""

    def __init__(self, target: ObserveTarget, child: int, budget: Any) -> None:  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
        self.target, self.child, self.budget = target, child, budget
        self.edit_emit = budget.emit
        self._thread = threading.get_ident()
        self._owners = []
        self._closed = False
        try:
            self._apis()
            self._dpi()
            self.process = self.raw(self.kernel.OpenProcess, 0x00101000, 0, target.pid)
            if self.process:
                self.own(lambda: self._close_handle(self.process))
            self.boolean(self.process)
            self.baseline = self.snapshot()
        except BaseException as error:
            self.close(error)
            raise

    @override
    def snapshot(self) -> dict[str, Any]:
        """Retain root/client identity and full-root raster placement only."""
        self._process_guard()
        if self.raw(
            self.user.GetForegroundWindow
        ) != self.target.hwnd or not self._visible(self.target.hwnd):
            raise ValueError("Reference root not foreground/visible")
        if self.raw(self.user.GetDpiForWindow, self.target.hwnd) != DPI:
            raise ValueError("Reference DPI changed")
        root = self._client_rect()
        self._occlusion(root)
        tick = LastInput(c.sizeof(LastInput), 0)
        self.boolean(self.raw(self.user.GetLastInputInfo, c.byref(tick)))
        result = {
            "root_rect": root,
            "input_tick": tick.tick,
            "monitors": self._monitors(),
        }
        self.budget.check()
        if hasattr(self, "baseline") and result != self.baseline:
            raise ValueError("Reference state changed")
        return result
