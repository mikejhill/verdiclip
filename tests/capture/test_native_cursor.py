"""Fault injection for the hosted-only visibility experiment; no native calls."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.native_cursor import (
    CursorInfo,
    CursorState,
    probe_magnification_cursor,
    probe_owned_cursor_baseline,
)
from tests.capture.test_native_scrollbar_contract import FunctionPort


class CursorFixture:
    """Independent global visibility oracle and native call receipts."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int]] = []
        self.initial_flags = 1
        self.hidden = False
        self.effective = True
        self.reads = 0
        self.failure = ""
        self.result: dict[str, object] = {}
        for name, value in (
            ("GITHUB_ACTIONS", "true"),
            ("RUNNER_ENVIRONMENT", "github-hosted"),
            ("VERDICLIP_HOSTED_SCROLLBAR", "1"),
        ):
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(ctypes, "WinDLL", self.load, raising=False)

    def load(self, name: str, **_kwargs: object) -> SimpleNamespace:
        """Only the two reviewed Windows DLLs are permitted."""
        if name == "user32":
            return SimpleNamespace(
                GetCursorInfo=FunctionPort(self.observe),
                GetForegroundWindow=FunctionPort(lambda: 123),
            )
        assert name == "Magnification"
        self.calls.append(("load", 0))
        return SimpleNamespace(
            MagInitialize=FunctionPort(lambda: self.operation("initialize")),
            MagUninitialize=FunctionPort(lambda: self.operation("uninitialize")),
            MagShowSystemCursor=FunctionPort(self.show),
        )

    def operation(self, name: str) -> int:
        """Return deterministic API failure without touching any machine."""
        self.calls.append((name, 0))
        return int(self.failure != name)

    def show(self, value: int) -> int:
        """A reported failure can still change state, requiring restoration."""
        self.calls.append(("show", value))
        self.hidden = not value and self.effective
        return int(self.failure != ("restore" if value else "hide"))

    def observe(self, address: object) -> int:
        """Opaque handles and coordinates are transient, not receipt fields."""
        self.reads += 1
        if self.failure == "read" and self.reads == 2:
            raise OSError("Failed during observation")
        info = ctypes.cast(
            cast(ctypes.c_void_p, address), ctypes.POINTER(CursorInfo)
        ).contents
        info.flags = self.initial_flags & ~1 if self.hidden else self.initial_flags
        info.cursor = 456
        info.point.x = 10 + int(self.failure == "position" and self.reads == 2)
        info.point.y = 20
        return 1

    def run(self) -> None:
        """Run against deterministic native ports only."""
        probe_magnification_cursor(self.result, lambda: self.failure != "popup")


def test_cursor_metadata_has_no_handles_or_coordinates() -> None:
    """Only flags and a coarse shape-presence class leave the process."""
    assert CursorState(0, 0, 10, 20, 123).metadata() == {
        "flags": 0,
        "visible": False,
        "handle_class": "null",
    }


@pytest.mark.parametrize("flags", [0, 2])
def test_hidden_cursor_never_changes_unknown_magnification_state(
    monkeypatch: pytest.MonkeyPatch, flags: int
) -> None:
    """Hidden and touch-suppressed baselines cannot prove a visible transition."""
    fixture = CursorFixture(monkeypatch)
    fixture.initial_flags = flags
    fixture.run()
    assert fixture.calls == []
    assert fixture.result["visibility_verdict"] == "inconclusive-hidden-baseline"
    assert fixture.result["cleanup_confirmed"] is True


@pytest.mark.parametrize("effective", [False, True])
def test_visible_baseline_proves_transition_separately_from_cleanup(
    monkeypatch: pytest.MonkeyPatch, *, effective: bool
) -> None:
    """Exercise the observed outcome without a native DLL or input side effect."""
    fixture = CursorFixture(monkeypatch)
    fixture.effective = effective
    fixture.run()
    assert fixture.calls == [
        ("load", 0),
        ("initialize", 0),
        ("show", 0),
        ("show", 1),
        ("uninitialize", 0),
    ]
    assert fixture.result["visibility_verdict"] == (
        "visible-hidden-restored" if effective else "ineffective"
    )
    assert fixture.result["cleanup_confirmed"] is True
    assert fixture.result["during_preserved"] is True
    assert fixture.result["after_preserved"] is True
    assert fixture.hidden is False


