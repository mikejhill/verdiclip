param(
    [string]$PythonPath = '.venv\Scripts\python.exe',
    [switch]$ExecuteAfterHandoff
)

# Default is a printed plan. The parent must hand over the exclusive desktop slot
# before -ExecuteAfterHandoff is used. Never use this to bypass an input-tool denial.
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = Join-Path $repoRoot 'src'
$testPath = Join-Path $repoRoot 'tests\editor\test_tabs_native.py'
$sourceHead = (& git -C $repoRoot rev-parse HEAD).Trim()
Write-Output 'Native tab matrix: merge, detach, Escape, hotkey cancellation.'
Write-Output 'Uses only synthetic documents and test-created windows; no screenshot acquisition.'
Write-Output 'Does not prove physical mouse gestures, Narrator or mixed-monitor Windows mapping.'
Write-Output "Interpreter: $PythonPath"
Write-Output "Source: $sourceRoot"
Write-Output "Git head: $sourceHead"
Write-Output "Tests: $testPath"
if (-not $ExecuteAfterHandoff) {
    Write-Output 'PLAN ONLY: no environment change, window creation or input was performed.'
    Write-Output 'After parent handoff, repeat with -ExecuteAfterHandoff and the intended Qt interpreter.'
    exit 0
}

$python = (Resolve-Path -LiteralPath $PythonPath).Path
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$proofRoot = Join-Path $repoRoot ".test-tmp\native-tabs-$stamp"
$oldPlatform = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM', 'Process')
$oldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
Push-Location $repoRoot
try {
    $env:QT_QPA_PLATFORM = 'windows'
    $env:PYTHONPATH = "$sourceRoot;$repoRoot"
    # Read-only WTS prerequisite. Unknown/locked/disconnected states stop here.
    & $python -c 'import sys; from PySide6.QtCore import qVersion; from tests.capture.desktop import DesktopSession; s=DesktopSession.current(); print("Qt",qVersion(),"WTS",s); sys.exit(0 if s is not None and s.state == 0 and s.flags == 1 else 2)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Interactive unlocked desktop was not positively verified. No native test was started.'
    }
    New-Item -ItemType Directory -Path $proofRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $proofRoot 'source-head.txt') -Value $sourceHead
    $pytestArgs = @('-m', 'pytest', $testPath, '--no-cov', '--require-interactive-capture',
        '--basetemp', (Join-Path $proofRoot 'temp'),
        '--junitxml', (Join-Path $proofRoot 'results.xml'), '-q')
    & $python @pytestArgs 2>&1 |
        Tee-Object -FilePath (Join-Path $proofRoot 'run.log')
    $testExit = $LASTEXITCODE
    Write-Output "Evidence: $proofRoot"
    if ($testExit -ne 0) {
        throw "Native matrix failed (exit $testExit). Preserve evidence; do not substitute another input route."
    }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', $oldPlatform, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $oldPythonPath, 'Process')
    Pop-Location
}
