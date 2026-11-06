"""Exercise actual Win32 bindings and failure handling with injected native calls."""

import ctypes
import ctypes.wintypes as w
import os
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from verdiclip.capture.input_integrity import GuiThreadInfo, InputIntegrityGuard
from verdiclip.capture.native_pointer import PointerPhase
from verdiclip.capture.native_pointer_win32 import NativePointerSession
from verdiclip.geometry import Point


class Call:
    """A callable ctypes-function stand-in that records ABI declarations."""

    def __init__(self, callback: Callable[..., object]) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        """Execute a pure callback, never a native function."""
        return self.callback(*args)


class Native:
    """Owned test process identities and coordinates with injectable failures."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.point = Point(20, 30)
        self.owned = False
        self.pid = 99
        self.permitted = True
        self.read_ok = self.gui_ok = self.write_ok = True
        self.writes: list[Point] = []
        self.integrity_constructed: list[object] = []
        self.clock = 100.0
        self.api = SimpleNamespace(
            GetForegroundWindow=Call(lambda: 101),
            GetAncestor=Call(lambda hwnd, _: hwnd),
            GetGUIThreadInfo=Call(self.gui),
            GetPhysicalCursorPos=Call(self.cursor),
            WindowFromPhysicalPoint=Call(lambda _: 201 if self.owned else 109),
            GetWindowThreadProcessId=Call(self.process),
            SetPhysicalCursorPos=Call(self.write),
        )
        monkeypatch.setattr(
            ctypes, "WinDLL", lambda *_args, **_kw: self.api, raising=False
        )

        def initialize(guard: object) -> None:
            self.integrity_constructed.append(guard)

        monkeypatch.setattr(InputIntegrityGuard, "__init__", initialize)
        monkeypatch.setattr(
            InputIntegrityGuard, "permits", lambda _self, _fg: self.permitted
        )
        monkeypatch.setattr(
            "verdiclip.capture.native_pointer_win32.time.monotonic", lambda: self.clock
        )

    def gui(self, _thread: int, address: int) -> bool:
        """Fill the actual ABI structure with coherent ordinary state."""
        info = ctypes.cast(address, ctypes.POINTER(GuiThreadInfo)).contents
        info.active, info.focus = 101, 102
        return self.gui_ok

    def cursor(self, address: int) -> bool:
        """Read fake physical coordinates into the real POINT layout."""
        point = ctypes.cast(address, ctypes.POINTER(w.POINT)).contents
        point.x, point.y = int(self.point.x), int(self.point.y)
        return self.read_ok

    def process(self, hwnd: int, address: int) -> int:
        """Return exact original or own PID, or a missing-window result."""
        pid = ctypes.cast(address, ctypes.POINTER(w.DWORD)).contents
        pid.value = os.getpid() if hwnd == 201 else self.pid
        return int(bool(pid.value))

    def write(self, x: int, y: int) -> bool:
        """Record one keyboard operation without moving a real cursor."""
        self.writes.append(Point(x, y))
        if self.write_ok:
            self.point = Point(x, y)
        return self.write_ok


def test_actual_binding_lifecycle_and_exact_keyboard_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover construction, declarations, admission, deadline, readback and close."""
    native = Native(monkeypatch)
    session = NativePointerSession()
    assert native.integrity_constructed == [session._integrity]
    assert native.api.GetAncestor.argtypes == [w.HWND, w.UINT]
    assert native.api.GetPhysicalCursorPos.restype is w.BOOL
    assert native.api.SetPhysicalCursorPos.argtypes == [ctypes.c_int, ctypes.c_int]
    assert not session.expired()
    assert session._process(0) == 0
    session.bind(frozenset({201}))
    native.owned = True
    operation_result_1 = session.natural_cursor(201)
    assert operation_result_1
    operation_result_2 = session.move(Point(20, 30))
    assert operation_result_2
    operation_result_3 = session.nudge(Point(21, 30))
    assert operation_result_3
    assert native.writes == [Point(21, 30)]
    assert session.route.point == Point(21, 30)
    assert not session.expired()
    native.clock = 101.0
    assert session.expired()
    session.close()
    assert session.route.phase is PointerPhase.CLOSED


@pytest.mark.parametrize("fault", ["pid", "integrity", "cursor", "gui"])
def test_constructor_refuses_missing_original_proof(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No incomplete original-recipient proof reaches any cursor write."""
    native = Native(monkeypatch)
    if fault == "pid":
        native.pid = 0
    elif fault == "integrity":
        native.permitted = False
    elif fault == "cursor":
        native.read_ok = False
    else:
        native.gui_ok = False
    with pytest.raises(ValueError, match="Native pointer"):
        NativePointerSession()
    assert not native.writes


def test_actual_native_failure_paths_do_not_retry_or_keep_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read refusal and write failure remain fail closed through actual methods."""
    native = Native(monkeypatch)
    session = NativePointerSession()
    session.bind(frozenset({201}))
    native.owned = True
    operation_result_4 = session.natural_cursor(201)
    assert operation_result_4
    with pytest.raises(ValueError, match="coordinates"):
        session._hit(Point(float("nan"), 0))
    native.gui_ok = False
    assert not session.safe()
    native.gui_ok = True
    native.write_ok = False
    operation_result_5 = session.nudge(Point(21, 30))
    assert not operation_result_5
    assert native.writes == [Point(21, 30)]
    assert session.route.phase is PointerPhase.CLOSED
