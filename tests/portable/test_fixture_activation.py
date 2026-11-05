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
    """Timing failures cannot fail admission; retired tracing reads no clock."""
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
$clock = [pscustomobject]@{Calls=0}
$clock | Add-Member ScriptProperty ElapsedMilliseconds {
    $this.Calls++;throw 'test failure'
}
$script:snapshotTraceComplete = $false
Write-SnapshotPhase 'geometry-start'
if ($clock.Calls -ne 1) { throw 'No attempted diagnostic' }
$script:snapshotTraceComplete = $true
Write-SnapshotPhase 'geometry-start'
if ($clock.Calls -ne 1) { throw 'Unbounded diagnostic' }
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


def test_owned_identity_and_diagnostics_avoid_unrelated_startup_work() -> None:
    """Identity retains owned-process ticks; diagnostics never walk stacks or CPU."""
    source = (
        Path(__file__).resolve().parents[1] / "capture/provider_fixture.ps1"
    ).read_text()
    for removed in (
        "Get-Process",
        "StackTrace",
        "ForEach-Object",
        "Select-Object",
        "TotalProcessorTime",
        ".Refresh()",
    ):
        assert removed not in source
    assert "[Diagnostics.Process]::GetCurrentProcess()" in source
    assert "finally { $ownedProcess.Dispose() }" in source
    startup = source.split("$startedTicks = Get-FixtureStartedTicks", 1)[1]
    stop = "if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json')))"
    assert startup.index(stop) < startup.index("$window = [Windows.Window]::new()")


def test_actual_owned_identity_and_bounded_focus_diagnostics() -> None:
    """Execute real identity and fixed phase helpers, without loading WPF."""
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for the pure identity and phase test")
    source = (
        Path(__file__).resolve().parents[1] / "capture/provider_fixture.ps1"
    ).read_text()
    identity = (
        "function Get-FixtureStartedTicks"
        + source.split("function Get-FixtureStartedTicks", 1)[1].split(
            "# Loaded only", 1
        )[0]
    )
    focus = (
        "function Write-FocusPhase"
        + source.split("function Write-FocusPhase", 1)[1].split(
            "$window.Add_Activated", 1
        )[0]
    )
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + identity
        + focus
        + r"""
$ticks = Get-FixtureStartedTicks
if ($ticks -isnot [long] -or $ticks -le 0) { throw 'Invalid process generation' }
$expected = [Diagnostics.Process]::GetProcessById($PID)
try {
    if ($ticks -ne $expected.StartTime.ToUniversalTime().Ticks) {
        throw 'Wrong owned process generation'
    }
} finally { $expected.Dispose() }
# Exercise the actual finally body for both a successful and failed identity read.
foreach ($fail in @($false, $true)) {
    $startTime = [pscustomobject]@{Fail=$fail}
    $startTime | Add-Member ScriptMethod ToUniversalTime {
        if ($this.Fail) { throw 'identity unavailable' }
        return [DateTime]::UtcNow
    }
    $ownedProcess = [pscustomobject]@{Disposed=0;StartTime=$startTime}
    $ownedProcess | Add-Member ScriptMethod Dispose {$this.Disposed++}
    $body = ${function:Get-FixtureStartedTicks}.ToString()
    $body = $body.Substring($body.IndexOf('try {'))
    $failed = $false
    try { $null = & ([scriptblock]::Create($body)) } catch { $failed = $true }
    if ($ownedProcess.Disposed -ne 1 -or $failed -ne $fail) {
        throw 'Identity handle retirement changed'
    }
}
$script:focusEvents = 0
$clock = [pscustomobject]@{Calls=0}
$clock | Add-Member ScriptProperty ElapsedMilliseconds {
    $this.Calls++
    return 123
}
for ($index=0; $index -lt 6; $index++) { Write-FocusPhase 'activated' }
if ($script:focusEvents -ne 4 -or $clock.Calls -ne 4) {
    throw 'Focus diagnostics exceeded their work bound'
}
[Console]::WriteLine('owned-identity-and-focus-passed')
"""
    )
    result = subprocess.run(  # noqa: S603 - actual repository helpers, own process only
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "owned-identity-and-focus-passed"
    assert result.stderr.splitlines() == ["FIXTURE_FOCUS=activated elapsed_ms=123"] * 4
