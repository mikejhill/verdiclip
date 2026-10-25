"""Execute only the helper's pure ownership predicate, never its native body."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from verdiclip.capture.scroll import uia_process

POLICY_SCRIPT = r"""
param([string]$Helper)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $Helper, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'Helper syntax failed' }
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Test-OwnedScrollMetrics'
}, $true)
if ($null -eq $function) { throw 'Ownership predicate missing' }
. ([scriptblock]::Create($function.Extent.Text))
$reference = [pscustomobject]@{ vertical_percent=20; view_size=50 }
if (-not (Test-OwnedScrollMetrics 20 50 $reference)) {
    throw 'Unchanged ownership refused'
}
if (Test-OwnedScrollMetrics 21 50 $reference) { throw 'Position drift accepted' }
if (Test-OwnedScrollMetrics 20 49 $reference) { throw 'Extent drift accepted' }
if (Test-OwnedScrollMetrics ([double]::NaN) 50 $reference) { throw 'NaN accepted' }
if (Test-OwnedScrollMetrics 20 ([double]::PositiveInfinity) $reference) {
    throw 'Infinity accepted'
}
[Console]::WriteLine('owned-metrics-policy-passed')
"""


@pytest.mark.skipif(
    sys.platform != "win32", reason="Uses installed Windows PowerShell parser"
)
def test_native_movement_guard_refuses_extent_and_position_drift(
    tmp_path: Path,
) -> None:
    """Provider extent changes cannot pass the predicate used before native movement."""
    script = tmp_path / "check-metrics.ps1"
    script.write_text(POLICY_SCRIPT, encoding="utf-8")
    host = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1")
    result = subprocess.run(  # noqa: S603 - fixed local pure-policy test, no native calls
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Helper",
            str(helper),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=0x08000000,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "owned-metrics-policy-passed" in result.stdout
