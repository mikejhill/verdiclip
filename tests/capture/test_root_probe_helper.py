"""Constrain the cold root experiment without invoking a desktop or UIA client."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from tests.capture.root_comparison import RootProbeTrace
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace

HERE = Path(__file__).parent
HELPER = HERE / "uia_root_probe.ps1"
NATIVE = HERE / "uia_root_probe_native.cs"
SOURCE = HERE.parents[1] / "src/verdiclip/capture/scroll"
WINDOWS = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Installed Windows PowerShell compiler and pure fakes",
)


def helper_source() -> str:
    """Read only the reviewed, test-owned script."""
    return HELPER.read_text(encoding="utf-8")


def native_source() -> str:
    """Read only the reviewed, test-owned client."""
    return NATIVE.read_text(encoding="utf-8")


def test_single_cold_request_uses_existing_private_pipe_protocol() -> None:
    """No warm lease reuse, provider preflight, added deadline, or alternate IPC."""
    helper = helper_source()
    assert "[Parameter(Mandatory=$true)][string]$PipeName" in helper
    assert "[ValidateSet('managed', 'native')][string]$Client" in helper
    assert "\\Averdiclip-uia-[a-f0-9]{32}\\z" in helper
    assert "[IO.Pipes.NamedPipeClientStream]::new(" in helper
    assert "$channel.Connect(5000)" in helper
    assert "@{ ready=$true; protocol=1 }" in helper
    assert helper.count("$reader.ReadLine()") == 1
    assert helper.count("$result = Invoke-RootProbe $request $runtime") == 1
    assert helper.index("$runtime = Start-RootProbe") < helper.index("ready=$true")
    assert helper.index("ready=$true") < helper.index("$reader.ReadLine()")
    assert "while (" not in helper
    assert "Start-Sleep" not in helper
    assert "Read-Host" not in helper


def test_startup_only_compiles_equal_read_only_scaffolding() -> None:
    """Managed adds its assemblies; native has no activation before the request."""
    startup = (
        helper_source()
        .split("function Start-RootProbe {", 1)[1]
        .split("function Invoke-RootProbe", 1)[0]
    )
    for assembly in (
        "UIAutomationClient",
        "UIAutomationTypes",
        "WindowsBase",
        "System.Drawing",
    ):
        assert f"Add-Type -AssemblyName {assembly}" in startup
    assert "if ($Client -ceq 'managed')" in startup
    for filename in (
        "uia_nofocus_interop.cs",
        "uia_root_probe_native.cs",
        "uia_desktop.cs",
    ):
        assert filename in startup
    assert "uia_nofocus.cs" not in helper_source()
    for forbidden in ("Activator", "::FromHandle", ".Construct(", ".Acquire("):
        assert forbidden not in startup
    native = native_source()
    assert "static RootProbe" not in native
    assert "public RootProbeSession(" not in native
    assert "RequireNativeOnlyProcess()" in startup


def test_direct_calling_thread_and_exact_runtime_receipt() -> None:
    """The script and client use the actual MTA, not a compensating new thread."""
    helper = helper_source()
    native = native_source()
    assert "[Threading.Thread]::CurrentThread.GetApartmentState()" in helper
    assert "Thread.CurrentThread.GetApartmentState() == ApartmentState.MTA" in native
    assert "$PSVersionTable.PSVersion.Major -ne 5" in helper
    assert "$PSVersionTable.PSVersion.Minor -ne 1" in helper
    assert "[IntPtr]::Size -ne 8" in helper
    assert "$env:PROCESSOR_ARCHITECTURE -cne 'AMD64'" in helper
    assert "$version.Length -gt 32" in helper
    assert "\\A[0-9]+(\\.[0-9]+){1,3}\\z" in helper
    assert "powershell=$powershell; clr=$clr; architecture='x64'" in helper
    for forbidden in ("RunOnMta", "new Thread", "SetApartmentState", "Task.Run"):
        assert forbidden not in helper + native


def test_exact_integer_identity_is_checked_before_any_native_call() -> None:
    """No JSON bool, numeric string, or floating-point timestamp is admitted."""
    helper = helper_source()
    assert "($Value -is [int] -or $Value -is [long])" in helper
    assert "$Value -gt 0 -and $Value -le $Maximum" in helper
    assert "$Names -cnotcontains $name" in helper
    assert "$Request.action -isnot [string]" in helper
    assert "$Request.action -cne 'inspect'" in helper
    assert "$Request.request_id -isnot [string]" in helper
    assert "$Request.request_id -cnotmatch '\\A[a-f0-9]{32}\\z'" in helper
    assert "$payload.process_id ([int]::MaxValue)" in helper
    assert "$payload.started_ticks ([datetime]::MaxValue.Ticks)" in helper
    operation = helper.split("function Invoke-RootProbe", 1)[1]
    assert operation.index("Assert-ProbeRequest $Request") < operation.index(
        "[IntPtr]::new([long]$payload.window_handle)"
    )


def test_native_owned_fields_precede_casts_and_validations() -> None:
    """Even partial construction and malicious VARIANT RCWs remain releasable."""
    native = native_source()
    assert native.index("instance = Activator.CreateInstance(type)") < native.index(
        "automation = (IAutomation2)instance"
    )
    assert 'new Guid("e22ad333-b25f-460c-83d0-0581107395c9")' in native
    assert "automation.put_AutoSetFocus(0)" in native
    assert native.count("automation.get_AutoSetFocus(out enabled)") == 2
    assert "automation.ElementFromHandle(window, out root)" in native
    assert "root.GetCurrentPropertyValue(30002, out processValue)" in native
    assert "root.GetCurrentPropertyValue(30020, out windowValue)" in native
    assert "processValue is int && (int)processValue == expectedProcess" in native
    assert "windowValue is int && (long)(int)windowValue == expectedHandle" in native
    assert "runtime.Length >= 1 && runtime.Length <= 64" in native
    assert "object[] owned = { instance, root, processValue, windowValue }" in native
    assert "int index = values.Length - 1; index >= 0; index--" in native
    assert "Object.ReferenceEquals(value, values[later])" in native
    assert "if (isCom(value)) Require(release(value) == 0)" in native
    assert "catch (Exception error) { failures.Add(error); }" in native
    assert "ExceptionDispatchInfo.Capture(failures[0]).Throw()" in native
    assert native.index("released = true;") < native.index(
        "RootProbeGuard.ReleaseOwned(owned"
    )


def test_guards_cover_generation_foreground_desktop_and_dpi_restore() -> None:
    """A handle alone, unknown desktop, changed foreground, or failed restore fails."""
    native = native_source()
    helper = helper_source()
    for guard in (
        "CaptureDesktop.Interactive()",
        "CaptureDesktop.IsWindowVisible(window)",
        "!CaptureDesktop.IsIconic(window)",
        "GetWindowThreadProcessId(window, out owner) != 0",
        "owner == (uint)processId",
        "process.StartTime.ToUniversalTime().Ticks == startedTicks",
        "GetForegroundWindow() == new IntPtr(foreground)",
        "AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), new IntPtr(-4))",
        "RestoreDpiWithCalls(previous, CaptureDesktop.SetThreadDpiAwarenessContext,",
        "GetThreadDpiAwarenessContext, AreDpiAwarenessContextsEqual, "
        "RootProbeTiming.Phase)",
        "Require(previous != IntPtr.Zero)",
        "IntPtr replaced = set(previous)",
        "Require(replaced != IntPtr.Zero)",
        "IntPtr current = get()",
        "Require(equal(current, previous))",
    ):
        assert guard in native
    assert helper.count("::RequireOwnedWindow(") == 3
    assert helper.count("::RequireNativeOnlyProcess()") == 4
    assert helper.count("::RequirePhysicalPixels()") == 2
    assert "$current.ProcessId -ne $payload.process_id" in helper
    assert "[long]$current.NativeWindowHandle -ne $payload.window_handle" in helper
    assert "::RequireRuntime($root.GetRuntimeId())" in helper


def test_foreground_boundary_samples_stay_outside_root_timing() -> None:
    """Cheap stage checks do not add process lookups or continuous monitoring."""
    helper = re.sub(
        r"^ +\[VerdiClip.Automation.RootProbeTiming\]::Phase\('probe_[a-z_0-9]+'\)\n",
        "",
        helper_source(),
        flags=re.MULTILINE,
    )
    guard = "[VerdiClip.Automation.RootProbeGuard]::RequireForeground("
    assert helper.count(guard) == 4
    assert (
        "$session.Construct()\n            "
        + guard
        + "$payload.foreground_handle)\n            $session.Acquire("
    ) in helper
    assert (
        "$session.Acquire($payload.window_handle, $requestId)\n            "
        + guard
        + "$payload.foreground_handle)\n            $session.Validate("
    ) in helper
    assert (
        "::RootRecord($requestId, $durations.root)\n            "
        + guard
        + "$payload.foreground_handle)\n            $started = "
        + "[VerdiClip.Automation.RootProbeTiming]::Start()"
    ) in helper
    cleanup = helper.split("    finally {", 1)[1]
    assert cleanup.index("$releaseConfirmed = $true") < cleanup.index(guard)
    assert cleanup.index(guard) < cleanup.index("::RestoreDpi(")
    body = native_source().split("public static void RequireForeground(", 1)[1]
    body = body.split("public static void RequireOwnedWindow(", 1)[0]
    assert "expected > 0 && GetForegroundWindow() == new IntPtr(expected)" in body
    assert "Process." not in body
    assert "Thread." not in body
    assert "while" not in body


def test_success_is_written_only_after_release_restore_and_final_guards() -> None:
    """Cleanup failures preserve the primary exception and prohibit ok=true."""
    helper = helper_source()
    operation = helper.split("function Invoke-RootProbe", 1)[1].split(
        "$channel = $null;", 1
    )[0]
    assert operation.index("$session.Release()") < operation.index("::RestoreDpi(")
    assert operation.index("::RestoreDpi(") < operation.index(
        "if ($null -ne $failure) { throw $failure }"
    )
    assert operation.index("if ($null -ne $failure)") < operation.index(
        "client=$Client; apartment='MTA'"
    )
    assert (
        operation.count(
            "[AggregateException]::new([Exception[]]@($failure, $_.Exception))"
        )
        == 3
    )
    assert "if (-not $releaseConfirmed -or -not $dpiRestored)" in operation
    assert "$current = $null\n                $root = $null" in operation
    assert "managed_reference_dropped" in operation
    assert "native_rcw_release_completed" in operation
    assert helper.index("$result = Invoke-RootProbe") < helper.index("ok=$true")
    assert "$responseAttempted = $true\n    $writer.WriteLine" in helper
    assert "$null -ne $writer -and -not $responseAttempted" in helper
    assert "error='unsupported_or_changed'" in helper
    assert "error=$" not in helper
    assert "WriteLine($_" not in helper
    assert "WriteLine($failure" not in helper


def test_timings_bracket_only_the_single_root_calls() -> None:
    """The diagnostic cannot initialize UIA, retry, or catch an operation failure."""
    helper = helper_source()
    native = native_source()
    managed = helper.split("$root = [System.Windows.Automation", 1)[1].split(
        "$started = [VerdiClip.Automation.RootProbeTiming]::Start()", 1
    )[0]
    assert managed.startswith(".AutomationElement]::FromHandle($handle)\n")
    assert "::Finish($started)" in managed
    assert "::RootRecord($requestId, $durations.root)" in managed
    assert helper.count("::FromHandle(") == 1
    assert native.count("automation.ElementFromHandle(") == 1
    acquire = native.split("public void Acquire(", 1)[1].split(
        "public void Validate(", 1
    )[0]
    assert (
        acquire.index("long? started = RootProbeTiming.Start()")
        < acquire.index("automation.ElementFromHandle(")
        < acquire.index("RootUs = RootProbeTiming.Finish(started)")
    )
    assert "catch" not in acquire
    assert "duration.Value.ToString(CultureInfo.InvariantCulture)" in native
    assert "elapsed >= 0 && elapsed <= 999999999" in native


def test_all_emitted_phases_are_test_only_fixed_allowlisted_markers() -> None:
    """No provider text, runtime IDs, or unreviewed stderr vocabulary."""
    helper = helper_source()
    native = native_source()
    phases = re.findall(r"::Phase\('([a-z_0-9]+)'\)", helper)
    phases += re.findall(r'RootProbeTiming.Phase\("([a-z_0-9]+)"\)', native)
    phases += re.findall(r"'VERDICLIP_UIA_PHASE=([a-z_0-9]+)'", helper)
    phases += re.findall(r'phase\("([a-z_0-9]+)"\)', native)
    assert phases
    assert all(
        f"VERDICLIP_UIA_PHASE={phase}".encode() in RootProbeTrace.MARKERS
        for phase in phases
    )
    assert "VERDICLIP_UIA_ROOT_US=" in native
    result = helper.split("client=$Client; apartment='MTA'", 1)[1].split("\n    }", 1)[
        0
    ]
    assert "window_handle" not in result
    assert "request_id" not in result
    assert "runtime_id" not in result
    assert "started_ticks" not in result


@pytest.mark.parametrize(
    "forbidden",
    [
        "SetFocus(",
        "SetForegroundWindow(",
        "SetCursorPos(",
        "SendInput(",
        "SetWindowsHookEx",
        "CopyFromScreen",
        "GetCurrentPattern",
        "SetScrollPercent",
        "ElementFromPoint",
        "GetFocusedElement",
        "CreateTreeWalker",
        "FindFirst(",
        "FindAll(",
        "GetParentElement",
        "RegisterRawInput",
        "FinalReleaseComObject",
        "GC.Collect",
    ],
)
def test_root_only_probe_has_no_input_pattern_traversal_or_forced_gc(
    forbidden: str,
) -> None:
    """The experiment cannot become an alternate capture or scrolling product."""
    source = helper_source() + native_source()
    # AutoSetFocus is an intentionally required, per-client policy property.
    source = source.replace("put_AutoSetFocus(", "put_AutoPolicy(").replace(
        "get_AutoSetFocus(", "get_AutoPolicy("
    )
    assert forbidden not in source


def run_windows_script(tmp_path: Path, name: str, body: str) -> str:
    """Run compilation or inert policy only, never the helper's transport entry."""
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(script),
            "-Helper",
            str(HELPER),
            "-NativeSource",
            str(NATIVE),
            "-Source",
            str(SOURCE),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return result.stdout.strip()


