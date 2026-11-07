"""Portable geometry, COM ABI and bounded-read tests; no live Windows calls."""

from __future__ import annotations

import ctypes as c
import sys
from typing import Any, cast

import pytest
from scripts.panel_msaa_probe import (
    MAX_CALLS,
    MAX_NODES,
    MAX_PARENTS,
    MAX_PARTS,
    MAX_POINTS,
    SCROLLBAR_ROLE,
    VT_I4,
    NativeMsaa,
    Node,
    Variant,
    _record_cleanup,
    collect,
    rail_points,
)
from scripts.panel_uia import Com


class FakeBackend:
    """Deterministic fake with visible traversal receipts."""

    def __init__(self, *, scrollbar: bool = False, fail: bool = False) -> None:
        self.scrollbar, self.fail = scrollbar, fail
        self.hits: list[tuple[int, int]] = []
        self.parents = 0
        self.limits: list[int] = []

    def hit(self, point: tuple[int, int]) -> object:
        """Record every query without moving any pointer."""
        self.hits.append(point)
        if self.fail:
            raise OSError("HRESULT 0x80004005")
        return 0

    def describe(self, _node: object) -> dict[str, object]:
        """Return structural data only."""
        return {"role": {"vt": VT_I4, "value": SCROLLBAR_ROLE if self.scrollbar else 1}}

    def parent(self, node: object) -> object:
        """An endless provider must still be bounded by the collector."""
        self.parents += 1
        return node

    def parts(self, _node: object, limit: int) -> list[object]:
        """Return only the requested bounded number of parts."""
        self.limits.append(limit)
        return list(range(limit))


def test_variant_native_layout() -> None:
    """VARIANT must include the two-pointer BRECORD member on x64."""
    assert Variant.value.offset == 8
    assert c.sizeof(Variant) == (24 if c.sizeof(c.c_void_p) == 8 else 16)
    child = Variant.child(42)
    assert child.vt == VT_I4
    assert child.integer == 42


def test_points_only_use_outer_geometry() -> None:
    """Every rail/control sample remains inside physical bounds, including negatives."""
    for rect in [(99, 129, 433, 393), (-500, -400, -100, -50), (0, 0, 4, 4)]:
        points = rail_points(rect)
        assert 0 < len(points) <= MAX_POINTS
        assert len(points) == len(set(points))
        assert all(rect[0] <= x < rect[2] and rect[1] <= y < rect[3] for x, y in points)
    assert len(rail_points((99, 129, 433, 393))) == MAX_POINTS


@pytest.mark.parametrize("rect", [(0, 0, 0, 10), (10, 0, 0, 10), (0, 0, 2, 2)])
def test_invalid_rectangle_has_no_queries(rect: tuple[int, int, int, int]) -> None:
    """Reject invalid input before invoking a provider."""
    backend = FakeBackend()
    with pytest.raises(ValueError, match="rectangle"):
        collect(backend, rect)
    assert not backend.hits


def test_parent_walk_is_bounded() -> None:
    """No descendant enumeration or unlimited parent search is permitted."""
    backend = FakeBackend()
    report = collect(backend, (0, 0, 400, 300))
    assert report["diagnostic_completed"] is True
    assert report["inspected_nodes"] == MAX_POINTS * (MAX_PARENTS + 1)
    assert backend.parents == MAX_POINTS * MAX_PARENTS
    assert not backend.limits


def test_scrollbar_parts_and_total_nodes_are_bounded() -> None:
    """Even every hit returning a scrollbar cannot exceed the global budget."""
    backend = FakeBackend(scrollbar=True)
    report = collect(backend, (0, 0, 400, 300))
    assert report["inspected_nodes"] == MAX_NODES
    assert report["diagnostic_completed"] is False
    assert all(0 <= limit <= MAX_PARTS for limit in backend.limits)
    assert backend.parents == 0


def test_provider_error_is_retained_without_retry() -> None:
    """The first failing point ends the diagnostic with exact HRESULT evidence."""
    backend = FakeBackend(fail=True)
    report = collect(backend, (0, 0, 400, 300))
    assert report["diagnostic_completed"] is False
    assert "0x80004005" in str(report["first_failure"])
    assert len(backend.hits) == 1


def test_simple_child_parent_is_current_container() -> None:
    """An MSAA simple child must not skip its owning accessible container."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    ref = Com(c.c_void_p(1), lambda _: None)
    node = adapter.parent(Node(ref, 7))
    assert node == Node(ref, 0)


def test_native_call_budget_and_guard() -> None:
    """Guard and budget checks happen before any provider call."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    events: list[dict[str, object]] = []
    guards: list[bool] = []
    adapter.emit, adapter.guard = events.append, lambda: guards.append(True)
    adapter.calls = MAX_CALLS
    with pytest.raises(RuntimeError, match="budget"):
        adapter._read("blocked", lambda: pytest.fail("provider called"))
    assert guards == [True]
    assert not events
    adapter.calls = 0
    with pytest.raises(OSError, match="80004005"):
        adapter._read("failing read", lambda: -2147467259)
    assert events[-1]["hresult"] == -2147467259
    assert guards == [True, True, True]
    assert events[-1]["event"] == "call_end"


