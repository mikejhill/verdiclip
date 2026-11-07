"""Read-only experimental UIA identity; no product imports or scroll operations.

Runtime IDs are opaque desktop-local values which Windows may reuse. These
checks establish observed continuity only, never a permanent identity guarantee.
ABI: Windows SDK UIAutomationClient.h, including IUnknown vtable entries.
"""

from __future__ import annotations

import ctypes as c
import sys
from collections.abc import Callable
from contextlib import suppress
from typing import override

from scripts.panel_uia import (
    MAX_ANCESTORS,
    MAX_FRAMEWORK_CHARS,
    SCROLL_IID,
    UNSUPPORTED,
    Com,
    Guid,
    Point,
    Rect,
    Structure,
)
from scripts.panel_uia import PanelUia as BaseUia

MAX_ID_PARTS = 64
VT_I4 = 3
Emit = Callable[[dict[str, object]], None]


def logged[T](emit: Emit, name: str, operation: Callable[[], T]) -> T:
    """Log cleanup too, without guard checks that could prevent releasing it."""
    emit({"event": "call_start", "name": name})
    try:
        result = operation()
    except BaseException as error:
        emit({"event": "call_error", "name": name, "error": repr(error)})
        raise
    emit({"event": "call_end", "name": name})
    return result


def cleanup[T](emit: Emit, name: str, operation: Callable[[], T]) -> T:
    """A broken log must not prevent releasing an already-owned resource."""
    logging_failure = None
    try:
        emit({"event": "call_start", "name": name})
    except BaseException as error:  # noqa: BLE001 - cleanup must still execute
        logging_failure = error
    result = operation()
    if logging_failure is not None:
        raise logging_failure
    emit({"event": "call_end", "name": name})
    return result


def record_cleanup_error(emit: Emit, error: BaseException) -> None:
    """Secondary evidence cannot prevent remaining cleanup or mask first failure."""
    with suppress(BaseException):
        emit({"event": "cleanup_error", "error": repr(error)})


def checked(result: int, name: str) -> None:
    """Keep signed Windows HRESULT semantics distinct from BOOL outputs."""
    if result < 0:
        raise OSError(f"{name}: HRESULT 0x{result & 0xFFFFFFFF:08x}")


class Reference(Com):
    """Existing ABI with explicitly logged, exact-once Release."""

    @override
    def release(self) -> None:
        if self.pointer.value is not None:
            cleanup(self.emit, "identity.Release", super().release)


class RuntimeIds:
    """Copy only bounded verified int32 arrays; never transfer SAFEARRAY ownership."""

    def __init__(self, api: c.CDLL, emit: Emit) -> None:
        self.api, self.emit = api, emit

    def invoke(self, name: str, *args: object) -> int:
        """Wrap each OleAut operation in externally supervised evidence."""
        return int(logged(self.emit, name, lambda: getattr(self.api, name)(*args)))

    def read(self, element: Com) -> tuple[int, ...]:
        """Destroy returned storage even when the provider returns a failed HRESULT."""
        array = c.c_void_p()
        failure: BaseException | None = None
        try:
            result = element.call(4, (c.POINTER(c.c_void_p),), c.byref(array))
            checked(result, "GetRuntimeId")
            if array.value is None:
                raise OSError("Null runtime ID")  # noqa: TRY301 - finally owns array
            if self.invoke("SafeArrayGetDim", array) != 1:
                raise OSError("Runtime ID must have one dimension")  # noqa: TRY301
            kind = c.c_uint16()
            checked(self.invoke("SafeArrayGetVartype", array, c.byref(kind)), "type")
            if kind.value != VT_I4 or self.invoke(
                "SafeArrayGetElemsize", array
            ) != c.sizeof(c.c_int32):
                raise OSError("Runtime ID must contain int32 values")  # noqa: TRY301
            lower, upper = c.c_int32(), c.c_int32()
            checked(
                self.invoke("SafeArrayGetLBound", array, 1, c.byref(lower)), "lower"
            )
            checked(
                self.invoke("SafeArrayGetUBound", array, 1, c.byref(upper)), "upper"
            )
            if lower.value != 0 or not 0 <= upper.value < MAX_ID_PARTS:
                raise OSError("Runtime ID bounds invalid")  # noqa: TRY301
            parts = []
            for index in range(upper.value + 1):
                native_index, value = c.c_int32(index), c.c_int32()
                checked(
                    self.invoke(
                        "SafeArrayGetElement",
                        array,
                        c.byref(native_index),
                        c.byref(value),
                    ),
                    "element",
                )
                parts.append(value.value)
            return tuple(parts)
        except BaseException as error:
            failure = error
            raise
        finally:
            if array.value is not None:
                try:
                    checked(
                        int(
                            cleanup(
                                self.emit,
                                "SafeArrayDestroy",
                                lambda: self.api.SafeArrayDestroy(array),
                            )
                        ),
                        "destroy",
                    )
                except BaseException as error:
                    record_cleanup_error(self.emit, error)
                    if failure is None:
                        raise


