#Requires -Version 7.2
param([string]$PythonPath = '', [switch]$CleanupOnly, [switch]$FunctionsOnly)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:diagnosticStage = 'prerequisites'

function Set-JobValue([string]$Name, [string]$Value) {
    # Actions echoes GITHUB_ENV into later step logs. Identity and paths may never
    # enter it; the private fixed-path locator is the only ownership handoff.
    $valid = switch -CaseSensitive ($Name) {
        'VERDICLIP_FIXTURE_NATIVE_EXIT' { $Value -cmatch '^-?[0-9]{1,10}$' }
        'VERDICLIP_FIXTURE_NATIVE_FORCED' { $Value -cin @('true', 'false') }
        'VERDICLIP_FIXTURE_PROVIDER_FORCED' { $Value -cin @('true', 'false', 'unknown') }
        'VERDICLIP_FIXTURE_NATIVE_CLEANUP' { $Value -cin @('confirmed', 'failed', 'unknown') }
        'VERDICLIP_FIXTURE_TRACE_RETIRED' { $Value -cin @('0', '1') }
        'VERDICLIP_FIXTURE_READY_TO_REMOVE' { $Value -cin @('0', '1') }
        'VERDICLIP_FIXTURE_LIFECYCLE' { $Value -cin @('1', '2') }
        default { $false }
    }
    if (-not $valid) { throw 'Public job value refused.' }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    if ($env:GITHUB_ENV) { [IO.File]::AppendAllText($env:GITHUB_ENV, "$Name=$Value`n") }
}

function Set-DiagnosticStage([string]$Stage) {
    if ($Stage -cnotin @('prerequisites', 'locator', 'trace_setup', 'native',
        'native_tree', 'trace_retirement', 'export', 'analysis', 'raw_removal',
        'lock_cleanup', 'locator_cleanup', 'complete')) { throw 'Unknown diagnostic stage.' }
    $script:diagnosticStage = $Stage
}

function Get-EntryNoFollow([string]$Path) {
    try { return (Get-Item -LiteralPath $Path -Force -ErrorAction Stop) }
    catch [System.Management.Automation.ItemNotFoundException] { return $null }
}