AST_FUNCTIONS = r"""
param([string]$Helper, [string]$NativeSource, [string]$Source)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $Helper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Script syntax failed' }
foreach ($name in @('Test-ProbeFields', 'Test-ProbeInteger', 'Assert-ProbeRequest')) {
    $function = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq $name
    }, $true)
    if ($null -eq $function) { throw 'Missing policy function' }
    . ([scriptblock]::Create($function.Extent.Text))
}
"""


@WINDOWS
@pytest.mark.parametrize("client", ["managed", "native"])
def test_actual_startup_compiles_without_constructing_clients(
    tmp_path: Path, client: str
) -> None:
    """Fresh PS5.1 processes parse and compile actual sources without desktop calls."""
    script = (
        AST_FUNCTIONS
        + "$Client='"
        + client
        + "'\n"
        + r"""
$function = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Start-RootProbe'
}, $true)
$body = $function.Extent.Text.Replace('$PSScriptRoot', '$ProbeDirectory')
$ProbeDirectory = Split-Path -Parent $Helper
. ([scriptblock]::Create($body))
$receipt = Start-RootProbe
if ($receipt.architecture -cne 'x64' -or $receipt.powershell -notmatch '^5\.1\.') {
    throw 'Runtime receipt missing'
}
if ($null -eq ('VerdiClip.Automation.RootProbeSession' -as [type])) {
    throw 'Test client did not compile'
}
if ($null -ne ('VerdiClip.Automation.NoFocusScroll' -as [type])) {
    throw 'Mutation implementation compiled'
}
if ($Client -ceq 'native') {
    [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess()
} else {
    if ($null -eq ('System.Windows.Automation.AutomationElement' -as [type])) {
        throw 'Managed UIA missing'
    }
}
[Console]::WriteLine('startup-only-passed')
"""
    )
    assert (
        run_windows_script(tmp_path, f"startup-{client}.ps1", script)
        == "startup-only-passed"
    )