def bind_arrays(api: c.CDLL) -> None:
    """Use fixed Windows widths, including on a non-Windows test host."""
    for name, args, result in (
        ("SafeArrayGetDim", [c.c_void_p], c.c_uint32),
        ("SafeArrayGetElemsize", [c.c_void_p], c.c_uint32),
        ("SafeArrayGetVartype", [c.c_void_p, c.POINTER(c.c_uint16)], c.c_int32),
        (
            "SafeArrayGetLBound",
            [c.c_void_p, c.c_uint32, c.POINTER(c.c_int32)],
            c.c_int32,
        ),
        (
            "SafeArrayGetUBound",
            [c.c_void_p, c.c_uint32, c.POINTER(c.c_int32)],
            c.c_int32,
        ),
        (
            "SafeArrayGetElement",
            [c.c_void_p, c.POINTER(c.c_int32), c.c_void_p],
            c.c_int32,
        ),
        ("SafeArrayDestroy", [c.c_void_p], c.c_int32),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = args, result


class IdentityUia(BaseUia):
    """Separate experimental adapter: all acquired outputs owned before admission."""

    def __init__(self, emit: Emit) -> None:
        if sys.platform != "win32":
            raise OSError("Identity probe requires Windows")
        self.emit = emit
        self.refs: list[Com] = []
        self.target = self.pattern = None
        self.ancestors: list[Structure] = []
        self.initialized = False
        self.ole, self.automation = c.WinDLL("ole32"), c.WinDLL("oleaut32")
        self.ole.CoInitializeEx.argtypes = [c.c_void_p, c.c_uint32]
        self.ole.CoInitializeEx.restype = c.c_int32
        self.ole.CoUninitialize.argtypes, self.ole.CoUninitialize.restype = [], None
        self.ole.CoCreateInstance.argtypes = [
            c.POINTER(Guid),
            c.c_void_p,
            c.c_uint32,
            c.POINTER(Guid),
            c.POINTER(c.c_void_p),
        ]
        self.ole.CoCreateInstance.restype = c.c_int32
        self.automation.SysStringLen.argtypes = [c.c_void_p]
        self.automation.SysStringLen.restype = c.c_uint32
        self.automation.SysFreeString.argtypes = [c.c_void_p]
        self.automation.SysFreeString.restype = None
        bind_arrays(self.automation)
        self.ids = RuntimeIds(self.automation, emit)
        try:
            # Retain successful initialization before logging/admitting its result.
            def initialize() -> int:
                result = int(self.ole.CoInitializeEx(None, 0))
                self.initialized = result >= 0
                return result

            checked(logged(emit, "identity.CoInitializeEx", initialize), "initialize")
            pointer = c.c_void_p()
            try:
                result = logged(
                    emit,
                    "identity.CoCreateInstance",
                    lambda: self.ole.CoCreateInstance(
                        c.byref(Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
                        None,
                        1,
                        c.byref(Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
                        c.byref(pointer),
                    ),
                )
            finally:
                reference = self.own(pointer) if pointer.value is not None else None
            checked(result, "create")
            if reference is None:
                raise OSError("Null automation")  # noqa: TRY301 - initialization cleanup
            self.uia = reference
            walker = self.acquire(self.uia, 16, (), ())
            if walker is None:
                raise OSError("Null raw walker")  # noqa: TRY301 - initialization cleanup
            self.walker = walker
        except BaseException:
            # close logs secondary failures; preserve the initial acquisition error.
            with suppress(BaseException):
                self.close()
            raise

    @override
    def own(self, pointer: c.c_void_p) -> Com:
        if pointer.value is None:
            raise OSError("Null COM reference")
        reference = Reference(pointer, self.emit)
        self.refs.append(reference)
        return reference

    def acquire(
        self,
        source: Com,
        slot: int,
        types: tuple[type[c._CData], ...],
        args: tuple[object, ...],
        *,
        optional_pattern: bool = False,
    ) -> Com | None:
        """Own even failure allocations before raising; close releases all of them."""
        pointer = c.c_void_p()
        try:
            result = source.call(
                slot, (*types, c.POINTER(c.c_void_p)), *args, c.byref(pointer)
            )
        finally:
            reference = self.own(pointer) if pointer.value is not None else None
        if optional_pattern and result & 0xFFFFFFFF == UNSUPPORTED:
            if reference is not None:
                reference.release()
            return None
        checked(result, f"acquire slot {slot}")
        return reference

    @override
    def structure(self, element: Com) -> Structure:
        """Retain/free BSTR even on HRESULT failure; log local allocation cleanup."""
        framework = c.c_void_p()
        try:
            result = element.call(40, (c.POINTER(c.c_void_p),), c.byref(framework))
            checked(result, "FrameworkId")
            length = logged(
                self.emit,
                "identity.SysStringLen",
                lambda: self.automation.SysStringLen(framework),
            )
            if length > MAX_FRAMEWORK_CHARS:
                raise OSError("Framework bound")
            name = (
                c.wstring_at(framework, length) if framework.value is not None else ""
            )
        finally:
            primary = sys.exception()
            if framework.value is not None:
                try:
                    cleanup(
                        self.emit,
                        "identity.SysFreeString",
                        lambda: self.automation.SysFreeString(framework),
                    )
                except BaseException as error:
                    record_cleanup_error(self.emit, error)
                    if primary is None:
                        raise
        rect = element.read(43, Rect)
        return Structure(
            element.read(20, c.c_int32).value,
            element.read(36, c.c_void_p).value or 0,
            element.read(21, c.c_int32).value,
            name,
            (rect.left, rect.top, rect.right, rect.bottom),
            bool(element.read(38, c.c_int32).value),
        )

    @override
    def navigate(self, element: Com, slot: int) -> Com | None:
        return self.acquire(self.walker, slot, (c.c_void_p,), (element.pointer,))

    @override
    def scroll_pattern(self, element: Com) -> Com | None:
        iid = Guid.parse(SCROLL_IID)
        return self.acquire(
            element,
            14,
            (c.c_int32, c.POINTER(Guid)),
            (10004, c.byref(iid)),
            optional_pattern=True,
        )

    def from_handle(self, hwnd: int) -> Com:
        """Acquire root from the selected native HWND, never from a PID guess."""
        element = self.acquire(self.uia, 6, (c.c_void_p,), (c.c_void_p(hwnd),))
        if element is None:
            raise OSError("Null root")
        return element

    def same(self, left: Com, right: Com) -> bool:
        """CompareElements HRESULT is success status; BOOL out is the answer."""
        same = c.c_int32()
        self.uia.checked(
            3,
            (c.c_void_p, c.c_void_p, c.POINTER(c.c_int32)),
            left.pointer,
            right.pointer,
            c.byref(same),
        )
        return bool(same.value)

    @override
    def discover(self, x: int, y: int) -> Structure:
        if self.target is not None:
            raise RuntimeError("One discovery per identity client")
        element = self.acquire(self.uia, 7, (Point,), (Point(x, y),))
        visited: set[tuple[int, ...]] = set()
        for _ in range(MAX_ANCESTORS):
            if element is None:
                raise OSError(
                    "No vertical ScrollPattern: ancestor chain ended after "
                    f"{len(self.ancestors)} nodes"
                )
            identity = self.ids.read(element)
            if identity in visited:
                raise OSError("Discovery ancestry cycle")
            visited.add(identity)
            state = self.structure(element)
            self.ancestors.append(state)
            pattern = self.scroll_pattern(element)
            if pattern is not None:
                if pattern.read(10, c.c_int32).value:
                    self.target, self.pattern = element, pattern
                    return state
                pattern.release()
            parent = self.navigate(element, 3)
            element.release()
            element = parent
        raise OSError("Identity discovery ancestry bound")

    @override
    def close(self) -> None:
        errors: list[BaseException] = []
        for reference in reversed(self.refs):
            try:
                reference.release()
            except BaseException as error:  # noqa: BLE001 - release remaining resources
                errors.append(error)
                record_cleanup_error(self.emit, error)
        self.refs.clear()
        if self.initialized:
            self.initialized = False
            try:
                cleanup(self.emit, "identity.CoUninitialize", self.ole.CoUninitialize)
            except BaseException as error:  # noqa: BLE001 - release remaining resources
                errors.append(error)
                record_cleanup_error(self.emit, error)
        if errors:
            raise RuntimeError("Identity COM cleanup failed") from errors[0]


def prove_association(
    client: IdentityUia,
    target: Com,
    root: Com,
    baseline: Com,
    expected: tuple[int, ...],
) -> list[tuple[int, ...]]:
    """Bounded ancestry proves association across PID boundaries, not immortality."""
    if client.ids.read(target) != expected or not client.same(target, baseline):
        raise OSError("Selected target was replaced")
    visited: list[tuple[int, ...]] = []
    element: Com | None = target
    try:
        for _ in range(MAX_ANCESTORS):
            if element is None:
                raise OSError("Selected root absent from target ancestry")
            identity = client.ids.read(element)
            if identity in visited:
                raise OSError("Root ancestry cycle")
            visited.append(identity)
            if client.same(element, root):
                return visited
            parent = client.navigate(element, 3)
            if element is not target:
                element.release()
            element = parent
        raise OSError("Root ancestry bound")
    finally:
        primary = sys.exception()
        if element is not None and element is not target:
            try:
                element.release()
            except BaseException as error:
                record_cleanup_error(client.emit, error)
                if primary is None:
                    raise
