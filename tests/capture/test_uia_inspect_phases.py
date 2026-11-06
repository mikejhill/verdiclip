"""Managed inspect diagnostics expose fixed phases, never provider content."""

import os
import re
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process
from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace


def test_native_inspect_markers_are_fixed_and_allowlisted() -> None:
    """Native resolution retains fixed phase evidence without provider text."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    native = Path(uia_process.__file__).with_name("uia_discovery.cs").read_text()
    stages = re.findall(r"VERDICLIP_UIA_PHASE=(inspect_[a-z_]+)", helper)
    stages += re.findall(r'Mark\("(inspect_[a-z_]+)"\)', native)
    expected = {
        stage for stage in WorkerTrace.MARKERS.values() if stage.startswith("inspect_")
    }
    assert set(stages) == expected
    assert "if(!diagnostics) return;" in native
    assert "($request.action -eq 'inspect')" in helper
    branch = helper.split("    if ($NativeOnly) {", 1)[1].split(
        "    if ($request.action -in", 1
    )[0]
    assert "continue" in branch
    assert "VERDICLIP_UIA_PHASE=inspect_" not in branch


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