@WINDOWS
def test_actual_request_validation_rejects_noninteger_and_extra_fields(
    tmp_path: Path,
) -> None:
    """Execute the real parser policy with JSON, not a second implementation."""
    script = (
        AST_FUNCTIONS
        + r"""
function New-Request {
    $json='{"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","action":"inspect",' +
        '"payload":{"window_handle":100,"process_id":42,' +
        '"started_ticks":638000000000000000,"foreground_handle":200}}'
    return $json | ConvertFrom-Json
}
$valid = New-Request
Assert-ProbeRequest $valid
$count = 0
function Refuse($request) {
    try { Assert-ProbeRequest $request } catch { $script:count++; return }
    throw 'Unsafe request accepted'
}
$fields=@('window_handle','process_id','started_ticks','foreground_handle')
foreach ($field in $fields) {
    foreach ($bad in @($null,$true,$false,'100',0,-1,1.5,[double]100)) {
        $request = New-Request; $request.payload.$field = $bad; Refuse $request
    }
}
$request=New-Request; $request.payload.process_id=[long]2147483648; Refuse $request
$request=New-Request
$request.payload.started_ticks=[long]3155378976000000000; Refuse $request
foreach ($bad in @($null,42,$true,('A'*32),('a'*31),('a'*32+"`n"))) {
    $request=New-Request; $request.request_id=$bad; Refuse $request
}
foreach ($bad in @($null,42,$true,'INSPECT','step','observe')) {
    $request=New-Request; $request.action=$bad; Refuse $request
}
$request=New-Request; $request | Add-Member NoteProperty extra 1; Refuse $request
$request=New-Request
$request.payload | Add-Member NoteProperty extra 1; Refuse $request
$request=New-Request; $request.PSObject.Properties.Remove('payload'); Refuse $request
$request=New-Request
$request.payload.PSObject.Properties.Remove('process_id'); Refuse $request
$request=New-Request; $request.payload=@(100,42,638000000000000000,200); Refuse $request
Refuse @($valid,$valid)
if ($count -ne 52) { throw 'Policy cases were not all executed' }
[Console]::WriteLine('request-policy-passed')
"""
    )
    assert (
        run_windows_script(tmp_path, "request-policy.ps1", script)
        == "request-policy-passed"
    )


