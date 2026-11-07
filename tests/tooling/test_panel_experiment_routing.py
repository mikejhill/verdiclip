"""Separate diagnostics never imply capture success or permit restoration writes."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from scripts import panel_feasibility as supervisor
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia_probe import Probe
from tests.tooling.test_panel_full_sweep import FakeProbe

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("experiment", "expected"),
    [
        ("standard", ["--settle-discovery"]),
        ("clipping", ["--clipping-diagnostic"]),
        ("seam", ["--seam-sweep"]),
        ("msaa", ["--settle-discovery", "--msaa-diagnostic"]),
    ],
)
def test_explicit_probe_modes(experiment: str, expected: list[str]) -> None:
    """An experiment never silently falls back to another capture mechanism."""
    assert supervisor.probe_arguments({"kind": "owned-edge"}, experiment) == expected


def test_unknown_experiment_fails() -> None:
    """Reject unknown routing instead of activating a standard writer."""
    with pytest.raises(ValueError, match="Unknown"):
        supervisor.probe_arguments({"kind": "owned-wpf"}, "surprise")


def test_read_only_failure_cannot_restore(tmp_path: Path) -> None:
    """Even observed state drift does not authorize a write in a read-only probe."""
    fake = FakeProbe(tmp_path)
    probe = cast("Probe", fake)
    probe.args = argparse.Namespace(msaa_diagnostic=True)
    probe.last = None
    Probe.restore_after_failure(probe)
    assert fake.requests == []


@pytest.mark.parametrize("failure", [None, "incomplete", "state", "pixels"])
def test_msaa_diagnostic_requires_unchanged_read_only_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Completed read-only investigation is not full capture qualification."""
    directory = tmp_path / "probe"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "msaa_diagnostic_completed": failure != "incomplete",
                "read_only_unchanged": failure != "state",
            }
        ),
        encoding="utf-8",
    )
    before = Pixels(2, 2, bytes(12))
    after = Pixels(2, 2, bytes([1]) * 12) if failure == "pixels" else before
    monkeypatch.setattr(
        supervisor,
        "load_pixels",
        lambda path: after if path.name == "after.png" else before,
    )
    result: dict[str, Any] = {
        "msaa_diagnostic": True,
        "helper": {"exit_code": 0},
        "checkpoint_passed": False,
    }
    if failure is not None:
        with pytest.raises(RuntimeError, match="MSAA diagnostic"):
            supervisor.verify_result(result, tmp_path, tmp_path, {})
    else:
        supervisor.verify_result(result, tmp_path, tmp_path, {})
        assert result["diagnostic_completed"] is True
    assert result["checkpoint_passed"] is False
