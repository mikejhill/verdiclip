"""Fixed diagnostic schedules retain failures without changing admission rules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast, override

import mss
import pytest
from scripts.panel_clipping_diagnostic import (
    ClippingDiagnostic,
    compare_overlap,
    requests,
    validate_fixture,
)
from scripts.panel_uia import Structure
from scripts.panel_uia_probe import Probe
from tests.tooling.test_panel_full_sweep import FakeProbe

from verdiclip.capture.scroll_stitch import ScrollFrame


class DiagnosticProbe(FakeProbe):
    """Provide the known native-sized panel without native APIs or fixture crops."""

    def __init__(self, output: Path) -> None:
        super().__init__(output)
        self.identity = replace(self.identity, rect=(10, 20, 14, 273))
        self.initial = self.last = replace(
            self.initial, vertical=11900 / 1204, vertical_view=25300 / 1457
        )
        self.names: list[str] = []

    @override
    def inspect_children(self) -> list[Structure]:
        return [replace(super().inspect_children()[0], rect=(13, 20, 14, 273))]

    @override
    def frame(self, _source: mss.MSS, name: str) -> ScrollFrame:
        """Retain unique document rows and a deliberate boundary discrepancy."""
        if self.full_sweep is not None:
            self.full_sweep.begin_frame()
        self.names.append(name)
        offset = round(self.last.vertical * 1204 / 100)
        rgb = b"".join(
            bytes((y % 256, y // 256, 123)) * 4 for y in range(offset, offset + 253)
        )
        if name == "offset-504-direct":
            rgb = rgb[:-12] + b"bad" + rgb[-9:]
        return ScrollFrame(4, 253, rgb)


def test_fixed_requests_reproduce_numeric_controls() -> None:
    """Absolute630 removes accumulated error;504's neighbors straddle its integer."""
    plan = requests(1204.0)
    assert [name for name, _, _ in plan] == [
        "503",
        "504-lower",
        "504-direct",
        "504-upper",
        "505",
        "630-direct",
        "630-accumulated",
    ]
    predicted = {name: 1204 * percent * 0.01 for name, _, percent in plan}
    assert predicted["503"] == 503
    assert predicted["505"] == 505
    assert (
        predicted["504-lower"] < 504 < predicted["504-direct"] < predicted["504-upper"]
    )
    assert predicted["630-direct"] == 630
    assert predicted["630-accumulated"] > 630


def test_diagnostic_retains_every_observation_and_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mismatch is evidence, not an adaptive retry or a capture success."""
    monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 0)
    probe = DiagnosticProbe(tmp_path)
    source = object.__new__(mss.MSS)
    before = probe.frame(source, "before")
    diagnostic = ClippingDiagnostic(
        cast("Probe", probe), source, (10, 20, 13, 273), before, 0
    )
    diagnostic.run()
    assert probe.report["clipping_diagnostic_completed"] is True
    assert probe.report["restored"] is True
    assert not probe.report.get("full_sweep_passed")
    assert not (tmp_path / "stitched.png").exists()
    observations = probe.report["clipping_observations"]
    assert isinstance(observations, list)
    assert len(observations) == 7
    comparisons = probe.report["clipping_comparisons"]
    assert isinstance(comparisons, list)
    direct = next(row for row in comparisons if row["name"] == "504-direct")
    assert direct["changed_rows"]
    assert diagnostic.frames == 16
    assert probe.requests[::2][:-1] == [0] * 7
    assert probe.requests[-1] == probe.initial.vertical
    assert all(f"offset-{name}" in probe.names for name, _, _ in requests(1204))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "browser"),
        ("mode", "bordered"),
        ("dpi_x", 120),
        ("dpi_y", 120),
        ("dpi_y", None),
    ],
)
def test_only_owned_nested_96_dpi_fixture(field: str, value: object) -> None:
    """Fixed offsets are diagnostic fixture controls, not a product policy."""
    fixture = {"kind": "owned-wpf", "mode": "nested", "dpi_x": 96, "dpi_y": 96}
    fixture[field] = value
    with pytest.raises(RuntimeError, match="owned nested WPF"):
        validate_fixture(fixture)


def test_compare_reports_all_changed_rows_without_relaxation() -> None:
    """Full predicted overlap is measured independently of stitch acceptance."""
    rgb = b"".join(bytes((y, y, y)) for y in range(100))
    old = ScrollFrame(1, 64, rgb[:192])
    new_rgb = bytearray(rgb[60:252])
    new_rgb[30] += 1
    report = compare_overlap(old, ScrollFrame(1, 64, bytes(new_rgb)), 20)
    assert report["changed_rows"] == [
        {
            "old_y": 30,
            "new_y": 10,
            "changed_pixels": 1,
            "changed_channels": 1,
            "max_channel_delta": 1,
            "changed_columns": [0],
        }
    ]
    gate = report["unchanged_stitcher_gate"]
    assert isinstance(gate, dict)
    assert gate["accepted"] is False


def test_diagnostic_restoration_mismatch_cannot_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collecting diagnostic evidence does not excuse changed restoration pixels."""
    monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 0)
    probe = DiagnosticProbe(tmp_path)
    source = object.__new__(mss.MSS)
    before = probe.frame(source, "before")
    original_frame = probe.frame

    def changed_frame(source: mss.MSS, name: str) -> ScrollFrame:
        frame = original_frame(source, name)
        return (
            replace(frame, rgb=b"bad" + frame.rgb[3:]) if name == "restore" else frame
        )

    monkeypatch.setattr(probe, "frame", changed_frame)
    diagnostic = ClippingDiagnostic(
        cast("Probe", probe), source, (10, 20, 13, 273), before, 0
    )
    with pytest.raises(RuntimeError, match="restoration changed"):
        diagnostic.run()
    assert not probe.report.get("clipping_diagnostic_completed")
    assert not (tmp_path / "stitched.png").exists()