@WINDOWS
def test_actual_compiled_release_policy_handles_aliases_and_failures(
    tmp_path: Path,
) -> None:
    """Only pure release delegates run; no native client, window, or COM is used."""
    script = r"""
param([string]$Helper, [string]$NativeSource, [string]$Source)
$ErrorActionPreference='Stop'
$tests = @'
using System;
using System.Collections.Generic;
using VerdiClip.Automation;
public static class PureRootProbeTests {
    public static void Run() {
        object client = new object(), root = new object(), variant = new object();
        var calls = new List<object>();
        Func<object,int> release = value => { calls.Add(value); return 0; };
        RootProbeGuard.ReleaseOwned(new object[] { client, root, null, variant },
            value => true, release);
        if (calls.Count != 3 || calls[0] != variant || calls[1] != root ||
            calls[2] != client)
            throw new Exception("Wrong release order");
        calls.Clear();
        RootProbeGuard.ReleaseOwned(new object[] { client, root, root, client },
            value => true, release);
        if (calls.Count != 2 || calls[0] != client || calls[1] != root)
            throw new Exception("Alias released twice");
        calls.Clear();
        var primary = new InvalidOperationException("private-provider-text");
        Exception seen = null;
        try {
            RootProbeGuard.ReleaseOwned(new object[] { client, root }, value => true,
                value => {
                    calls.Add(value); if (value == root) throw primary; return 0;
                });
        } catch (Exception error) { seen = error; }
        if (!Object.ReferenceEquals(seen, primary) || calls.Count != 2 ||
            calls[1] != client)
            throw new Exception("Failure lost or remaining resource leaked");
        calls.Clear();
        seen = null;
        try {
            RootProbeGuard.ReleaseOwned(new object[] { client, root }, value => true,
                value => { calls.Add(value); throw primary; });
        } catch (Exception error) { seen = error; }
        var aggregate = seen as AggregateException;
        if (aggregate == null || aggregate.InnerExceptions.Count != 2 ||
            calls.Count != 2)
            throw new Exception("Multiple cleanup errors hidden");
        seen = null;
        try {
            RootProbeGuard.ReleaseOwned(new object[] { root },
                value => true, value => 1);
        }
        catch (Exception error) { seen = error; }
        if (seen == null) throw new Exception("Outstanding RCW reference accepted");
        calls.Clear();
        seen = null;
        try {
            RootProbeGuard.ReleaseOwned(new object[] { client, root },
                value => { if (value == root) throw primary; return true; }, release);
        } catch (Exception error) { seen = error; }
        if (!Object.ReferenceEquals(seen, primary) || calls.Count != 1 ||
            calls[0] != client)
            throw new Exception("isCom error leaked remaining client");
        RootProbeGuard.ReleaseOwned(new object[] { 1, 2 }, value => false,
            value => { throw new Exception("Scalar released as RCW"); });
        RootProbeGuard.RequireRuntime(new int[] { 1 });
        RootProbeGuard.RequireRuntime(new int[64]);
        foreach (int[] invalid in new int[][] { null, new int[0], new int[65] }) {
            seen = null;
            try { RootProbeGuard.RequireRuntime(invalid); }
            catch (Exception error) { seen = error; }
            if (seen == null) throw new Exception("Unbounded runtime accepted");
        }
        // Exercise actual restore policy without invoking user32 or a desktop.
        foreach (string fault in new string[] {
            "", "previous", "set-throws", "set-zero", "get-throws",
            "equal-throws", "equal-false"
        }) {
            var phases = new List<string>();
            int sets = 0, gets = 0, equals = 0;
            Exception restoreFailure = null;
            IntPtr previous = fault == "previous" ? IntPtr.Zero : new IntPtr(17);
            try {
                RootProbeGuard.RestoreDpiWithCalls(previous,
                    value => {
                        sets++;
                        if (value != previous) throw new Exception("Token changed");
                        if (fault == "set-throws") throw primary;
                        return fault == "set-zero" ? IntPtr.Zero : new IntPtr(-4);
                    },
                    () => {
                        gets++;
                        if (fault == "get-throws") throw primary;
                        return new IntPtr(23);
                    },
                    (current, saved) => {
                        equals++;
                        if (current != new IntPtr(23) || saved != previous)
                            throw new Exception("Comparison arguments changed");
                        if (fault == "equal-throws") throw primary;
                        return fault != "equal-false";
                    }, phases.Add);
            } catch (Exception error) { restoreFailure = error; }
            if ((restoreFailure == null) != (fault == ""))
                throw new Exception("Restoration failure became success");
            if (fault.EndsWith("-throws") &&
                !Object.ReferenceEquals(primary, restoreFailure))
                throw new Exception("Restoration exception replaced");
            int expectedSets = fault == "previous" ? 0 : 1;
            int expectedGets = fault == "previous" || fault.StartsWith("set-") ? 0 : 1;
            int expectedEquals = expectedGets == 0 || fault == "get-throws" ? 0 : 1;
            if (sets != expectedSets || gets != expectedGets ||
                equals != expectedEquals)
                throw new Exception("Restoration retried or continued after failure");
            var expected = new List<string> { "probe_restore_previous" };
            if (expectedSets != 0) expected.Add("probe_restore_set");
            if (expectedGets != 0) expected.Add("probe_restore_set_succeeded");
            if (expectedEquals != 0) expected.Add("probe_restore_compare");
            if (fault == "") expected.Add("probe_restore_verified");
            if (String.Join(",", phases) != String.Join(",", expected))
                throw new Exception("Restoration markers lost failure boundary");
        }
        RootProbeGuard.RequireMta();
        RootProbeGuard.RequireNativeOnlyProcess();
        // Creating this holder and releasing its empty ownership cannot activate COM.
        var session = new RootProbeSession();
        session.Release();
        seen = null;
        try { session.Release(); } catch (Exception error) { seen = error; }
        if (seen == null) throw new Exception("Release retried");
        if (RootProbeTiming.Finish(null).HasValue)
            throw new Exception("Missing time invented");
        RootProbeTiming.RootRecord("invalid", 1);
        RootProbeTiming.RootRecord(new string('a',32), null);
    }
}
'@
$testFile=Join-Path (Split-Path -Parent $PSCommandPath) 'pure-root-tests.cs'
[IO.File]::WriteAllText($testFile,$tests)
Add-Type -Path @((Join-Path $Source 'uia_nofocus_interop.cs'),
    $NativeSource, (Join-Path $Source 'uia_desktop.cs'), $testFile)
[PureRootProbeTests]::Run()
Add-Type -AssemblyName UIAutomationTypes
$refused=$false
try { [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess() }
catch { $refused=$true }
if (-not $refused) { throw 'Managed UIA assembly was accepted in native process' }
[Console]::WriteLine('release-policy-passed')
"""
    assert (
        run_windows_script(tmp_path, "release-policy.ps1", script)
        == "release-policy-passed"
    )