def test_native_property_uses_accessible_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Role/state take a VARIANT by value and retain raw result tags."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    adapter.guard, adapter.emit, adapter.calls = lambda: None, lambda _: None, 0
    cleared: list[int] = []

    class Automation:
        """Only the cleanup function needed for numeric properties."""

        @staticmethod
        def VariantClear(pointer: object) -> int:  # noqa: N802 - Windows API spelling
            cleared.append(c.cast(cast(Any, pointer), c.POINTER(Variant)).contents.vt)
            return 0

    adapter.auto = cast("c.WinDLL", Automation())
    calls: list[tuple[int, int]] = []

    def call(
        _self: Com, slot: int, _types: tuple[type[c._CData], ...], *args: object
    ) -> int:
        child = cast(Variant, args[0])
        calls.append((slot, child.integer))
        value = c.cast(cast(Any, args[1]), c.POINTER(Variant)).contents
        value.vt, value.integer = VT_I4, SCROLLBAR_ROLE
        return 0

    monkeypatch.setattr(Com, "call", call)
    node = Node(Com(c.c_void_p(1), lambda _: None), 9)
    assert adapter._property(node, 13, "get_accRole") == {
        "hresult": 0,
        "vt": VT_I4,
        "value": SCROLLBAR_ROLE,
    }
    assert calls == [(13, 9)]
    assert cleared == [VT_I4]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only native rejection check")
def test_non_windows_rejects_without_loading_dlls() -> None:
    """Portable test collection must never touch a live accessibility provider."""
    with pytest.raises(OSError, match="Windows"):
        NativeMsaa(frozenset({123}), 456, lambda: None, lambda _: None)


def test_parts_transfer_dispatch_reference_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch variant is emptied before VariantClear; QI owns its reference."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    adapter.guard, adapter.emit, adapter.calls = lambda: None, lambda _: None, 0
    cleared: list[int] = []
    queried: list[int | None] = []

    class Automation:
        @staticmethod
        def VariantClear(pointer: object) -> int:  # noqa: N802 - Windows API spelling
            """Record tags remaining after ownership transfer."""
            cleared.append(c.cast(cast(Any, pointer), c.POINTER(Variant)).contents.vt)
            return 0

    class Accessibility:
        @staticmethod
        def AccessibleChildren(  # noqa: N802 - Windows API spelling
            _pointer: object,
            _start: int,
            count: int,
            values: c.Array[Variant],
            obtained: object,
        ) -> int:
            """Return one simple child and one object child."""
            assert count == 2
            values[0].vt, values[0].integer = VT_I4, 7
            values[1].vt, values[1].pointer = 9, 123
            c.cast(cast(Any, obtained), c.POINTER(c.c_int32)).contents.value = 2
            return 0

    adapter.auto = cast("c.WinDLL", Automation())
    adapter.acc = cast("c.WinDLL", Accessibility())

    def call(
        _self: Com, slot: int, _types: tuple[type[c._CData], ...], *args: object
    ) -> int:
        assert slot == 8
        c.cast(cast(Any, args[0]), c.POINTER(c.c_int32)).contents.value = 2
        return 0

    def query(_self: NativeMsaa, pointer: c.c_void_p) -> Com:
        queried.append(pointer.value)
        return Com(c.c_void_p(456), lambda _: None)

    monkeypatch.setattr(Com, "call", call)
    monkeypatch.setattr(NativeMsaa, "_query", query)
    ref = Com(c.c_void_p(1), lambda _: None)
    parts = adapter.parts(Node(ref), 5)
    assert cast(Node, parts[0]).child_id == 7
    assert cast(Node, parts[1]).accessible.pointer.value == 456
    assert queried == [123]
    assert cleared == [VT_I4, 0]


def test_close_releases_once_without_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ownership loss does not prevent reference cleanup; close is idempotent."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    events: list[dict[str, object]] = []
    adapter.emit = events.append
    released: list[int | None] = []
    uninitialized: list[bool] = []
    adapter.refs = [Com(c.c_void_p(i), lambda _: None) for i in (1, 2)]
    adapter.initialized = True
    adapter.guard = lambda: pytest.fail("cleanup must not require retained ownership")

    class Ole:
        @staticmethod
        def CoUninitialize() -> None:  # noqa: N802 - Windows API spelling
            """Record the single matched apartment cleanup."""
            uninitialized.append(True)

    adapter.ole = cast("c.WinDLL", Ole())
    monkeypatch.setattr(
        Com, "release", lambda self: released.append(self.pointer.value)
    )
    adapter.close()
    adapter.close()
    assert released == [2, 1]
    assert uninitialized == [True]
    assert [e["operation"] for e in events if e["event"] == "call_start"] == [
        "msaa.cleanup.Release",
        "msaa.cleanup.Release",
        "msaa.cleanup.CoUninitialize",
    ]
    assert [e["event"] for e in events] == ["call_start", "call_end"] * 3


