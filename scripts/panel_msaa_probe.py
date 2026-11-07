"""Bounded read-only MSAA rail experiment, never a product capture path.

Call only in a disposable, externally supervised owned-fixture helper, after UIA
has settled. The caller supplies an ownership/input/DPI guard and owned PIDs.
There is deliberately no CLI, default target, tree-wide search, or action method.
SDK ABI: oleacc.h IAccessible (IDispatch slots 0..6), oaidl.h VARIANT.
https://learn.microsoft.com/windows/win32/api/oleacc/nf-oleacc-accessibleobjectfrompoint
"""

from __future__ import annotations

import ctypes as c
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from scripts.panel_uia import Com, Guid, Point

SCROLLBAR_ROLE = 3
MAX_POINTS = 12
MAX_PARENTS = 2
MAX_PARTS = 5
MAX_NODES = 64
MAX_CALLS = 512
IID_ACCESSIBLE = "618736e0-3c3d-11cf-810c-00aa00389b71"
VT_I4 = 3
VT_BSTR = 8
VT_DISPATCH = 9
MAX_ROLE_CHARS = 256
RECT_COMPONENTS = 4
MIN_SIDE = 4


class Record(c.Structure):
    """VARIANT BRECORD forces the correct 24-byte x64 union layout."""

    _fields_ = [("record", c.c_void_p), ("info", c.c_void_p)]


class Value(c.Union):
    """Only read permitted alternatives; retain unknown variant tags."""

    _fields_ = [
        ("integer", c.c_int32),
        ("pointer", c.c_void_p),
        ("number", c.c_double),
        ("record", Record),
    ]


class Variant(c.Structure):
    """Windows VARIANT, including alignment on x86 and x64."""

    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", c.c_uint16),
        ("reserved1", c.c_uint16),
        ("reserved2", c.c_uint16),
        ("reserved3", c.c_uint16),
        ("value", Value),
    ]

    @classmethod
    def child(cls, value: int) -> Variant:
        """Create a CHILDID_SELF or simple-child identifier."""
        result = cls()
        result.vt, result.integer = VT_I4, value
        return result


