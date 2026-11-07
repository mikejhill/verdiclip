"""Experimental windowless MTA UIA adapter, exclusively for supervised probes.

ABI source: Microsoft WinSDK UIAutomationClient.h:
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/UIAutomationClient.h
Vtable indexes include the three IUnknown entries.

No timeout can interrupt a COM provider. Run this in a disposable process with an
external hard deadline. This module never injects code or sends native input.
"""

from __future__ import annotations

import ctypes as c
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass

MAX_ANCESTORS = 32
MAX_CHILDREN = 64
MAX_FRAMEWORK_CHARS = 256
SCROLL_IID = "88f4d42a-e881-459d-a77c-73bbbb7e02dc"
FULL_PERCENT = 100
UNSUPPORTED = 0x80040204


class Guid(c.Structure):
    """Native GUID with Windows byte ordering."""

    _fields_ = [("data", c.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> Guid:
        """Parse a canonical GUID."""
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class Point(c.Structure):
    """SDK POINT, passed by value."""

    _fields_ = [("x", c.c_int32), ("y", c.c_int32)]


class Rect(c.Structure):
    """SDK RECT, four signed 32-bit integers, not UIA doubles."""

    _fields_ = [(name, c.c_int32) for name in ("left", "top", "right", "bottom")]


@dataclass(frozen=True)
class Structure:
    """Non-text structural evidence from the provider."""

    pid: int
    hwnd: int
    control_type: int
    framework: str
    rect: tuple[int, int, int, int]
    offscreen: bool


@dataclass(frozen=True)
class ScrollState:
    """Provider units are percentages, never pixel displacement."""

    horizontal: float
    vertical: float
    horizontal_view: float
    vertical_view: float
    horizontal_scrollable: bool
    vertical_scrollable: bool


class Com:
    """One owned COM reference, explicitly released once."""

    def __init__(
        self, pointer: c.c_void_p, emit: Callable[[dict[str, object]], None]
    ) -> None:
        self.pointer = pointer
        self.emit = emit

    def call(self, slot: int, types: tuple[type[c._CData], ...], *args: object) -> int:
        """Log start before entering RPC, then its elapsed time and HRESULT."""
        if self.pointer.value is None:
            raise RuntimeError("Released COM reference")
        self.emit({"event": "call_start", "slot": slot})
        start = time.monotonic()
        table = c.cast(self.pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        result = int(
            c.WINFUNCTYPE(c.c_int32, c.c_void_p, *types)(table[slot])(
                self.pointer, *args
            )
        )
        self.emit(
            {
                "event": "call_end",
                "slot": slot,
                "seconds": time.monotonic() - start,
                "hresult": result,
            }
        )
        return result

    def checked(
        self, slot: int, types: tuple[type[c._CData], ...], *args: object
    ) -> None:
        """Fail without retry on a failed HRESULT."""
        result = self.call(slot, types, *args)
        if result < 0:
            raise OSError(f"UIA slot {slot}: HRESULT 0x{result & 0xFFFFFFFF:08x}")

    def read[T: c._CData](self, slot: int, kind: type[T]) -> T:
        """Read a single typed out parameter."""
        value = kind()
        self.checked(slot, (c.POINTER(kind),), c.byref(value))
        return value

    def release(self) -> None:
        """Release once; Release returns ULONG, not HRESULT."""
        if self.pointer.value is not None:
            pointer, self.pointer = self.pointer, c.c_void_p()
            table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
            c.WINFUNCTYPE(c.c_uint32, c.c_void_p)(table[2])(pointer)


class PanelUia:
    """Own UIA, walker, target and pattern on this process's MTA thread."""

    def __init__(self, emit: Callable[[dict[str, object]], None]) -> None:
        if sys.platform != "win32":
            raise OSError("UIA qualification requires Windows")
        self.emit = emit
        self.refs: list[Com] = []
        self.target: Com | None = None
        self.pattern: Com | None = None
        self.ancestors: list[Structure] = []
        self.ole = c.WinDLL("ole32")
        self.automation = c.WinDLL("oleaut32")
        self.automation.SysFreeString.argtypes = [c.c_void_p]
        self.automation.SysFreeString.restype = None
        self.automation.SysStringLen.argtypes = [c.c_void_p]
        self.automation.SysStringLen.restype = c.c_uint32
        self.ole.CoInitializeEx.argtypes = [c.c_void_p, c.c_uint32]
        self.ole.CoInitializeEx.restype = c.c_int32
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.initialized = self.ole.CoInitializeEx(None, 0) >= 0
        if not self.initialized:
            raise OSError("CoInitializeEx MTA failed")
        try:
            self.ole.CoCreateInstance.argtypes = [
                c.POINTER(Guid),
                c.c_void_p,
                c.c_uint32,
                c.POINTER(Guid),
                c.POINTER(c.c_void_p),
            ]
            self.ole.CoCreateInstance.restype = c.c_int32
            pointer = c.c_void_p()
            result = self.ole.CoCreateInstance(
                c.byref(Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
                None,
                1,
                c.byref(Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
                c.byref(pointer),
            )
            if result < 0:
                raise OSError(f"CoCreateInstance failed: {result}")  # noqa: TRY301 - cleanup
            self.uia = self.own(pointer)
            self.walker = self.own(self.uia.read(16, c.c_void_p))
        except BaseException:
            self.close()
            raise

    def own(self, pointer: c.c_void_p) -> Com:
        """Register one non-null owned reference for terminal cleanup."""
        if pointer.value is None:
            raise OSError("Unexpected null COM interface")
        reference = Com(pointer, self.emit)
        self.refs.append(reference)
        return reference

    def structure(self, element: Com) -> Structure:
        """Read identity and geometry, freeing the returned BSTR."""
        framework = element.read(40, c.c_void_p)
        try:
            length = self.automation.SysStringLen(framework)
            if length > MAX_FRAMEWORK_CHARS:
                raise OSError("FrameworkId exceeds diagnostic bound")
            name = (
                c.wstring_at(framework, length) if framework.value is not None else ""
            )
        finally:
            self.automation.SysFreeString(framework)
        rect = element.read(43, Rect)
        return Structure(
            element.read(20, c.c_int32).value,
            element.read(36, c.c_void_p).value or 0,
            element.read(21, c.c_int32).value,
            name,
            (rect.left, rect.top, rect.right, rect.bottom),
            bool(element.read(38, c.c_int32).value),
        )

    def navigate(self, element: Com, slot: int) -> Com | None:
        """Read exactly one parent, first child or next sibling."""
        pointer = c.c_void_p()
        self.walker.checked(
            slot, (c.c_void_p, c.POINTER(c.c_void_p)), element.pointer, c.byref(pointer)
        )
        return self.own(pointer) if pointer.value is not None else None

    def scroll_pattern(self, element: Com) -> Com | None:
        """Only UIA_E_NOTSUPPORTED or a successful null means unsupported."""
        pointer = c.c_void_p()
        result = element.call(
            14,
            (c.c_int32, c.POINTER(Guid), c.POINTER(c.c_void_p)),
            10004,
            c.byref(Guid.parse(SCROLL_IID)),
            c.byref(pointer),
        )
        if result < 0:
            if result & 0xFFFFFFFF == UNSUPPORTED:
                return None
            raise OSError(f"GetCurrentPatternAs failed: {result}")
        return self.own(pointer) if pointer.value is not None else None

    def discover(self, x: int, y: int) -> Structure:
        """Retain the nearest vertically scrolling ancestor, max 32 nodes."""
        if self.target is not None:
            raise RuntimeError("One discovery per probe")
        pointer = c.c_void_p()
        self.uia.checked(
            7, (Point, c.POINTER(c.c_void_p)), Point(x, y), c.byref(pointer)
        )
        element = self.own(pointer)
        for index in range(MAX_ANCESTORS):
            state = self.structure(element)
            self.ancestors.append(state)
            # Publish before the next native call: failures must retain the
            # visited non-text structure, not only successful discoveries.
            self.emit({"event": "ancestor", "index": index, **asdict(state)})
            pattern = self.scroll_pattern(element)
            self.emit(
                {
                    "event": "ancestor_pattern",
                    "index": index,
                    "scroll_pattern_present": pattern is not None,
                }
            )
            if pattern is not None:
                vertical = bool(pattern.read(10, c.c_int32).value)
                self.emit(
                    {
                        "event": "ancestor_vertical",
                        "index": index,
                        "vertical_scrollable": vertical,
                    }
                )
                if vertical:
                    self.target, self.pattern = element, pattern
                    return state
                pattern.release()
            parent = self.navigate(element, 3)
            element.release()
            if parent is None:
                raise OSError(
                    "No vertical ScrollPattern: ancestor chain ended after "
                    f"{index + 1} nodes"
                )
            element = parent
        raise OSError("No vertical ScrollPattern: reached bound of 32 ancestors")

    def read_state(self) -> ScrollState:
        """Read the selected scroll provider's current state."""
        if self.pattern is None:
            raise RuntimeError("No selected panel")
        return ScrollState(
            self.pattern.read(5, c.c_double).value,
            self.pattern.read(6, c.c_double).value,
            self.pattern.read(7, c.c_double).value,
            self.pattern.read(8, c.c_double).value,
            bool(self.pattern.read(9, c.c_int32).value),
            bool(self.pattern.read(10, c.c_int32).value),
        )

    def set_vertical(self, percent: float) -> None:
        """Request a vertical percentage while preserving the horizontal axis."""
        if self.pattern is None or not 0 <= percent <= FULL_PERCENT:
            raise ValueError("A selected panel and percent in [0, 100] are required")
        self.pattern.checked(4, (c.c_double, c.c_double), -1.0, percent)

    def inspect_children(self) -> list[Structure]:
        """Inspect at most 64 immediate children; no desktop subtree search."""
        if self.target is None:
            raise RuntimeError("No selected panel")
        states: list[Structure] = []
        child = self.navigate(self.target, 4)
        for index in range(MAX_CHILDREN):
            if child is None:
                break
            states.append(self.structure(child))
            sibling = self.navigate(child, 6) if index + 1 < MAX_CHILDREN else None
            child.release()
            child = sibling
        return states

    def close(self) -> None:
        """Release all references before balancing successful initialization."""
        for reference in reversed(self.refs):
            reference.release()
        self.refs.clear()
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False
