"""Explicit bounded discovery settling never retries a native write or error."""

from __future__ import annotations

import argparse
import ctypes as c
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.panel_uia import PanelUia, Structure
from scripts.panel_uia_probe import NativeGuard, Probe

STATE = Structure(123, 0, 50033, "Chrome", (10, 20, 100, 200), offscreen=False)
CHAIN_END = "No vertical ScrollPattern: ancestor chain ended after 1 nodes"


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    """Supply no native objects, and a deterministic clock for the fixed wait."""
    instance = object.__new__(Probe)
    instance.args = argparse.Namespace(x=50, y=50, settle_discovery=True)
    instance.uia = object.__new__(PanelUia)
    instance.uia.target = None
    instance.uia.ancestors = []
    instance.guard = object.__new__(NativeGuard)
    instance.report = {}
    now = [0.0]

    def sleep(seconds: float) -> None:
        assert 0 < seconds <= 0.05
        now[0] += seconds

    def no_write(*_args: object) -> None:
        pytest.fail("No mutating operation is permitted before verified discovery")

    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: now[0])
    monkeypatch.setattr("scripts.panel_uia_probe.time.sleep", sleep)
    monkeypatch.setattr(NativeGuard, "check", lambda _: None)
    monkeypatch.setattr(Probe, "emit", lambda *_: None)
    monkeypatch.setattr(PanelUia, "set_vertical", no_write)
    monkeypatch.setattr(Probe, "seek", no_write)
    return instance


@pytest.mark.parametrize("delayed_success", [False, True])
def test_one_delayed_lookup_keeps_client_and_cold_receipt(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
    *,
    delayed_success: bool,
) -> None:
    """A cold miss permits one second of settling, never an unbounded loop."""
    calls = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(self)
        assert self.ancestors == []
        self.ancestors.append(STATE)
        if delayed_success and len(calls) == 2:
            return STATE
        raise OSError(CHAIN_END)

    monkeypatch.setattr(PanelUia, "discover", discover)
    if delayed_success:
        assert probe.discover_target() == STATE
    else:
        with pytest.raises(OSError, match="chain ended"):
            probe.discover_target()
    assert len(calls) == 2
    assert calls[0] is calls[1]
    cold = probe.report["discovery_cold"]
    delayed = probe.report["discovery_delayed"]
    assert isinstance(cold, dict)
    assert isinstance(delayed, dict)
    assert cold["error"] == repr(OSError(CHAIN_END))
    assert len(cold["ancestors"]) == 1
    assert len(delayed["ancestors"]) == 1
    assert cold["monotonic"] == 0
    assert delayed["monotonic"] == pytest.approx(1)


