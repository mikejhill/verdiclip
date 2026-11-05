"""Managed inspect diagnostics expose fixed phases, never provider content."""

import os
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process
from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace


def test_inspect_markers_guard_only_existing_managed_calls() -> None:
    """Markers add no provider query and cannot enter native or warm sample paths."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    expected = {
        "inspect_desktop": (
            "$dpiContext = [CaptureDesktop]::SetThreadDpiAwarenessContext"
        ),
        "inspect_root": ("$rootStarted = $null"),
        "inspect_process": "$process = Get-Process -Id $root.Current.ProcessId",
        "inspect_root_runtime": "$rootRuntime = ($root.GetRuntimeId()",
        "inspect_bounds": "$bounds = $element.Current.BoundingRectangle",
        "inspect_pattern": "if ($element.TryGetCurrentPattern",
        "inspect_scrollable": "if ($pattern.Current.VerticallyScrollable)",
        "inspect_hit": (
            "$element = [System.Windows.Automation.AutomationElement]::FromPoint"
        ),
        "inspect_ancestor_identity": "if ($element.Current.ProcessId",
        "inspect_parent": "$element = $walker.GetParent($element)",
        "inspect_node_identity": "$runtime = ($element.GetRuntimeId()",
        "inspect_control": "$kind = 'other'",
        "inspect_reference": "$reference = if ($null -ne $pattern)",
        "inspect_complete": "$result = @{ nodes=$nodes;",
    }
    marker_lines = [
        line.strip()
        for line in helper.splitlines()
        if "VERDICLIP_UIA_PHASE=inspect_" in line
    ]
    assert len(marker_lines) == len(expected)
    assert {
        stage for stage in WorkerTrace.MARKERS.values() if stage.startswith("inspect_")
    } == set(expected)
    for stage, operation in expected.items():
        marker = (
            "if ($request.action -eq 'inspect') { [Console]::Error.WriteLine("
            f"'VERDICLIP_UIA_PHASE={stage}') }}"
        )
        assert marker in marker_lines
        following = helper.split(marker, 1)[1].splitlines()
        next_statement = next(
            line.strip()
            for line in following
            if line.strip() and not line.strip().startswith("#")
        )
        assert next_statement.startswith(operation)
    native_branch = helper.split("    if ($NativeOnly) {", 1)[1].split(
        "    if ($request.action -in", 1
    )[0]
    assert "continue" in native_branch
    assert "VERDICLIP_UIA_PHASE=inspect_" not in native_branch


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
@pytest.mark.parametrize("action", ["inspect", "observe", "sample", "step", "restore"])
def test_actual_marker_guards_emit_only_for_inspect(
    tmp_path: Path, action: str
) -> None:
    """Run actual marker statements alone, with no native API or provider query."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    statements = [
        line.strip()
        for line in helper.splitlines()
        if "VERDICLIP_UIA_PHASE=inspect_" in line
    ]
    script = tmp_path / "inspect-marker-probe.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + "$request = @{ action = '"
        + action
        + "' }\n"
        + "\n".join(statements),
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [str(host), "-NoProfile", "-NonInteractive", "-Mta", "-File", str(script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    actual = result.stderr.splitlines()
    if action == "inspect":
        expected = [
            line.split("WriteLine('", 1)[1].split("')", 1)[0] for line in statements
        ]
        assert actual == expected
    else:
        assert actual == []
