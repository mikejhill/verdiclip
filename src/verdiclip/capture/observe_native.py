"""Unused read-only ctypes adapter for the owned-WPF one-observation proof.

ABI: Microsoft WinSDK UIAutomationClient.h (IUnknown occupies slots 0..2):
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/UIAutomationClient.h
IUIAutomation: CompareElements=3, ElementFromHandle=6, ElementFromPoint=7,
RawViewWalker=16. Element: GetRuntimeId=4, GetCurrentPatternAs=14, PID=20,
HWND=36, IsOffscreen=38, FrameworkId=40, BoundingRectangle=43. Walker:
GetParentElement=3. ScrollPattern: read-only getters=5..10; NO write wrappers.
HRESULT/BOOL/LONG/RECT/POINT are explicitly 32 bit; HWND/interfaces are pointers.
SAFEARRAY is zero-based one-dimensional VT_I4; BSTR uses SysStringLen/FreeString.
DWM DWMWA_CLOAKED=14 uses DWORD. DPI context -4 is per-monitor-v2.

All native calls belong to one child thread. Deadlines cannot interrupt COM or
cleanup: an external owned-process watchdog remains mandatory. Same-process
association and fixture-overlay assumptions do not qualify general WPF/Chromium.
"""

from __future__ import annotations

import ctypes as c
import math
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Never

from verdiclip.capture.observe_protocol import (
    OBSERVE_SECONDS,
    ObservationError,
    ObserveTarget,
)

_I32 = c.c_int32
_U32 = c.c_uint32
_PTR = c.c_void_p
_MAX_ANCESTORS = 32
_MAX_WINDOWS = 256
_MAX_MONITORS = 8
_MAX_BSTR = 256
_VT_I4 = 3
_FIXTURE_DPI = 96
_WAIT_TIMEOUT = 258
_UNSUPPORTED = 0x80040204


class Point(c.Structure):
    """Win32 POINT, including signed physical negative desktop coordinates."""

    _fields_ = [("x", _I32), ("y", _I32)]


class Rect(c.Structure):
    """Win32/UIA RECT, not a rectangle of doubles."""

    _fields_ = [(key, _I32) for key in ("left", "top", "right", "bottom")]

    def values(self) -> list[int]:
        """Copy native physical edges into detached metadata."""
        return [self.left, self.top, self.right, self.bottom]


class Guid(c.Structure):
    """Windows little-endian in-memory GUID representation."""

    _fields_ = [("data", c.c_ubyte * 16)]

    @classmethod
    def parse(cls, text: str) -> Guid:
        """Decode a fixed SDK IID/CLSID, never a caller-selected provider."""
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class FileTime(c.Structure):
    """Unsigned 64-bit creation timestamp split into SDK DWORDs."""

    _fields_ = [("low", _U32), ("high", _U32)]


class LastInput(c.Structure):
    """SDK LASTINPUTINFO; input is observed, never synthesized."""

    _fields_ = [("size", _U32), ("tick", _U32)]


class MonitorInfo(c.Structure):
    """SDK MONITORINFO, explicitly sized independent of host C long."""

    _fields_ = [("size", _U32), ("monitor", Rect), ("work", Rect), ("flags", _U32)]


class ObserveBudget:
    """Original operation deadline and strict cancellation, permanently latched."""

    def __init__(
        self, deadline: float, clock: Callable[[], float], cancelled: Callable[[], bool]
    ) -> None:
        self.clock, self.cancelled = clock, cancelled
        self.failure: Exception | None = None
        now = self._time()
        if (
            type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or not now < deadline <= now + OBSERVE_SECONDS
        ):
            raise ObservationError("Invalid original observe deadline")
        self.deadline, self.last = deadline, now
        self.check()

    def fail(self, error: Exception) -> Never:
        """Preserve the first error and prohibit all further evidence reads."""
        if self.failure is None:
            self.failure = error
        raise self.failure

    def _time(self) -> float:
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ObservationError("Invalid monotonic clock")
        return float(value)

    def check(self) -> None:
        """Check before/after native work; cleanup is deliberately exempt."""
        if self.failure is not None:
            raise self.failure
        try:
            cancelled = self.cancelled()
            if type(cancelled) is not bool:
                self.fail(ObservationError("Invalid cancellation callback"))
            if cancelled:
                self.fail(ObservationError("Observe cancelled"))
            now = self._time()
            if now < self.last or now >= self.deadline:
                self.fail(
                    ObservationError("Observe deadline expired or clock regressed")
                )
            self.last = now
        except Exception as error:  # noqa: BLE001 - callback errors must latch
            self.fail(error)


