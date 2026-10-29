"""Pure probe ordering, admission, protocol and timeout tests; no Qt imports."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts import qt_cold_probe, run_qt_cold_probes
from scripts.latency_trace import LatencyTrace


def report_for(mode: str) -> dict[str, object]:
    """A synthetic report describes one isolated child, without executing it."""
    return {
        "metadata": {"mode": mode, "process_isolated": True},
        "phases": [
            {
                "name": name,
                "completed": True,
                "wall_ms": 2000.0,
                "process_cpu_ms": 300.0,
                "thread_cpu_ms": 290.0,
            }
            for name in ("application-construction", mode)
        ],
    }


def test_one_operation_precedes_inventory_and_retains_result() -> None:
    """Post-measurement inventory must not warm the measured font path."""
    calls: list[str] = []
    value = object()

    def operation() -> object:
        calls.append("operation")
        return value

    def metadata() -> dict[str, object]:
        calls.append("inventory")
        return {"observed": True}

    def other() -> object:
        pytest.fail("A different constituent must run in a different process")

    trace = LatencyTrace()
    observed = qt_cold_probe.measure_operation(
        "selected", {"selected": operation, "other": other}, metadata, trace
    )
    assert observed is value
    assert calls == ["operation", "inventory"]
    assert trace.phases[0]["completed"] is True
    assert trace.metadata == {"observed": True}


def test_font_inventory_matches_only_direct_supported_files(tmp_path: Path) -> None:
    """Directory metadata follows Qt FreeType's direct scan, not recursive TTC."""
    (tmp_path / "font.TTF").write_bytes(b"font")
    (tmp_path / "font.ttc").write_bytes(b"collection")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "font.otf").write_bytes(b"nested")
    result = qt_cold_probe.font_inventory(str(tmp_path))
    assert result["matching_direct_font_files"] == 1
    assert result["matching_direct_font_bytes"] == 4


@pytest.mark.parametrize("module", [qt_cold_probe, run_qt_cold_probes])
def test_cli_rejects_local_before_any_qt_or_child(
    module: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordinary local invocation cannot accidentally execute this native probe."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("RUNNER_OS", "Windows")
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> str:
        calls.append("native")
        pytest.fail("No Qt operation or child is admitted locally")

    if module is qt_cold_probe:
        monkeypatch.setattr(qt_cold_probe, "_run_qt", forbidden)
        command = qt_cold_probe.main
        arguments = ["spinbox"]
    else:
        monkeypatch.setattr(run_qt_cold_probes, "run_all", forbidden)
        command = run_qt_cold_probes.main
        arguments = ["--output", str(tmp_path)]
    with pytest.raises(SystemExit, match="restricted to hosted Windows"):
        command(arguments)
    assert calls == []


def test_invalid_cli_mode_rejected_without_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Argument parsing rejects unknown modes before native admission."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit) as caught:
        qt_cold_probe.main(["unknown"])
    assert caught.value.code == 2


def test_protocol_preserves_large_latency_without_invented_budget() -> None:
    """Diagnostic success means complete measurement, never fast acceptance."""
    report = report_for("spinbox")
    actual = run_qt_cold_probes.validate_report(json.dumps(report), "spinbox")
    assert actual == report


@pytest.mark.parametrize("field", ["qt", "qpa"])
def test_protocol_rejects_wrong_actual_profile(field: str) -> None:
    """A child must actually match the pinned parent's Qt and QPA profile."""
    report = report_for("spinbox")
    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    metadata.update(qt="6.12.0", qpa="offscreen")
    metadata[field] = "unexpected"
    with pytest.raises(ValueError, match="Wrong measured"):
        run_qt_cold_probes.validate_report(
            json.dumps(report), "spinbox", "6.12.0", "offscreen"
        )


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"metadata": {}, "phases": []}',
        json.dumps(report_for("lineedit")),
        json.dumps({**report_for("spinbox"), "phases": []}),
        json.dumps({**report_for("spinbox"), "metadata": {"mode": "spinbox"}}),
    ],
)
def test_protocol_rejects_incomplete_or_wrong_identity(raw: str) -> None:
    """Zero process exit cannot qualify malformed or foreign evidence."""
    with pytest.raises(
        (ValueError, TypeError),
        match=r"Expecting value|Probe report|Wrong probe|Missing",
    ):
        run_qt_cold_probes.validate_report(raw, "spinbox")


@pytest.mark.parametrize("clock", [True, -1.0, float("nan"), float("inf"), "1"])
def test_protocol_rejects_invalid_clock(clock: object) -> None:
    """Missing, nonnumeric and nonfinite clocks cannot appear complete."""
    report = report_for("spinbox")
    phases = report["phases"]
    assert isinstance(phases, list)
    phases[0]["wall_ms"] = clock
    with pytest.raises(ValueError, match="Invalid phase clock"):
        run_qt_cold_probes.validate_report(json.dumps(report), "spinbox")


def test_supervisor_complete_children_remain_diagnostic(tmp_path: Path) -> None:
    """All complete reports pass regardless of their large recorded latency."""

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, json.dumps(report_for(args[-1])), ""
        )

    complete = run_qt_cold_probes.run_all(tmp_path, runner)
    assert complete is True
    for mode in qt_cold_probe.MODES:
        receipt = json.loads((tmp_path / f"{mode}.json").read_text())
        assert receipt["complete"] is True
        assert receipt["report"] == report_for(mode)


def test_workflow_runs_probes_after_cold_gate_with_outer_deadline() -> None:
    """Constituent inspection cannot warm or replace the acceptance process."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    cold = workflow.index("- name: First capture before any other editor test")
    probe = workflow.index("- name: Independent fresh-process constituent diagnostics")
    artifact = workflow.index("- name: Retain focused qualification")
    assert cold < probe < artifact
    assert "timeout-minutes: 3" in workflow[probe:artifact]
    assert "continue-on-error" not in workflow[cold:artifact]
    assert "qt-cold-probes/" in workflow[artifact:]


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "malformed"])
def test_supervisor_preserves_failure_and_runs_each_fresh_child_once(
    failure: str, tmp_path: Path
) -> None:
    """A failed child produces an incomplete receipt without retrying it."""
    calls: list[str] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        mode = args[-1]
        calls.append(mode)
        assert args[1:3] == ["-m", "scripts.qt_cold_probe"]
        assert kwargs["timeout"] == 30
        assert kwargs["check"] is False
        if mode == "spinbox":
            if failure == "timeout":
                raise subprocess.TimeoutExpired(
                    args, 30, output=b"partial evidence", stderr=b"partial error"
                )
            if failure == "nonzero":
                return subprocess.CompletedProcess(args, 7, "", "owned child failed")
            return subprocess.CompletedProcess(args, 0, "bad json", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(report_for(mode)), "")

    complete = run_qt_cold_probes.run_all(tmp_path, runner)
    receipts = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("*.json"))]
    assert complete is False
    assert calls == list(qt_cold_probe.MODES)
    assert len(receipts) == 4
    failed = json.loads((tmp_path / "spinbox.json").read_text())
    assert failed["complete"] is False
    expected = {
        "timeout": "timeout",
        "nonzero": "execution-failure",
        "malformed": "incomplete",
    }
    assert failed["status"] == expected[failure]
    if failure == "timeout":
        assert failed["stdout"] == "partial evidence"
        assert failed["stderr"] == "partial error"
    if failure == "malformed":
        assert failed["stdout"] == "bad json"
