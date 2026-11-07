"""Portable full-outer browser collection controls; never call native APIs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import mss
import pytest
from scripts.panel_browser_sequence import BrowserSequence, sequence_offsets
from scripts.panel_uia import ScrollState
from scripts.panel_uia_probe import Probe
from tests.tooling.test_panel_full_sweep import FakeProbe


@pytest.fixture
def sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BrowserSequence:
    """Reuse pure provider fixtures, with Chrome outer geometry and no children."""
    monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 0)
    probe = FakeProbe(tmp_path)
    probe.identity = replace(probe.identity, framework="Chrome")
    source = object.__new__(mss.MSS)
    before = probe.frame(source, "before")
    return BrowserSequence(cast("Probe", probe), source, probe.identity.rect, before, 0)


def test_fixed_offsets_and_no_stitched_admission(sequence: BrowserSequence) -> None:
    """All evidence remains full width; only diagnostic completion is emitted."""
    sequence.run()
    probe = cast("FakeProbe", sequence.probe)
    plan = next(e for e in probe.events if e["event"] == "browser_sequence_plan")
    assert plan["offsets"] == pytest.approx((16, 32, 64, 93))
    observed = [e for e in probe.events if e["event"] == "browser_sequence_observation"]
    assert len(observed) == 8
    assert all(e["width"] == 4 and e["height"] == 64 for e in observed)
    assert sequence.frames == 9
    assert probe.requests[-3:] == [100, 0, 40]
    assert probe.last == probe.initial
    assert probe.report["browser_sequence_completed"] is True
    assert probe.report["restored"] is True
    assert "full_sweep_passed" not in probe.report
    assert not (probe.output / "stitched.png").exists()


@pytest.mark.parametrize("name", ["bottom-repeat", "top-repeat", "restore"])
def test_exact_outer_controls(sequence: BrowserSequence, name: str) -> None:
    """Even a changed scrollbar pixel rejects diagnostic completion."""
    probe = cast("FakeProbe", sequence.probe)
    probe.corrupt = name
    with pytest.raises(RuntimeError, match="changed"):
        sequence.run()
    assert not probe.report.get("browser_sequence_completed")


@pytest.mark.parametrize("limit", ["frames", "time"])
def test_budgets_precede_write(
    sequence: BrowserSequence, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Inherited hard budgets protect the collector and restoration."""
    if limit == "frames":
        sequence.frames = 128
    else:
        monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 90)
    with pytest.raises(RuntimeError, match=r"limit|deadline"):
        sequence.run()
    probe = cast("FakeProbe", sequence.probe)
    assert not probe.requests
    assert probe.report["restoration_prohibited"] is True


@pytest.mark.parametrize("change", ["framework", "rect", "offscreen", "state"])
def test_outer_validation_precedes_write(
    sequence: BrowserSequence, change: str
) -> None:
    """No fixture-content geometry can rescue a changed native outer target."""
    probe = cast("FakeProbe", sequence.probe)
    if change == "framework":
        probe.identity = replace(probe.identity, framework="WPF")
    elif change == "rect":
        probe.identity = replace(probe.identity, rect=(0, 0, 4, 64))
    elif change == "offscreen":
        probe.identity = replace(probe.identity, offscreen=True)
    else:
        probe.last = replace(probe.last, vertical_view=50)
    with pytest.raises(RuntimeError, match="changed"):
        sequence.run()
    assert not probe.requests
    assert probe.report["restoration_prohibited"] is True


def test_read_uncertainty_latches(
    sequence: BrowserSequence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed guarded native read forbids fallback writes."""
    probe = cast("FakeProbe", sequence.probe)

    def fail() -> None:
        raise RuntimeError("Provider read uncertain")

    monkeypatch.setattr(probe, "validate", fail)
    with pytest.raises(RuntimeError, match="read uncertain"):
        sequence.run()
    assert probe.report["restoration_prohibited"] is True
    assert not probe.requests


def test_no_child_geometry_or_stitcher(
    sequence: BrowserSequence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector must not use a fixture crop, child scrollbar or stitcher."""
    probe = cast("FakeProbe", sequence.probe)

    def forbidden() -> None:
        pytest.fail("Unexpected crop/stitch operation")

    monkeypatch.setattr(probe, "inspect_children", forbidden)
    monkeypatch.setattr(sequence, "_make_stitcher", forbidden)
    monkeypatch.setattr(sequence, "content", forbidden)
    sequence.run()
    assert probe.report["browser_sequence_completed"] is True


def test_stalled_progress(
    sequence: BrowserSequence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that stops early never establishes diagnostic completion."""
    probe = cast("FakeProbe", sequence.probe)
    original = probe.seek

    def seek(percent: float) -> None:
        original(percent)
        probe.stalled = True

    monkeypatch.setattr(probe, "seek", seek)
    with pytest.raises(RuntimeError, match="no provider progress"):
        sequence.run()
    assert not probe.report.get("browser_sequence_completed")


@pytest.mark.parametrize("extent", [float("nan"), float("inf"), 0, -1, 10000])
def test_invalid_or_excessive_plan(extent: float) -> None:
    """Preflight bounds prevent an unbounded offset list or unsafe write."""
    with pytest.raises(RuntimeError, match=r"extent|limit"):
        sequence_offsets(64, extent)


def test_exact_budget_and_short_document() -> None:
    """Count actual captures plus a fallback reserve, including clipped plans."""
    short = sequence_offsets(64, 1)
    assert short == (1,)
    fits = sequence_offsets(64, 3872)
    assert len(fits) + 6 == 128
    with pytest.raises(RuntimeError, match="frame limit"):
        sequence_offsets(64, 3904)


def test_deadline_after_slow_read(
    sequence: BrowserSequence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read returning after deadline cannot lead to a scroll or completion."""
    probe = cast("FakeProbe", sequence.probe)
    original = probe.validate

    def slow_read() -> ScrollState:
        state = original()
        monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 90)
        return state

    monkeypatch.setattr(probe, "validate", slow_read)
    with pytest.raises(RuntimeError, match="deadline"):
        sequence.run()
    assert not probe.requests
    assert probe.report["restoration_prohibited"] is True


def test_cleanup_uncertainty_never_completes(sequence: BrowserSequence) -> None:
    """Even completed replay comparisons cannot override unresolved write state."""
    probe = cast("FakeProbe", sequence.probe)
    probe.uncertain = True
    with pytest.raises(RuntimeError, match="cleanup-ready"):
        sequence.run()
    assert not probe.report.get("browser_sequence_completed")
