"""Opt-in fixture marker tests, including actual Windows PowerShell pure probes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.capture import scroll_peer
from tests.capture.test_scroll_metric_policy import run_policy_probe

ROOT = Path(__file__).resolve().parents[2]
NONCE = "b" * 32
SOURCE = "a" * 40


def prepare_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> scroll_peer.ScrollPeer:
    """Start no native process and generate no costly bitmap in these pure tests."""
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(scroll_peer, "provider_pixels", lambda: b"synthetic")
    process = SimpleNamespace(pid=123, returncode=0)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    return scroll_peer.ScrollPeer(tmp_path / "fixture")


def test_disabled_trace_never_reads_diagnostic_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal startup does not resolve diagnostic paths or sample extra QPC data."""
    monkeypatch.delenv("VERDICLIP_FIXTURE_TRACE_DIR", raising=False)
    peer = prepare_peer(tmp_path, monkeypatch)

    def forbidden(*_args: object) -> None:
        raise AssertionError("Disabled diagnostics ran")

    monkeypatch.setattr(scroll_peer.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(scroll_peer.time, "get_clock_info", forbidden)
    peer.start()
    assert peer.process is not None
    assert peer.process.pid == 123
    assert peer._trace_directory is None
    peer._trace_exit()


@pytest.mark.parametrize("failure", ["clock", "manifest", "resolve", "exit_clock"])
def test_optional_provenance_failure_never_replaces_native_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Field evaluation as well as receipt I/O remains inside best-effort guards."""
    peer = prepare_peer(tmp_path, monkeypatch)
    trace = tmp_path / "private"
    trace.mkdir()
    monkeypatch.setattr(scroll_peer, "fixture_trace_directory", lambda _provider: trace)
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", SOURCE)

    def broken_clock() -> int:
        raise OSError("Optional QPC unavailable")

    if failure == "clock":
        monkeypatch.setattr(scroll_peer.time, "perf_counter_ns", broken_clock)
    elif failure == "manifest":
        (trace / "owner.json").write_text("existing-owned-evidence")
    elif failure == "resolve":

        def broken_resolve(_path: Path) -> Path:
            raise OSError("Optional path unavailable")

        monkeypatch.setattr(Path, "resolve", broken_resolve)
    peer.start()
    assert peer.process is not None
    assert peer.process.pid == 123
    if failure == "manifest":
        assert (trace / "owner.json").read_text() == "existing-owned-evidence"
    if failure == "exit_clock":
        monkeypatch.setattr(scroll_peer.time, "perf_counter_ns", broken_clock)
        peer._trace_exit()
        assert not (trace / "exit.json").exists()


def test_receipts_use_actual_popen_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No process-name lookup or managed-thread identity enters the ownership join."""
    peer = prepare_peer(tmp_path, monkeypatch)
    trace = tmp_path / "private"
    trace.mkdir()
    monkeypatch.setattr(scroll_peer, "fixture_trace_directory", lambda _provider: trace)
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", SOURCE)
    peer.start()
    owner = json.loads((trace / "owner.json").read_text())
    assert peer.process is not None
    assert owner["pid"] == peer.process.pid == 123
    assert owner["nonce"] == peer.nonce
    assert owner["source_sha"] == SOURCE
    assert owner["launch_begin_ns"] <= owner["launch_end_ns"]
    peer._forced = True
    peer._trace_exit()
    exited = json.loads((trace / "exit.json").read_text())
    assert exited["forced"] is True
    assert exited["pid"] == owner["pid"]


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "wrong_name",
        "sibling",
        "source",
        "clock",
        "nonmonotonic",
        "winforms",
        "symlink",
    ],
)
def test_only_explicit_private_trace_directory_is_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Hosted opt-in does not allow arbitrary marker file destinations."""
    trace = tmp_path / ("VerdiClip-" + NONCE)
    trace.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("VERDICLIP_FIXTURE_TRACE_DIR", str(trace))
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", SOURCE)
    monkeypatch.setenv("VERDICLIP_HOSTED_SOURCE", SOURCE)
    clock = SimpleNamespace(implementation="QueryPerformanceCounter()", monotonic=True)
    monkeypatch.setattr(scroll_peer.time, "get_clock_info", lambda _name: clock)
    if change == "wrong_name":
        monkeypatch.setenv("VERDICLIP_FIXTURE_TRACE_DIR", str(tmp_path))
    elif change == "sibling":
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "other"))
    elif change == "source":
        monkeypatch.setenv("VERDICLIP_HOSTED_SOURCE", "c" * 40)
    elif change == "clock":
        clock.implementation = "CLOCK_MONOTONIC"
    elif change == "nonmonotonic":
        clock.monotonic = False
    elif change == "symlink":
        trace.rmdir()
        trace.symlink_to(tmp_path, target_is_directory=True)
    assert scroll_peer.fixture_trace_directory(
        "winforms" if change == "winforms" else "wpf"
    ) == (trace if change == "none" else None)


def test_marker_source_is_opt_in_bounded_and_preserves_admission() -> None:
    """Fixed marker calls do not move native work or substitute its provider."""
    source = (ROOT / "tests/capture/provider_fixture.ps1").read_text()
    helper = (ROOT / "tests/capture/fixture_startup_trace.ps1").read_text()
    native = (ROOT / "tests/capture/native_scrollbar.py").read_text()
    assert "[AppDomain]::GetCurrentThreadId()" in helper
    assert "ManagedThreadId" not in helper
    assert "Add-Type" not in helper
    assert "ConvertTo-Json" not in helper
    assert "[IO.FileMode]::CreateNew" in helper
    assert "$script:fixtureTraceCount -ge 26" in helper
    assert "$script:fixtureTraceEnabled = $false" in source
    assert (
        source.index("Write-FixtureTrace 'window_begin'")
        < source.index("$window = [Windows.Window]::new()")
        < source.index("Write-FixtureTrace 'window_end'")
    )
    assert "remaining_ms = int((20 - (time.monotonic() - started)) * 1000)" in native
    assert native.index(
        "started = time.monotonic()", native.index("def prepare(")
    ) < native.index("self.peer.start()")


def test_workflow_is_explicit_isolated_and_cleans_before_publication() -> None:
    """No automatic push trial, artifact upload or altered acceptance infrastructure."""
    workflow = (ROOT / ".github/workflows/fixture-startup-diagnostic.yml").read_text()
    runner = (ROOT / "scripts/test_fixture_startup_diagnostics.ps1").read_text()
    assert "types: [opened, reopened]" in workflow
    assert "push:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "upload-artifact" not in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "5a5983275dfbd7e7243bd329ca3d859faca024c2" in workflow
    assert (
        "github.event.pull_request.head.ref == 'diagnose/013-fixture-startup'"
        in workflow
    )
    assert (
        "github.event.pull_request.base.ref == 'fix/013-capture-regressions'"
        in workflow
    )
    assert "git diff --quiet $base HEAD -- src" in workflow
    assert "'-m', 'scripts.fixture_startup_summary'" in runner
    assert (
        "'-start', ((Join-Path $PSScriptRoot 'gui_waits.wprp') + '!VerdiClipWaits')"
        in runner
    )
    assert runner.index("$state.trace_attempted = $true") < runner.index(
        "$code = Invoke-PrivateTool $wpr @('-start'"
    )
    assert runner.index("$removed = Remove-OwnedRawFiles") < runner.index(
        "if (Test-PublicationGate $retired ($removed -and $locatorRemoved) $analysis)"
    )
    assert "@('-cancel', '-instancename', $State.owner)" in runner
    assert workflow.index("Diagnose one unchanged") < workflow.index(
        "Test pure Windows"
    )


def test_recovery_identity_stays_private_and_native_walk_never_recurses_links() -> None:
    """Lifecycle proof is public, but recovery identities and targets are not."""
    runner = (ROOT / "scripts/test_fixture_startup_diagnostics.ps1").read_text()
    assert "VERDICLIP_FIXTURE_RUN_ID" not in runner
    assert "Write-PrivateLocator $state $temporaryRoot" in runner
    assert runner.index("Write-PrivateLocator $state $temporaryRoot") < runner.index(
        "$state.trace_attempted = $true"
    )
    assert "verdiclip-fixture-startup-locator.json" in runner
    verdict = runner.split("function Read-NativeVerdict", 1)[1].split("\nfunction ", 1)[
        0
    ]
    assert "Get-OwnedNativeTree" in verdict
    assert "Assert-OrdinaryTree" not in verdict
    assert "-Recurse" not in verdict
    removal = runner.split("function Remove-OwnedNativeTree", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "[IO.Directory]::Delete($current.FullName, $false)" in removal
    assert "[IO.File]::Delete($current.FullName)" in removal
    assert "-Recurse" not in removal
    assert "'SymbolicLink'" in removal
    assert "Remove-PrivateLocator $temporaryRoot $owner" in runner


PROBE = r"""
param([string]$Helper, [string]$Fixture, [string]$Directory, [string]$Case)
$ErrorActionPreference = 'Stop'
foreach ($source in @($Helper, $Fixture)) {
    $tokens = $null; $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile(
        $source, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Diagnostic PowerShell syntax failed' }
}
. $Helper
$env:RUNNER_TEMP = [IO.Directory]::GetParent($Directory).FullName
$nonce = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
if ($Case -eq 'bad_nonce') { $nonce += "`n" }
if ($Case -eq 'existing') {
    [IO.File]::WriteAllText((Join-Path $Directory 'markers.csv'), 'preserved')
}
Initialize-FixtureTrace $Directory $nonce
if ($Case -in @('bad_nonce', 'existing')) {
    if ($script:fixtureTraceEnabled) { throw 'Unsafe initialization admitted' }
    return
}
if (-not $script:fixtureTraceEnabled) { throw 'Trace unavailable' }
$culture = [Globalization.CultureInfo]::GetCultureInfo('tr-TR')
[Threading.Thread]::CurrentThread.CurrentCulture = $culture
Write-FixtureTrace 'entry'
Write-FixtureTrace 'window_begin'
Write-FixtureTrace 'window_begin'
if ($Case -eq 'bad_name') {
    Write-FixtureTrace 'arbitrary content'
    if ($script:fixtureTraceEnabled) { throw 'Unbounded name accepted' }
} elseif ($Case -eq 'write_failure') {
    $script:fixtureTraceWriter.Dispose()
    Write-FixtureTrace 'window_end'
    if ($script:fixtureTraceEnabled) { throw 'Failed write stayed active' }
} else {
    Write-FixtureTrace 'window_end'
    Write-FixtureTrace 'retirement_begin'
    Write-FixtureTrace 'fixture_retired'
}
Close-FixtureTrace
if ($script:fixtureTraceEnabled -or $null -ne $script:fixtureTraceWriter) {
    throw 'Marker writer not retired'
}
$lines = [IO.File]::ReadAllLines((Join-Path $Directory 'markers.csv'))
$expected = if ($Case -eq 'valid') { 5 } else { 2 }
if ($lines.Count -ne $expected) { throw 'Marker bounds failed' }
$thread = [AppDomain]::GetCurrentThreadId()
foreach ($line in $lines) {
    $fields = $line.Split(',')
    if ($fields.Count -ne 7 -or [int]$fields[2] -ne $PID -or
        [int]$fields[3] -ne $thread -or
        [long]$fields[5] -ne [Diagnostics.Stopwatch]::Frequency -or
        $fields[6] -cne $nonce) { throw 'Native identity mismatch' }
}
"""


@pytest.mark.skipif(
    sys.platform != "win32", reason="Actual installed Windows PowerShell 5.1 required"
)
@pytest.mark.parametrize(
    "case", ["valid", "bad_nonce", "existing", "bad_name", "write_failure"]
)
def test_actual_powershell_marker_helper(tmp_path: Path, case: str) -> None:
    """Exercise real parsing/QPC/native thread IDs and hostile data without WPF."""
    script = tmp_path / "probe.ps1"
    script.write_text(PROBE)
    directory = tmp_path / ("VerdiClip-" + NONCE)
    directory.mkdir()
    executable = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-File",
            str(script),
            "-Helper",
            str(ROOT / "tests/capture/fixture_startup_trace.ps1"),
            "-Fixture",
            str(ROOT / "tests/capture/provider_fixture.ps1"),
            "-Directory",
            str(directory),
            "-Case",
            case,
        ]
    )
    assert result.returncode == 0, result.stderr
    if case == "bad_nonce":
        assert not (directory / "markers.csv").exists()
    elif case == "existing":
        assert (directory / "markers.csv").read_text() == "preserved"


RUNNER_PROBE = r"""
param([string]$Runner, [string]$Directory, [string]$Case)
$ErrorActionPreference = 'Stop'
. $Runner -FunctionsOnly
if ($Case -eq 'public_values') {
    $env:GITHUB_ENV = Join-Path $Directory 'synthetic-job-env'
    Set-JobValue 'VERDICLIP_FIXTURE_NATIVE_EXIT' '0'
    $before = [IO.File]::ReadAllText($env:GITHUB_ENV)
    foreach ($bad in @(
        @('VERDICLIP_FIXTURE_RUN_ID', ('VerdiClip-' + ('b' * 32))),
        @('VERDICLIP_FIXTURE_NATIVE_EXIT', $Directory),
        @('VERDICLIP_FIXTURE_TRACE_RETIRED', 'false')
    )) {
        $refused = $false
        try { Set-JobValue $bad[0] $bad[1] } catch { $refused = $true }
        if (-not $refused -or
            [IO.File]::ReadAllText($env:GITHUB_ENV) -cne $before) {
            throw 'Private or malformed job value escaped'
        }
    }
    return
}
$script:repoRoot = Join-Path $Directory 'repo'
$null = [IO.Directory]::CreateDirectory($script:repoRoot)
$root = Join-Path $Directory 'private'
$null = [IO.Directory]::CreateDirectory($root)
$owner = 'VerdiClip-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
$state = [pscustomobject]@{
    trace_attempted=$true; trace_started=$true; trace_retired=$false
    trace_finalized=$false; owner=$owner; native_attempted=$false
    native_before=@(); native_directories=@()
}
$script:calls = [Collections.Generic.List[string]]::new()
function Assert-MicrosoftBinary([string]$Path) { }
function Set-JobValue([string]$Name, [string]$Value) { }
function Write-Ownership($State, [string]$Root) { }
function Invoke-PrivateTool($File, $Arguments, $Root, $Label, $Seconds) {
    if ($Arguments[-2] -cne '-instancename' -or
        $Arguments[-1] -cne $owner) { throw 'Unowned recording action' }
    $script:calls.Add($Arguments[0])
    if ($Case -eq 'stop_success') { return 0 }
    if ($Case -eq 'cancel_success' -and $Arguments[0] -ceq '-cancel') { return 0 }
    return 1
}
if ($Case -in @('stop_success', 'cancel_success', 'cancel_failure')) {
    $retired = Retire-OwnedTrace $state $root
    if ($Case -eq 'stop_success') {
        if (-not $retired -or -not $state.trace_finalized -or
            ($script:calls -join ',') -cne '-stop') { throw 'Stop proof failed' }
    } elseif ($Case -eq 'cancel_success') {
        if (-not $retired -or $state.trace_finalized -or
            ($script:calls -join ',') -cne '-stop,-cancel') {
            throw 'Cancel proof failed'
        }
    } elseif ($retired -or (Test-PublicationGate $retired $true @{})) {
        throw 'Unretired recording admitted'
    }
    return
}
if ($Case -eq 'start_uncertain') {
    $state.trace_started = $false
    $null = Retire-OwnedTrace $state $root
    if (($script:calls -join ',') -cne '-cancel') {
        throw 'Uncertain start skipped named cancellation'
    }
    return
}
if ($Case -eq 'gates') {
    foreach ($retired in @($true, $false)) {
        foreach ($removed in @($true, $false)) {
            $expected = $retired -and $removed
            if ((Test-PublicationGate $retired $removed @{}) -ne $expected) {
                throw 'Publication gate failed'
            }
            if (Test-PublicationGate $retired $removed $null) {
                throw 'Absent aggregate admitted'
            }
        }
    }
    if (Test-PublicationGate 'false' $true @{}) {
        throw 'String retirement proof admitted'
    }
    return
}
if ($Case -eq 'owner_schema') {
    $env:VERDICLIP_BENCHMARK_SOURCE_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $good = [ordered]@{
        schema=1; owner=$owner; source_sha=$env:VERDICLIP_BENCHMARK_SOURCE_SHA
        repair_base_sha='5a5983275dfbd7e7243bd329ca3d859faca024c2'
        repo_root=$script:repoRoot; private_root=$root
        trace_attempted=$false; trace_started=$false; trace_retired=$false
        trace_finalized=$false; native_attempted=$false; native_before=@()
        native_directories=@(); native_pid=0; native_started_ticks=0
        native_exited=$false; native_exit=125; native_forced=$false
        native_fixture_forced='unknown'; native_cleanup='unknown'
        native_ownership_uncertain=$false
    }
    $receipt = Join-Path $root 'runner-owner.json'
    [IO.File]::WriteAllText($receipt, ($good | ConvertTo-Json -Depth 4))
    $null = Get-OwnedState $root $owner
    foreach ($mutation in @(
        @('trace_retired', 'false'), @('native_pid', '123'),
        @('trace_started', $true), @('native_before', 'not-an-array'),
        @('native_fixture_forced', $false), @('source_sha', 'foreign')
    )) {
        $changed = [ordered]@{}
        foreach ($key in $good.Keys) { $changed[$key] = $good[$key] }
        $changed[$mutation[0]] = $mutation[1]
        [IO.File]::WriteAllText($receipt, ($changed | ConvertTo-Json -Depth 4))
        $refused = $false
        try { $null = Get-OwnedState $root $owner } catch { $refused = $true }
        if (-not $refused) { throw 'Malformed ownership admitted' }
    }
    return
}
$state.trace_retired = $true
if ($Case -eq 'live_writer') {
    if (Remove-OwnedRawFiles $state $root $false) { throw 'Live writer admitted' }
    if (-not [IO.Directory]::Exists($root)) { throw 'Live writer files removed' }
    return
}
if ($Case -eq 'delete_recovery') {
    $script:denyDelete = $true
    function Remove-Item {
        [CmdletBinding()]
        param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
        if ($script:denyDelete) { throw 'Injected deletion failure' }
        Microsoft.PowerShell.Management\Remove-Item @PSBoundParameters
    }
    $lock = Open-NativeLock $owner
    $removed = Remove-OwnedRawFiles $state $root $true
    if ($removed -or (Test-PublicationGate $true $removed @{})) {
        throw 'Failed removal admitted'
    }
    Close-NativeLock $lock $owner -Preserve
    $lock = Open-NativeLock $owner -Existing
    $script:denyDelete = $false
    if (-not (Remove-OwnedRawFiles $state $root $true)) {
        throw 'Owned deletion recovery failed'
    }
    Close-NativeLock $lock $owner
    if ([IO.Directory]::Exists($root)) { throw 'Private raw files survived' }
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows pwsh required")
@pytest.mark.parametrize(
    "case",
    [
        "stop_success",
        "cancel_success",
        "cancel_failure",
        "start_uncertain",
        "gates",
        "live_writer",
        "delete_recovery",
        "owner_schema",
        "public_values",
    ],
)
def test_actual_runner_cleanup_and_publication_guards(
    tmp_path: Path, case: str
) -> None:
    """Run real PowerShell functions with fake trace commands, never live tracing."""
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("Installed pwsh required for the runner's pure mocks")
    script = tmp_path / "runner-probe.ps1"
    script.write_text(RUNNER_PROBE)
    result = run_policy_probe(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Runner",
            str(ROOT / "scripts/test_fixture_startup_diagnostics.ps1"),
            "-Directory",
            str(tmp_path),
            "-Case",
            case,
        ]
    )
    assert result.returncode == 0, result.stderr


NATIVE_TREE_PROBE = r"""
param([string]$Runner, [string]$Directory, [string]$Case)
$ErrorActionPreference = 'Stop'
. $Runner -FunctionsOnly
$script:repoRoot = Join-Path $Directory 'repo'
$native = Join-Path $script:repoRoot (
    '.test-tmp/native-broker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
$outside = Join-Path $Directory 'outside'
if ($Case -in @('root_link', 'junction')) {
    $refused = $false
    try { $null = Get-OwnedNativeTree $native } catch { $refused = $true }
    if (-not $refused) { throw 'Ambiguous native root or junction admitted' }
    $refused = $false
    try { Remove-OwnedNativeTree $native } catch { $refused = $true }
    if (-not $refused) { throw 'Ambiguous native cleanup admitted' }
    if (-not [IO.File]::Exists((Join-Path $outside 'sentinel'))) {
        throw 'Foreign junction target modified'
    }
    return
}
$tree = Get-OwnedNativeTree $native
$receipts = @($tree.files | Where-Object { $_.Name -ceq 'scrollbar-native.json' })
if ($receipts.Count -ne 1) {
    throw 'Receipt discovery followed or duplicated a symlink'
}
$state = [pscustomobject]@{
    native_directories=@('native-broker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
    native_cleanup='unknown'; native_fixture_forced='unknown'
    native_attempted=$true; native_before=@(); native_ownership_uncertain=$false
    trace_retired=$true
}
Read-NativeVerdict $state
if ($state.native_cleanup -cne 'confirmed' -or
    $state.native_fixture_forced -cne 'false') { throw 'Ordinary receipt unavailable' }
if ($Case -eq 'real_pytest') {
    # Exercise the actual verdict-to-cleanup path, not just the walker helper.
    function Set-JobValue([string]$Name, [string]$Value) { }
    $private = Join-Path $Directory 'private'
    $null = [IO.Directory]::CreateDirectory($private)
    if (-not (Remove-OwnedRawFiles $state $private $true)) {
        throw 'Native receipt-to-cleanup path failed'
    }
    if ([IO.Directory]::Exists($private)) { throw 'Raw trace root survived' }
} else { Remove-OwnedNativeTree $native }
if ([IO.Directory]::Exists($native)) { throw 'Native raw files survived' }
if ($Case -eq 'foreign_link' -and
    [IO.File]::ReadAllText((Join-Path $outside 'sentinel')) -cne 'preserved') {
    throw 'Foreign symlink target modified'
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows reparse semantics")
@pytest.mark.parametrize(
    "case", ["pytest_current", "real_pytest", "foreign_link", "root_link", "junction"]
)
def test_actual_windows_native_tree_never_follows_links(
    tmp_path: Path, case: str
) -> None:
    """Exercise real Windows links, not mocked reparse flags or unlink outcomes."""
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("Installed pwsh required for owned native tree tests")
    native = tmp_path / "repo/.test-tmp" / ("native-broker-" + NONCE)
    ordinary = native / "case/temp/test_native0"
    ordinary.mkdir(parents=True)
    if case != "real_pytest":
        (ordinary / "scrollbar-native.json").write_text(
            json.dumps({"cleanup_errors": [], "retired": True})
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("preserved")
    (outside / "scrollbar-native.json").write_text("must never be read")
    if case == "real_pytest":
        probe = tmp_path / "test_owned_receipt.py"
        probe.write_text(
            "def test_owned_receipt(tmp_path):\n"
            "    (tmp_path / 'scrollbar-native.json').write_text(\n"
            '        \'{"cleanup_errors": [], "retired": true}\')\n'
        )
        config = tmp_path / "pytest.ini"
        config.write_text("[pytest]\n")
        environment = os.environ.copy()
        environment.pop("PYTEST_ADDOPTS", None)
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        generated = subprocess.run(  # noqa: S603 - one fixed pure test in owned temp tree
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(config),
                str(probe),
                "--basetemp",
                str(native / "case/temp"),
                "-p",
                "no:cacheprovider",
                "-q",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert generated.returncode == 0, generated.stderr
        aliases = list((native / "case/temp").glob("*current"))
        assert len(aliases) == 1
        assert aliases[0].is_symlink()
    elif case == "pytest_current":
        ordinary.with_name("test_nativecurrent").symlink_to(
            ordinary, target_is_directory=True
        )
    elif case == "foreign_link":
        (native / "foreign").symlink_to(outside, target_is_directory=True)
    elif case == "root_link":
        shutil.rmtree(native)
        native.symlink_to(outside, target_is_directory=True)
    else:
        command = Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"
        created = subprocess.run(  # noqa: S603 - create a disposable test junction only
            [
                str(command),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(native / "junction"),
                str(outside),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    script = tmp_path / "native-tree-probe.ps1"
    script.write_text(NATIVE_TREE_PROBE)
    result = run_policy_probe(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Runner",
            str(ROOT / "scripts/test_fixture_startup_diagnostics.ps1"),
            "-Directory",
            str(tmp_path),
            "-Case",
            case,
        ]
    )
    assert result.returncode == 0, result.stderr
    assert (outside / "sentinel").read_text() == "preserved"
    if case in {"root_link", "junction"}:
        assert native.exists()
    else:
        assert not native.exists()


LOCATOR_PROBE = r"""
param([string]$Runner, [string]$Directory, [string]$Case)
$ErrorActionPreference = 'Stop'
. $Runner -FunctionsOnly
$script:repoRoot = Join-Path $Directory 'repo'
$null = [IO.Directory]::CreateDirectory($script:repoRoot)
$env:RUNNER_TEMP = $Directory
$env:GITHUB_REPOSITORY = 'mikejhill/verdiclip'
$env:GITHUB_RUN_ID = '123456'
$env:GITHUB_RUN_ATTEMPT = '1'
$env:GITHUB_JOB = 'diagnose'
$env:VERDICLIP_BENCHMARK_SOURCE_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$env:VERDICLIP_FIXTURE_REPAIR_BASE = '5a5983275dfbd7e7243bd329ca3d859faca024c2'
$owner = 'VerdiClip-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
$root = Join-Path $Directory $owner
$state = [pscustomobject]@{
    owner=$owner; source_sha=$env:VERDICLIP_BENCHMARK_SOURCE_SHA
    repair_base_sha=$env:VERDICLIP_FIXTURE_REPAIR_BASE
    private_root=$root; repo_root=$script:repoRoot
}
$script:publicValues = [Collections.Generic.List[string]]::new()
function Set-JobValue([string]$Name, [string]$Value) {
    $script:publicValues.Add($Name + '=' + $Value)
}
Write-PrivateLocator $state $Directory
$locator = Get-PrivateLocatorPath $Directory
$before = [IO.File]::ReadAllText($locator)
if (($script:publicValues -join ',').Contains($owner) -or
    ($script:publicValues -join ',').Contains($Directory)) {
    throw 'Private locator escaped into public environment'
}
if ($Case -eq 'exclusive') {
    $refused = $false
    try { Write-PrivateLocator $state $Directory } catch { $refused = $true }
    if (-not $refused -or [IO.File]::ReadAllText($locator) -cne $before) {
        throw 'Existing locator overwritten'
    }
    return
}
if ($Case -eq 'wrong_owner_remove') {
    $refused = $false
    try { Remove-PrivateLocator $Directory ('VerdiClip-' + ('c' * 32)) }
    catch { $refused = $true }
    if (-not $refused -or [IO.File]::ReadAllText($locator) -cne $before) {
        throw 'Foreign locator removal admitted'
    }
    return
}
if ($Case -in @('unproved_cleanup', 'raw_present')) {
    if ($Case -eq 'raw_present') {
        $null = [IO.Directory]::CreateDirectory($root)
        [IO.File]::WriteAllText((Join-Path $root 'raw'), 'private')
        $env:VERDICLIP_FIXTURE_TRACE_RETIRED = '1'
        $env:VERDICLIP_FIXTURE_READY_TO_REMOVE = '1'
    } else {
        $env:VERDICLIP_FIXTURE_TRACE_RETIRED = '0'
        $env:VERDICLIP_FIXTURE_READY_TO_REMOVE = '0'
    }
    $refused = $false
    try { Remove-PrivateLocator $Directory $owner } catch { $refused = $true }
    if (-not $refused -or [IO.File]::ReadAllText($locator) -cne $before) {
        throw 'Recovery locator removed before cleanup proof'
    }
    return
}
if ($Case -eq 'context') { $env:GITHUB_RUN_ATTEMPT = '2' }
if ($Case -eq 'partial') { [IO.File]::WriteAllText($locator, '{') }
if ($Case -eq 'missing') { [IO.File]::Delete($locator) }
if ($Case -in @('source', 'path')) {
    $value = $before | ConvertFrom-Json
    if ($Case -eq 'source') { $value.source_sha = 'c' * 40 }
    else { $value.private_root = Join-Path $Directory 'unowned' }
    [IO.File]::WriteAllText($locator, ($value | ConvertTo-Json -Depth 4))
}
if ($Case -eq 'linked') {
    $foreign = Join-Path $Directory 'foreign-locator'
    [IO.File]::Move($locator, $foreign)
    $null = New-Item -ItemType SymbolicLink -Path $locator -Target $foreign
}
if ($Case -ne 'valid') {
    $refused = $false
    try { $null = Read-PrivateLocator $Directory } catch { $refused = $true }
    if (-not $refused) { throw 'Invalid private locator admitted' }
    return
}
$value = Read-PrivateLocator $Directory
if ($value.owner -cne $owner -or $value.private_root -cne $root) {
    throw 'Private locator identity changed'
}
$env:VERDICLIP_FIXTURE_TRACE_RETIRED = '1'
$env:VERDICLIP_FIXTURE_READY_TO_REMOVE = '1'
Remove-PrivateLocator $Directory $owner
if ([IO.File]::Exists($locator)) { throw 'Private locator deletion unverified' }
if ($script:publicValues -cnotcontains 'VERDICLIP_FIXTURE_LIFECYCLE=2') {
    throw 'Cleanup completion was not recorded'
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows private locator")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "exclusive",
        "wrong_owner_remove",
        "unproved_cleanup",
        "raw_present",
        "context",
        "partial",
        "missing",
        "source",
        "path",
        "linked",
    ],
)
def test_actual_windows_private_recovery_locator(tmp_path: Path, case: str) -> None:
    """The fallback reads only its exclusive private locator and exact run context."""
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("Installed pwsh required for private locator tests")
    script = tmp_path / "locator-probe.ps1"
    script.write_text(LOCATOR_PROBE)
    result = run_policy_probe(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Runner",
            str(ROOT / "scripts/test_fixture_startup_diagnostics.ps1"),
            "-Directory",
            str(tmp_path),
            "-Case",
            case,
        ]
    )
    assert result.returncode == 0, result.stderr