@dataclass
class _Owner:
    release: Callable[[], object]
    live: bool = True

    def close(self) -> None:
        if self.live:
            self.live = False
            self.release()


class _Com:
    """A retained interface; only fixed call sites choose vtable slots."""

    def __init__(self, native: NativeObservation, pointer: c.c_void_p) -> None:
        self.native, self.pointer = native, pointer
        self.owner = native.own(self._release)

    def _function(
        self, slot: int, result: type[c._CData], arguments: tuple[type[c._CData], ...]
    ) -> Callable[..., Any]:
        table = c.cast(self.pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[slot])

    def _release(self) -> object:
        function = self._function(2, _U32, ())
        return function(self.pointer)

    def call(self, slot: int, types: tuple[type[c._CData], ...], *args: object) -> int:
        if not self.owner.live:
            self.native.budget.fail(ObservationError("Released COM interface"))
        self.native.budget.check()
        return int(
            self.native.raw(self._function(slot, _I32, types), self.pointer, *args)
        )

    def read[T: c._CData](self, slot: int, kind: type[T]) -> T:
        result = kind()
        status = self.call(slot, (c.POINTER(kind),), c.byref(result))
        self.native.hresult(status)
        return result


class NativeObservation:
    """Own all resources before post-call checks can throw, on one child thread."""

    def __init__(self, target: ObserveTarget, budget: ObserveBudget) -> None:
        self.target, self.budget = target, budget
        self._thread = threading.get_ident()
        self._owners: list[_Owner] = []
        self._closed = False
        try:
            if sys.platform != "win32":
                self.budget.fail(OSError("Owned WPF observation requires Windows"))
            self._apis()
            self._dpi()
            self.process = self.raw(self.kernel.OpenProcess, 0x00101000, 0, target.pid)
            if self.process:
                self.own(lambda: self._close_handle(self.process))
            self.boolean(self.process)
            self._process_guard()
            self._com_init()
            self.root = self._interface(self.uia, 6, (_PTR,), target.hwnd)
            self.walker = self._interface(self.uia, 16, ())
            self.root_baseline = self.structure(self.root)
            if (
                self.root_baseline["pid"] != target.pid
                or self.root_baseline["hwnd"] != target.hwnd
                or self.root_baseline["framework"] != "WPF"
                or self.root_baseline["offscreen"]
            ):
                self.budget.fail(ObservationError("Invalid owned fixture UIA root"))
            self.root_id = self.runtime_id(self.root)
            self.panel, self.pattern = self._discover()
            self.panel_baseline = self.structure(self.panel)
            self.panel_id = self.runtime_id(self.panel)
            self.budget.check()
        except BaseException as error:
            self.close(error)
            raise

    def _check_thread(self) -> None:
        if threading.get_ident() != self._thread:
            raise ObservationError("Native observation has different thread affinity")

    def own(self, release: Callable[[], object]) -> _Owner:
        """Register immediately, before checking acquisition status or callbacks."""
        self._check_thread()
        if self._closed:
            self.budget.fail(ObservationError("Native observation is closed"))
        owner = _Owner(release)
        self._owners.append(owner)
        return owner

    def close(self, primary: BaseException | None = None) -> None:
        """Attempt every owner in reverse order, retaining the original failure."""
        self._check_thread()
        if self._closed:
            return
        self._closed = True
        failure = primary
        for owner in reversed(self._owners):
            try:
                owner.close()
            except BaseException as error:  # noqa: BLE001 - balance every owner/apartment
                if failure is None:
                    failure = error
                else:
                    BaseException.add_note(
                        failure, f"Observation cleanup failed: {type(error).__name__}"
                    )
        self._owners.clear()
        if failure is not None and primary is None:
            raise failure

    def raw(self, function: Callable[..., Any], *args: object) -> Any:  # noqa: ANN401 - fixed native ABI boundary
        """No post-check here: acquisition callers must first retain their output."""
        self._check_thread()
        if self._closed:
            self.budget.fail(ObservationError("Native observation is closed"))
        self.budget.check()
        try:
            return function(*args)
        except Exception as error:  # noqa: BLE001 - native callback failure is terminal
            self.budget.fail(error)

    def boolean(self, result: object) -> None:
        """Read GetLastError immediately, before another native call or cleanup."""
        if not result:
            self.budget.fail(c.WinError(c.get_last_error()))
        self.budget.check()

    def hresult(self, status: int) -> None:
        """Owned outputs must already be registered when HRESULT is admitted."""
        if status < 0:
            self.budget.fail(OSError(f"Native HRESULT 0x{status & 0xFFFFFFFF:08x}"))
        self.budget.check()

    def _apis(self) -> None:
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.user = c.WinDLL("user32", use_last_error=True)
        self.dwm = c.WinDLL("dwmapi", use_last_error=True)
        self.ole = c.WinDLL("ole32", use_last_error=True)
        self.auto = c.WinDLL("oleaut32", use_last_error=True)
        self.monitor_callback = c.WINFUNCTYPE(
            _I32, _PTR, _PTR, c.POINTER(Rect), c.c_ssize_t
        )
        self.window_callback = c.WINFUNCTYPE(_I32, _PTR, c.c_ssize_t)
        signatures = {
            "kernel": {
                "OpenProcess": ([_U32, _I32, _U32], _PTR),
                "CloseHandle": ([_PTR], _I32),
                "WaitForSingleObject": ([_PTR, _U32], _U32),
                "GetProcessTimes": ([_PTR, *([c.POINTER(FileTime)] * 4)], _I32),
            },
            "user": {
                "SetProcessDpiAwarenessContext": ([_PTR], _I32),
                "GetThreadDpiAwarenessContext": ([], _PTR),
                "AreDpiAwarenessContextsEqual": ([_PTR, _PTR], _I32),
                "GetDpiForWindow": ([_PTR], _U32),
                "GetWindowThreadProcessId": ([_PTR, c.POINTER(_U32)], _U32),
                "GetForegroundWindow": ([], _PTR),
                "GetAncestor": ([_PTR, _U32], _PTR),
                "WindowFromPoint": ([Point], _PTR),
                "GetClientRect": ([_PTR, c.POINTER(Rect)], _I32),
                "ClientToScreen": ([_PTR, c.POINTER(Point)], _I32),
                "GetLastInputInfo": ([c.POINTER(LastInput)], _I32),
                "GetTopWindow": ([_PTR], _PTR),
                "GetWindow": ([_PTR, _U32], _PTR),
                "GetWindowRect": ([_PTR, c.POINTER(Rect)], _I32),
                "IsWindowVisible": ([_PTR], _I32),
                "IsIconic": ([_PTR], _I32),
                "EnumChildWindows": ([_PTR, self.window_callback, c.c_ssize_t], _I32),
                "EnumDisplayMonitors": (
                    [_PTR, _PTR, self.monitor_callback, c.c_ssize_t],
                    _I32,
                ),
                "GetMonitorInfoW": ([_PTR, c.POINTER(MonitorInfo)], _I32),
            },
            "dwm": {"DwmGetWindowAttribute": ([_PTR, _U32, _PTR, _U32], _I32)},
            "ole": {
                "CoInitializeEx": ([_PTR, _U32], _I32),
                "CoUninitialize": ([], None),
                "CoCreateInstance": (
                    [c.POINTER(Guid), _PTR, _U32, c.POINTER(Guid), c.POINTER(_PTR)],
                    _I32,
                ),
            },
            "auto": {
                "SysStringLen": ([_PTR], _U32),
                "SysFreeString": ([_PTR], None),
                "SafeArrayGetDim": ([_PTR], _U32),
                "SafeArrayGetElemsize": ([_PTR], _U32),
                "SafeArrayGetVartype": ([_PTR, c.POINTER(c.c_uint16)], _I32),
                "SafeArrayGetLBound": ([_PTR, _U32, c.POINTER(_I32)], _I32),
                "SafeArrayGetUBound": ([_PTR, _U32, c.POINTER(_I32)], _I32),
                "SafeArrayGetElement": ([_PTR, c.POINTER(_I32), _PTR], _I32),
                "SafeArrayDestroy": ([_PTR], _I32),
            },
        }
        for library, entries in signatures.items():
            for name, (arguments, result) in entries.items():
                function = getattr(getattr(self, library), name)
                function.argtypes, function.restype = arguments, result

    def _dpi(self) -> None:
        self.raw(self.user.SetProcessDpiAwarenessContext, _PTR(-4))
        context = self.raw(self.user.GetThreadDpiAwarenessContext)
        equal = self.raw(self.user.AreDpiAwarenessContextsEqual, context, _PTR(-4))
        if not equal:
            self.budget.fail(ObservationError("PMv2 DPI context not established"))
        self.budget.check()

    def _close_handle(self, handle: int) -> None:
        if not self.kernel.CloseHandle(handle):
            raise c.WinError(c.get_last_error())

    def _process_guard(self) -> None:
        wait = self.raw(self.kernel.WaitForSingleObject, self.process, 0)
        if wait != _WAIT_TIMEOUT:
            self.budget.fail(ObservationError("Pinned target is not live"))
        times = [FileTime() for _ in range(4)]
        self.boolean(
            self.raw(
                self.kernel.GetProcessTimes, self.process, *(c.byref(t) for t in times)
            )
        )
        creation = times[0].low | (times[0].high << 32)
        pid = _U32()
        self.boolean(
            self.raw(self.user.GetWindowThreadProcessId, self.target.hwnd, c.byref(pid))
        )
        root = self.raw(self.user.GetAncestor, self.target.hwnd, 2)
        if (
            creation != self.target.process_created_100ns
            or pid.value != self.target.pid
            or root != self.target.hwnd
        ):
            self.budget.fail(ObservationError("Pinned target/window identity changed"))
        self.budget.check()

    def _com_init(self) -> None:
        status = self.raw(self.ole.CoInitializeEx, None, 0)
        if status >= 0:
            self.own(self.ole.CoUninitialize)
        self.hresult(status)
        pointer = _PTR()
        status = self.raw(
            self.ole.CoCreateInstance,
            c.byref(Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
            None,
            1,
            c.byref(Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
            c.byref(pointer),
        )
        reference = _Com(self, pointer) if pointer.value is not None else None
        self.hresult(status)
        if reference is None:
            self.budget.fail(ObservationError("Null UIA factory"))
        self.uia = reference

    def _interface(
        self, source: _Com, slot: int, types: tuple[type[c._CData], ...], *args: object
    ) -> _Com:
        pointer = _PTR()
        status = source.call(slot, (*types, c.POINTER(_PTR)), *args, c.byref(pointer))
        reference = _Com(self, pointer) if pointer.value is not None else None
        self.hresult(status)
        if reference is None:
            self.budget.fail(ObservationError("Unexpected null UIA interface"))
        return reference

    def _parent(self, element: _Com) -> _Com:
        return self._interface(self.walker, 3, (_PTR,), element.pointer)

    def _same(self, first: _Com, second: _Com) -> bool:
        result = _I32()
        self.hresult(
            self.uia.call(
                3,
                (_PTR, _PTR, c.POINTER(_I32)),
                first.pointer,
                second.pointer,
                c.byref(result),
            )
        )
        return bool(result.value)

    def _pattern(self, element: _Com) -> _Com | None:
        pointer = _PTR()
        status = element.call(
            14,
            (_I32, c.POINTER(Guid), c.POINTER(_PTR)),
            10004,
            c.byref(Guid.parse("88f4d42a-e881-459d-a77c-73bbbb7e02dc")),
            c.byref(pointer),
        )
        reference = _Com(self, pointer) if pointer.value is not None else None
        if status & 0xFFFFFFFF == _UNSUPPORTED:
            if reference is not None:
                reference.owner.close()
            self.budget.check()
            return None
        self.hresult(status)
        return reference

    def _bstr(self, element: _Com) -> str:
        pointer = _PTR()
        status = element.call(40, (c.POINTER(_PTR),), c.byref(pointer))
        owner = (
            self.own(lambda: self.auto.SysFreeString(pointer))
            if pointer.value is not None
            else None
        )
        self.hresult(status)
        try:
            length = self.raw(self.auto.SysStringLen, pointer)
            if length > _MAX_BSTR:
                self.budget.fail(ObservationError("Framework string exceeds bound"))
            result = c.wstring_at(pointer, length) if pointer.value is not None else ""
            self.budget.check()
            return result
        finally:
            if owner is not None:
                self._release_now(owner)

    def _release_now(self, owner: _Owner) -> None:
        primary = sys.exception()
        try:
            owner.close()
        except BaseException as error:
            if primary is not None:
                BaseException.add_note(
                    primary, f"Observation cleanup failed: {type(error).__name__}"
                )
            else:
                raise

    def _destroy_array(self, pointer: c.c_void_p) -> None:
        status = self.auto.SafeArrayDestroy(pointer)
        if status < 0:
            raise OSError("SafeArrayDestroy failed")

    def runtime_id(self, element: _Com) -> tuple[int, ...]:
        """Read a bounded zero-based VT_I4 runtime ID, always destroying its array."""
        pointer = _PTR()
        status = element.call(4, (c.POINTER(_PTR),), c.byref(pointer))
        owner = (
            self.own(lambda: self._destroy_array(pointer))
            if pointer.value is not None
            else None
        )
        self.hresult(status)
        if pointer.value is None:
            self.budget.fail(ObservationError("Null runtime ID array"))
        try:
            dimensions = self.raw(self.auto.SafeArrayGetDim, pointer)
            element_size = self.raw(self.auto.SafeArrayGetElemsize, pointer)
            kind, low, high = c.c_uint16(), _I32(), _I32()
            self.hresult(
                self.raw(self.auto.SafeArrayGetVartype, pointer, c.byref(kind))
            )
            self.hresult(
                self.raw(self.auto.SafeArrayGetLBound, pointer, 1, c.byref(low))
            )
            self.hresult(
                self.raw(self.auto.SafeArrayGetUBound, pointer, 1, c.byref(high))
            )
            if (
                dimensions != 1
                or element_size != c.sizeof(_I32)
                or kind.value != _VT_I4
                or low.value != 0
                or not 0 <= high.value < _MAX_ANCESTORS
            ):
                self.budget.fail(ObservationError("Invalid bounded runtime ID array"))
            result = []
            for index in range(high.value + 1):
                item = _I32()
                self.hresult(
                    self.raw(
                        self.auto.SafeArrayGetElement,
                        pointer,
                        c.byref(_I32(index)),
                        c.byref(item),
                    )
                )
                result.append(item.value)
            return tuple(result)
        finally:
            if owner is not None:
                self._release_now(owner)

    def structure(self, element: _Com) -> dict[str, Any]:
        """Read structural evidence only, never names, text or arbitrary properties."""
        return {
            "pid": element.read(20, _I32).value,
            "hwnd": element.read(36, _PTR).value or 0,
            "offscreen": bool(element.read(38, _I32).value),
            "framework": self._bstr(element),
            "rect": element.read(43, Rect).values(),
        }

    def _association(self, element: _Com) -> None:
        seen: set[tuple[int, ...]] = set()
        current = element
        for _ in range(_MAX_ANCESTORS):
            identity = self.runtime_id(current)
            if identity in seen:
                self.budget.fail(ObservationError("Cyclic UIA ancestry"))
            seen.add(identity)
            if self.structure(current)["pid"] != self.target.pid:
                self.budget.fail(ObservationError("Fixture UIA ownership changed"))
            if self._same(current, self.root):
                return
            current = self._parent(current)
        self.budget.fail(ObservationError("UIA ancestry exceeds bound"))

    def _discover(self) -> tuple[_Com, _Com]:
        element = self._interface(self.uia, 7, (Point,), Point(*self.target.point))
        seen: set[tuple[int, ...]] = set()
        for _ in range(_MAX_ANCESTORS):
            identity = self.runtime_id(element)
            if identity in seen:
                self.budget.fail(ObservationError("Cyclic UIA discovery"))
            seen.add(identity)
            structure = self.structure(element)
            if structure["pid"] != self.target.pid or structure["framework"] != "WPF":
                self.budget.fail(ObservationError("Unsupported owned WPF fixture"))
            pattern = self._pattern(element)
            if pattern is not None and bool(pattern.read(10, _I32).value):
                self._association(element)
                return element, pattern
            if self._same(element, self.root):
                self.budget.fail(ObservationError("No fixture scroll panel"))
            element = self._parent(element)
        return self.budget.fail(ObservationError("UIA discovery exceeds bound"))

    def _client_rect(self) -> list[int]:
        rect = Rect()
        self.boolean(self.raw(self.user.GetClientRect, self.target.hwnd, c.byref(rect)))
        first, last = Point(rect.left, rect.top), Point(rect.right, rect.bottom)
        for point in (first, last):
            self.boolean(
                self.raw(self.user.ClientToScreen, self.target.hwnd, c.byref(point))
            )
        return [first.x, first.y, last.x, last.y]

    def _monitors(self) -> list[list[int | bool]]:
        entries: list[list[int | bool]] = []
        errors: list[Exception] = []

        def visit(handle: int, _dc: int, _rect: object, _data: int) -> int:
            try:
                if len(entries) >= _MAX_MONITORS:
                    self.budget.fail(
                        ObservationError("Monitor enumeration exceeds bound")
                    )
                info = MonitorInfo()
                info.size = c.sizeof(info)
                self.boolean(self.raw(self.user.GetMonitorInfoW, handle, c.byref(info)))
                entries.append([handle, *info.monitor.values(), bool(info.flags & 1)])
            except Exception as error:  # noqa: BLE001 - never escape a ctypes callback
                errors.append(error)
                return 0
            else:
                return 1

        success = self.raw(
            self.user.EnumDisplayMonitors, None, None, self.monitor_callback(visit), 0
        )
        if errors:
            self.budget.fail(errors[0])
        self.boolean(success)
        return sorted(entries, key=lambda entry: int(entry[0]))

    def _visible(self, hwnd: int) -> bool:
        visible = self.raw(self.user.IsWindowVisible, hwnd)
        minimized = self.raw(self.user.IsIconic, hwnd)
        if not visible or minimized:
            return False
        cloaked = _U32()
        self.hresult(
            self.raw(
                self.dwm.DwmGetWindowAttribute,
                hwnd,
                14,
                c.byref(cloaked),
                c.sizeof(cloaked),
            )
        )
        return not cloaked.value

    def _intersects(self, hwnd: int, panel: list[int]) -> bool:
        rect = Rect()
        self.boolean(self.raw(self.user.GetWindowRect, hwnd, c.byref(rect)))
        if rect.left >= rect.right or rect.top >= rect.bottom:
            self.budget.fail(ObservationError("Indeterminate window geometry"))
        return bool(
            rect.left < panel[2]
            and panel[0] < rect.right
            and rect.top < panel[3]
            and panel[1] < rect.bottom
        )

    def _occlusion(self, panel: list[int]) -> None:
        seen: set[int] = set()
        hwnd = self.raw(self.user.GetTopWindow, None)
        while hwnd != self.target.hwnd:
            if not hwnd or hwnd in seen or len(seen) >= _MAX_WINDOWS:
                self.budget.fail(
                    ObservationError("Root absent or cyclic/oversized Z-order")
                )
            seen.add(hwnd)
            if self._visible(hwnd) and self._intersects(hwnd, panel):
                self.budget.fail(
                    ObservationError("External window intersects full panel")
                )
            hwnd = self.raw(self.user.GetWindow, hwnd, 2)
        self.budget.check()
        self._child_windows(panel)

    def _child_windows(self, panel: list[int]) -> None:
        visited: set[int] = set()
        errors: list[Exception] = []

        def visit(hwnd: int, _data: int) -> int:
            try:
                if hwnd in visited or len(visited) >= _MAX_WINDOWS:
                    self.budget.fail(
                        ObservationError("Child HWND enumeration exceeds bound")
                    )
                visited.add(hwnd)
                if self._intersects(hwnd, panel) and self._visible(hwnd):
                    self.budget.fail(
                        ObservationError("Native child HWND covers fixture panel")
                    )
            except Exception as error:  # noqa: BLE001 - keep errors outside ctypes
                errors.append(error)
                return 0
            else:
                return 1

        # EnumChildWindows has no meaningful return value; callback errors latch.
        self.raw(
            self.user.EnumChildWindows, self.target.hwnd, self.window_callback(visit), 0
        )
        if errors:
            self.budget.fail(errors[0])
        self.budget.check()

    def snapshot(self) -> dict[str, object]:
        """Read exact guards without reacquisition, writes or visibility overclaims."""
        self._process_guard()
        self._association(self.panel)
        if (
            self.structure(self.root) != self.root_baseline
            or self.runtime_id(self.root) != self.root_id
        ):
            self.budget.fail(ObservationError("Root UIA identity changed"))
        structure = self.structure(self.panel)
        if (
            structure != self.panel_baseline
            or self.runtime_id(self.panel) != self.panel_id
            or structure["offscreen"]
        ):
            self.budget.fail(ObservationError("Panel UIA identity/geometry changed"))
        foreground = self.raw(self.user.GetForegroundWindow)
        if foreground != self.target.hwnd or not self._visible(self.target.hwnd):
            self.budget.fail(ObservationError("Fixture root is not visible foreground"))
        dpi = self.raw(self.user.GetDpiForWindow, self.target.hwnd)
        if dpi != _FIXTURE_DPI:
            self.budget.fail(ObservationError("Initial fixture requires 96 DPI"))
        tick = LastInput(c.sizeof(LastInput), 0)
        self.boolean(self.raw(self.user.GetLastInputInfo, c.byref(tick)))
        panel = structure["rect"]
        left, top, right, bottom = panel
        points = [
            self.target.point,
            (left, top),
            (right - 1, top),
            (left, bottom - 1),
            (right - 1, bottom - 1),
            ((left + right) // 2, (top + bottom) // 2),
        ]
        for point in points:
            hit = self.raw(self.user.WindowFromPoint, Point(*point))
            root = self.raw(self.user.GetAncestor, hit, 2)
            if root != self.target.hwnd:
                self.budget.fail(ObservationError("Full panel point ownership changed"))
        self._occlusion(panel)
        scroll = [self.pattern.read(slot, c.c_double).value for slot in range(5, 9)]
        horizontal, vertical = (
            bool(self.pattern.read(slot, _I32).value) for slot in (9, 10)
        )
        result = {
            "target": {
                **self.target.value(),
                "runtime_id": list(self.panel_id),
                "framework": "WPF",
            },
            "guard": {
                "foreground_hwnd": foreground,
                "foreground_pid": self.target.pid,
                "input_tick": tick.tick,
                "root_client_rect": self._client_rect(),
                "target_rect": panel,
                "capture_rect": panel,
                "dpi": [dpi, dpi],
                "monitors": self._monitors(),
                "visibility_evidence": {
                    "scope": "owned-wpf-fixture-external-check-v1",
                    "root_visible": True,
                    "root_minimized": False,
                    "root_cloaked": False,
                    "external_intersection_count": 0,
                    "point_root_checks": 6,
                    "internal_overlay_assessment": "fixture-assumption-only",
                },
            },
            "scroll": dict(
                zip(
                    (
                        "horizontal",
                        "vertical",
                        "horizontal_view",
                        "vertical_view",
                        "horizontal_scrollable",
                        "vertical_scrollable",
                    ),
                    [*scroll, horizontal, vertical],
                    strict=True,
                )
            ),
        }
        self.budget.check()
        return result
