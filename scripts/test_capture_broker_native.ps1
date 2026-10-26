param(
    [string[]]$PythonPaths = @(),
    [switch]$ExecuteAfterHandoff,
    [switch]$PhysicalObservation,
    [switch]$DeliveryOnly
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($DeliveryOnly -and $PhysicalObservation) { throw 'Delivery and physical-input lanes are separate windows.' }
if (-not $ExecuteAfterHandoff) {
    if ($DeliveryOnly) {
        Write-Output 'PLAN ONLY: reserve 2 minutes of native UI for three synthetic-only delivery cases on each Qt runtime. No screenshot, input, clipboard copy, or real registration.'
        Write-Output 'Private synthetic PNGs, real editor presentation and owned tray notification requests are exercised. Balloon visibility and physical notification clicks are not certified.'
        Write-Output 'Pass -PythonPaths for both isolated runtimes when a UI handoff is explicitly granted. Each owned case process is bounded to20s; whole lane120s.'
        return
    }
    Write-Output 'PLAN ONLY: no Qt application, native DLL, child, hooks or input.'
    Write-Output 'Reserve 8 minutes exclusive for six cases on each Qt runtime; reserve 2 additional minutes with Mike for physical raw-pointer cases.'
    Write-Output 'Each case creates only test-owned Qt hosts or one external Win32 owner+popup, plus passive capture overlays per display.'
    Write-Output 'Harness sends NO input. A human/separately authorized guarded driver follows driver-request.json, scoped to its listed HWNDs. Stop on driver refusal.'
    Write-Output 'Default keyboard and fixture-injected-pointer lanes are separate; only the physical lane can qualify raw-pointer observations.'
    Write-Output 'Each case is a separate process, 40s maximum; whole run 8 minutes (physical-only run 2 minutes), stop on first failure. Cleanup kills only the owned process tree if required.'
    return
}
if ($PythonPaths.Count -eq 0) { throw 'Pass -PythonPaths for the isolated verified Qt runtimes; no interpreter is selected implicitly.' }
$sourceHead = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceHead -notmatch '^[a-f0-9]{40}$') { throw 'Cannot establish source provenance.' }
$sourceChanges = & git -C $repoRoot status --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Cannot establish source cleanliness.' }
$sourceDirty = if ($sourceChanges) { 'true' } else { 'false' }
$cases = @(
    'qt-menu-production-default-escape-x1', 'owned-modal-production-default-escape-x1',
    'qt-menu-fixture-injected-absolute-drag-x1', 'win32-menu-production-default-escape-x1',
    'win32-menu-fixture-injected-absolute-drag-x1', 'qt-menu-production-default-escape-x2'
)
if ($PhysicalObservation) {
    $cases = @('qt-menu-physical-raw-pointer-drag-x1', 'win32-menu-physical-raw-pointer-drag-x1')
}
if ($DeliveryOnly) { $cases = @('popup-save-present', 'modal-provider-error', 'popup-write-retry') }
$directory = Join-Path $repoRoot ('.test-tmp/native-broker-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($directory) | Out-Null
$overall = [Diagnostics.Stopwatch]::StartNew()
$limit = if ($PhysicalObservation -or $DeliveryOnly) { 120 } else { 480 }
$caseLimit = if ($DeliveryOnly) { 20 } else { 40 }
foreach ($python in $PythonPaths) {
    $pythonPath = if ([IO.Path]::IsPathRooted($python)) { [IO.Path]::GetFullPath($python) } else { [IO.Path]::GetFullPath((Join-Path $repoRoot $python)) }
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'An explicit qualification interpreter does not exist.' }
    foreach ($case in $cases) {
        if ($overall.Elapsed.TotalSeconds -ge $limit) { throw 'Native matrix exceeded its overall bound; no further cases started.' }
        $caseDirectory = Join-Path $directory ([Guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($caseDirectory) | Out-Null
        $info = [Diagnostics.ProcessStartInfo]::new($pythonPath)
        $info.WorkingDirectory = $repoRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.Environment['QT_QPA_PLATFORM'] = 'windows'
        $info.Environment['PYTHONPATH'] = (Join-Path $repoRoot 'src')
        $info.Environment['PYTHONUTF8'] = '1'
        $info.Environment['VERDICLIP_QUALIFICATION_HEAD'] = $sourceHead
        $info.Environment['VERDICLIP_QUALIFICATION_DIRTY'] = $sourceDirty
        $selector = if ($DeliveryOnly) { 'tests/shell/test_delivery_native.py::test_native_synthetic_delivery[' + $case + ']' } else { 'tests/capture/test_broker_native.py::test_production_broker_preserves_declared_owners[' + $case + ']' }
        $handoffFlag = if ($DeliveryOnly) { '--native-delivery-handoff' } else { '--native-broker-handoff' }
        $arguments = @('-m', 'pytest', $selector,
            $handoffFlag, '--require-interactive-capture', '-s', '--no-cov',
            ('--basetemp=' + (Join-Path $caseDirectory 'temp')), ('--junitxml=' + (Join-Path $caseDirectory 'result.xml')))
        if ($PhysicalObservation) { $arguments += '--native-broker-physical' }
        foreach ($argument in $arguments) { $info.ArgumentList.Add($argument) }
        if ($DeliveryOnly) { Write-Output "Synthetic delivery case $case; no input required. Receipts at $caseDirectory/temp." } else { Write-Output "Case $case; follow the live driver-request.json under $caseDirectory/temp. No input is generated by this script." }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $info
        $started = $false
        $stdout = $null
        $stderr = $null
        try {
            if (-not $process.Start()) { throw 'Could not start owned qualification process.' }
            $started = $true
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            $remaining = [Math]::Min($caseLimit, $limit - $overall.Elapsed.TotalSeconds)
            if ($remaining -le 0 -or -not $process.WaitForExit([int]($remaining * 1000))) {
                $process.Kill($true)
                $process.WaitForExit(5000) | Out-Null
                throw 'Owned native case timed out; its process tree was terminated. This is a FAILED qualification.'
            }
            if ($process.ExitCode -ne 0) { throw "Native case failed with exit $($process.ExitCode); evidence retained at $caseDirectory" }
        } finally {
            if ($started -and -not $process.HasExited) { $process.Kill($true); $process.WaitForExit(5000) | Out-Null }
            if ($null -ne $stdout -and $stdout.Wait(5000)) {
                [IO.File]::WriteAllText((Join-Path $caseDirectory 'stdout.log'), $stdout.GetAwaiter().GetResult())
            }
            if ($null -ne $stderr -and $stderr.Wait(5000)) {
                [IO.File]::WriteAllText((Join-Path $caseDirectory 'stderr.log'), $stderr.GetAwaiter().GetResult())
            }
            $process.Dispose()
        }
    }
}
Write-Output "Matrix completed. Inspect receipts before any readiness decision. Evidence: $directory"