@WINDOWS
def test_actual_operation_with_inert_clients_proves_cleanup_and_receipt(
    tmp_path: Path,
) -> None:
    """Run the real operation over fake types, including combined cleanup failures."""
    script = (
        AST_FUNCTIONS
        + r"""
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Threading;
public static class ProbeFake {
    public static string Fault;
    public static List<string> Trace = new List<string>();
    public static int OwnedChecks, NativeChecks, PhysicalChecks, ForegroundChecks;
    public static void Reset(string fault) {
        Fault=fault; Trace.Clear();
        OwnedChecks=NativeChecks=PhysicalChecks=ForegroundChecks=0;
    }
    public static void Hit(string stage) {
        Trace.Add(stage);
        if (Array.IndexOf(Fault.Split(','),stage) >= 0)
            throw new InvalidOperationException("private-provider-text:"+stage);
    }
}
public static class CaptureDesktop {
    public static IntPtr SetThreadDpiAwarenessContext(IntPtr context) {
        ProbeFake.Hit("dpi-enter");
        if (context.ToInt64() != -4) throw new Exception("Wrong physical context");
        return ProbeFake.Fault == "dpi-zero" ? IntPtr.Zero : new IntPtr(-2);
    }
}
namespace VerdiClip.Automation {
public static class RootProbeGuard {
    public static void RequireMta() {
        if (Thread.CurrentThread.GetApartmentState() != ApartmentState.MTA)
            throw new Exception("Wrong actual calling apartment");
    }
    public static void RequireNativeOnlyProcess() {
        ProbeFake.Hit("native-"+(++ProbeFake.NativeChecks));
    }
    public static void RequireForeground(long expected) {
        if (expected != 200) throw new Exception("Wrong foreground identity");
        ProbeFake.Hit("foreground-"+(++ProbeFake.ForegroundChecks));
    }
    public static void RequireOwnedWindow(long handle,int pid,long ticks,long fg) {
        if (handle != 100 || pid != 42 || ticks != 638000000000000000 || fg != 200)
            throw new Exception("Identity conversion changed");
        ProbeFake.Hit("owned-"+(++ProbeFake.OwnedChecks));
    }
    public static void RequirePhysicalPixels() {
        ProbeFake.Hit("physical-"+(++ProbeFake.PhysicalChecks));
    }
    public static void RestoreDpi(IntPtr prior) {
        if (prior.ToInt64() != -2) throw new Exception("Wrong restoration context");
        ProbeFake.Hit("restore");
    }
    public static void RequireRuntime(int[] value) {
        if (value == null || value.Length == 0 || value.Length > 64)
            throw new Exception("private-runtime-error");
    }
}
public static class RootProbeTiming {
    public static long? Start() { return 0; }
    public static long? Finish(long? value) { return value; }
    public static void RootRecord(string id,long? duration) { }
    public static void Phase(string phase) { ProbeFake.Trace.Add(phase); }
}
public sealed class RootProbeSession {
    public long? ConstructUs,RootUs,ValidateUs,ReleaseUs;
    public void Construct() { ProbeFake.Hit("construct"); ConstructUs=0; }
    public void Acquire(long handle,string id) {
        ProbeFake.Hit("acquire"); RootUs=0;
    }
    public void Validate(int pid,long handle) {
        ProbeFake.Hit("validate"); ValidateUs=0;
    }
    public void Release() { ProbeFake.Hit("release"); ReleaseUs=0; }
}
}
namespace System.Windows.Automation {
public struct ProbeCurrent {
    public int ProcessId, NativeWindowHandle;
}
public sealed class AutomationElement {
    public static AutomationElement FromHandle(IntPtr handle) {
        ProbeFake.Hit("acquire"); return new AutomationElement();
    }
    public ProbeCurrent Current {
        get {
            ProbeFake.Hit("validate");
            return new ProbeCurrent { ProcessId=42, NativeWindowHandle=100 };
        }
    }
    public int[] GetRuntimeId() { ProbeFake.Hit("runtime"); return new int[] { 1 }; }
}
}
'@
$function = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Invoke-RootProbe'
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
$json='{"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","action":"inspect",' +
    '"payload":{"window_handle":100,"process_id":42,' +
    '"started_ticks":638000000000000000,"foreground_handle":200}}'
$request=$json | ConvertFrom-Json
$runtime=@{powershell='5.1.0.0';clr='4.0.30319.42000';architecture='x64'}
$count=0
foreach ($Client in @('managed','native')) {
    $faults=@('', 'dpi-enter', 'dpi-zero', 'physical-1', 'owned-1', 'acquire',
        'validate', 'owned-2', 'physical-2', 'restore', 'owned-3',
        'acquire,restore','foreground-1','foreground-2','foreground-1,restore',
        'foreground-1,foreground-2,restore')
    if ($Client -ceq 'native') {
        $faults += @('native-1','native-2','native-3','construct','release',
            'construct,release','validate,release,restore','foreground-3',
            'release,foreground-3,restore')
    }
    if ($Client -ceq 'managed') { $faults += @('runtime') }
    foreach ($fault in $faults) {
        [ProbeFake]::Reset($fault); $result=$null; $failure=$null
        try { $result=Invoke-RootProbe $request $runtime }
        catch { $failure=$_.Exception }
        $count++
        if ($fault -ceq '') {
            if ($null -ne $failure) { throw $failure }
            $keys=@('client','apartment','identity_verified','foreground_unchanged',
                'native_only_verified','release_confirmed','dpi_restored',
                'release_kind','durations_us','runtime')
            if ($result.Count -ne $keys.Count) { throw 'Unexpected result fields' }
            foreach ($key in $keys) {
                if (-not $result.ContainsKey($key)) { throw 'Missing result field' }
            }
            foreach ($key in @('identity_verified','foreground_unchanged',
                'release_confirmed','dpi_restored')) {
                if ($result[$key] -isnot [bool] -or -not $result[$key]) {
                    throw 'Unverified result flag'
                }
            }
            $native=$Client -ceq 'native'
            if ($result.client -cne $Client -or $result.apartment -cne 'MTA' -or
                $result.native_only_verified -isnot [bool] -or
                $result.native_only_verified -ne $native) { throw 'Wrong client' }
            $kind=if($native){'native_rcw_release_completed'}else{
                'managed_reference_dropped'
            }
            if ($result.release_kind -cne $kind) { throw 'Ambiguous release' }
            if ($result.runtime.Count -ne 3 -or
                $result.durations_us.Count -ne 4) { throw 'Wrong nested schema' }
            if (-not $native -and $null -ne $result.durations_us.construct) {
                throw 'Managed constructor duration invented'
            }
            if ([ProbeFake]::OwnedChecks -ne 3) { throw 'Missing ownership guard' }
            $trace=[ProbeFake]::Trace
            $expectedForeground=if($native){3}else{2}
            if ([ProbeFake]::ForegroundChecks -ne $expectedForeground) {
                throw 'Missing foreground boundary'
            }
            $last='foreground-'+$expectedForeground
            if ($trace.IndexOf($last) -gt $trace.IndexOf('restore')) {
                throw 'Release foreground check came after DPI restoration'
            }
            if ($native -and ($trace.IndexOf('construct') -gt
                $trace.IndexOf('foreground-1') -or
                $trace.IndexOf('foreground-1') -gt $trace.IndexOf('acquire'))) {
                throw 'Construction foreground boundary missing'
            }
            $rootBoundary=if($native){'foreground-2'}else{'foreground-1'}
            if ($trace.IndexOf('acquire') -gt $trace.IndexOf($rootBoundary) -or
                $trace.IndexOf($rootBoundary) -gt $trace.IndexOf('validate')) {
                throw 'Root foreground boundary missing'
            }
            if ($trace.IndexOf('restore') -gt $trace.IndexOf('owned-3')) {
                throw 'Result preceded cleanup'
            }
            if ($native -and $trace.IndexOf('release') -gt
                $trace.IndexOf('restore')) { throw 'Wrong cleanup order' }
        } else {
            if ($null -eq $failure -or $null -ne $result) {
                throw 'Failure returned success'
            }
            if ($fault -notin @('dpi-enter','dpi-zero','native-1') -and
                -not [ProbeFake]::Trace.Contains('restore')) {
                throw 'DPI cleanup missing after error'
            }
            if ($fault.Contains(',')) {
                $aggregate=$failure
                while ($null -ne $aggregate -and
                    $aggregate -isnot [AggregateException]) {
                    $aggregate=$aggregate.InnerException
                }
                if ($null -eq $aggregate -or
                    $aggregate.Flatten().InnerExceptions.Count -ne
                    $fault.Split(',').Length) {
                    throw 'Original exception objects were not retained'
                }
                $text=$failure.ToString()
                foreach ($stage in $fault.Split(',')) {
                    if (-not $text.Contains('private-provider-text:'+$stage)) {
                        throw 'Primary or cleanup exception was lost'
                    }
                }
            }
        }
        if ($fault -ceq 'validate,release,restore') {
            $diagnostics=@([ProbeFake]::Trace | Where-Object {
                $_ -like 'probe_*failed*'
            })
            $expected='probe_operation_failed,probe_cleanup_failed_1,' +
                'probe_cleanup_failed_3'
            if (($diagnostics -join ',') -cne $expected) {
                throw 'Primary and cleanup failures were not separately retained'
            }
        }
        $phaseMap=@{
            'owned-2'='probe_owned_2'; 'physical-2'='probe_physical_2'
            'restore'='probe_restore_1'; 'owned-3'='probe_owned_3'
            'runtime'='probe_runtime_1'; 'release'='probe_release_native_1'
        }
        foreach ($faultStage in $fault.Split(',')) {
            if ($phaseMap.ContainsKey($faultStage)) {
                $index=[ProbeFake]::Trace.IndexOf($faultStage)
                if ($index -lt 1 -or
                    [ProbeFake]::Trace[$index-1] -cne $phaseMap[$faultStage]) {
                    throw 'Failure stage diagnostic missing or incorrect'
                }
            }
        }
        if (@([ProbeFake]::Trace | Where-Object { $_ -ceq 'release' }).Count -gt 1) {
            throw 'Release retried'
        }
    }
}
if ($count -ne 42) { throw 'Operation cases were not all executed' }
[Console]::WriteLine('operation-policy-passed')
"""
    )
    assert (
        run_windows_script(tmp_path, "operation-policy.ps1", script)
        == "operation-policy-passed"
    )


