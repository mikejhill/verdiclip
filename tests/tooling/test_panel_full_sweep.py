"""Full-sweep qualification uses pure fake providers, never native guards."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import mss
import mss.tools
import pytest
from scripts.panel_full_sweep import FullSweep
from scripts.panel_uia import ScrollState, Structure
from scripts.panel_uia_probe import Probe

from verdiclip.capture.scroll_stitch import ScrollFrame

HEIGHT = 64
EXTENT = 157
VIEWPORT = (10, 20, 13, 84)


def rows(start: int, end: int) -> bytes:
    """Unique exact RGB scanlines for independently known complete content."""
    return b"".join(
        bytes((row % 256, row // 256, 123)) * 3 for row in range(start, end)
    )


class FakeProbe:
    """Simulate percent readback and physical viewport without desktop access."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.identity = Structure(1, 1, 50030, "WPF", (10, 20, 14, 84), offscreen=False)
        self.initial = ScrollState(
            0,
            40,
            100,
            HEIGHT * 100 / EXTENT,
            horizontal_scrollable=False,
            vertical_scrollable=True,
        )
        self.last = self.initial
        self.uia = self
        self.requests: list[float] = []
        self.events: list[dict[str, object]] = []
        self.report: dict[str, object] = {}
        self.corrupt = ""
        self.stalled = False
        self.offset_error = 0
        self.uncertain = False
        self.full_sweep: FullSweep | None = None

    def inspect_children(self) -> list[Structure]:
        """Return one native edge scrollbar."""
        return [Structure(1, 1, 50014, "WPF", (13, 20, 14, 84), offscreen=False)]

    def validate(self) -> ScrollState:
        """Retain acknowledged state."""
        return self.last

    def emit(self, event: dict[str, object]) -> None:
        """Collect experiment receipts."""
        self.events.append(event)

    def seek(self, percent: float) -> None:
        """Acknowledge requested percent unless stalled."""
        if self.full_sweep is not None:
            self.full_sweep.before_write()
        self.requests.append(percent)
        if not self.stalled:
            self.last = replace(self.last, vertical=percent)

    def frame(self, _source: mss.MSS, name: str) -> ScrollFrame:
        """Build exact full-UIA pixels including the scrollbar."""
        if self.full_sweep is not None:
            self.full_sweep.begin_frame()
        offset = round(self.last.vertical * (EXTENT - HEIGHT) / 100)
        offset += self.offset_error if self.last.vertical > 0 else 0
        pixels = rows(offset, offset + HEIGHT)
        # A scrollbar is outside the stitched viewport but inside replay evidence.
        bar = bytes((round(self.last.vertical), 0, 0))
        rgb = b"".join(pixels[i : i + 9] + bar for i in range(0, len(pixels), 9))
        if name == self.corrupt:
            rgb = rgb[:9] + b"bad" + rgb[12:]
        return ScrollFrame(4, HEIGHT, rgb)


@pytest.fixture
def experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FullSweep:
    """A static clock and inert MSS value prove no native work is attempted."""
    monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 0)
    fake = FakeProbe(tmp_path)
    source = object.__new__(mss.MSS)
    return FullSweep(
        cast("Probe", fake), source, VIEWPORT, fake.frame(source, "before"), 0
    )


def test_golden_complete_rows_and_short_final_step(experiment: FullSweep) -> None:
    """Every row appears once, with a clipped final step and exact restoration."""
    experiment.run()
    probe = cast("FakeProbe", experiment.probe)
    assert probe.report["full_sweep_passed"] is True
    assert probe.report["restored"] is True
    assert probe.last == probe.initial
    assert [e["measured_rows"] for e in probe.events if e["event"] == "sweep_step"] == [
        32,
        32,
        29,
    ]
    assert (probe.output / "stitched.png").read_bytes() == mss.tools.to_png(
        rows(0, EXTENT), (3, EXTENT)
    )
    assert probe.requests[-3:] == [100, 0, 40]
    assert experiment.frames == 8