def rail_points(rect: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Fixed 12-point geometry from outer UIA bounds, never fixture crops."""
    if len(rect) != RECT_COMPONENTS or any(type(v) is not int for v in rect):
        raise ValueError("Outer rectangle must contain four physical integers")
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    if width < MIN_SIDE or height < MIN_SIDE:
        raise ValueError("Outer rectangle is empty or too small")

    def x(fraction: float) -> int:
        return min(right - 1, left + round((width - 1) * fraction))

    def y(fraction: float) -> int:
        return min(bottom - 1, top + round((height - 1) * fraction))

    points = [
        (x(side), y(row)) for side in (0.98, 0.94, 0.02) for row in (0.25, 0.5, 0.75)
    ]
    points.extend((x(column), y(0.98)) for column in (0.25, 0.5, 0.75))
    return list(dict.fromkeys(points))[:MAX_POINTS]


class Backend(Protocol):
    """Small read-only interface makes traversal limits portable to test."""

    def hit(self, point: tuple[int, int], /) -> object:
        """Read the object at a point."""
        ...

    def describe(self, node: object, /) -> dict[str, object]:
        """Read structural properties."""
        ...

    def parent(self, node: object, /) -> object | None:
        """Read one parent."""
        ...

    def parts(self, node: object, limit: int, /) -> list[object]:
        """Read a bounded scrollbar child list."""
        ...


def _node_budget(count: int) -> None:
    if count >= MAX_NODES:
        raise RuntimeError("MSAA node budget exhausted")


def collect(backend: Backend, rect: tuple[int, int, int, int]) -> dict[str, object]:
    """Stop on first provider failure; never retry, widen or enumerate the desktop."""
    points = rail_points(rect)
    results: list[dict[str, object]] = []
    report: dict[str, object] = {
        "experimental": True,
        "read_only": True,
        "outer_rect": list(rect),
        "planned_points": [list(p) for p in points],
        "points": results,
        "diagnostic_completed": False,
        "first_failure": None,
    }
    count = 0
    try:
        for point in points:
            _node_budget(count)
            chain: list[dict[str, object]] = []
            results.append({"point": list(point), "chain": chain})
            node = backend.hit(point)
            for depth in range(MAX_PARENTS + 1):
                _node_budget(count)
                description = backend.describe(node)
                count += 1
                chain.append(description)
                role = description.get("role")
                if (
                    isinstance(role, dict)
                    and role.get("vt") == VT_I4
                    and role.get("value") == SCROLLBAR_ROLE
                ):
                    children = []
                    description["parts"] = children
                    for part in backend.parts(node, min(MAX_PARTS, MAX_NODES - count)):
                        _node_budget(count)
                        children.append(backend.describe(part))
                        count += 1
                    break
                if depth == MAX_PARENTS:
                    break
                parent = backend.parent(node)
                if parent is None:
                    break
                node = parent
        report["diagnostic_completed"] = True
    except (OSError, RuntimeError, ValueError) as error:
        report["first_failure"] = repr(error)
    report["inspected_nodes"] = count
    return report


@dataclass(frozen=True)
class Node:
    """Retained accessible interface plus an explicit simple-child identifier."""

    accessible: Com
    child_id: int = 0


class NativeMsaa:
    """Own COM references and retain each native HRESULT and raw structural value."""

    def __init__(
        self,
        owned_pids: frozenset[int],
        root_hwnd: int,
        guard: Callable[[], None],
        emit: Callable[[dict[str, object]], None],
    ) -> None:
        if sys.platform != "win32":
            raise OSError("MSAA native experiment requires Windows")
        if not owned_pids or any(pid <= 0 for pid in owned_pids):
            raise ValueError("Explicit owned fixture PIDs are required")
        if root_hwnd <= 0:
            raise ValueError("Explicit owned fixture root HWND is required")
        self.root_hwnd = root_hwnd
        self.pids, self.guard, self.emit = owned_pids, guard, emit
        self.refs: list[Com] = []
        self.cleanup_errors: list[dict[str, object]] = []
        self.calls = 0
        self.initialized = False
        self.ole = c.WinDLL("ole32")
        self.auto = c.WinDLL("oleaut32")
        self.acc = c.WinDLL("oleacc")
        self.user = c.WinDLL("user32")
        self._bind()
        result = self.ole.CoInitializeEx(None, 0)
        if result < 0:
            raise OSError(f"CoInitializeEx failed: 0x{result & 0xFFFFFFFF:08x}")
        self.initialized = True

    def _bind(self) -> None:
        signatures = [
            (self.ole.CoInitializeEx, [c.c_void_p, c.c_uint32], c.c_int32),
            (self.ole.CoUninitialize, [], None),
            (self.user.WindowFromPoint, [Point], c.c_void_p),
            (self.user.GetAncestor, [c.c_void_p, c.c_uint32], c.c_void_p),
            (self.auto.VariantClear, [c.POINTER(Variant)], c.c_int32),
            (self.auto.SysStringLen, [c.c_void_p], c.c_uint32),
            (
                self.acc.AccessibleObjectFromPoint,
                [Point, c.POINTER(c.c_void_p), c.POINTER(Variant)],
                c.c_int32,
            ),
            (
                self.acc.AccessibleChildren,
                [
                    c.c_void_p,
                    c.c_int32,
                    c.c_int32,
                    c.POINTER(Variant),
                    c.POINTER(c.c_int32),
                ],
                c.c_int32,
            ),
            (
                self.acc.WindowFromAccessibleObject,
                [c.c_void_p, c.POINTER(c.c_void_p)],
                c.c_int32,
            ),
            (
                self.user.GetWindowThreadProcessId,
                [c.c_void_p, c.POINTER(c.c_uint32)],
                c.c_uint32,
            ),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result

    def _read(
        self,
        name: str,
        function: Callable[[], int],
        *,
        owned_output: c.c_void_p | None = None,
    ) -> int:
        if self.cleanup_errors:
            raise RuntimeError("MSAA cleanup failed; no further provider reads")
        self.guard()
        if self.calls >= MAX_CALLS:
            raise RuntimeError("MSAA native call budget exhausted")
        self.calls += 1
        self.emit(
            {"event": "call_start", "operation": f"msaa.{name}", "call": self.calls}
        )
        started = time.monotonic()
        result = int(function())
        self.emit(
            {
                "event": "call_end",
                "operation": f"msaa.{name}",
                "hresult": result,
                "seconds": time.monotonic() - started,
            }
        )
        # Adopt a returned COM reference before the post-call guard can fail.
        # This is cleanup bookkeeping, not consumption of provider properties.
        if owned_output is not None and owned_output.value is not None:
            self._own(owned_output)
        self.guard()
        if result < 0:
            raise OSError(f"{name}: HRESULT 0x{result & 0xFFFFFFFF:08x}")
        return result

    def _cleanup(self, name: str, function: Callable[[], int | None]) -> None:
        """Watch cleanup without ownership guards, preserving any original failure."""
        operation = f"msaa.cleanup.{name}"
        self.emit({"event": "call_start", "operation": operation})
        started = time.monotonic()
        result: int | None = None
        failure: str | None = None
        try:
            result = function()
        except Exception as error:  # noqa: BLE001 - cleanup must preserve original failure
            failure = repr(error)
        if result is not None and result < 0:
            failure = f"HRESULT 0x{result & 0xFFFFFFFF:08x}"
        receipt: dict[str, object] = {
            "event": "call_end",
            "operation": operation,
            "seconds": time.monotonic() - started,
            "hresult": result,
        }
        if failure is not None:
            receipt["error"] = failure
            self.cleanup_errors.append({"operation": operation, "error": failure})
        self.emit(receipt)

    def _own(self, pointer: c.c_void_p) -> Com:
        if pointer.value is None:
            raise OSError("Null accessible interface")
        for retained in self.refs:
            if retained.pointer is pointer:
                return retained
        ref = Com(pointer, self.emit)
        self.refs.append(ref)
        return ref

    def _query(self, dispatch: c.c_void_p) -> Com:
        source = self._own(dispatch)
        result = c.c_void_p()
        iid = Guid.parse(IID_ACCESSIBLE)
        self._read(
            "QueryInterface(IAccessible)",
            lambda: source.call(
                0,
                (c.POINTER(Guid), c.POINTER(c.c_void_p)),
                c.byref(iid),
                c.byref(result),
            ),
            owned_output=result,
        )
        return self._own(result)

    def _variant(self, value: Variant) -> dict[str, object]:
        result: dict[str, object] = {"vt": int(value.vt)}
        if value.vt == VT_I4:
            result["value"] = int(value.integer)
        elif value.vt == VT_BSTR:
            length = int(self.auto.SysStringLen(value.pointer))
            result["length"] = length
            result["value"] = (
                c.wstring_at(value.pointer, min(length, MAX_ROLE_CHARS))
                if value.pointer
                else ""
            )
            result["truncated"] = length > MAX_ROLE_CHARS
        return result

    def hit(self, point: tuple[int, int]) -> object:
        """AccessibleObjectFromPoint is read-only; never replay the selection click."""
        self.guard()
        hit = self.user.WindowFromPoint(Point(*point))
        if not hit or self.user.GetAncestor(hit, 2) != self.root_hwnd:
            raise OSError("Rail point is outside the visible owned fixture root")
        pointer, child = c.c_void_p(), Variant()
        try:
            self._read(
                "AccessibleObjectFromPoint",
                lambda: self.acc.AccessibleObjectFromPoint(
                    Point(*point), c.byref(pointer), c.byref(child)
                ),
                owned_output=pointer,
            )
            ref = self._own(pointer)
            self.emit(
                {
                    "event": "msaa_hit_child",
                    "point": list(point),
                    "child": self._variant(child),
                }
            )
            if child.vt != VT_I4 or child.integer < 0:
                raise OSError("Unsupported MSAA hit child variant")
            return Node(ref, int(child.integer))
        finally:
            self._cleanup(
                "VariantClear", lambda: self.auto.VariantClear(c.byref(child))
            )

    def _property(self, node: Node, slot: int, name: str) -> dict[str, object]:
        value = Variant()
        try:
            status = self._read(
                name,
                lambda: node.accessible.call(
                    slot,
                    (Variant, c.POINTER(Variant)),
                    Variant.child(node.child_id),
                    c.byref(value),
                ),
            )
            return {"hresult": status, **self._variant(value)}
        finally:
            self._cleanup(
                "VariantClear", lambda: self.auto.VariantClear(c.byref(value))
            )

    def _owner(self, node: Node) -> dict[str, int]:
        hwnd, pid = c.c_void_p(), c.c_uint32()
        status = self._read(
            "WindowFromAccessibleObject",
            lambda: self.acc.WindowFromAccessibleObject(
                node.accessible.pointer, c.byref(hwnd)
            ),
        )
        if hwnd.value is None:
            raise OSError("Accessible owner HWND is null; target not verified")
        self.guard()
        if (
            not self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
            or pid.value not in self.pids
            or self.user.GetAncestor(hwnd, 2) != self.root_hwnd
        ):
            raise OSError("Accessible object is outside owned fixture PIDs")
        return {"hresult": status, "hwnd": int(hwnd.value), "pid": int(pid.value)}

    def describe(self, value: object) -> dict[str, object]:
        """Read only ownership, numeric role/state and physical bounds."""
        node = cast(Node, value)
        owner = self._owner(node)
        role = self._property(node, 13, "get_accRole")
        state = self._property(node, 14, "get_accState")
        coordinates = [c.c_int32() for _ in range(4)]
        status = self._read(
            "accLocation",
            lambda: node.accessible.call(
                22,
                (c.POINTER(c.c_int32),) * 4 + (Variant,),
                *(c.byref(v) for v in coordinates),
                Variant.child(node.child_id),
            ),
        )
        result: dict[str, object] = {
            "child_id": node.child_id,
            "owner": owner,
            "role": role,
            "state": state,
            "location": {
                "hresult": status,
                "xywh": [int(v.value) for v in coordinates],
            },
        }
        self.emit({"event": "msaa_node", "structure": result})
        return result

    def parent(self, value: object) -> object | None:
        """A simple child's parent is its current container, not accParent's result."""
        node = cast(Node, value)
        if node.child_id:
            return Node(node.accessible)
        pointer = c.c_void_p()
        self._read(
            "get_accParent",
            lambda: node.accessible.call(7, (c.POINTER(c.c_void_p),), c.byref(pointer)),
            owned_output=pointer,
        )
        return Node(self._query(pointer)) if pointer.value is not None else None

    def parts(self, value: object, limit: int) -> list[object]:
        """Inspect at most five scrollbar parts, never arbitrary descendants."""
        node = cast(Node, value)
        if node.child_id:
            return []
        total = c.c_int32()
        self._read(
            "get_accChildCount",
            lambda: node.accessible.call(8, (c.POINTER(c.c_int32),), c.byref(total)),
        )
        self.emit({"event": "msaa_child_count", "count": int(total.value)})
        count = min(max(total.value, 0), limit, MAX_PARTS)
        if count <= 0:
            return []
        variants, obtained = (Variant * count)(), c.c_int32()
        result: list[object] = []
        try:
            self._read(
                "AccessibleChildren",
                lambda: self.acc.AccessibleChildren(
                    node.accessible.pointer, 0, count, variants, c.byref(obtained)
                ),
            )
            if not 0 <= obtained.value <= count:
                raise OSError("AccessibleChildren returned an invalid count")
            for variant in variants[: obtained.value]:
                self.emit(
                    {"event": "msaa_child_variant", "child": self._variant(variant)}
                )
                if variant.vt == VT_I4 and variant.integer > 0:
                    result.append(Node(node.accessible, int(variant.integer)))
                elif variant.vt == VT_DISPATCH and variant.pointer:
                    # Transfer this owned dispatch reference out of the VARIANT.
                    pointer = c.c_void_p(variant.pointer)
                    variant.vt, variant.pointer = 0, None
                    result.append(Node(self._query(pointer)))
                else:
                    raise OSError("Unsupported AccessibleChildren variant")
            return result
        finally:
            for variant in variants:
                self._cleanup(
                    "VariantClear",
                    lambda value=variant: self.auto.VariantClear(c.byref(value)),
                )

    def close(self) -> None:
        """Release each owned reference once, even after lost ownership or errors."""
        while self.refs:
            self._cleanup("Release", self.refs.pop().release)
        if self.initialized:
            self.initialized = False
            self._cleanup("CoUninitialize", self.ole.CoUninitialize)


def _record_cleanup(result: dict[str, object] | None, adapter: NativeMsaa) -> None:
    if result is not None:
        result["cleanup_errors"] = list(adapter.cleanup_errors)
        if adapter.cleanup_errors:
            result["diagnostic_completed"] = False
            if result["first_failure"] is None:
                result["first_failure"] = "MSAA cleanup failed; inspect cleanup_errors"


def probe_msaa(
    rect: tuple[int, int, int, int],
    owned_pids: frozenset[int],
    root_hwnd: int,
    guard: Callable[[], None],
    emit: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Run only under caller's hard deadline and already-established DPI/input guard."""
    rail_points(rect)
    guard()
    adapter = NativeMsaa(owned_pids, root_hwnd, guard, emit)
    result: dict[str, object] | None = None
    try:
        result = collect(adapter, rect)
        guard()
        return result
    finally:
        adapter.close()
        _record_cleanup(result, adapter)
