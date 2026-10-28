#Requires -Version 7.0
# One specifically authorized hosted staging-only attempt. Never create a device.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'display_staging_policy.ps1')
. (Join-Path $PSScriptRoot 'display_staging_diagnostics.ps1')
. (Join-Path $PSScriptRoot 'publisher_trust_policy.ps1')
. (Join-Path $PSScriptRoot 'publisher_trust_operations.ps1')
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or $env:GITHUB_RUN_ATTEMPT -ne '1') {
    throw 'Staging requires one explicit standard hosted Windows x64 attempt.'
}
$event = Get-Content -Raw -LiteralPath $env:GITHUB_EVENT_PATH | ConvertFrom-Json
$publisherTrustRequested = $event.inputs.PSObject.Properties.Name -contains 'publisher_trust_staging' -and
    [string]$event.inputs.publisher_trust_staging -eq 'true'
if ($event.repository.private -ne $false -or
    [string]$event.inputs.driver_store_staging -ne 'true' -or
    [string]$event.inputs.driver_signature_preflight -eq 'true' -or
    [string]$event.inputs.desktop_capability_only -eq 'true' -or
    [string]$event.inputs.owned_window_canary -eq 'true' -or
    [string]$event.inputs.rdp_prerequisites_only -eq 'true') { throw 'Exclusive public staging dispatch required.' }
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
using System.Text;
public static class StagingWindowGuard {
    delegate bool Callback(IntPtr h,IntPtr p);
    [DllImport("user32.dll",SetLastError=true)] static extern bool EnumWindows(Callback cb,IntPtr p);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)]
    static extern int GetClassNameW(IntPtr h,StringBuilder name,int count);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]
    static extern int GetWindowTextW(IntPtr h,StringBuilder text,int count);
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
    struct Entry { public uint Size,Usage,Pid; public UIntPtr Heap; public uint Module,Threads,Parent;
        public int Priority; public uint Flags; [MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)] public string Exe; }
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)]
    static extern bool Process32FirstW(IntPtr snapshot,ref Entry entry);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)]
    static extern bool Process32NextW(IntPtr snapshot,ref Entry entry);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [StructLayout(LayoutKind.Sequential)]
    struct FileInfo { public uint Attributes,CreationLow,CreationHigh,AccessLow,AccessHigh,WriteLow,WriteHigh,
        Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow; }
    [DllImport("kernel32.dll",SetLastError=true)]
    static extern bool GetFileInformationByHandle(IntPtr handle,out FileInfo info);
    public static string FileIdentity(IntPtr handle) {
        FileInfo info;if(!GetFileInformationByHandle(handle,out info))throw new Win32Exception();
        return info.Volume.ToString("x8")+":"+info.IndexHigh.ToString("x8")+info.IndexLow.ToString("x8")+
            ":"+info.CreationHigh.ToString("x8")+info.CreationLow.ToString("x8");
    }
    public static string Caption(string value,uint expectedPid) {
        if(expectedPid==(uint)Environment.ProcessId)throw new Exception("Foreign caption only");
        IntPtr h=new IntPtr(unchecked((long)Convert.ToUInt64(value.Substring(2),16)));
        uint pid;if(GetWindowThreadProcessId(h,out pid)==0 || pid!=expectedPid)throw new Exception("Window identity changed");
        var name=new StringBuilder(257);
        if(GetClassNameW(h,name,257)==0 || name.ToString()!="#32770")throw new Exception("Dialog class changed");
        // Foreign caption only: no WM_GETTEXT, UIA, input, control invocation or secure-desktop access.
        var text=new StringBuilder(513);int n=GetWindowTextW(h,text,513);
        uint after;if(GetWindowThreadProcessId(h,out after)==0 || after!=expectedPid)throw new Exception("Window identity changed");
        if(n>=512)throw new Exception("Caption may be truncated");return text.ToString();
    }
    public static uint Parent(uint targetPid) {
        IntPtr snapshot=CreateToolhelp32Snapshot(2,0);
        if(snapshot==new IntPtr(-1))throw new Win32Exception();
        try {
            var e=new Entry{Size=(uint)Marshal.SizeOf(typeof(Entry))};
            var clock=System.Diagnostics.Stopwatch.StartNew();
            bool found=Process32FirstW(snapshot,ref e);int count=0;
            while(found) {
                if(++count>4096 || clock.ElapsedMilliseconds>=500)throw new Exception("Process snapshot bound");
                if(e.Pid==targetPid)return e.Parent;
                found=Process32NextW(snapshot,ref e);
            }
            throw new Exception("Process absent from snapshot");
        } finally { if(!CloseHandle(snapshot))throw new Win32Exception(); }
    }
    static string Handle(IntPtr h) { return "0x"+unchecked((ulong)h.ToInt64()).ToString("x"); }
    public static object Read() {
        var rows=new List<object>(); Exception error=null;
        Callback cb=(h,p)=>{try {
            if(IsWindowVisible(h)) {
                if(rows.Count>=1024)throw new Exception("Visible window bound");
                uint pid;if(GetWindowThreadProcessId(h,out pid)==0)throw new Win32Exception();
                var name=new StringBuilder(257);
                if(GetClassNameW(h,name,257)==0)throw new Win32Exception();
                rows.Add(new {handle=Handle(h),pid=pid,@class=name.ToString()});
            } return true;
        }catch(Exception e){error=e;return false;}};
        bool ok=EnumWindows(cb,IntPtr.Zero);GC.KeepAlive(cb);
        if(error!=null)throw error;if(!ok)throw new Win32Exception();
        return new {foreground=Handle(GetForegroundWindow()),windows=rows.ToArray()};
    }
}
'@
$windowBaseline = [StagingWindowGuard]::Read()
$journal = [Collections.Generic.List[object]]::new()
$guardEverTripped = $false; $admissionEverFailed = $false
$witnessCount = 0; $lastWitnessKey = $null
$dialogDiagnosticsTaken = $false
$stageChildIdentity = $null; $setupLogOffsets = @{}
$deadline = 180000L
function Read-StagingDialog([string]$Handle,[long]$WindowPid,[long]$WorkerPid) {
    $process = $null; $worker = $null
    try {
        $process = Get-Process -Id $WindowPid -ErrorAction Stop
        $worker = Get-Process -Id $WorkerPid -ErrorAction Stop
        $created=$process.StartTime.ToUniversalTime().Ticks
        $workerCreated=$worker.StartTime.ToUniversalTime().Ticks
        $parentPid=[StagingWindowGuard]::Parent([uint32]$WindowPid)
        $caption=[StagingWindowGuard]::Caption($Handle,[uint32]$WindowPid)
        # PID reuse/exits invalidate correlation. Names are classified, never emitted verbatim.
        if ($process.HasExited -or $worker.HasExited -or $null -eq $stageChildIdentity -or
            $workerCreated -ne $stageChildIdentity.created_utc_ticks) { throw 'Process generation changed.' }
        $knownName=$process.ProcessName.ToLowerInvariant()
        if ($knownName -notin @('pnputil','rundll32','drvinst','consent','dllhost','svchost')) { $knownName='other' }
        return [ordered]@{window_pid=$WindowPid;process_created_utc_ticks=$created;process_category=$knownName;
            parent_pid=$parentPid;worker_pid=$WorkerPid;worker_created_utc_ticks=$workerCreated;
            same_process=($WindowPid -eq $WorkerPid -and $created -eq $workerCreated);
            direct_child_observed=($parentPid -eq $WorkerPid -and $created -ge $workerCreated);
            caption=(Get-StagingCaptionClassification $caption);failure=$null}
    } catch { return [ordered]@{window_pid=$WindowPid;worker_pid=$WorkerPid;failure='Dialog identity/caption observation unavailable.'} }
    finally { if($null -ne $process){$process.Dispose()};if($null -ne $worker){$worker.Dispose()} }
}
function Assert-StagingWindows([string]$Label, [switch]$ObservationOnly, [long]$WorkerPid=0) {
    $frame = [StagingWindowGuard]::Read()
    $witness = Get-StagingWindowWitness $windowBaseline $frame
    if ($witness.changed) {
        $script:guardEverTripped = $true
        $key = ConvertTo-Json $witness -Depth 8 -Compress
        if ($script:witnessCount -lt 16 -and $key -cne $script:lastWitnessKey) {
            $journal.Add(@{phase=$Label;elapsed_ms=$clock.ElapsedMilliseconds;window_witness=$witness})
            $script:witnessCount++; $script:lastWitnessKey = $key
        }
        if ($Label -eq 'one-staging-attempt' -and $WorkerPid -gt 0 -and -not $script:dialogDiagnosticsTaken) {
            $script:dialogDiagnosticsTaken = $true
            $dialog=@($frame.windows | Where-Object { $_.handle -eq $frame.foreground -and $_.class -ceq '#32770' })
            if ($dialog.Count -eq 1 -and $clock.ElapsedMilliseconds -lt $deadline-5000) {
                $journal.Add(@{phase='dialog-diagnostic';elapsed_ms=$clock.ElapsedMilliseconds;
                    observation=(Read-StagingDialog $dialog[0].handle $dialog[0].pid $WorkerPid)})
            }
        }
        if ($WorkerPid -gt 0 -and @($frame.windows | Where-Object pid -eq $WorkerPid).Count) {
            throw 'Observation worker exposed a visible window; collection stopped.'
        }
        if (-not $ObservationOnly) { throw 'Visible window/foreground changed; possible prompt.' }
    }
}
function Invoke-StagingProcess([string]$Label, [string]$Executable, [string[]]$Arguments, [int]$Limit,
    [switch]$ObservationOnly) {
    if ($ObservationOnly) {
        Assert-StagingObservationCommand $Label $Executable $Arguments $hostPath $pnp `
            (Join-Path $PSScriptRoot 'display_staging_inventory.ps1')
    }
    $budget = Get-StagingBudget $clock.ElapsedMilliseconds $deadline $Limit
    if ($budget -le 5000) { throw 'Insufficient remaining child cleanup budget.' }
    $operationDeadline = [Math]::Min($deadline,$clock.ElapsedMilliseconds+$budget)
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    foreach ($arg in $Arguments) { $info.ArgumentList.Add($arg) }
    $child = [Diagnostics.Process]::new(); $child.StartInfo = $info
    $started = $false; $stopped = $false; $stdout = $null; $stderr = $null
    $operationFailure = $null; $killed = $false; $exitCode = $null
    try {
        Assert-StagingWindows $Label -ObservationOnly:$ObservationOnly
        if (-not $ObservationOnly -and ($guardEverTripped -or $admissionEverFailed)) {
            throw 'Prior failure forbids further guarded operations.'
        }
        $started = $child.Start()
        if (-not $started) { throw 'Owned child did not start.' }
        if ($Label -eq 'one-staging-attempt') {
            $script:attempted = $true
            $script:stageChildIdentity=@{pid=$child.Id;created_utc_ticks=$child.StartTime.ToUniversalTime().Ticks}
        }
        $child.StandardInput.Close() # never answer a publisher/UAC/console prompt
        $stdout = $child.StandardOutput.ReadToEndAsync(); $stderr = $child.StandardError.ReadToEndAsync()
        $waitEnd = $operationDeadline - 5000
        while (-not $child.HasExited) {
            Assert-StagingWindows $Label -ObservationOnly:$ObservationOnly -WorkerPid $child.Id
            if ($child.MainWindowHandle -ne [IntPtr]::Zero) { throw 'Owned process exposed a window/prompt.' }
            $remaining = $waitEnd - $clock.ElapsedMilliseconds
            if ($remaining -le 0) { throw ('Owned process deadline: ' + $Label) }
            $child.WaitForExit([int][Math]::Min(200,$remaining)) | Out-Null
        }
        $stopped = $true
        $exitCode = $child.ExitCode
        Assert-StagingWindows $Label -ObservationOnly:$ObservationOnly -WorkerPid $child.Id
        Assert-StagingDeadline $clock.ElapsedMilliseconds $operationDeadline
        if (-not $stdout.Wait(500) -or -not $stderr.Wait(500)) { throw 'Owned output drain incomplete.' }
        $output = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        if ($output.Length -gt 8388608) { throw 'Owned output size bound exceeded.' }
        $journal.Add(@{phase=$Label;exit=$child.ExitCode;output_chars=$output.Length})
        if ($child.ExitCode -ne 0) {
            $tail = $output.Substring([Math]::Max(0,$output.Length-4000)).Replace($root,'<staging>')
            throw ($Label + ' exit ' + $child.ExitCode + ': ' + $tail)
        }
        Assert-StagingWindows $Label -ObservationOnly:$ObservationOnly -WorkerPid $child.Id
        Assert-StagingDeadline $clock.ElapsedMilliseconds $operationDeadline
        if ($Label -eq 'one-staging-attempt') { $script:stageOutcomeObserved = $true }
        return $output
    } catch {
        $operationFailure = $_.Exception.Message.Replace($root,'<staging>')
        throw
    } finally {
        if ($started -and -not $child.HasExited) {
            $child.Kill($true)
            $killed = $true
            $left = [Math]::Max(0,[Math]::Min(5000,$deadline-$clock.ElapsedMilliseconds))
            $stopped = $child.WaitForExit([int]$left)
        }
        if ($started -and $child.HasExited) { $stopped=$true; $exitCode=$child.ExitCode }
        $tails = [ordered]@{stdout=$null;stderr=$null}
        foreach ($stream in @(@{name='stdout';task=$stdout},@{name='stderr';task=$stderr})) {
            # Never wait in the exception path. Completed output only, bounded and redacted.
            if ($null -ne $stream.task -and $stream.task.IsCompletedSuccessfully) {
                $value=$stream.task.GetAwaiter().GetResult()
                $tails[$stream.name]=$value.Substring([Math]::Max(0,$value.Length-4000)).Replace($root,'<staging>')
            }
        }
        $journal.Add(@{phase=$Label;owned_process_started=$started;owned_process_exited=$stopped;
            child_pid=$(if($started){$child.Id}else{$null});
            killed=$killed;exit=$exitCode;failure=$operationFailure;elapsed_ms=$clock.ElapsedMilliseconds;
            stdout_drained=($null -ne $stdout -and $stdout.IsCompletedSuccessfully);
            stderr_drained=($null -ne $stderr -and $stderr.IsCompletedSuccessfully);
            failed_output_tails=$(if($operationFailure){$tails}else{$null})})
        $child.Dispose()
        if ($started -and -not $stopped) { throw 'Owned process cleanup uncertain.' }
    }
}
function Read-StagingSnapshot([switch]$ObservationOnly) {
    $json = Invoke-StagingProcess 'inventory' $hostPath @('-NoProfile','-NonInteractive','-File',
        (Join-Path $PSScriptRoot 'display_staging_inventory.ps1')) 15000 -ObservationOnly:$ObservationOnly
    return ConvertFrom-Json -InputObject $json -Depth 20
}
function Read-StagingPackages([switch]$ObservationOnly) {
    return Invoke-StagingProcess 'package-inventory' $pnp @('/enum-drivers','/files') 15000 -ObservationOnly:$ObservationOnly
}
function Read-StagingLogAnchor($File,[long]$Length) {
    $start=[Math]::Max(0,$Length-64);$File.Seek($start,[IO.SeekOrigin]::Begin)|Out-Null
    $bytes=[byte[]]::new([int]($Length-$start));$offset=0
    while($offset -lt $bytes.Length) {
        if ($clock.ElapsedMilliseconds -ge $deadline-5000) { throw 'Log anchor budget exhausted.' }
        $read=$File.Read($bytes,$offset,$bytes.Length-$offset)
        if($read -eq 0){throw 'Log anchor unavailable.'};$offset+=$read
    }
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}
function Get-StagingLogCheckpoint([string]$Path) {
    if(-not [IO.File]::Exists($Path)){return @{present=$false;length=0L;identity=$null;anchor=$null}}
    $file=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        $length=$file.Length
        return @{present=$true;length=$length;identity=[StagingWindowGuard]::FileIdentity($file.SafeFileHandle.DangerousGetHandle());
            anchor=(Read-StagingLogAnchor $file $length)}
    } finally {$file.Dispose()}
}
function Read-StagingSetupLogs {
    $rows=@()
    foreach ($name in @('setupapi.dev.log','setupapi.app.log')) {
        $file=$null
        try {
            if ($clock.ElapsedMilliseconds -ge $deadline-5000 -or -not $setupLogOffsets.ContainsKey($name)) {
                throw 'Missing pre-stage log baseline or diagnostic budget.'
            }
            $path=Join-Path $env:SystemRoot ('INF/'+$name)
            if (-not [IO.File]::Exists($path)) { $rows+=@{name=$name;present=$false};continue }
            $file=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,
                ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
            $checkpoint=$setupLogOffsets[$name];$baseline=[long]$checkpoint.length;$length=$file.Length
            $identity=[StagingWindowGuard]::FileIdentity($file.SafeFileHandle.DangerousGetHandle())
            $anchor=Read-StagingLogAnchor $file $baseline
            Assert-StagingLogContinuity $checkpoint $identity $length $anchor
            $start=[Math]::Max($baseline,$length-262144)
            $file.Seek($start,[IO.SeekOrigin]::Begin)|Out-Null
            $bytes=[byte[]]::new([int]($length-$start));$offset=0
            while($offset -lt $bytes.Length) {
                if ($clock.ElapsedMilliseconds -ge $deadline-5000) { throw 'SetupAPI read budget exhausted.' }
                $read=$file.Read($bytes,$offset,$bytes.Length-$offset)
                if($read -eq 0){throw 'SetupAPI suffix changed during read.'};$offset+=$read
            }
            $text=[Text.Encoding]::Latin1.GetString($bytes)
            Assert-StagingLogContinuity $checkpoint ([StagingWindowGuard]::FileIdentity($file.SafeFileHandle.DangerousGetHandle())) `
                $file.Length (Read-StagingLogAnchor $file $baseline)
            $rows+=@{name=$name;present=$true;baseline_bytes=$baseline;observed_bytes=$length;
                continuity_checked=$true;baseline_present=$checkpoint.present;
                suffix_bytes=$bytes.Length;truncated=($start -gt $baseline);
                classification=(Get-StagingSetupLogClassification $text)}
        } catch { $rows+=@{name=$name;failure='SetupAPI observation unavailable or bounded read refused.'} }
        finally { if($null -ne $file){$file.Dispose()} }
    }
    return $rows
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
$before = $null; $beforePackages = $null; $beforePackagesElapsed=0L; $attempted = $false; $ownedPackage = $null
$stageOutcomeObserved = $false
$admitted = $false; $cleanupVerified = $false; $failure = $null; $cleanupFailure = $null
$preflightReport = $null; $final = $null; $packageRemoved = $false
$originalBefore=$null;$publisherLeaf=$null;$publisherAddAttempted=$false;$publisherAdded=$false
$publisherRollback=$null;$publisherFailure=$null;$publisherBaselineAbsent=$false
$publisherRollbackStarted=$null;$publisherRollbackFinished=$null
$publisherVerification=$null
$observationErrors=@();$cleanupPlan=$null;$packageObservations=[Collections.Generic.List[object]]::new()
$partialOperationUncertainty=$false;$setupLogs=@();$success=$false
try {
    $before = Read-StagingSnapshot
    $originalBefore=$before
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
    $beforePackagesElapsed=$clock.ElapsedMilliseconds
    if ($beforePackages -match '(?i)mttvdd' -or
        $beforePackages.IndexOf($model.provider,[StringComparison]::OrdinalIgnoreCase) -ge 0) { throw 'Preexisting package/provider.' }
    if($publisherTrustRequested) {
        $publisherLeaf=Read-VerifiedPublisherLeaf (Join-Path $dataRoot 'VirtualDisplayDriver/MttVDD.cat')
        if(Read-PinnedPublisherPresent){throw 'Publisher leaf already exists; no trust modification allowed.'}
        $publisherBaselineAbsent=$true
        Assert-StagingWindows 'before-publisher-add'
        if($clock.ElapsedMilliseconds -ge 150000){throw 'Insufficient certificate rollback reserve.'}
        # Mark before the write: even a throw after Add must enter independent rollback.
        Add-OwnedPublisherLeaf $publisherLeaf { $script:publisherAddAttempted=$true }
        $publisherAdded=Read-PinnedPublisherPresent
        if(-not $publisherAdded){throw 'Publisher add not observed.'}
        $expected=ConvertFrom-Json ($originalBefore | ConvertTo-Json -Depth 20) -Depth 20
        $key='LocalMachine/TrustedPublisher'
        if($expected.trust.$key -is [string]){throw 'Existing publisher store baseline required.'}
        $expected.trust.$key=@(@($expected.trust.$key)+'3CF8CF26D8BA266C3A483AB7D26D4A818E317D76' | Sort-Object -CaseSensitive)
        $before=Read-StagingSnapshot
        Assert-StagingSnapshotEqual $expected $before
    }
    $immediate = Read-StagingSnapshot
    Assert-StagingSnapshotEqual $before $immediate
    Assert-NoStagingMatch @($immediate.devices) $model.ids
    foreach ($name in @('setupapi.dev.log','setupapi.app.log')) {
        $path=Join-Path $env:SystemRoot ('INF/'+$name)
        $setupLogOffsets[$name]=Get-StagingLogCheckpoint $path
    }
    Invoke-StagingProcess 'one-staging-attempt' $pnp @('/add-driver',$inf) 30000 | Out-Null
    $afterPackages = Read-StagingPackages
    $ownedPackage = Get-NewStagedPackage (Get-PublishedPackages $beforePackages) (Get-PublishedPackages $afterPackages)
    Confirm-ExportIdentity $ownedPackage | Out-Null
    $after = Read-StagingSnapshot
    Assert-StagingSnapshotEqual $before $after
    if ($clock.ElapsedMilliseconds -ge $deadline) { throw 'Normal-path deadline expired before admission acceptance.' }
    $admitted = $true
} catch {
    $admissionEverFailed = $true
    $failure = $_.Exception.Message.Replace($root,'<staging>')
}
finally {
    $deadline = 270000L
    if($publisherTrustRequested){$deadline=240000L} # independent 30-second trust rollback reserve
    try {
    $observationErrors = @()
    # Separate observation from privileged cleanup. Every read retains its own child cap.
    $observations = Invoke-StagingObservations { Read-StagingPackages -ObservationOnly } {
        Read-StagingSnapshot -ObservationOnly
    }
    $currentPackages = $observations.packages; $final = $observations.snapshot
    $packageObservations = [Collections.Generic.List[object]]::new()
    $packageReceipt=Get-StagingPackageObservation 'before-cleanup' $clock.ElapsedMilliseconds $currentPackages
    $packageReceipt.error=$observations.package_error;$packageObservations.Add($packageReceipt)
    foreach ($errorText in @($observations.package_error,$observations.snapshot_error)) {
        if ($errorText) { $observationErrors += $errorText.Replace($root,'<staging>') }
    }
    $cleanupPlan = $null
    try {
        if ($attempted) {
            # Observations do not authorize removal after a failed/interrupted attempt.
            if ($admissionEverFailed -or $guardEverTripped -or -not $stageOutcomeObserved) {
                throw 'Latched failure or incomplete stage outcome forbids removal.'
            }
            if ($observationErrors.Count -or $null -eq $currentPackages -or $null -eq $final) {
                throw 'Missing independent cleanup observations.'
            }
            $ownedPackage = Get-NewStagedPackage (Get-PublishedPackages $beforePackages) (Get-PublishedPackages $currentPackages)
            Confirm-ExportIdentity $ownedPackage | Out-Null
            $preDelete = Read-StagingSnapshot # guarded immediate admission check
            Assert-StagingSnapshotEqual $before $preDelete # no unexpected binding/trust/display change
            $cleanupPlan = Get-StagingPostmortemPlan -StageStarted $attempted -StageOutcomeObserved $stageOutcomeObserved `
                -GuardEverTripped $guardEverTripped -AdmissionEverFailed $admissionEverFailed `
                -BaselineKnown ($null -ne $before) -IdentityVerified $true -BindingsStable $true `
                -TrustStable $true -DisplayStable $true -RemainingMilliseconds ($deadline-$clock.ElapsedMilliseconds)
            if (-not $cleanupPlan.permit_exact_package_removal) { throw 'Cleanup policy refused removal.' }
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
            $cleanupVerified = -not $guardEverTripped -and -not $admissionEverFailed -and $observationErrors.Count -eq 0
        }
    } catch { $cleanupFailure = $_.Exception.Message.Replace($root,'<staging>') }
    # Even a failed ownership/export/delete check must not suppress the final read.
    if ($cleanupFailure) {
        $postFailure = Invoke-StagingObservations { Read-StagingPackages -ObservationOnly } {
            Read-StagingSnapshot -ObservationOnly
        }
        $packageReceipt=Get-StagingPackageObservation 'after-cleanup-failure' $clock.ElapsedMilliseconds $postFailure.packages
        $packageReceipt.error=$postFailure.package_error;$packageObservations.Add($packageReceipt)
        if ($null -ne $postFailure.snapshot) { $final=$postFailure.snapshot }
        foreach ($errorText in @($postFailure.package_error,$postFailure.snapshot_error)) {
            if ($errorText) { $observationErrors += $errorText.Replace($root,'<staging>') }
        }
    }
    $partialOperationUncertainty = $attempted -and -not $stageOutcomeObserved
    if ($partialOperationUncertainty) {
        $cleanupVerified = $false
        $cleanupFailure = ([string]$cleanupFailure + '; OS staging outcome remains uncertain after incomplete child result.').TrimStart(';',' ')
    }
    if ($guardEverTripped -or $admissionEverFailed -or $observationErrors.Count) { $cleanupVerified=$false }
    $setupLogs=Read-StagingSetupLogs
    $success = $admitted -and $cleanupVerified -and $packageRemoved -and -not $failure -and -not $cleanupFailure
    } catch {
        $cleanupVerified=$false;$success=$false
        $cleanupFailure=([string]$cleanupFailure+'; '+$_.Exception.Message.Replace($root,'<staging>')).TrimStart(';',' ')
    } finally {
        $deadline=270000L
        # Certificate rollback is deliberately outside the package/guard path.
        # No package failure or global window delta suppresses this exact owned-leaf removal.
        if($publisherAddAttempted -and $publisherBaselineAbsent -and $null -ne $publisherLeaf) {
            $publisherRollbackStarted=$clock.ElapsedMilliseconds
            $publisherRollback=Invoke-PublisherRollback { Remove-OwnedPublisherLeaf $publisherLeaf } {
                -not (Read-PinnedPublisherPresent)
            }
            $publisherRollbackFinished=$clock.ElapsedMilliseconds
            try {
                $restored=Read-StagingSnapshot -ObservationOnly
                Assert-StagingSnapshotEqual $originalBefore $restored
                Assert-StagingDeadline $clock.ElapsedMilliseconds $deadline
                $final=$restored
            } catch {$publisherFailure=$_.Exception.Message.Replace($root,'<staging>')}
        }
        if($publisherTrustRequested -and (-not $publisherAdded -or $null -eq $publisherRollback -or
            -not $publisherRollback.absent_verified -or $publisherRollback.remove_error -or
            $publisherRollback.verification_error -or $publisherFailure)) {$success=$false;$cleanupVerified=$false}
        if($null -ne $publisherLeaf){$publisherLeaf.Dispose()}
    }
    $report = [ordered]@{
        schema=3;kind='approved-disposable-driver-store-staging-only';source_sha=$env:GITHUB_SHA
        runner_image=$env:ImageOS;image_version=$env:ImageVersion;os=[Environment]::OSVersion.Version.ToString()
        archive_sha256='e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a'
        attempted=$attempted;admitted=$admitted;owned_package=$ownedPackage;package_removed=$packageRemoved
        stage_outcome_observed=$stageOutcomeObserved;partial_operation_uncertainty=$partialOperationUncertainty
        cleanup_verified=$cleanupVerified;passed=[bool]$success;failure=$failure;cleanup_failure=$cleanupFailure
        guard_ever_tripped=$guardEverTripped;admission_ever_failed=$admissionEverFailed
        observation_errors=$observationErrors;cleanup_plan=$cleanupPlan;window_witnesses_truncated=($witnessCount -ge 16)
        package_observations=$packageObservations.ToArray()
        baseline_packages=(Get-StagingPackageObservation 'before-staging' $beforePackagesElapsed $beforePackages)
        staging_child_identity=$stageChildIdentity;setup_log_observations=@($setupLogs)
        before_snapshot=(Get-StagingSnapshotReceipt $originalBefore);staging_snapshot=(Get-StagingSnapshotReceipt $before)
        final_snapshot=(Get-StagingSnapshotReceipt $final)
        publisher_trust_requested=$publisherTrustRequested;publisher_baseline_absent=$publisherBaselineAbsent
        publisher_add_attempted=$publisherAddAttempted;publisher_added=$publisherAdded
        publisher_rollback=$publisherRollback;publisher_restore_failure=$publisherFailure
        publisher_rollback_started_ms=$publisherRollbackStarted;publisher_rollback_finished_ms=$publisherRollbackFinished
        publisher_rollback_timely=($null -ne $publisherRollbackStarted -and $null -ne $publisherRollbackFinished -and
            $publisherRollbackStarted -lt 270000 -and $publisherRollbackFinished -lt 270000)
        publisher_store='LocalMachine/TrustedPublisher'
        publisher_verification=$publisherVerification
        publisher_sha256='69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb'
        signature_preflight_completed=($null -ne $preflightReport -and $preflightReport.verification_completed)
        elapsed_ms=$clock.ElapsedMilliseconds;journal=$journal.ToArray()
        device_creation_requested=$false;driver_loading_requested=$false;display_changes_requested=$false
        trust_changes_requested=$publisherTrustRequested;prompts_answered=$false;reboot_requested=$false;artifacts_uploaded=$false
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
