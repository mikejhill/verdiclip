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
        "\nfunction ", 1
    )[0]
    script = (
        "function Confirm-FixtureActivation"
        + function
        + r"""
$ErrorActionPreference = 'Stop'
[Console]::Error.WriteLine('ACTIVATION_PHASE=script-started')
$owner = [pscustomobject]@{ IsActive = ($case -eq 'active'); Calls = 0; Case = $case }
$owner | Add-Member ScriptMethod Activate {
    $this.Calls++
    if ($this.Case -eq 'inactive') { $this.IsActive = $true; return $true }
    if ($this.Case -eq 'incomplete') { return $true }
    return $false
}
$failed = $false
[Console]::Error.WriteLine('ACTIVATION_PHASE=before-decision')
try { Confirm-FixtureActivation $owner } catch { $failed = $true }
[Console]::Error.WriteLine('ACTIVATION_PHASE=after-decision')
if ($case -eq 'active') {
    if ($owner.Calls -ne 0 -or $failed) { throw 'Redundant activation' }
} elseif ($case -eq 'inactive') {
    if ($owner.Calls -ne 1 -or $failed -or -not $owner.IsActive) {
        throw 'Activation failed'
    }
} elseif (-not $failed -or $owner.Calls -ne 1) { throw 'Unsafe admission' }
[Console]::Error.WriteLine('ACTIVATION_PHASE=assertions-complete')
"""
    )
    # case is a closed parametrization, never external PowerShell text.
    command = [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"$case = '{case}'\n" + script,
    ]
    try:
        result = subprocess.run(  # noqa: S603 - repository function, closed fake cases
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"Pure activation case {case} exceeded unchanged 10s deadline; "
            f"last phase evidence: {error.stderr!r}",
            pytrace=False,
        )
    assert result.returncode == 0, result.stderr


def test_snapshot_diagnostics_are_bounded_and_best_effort() -> None:
    """Timing failures cannot fail admission; retired tracing does no process work."""
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the pure decision test")
    source = (
        Path(__file__).resolve().parents[1] / "capture/provider_fixture.ps1"
    ).read_text()
    function = (
        "function Write-SnapshotPhase"
        + source.split("function Write-SnapshotPhase", 1)[1].split(
            "$script:snapshotTraceComplete = $false", 1
        )[0]
    )
    script = (
        function
        + r"""
$ErrorActionPreference = 'Stop'
$fixtureProcess = [pscustomobject]@{Calls=0}
$fixtureProcess | Add-Member ScriptMethod Refresh {$this.Calls++;throw 'test failure'}
$script:snapshotTraceComplete = $false
Write-SnapshotPhase 'geometry-start'
if ($fixtureProcess.Calls -ne 1) { throw 'No attempted diagnostic' }
$script:snapshotTraceComplete = $true
Write-SnapshotPhase 'geometry-start'
if ($fixtureProcess.Calls -ne 1) { throw 'Unbounded diagnostic' }
"""
    )
    result = subprocess.run(  # noqa: S603 - repository function with fake process only
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