@pytest.mark.parametrize("outcome", ["target", "HRESULT", "bound", "wrong_count"])
def test_only_exact_normal_miss_permits_second_read(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Success, failed RPC, bounded traversal and mismatched evidence do not retry."""
    calls = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        if outcome == "target":
            return STATE
        if outcome == "bound":
            raise OSError("No vertical ScrollPattern: reached bound of 32 ancestors")
        if outcome == "wrong_count":
            raise OSError(
                "No vertical ScrollPattern: ancestor chain ended after 2 nodes"
            )
        raise OSError("GetCurrentPatternAs failed: -1")

    monkeypatch.setattr(PanelUia, "discover", discover)
    if outcome == "target":
        assert probe.discover_target() == STATE
    else:
        with pytest.raises(OSError, match=r"GetCurrentPatternAs|No vertical"):
            probe.discover_target()
    assert calls == [1]
    assert "discovery_delayed" not in probe.report


def test_flag_absent_preserves_single_lookup(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The WPF path still performs exactly one discovery without a wait."""
    probe.args.settle_discovery = False
    calls = []

    def discover(_self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        raise OSError(CHAIN_END)

    monkeypatch.setattr(PanelUia, "discover", discover)
    with pytest.raises(OSError, match="chain ended"):
        probe.discover_target()
    assert calls == [1]
    assert probe.report == {}


def test_ownership_loss_during_wait_stops_lookup(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Input loss during settling prohibits the delayed read and every write."""
    calls = []
    checks = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        raise OSError(CHAIN_END)

    def check(_self: NativeGuard) -> None:
        checks.append(1)
        if len(checks) == 3:
            raise RuntimeError("Input changed")

    monkeypatch.setattr(PanelUia, "discover", discover)
    monkeypatch.setattr(NativeGuard, "check", check)
    with pytest.raises(RuntimeError, match="Input changed"):
        probe.discover_target()
    assert calls == [1]
    assert "discovery_delayed" not in probe.report


@pytest.mark.parametrize("foreign", [False, True])
def test_execute_never_scrolls_without_verified_target(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    foreign: bool,
) -> None:
    """Even delayed discovery cannot bypass the original target ownership gate."""
    manifest = tmp_path / "fixture.json"
    manifest.write_text(json.dumps({"root_hwnd": 456}), encoding="utf-8")
    probe.args.fixture_manifest = manifest
    probe.args.expected_pid = 123

    def pid(_hwnd: int, pointer: Any) -> int:  # noqa: ANN401 - ctypes boundary fake
        c.cast(pointer, c.POINTER(c.c_uint32)).contents.value = 123
        return 1

    def guard_init(self: NativeGuard, *_args: object) -> None:
        monkeypatch.setattr(
            self, "user", SimpleNamespace(GetWindowThreadProcessId=pid), raising=False
        )

    def panel_init(self: PanelUia, _emit: object) -> None:
        self.target = None
        self.ancestors = []

    calls = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        if foreign and len(calls) == 2:
            return replace(STATE, pid=999)
        raise OSError(CHAIN_END)

    monkeypatch.setattr(NativeGuard, "__init__", guard_init)
    monkeypatch.setattr(PanelUia, "__init__", panel_init)
    monkeypatch.setattr(PanelUia, "discover", discover)
    monkeypatch.setattr(PanelUia, "inspect_children", lambda _: [])
    with pytest.raises(
        (OSError, RuntimeError), match=r"chain ended|Target PID/visibility"
    ):
        probe.execute()
    assert calls == [1, 1]


@pytest.mark.parametrize(
    "outcome",
    ["normal", "cold_success", "HRESULT", "bound", "wrong_count", "deadline", "guard"],
)
def test_raw_readiness_uses_same_classifier_and_budget(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Only the original completed normal miss admits one final observation."""
    probe.args.raw_geometry_diagnostic = True
    probe.raw_deadline = 0.5 if outcome == "deadline" else 30.0
    probe.raw_clock_floor = 0.0
    calls = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        if outcome == "cold_success" or len(calls) == 2:
            return STATE
        if outcome == "HRESULT":
            raise OSError("GetCurrentPatternAs failed: -1")
        if outcome == "bound":
            raise OSError("No vertical ScrollPattern: reached bound of 32 ancestors")
        if outcome == "wrong_count":
            raise OSError(
                "No vertical ScrollPattern: ancestor chain ended after 2 nodes"
            )
        if outcome == "guard":
            monkeypatch.setattr(
                NativeGuard,
                "check",
                lambda _: (_ for _ in ()).throw(RuntimeError("guard lost")),
            )
        raise OSError(CHAIN_END)

    monkeypatch.setattr(PanelUia, "discover", discover)
    if outcome in {"normal", "cold_success"}:
        assert probe.discover_target() == STATE
    else:
        with pytest.raises((RuntimeError, OSError)):
            probe.discover_target()
    assert len(calls) == (2 if outcome == "normal" else 1)


@pytest.mark.parametrize("when", [30.0, 30.001, float("nan"), -1])
def test_raw_second_read_deadline_admission(
    probe: Probe, monkeypatch: pytest.MonkeyPatch, when: float
) -> None:
    """An expired/regressed clock cannot admit even the first native lookup."""
    probe.args.raw_geometry_diagnostic = True
    probe.raw_deadline, probe.raw_clock_floor = 30.0, 0.0
    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: when)
    with pytest.raises(RuntimeError, match="deadline/regression"):
        probe.discover_target()


@pytest.mark.parametrize("duration", [3, float("nan"), None, True])
def test_raw_completed_call_error_cannot_be_readiness_miss(
    tmp_path: Path, duration: object
) -> None:
    """Completed native duration rejection is synchronous after preserved log."""
    instance = object.__new__(Probe)
    instance.args = argparse.Namespace(raw_geometry_diagnostic=True)
    with (tmp_path / "calls").open("w") as stream:
        instance.log = stream
        with pytest.raises(RuntimeError, match="duration"):
            instance.emit({"event": "call_end", "slot": 14, "seconds": duration})
    assert '"call_end"' in (tmp_path / "calls").read_text()


@pytest.mark.parametrize("expired", [30.0, 30.001])
def test_raw_slow_discovery_log_prevents_native_read(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
    expired: float,
) -> None:
    """Logging cannot spend the remaining budget then admit discovery."""
    probe.args.raw_geometry_diagnostic = True
    probe.raw_deadline, probe.raw_clock_floor = 30.0, 0.0
    now = [0.0]
    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: now[0])

    def emit(_self: Probe, event: dict[str, object]) -> None:
        if event["event"] == "discovery_start":
            now[0] = expired

    monkeypatch.setattr(Probe, "emit", emit)

    def forbidden(*_args: object) -> Structure:
        pytest.fail("Expired discovery must not enter native provider")

    monkeypatch.setattr(PanelUia, "discover", forbidden)
    with pytest.raises(RuntimeError, match="deadline"):
        probe.discover_target()
    cold = probe.report["discovery_cold"]
    assert isinstance(cold, dict)
    assert "error" in cold


@pytest.mark.parametrize("expired", [30.0, 30.001])
def test_raw_call_start_flush_checks_deadline(
    monkeypatch: pytest.MonkeyPatch,
    expired: float,
) -> None:
    """The adapter's pre-COM emit boundary fails synchronously after slow flush."""
    from unittest.mock import Mock  # noqa: PLC0415 - isolated fake log

    instance = object.__new__(Probe)
    instance.args = argparse.Namespace(raw_geometry_diagnostic=True)
    instance.raw_deadline, instance.raw_clock_floor = 30.0, 0.0
    now = [0.0]
    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: now[0])
    instance.log = Mock()
    instance.log.flush.side_effect = lambda: now.__setitem__(0, expired)
    with pytest.raises(RuntimeError, match="deadline"):
        instance.emit({"event": "call_start", "slot": 14})
    instance.log.flush.assert_called_once()
