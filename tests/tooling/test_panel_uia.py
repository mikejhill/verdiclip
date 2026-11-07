"""Portable checks of the experimental probe's native ABI and safety bounds."""

from __future__ import annotations

import ctypes as c
from dataclasses import replace
from typing import Any, cast

import pytest
from scripts.panel_uia import Com, Guid, PanelUia, Point, Rect, ScrollState, Structure
from scripts.panel_uia_probe import Probe


def test_native_layouts() -> None:
    """POINT/RECT are int32, GUID Windows-endian, HWND pointer-sized."""
    assert c.sizeof(Point) == 8
    assert c.sizeof(Rect) == 16
    assert Rect.bottom.offset == 12
    assert bytes(Guid.parse("00112233-4455-6677-8899-aabbccddeeff")) == bytes.fromhex(
        "33221100554477668899aabbccddeeff"
    )


def test_com_call_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise real vtable indirection with a portable callback convention."""
    monkeypatch.setattr(c, "WINFUNCTYPE", c.CFUNCTYPE, raising=False)
    calls: list[int] = []

    @c.CFUNCTYPE(c.c_int32, c.c_void_p, c.POINTER(c.c_int32))
    def getter(_this: int, result: Any) -> int:  # noqa: ANN401 - ctypes callback
        result[0] = 123
        return 0

    @c.CFUNCTYPE(c.c_uint32, c.c_void_p)
    def release(_this: int) -> int:
        calls.append(2)
        return 0

    table = (c.c_void_p * 4)()
    table[2] = c.cast(release, c.c_void_p)
    table[3] = c.cast(getter, c.c_void_p)
    interface = c.pointer(c.cast(table, c.POINTER(c.c_void_p)))
    events: list[dict[str, object]] = []
    reference = Com(c.cast(interface, c.c_void_p), events.append)
    assert reference.read(3, c.c_int32).value == 123
    assert [event["event"] for event in events] == ["call_start", "call_end"]
    reference.release()
    reference.release()
    assert calls == [2]
    with pytest.raises(RuntimeError, match="Released"):
        reference.read(3, c.c_int32)


def test_scroll_slots_preserve_horizontal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider slots are fixed and horizontal always receives NoScroll."""
    panel = object.__new__(PanelUia)
    panel.pattern = Com(c.c_void_p(1), lambda _: None)
    calls: list[tuple[int, tuple[object, ...]]] = []

    def checked(
        _self: Com, slot: int, _types: tuple[type[c._CData], ...], *args: object
    ) -> None:
        calls.append((slot, args))

    monkeypatch.setattr(Com, "checked", checked)
    panel.set_vertical(50)
    assert calls == [(4, (-1.0, 50))]
    for invalid in (-1, 101, float("nan")):
        with pytest.raises(ValueError, match="selected panel"):
            panel.set_vertical(invalid)
    assert len(calls) == 1


def test_discovery_and_children_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile endless provider cannot cause unbounded traversal."""
    panel = object.__new__(PanelUia)
    panel.target = None
    panel.ancestors = []
    panel.refs = []
    panel.emit = lambda _: None
    panel.uia = Com(c.c_void_p(1), panel.emit)
    node = Com(c.c_void_p(2), panel.emit)
    counts = {"structure": 0, "navigate": 0}
    state = Structure(1, 2, 50030, "WPF", (0, 0, 20, 20), offscreen=False)

    def structure(_self: PanelUia, _node: Com) -> Structure:
        counts["structure"] += 1
        return state

    def navigate(_self: PanelUia, _node: Com, _slot: int) -> Com:
        counts["navigate"] += 1
        return node

    monkeypatch.setattr(Com, "checked", lambda *_: None)
    monkeypatch.setattr(Com, "release", lambda *_: None)
    monkeypatch.setattr(PanelUia, "own", lambda *_: node)
    monkeypatch.setattr(PanelUia, "structure", structure)
    monkeypatch.setattr(PanelUia, "scroll_pattern", lambda *_: None)
    monkeypatch.setattr(PanelUia, "navigate", navigate)
    with pytest.raises(OSError, match="32 ancestors"):
        panel.discover(1, 1)
    assert counts == {"structure": 32, "navigate": 32}
    panel.target = node
    counts.update(structure=0, navigate=0)
    assert len(panel.inspect_children()) == 64
    assert counts == {"structure": 64, "navigate": 64}


def test_uncertain_scroll_never_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing/failed acknowledgement prohibits even a guard/retry attempt."""
    probe = object.__new__(Probe)
    probe.uncertain = True
    probe.initial = ScrollState(
        -1, 25, 100, 20, horizontal_scrollable=False, vertical_scrollable=True
    )
    probe.last = replace(probe.initial, vertical=100)
    calls: list[str] = []
    monkeypatch.setattr(Probe, "validate", lambda _: calls.append("validate"))
    probe.restore_after_failure()
    assert not calls


