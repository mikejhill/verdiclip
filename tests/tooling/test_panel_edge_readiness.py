"""Read-only cold/delayed discovery with no native desktop interaction."""

from __future__ import annotations

import argparse
import ctypes as c
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.panel_edge_readiness import ReadinessProbe, normal_chain_end
from scripts.panel_uia import PanelUia, Structure
from scripts.panel_uia_probe import NativeGuard

STATE = Structure(123, 0, 50033, "Chrome", (10, 20, 100, 200), offscreen=False)
CHAIN_END = "No vertical ScrollPattern: ancestor chain ended after 1 nodes"


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReadinessProbe:
    """Initialize only Python test doubles, never COM or user32."""
    manifest = tmp_path / "fixture.json"
    manifest.write_text(json.dumps({"root_hwnd": 456}), encoding="utf-8")

    def pid(_hwnd: int, pointer: Any) -> int:  # noqa: ANN401 - ctypes fake
        c.cast(pointer, c.POINTER(c.c_uint32)).contents.value = 123
        return 1

    def guard_init(self: NativeGuard, *_args: object) -> None:
        monkeypatch.setattr(
            self, "user", SimpleNamespace(GetWindowThreadProcessId=pid), raising=False
        )

    def panel_init(self: PanelUia, _emit: object) -> None:
        self.target = None
        self.ancestors = []

    def forbidden(*_args: object) -> None:
        pytest.fail("Readiness experiment must never issue scrolling writes")

    monkeypatch.setattr(NativeGuard, "__init__", guard_init)
    monkeypatch.setattr(NativeGuard, "check", lambda _: None)
    monkeypatch.setattr(PanelUia, "__init__", panel_init)
    monkeypatch.setattr(PanelUia, "close", lambda _: None)
    monkeypatch.setattr(PanelUia, "set_vertical", forbidden)
    return ReadinessProbe(
        argparse.Namespace(
            x=40,
            y=50,
            expected_pid=123,
            fixture_manifest=manifest,
            output=tmp_path / "probe",
        )
    )


@pytest.mark.parametrize("delayed_success", [False, True])
def test_two_observations_share_client_and_keep_cold_failure(
    probe: ReadinessProbe,
    monkeypatch: pytest.MonkeyPatch,
    *,
    delayed_success: bool,
) -> None:
    """One normal cold miss permits exactly one delayed observation, never writes."""
    clients = []
    waits = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        clients.append(self)
        assert self.ancestors == []
        self.ancestors.append(STATE)
        if len(clients) == 2 and delayed_success:
            return STATE
        raise OSError(CHAIN_END)

    monkeypatch.setattr(PanelUia, "discover", discover)
    monkeypatch.setattr(ReadinessProbe, "wait_once", lambda _: waits.append(1))
    assert probe.run() == 0
    assert len(clients) == 2
    assert clients[0] is clients[1]
    assert waits == [1]
    assert probe.report["finished"] is True
    assert probe.report["diagnostic_completed"] is True
    assert probe.report["read_only"] is True
    assert probe.report["capture_checkpoint_passed"] is False
    assert probe.observations[0]["error"] == repr(OSError(CHAIN_END))
    assert probe.observations[1]["outcome"] == (
        "target_found" if delayed_success else "no_pattern"
    )
    saved = json.loads((probe.output / "manifest.json").read_text())
    assert len(saved["observations"]) == 2


@pytest.mark.parametrize("outcome", ["target", "HRESULT failure", "bound", "input"])
def test_no_delayed_observation_after_success_or_abnormal_failure(
    probe: ReadinessProbe,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """No fallback on native errors, traversal exhaustion, or ownership changes."""
    calls = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        if outcome == "target":
            return STATE
        if outcome == "bound":
            raise OSError("No vertical ScrollPattern: reached bound of 32 ancestors")
        if outcome == "input":
            raise RuntimeError("Input changed")
        raise OSError(outcome)

    def forbidden(_self: ReadinessProbe) -> None:
        pytest.fail("Unexpected delayed observation")

    monkeypatch.setattr(PanelUia, "discover", discover)
    monkeypatch.setattr(ReadinessProbe, "wait_once", forbidden)
    assert probe.run() == (0 if outcome == "target" else 1)
    assert calls == [1]
    assert probe.report["finished"] is (outcome == "target")
    assert probe.report["diagnostic_completed"] is (outcome == "target")
    assert probe.report["capture_checkpoint_passed"] is False


def test_fixed_delay_checks_ownership_and_has_no_discovery(
    probe: ReadinessProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-second delay checks ownership, rather than polling the provider."""
    probe.guard = object.__new__(NativeGuard)
    now = [0.0]
    checks = []
    sleeps = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("scripts.panel_edge_readiness.time.monotonic", lambda: now[0])
    monkeypatch.setattr("scripts.panel_edge_readiness.time.sleep", sleep)
    monkeypatch.setattr(NativeGuard, "check", lambda _: checks.append(now[0]))
    probe.wait_once()
    assert now[0] == pytest.approx(1)
    assert len(checks) > 1
    assert max(sleeps) <= 0.05
    probe.log.close()


def test_chain_end_requires_exact_diagnostic_and_count() -> None:
    """Do not mistake provider errors or bounds for normal absence."""
    assert normal_chain_end(OSError(CHAIN_END), 1)
    assert not normal_chain_end(OSError(CHAIN_END), 2)
    assert not normal_chain_end(OSError("HRESULT failure"), 1)
    assert not normal_chain_end(OSError(CHAIN_END), 0)


def test_ownership_loss_during_delay_stops_before_second_observation(
    probe: ReadinessProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No read or write follows detected external interference."""
    calls = []
    checks = []

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        calls.append(1)
        self.ancestors.append(STATE)
        raise OSError(CHAIN_END)

    def check(_self: NativeGuard) -> None:
        checks.append(1)
        if len(checks) == 3:
            raise RuntimeError("Input changed during delay")

    monkeypatch.setattr(PanelUia, "discover", discover)
    monkeypatch.setattr(NativeGuard, "check", check)
    assert probe.run() == 1
    assert calls == [1]
    assert probe.report["finished"] is False
    assert probe.report["diagnostic_completed"] is False
    assert probe.report["first_failure"] == repr(
        RuntimeError("Input changed during delay")
    )


def test_cleanup_failure_cannot_claim_finished(
    probe: ReadinessProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the terminal manifest even when native reference cleanup fails."""
    monkeypatch.setattr(PanelUia, "discover", lambda *_: STATE)

    def close(_self: PanelUia) -> None:
        raise OSError("Release failed")

    monkeypatch.setattr(PanelUia, "close", close)
    assert probe.run() == 1
    assert probe.report["finished"] is False
    assert probe.report["diagnostic_completed"] is False
    saved = json.loads((probe.output / "manifest.json").read_text())
    assert saved["cleanup_failure"] == repr(OSError("Release failed"))
