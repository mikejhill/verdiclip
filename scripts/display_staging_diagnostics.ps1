# Pure diagnostic policy. Native reads and privileged actions stay in the executor.
Set-StrictMode -Version Latest
function Get-StagingPostmortemPlan(
    [bool]$StageStarted, [bool]$StageOutcomeObserved, [bool]$GuardEverTripped=$true,
    [bool]$AdmissionEverFailed=$true,
    [bool]$BaselineKnown, [bool]$IdentityVerified, [bool]$BindingsStable,
    [bool]$TrustStable, [bool]$DisplayStable, [long]$RemainingMilliseconds
) {
    # The executor must recompute this before EACH operation. These are not leases.
    $budgetAvailable = $RemainingMilliseconds -gt 5000
    $uncertain = $StageStarted -and -not $StageOutcomeObserved
    $failureLatched = $GuardEverTripped -or $AdmissionEverFailed -or $uncertain
    return [ordered]@{
        collect_package_inventory=$budgetAvailable
        collect_final_snapshot=$budgetAvailable
        permit_exact_package_removal=($budgetAvailable -and $StageStarted -and
            $StageOutcomeObserved -and -not $failureLatched -and $BaselineKnown -and
            $IdentityVerified -and $BindingsStable -and $TrustStable -and $DisplayStable)
        admission_failure_latched=$failureLatched
        forbid_cleanup_success_claim=$failureLatched
        cleanup_success_established=$false # a plan is never post-removal evidence
        stage_or_retry_permitted=$false
        answer_prompt_permitted=$false
    }
}
function Invoke-StagingObservations([scriptblock]$ReadPackages, [scriptblock]$ReadSnapshot) {
    # Independent reads: a failed package query must not suppress the final snapshot.
    $result = [ordered]@{packages=$null;snapshot=$null;package_error=$null;snapshot_error=$null}
    try { $result.packages = & $ReadPackages } catch { $result.package_error = $_.Exception.Message }
    try { $result.snapshot = & $ReadSnapshot } catch { $result.snapshot_error = $_.Exception.Message }
    return $result
}
function Get-StagingCaptionClassification([string]$Caption) {
    if ($Caption.Length -gt 512) { throw 'Caption bound exceeded.' }
    # Emit only fixed classifications and a hash, never raw titles/control text.
    $normalized = $Caption.Trim().Replace('&','').ToLowerInvariant()
    $category = switch -Exact ($normalized) {
        'windows security' { 'windows-security'; break }
        'security warning' { 'security-warning'; break }
        'user account control' { 'uac'; break }
        default { 'unclassified' }
    }
    return [ordered]@{category=$category;characters=$Caption.Length;
        sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
            [Text.UTF8Encoding]::new($false,$true).GetBytes($Caption))).ToLowerInvariant();
        prompt_answered=$false;purpose_proven=$false}
}
function Get-StagingPackageObservation([string]$Phase,[long]$Elapsed,$Text) {
    if ($null -eq $Text) { return [ordered]@{phase=$Phase;elapsed_ms=$Elapsed;known=$false;names=@();sha256=$null} }
    if ($Text -isnot [string]) { throw 'Invalid package observation text.' }
    $names=@(Get-PublishedPackages $Text)
    $canonical=$names -join "`n"
    return [ordered]@{phase=$Phase;elapsed_ms=$Elapsed;known=$true;names=$names;
        pinned_inf_mentioned=($Text -match '(?i)\bmttvdd\.inf\b');
        sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
            [Text.Encoding]::UTF8.GetBytes($canonical))).ToLowerInvariant()}
}
function Get-StagingSetupLogClassification([string]$Text) {
    if ($Text.Length -gt 262144) { throw 'SetupAPI diagnostic input bound exceeded.' }
    # Classify only sections mentioning the pinned INF. Do not emit arbitrary log lines.
    $sections=@([regex]::Split($Text,'(?m)(?=^>>>\s+\[)') | Where-Object {$_ -match '(?i)\bmttvdd\.inf\b'})
    $matched=$sections -join "`n"
    $codes=@([regex]::Matches($matched,'(?i)\b0x[0-9a-f]{8}\b') | ForEach-Object {$_.Value.ToLowerInvariant()} | Sort-Object -Unique | Select-Object -First 32)
    return [ordered]@{relevant_sections=$sections.Count;hex_tokens=$codes;
        mentions_untrusted=($matched -match '(?i)untrusted|not trusted');
        mentions_publisher=($matched -match '(?i)publisher');
        mentions_prompt=($matched -match '(?i)prompt|dialog');
        mentions_user_cancel=($matched -match '(?i)user.*cancel|cancel.*user');
        relevant_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
            [Text.Encoding]::UTF8.GetBytes($matched))).ToLowerInvariant();
        policy_outcome_proven=$false}
}
function Assert-StagingLogContinuity($Checkpoint,[string]$Identity,[long]$Length,[string]$Anchor) {
    if ($null -eq $Checkpoint -or -not $Identity -or $Length -lt $Checkpoint.length) {
        throw 'Missing or truncated log continuity evidence.'
    }
    if ($Checkpoint.present -and ($Identity -cne $Checkpoint.identity -or $Anchor -cne $Checkpoint.anchor)) {
        throw 'Log file identity or baseline anchor changed.'
    }
}
function Assert-StagingObservationCommand([string]$Label,[string]$Executable,[string[]]$Arguments,
    [string]$HostPath,[string]$PnPPath,[string]$InventoryPath) {
    if ($Label -ceq 'package-inventory') { $expectedExecutable=$PnPPath; $expectedArguments=@('/enum-drivers','/files') }
    elseif ($Label -ceq 'inventory') {
        $expectedExecutable=$HostPath; $expectedArguments=@('-NoProfile','-NonInteractive','-File',$InventoryPath)
    } else { throw 'Observation mode is restricted to the two read-only commands.' }
    if (-not $Executable -or $Executable -cne $expectedExecutable -or $Arguments.Count -ne $expectedArguments.Count) {
        throw 'Unexpected observation executable/argument count.'
    }
    for ($index=0;$index -lt $expectedArguments.Count;$index++) {
        if ($Arguments[$index] -cne $expectedArguments[$index]) { throw 'Unexpected observation argument.' }
    }
}
function ConvertTo-StagingHandle([string]$Handle) {
    if ($Handle -notmatch '^0x[0-9a-fA-F]{1,16}$') { throw 'Invalid window handle.' }
    $number=[UInt64]::Parse($Handle.Substring(2),[Globalization.NumberStyles]::AllowHexSpecifier,
        [Globalization.CultureInfo]::InvariantCulture)
    return '0x'+$number.ToString('x',[Globalization.CultureInfo]::InvariantCulture)
}
function ConvertTo-StagingWindowMap($Frame) {
    if ($null -eq $Frame) {
        throw 'Missing or invalid window observation.'
    }
    ConvertTo-StagingHandle $Frame.foreground | Out-Null
    $windows = @($Frame.windows)
    if ($windows.Count -gt 1024) { throw 'Window observation bound exceeded.' }
    $result = @{}
    foreach ($window in $windows) {
        $integerPid=$window.pid -is [int] -or $window.pid -is [uint32] -or $window.pid -is [long]
        if (-not $integerPid -or $window.pid -le 0 -or $window.pid -gt 4294967295L -or
            $window.class -isnot [string] -or $window.class.Length -eq 0 -or $window.class.Length -gt 256) {
            throw 'Invalid window row.'
        }
        $handle = ConvertTo-StagingHandle $window.handle
        if ($handle -eq '0x0') { throw 'Visible window cannot have a null handle.' }
        if ($result.ContainsKey($handle)) { throw 'Ambiguous duplicate window handle.' }
        # Retain no titles, command lines, screenshots or raw class names.
        $bytes = [Text.UTF8Encoding]::new($false,$true).GetBytes($window.class)
        $hash = [Security.Cryptography.SHA256]::HashData($bytes)
        $result[$handle] = [ordered]@{handle=$handle;pid=[long]$window.pid;
            class_sha256=[Convert]::ToHexString($hash).ToLowerInvariant()}
    }
    return $result
}
function Get-StagingWindowWitness($Before, $After, [int]$Limit=16) {
    if ($Limit -lt 1 -or $Limit -gt 16) { throw 'Invalid witness bound.' }
    $left = ConvertTo-StagingWindowMap $Before
    $right = ConvertTo-StagingWindowMap $After
    $added = @(); $removed = @()
    foreach ($handle in @($left.Keys | Sort-Object)) {
        if (-not $right.ContainsKey($handle) -or
            (ConvertTo-Json $left[$handle] -Compress) -cne (ConvertTo-Json $right[$handle] -Compress)) {
            $removed += $left[$handle]
        }
    }
    foreach ($handle in @($right.Keys | Sort-Object)) {
        if (-not $left.ContainsKey($handle) -or
            (ConvertTo-Json $right[$handle] -Compress) -cne (ConvertTo-Json $left[$handle] -Compress)) {
            $added += $right[$handle]
        }
    }
    $beforeForeground=ConvertTo-StagingHandle $Before.foreground
    $afterForeground=ConvertTo-StagingHandle $After.foreground
    $foregroundChanged = $beforeForeground -cne $afterForeground
    return [ordered]@{
        changed=($foregroundChanged -or $added.Count -gt 0 -or $removed.Count -gt 0)
        foreground_changed=$foregroundChanged
        before_foreground=$beforeForeground;after_foreground=$afterForeground
        added_count=$added.Count;removed_count=$removed.Count
        added=@($added | Select-Object -First $Limit);removed=@($removed | Select-Object -First $Limit)
        truncated=($added.Count -gt $Limit -or $removed.Count -gt $Limit)
        prompt_identity_established=$false
        transient_activity_excluded=$false
    }
}
