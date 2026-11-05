"""Private timing sidecars cannot leak into public receipts or overwrite files."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.gui_readiness_profile import Trace, write_private_phases


def test_private_sidecar_is_exclusive_and_contains_original_nanoseconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timing anchors remain private, with no overwrite of an earlier receipt."""
    path = tmp_path / ("VerdiClip-" + "a" * 32 + ".phases.json")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("VERDICLIP_GUI_PHASE_FILE", str(path))
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", "b" * 40)
    monkeypatch.setattr(
        "scripts.gui_readiness_profile.time.get_clock_info",
        lambda _: SimpleNamespace(
            implementation="QueryPerformanceCounter()", monotonic=True
        ),
    )
    trace = Trace(private_intervals=[("editor_show", 1, 100, 200)])
    write_private_phases(trace)
    packet = json.loads(path.read_text())
    assert packet["intervals"] == [["editor_show", 1, 100, 200]]
    assert trace.records == []
    with pytest.raises(FileExistsError):
        write_private_phases(trace)


def test_private_sidecar_rejects_other_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A substituted destination cannot write outside the runner temp root."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv(
        "VERDICLIP_GUI_PHASE_FILE",
        str(tmp_path / "nested" / ("VerdiClip-" + "a" * 32 + ".phases.json")),
    )
    with pytest.raises(ValueError, match="destination invalid"):
        write_private_phases(Trace())


def test_private_sidecar_rejects_unmatched_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different Python clock must not be treated as raw Windows QPC."""
    path = tmp_path / ("VerdiClip-" + "a" * 32 + ".phases.json")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("VERDICLIP_GUI_PHASE_FILE", str(path))
    monkeypatch.setattr(
        "scripts.gui_readiness_profile.time.get_clock_info",
        lambda _: SimpleNamespace(implementation="other", monotonic=True),
    )
    with pytest.raises(ValueError, match="not Windows QPC"):
        write_private_phases(Trace())
    assert not path.exists()
