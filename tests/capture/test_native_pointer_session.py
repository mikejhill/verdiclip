"""Actual native-session methods against injected functions, never user32."""

import ctypes
import os
from types import SimpleNamespace
from typing import cast

import pytest

from verdiclip.capture.input_integrity import GuiThreadInfo, InputIntegrityGuard
from verdiclip.capture.native_pointer import NativePointerRoute, PointerPhase
from verdiclip.capture.native_pointer_win32 import (
    NativePointerSession,
    OriginalRecipientIntegrity,
)
from verdiclip.geometry import Point


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 1.5, 2**31, -(2**31) - 1]
)
def test_invalid_nudge_coordinates_refuse_before_native_write(value: float) -> None:
    """Reject nonintegral, nonfinite and overflowing native coordinate values."""
    assert not NativePointerSession._valid(Point(value, 0))


def test_reentrant_cancel_after_proof_prevents_nudge() -> None:
    """A positive stale proof cannot authorize a cursor write after retirement."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    route.admit(7, lambda: True)
    calls: list[tuple[int, int]] = []

    def safe(_point: Point) -> bool:
        route.close()
        return True

    fake = SimpleNamespace(
        route=route,
        safe=safe,
        _valid=NativePointerSession._valid,
        _owned=lambda: route.phase is PointerPhase.OWNED,
        api=SimpleNamespace(SetPhysicalCursorPos=lambda x, y: calls.append((x, y))),
    )
    result = NativePointerSession.nudge(cast(NativePointerSession, fake), Point(1, 2))
    assert not result
    assert not calls


@pytest.mark.parametrize("actual", [Point(2, 2), Point(1, 2)])
def test_nudge_uses_one_write_and_exact_readback(actual: Point) -> None:
    """Never retry an incorrect readback or manufacture its intended coordinate."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    route.admit(7, lambda: True)
    calls: list[tuple[int, int]] = []

    def write(x: int, y: int) -> bool:
        calls.append((x, y))
        return True

    fake = SimpleNamespace(
        route=route,
        safe=lambda _point: True,
        _valid=NativePointerSession._valid,
        _owned=lambda: route.phase is PointerPhase.OWNED,
        api=SimpleNamespace(SetPhysicalCursorPos=write),
        cursor=lambda: actual,
        move=lambda point: route.move(point, lambda: True),
    )
    result = NativePointerSession.nudge(cast(NativePointerSession, fake), Point(1, 2))
    assert result is (actual == Point(1, 2))
    assert calls == [(1, 2)]
    assert (route.phase is PointerPhase.CLOSED) is (actual != Point(1, 2))


@pytest.mark.parametrize("stage", ["cursor", "hit", "closed"])
def test_natural_cursor_read_failure_or_stale_callback_is_closed(stage: str) -> None:
    """Queued callbacks must not leak ordinary read failures or reopen retirement."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    if stage == "closed":
        route.close()
    calls: list[str] = []

    def cursor() -> Point:
        calls.append("cursor")
        if stage == "cursor":
            raise OSError("not exposed")
        return Point(1, 2)

    def hit(_point: Point) -> int:
        raise ValueError("not exposed")

    fake = SimpleNamespace(route=route, cursor=cursor, _hit=hit, safe=lambda: True)
    result = NativePointerSession.natural_cursor(cast(NativePointerSession, fake), 7)
    assert not result
    assert route.phase is PointerPhase.CLOSED
    if stage == "closed":
        assert not calls


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "original_pid",
        "owned_pid",
        "foreground",
        "focus",
        "current_hit",
        "target_hit",
        "integrity",
    ],
)
def test_actual_safe_guard_preserves_original_recipient_and_current_owned_hit(
    fault: str,
) -> None:
    """Neither a target hit nor own process can conceal a changed foreign owner."""
    current = Point(1, 2)
    target = Point(2, 2)
    fake = SimpleNamespace(
        _foreground=10,
        _focus=11,
        _original=12,
        _pid=13,
        _windows=frozenset({7}),
        _state=lambda: (
            20 if fault == "foreground" else 10,
            21 if fault == "focus" else 11,
        ),
        _process=lambda hwnd: (
            (14 if fault == "original_pid" else 13)
            if hwnd == 12
            else (0 if fault == "owned_pid" else os.getpid())
        ),
        cursor=lambda: current,
        _hit=lambda point: (
            99
            if (point == current and fault == "current_hit")
            or (point == target and fault == "target_hit")
            else 7
        ),
        _integrity=SimpleNamespace(permits=lambda _: fault != "integrity"),
    )
    assert NativePointerSession.safe(cast(NativePointerSession, fake), target) is (
        fault == "none"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "capture",
        "menu",
        "move",
        "flags",
        "active",
        "changed",
        "missing",
        "query",
    ],
)
def test_actual_gui_snapshot_refuses_protected_or_incoherent_state(fault: str) -> None:
    """Use real structure decoding with fake read-only native functions."""
    values = iter([0 if fault == "missing" else 10, 20 if fault == "changed" else 10])

    def read(_thread: int, address: int) -> bool:
        info = ctypes.cast(address, ctypes.POINTER(GuiThreadInfo)).contents
        info.active = 20 if fault == "active" else 10
        info.focus = 11
        if fault in {"capture", "menu", "move"}:
            setattr(info, fault, 22)
        if fault == "flags":
            info.flags = 0x10
        return fault != "query"

    fake = SimpleNamespace(
        api=SimpleNamespace(
            GetForegroundWindow=lambda: next(values), GetGUIThreadInfo=read
        )
    )
    if fault == "none":
        assert NativePointerSession._state(cast(NativePointerSession, fake)) == (10, 11)
    else:
        with pytest.raises(ValueError, match="Native pointer"):
            NativePointerSession._state(cast(NativePointerSession, fake))


@pytest.mark.parametrize(
    ("actual", "pid", "expected"), [(7, 13, 12), (99, 13, 99), (7, 14, 7)]
)
def test_integrity_substitution_only_for_exact_owned_hit(
    monkeypatch: pytest.MonkeyPatch,
    actual: int,
    pid: int,
    expected: int,
) -> None:
    """An unrelated hit or changed original PID cannot inherit saved trust."""
    monkeypatch.setattr(
        InputIntegrityGuard, "_pointer_recipient", lambda _self, _info: actual
    )
    guard = object.__new__(OriginalRecipientIntegrity)
    guard._session = cast(
        NativePointerSession,
        SimpleNamespace(
            _windows=frozenset({7}),
            _original=12,
            _pid=13,
            _process=lambda _: pid,
        ),
    )
    assert guard._pointer_recipient(GuiThreadInfo()) == expected


@pytest.mark.parametrize("fault", ["none", "process", "root"])
def test_binding_requires_owned_top_level_handles(fault: str) -> None:
    """An owned child HWND is not accepted as the selection top-level surface."""
    route = NativePointerRoute()
    fake = SimpleNamespace(
        route=route,
        _process=lambda _: 0 if fault == "process" else os.getpid(),
        api=SimpleNamespace(
            GetAncestor=lambda hwnd, _flag: hwnd + 1 if fault == "root" else hwnd
        ),
    )
    if fault == "none":
        NativePointerSession.bind(cast(NativePointerSession, fake), frozenset({7}))
        assert fake._windows == frozenset({7})
    else:
        with pytest.raises(ValueError, match="not owned"):
            NativePointerSession.bind(cast(NativePointerSession, fake), frozenset({7}))
        assert not hasattr(fake, "_windows")
