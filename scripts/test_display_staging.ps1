#Requires -Version 7.0
# One specifically authorized hosted staging-only attempt. Never create a device.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'display_staging_policy.ps1')
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or $env:GITHUB_RUN_ATTEMPT -ne '1') {
    throw 'Staging requires one explicit standard hosted Windows x64 attempt.'
}
$event = Get-Content -Raw -LiteralPath $env:GITHUB_EVENT_PATH | ConvertFrom-Json
if ($event.repository.private -ne $false -or
    [string]$event.inputs.driver_store_staging -ne 'true' -or
    [string]$event.inputs.driver_signature_preflight -eq 'true' -or
    [string]$event.inputs.desktop_capability_only -eq 'true' -or
    [string]$event.inputs.owned_window_canary -eq 'true') { throw 'Exclusive public staging dispatch required.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Already elevated administrator token required; no elevation attempted.'
}
$clock = [Diagnostics.Stopwatch]::StartNew()
$root = Join-Path $env:RUNNER_TEMP ('display-stage-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$hostPath = (Get-Process -Id $PID).Path
$pnp = Join-Path $env:SystemRoot 'System32/pnputil.exe'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class StagingWindowGuard {
    delegate bool Callback(IntPtr h,IntPtr p);
    [DllImport("user32.dll",SetLastError=true)] static extern bool EnumWindows(Callback cb,IntPtr p);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    public static string Read() {
        var rows=new List<string>(); Exception error=null;
        Callback cb=(h,p)=>{try {
            if(IsWindowVisible(h)) {
                if(rows.Count>=1024)throw new Exception("Visible window bound");
                uint pid;if(GetWindowThreadProcessId(h,out pid)==0)throw new Win32Exception();
                rows.Add(h.ToInt64()+":"+pid);
            } return true;
        }catch(Exception e){error=e;return false;}};
        bool ok=EnumWindows(cb,IntPtr.Zero);GC.KeepAlive(cb);
        if(error!=null)throw error;if(!ok)throw new Win32Exception();
        rows.Sort(StringComparer.Ordinal);return GetForegroundWindow().ToInt64()+"|"+string.Join("|",rows);
    }
}
'@
$windowBaseline = [StagingWindowGuard]::Read()
$journal = [Collections.Generic.List[object]]::new()
$deadline = 180000L
function Invoke-StagingProcess([string]$Label, [string]$Executable, [string[]]$Arguments, [int]$Limit) {
    $budget = Get-StagingBudget $clock.ElapsedMilliseconds $deadline $Limit
    if ($budget -le 5000) { throw 'Insufficient remaining child cleanup budget.' }
    $operationDeadline = [Math]::Min($deadline,$clock.ElapsedMilliseconds+$budget)
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    foreach ($arg in $Arguments) { $info.ArgumentList.Add($arg) }
    $child = [Diagnostics.Process]::new(); $child.StartInfo = $info
    $started = $false; $stopped = $false
    try {
        $started = $child.Start()
        if (-not $started) { throw 'Owned child did not start.' }
        if ($Label -eq 'one-staging-attempt') { $script:attempted = $true }
        $child.StandardInput.Close() # never answer a publisher/UAC/console prompt
        $stdout = $child.StandardOutput.ReadToEndAsync(); $stderr = $child.StandardError.ReadToEndAsync()
        $waitEnd = $operationDeadline - 5000
        while (-not $child.HasExited) {
            if ([StagingWindowGuard]::Read() -cne $windowBaseline) { throw 'Visible window/foreground changed; possible prompt.' }
            if ($child.MainWindowHandle -ne [IntPtr]::Zero) { throw 'Owned process exposed a window/prompt.' }
            $remaining = $waitEnd - $clock.ElapsedMilliseconds
            if ($remaining -le 0) { throw ('Owned process deadline: ' + $Label) }
            $child.WaitForExit([int][Math]::Min(200,$remaining)) | Out-Null
        }
        $stopped = $true
        $observedWindows = [StagingWindowGuard]::Read()
        Assert-StagingCompletion $clock.ElapsedMilliseconds $operationDeadline $windowBaseline $observedWindows
        if (-not $stdout.Wait(500) -or -not $stderr.Wait(500)) { throw 'Owned output drain incomplete.' }
        $output = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        if ($output.Length -gt 8388608) { throw 'Owned output size bound exceeded.' }
        $journal.Add(@{phase=$Label;exit=$child.ExitCode;output_chars=$output.Length})
        if ($child.ExitCode -ne 0) {
            $tail = $output.Substring([Math]::Max(0,$output.Length-4000)).Replace($root,'<staging>')
            throw ($Label + ' exit ' + $child.ExitCode + ': ' + $tail)
        }
        $observedWindows = [StagingWindowGuard]::Read()
        Assert-StagingCompletion $clock.ElapsedMilliseconds $operationDeadline $windowBaseline $observedWindows
        if ($Label -eq 'one-staging-attempt') { $script:stageOutcomeObserved = $true }
        return $output
    } finally {
        if ($started -and -not $child.HasExited) {
            $child.Kill($true)
            $left = [Math]::Max(0,[Math]::Min(5000,$deadline-$clock.ElapsedMilliseconds))
            $stopped = $child.WaitForExit([int]$left)
        }
        $journal.Add(@{phase=$Label;owned_process_started=$started;owned_process_exited=$stopped})
        $child.Dispose()
        if ($started -and -not $stopped) { throw 'Owned process cleanup uncertain.' }
    }
}
function Read-StagingSnapshot {
    $json = Invoke-StagingProcess 'inventory' $hostPath @('-NoProfile','-NonInteractive','-File',
        (Join-Path $PSScriptRoot 'display_staging_inventory.ps1')) 15000
    return ConvertFrom-Json -InputObject $json -Depth 20
}
function Read-StagingPackages {
    return Invoke-StagingProcess 'package-inventory' $pnp @('/enum-drivers','/files') 15000
}
function Confirm-ExportIdentity([string]$PublishedInf) {
    $exportRoot = Join-Path $root ('export-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($exportRoot) | Out-Null
    Invoke-StagingProcess 'verify-new-package-export' $pnp @('/export-driver',$PublishedInf,$exportRoot) 15000 | Out-Null
    $required = @{
        'mttvdd.inf'='550d211fe481e74dfe3f9d724ed78be48b3a9113405965d683d9373e8d672f5d'
        'mttvdd.cat'='08a0093fc9b2e32b287a6f8a77ca4de0a31830d29fc33d2b13a918dc859468f6'
        'mttvdd.dll'='c9ca837f57a98fbd43bc416a7f535a95843626e7759eaf85cf0cd7ce334dbb05'
    }
    $exported = @(Get-ChildItem -LiteralPath $exportRoot -Recurse -File)
    if ($exported.Count -gt 16) { throw 'Unexpected export size/contents.' }
    foreach ($name in $required.Keys) {
        $match = @($exported | Where-Object Name -eq $name)
        if ($match.Count -ne 1 -or $match[0].Length -gt 67108864 -or
            (Get-FileHash -LiteralPath $match[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $required[$name]) {
            throw 'Export does not match the exact approved package.'
        }
    }
    return $true
}
$before = $null; $beforePackages = $null; $attempted = $false; $ownedPackage = $null
$stageOutcomeObserved = $false
$admitted = $false; $cleanupVerified = $false; $failure = $null; $cleanupFailure = $null
$preflightReport = $null; $final = $null; $packageRemoved = $false
try {
    $before = Read-StagingSnapshot
    Invoke-StagingProcess 'verify-existing-pnputil' $hostPath @('-NoProfile','-NonInteractive','-File',
        (Join-Path $PSScriptRoot 'display_staging_inventory.ps1'),'-VerifyPnPTool') 15000 | Out-Null
    $stateFile = Join-Path $root 'preflight.json'
    Invoke-StagingProcess 'repeat-signature-preflight' $hostPath @('-NoProfile','-NonInteractive','-File',
        (Join-Path $PSScriptRoot 'inspect_display_package.ps1'),'-StateFile',$stateFile) 90000 | Out-Null
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json -Depth 20
    $preflightReport = $state.report
    $authChecks = @($preflightReport.verification_results | Where-Object check -match 'authenticode$')
    if (-not $preflightReport.pinned_hash_matched -or -not $preflightReport.verification_completed -or
        $preflightReport.verification_results.Count -ne 6 -or $authChecks.Count -ne 3 -or
        @($authChecks | Where-Object verdict -ne 'verified').Count -ne 0 -or
        @($preflightReport.verification_results | Where-Object verdict -notin @('verified','rejected')).Count -ne 0) {
        throw 'Repeated signature preflight did not satisfy the staging policy.'
    }
    $dataRoot = [IO.Path]::GetFullPath([string]$state.data_root)
    $tempPrefix = [IO.Path]::GetFullPath($env:RUNNER_TEMP) + [IO.Path]::DirectorySeparatorChar
    if (-not $dataRoot.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Preflight data escaped runner temp.' }
    $inf = Join-Path $dataRoot 'VirtualDisplayDriver/MttVDD.inf'
    if ((Get-FileHash -LiteralPath $inf -Algorithm SHA256).Hash.ToLowerInvariant() -ne
        '550d211fe481e74dfe3f9d724ed78be48b3a9113405965d683d9373e8d672f5d') { throw 'Pinned INF mismatch.' }
    $model = Get-StagingInfIdentity (Get-Content -Raw -LiteralPath $inf)
    $beforePackages = Read-StagingPackages
    if ($beforePackages -match '(?i)mttvdd' -or
        $beforePackages.IndexOf($model.provider,[StringComparison]::OrdinalIgnoreCase) -ge 0) { throw 'Preexisting package/provider.' }
    $immediate = Read-StagingSnapshot
    Assert-StagingSnapshotEqual $before $immediate
    Assert-NoStagingMatch @($immediate.devices) $model.ids
    Invoke-StagingProcess 'one-staging-attempt' $pnp @('/add-driver',$inf) 30000 | Out-Null
    $afterPackages = Read-StagingPackages
    $ownedPackage = Get-NewStagedPackage (Get-PublishedPackages $beforePackages) (Get-PublishedPackages $afterPackages)
    Confirm-ExportIdentity $ownedPackage | Out-Null
    $after = Read-StagingSnapshot
    Assert-StagingSnapshotEqual $before $after
    if ($clock.ElapsedMilliseconds -ge $deadline) { throw 'Normal-path deadline expired before admission acceptance.' }
    $admitted = $true
} catch { $failure = $_.Exception.Message.Replace($root,'<staging>') }
finally {
    $deadline = 270000L
    try {
        if ($attempted) {
            # Reidentify even after a failed/timed-out add. Never delete by output alone.
            $currentPackages = Read-StagingPackages
            $ownedPackage = Get-NewStagedPackage (Get-PublishedPackages $beforePackages) (Get-PublishedPackages $currentPackages)
            Confirm-ExportIdentity $ownedPackage | Out-Null
            $preDelete = Read-StagingSnapshot
            Assert-StagingSnapshotEqual $before $preDelete # no unexpected binding/trust/display change
            Invoke-StagingProcess 'remove-exact-owned-package' $pnp @('/delete-driver',$ownedPackage) 30000 | Out-Null
            $remainingPackages = Get-PublishedPackages (Read-StagingPackages)
            $baselinePackages = Get-PublishedPackages $beforePackages
            if (($remainingPackages -join '|') -cne ($baselinePackages -join '|')) { throw 'Final package inventory differs.' }
            $packageRemoved = $true
        }
        if ($null -ne $before) {
            $final = Read-StagingSnapshot
            Assert-StagingSnapshotEqual $before $final
            if ($clock.ElapsedMilliseconds -ge $deadline) { throw 'Cleanup deadline expired before final acceptance.' }
            $cleanupVerified = $true
        }
    } catch { $cleanupFailure = $_.Exception.Message.Replace($root,'<staging>') }
    $partialOperationUncertainty = $attempted -and -not $stageOutcomeObserved
    if ($partialOperationUncertainty) {
        $cleanupVerified = $false
        $cleanupFailure = ([string]$cleanupFailure + '; OS staging outcome remains uncertain after incomplete child result.').TrimStart(';',' ')
    }
    $success = $admitted -and $cleanupVerified -and $packageRemoved -and -not $failure -and -not $cleanupFailure
    $report = [ordered]@{
        schema=1;kind='approved-disposable-driver-store-staging-only';source_sha=$env:GITHUB_SHA
        runner_image=$env:ImageOS;image_version=$env:ImageVersion;os=[Environment]::OSVersion.Version.ToString()
        archive_sha256='e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a'
        attempted=$attempted;admitted=$admitted;owned_package=$ownedPackage;package_removed=$packageRemoved
        stage_outcome_observed=$stageOutcomeObserved;partial_operation_uncertainty=$partialOperationUncertainty
        cleanup_verified=$cleanupVerified;passed=[bool]$success;failure=$failure;cleanup_failure=$cleanupFailure
        before_snapshot=(Get-StagingSnapshotReceipt $before);final_snapshot=(Get-StagingSnapshotReceipt $final)
        signature_preflight_completed=($null -ne $preflightReport -and $preflightReport.verification_completed)
        elapsed_ms=$clock.ElapsedMilliseconds;journal=$journal.ToArray()
        device_creation_requested=$false;driver_loading_requested=$false;display_changes_requested=$false
        trust_changes_requested=$false;prompts_answered=$false;reboot_requested=$false;artifacts_uploaded=$false
        runtime_verified=$false;multi_monitor_verified=$false;product_driver_dependency=$false
        visible_window_poll_ms=200;unobserved_transient_activity_excluded=$false
    }
    $json = $report | ConvertTo-Json -Depth 12
    Write-Output $json
    @('### Staging-only admission result','','```json',$json,'```',
        'No runtime, multi-monitor or product-adoption qualification. VM disposal is not cleanup proof.') -join "`n" |
        Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
}
if (-not $success) { exit 1 }
