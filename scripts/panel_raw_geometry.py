"""Read-only owned Chromium geometry research; never chooses capture bounds.

WinSDK VARIANT x64 ABI: 8-byte header plus 16-byte BRECORD-capable union.
UIAutomationClient.h slots10 GetCurrentPropertyValue(INT,VARIANT*) and43 RECT*.
https://learn.microsoft.com/en-us/windows/win32/api/oaidl/ns-oaidl-variant
https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-automation-element-propids
Only a disposable externally supervised helper may call this module.
"""

from __future__ import annotations

import ctypes as c
import math
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from scripts.panel_uia import Com, Rect

VT_R8 = 5
VT_ARRAY = 0x2000
PROPERTY = 30001
MAX_COORDINATE = 1_000_000
CALL_SECONDS = 3.0
POINTER_BYTES = 8
VARIANT_BYTES = 24
COMPONENTS = 4


class Value(c.Union):
    """Include BRECORD's two pointers so x64 VARIANT is not truncated."""

    _fields_ = [
        ("array", c.c_void_p),
        ("number", c.c_double),
        ("record", c.c_void_p * 2),
    ]


class Variant(c.Structure):
    """Zero initialized tagged union; VariantClear owns its returned SAFEARRAY."""

    _fields_ = [("vt", c.c_uint16), ("reserved", c.c_uint16 * 3), ("value", Value)]