def test_probe_trace_is_scoped_bounded_and_discards_unknown_text() -> None:
    """Extra vocabulary applies only to the experiment and survives fragmentation."""
    production = WorkerTrace(lambda: 1.0)
    probe = RootProbeTrace(lambda: 1.0)
    expected = [
        "probe_runtime_1",
        "probe_operation_failed",
        "probe_release_managed_1",
        "probe_foreground_4",
        "probe_cleanup_failed_2",
        "probe_restore_1",
        "probe_cleanup_failed_3",
    ]
    data = (
        b"private-provider-text\n"
        + b"".join(f"VERDICLIP_UIA_PHASE={stage}\r\n".encode() for stage in expected)
        + b"VERDICLIP_UIA_PHASE=probe_private_value\n"
    )
    for byte in data:
        probe_accepted = probe.error_bytes(bytes([byte]))
        production_accepted = production.error_bytes(bytes([byte]))
        assert probe_accepted
        assert production_accepted
    assert [event.stage for event in probe.events] == expected
    assert not production.events
    assert probe.dropped_events == 0
    assert RootProbeTrace.MARKERS is not WorkerTrace.MARKERS
    for _ in range(70):
        probe.error_bytes(b"VERDICLIP_UIA_PHASE=probe_restore_1\n")
    assert len(probe.events) == 64
    assert probe.dropped_events == 13
    overflow_accepted = probe.error_bytes(b"x" * WorkerTrace.MAX_ERROR_BYTES)
    assert not overflow_accepted