@pytest.mark.parametrize(
    "failure", ["hide", "restore", "uninitialize", "position", "read"]
)
def test_native_failure_still_restores_and_uninitializes(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Exercise the observed outcome without a native DLL or input side effect."""
    fixture = CursorFixture(monkeypatch)
    fixture.failure = failure
    with pytest.raises((AssertionError, OSError)):
        fixture.run()
    assert fixture.calls[-2:] == [("show", 1), ("uninitialize", 0)]
    assert fixture.hidden is False
    assert fixture.result["visibility_verdict"] == "incomplete"
    if failure in {"restore", "uninitialize", "read"}:
        assert fixture.result["cleanup_confirmed"] is False


@pytest.mark.parametrize("failure", ["initialize", "popup"])
def test_setup_failure_never_hides_cursor(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Exercise the observed outcome without a native DLL or input side effect."""
    fixture = CursorFixture(monkeypatch)
    fixture.failure = failure
    with pytest.raises(AssertionError):
        fixture.run()
    assert not any(name == "show" for name, _ in fixture.calls)


@pytest.mark.parametrize(
    "name", ["GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "VERDICLIP_HOSTED_SCROLLBAR"]
)
def test_each_hosted_guard_precedes_any_native_access(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Exercise the observed outcome without a native DLL or input side effect."""
    fixture = CursorFixture(monkeypatch)
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match="disposable hosted fixture"):
        fixture.run()
    assert fixture.reads == 0
    assert fixture.calls == []


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("becomes_visible", [False, True])
@pytest.mark.parametrize("read_failure", [False, True])
def test_owned_popup_baseline_balances_its_shape_and_count(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owned: bool,
    becomes_visible: bool,
    read_failure: bool,
) -> None:
    """A fixture-owned capture can show an arrow once, never borrow another queue."""
    fixture = CursorFixture(monkeypatch)
    fixture.initial_flags = 0
    count = -1 if becomes_visible else -2
    initial_count = count
    cursor = 456
    calls: list[tuple[str, int]] = []
    original_load = fixture.load

    def set_cursor(value: int) -> int:
        nonlocal cursor
        previous, cursor = cursor, value
        calls.append(("cursor", value))
        return previous

    def show(value: int) -> int:
        nonlocal count
        count += 1 if value else -1
        calls.append(("count", value))
        return count

    def observe(address: object) -> int:
        fixture.reads += 1
        if read_failure and fixture.reads == 3:
            raise OSError("Candidate baseline read failed")
        info = ctypes.cast(
            cast(ctypes.c_void_p, address), ctypes.POINTER(CursorInfo)
        ).contents
        info.flags = int(count >= 0 and not fixture.hidden)
        info.cursor = cursor
        info.point.x, info.point.y = 10, 20
        return 1

    def load(name: str, **kwargs: object) -> SimpleNamespace:
        api = original_load(name, **kwargs)
        if name == "user32":
            api.GetCursorInfo = FunctionPort(observe)
            api.GetCapture = FunctionPort(lambda: 123 if owned else 999)
            api.LoadCursorW = FunctionPort(lambda *_args: 777)
            api.SetCursor = FunctionPort(set_cursor)
            api.ShowCursor = FunctionPort(show)
        return api

    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    result: dict[str, object] = {}
    if owned and read_failure:
        with pytest.raises(OSError, match="Candidate baseline"):
            probe_owned_cursor_baseline(result, 123, lambda: True)
        assert result["cleanup_confirmed"] is False
    else:
        probe_owned_cursor_baseline(result, 123, lambda: True)
        assert result["cleanup_confirmed"] is True
        expected = (
            "inconclusive-no-owned-mouse-capture"
            if not owned
            else "visible-hidden-restored"
            if becomes_visible
            else "inconclusive-hidden-baseline"
        )
        assert result["visibility_verdict"] == expected
    assert count == initial_count
    assert cursor == 456
    assert calls == (
        [("cursor", 777), ("count", 1), ("count", 0), ("cursor", 456)] if owned else []
    )


def test_owned_baseline_refuses_local_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline setup retains the same host-only boundary as the candidate."""
    fixture = CursorFixture(monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS")
    with pytest.raises(RuntimeError, match="disposable hosted fixture"):
        probe_owned_cursor_baseline({}, 123, lambda: True)
    assert fixture.reads == 0
    assert fixture.calls == []
