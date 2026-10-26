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

GEOMETRY_SCRIPT = (
    POLICY_SCRIPT[: POLICY_SCRIPT.index("$reference =")].replace(
        "Test-OwnedScrollMetrics", "Get-Bounds"
    )
    + r"""
$rectangle = [pscustomobject]@{
    IsEmpty=$false; Left=-100.25; Top=20.25; Right=200.75; Bottom=120.75
    Width=301; Height=100.5
}
$element = [pscustomobject]@{
    Current=[pscustomobject]@{ BoundingRectangle=$rectangle }
}
$bounds = @(Get-Bounds $element)
if ($bounds.Count -ne 4 -or ($bounds -join ',') -ne '-100,21,300,99') {
    throw ('Incorrect integral physical bounds: ' + ($bounds -join ','))
}
[Console]::WriteLine('owned-metrics-policy-passed')
"""
)

BOUNDS_SCRIPT = (
    POLICY_SCRIPT[: POLICY_SCRIPT.index("$reference =")].replace(
        "Test-OwnedScrollMetrics", "Test-SamePhysicalBounds"
    )
    + r"""
$expected = '[88.0,111.0,404.0,281.0]' | ConvertFrom-Json
if (-not (Test-SamePhysicalBounds @(88,111,404,281) $expected)) {
    throw 'Equivalent JSON numeric bounds were refused'
}
if (Test-SamePhysicalBounds @(88,111,404,281) @(88,111,405,281)) {
    throw 'Changed physical geometry accepted'
}
if (Test-SamePhysicalBounds @(88,111,404,281) @(88,111,404.5,281)) {
    throw 'Fractional geometry accepted'
}
if (Test-SamePhysicalBounds @(88,111,404,281) @(88,111,404)) {
    throw 'Missing coordinate accepted'
}
if (Test-SamePhysicalBounds @(88,111,404,281) @(88,111,404,[double]::NaN)) {
    throw 'Nonfinite geometry accepted'
}
[Console]::WriteLine('owned-metrics-policy-passed')
"""
)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Uses installed Windows PowerShell parser"
)
@pytest.mark.parametrize(
    "script_text",
    [POLICY_SCRIPT, GEOMETRY_SCRIPT, BOUNDS_SCRIPT],
    ids=["metrics", "geometry", "numeric_bounds"],
)
def test_native_movement_guard_refuses_extent_and_position_drift(
    tmp_path: Path, script_text: str
) -> None:
    """Execute real helper geometry/ownership math without a provider or display."""
    script = tmp_path / "check-metrics.ps1"
    script.write_text(script_text, encoding="utf-8")
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