def test_scroll_state_uses_documented_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """All six ScrollPattern fields use SDK slots 5 through 10."""
    panel = object.__new__(PanelUia)
    panel.pattern = Com(c.c_void_p(1), lambda _: None)
    slots: list[tuple[int, type[c._CData]]] = []

    def read[T: c._CData](_self: Com, slot: int, kind: type[T]) -> T:
        slots.append((slot, kind))
        return cast("T", c.c_double(slot) if kind is c.c_double else c.c_int32(slot))

    monkeypatch.setattr(Com, "read", read)
    state = panel.read_state()
    assert (state.horizontal, state.vertical) == (5, 6)
    assert (state.horizontal_view, state.vertical_view) == (7, 8)
    assert state.horizontal_scrollable
    assert state.vertical_scrollable
    assert slots == [(i, c.c_double) for i in range(5, 9)] + [
        (9, c.c_int32),
        (10, c.c_int32),
    ]


def test_close_releases_before_uninitializing(monkeypatch: pytest.MonkeyPatch) -> None:
    """COM references precede CoUninitialize, including successful S_FALSE init."""
    panel = object.__new__(PanelUia)
    panel.refs = [Com(c.c_void_p(i), lambda _: None) for i in (1, 2)]
    panel.initialized = True
    released: list[int | str | None] = []

    class Ole:
        @staticmethod
        def CoUninitialize() -> None:  # noqa: N802 - native API fake
            released.append("uninitialize")

    monkeypatch.setattr(panel, "ole", Ole(), raising=False)
    monkeypatch.setattr(
        Com, "release", lambda self: released.append(self.pointer.value)
    )
    panel.close()
    panel.close()
    assert released == [2, 1, "uninitialize"]


def test_bounded_acknowledgement_accepts_quantized_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async initial readback then stable quantized midpoint needs one write."""
    from scripts.panel_uia_probe import NativeGuard  # noqa: PLC0415 - test fixture

    probe = object.__new__(Probe)
    probe.uia = object.__new__(PanelUia)
    probe.uia.target = Com(c.c_void_p(1), lambda _: None)
    probe.guard = object.__new__(NativeGuard)
    probe.initial = ScrollState(
        30, 25, 80, 20, horizontal_scrollable=True, vertical_scrollable=True
    )
    probe.last = replace(probe.initial, vertical=0)
    probe.identity = Structure(1, 2, 50030, "WPF", (0, 0, 20, 20), offscreen=False)
    current = replace(probe.initial, vertical=49.98)
    states = iter([probe.last, current, current, current])
    writes: list[float] = []
    monkeypatch.setattr(Probe, "emit", lambda *_: None)
    monkeypatch.setattr(Probe, "validate", lambda self: self.last)
    monkeypatch.setattr(NativeGuard, "check", lambda _: None)
    monkeypatch.setattr(PanelUia, "structure", lambda *_: probe.identity)
    monkeypatch.setattr(PanelUia, "read_state", lambda _: next(states))
    monkeypatch.setattr(PanelUia, "set_vertical", lambda _, p: writes.append(p))
    monkeypatch.setattr("scripts.panel_uia_probe.time.sleep", lambda _: None)
    probe.seek(50)
    assert writes == [50]
    assert probe.last == current
    assert not probe.uncertain


def test_horizontal_change_latches_uncertain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider ignoring NoScroll cannot trigger a blind restore."""
    from scripts.panel_uia_probe import NativeGuard  # noqa: PLC0415 - test fixture

    probe = object.__new__(Probe)
    probe.uia = object.__new__(PanelUia)
    probe.uia.target = Com(c.c_void_p(1), lambda _: None)
    probe.guard = object.__new__(NativeGuard)
    probe.initial = ScrollState(
        30, 25, 80, 20, horizontal_scrollable=True, vertical_scrollable=True
    )
    probe.last = probe.initial
    probe.identity = Structure(1, 2, 50030, "WPF", (0, 0, 20, 20), offscreen=False)
    writes: list[float] = []
    monkeypatch.setattr(Probe, "emit", lambda *_: None)
    monkeypatch.setattr(Probe, "validate", lambda self: self.last)
    monkeypatch.setattr(NativeGuard, "check", lambda _: None)
    monkeypatch.setattr(PanelUia, "structure", lambda *_: probe.identity)
    changed = replace(probe.initial, horizontal=0, vertical=0)
    monkeypatch.setattr(PanelUia, "read_state", lambda _: changed)
    monkeypatch.setattr(PanelUia, "set_vertical", lambda _, p: writes.append(p))
    with pytest.raises(RuntimeError, match="horizontal state changed"):
        probe.seek(0)
    assert probe.uncertain
    probe.restore_after_failure()
    assert writes == [0]