@pytest.mark.parametrize("name", ["bottom-repeat", "top-repeat", "restore"])
def test_full_rectangle_replays_required(experiment: FullSweep, name: str) -> None:
    """Even changed pixels outside content prohibit terminal success/output."""
    probe = cast("FakeProbe", experiment.probe)
    probe.corrupt = name
    with pytest.raises(RuntimeError, match="changed"):
        experiment.run()
    assert not (probe.output / "stitched.png").exists()
    assert not probe.report.get("full_sweep_passed")


@pytest.mark.parametrize("limit", ["frames", "time"])
def test_limits_precede_scroll(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Exhausted safety budgets cannot cause another scroll."""
    if limit == "frames":
        monkeypatch.setattr("scripts.panel_full_sweep.MAX_FRAMES", 1)
    else:
        monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 90)
    with pytest.raises(RuntimeError, match=r"limit|deadline"):
        experiment.run()
    probe = cast("FakeProbe", experiment.probe)
    assert probe.requests == []
    assert not (probe.output / "stitched.png").exists()


def test_no_progress_has_no_output(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stalled provider readback never establishes bottom."""
    probe = cast("FakeProbe", experiment.probe)
    original_seek = probe.seek

    def seek(percent: float) -> None:
        original_seek(percent)
        probe.stalled = True

    monkeypatch.setattr(probe, "seek", seek)
    with pytest.raises(RuntimeError, match="no provider progress"):
        experiment.run()
    assert not (probe.output / "stitched.png").exists()


def test_displacement_mismatch_has_no_output(experiment: FullSweep) -> None:
    """Unique overlap alone cannot admit provider/pixel disagreement."""
    probe = cast("FakeProbe", experiment.probe)
    probe.offset_error = 3
    with pytest.raises(RuntimeError, match="displacement disagrees"):
        experiment.run()
    assert not (probe.output / "stitched.png").exists()


def test_other_framework_rejected_before_scroll(experiment: FullSweep) -> None:
    """WPF-only experimental evidence does not pretend to qualify browsers."""
    probe = cast("FakeProbe", experiment.probe)
    probe.identity = replace(probe.identity, framework="Chrome")
    with pytest.raises(RuntimeError, match="qualified WPF"):
        experiment.run()
    assert probe.requests == []


@pytest.mark.parametrize("reason", ["uncertain", "deadline", "guard"])
def test_failed_sweep_cannot_restore_unsafely(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """Reuse the actual probe's failure path without ever calling a native guard."""
    probe = cast("FakeProbe", experiment.probe)
    probe.last = replace(probe.initial, vertical=90)
    probe.uncertain = reason == "uncertain"
    probe.report["full_sweep_deadline_exceeded"] = reason == "deadline"

    def invalid() -> ScrollState:
        raise RuntimeError("Guard lost ownership")

    if reason == "guard":
        monkeypatch.setattr(probe, "validate", invalid)
    Probe.restore_after_failure(cast("Probe", probe))
    assert probe.requests == []
    if reason == "guard":
        assert "Guard lost ownership" in str(probe.report["restore_failure"])


def test_first_failure_survives_restore_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing terminal boundary preserves sweep failure over restoration."""
    probe = Probe(argparse.Namespace(output=str(tmp_path / "probe")))

    def fail_execute(self: Probe) -> None:
        self.initial = ScrollState(
            0, 40, 100, 50, horizontal_scrollable=False, vertical_scrollable=True
        )
        self.last = replace(self.initial, vertical=100)
        raise RuntimeError("First sweep failure")

    monkeypatch.setattr(Probe, "execute", fail_execute)
    assert probe.run() == 1
    assert probe.report["first_failure"] == "RuntimeError('First sweep failure')"
    assert "not initialized" in str(probe.report["restore_failure"])
    assert not (probe.output / "stitched.png").exists()


def test_changed_native_viewport_precedes_scroll(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moved scrollbar invalidates previously derived geometry."""
    probe = cast("FakeProbe", experiment.probe)
    monkeypatch.setattr(probe, "inspect_children", list)
    with pytest.raises(RuntimeError, match="Native viewport changed"):
        experiment.run()
    assert probe.requests == []


@pytest.mark.parametrize("blocked", ["viewport", "budget"])
def test_failure_latch_blocks_fallback_scroll(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    """Changed native geometry and exhausted budget survive the failure boundary."""
    probe = cast("FakeProbe", experiment.probe)
    probe.last = replace(probe.initial, vertical=90)
    if blocked == "viewport":
        monkeypatch.setattr(probe, "inspect_children", list)
    else:
        experiment.frames = 128
    with pytest.raises(RuntimeError):
        experiment.capture(100, "blocked")
    Probe.restore_after_failure(cast("Probe", probe))
    assert probe.requests == []
    assert probe.report["restoration_prohibited"] is True


@pytest.mark.parametrize("slow_grab", [False, True])
def test_real_frame_timeout_prohibits_fallback_scroll(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch, *, slow_grab: bool
) -> None:
    """Both settling expiry branches latch before the real fallback path."""
    probe = cast("FakeProbe", experiment.probe)
    probe.last = replace(probe.initial, vertical=90)
    now = [0.0]
    samples = [0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    def grab(_area: object) -> SimpleNamespace:
        samples[0] += 1
        if slow_grab:
            now[0] += 3
        return SimpleNamespace(
            rgb=bytes([samples[0]]) * 4 * HEIGHT * 3, size=(4, HEIGHT)
        )

    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: now[0])
    monkeypatch.setattr("scripts.panel_uia_probe.time.sleep", sleep)
    source = cast("mss.MSS", SimpleNamespace(grab=grab))
    with pytest.raises(RuntimeError, match=r"deadline|settle"):
        Probe.frame(cast("Probe", probe), source, "timeout")
    Probe.restore_after_failure(cast("Probe", probe))
    assert probe.requests == []
    assert probe.report["restoration_prohibited"] is True


def test_actual_seek_rechecks_deadline_after_slow_validation(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No native write escapes a deadline consumed during provider validation."""
    probe = cast("FakeProbe", experiment.probe)
    probe.last = replace(probe.initial, vertical=90)
    now = [0.0]
    calls = [0]

    def validate() -> ScrollState:
        calls[0] += 1
        if calls[0] == 3:
            now[0] = 90
        return probe.last

    def forbidden_write(*_args: object, **_kwargs: object) -> None:
        pytest.fail("An expired deadline must never issue a native write")

    monkeypatch.setattr(probe, "validate", validate)
    monkeypatch.setattr(
        probe, "guard", SimpleNamespace(check=lambda: None), raising=False
    )
    monkeypatch.setattr(probe, "set_vertical", forbidden_write, raising=False)
    monkeypatch.setattr("scripts.panel_uia_probe.time.monotonic", lambda: now[0])
    with pytest.raises(RuntimeError, match="deadline"):
        Probe.seek(cast("Probe", probe), 100)
    Probe.restore_after_failure(cast("Probe", probe))
    assert calls[0] == 3
    assert probe.requests == []


def test_known_failure_can_restore_with_shared_frame_budget(
    experiment: FullSweep, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known stitching failure still permits guarded restoration within limits."""
    probe = cast("FakeProbe", experiment.probe)
    probe.last = replace(probe.initial, vertical=90)
    experiment.frames = 127
    monkeypatch.setattr(
        "scripts.panel_uia_probe.mss.MSS", lambda: nullcontext(experiment.source)
    )
    Probe.restore_after_failure(cast("Probe", probe))
    assert probe.requests == [40]
    assert probe.report["restored"] is True
    assert experiment.frames == 128
    probe.last = replace(probe.initial, vertical=90)
    Probe.restore_after_failure(cast("Probe", probe))
    assert probe.requests == [40]
    assert probe.report["restoration_prohibited"] is True


@pytest.mark.parametrize("view", [1e-320, 0.001, float("nan"), float("inf")])
def test_invalid_derived_extent_rejected_before_initial_seek(
    experiment: FullSweep, view: float
) -> None:
    """Unbounded/nonfinite extents never begin a full sweep."""
    probe = cast("FakeProbe", experiment.probe)
    probe.initial = probe.last = replace(probe.initial, vertical_view=view)
    with pytest.raises(RuntimeError, match=r"limit|Invalid"):
        experiment.run()
    assert probe.requests == []