def test_read_guards_before_after_and_uses_common_watchdog_schema() -> None:
    """Both ownership checks precede consumption; oleacc gets the normal watchdog."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    timeline: list[str] = []
    events: list[dict[str, object]] = []
    adapter.guard = lambda: timeline.append("guard")
    adapter.emit, adapter.calls = events.append, 0

    def function() -> int:
        timeline.append("native")
        return 1

    assert adapter._read("AccessibleChildren", function) == 1
    timeline.append("consumed")
    assert timeline == ["guard", "native", "guard", "consumed"]
    assert [event["event"] for event in events] == ["call_start", "call_end"]
    assert all(event["operation"] == "msaa.AccessibleChildren" for event in events)
    assert events[-1]["hresult"] == 1


def test_post_call_guard_failure_retains_hresult_and_owned_output() -> None:
    """A COM result is retained for cleanup even if ownership changed during RPC."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    events: list[dict[str, object]] = []
    adapter.emit, adapter.calls, adapter.refs = events.append, 0, []
    checks = 0
    output = c.c_void_p()

    def guard() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("ownership changed during call")

    def function() -> int:
        output.value = 123
        return 0

    adapter.guard = guard
    with pytest.raises(RuntimeError, match="ownership changed"):
        adapter._read("AccessibleObjectFromPoint", function, owned_output=output)
    assert checks == 2
    assert events[-1]["event"] == "call_end"
    assert events[-1]["hresult"] == 0
    assert len(adapter.refs) == 1
    assert adapter.refs[0].pointer is output
    assert adapter._own(output) is adapter.refs[0]
    assert len(adapter.refs) == 1


def test_variant_cleanup_failure_does_not_replace_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guarded RPC failure stays primary even if VariantClear itself fails."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    events: list[dict[str, object]] = []
    adapter.emit, adapter.calls = events.append, 0
    checks = 0

    def guard() -> None:
        nonlocal checks
        checks += 1

    class Automation:
        @staticmethod
        def VariantClear(_pointer: object) -> int:  # noqa: N802 - Windows API spelling
            """Simulate failed remote cleanup without throwing away the first error."""
            return -2147467259

    adapter.auto = cast("c.WinDLL", Automation())
    adapter.guard = guard
    monkeypatch.setattr(Com, "call", lambda *_args: -2147024891)
    with pytest.raises(OSError, match="80070005"):
        adapter._property(Node(Com(c.c_void_p(1), events.append)), 13, "get_accRole")
    assert checks == 2
    assert events[-2]["event"] == "call_start"
    assert events[-2]["operation"] == "msaa.cleanup.VariantClear"
    assert events[-1]["event"] == "call_end"
    assert events[-1]["hresult"] == -2147467259
    assert adapter.cleanup_errors == [
        {"operation": "msaa.cleanup.VariantClear", "error": "HRESULT 0x80004005"},
    ]
    with pytest.raises(RuntimeError, match="cleanup failed"):
        adapter._read(
            "no later RPC", lambda: pytest.fail("provider must not be called")
        )
    assert checks == 2


def test_cleanup_exception_preserves_active_failure() -> None:
    """Cleanup errors are recorded without masking an active exception."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = []
    events: list[dict[str, object]] = []
    adapter.emit = events.append
    adapter.guard = lambda: pytest.fail("cleanup must not use ownership guard")

    def cleanup() -> None:
        raise OSError("cleanup exception")

    def fail_then_clean() -> None:
        try:
            raise RuntimeError("original provider failure")
        finally:
            adapter._cleanup("Release", cleanup)

    with pytest.raises(RuntimeError, match="original provider failure"):
        fail_then_clean()
    assert events[-1]["error"] == "OSError('cleanup exception')"
    assert adapter.cleanup_errors[0]["operation"] == "msaa.cleanup.Release"


@pytest.mark.parametrize("first", [None, "original provider failure"])
def test_cleanup_receipt_marks_failure_without_overwriting_first(
    first: str | None,
) -> None:
    """A cleanup-only failure cannot look complete or replace an earlier failure."""
    adapter = object.__new__(NativeMsaa)
    adapter.cleanup_errors = [{"operation": "msaa.cleanup.Release", "error": "failed"}]
    report: dict[str, object] = {"diagnostic_completed": True, "first_failure": first}
    _record_cleanup(report, adapter)
    assert report["diagnostic_completed"] is False
    assert report["first_failure"] == (
        first or "MSAA cleanup failed; inspect cleanup_errors"
    )
    assert report["cleanup_errors"] == adapter.cleanup_errors
