"""Exercise the fixture's activation decision without loading GUI assemblies."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["active", "inactive", "refused", "incomplete"])
def test_fixture_activation_decision(case: str) -> None:
    """Only an inactive owned window needs activation; unsuccessful admission fails."""
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the pure decision test")
    source = (
        Path(__file__).resolve().parents[1] / "capture/provider_fixture.ps1"
    ).read_text()
    function = source.split("function Confirm-FixtureActivation", 1)[1].split(
        "function Write-FixtureState", 1
    )[0]
    script = (
        "function Confirm-FixtureActivation"
        + function
        + r"""
$ErrorActionPreference = 'Stop'
$owner = [pscustomobject]@{ IsActive = ($case -eq 'active'); Calls = 0; Case = $case }
$owner | Add-Member ScriptMethod Activate {
    $this.Calls++
    if ($this.Case -eq 'inactive') { $this.IsActive = $true; return $true }
    if ($this.Case -eq 'incomplete') { return $true }
    return $false
}
$failed = $false
try { Confirm-FixtureActivation $owner } catch { $failed = $true }
if ($case -eq 'active') {
    if ($owner.Calls -ne 0 -or $failed) { throw 'Redundant activation' }
} elseif ($case -eq 'inactive') {
    if ($owner.Calls -ne 1 -or $failed -or -not $owner.IsActive) {
        throw 'Activation failed'
    }
} elseif (-not $failed -or $owner.Calls -ne 1) { throw 'Unsafe admission' }
"""
    )
    # case is a closed parametrization, never external PowerShell text.
    result = subprocess.run(  # noqa: S603 - reviewed repository function, closed fake cases
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$case = '{case}'\n" + script,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