class ArrayApi:
    """Exact OleAut32 signatures; no pointer arithmetic or array-data casts."""

    def __init__(self) -> None:
        if (
            sys.platform != "win32"
            or c.sizeof(c.c_void_p) != POINTER_BYTES
            or c.sizeof(Variant) != VARIANT_BYTES
        ):
            raise OSError("Raw geometry diagnostic requires Windows x64 VARIANT")
        self.dll = c.WinDLL("oleaut32", use_last_error=True)
        signatures = {
            "VariantClear": ([c.POINTER(Variant)], c.c_int32),
            "SafeArrayGetDim": ([c.c_void_p], c.c_uint32),
            "SafeArrayGetElemsize": ([c.c_void_p], c.c_uint32),
            "SafeArrayGetVartype": ([c.c_void_p, c.POINTER(c.c_uint16)], c.c_int32),
            "SafeArrayGetLBound": (
                [c.c_void_p, c.c_uint32, c.POINTER(c.c_int32)],
                c.c_int32,
            ),
            "SafeArrayGetUBound": (
                [c.c_void_p, c.c_uint32, c.POINTER(c.c_int32)],
                c.c_int32,
            ),
            "SafeArrayGetElement": (
                [c.c_void_p, c.POINTER(c.c_int32), c.c_void_p],
                c.c_int32,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, result


def compare(  # noqa: C901, PLR0912, PLR0913, PLR0915 - fixed ownership sequence
    target: Com,
    guard: Callable[[], object],
    emit: Callable[[dict[str, object]], None],
    deadline: float,
    *,
    api: Any = None,  # noqa: ANN401 - private fake ABI test seam
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Typed/raw/typed on one retained target; all returned geometry is evidence."""
    if not math.isfinite(deadline) or deadline <= clock():
        raise ValueError("Raw geometry deadline expired")
    automation = ArrayApi().dll if api is None else api
    variant = Variant()
    last = clock()

    def check() -> None:
        nonlocal last
        now = clock()
        if not math.isfinite(now) or now < last or now >= deadline:
            raise RuntimeError("Raw geometry deadline/clock failure")
        last = now

    def call(name: str, operation: Callable[[], Any], *, cleanup: bool = False) -> Any:  # noqa: ANN401 - native scalar result
        if not cleanup:
            check()
            guard()
            check()
        emit({"event": "call_start", "raw_geometry": name})
        start = clock()
        result = operation()
        elapsed = clock() - start
        emit({"event": "call_end", "raw_geometry": name, "seconds": elapsed})
        if elapsed < 0 or elapsed >= CALL_SECONDS:
            raise RuntimeError("Raw geometry native call exceeded bound")
        if not cleanup:
            check()
            guard()
            check()
        if (
            name
            not in (
                "typed_before",
                "typed_after",
                "SafeArrayGetDim",
                "SafeArrayGetElemsize",
            )
            and result < 0
        ):
            raise OSError(f"{name} HRESULT 0x{result & 0xFFFFFFFF:08x}")
        return result

    result = None
    try:
        before = call("typed_before", lambda: target.read(43, Rect))
        call(
            "GetCurrentPropertyValue",
            lambda: target.call(
                10, (c.c_int32, c.POINTER(Variant)), PROPERTY, c.byref(variant)
            ),
        )
        if (
            variant.vt != VT_ARRAY | VT_R8
            or any(variant.reserved)
            or not variant.value.array
        ):
            raise ValueError("Raw property must be exact nonnull VT_ARRAY|VT_R8")
        array = variant.value.array
        if call("SafeArrayGetDim", lambda: automation.SafeArrayGetDim(array)) != 1:
            raise ValueError("Raw rectangle array dimension")
        if call(
            "SafeArrayGetElemsize", lambda: automation.SafeArrayGetElemsize(array)
        ) != c.sizeof(c.c_double):
            raise ValueError("Raw rectangle array element size")
        kind = c.c_uint16()
        call(
            "SafeArrayGetVartype",
            lambda: automation.SafeArrayGetVartype(array, c.byref(kind)),
        )
        low, high = c.c_int32(), c.c_int32()
        call(
            "SafeArrayGetLBound",
            lambda: automation.SafeArrayGetLBound(array, 1, c.byref(low)),
        )
        call(
            "SafeArrayGetUBound",
            lambda: automation.SafeArrayGetUBound(array, 1, c.byref(high)),
        )
        if kind.value != VT_R8 or low.value != 0 or high.value != COMPONENTS - 1:
            raise ValueError("Raw rectangle array must be four zero-based doubles")
        values = []
        for index in range(4):
            position, value = c.c_int32(index), c.c_double()
            call(
                "SafeArrayGetElement",
                lambda position=position, value=value: automation.SafeArrayGetElement(
                    array, c.byref(position), c.byref(value)
                ),
            )
            if not math.isfinite(value.value) or abs(value.value) > MAX_COORDINATE:
                raise ValueError("Raw rectangle nonfinite/out-of-bound value")
            values.append(value.value)
        if (
            values[2] <= 0
            or values[3] <= 0
            or any(abs(values[i] + values[i + 2]) > MAX_COORDINATE for i in range(2))
        ):
            raise ValueError("Raw rectangle invalid extent")
        after = call("typed_after", lambda: target.read(43, Rect))
        typed_before = [before.left, before.top, before.right, before.bottom]
        typed_after = [after.left, after.top, after.right, after.bottom]
        if typed_before != typed_after:
            raise ValueError("Typed rectangle changed around raw read")
        result = {
            "typed_before_ltrb": typed_before,
            "raw_xywh": values,
            "raw_decimal": [repr(value) for value in values],
            "raw_hex": [float(value).hex() for value in values],
            "typed_after_ltrb": typed_after,
            "fractional_components": [not value.is_integer() for value in values],
            "geometry_qualified": False,
        }
    finally:
        failure = sys.exception()
        # Logging must not prevent destruction of a possibly allocated VARIANT.
        try:
            try:
                emit({"event": "call_start", "raw_geometry": "VariantClear"})
            finally:
                try:
                    start = clock()
                finally:
                    cleared = automation.VariantClear(c.byref(variant))
            elapsed = clock() - start
            emit(
                {
                    "event": "call_end",
                    "raw_geometry": "VariantClear",
                    "seconds": elapsed,
                    "hresult": cleared,
                }
            )
            if cleared < 0 or elapsed < 0 or elapsed >= CALL_SECONDS:
                raise OSError("VariantClear failed or exceeded call deadline")  # noqa: TRY301
        except Exception as error:
            if failure is None:
                raise
            failure.add_note(f"Variant cleanup failure: {error!r}")
            with suppress(Exception):  # Original and cleanup errors remain attached.
                emit(
                    {
                        "event": "cleanup_error",
                        "raw_geometry": "VariantClear",
                        "error": repr(error),
                    }
                )
    check()
    guard()
    check()
    if result is None:
        raise RuntimeError("Missing raw geometry evidence")
    return result