function Assert-NoReparseParents([string]$Path, [switch]$IncludeLeaf) {
    $current = [IO.Path]::GetFullPath($Path)
    if (-not $IncludeLeaf) { $current = [IO.Path]::GetDirectoryName($current) }
    while ($current) {
        $item = Get-EntryNoFollow $current
        if ($null -eq $item -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Owned path ancestry refused.' }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -ceq $current) { break }
        $current = $parent
    }
}

function Assert-OwnedNativeRoot([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $parent = [IO.Path]::GetFullPath((Join-Path $repoRoot '.test-tmp'))
    if ([IO.Path]::GetFileName($full) -cnotmatch '^native-broker-[a-f0-9]{32}$' -or
        -not [IO.Path]::GetDirectoryName($full).Equals($parent, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Native root confinement refused.'
    }
    Assert-NoReparseParents $full
    $item = Get-EntryNoFollow $full
    if ($null -ne $item -and (-not $item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Linked or non-directory native root refused.'
    }
}

function Get-OwnedNativeTree([string]$Path) {
    # Never use -Recurse: pytest's *current aliases must not duplicate receipts,
    # and a child link must never grant access to its target, even inside the root.
    Assert-OwnedNativeRoot $Path
    if ($null -eq (Get-EntryNoFollow $Path)) { throw 'Owned native root unavailable.' }
    $files = [Collections.Generic.List[object]]::new()
    $directories = [Collections.Generic.List[object]]::new()
    $links = [Collections.Generic.List[object]]::new()
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Path)
    while ($pending.Count) {
        $directory = $pending.Pop()
        Assert-NoReparseParents $directory -IncludeLeaf
        $directories.Add((Get-Item -LiteralPath $directory -Force))
        foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                # PowerShell obtains LinkType from the Windows reparse tag.
                # Junctions and all unknown tags remain forbidden.
                if ($item.LinkType -cne 'SymbolicLink') { throw 'Non-symbolic reparse entry refused.' }
                $links.Add($item)
            } elseif ($item.PSIsContainer) {
                $pending.Push($item.FullName)
            } else { $files.Add($item) }
        }
    }
    return [pscustomobject]@{ files = $files.ToArray(); directories = $directories.ToArray(); links = $links.ToArray() }
}

function Remove-OwnedNativeTree([string]$Path) {
    Assert-OwnedNativeRoot $Path
    if ($null -eq (Get-EntryNoFollow $Path)) { return }
    $tree = Get-OwnedNativeTree $Path # Validate the full no-follow plan before mutation.
    foreach ($link in $tree.links) {
        Assert-NoReparseParents $link.FullName
        $current = Get-EntryNoFollow $link.FullName
        if ($null -eq $current -or -not ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $current.LinkType -cne 'SymbolicLink') { throw 'Owned symbolic link changed.' }
        # Delete the link entry, never recursively and never resolve its target.
        if ($current.Attributes -band [IO.FileAttributes]::Directory) {
            [IO.Directory]::Delete($current.FullName, $false)
        } else { [IO.File]::Delete($current.FullName) }
        if ($null -ne (Get-EntryNoFollow $link.FullName)) { throw 'Owned symbolic link removal unverified.' }
    }
    foreach ($file in $tree.files) {
        Assert-NoReparseParents $file.FullName
        $current = Get-EntryNoFollow $file.FullName
        if ($null -eq $current -or $current.PSIsContainer -or
            $current.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Owned ordinary file changed.' }
        [IO.File]::Delete($current.FullName)
    }
    for ($index = $tree.directories.Count - 1; $index -ge 0; $index--) {
        $directory = $tree.directories[$index].FullName
        Assert-NoReparseParents $directory -IncludeLeaf
        [IO.Directory]::Delete($directory, $false) # Unexpected new children fail closed.
    }
    if ($null -ne (Get-EntryNoFollow $Path)) { throw 'Native raw cleanup unverified.' }
}

function Get-PrivateLocatorPath([string]$TemporaryRoot) {
    $root = [IO.Path]::GetFullPath($TemporaryRoot)
    Assert-NoReparseParents $root -IncludeLeaf
    return (Join-Path $root 'verdiclip-fixture-startup-locator.json')
}

function Read-PrivateLocator([string]$TemporaryRoot) {
    $path = Get-PrivateLocatorPath $TemporaryRoot
    $item = Get-EntryNoFollow $path
    if ($null -eq $item -or $item.PSIsContainer -or
        $item.Attributes -band [IO.FileAttributes]::ReparsePoint -or $item.Length -gt 8192) { throw 'Private locator unavailable.' }
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $true, 1024, $true)
        try {
            $characters = [char[]]::new(8193)
            $length = $reader.ReadBlock($characters, 0, $characters.Length)
            if ($length -gt 8192) { throw 'Private locator exceeds budget.' }
            $text = [string]::new($characters, 0, $length)
        } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
    if ($text.Length -gt 8192) { throw 'Private locator exceeds budget.' }
    $value = $text | ConvertFrom-Json
    $keys = @('schema', 'purpose', 'owner', 'source_sha', 'repair_base_sha', 'private_root',
        'repo_root', 'runner_temp', 'repository', 'run_id', 'run_attempt', 'job')
    if ($value -isnot [pscustomobject] -or @($value.PSObject.Properties).Count -ne $keys.Count -or
        @($value.PSObject.Properties.Name | Where-Object { $_ -cnotin $keys }).Count -or
        ($value.schema -isnot [int] -and $value.schema -isnot [long]) -or $value.schema -ne 1) { throw 'Private locator schema refused.' }
    foreach ($key in $keys | Where-Object { $_ -cne 'schema' }) {
        if ($value.$key -isnot [string] -or $value.$key.Length -gt 2048) { throw 'Private locator types refused.' }
    }
    $temporary = [IO.Path]::GetFullPath($TemporaryRoot)
    if ($value.purpose -cne 'fixture-startup-diagnostic' -or $value.owner -cnotmatch '^VerdiClip-[a-f0-9]{32}$' -or
        $value.source_sha -cnotmatch '^[a-f0-9]{40}$' -or $value.source_sha -cne $env:VERDICLIP_BENCHMARK_SOURCE_SHA -or
        $value.repair_base_sha -cne '5a5983275dfbd7e7243bd329ca3d859faca024c2' -or
        $value.repair_base_sha -cne $env:VERDICLIP_FIXTURE_REPAIR_BASE -or
        $value.repo_root -cne $repoRoot -or $value.runner_temp -cne $temporary -or
        $value.private_root -cne (Join-Path $temporary $value.owner) -or
        $value.repository -cne 'mikejhill/verdiclip' -or $value.repository -cne $env:GITHUB_REPOSITORY -or
        $value.run_id -cnotmatch '^[1-9][0-9]{0,19}$' -or $value.run_id -cne $env:GITHUB_RUN_ID -or
        $value.run_attempt -cnotmatch '^[1-9][0-9]{0,5}$' -or $value.run_attempt -cne $env:GITHUB_RUN_ATTEMPT -or
        $value.job -cnotmatch '^[A-Za-z_][A-Za-z0-9_-]{0,99}$' -or $value.job -cne $env:GITHUB_JOB) {
        throw 'Private locator context refused.'
    }
    Assert-NoReparseParents $value.repo_root -IncludeLeaf
    Assert-NoReparseParents $value.private_root
    $ownedRoot = Get-EntryNoFollow $value.private_root
    if ($null -ne $ownedRoot -and (-not $ownedRoot.PSIsContainer -or
        $ownedRoot.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Private locator root redirected.' }
    return $value
}

function Write-PrivateLocator($State, [string]$TemporaryRoot) {
    $path = Get-PrivateLocatorPath $TemporaryRoot
    $value = [ordered]@{
        schema = 1; purpose = 'fixture-startup-diagnostic'; owner = $State.owner
        source_sha = $State.source_sha; repair_base_sha = $State.repair_base_sha
        private_root = $State.private_root; repo_root = $repoRoot
        runner_temp = [IO.Path]::GetFullPath($TemporaryRoot); repository = $env:GITHUB_REPOSITORY
        run_id = $env:GITHUB_RUN_ID; run_attempt = $env:GITHUB_RUN_ATTEMPT; job = $env:GITHUB_JOB
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Compress))
    if ($bytes.Length -gt 8192) { throw 'Private locator exceeds budget.' }
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    $verified = Read-PrivateLocator $TemporaryRoot
    if ($verified.owner -cne $State.owner) { throw 'Private locator ownership changed.' }
}

function Remove-PrivateLocator([string]$TemporaryRoot, [string]$Owner) {
    $value = Read-PrivateLocator $TemporaryRoot
    if ($value.owner -cne $Owner -or $null -ne (Get-EntryNoFollow $value.private_root) -or
        $null -ne (Get-EntryNoFollow (Join-Path $repoRoot '.test-tmp/fixture-startup-diagnostic.lock')) -or
        $env:VERDICLIP_FIXTURE_TRACE_RETIRED -cne '1' -or $env:VERDICLIP_FIXTURE_READY_TO_REMOVE -cne '1') {
        throw 'Private locator cleanup prerequisites unverified.'
    }
    $path = Get-PrivateLocatorPath $TemporaryRoot
    [IO.File]::Delete($path)
    if ($null -ne (Get-EntryNoFollow $path)) { throw 'Private locator removal unverified.' }
    Set-JobValue 'VERDICLIP_FIXTURE_LIFECYCLE' '2'
}

function Assert-OrdinaryTree([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Path)
    while ($pending.Count) {
        $item = Get-Item -LiteralPath $pending.Pop() -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused.' }
        if ($item.PSIsContainer) {
            foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) { $pending.Push($child.FullName) }
        }
    }
}

function Write-Ownership($State, [string]$Root) {
    $temporary = Join-Path $Root 'runner-owner.tmp'
    [IO.File]::WriteAllText($temporary, ($State | ConvertTo-Json -Depth 8 -Compress))
    [IO.File]::Move($temporary, (Join-Path $Root 'runner-owner.json'), $true)
}

function Get-NativeNames([string]$NativeRoot) {
    if (-not (Test-Path -LiteralPath $NativeRoot)) { return @() }
    if ((Get-Item -LiteralPath $NativeRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Native root redirected.' }
    return @(Get-ChildItem -LiteralPath $NativeRoot -Directory -Force |
        Where-Object { $_.Name -cmatch '^native-broker-[a-f0-9]{32}$' } |
        ForEach-Object { $_.Name })
}

function Find-OwnedNativeDirectories($State, [string]$Root) {
    if (-not $State.native_attempted) { return }
    $current = @(Get-NativeNames (Join-Path $repoRoot '.test-tmp'))
    $created = @($current | Where-Object { $_ -cnotin $State.native_before })
    # One wrapper, one runtime, one native-broker directory. Never guess on ambiguity.
    if ($created.Count -gt 1) { throw 'Native directory ownership is ambiguous.' }
    foreach ($name in $created) {
        if ($name -cnotin $State.native_directories) {
            $State.native_directories += $name
            Write-Ownership $State $Root
        }
    }
    if (@($State.native_directories).Count -gt 1) { throw 'Native directory generation changed.' }
}

function Assert-MicrosoftBinary([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Installed tool missing.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
        throw 'Installed Microsoft tool identity not verified.'
    }
}

function Invoke-PrivateTool([string]$File, [string[]]$Arguments, [string]$Root,
                            [string]$Label, [int]$Seconds = 30) {
    # No stdout, stderr or exception from a subprocess is copied into the job log.
    $info = [Diagnostics.ProcessStartInfo]::new($File)
    $info.WorkingDirectory = $repoRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    if ($File -ceq $env:ComSpec) {
        if ($Arguments.Count -ne 4 -or ($Arguments[0..2] -join ' ') -cne '/d /s /c') { throw 'Unexpected compiler shell arguments.' }
        # cmd does not implement CRT argv quoting. Preserve the reviewed command's
        # nested path quotes with its documented /s /c outer-quote convention.
        $info.Arguments = '/d /s /c "' + $Arguments[3] + '"'
    } else {
        foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Owned tool did not start.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($Seconds * 1000)) {
            $process.Kill($true)
            if (-not $process.WaitForExit(5000)) { throw 'Owned tool exit unverified.' }
            return 124
        }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw 'Owned tool output retirement unverified.' }
        [IO.File]::WriteAllText((Join-Path $Root "$Label.stdout.log"), $stdout.GetAwaiter().GetResult())
        [IO.File]::WriteAllText((Join-Path $Root "$Label.stderr.log"), $stderr.GetAwaiter().GetResult())
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Build-PrivateReader([string]$Root) {
    $locator = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    Assert-MicrosoftBinary $locator
    $code = Invoke-PrivateTool $locator @('-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath') $Root 'locator'
    $installation = [IO.File]::ReadAllText((Join-Path $Root 'locator.stdout.log')).Trim()
    if ($code -ne 0 -or -not $installation -or $installation.Contains("`n")) { throw 'Installed compiler unavailable.' }
    $setup = Join-Path $installation 'VC/Auxiliary/Build/vcvars64.bat'
    $toolsets = @(Get-ChildItem -LiteralPath (Join-Path $installation 'VC/Tools/MSVC') -Directory |
        Sort-Object { [version]$_.Name } -Descending)
    if (-not $toolsets.Count) { throw 'Installed compiler unavailable.' }
    $bin = Join-Path $toolsets[0].FullName 'bin/Hostx64/x64'
    $compiler = Join-Path $bin 'cl.exe'
    Assert-MicrosoftBinary $compiler
    Assert-MicrosoftBinary (Join-Path $bin 'link.exe')
    Assert-MicrosoftBinary $env:ComSpec
    # Existing vendor setup only; no package installation or fixture prewarming.
    $command = '"' + $setup + '" >nul && "' + $compiler + '" /nologo /W4 /WX /EHsc /std:c++17 scripts\read_scheduler_etl.cpp /Fe:"' +
        (Join-Path $Root 'reader.exe') + '" /Fo:"' + (Join-Path $Root 'reader.obj') + '" /link advapi32.lib tdh.lib'
    if ((Invoke-PrivateTool $env:ComSpec @('/d', '/s', '/c', $command) $Root 'compile' 60) -ne 0 -or
        -not (Test-Path -LiteralPath (Join-Path $Root 'reader.exe') -PathType Leaf)) { throw 'Reader compilation failed.' }
}

function Retire-OwnedTrace($State, [string]$Root, [switch]$CancelOnly) {
    if ($State.trace_retired) { return $true }
    if (-not $State.trace_attempted) {
        $State.trace_retired = $true
    } else {
        try {
            $wpr = Join-Path $env:SystemRoot 'System32/wpr.exe'
            Assert-MicrosoftBinary $wpr
            if ($State.trace_started -and -not $CancelOnly) {
                $code = Invoke-PrivateTool $wpr @('-stop', (Join-Path $Root 'trace.etl'),
                    '-skipPdbGen', '-instancename', $State.owner) $Root 'trace-stop' 30
                if ($code -eq 0) { $State.trace_retired = $true; $State.trace_finalized = $true }
            }
            if (-not $State.trace_retired) {
                $code = Invoke-PrivateTool $wpr @('-cancel', '-instancename', $State.owner) $Root 'trace-cancel' 30
                if ($code -eq 0) { $State.trace_retired = $true }
            }
        } catch { $State.trace_retired = $false }
    }
    Write-Ownership $State $Root
    if ($State.trace_retired) { Set-JobValue 'VERDICLIP_FIXTURE_TRACE_RETIRED' '1' }
    return [bool]$State.trace_retired
}

function Publish-NativeResult($State) {
    Set-JobValue 'VERDICLIP_FIXTURE_NATIVE_EXIT' ([string]$State.native_exit)
    Set-JobValue 'VERDICLIP_FIXTURE_NATIVE_FORCED' (([string]$State.native_forced).ToLowerInvariant())
    Set-JobValue 'VERDICLIP_FIXTURE_PROVIDER_FORCED' $State.native_fixture_forced
    Set-JobValue 'VERDICLIP_FIXTURE_NATIVE_CLEANUP' $State.native_cleanup
}

function Read-NativeVerdict($State) {
    $State.native_cleanup = 'unknown'
    if (@($State.native_directories).Count -ne 1) { return }
    $directory = Join-Path (Join-Path $repoRoot '.test-tmp') $State.native_directories[0]
    $tree = Get-OwnedNativeTree $directory
    $receipts = @($tree.files | Where-Object { $_.Name -ceq 'scrollbar-native.json' })
    if ($receipts.Count -ne 1 -or $receipts[0].Length -gt 262144) { return }
    try {
        $verdict = Get-Content -LiteralPath $receipts[0].FullName -Raw | ConvertFrom-Json
        if ($verdict.PSObject.Properties.Name -notcontains 'cleanup_errors' -or
            $verdict.PSObject.Properties.Name -notcontains 'retired') { return }
        $State.native_cleanup = if (@($verdict.cleanup_errors).Count -eq 0 -and $verdict.retired -is [bool] -and $verdict.retired) { 'confirmed' } else { 'failed' }
        # Fixed classification only. Raw errors, handles, PIDs and paths stay private.
        if (@($verdict.cleanup_errors | Where-Object { $_ -is [string] -and $_.Contains('Owned provider required forced termination') }).Count) {
            $State.native_fixture_forced = 'true'
        } elseif ($State.native_cleanup -ceq 'confirmed') {
            $State.native_fixture_forced = 'false'
        }
    } catch { $State.native_cleanup = 'unknown' }
}

function Invoke-NativeOnce($State, [string]$Root, [string]$Python) {
    $State.native_before = @(Get-NativeNames (Join-Path $repoRoot '.test-tmp'))
    $State.native_attempted = $true
    Write-Ownership $State $Root # Persist the baseline before the only invocation.
    $info = [Diagnostics.ProcessStartInfo]::new((Get-Process -Id $PID).Path)
    $info.WorkingDirectory = $repoRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($argument in @('-NoProfile', '-NonInteractive', '-File',
        (Join-Path $PSScriptRoot 'test_capture_broker_native.ps1'), '-ScrollbarOnly',
        '-ExecuteAfterHandoff', '-PythonPaths', $Python)) { $info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $started = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not $process.Start()) { throw 'Native wrapper did not start.' }
        $started = $true
        $State.native_pid = $process.Id
        $State.native_started_ticks = $process.StartTime.ToUniversalTime().Ticks
        Write-Ownership $State $Root
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        while (-not $process.WaitForExit([int][Math]::Max(0, [Math]::Min(100, 90000 - $watch.ElapsedMilliseconds)))) {
            try { Find-OwnedNativeDirectories $State $Root }
            catch { $State.native_ownership_uncertain = $true }
            if ($watch.Elapsed.TotalSeconds -ge 90) {
                $State.native_forced = $true
                $State.native_exit = 124
                $process.Kill($true)
                if (-not $process.WaitForExit([int][Math]::Max(0, 90000 - $watch.ElapsedMilliseconds))) { throw 'Native wrapper tree exit unverified.' }
                break
            }
        }
        if (-not $State.native_forced) { $State.native_exit = $process.ExitCode }
        $State.native_exited = $true
        Publish-NativeResult $State # Save the original outcome before optional diagnostics.
        Write-Ownership $State $Root
        if (-not $stdout.Wait([int][Math]::Max(0, 90000 - $watch.ElapsedMilliseconds)) -or
            -not $stderr.Wait([int][Math]::Max(0, 90000 - $watch.ElapsedMilliseconds))) { throw 'Native output retirement unverified.' }
        [IO.File]::WriteAllText((Join-Path $Root 'native-wrapper.stdout.log'), $stdout.GetAwaiter().GetResult())
        [IO.File]::WriteAllText((Join-Path $Root 'native-wrapper.stderr.log'), $stderr.GetAwaiter().GetResult())
    } finally {
        if ($started -and -not $process.HasExited) {
            $State.native_forced = $true
            $State.native_exit = 124
            $process.Kill($true)
            $State.native_exited = $process.WaitForExit([int][Math]::Max(0, 90000 - $watch.ElapsedMilliseconds))
        }
        $process.Dispose()
        try {
            Find-OwnedNativeDirectories $State $Root
        } catch { $State.native_ownership_uncertain = $true }
        # Receipt parsing is a separate diagnostic, not evidence that the already
        # established native directory belongs to someone else.
        try { Read-NativeVerdict $State } catch { $State.native_cleanup = 'unknown' }
        Publish-NativeResult $State
        Write-Ownership $State $Root
    }
}

function Stop-OwnedNativeAfterInterruption($State, [string]$Root) {
    if (-not $State.native_attempted -or $State.native_exited) { return $true }
    # A missing identity after an interrupted Start is uncertainty, not permission
    # to search for or terminate similarly named processes.
    if ($State.native_pid -le 0 -or $State.native_started_ticks -le 0) { return $false }
    $process = Get-Process -Id $State.native_pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false } # Descendants and exit code are unknown.
    try {
        if ($process.StartTime.ToUniversalTime().Ticks -ne $State.native_started_ticks) { return $false }
        $State.native_forced = $true
        $State.native_exit = 124
        $process.Kill($true)
        $State.native_exited = $process.WaitForExit(5000)
        Write-Ownership $State $Root
        Publish-NativeResult $State
        return [bool]$State.native_exited
    } finally { $process.Dispose() }
}

function Remove-OwnedRawFiles($State, [string]$Root, [bool]$NativeExited) {
    # Do not race an unverified native writer or follow a redirected directory.
    if (-not $NativeExited) { Set-DiagnosticStage 'native'; return $false }
    if ($State.native_ownership_uncertain) { Set-DiagnosticStage 'native_tree'; return $false }
    try {
        Set-DiagnosticStage 'native_tree'
        Find-OwnedNativeDirectories $State $Root
        Set-DiagnosticStage 'raw_removal'
        Assert-OrdinaryTree $Root
        Set-DiagnosticStage 'native_tree'
        $fixtureOwner = Join-Path $Root 'owner.json'
        if (Test-Path -LiteralPath $fixtureOwner -PathType Leaf) {
            if ((Get-Item -LiteralPath $fixtureOwner).Length -gt 65536) { throw 'Fixture owner oversized.' }
            $owned = Get-Content -LiteralPath $fixtureOwner -Raw | ConvertFrom-Json
            if (@($State.native_directories).Count -ne 1 -or $owned.fixture_directory -isnot [string]) { throw 'Fixture output ownership missing.' }
            $nativeDirectory = Join-Path (Join-Path $repoRoot '.test-tmp') $State.native_directories[0]
            $prefix = [IO.Path]::GetFullPath($nativeDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
            if (-not [IO.Path]::GetFullPath($owned.fixture_directory).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture output ownership mismatch.' }
        }
        foreach ($name in $State.native_directories) {
            if ($name -cnotmatch '^native-broker-[a-f0-9]{32}$' -or $name -cin $State.native_before) { throw 'Native cleanup ownership refused.' }
            $directory = Join-Path (Join-Path $repoRoot '.test-tmp') $name
            Remove-OwnedNativeTree $directory
        }
        # An unsettled recorder may still write here; keep its ownership receipt.
        if (-not $State.trace_retired) { Set-DiagnosticStage 'trace_retirement'; return $false }
        Set-DiagnosticStage 'raw_removal'
        Assert-OrdinaryTree $Root
        Set-JobValue 'VERDICLIP_FIXTURE_READY_TO_REMOVE' '1'
        Remove-Item -LiteralPath $Root -Recurse -Force
        if (Test-Path -LiteralPath $Root) { throw 'Trace raw cleanup unverified.' }
        return $true
    } catch { return $false }
}

function Get-OwnedState([string]$Root, [string]$Owner) {
    Assert-OrdinaryTree $Root
    $receipt = Join-Path $Root 'runner-owner.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or
        (Get-Item -LiteralPath $receipt).Length -gt 65536) { throw 'Runner ownership receipt unavailable.' }
    $state = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    $keys = @('schema', 'owner', 'source_sha', 'repair_base_sha', 'repo_root', 'private_root',
        'trace_attempted', 'trace_started', 'trace_retired', 'trace_finalized',
        'native_attempted', 'native_before', 'native_directories', 'native_pid',
        'native_started_ticks', 'native_exited', 'native_exit', 'native_forced',
        'native_fixture_forced', 'native_cleanup', 'native_ownership_uncertain')
    if ($state -isnot [pscustomobject] -or @($state.PSObject.Properties).Count -ne $keys.Count -or
        @($state.PSObject.Properties.Name | Where-Object { $_ -cnotin $keys }).Count) { throw 'Runner ownership schema refused.' }
    foreach ($name in @('trace_attempted', 'trace_started', 'trace_retired', 'trace_finalized',
        'native_attempted', 'native_exited', 'native_forced', 'native_ownership_uncertain')) {
        if ($state.$name -isnot [bool]) { throw 'Runner ownership boolean refused.' }
    }
    foreach ($name in @('schema', 'native_pid', 'native_started_ticks', 'native_exit')) {
        if ($state.$name -isnot [int] -and $state.$name -isnot [long]) { throw 'Runner ownership integer refused.' }
    }
    if ($state.schema -ne 1 -or $state.owner -cne $Owner -or
        $state.source_sha -cne $env:VERDICLIP_BENCHMARK_SOURCE_SHA -or
        $state.repo_root -cne $repoRoot -or $state.private_root -cne $Root -or
        $state.repair_base_sha -cne '5a5983275dfbd7e7243bd329ca3d859faca024c2' -or
        $state.source_sha -cnotmatch '^[a-f0-9]{40}$') { throw 'Runner ownership receipt mismatch.' }
    if ($state.native_pid -lt 0 -or $state.native_pid -gt [int]::MaxValue -or
        $state.native_started_ticks -lt 0 -or $state.native_started_ticks -gt [DateTime]::MaxValue.Ticks -or
        $state.native_exit -lt [int]::MinValue -or $state.native_exit -gt [int]::MaxValue -or
        $state.native_fixture_forced -isnot [string] -or $state.native_cleanup -isnot [string] -or
        $state.native_fixture_forced -cnotin @('true', 'false', 'unknown') -or
        $state.native_cleanup -cnotin @('confirmed', 'failed', 'unknown')) { throw 'Runner ownership range refused.' }
    foreach ($name in @('native_before', 'native_directories')) {
        if ($state.$name -isnot [System.Collections.IList] -or $state.$name.Count -gt 256 -or
            @($state.$name | Select-Object -Unique).Count -ne $state.$name.Count) { throw 'Runner native ownership array refused.' }
    }
    foreach ($name in @($state.native_before) + @($state.native_directories)) {
        if ($name -isnot [string] -or $name -cnotmatch '^native-broker-[a-f0-9]{32}$') { throw 'Invalid native ownership receipt.' }
    }
    if ($state.native_directories.Count -gt 1 -or
        @($state.native_directories | Where-Object { $_ -cin $state.native_before }).Count -or
        ($state.trace_started -and -not $state.trace_attempted) -or
        ($state.trace_finalized -and -not ($state.trace_attempted -and $state.trace_started -and $state.trace_retired)) -or
        (($state.native_pid -eq 0) -ne ($state.native_started_ticks -eq 0)) -or
        ($state.native_exited -and $state.native_pid -eq 0) -or
        ($state.native_forced -and $state.native_exit -ne 124) -or
        (-not $state.native_attempted -and ($state.native_pid -ne 0 -or $state.native_exited -or
            $state.native_forced -or $state.native_directories.Count -ne 0))) {
        throw 'Runner ownership relationship refused.'
    }
    return $state
}

function Open-NativeLock([string]$Owner, [switch]$Existing) {
    $nativeRoot = Join-Path $repoRoot '.test-tmp'
    if (-not (Test-Path -LiteralPath $nativeRoot)) { [IO.Directory]::CreateDirectory($nativeRoot) | Out-Null }
    if ((Get-Item -LiteralPath $nativeRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Native root redirected.' }
    $path = Join-Path $nativeRoot 'fixture-startup-diagnostic.lock'
    if ($Existing -and (Test-Path -LiteralPath $path)) {
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Native lock redirected.' }
        $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $false, 1024, $true)
        try { $ownerInFile = $reader.ReadToEnd() } finally { $reader.Dispose() }
        if ($ownerInFile -cne $Owner) { $stream.Dispose(); throw 'Native lock ownership mismatch.' }
    } else {
        $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $bytes = [Text.Encoding]::UTF8.GetBytes($Owner)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    return $stream
}

function Close-NativeLock($Lock, [string]$Owner, [switch]$Preserve) {
    if ($null -eq $Lock) { return }
    $Lock.Dispose()
    if ($Preserve) { return } # Retain the ownership token for the always() fallback.
    $path = Join-Path $repoRoot '.test-tmp/fixture-startup-diagnostic.lock'
    foreach ($entry in @((Split-Path -Parent $path), $path)) {
        if ((Get-Item -LiteralPath $entry -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Native lock cleanup redirected.' }
    }
    if ([IO.File]::ReadAllText($path) -cne $Owner) { throw 'Native lock ownership changed.' }
    Remove-Item -LiteralPath $path
    if (Test-Path -LiteralPath $path) { throw 'Native lock cleanup unverified.' }
}

function Test-PublicationGate($TraceRetired, $RawRemoved, $Analysis) {
    return $TraceRetired -is [bool] -and $RawRemoved -is [bool] -and
        $TraceRetired -and $RawRemoved -and $null -ne $Analysis
}

function Read-PublicAggregate([string]$Path, [string]$Source) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        (Get-Item -LiteralPath $Path).Length -gt 65536) { throw 'Aggregate unavailable.' }
    Assert-OrdinaryTree $Path
    $value = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
    $rootKeys = @('schema', 'status', 'source_sha', 'repair_base_sha', 'marker_count', 'marker_capture_complete',
        'incomplete_phases', 'clock_bridge_exact', 'scheduler', 'reason')
    if (@($value.PSObject.Properties.Name).Count -ne $rootKeys.Count -or
        @($value.PSObject.Properties.Name | Where-Object { $_ -cnotin $rootKeys }).Count -or
        $value.schema -ne 1 -or $value.source_sha -cne $Source -or
        $value.repair_base_sha -cne '5a5983275dfbd7e7243bd329ca3d859faca024c2' -or
        $value.status -cnotin @('complete', 'incomplete', 'unavailable') -or
        $value.marker_count -lt 0 -or $value.marker_count -gt 26 -or
        $value.reason -cnotin @('none', 'evidence_unavailable')) { throw 'Aggregate schema refused.' }
    if (($value.schema -isnot [int] -and $value.schema -isnot [long]) -or
        ($value.marker_count -isnot [int] -and $value.marker_count -isnot [long]) -or
        $value.marker_capture_complete -isnot [bool] -or $value.clock_bridge_exact -isnot [bool] -or
        $value.incomplete_phases -isnot [System.Collections.IList]) { throw 'Aggregate types refused.' }
    # Revalidate every public leaf independently of the Python emitter. No free
    # text, absolute clocks, paths, identifiers or unrecognized fields can pass.
    $phaseNames = @('assemblies', 'identity', 'window', 'controls', 'pixels', 'bitmap',
        'freeze', 'content', 'activation', 'snapshot')
    $strings = @('complete', 'incomplete', 'unavailable', 'none', 'evidence_unavailable',
        'raw QPC via Stopwatch with exact integer tick bridge', 'initial_lifetime',
        'same_timestamp_group', 'ready_while_scheduled', 'unsupported_old_state') + $phaseNames
    $keys = $rootKeys + @('event_loss_checked', 'events_lost', 'buffers_lost', 'thread_lifetime_checked',
        'thread_reuse_rejected', 'outside_lifetime_events_excluded', 'clock',
        'alignment_endpoint_rounding_bound_ns', 'thread_lifetime_unknown_state_reasons',
        'reason', 'entry_count', 'unknown_ms', 'analysis_complete', 'phase_count', 'phases',
        'causal_claim', 'scheduled_time_is_not_exclusive_application_cpu',
        'independent_xperf_field_crosscheck', 'phase', 'capture_ordinal', 'wall_ms',
        'scheduled_ms', 'waiting_ms', 'ready_ms', 'state_complete')
    $pending = [Collections.Generic.Stack[object]]::new()
    $pending.Push($value)
    while ($pending.Count) {
        $item = $pending.Pop()
        if ($null -eq $item -or $item -is [bool]) { continue }
        if ($item -is [string]) {
            if ($item -cne $Source -and $item -cne '5a5983275dfbd7e7243bd329ca3d859faca024c2' -and
                $item -cnotin $strings) { throw 'Free-text aggregate refused.' }
        } elseif ($item -is [System.Collections.IList]) {
            if ($item.Count -gt 26) { throw 'Aggregate array exceeds budget.' }
            foreach ($entry in $item) { $pending.Push($entry) }
        } elseif ($item -is [pscustomobject]) {
            foreach ($property in $item.PSObject.Properties) {
                if ($property.Name -cnotin $keys) { throw 'Private aggregate field refused.' }
                $pending.Push($property.Value)
            }
        } elseif ($item -is [ValueType]) {
            $number = [double]$item
            if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or
                $number -lt 0 -or $number -gt 2000000001) { throw 'Aggregate number exceeds budget.' }
        } else { throw 'Aggregate value refused.' }
    }
    return $value
}

function Invoke-FixtureDiagnostic {
    if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or
        $env:RUNNER_OS -cne 'Windows' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
        $env:VERDICLIP_BENCHMARK_SOURCE_SHA -cnotmatch '^[a-f0-9]{40}$' -or
        $env:VERDICLIP_FIXTURE_REPAIR_BASE -cne '5a5983275dfbd7e7243bd329ca3d859faca024c2' -or
        $env:VERDICLIP_HOSTED_SOURCE -cne $env:VERDICLIP_BENCHMARK_SOURCE_SHA) { throw 'Disposable hosted source identity required.' }
    $temporaryRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP)
    Assert-NoReparseParents $temporaryRoot -IncludeLeaf
    $lock = $null
    if ($CleanupOnly) {
        Set-DiagnosticStage 'locator'
        $locatorPath = Get-PrivateLocatorPath $temporaryRoot
        if ($null -eq (Get-EntryNoFollow $locatorPath)) {
            if (-not $env:VERDICLIP_FIXTURE_LIFECYCLE) { return 0 } # Main never reserved work.
            if ($env:VERDICLIP_FIXTURE_LIFECYCLE -ceq '2' -and
                $env:VERDICLIP_FIXTURE_TRACE_RETIRED -ceq '1' -and $env:VERDICLIP_FIXTURE_READY_TO_REMOVE -ceq '1') { return 0 }
            throw 'Missing private locator leaves ownership unknown.'
        }
        $locator = Read-PrivateLocator $temporaryRoot
        $owner = $locator.owner
        $root = $locator.private_root
        if ($null -eq (Get-EntryNoFollow $root)) {
            if ($env:VERDICLIP_FIXTURE_TRACE_RETIRED -ceq '1' -and $env:VERDICLIP_FIXTURE_READY_TO_REMOVE -ceq '1') {
                # Main may have been interrupted between removing raw files and
                # releasing its lock. Verify and remove that final owned receipt.
                Set-DiagnosticStage 'lock_cleanup'
                $lock = Open-NativeLock $owner -Existing
                Close-NativeLock $lock $owner
                Set-DiagnosticStage 'locator_cleanup'
                Remove-PrivateLocator $temporaryRoot $owner
                Set-DiagnosticStage 'complete'
                return 0
            }
            throw 'Missing receipt does not prove cleanup.'
        }
        $state = Get-OwnedState $root $owner
        $retired = $false
        $removed = $false
        try {
            $nativeExited = -not $state.native_attempted
            try {
                $lock = Open-NativeLock $owner -Existing
                $nativeExited = Stop-OwnedNativeAfterInterruption $state $root
            } catch { $nativeExited = -not $state.native_attempted }
            Set-DiagnosticStage 'trace_retirement'
            $retired = Retire-OwnedTrace $state $root -CancelOnly
            $removed = Remove-OwnedRawFiles $state $root $nativeExited
            Publish-NativeResult $state
        } finally {
            if ($retired -and $removed) { Set-DiagnosticStage 'lock_cleanup' }
            Close-NativeLock $lock $owner -Preserve:(-not ($retired -and $removed))
        }
        if (-not $retired -or -not $removed) {
            Write-Output ('FIXTURE_STARTUP_STATUS=withheld; stage:' + $script:diagnosticStage + '; reason:verification_failed')
            return 1
        }
        Set-DiagnosticStage 'locator_cleanup'
        Remove-PrivateLocator $temporaryRoot $owner
        Set-DiagnosticStage 'complete'
        return 0
    }
    $head = (& git -C $repoRoot rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -cne $env:VERDICLIP_BENCHMARK_SOURCE_SHA) { throw 'Exact source checkout required.' }
    if ($env:VERDICLIP_HOSTED_SCROLLBAR -cne '1' -or $env:VERDICLIP_NATIVE_PROVIDER -cne 'wpf' -or
        $env:VERDICLIP_EXPECTED_QT -cne '6.12.0' -or $env:QT_SCALE_FACTOR -cne '2' -or
        $env:VERDICLIP_EXPECTED_QT_SCALE -cne '2') { throw 'Single WPF Qt 6.12 scale 2 profile required.' }
    $python = [IO.Path]::GetFullPath($PythonPath)
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Explicit native Python missing.' }
    $owner = 'VerdiClip-' + [Guid]::NewGuid().ToString('N')
    $root = Join-Path $temporaryRoot $owner
    $state = [pscustomobject]@{
        schema = 1; owner = $owner; source_sha = $head; repo_root = $repoRoot; private_root = $root
        repair_base_sha = '5a5983275dfbd7e7243bd329ca3d859faca024c2'
        trace_attempted = $false; trace_started = $false; trace_retired = $false; trace_finalized = $false
        native_attempted = $false; native_before = @(); native_directories = @()
        native_pid = 0; native_started_ticks = 0; native_exited = $false
        native_ownership_uncertain = $false
        native_exit = 125; native_forced = $false; native_fixture_forced = 'unknown'; native_cleanup = 'unknown'
    }
    Set-DiagnosticStage 'locator'
    Set-JobValue 'VERDICLIP_FIXTURE_LIFECYCLE' '1'
    Set-JobValue 'VERDICLIP_FIXTURE_TRACE_RETIRED' '0'
    Set-JobValue 'VERDICLIP_FIXTURE_READY_TO_REMOVE' '0'
    Write-PrivateLocator $state $temporaryRoot # Exclusive reservation before any owned recording/native work.
    # CreateDirectoryW fails if the directory exists: never adopt another run.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FixtureDiagnosticDirectory {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CreateDirectoryW(string path, IntPtr attributes);
}
'@
    if (-not [FixtureDiagnosticDirectory]::CreateDirectoryW($root, [IntPtr]::Zero)) { throw 'Exclusive private directory creation failed.' }
    Write-Ownership $state $root
    Remove-Item Env:VERDICLIP_FIXTURE_TRACE_DIR -ErrorAction SilentlyContinue
    $analysis = $null
    $diagnosticExit = 1
    $retired = $false
    $removed = $false
    $locatorRemoved = $false
    $evidenceStage = 'trace_setup'
    $firstFailureStage = $null
    try {
        $lock = Open-NativeLock $owner
        try {
            Set-DiagnosticStage 'trace_setup'
            $wpr = Join-Path $env:SystemRoot 'System32/wpr.exe'
            $xperf = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Windows Performance Toolkit/xperf.exe'
            Assert-MicrosoftBinary $wpr
            Assert-MicrosoftBinary $xperf
            Build-PrivateReader $root
            $state.trace_attempted = $true
            Write-Ownership $state $root # BEFORE start, including nonzero/uncertain starts.
            $code = Invoke-PrivateTool $wpr @('-start', ((Join-Path $PSScriptRoot 'gui_waits.wprp') + '!VerdiClipWaits'),
                '-instancename', $owner) $root 'trace-start' 15
            $state.trace_started = $code -eq 0
            if (-not $state.trace_started) { $firstFailureStage = 'trace_setup' }
            Write-Ownership $state $root
        } catch {
            $firstFailureStage = 'trace_setup'
            $state.trace_started = $false
            Write-Ownership $state $root
        }
        if ($state.trace_started) { $env:VERDICLIP_FIXTURE_TRACE_DIR = $root }
        try {
            Set-DiagnosticStage 'native'
            Invoke-NativeOnce $state $root $python
        } catch {
            if ($null -eq $firstFailureStage) { $firstFailureStage = 'native' }
            throw
        } finally {
            Remove-Item Env:VERDICLIP_FIXTURE_TRACE_DIR -ErrorAction SilentlyContinue
            Set-DiagnosticStage 'trace_retirement'
            $retired = Retire-OwnedTrace $state $root
            if (-not $retired -and $null -eq $firstFailureStage) { $firstFailureStage = 'trace_retirement' }
        }
        if ($state.trace_finalized) {
            try {
                Set-DiagnosticStage 'export'
                $evidenceStage = 'export'
                $exportCode = Invoke-PrivateTool $xperf @('-i', (Join-Path $root 'trace.etl'),
                    '-o', (Join-Path $root 'exported.csv'), '-a', 'dumper') $root 'xperf' 30
                if ($exportCode -ne 0 -and $null -eq $firstFailureStage) { $firstFailureStage = 'export' }
                if ($exportCode -eq 0) {
                    Set-DiagnosticStage 'analysis'
                    $evidenceStage = 'analysis'
                    $code = Invoke-PrivateTool $python @('-m', 'scripts.fixture_startup_summary',
                        (Join-Path $root 'reader.exe'), $root) $root 'analysis' 30
                    $analysisPath = Join-Path $root 'analysis.json'
                    if ($code -in @(0, 2) -and (Test-Path -LiteralPath $analysisPath -PathType Leaf) -and
                        (Get-Item -LiteralPath $analysisPath).Length -le 65536) {
                        $analysis = Read-PublicAggregate $analysisPath $head
                        if (($code -eq 0) -ne ($analysis.status -ceq 'complete')) { throw 'Analyzer exit and report disagree.' }
                        $diagnosticExit = if ($code -eq 0) { 0 } else { 1 }
                    }
                    if (($null -eq $analysis -or $code -ne 0) -and $null -eq $firstFailureStage) { $firstFailureStage = 'analysis' }
                }
            } catch {
                if ($null -eq $firstFailureStage) { $firstFailureStage = $script:diagnosticStage }
                $analysis = $null
            }
        }
    } catch {
        if ($null -eq $firstFailureStage) { $firstFailureStage = $script:diagnosticStage }
        $diagnosticExit = 1
    }
    finally {
        Remove-Item Env:VERDICLIP_FIXTURE_TRACE_DIR -ErrorAction SilentlyContinue
        if (-not $retired) { $retired = Retire-OwnedTrace $state $root -CancelOnly }
        $nativeExited = -not $state.native_attempted -or $state.native_exited
        $removed = Remove-OwnedRawFiles $state $root $nativeExited
        Publish-NativeResult $state
        if ($retired -and $removed) { Set-DiagnosticStage 'lock_cleanup' }
        Close-NativeLock $lock $owner -Preserve:(-not ($retired -and $removed))
        if ($retired -and $removed) {
            Set-DiagnosticStage 'locator_cleanup'
            Remove-PrivateLocator $temporaryRoot $owner
            $locatorRemoved = $true
        }
    }
    # The in-memory aggregate is deliberately inaccessible until BOTH gates pass.
    if (Test-PublicationGate $retired ($removed -and $locatorRemoved) $analysis) {
        if ($analysis.status -ceq 'complete') { Set-DiagnosticStage 'complete' }
        else {
            Set-DiagnosticStage 'analysis'
            Write-Output 'FIXTURE_STARTUP_STATUS=incomplete; stage:analysis; reason:evidence_incomplete'
        }
        Write-Output ('FIXTURE_STARTUP_CONTEXT=source_sha:' + $head + '; repair_base_sha:' + $state.repair_base_sha)
        Write-Output ('FIXTURE_STARTUP_DIAGNOSTIC=' + ($analysis | ConvertTo-Json -Depth 12 -Compress))
    } else {
        if ($retired -and $removed -and $locatorRemoved) {
            if ($null -ne $firstFailureStage) { Set-DiagnosticStage $firstFailureStage }
            else { Set-DiagnosticStage $evidenceStage }
        }
        Write-Output ('FIXTURE_STARTUP_STATUS=unavailable; stage:' + $script:diagnosticStage + '; reason:verification_failed')
        Write-Output 'FIXTURE_STARTUP_DIAGNOSTIC=unavailable; no causal claim'
        $diagnosticExit = 1
    }
    if ($state.native_exit -ne 0) { return [int]$state.native_exit }
    return $diagnosticExit
}

if (-not $FunctionsOnly) {
    try {
        # Keep exit status separate from the deliberately bounded public output.
        $result = @(Invoke-FixtureDiagnostic)
        if ($result.Count -gt 1) { $result[0..($result.Count - 2)] | Write-Output }
        exit ([int]$result[-1])
    } catch {
        Write-Output ('FIXTURE_STARTUP_STATUS=withheld; stage:' + $script:diagnosticStage + '; reason:verification_failed')
        Write-Output 'FIXTURE_STARTUP_DIAGNOSTIC=withheld; owned cleanup or prerequisites unverified; no causal claim'
        exit 1
    }
}
