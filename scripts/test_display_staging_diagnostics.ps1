# Fake observations and policy states only. No native/driver/process operations.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'display_staging_policy.ps1')
. (Join-Path $PSScriptRoot 'display_staging_diagnostics.ps1')
$checks=0
function Refused([scriptblock]$Call) {
    $failed=$false;try { & $Call | Out-Null } catch { $failed=$true }
    if(-not $failed){throw 'Expected malformed observation refusal.'};$script:checks++
}
function Frame([string]$Foreground,[object[]]$Windows) {
    return [pscustomobject]@{foreground=$Foreground;windows=$Windows}
}
$row=[pscustomobject]@{handle='0x10';pid=10;class='synthetic-private-class'}
$before=Frame '0x10' @($row)
$same=Get-StagingWindowWitness $before $before
if($same.changed){throw 'Identical observations changed.'};$checks++
$roundTrip=$before | ConvertTo-Json -Depth 8 | ConvertFrom-Json
if((Get-StagingWindowWitness $before $roundTrip).changed){throw 'JSON integer shapes changed identity.'};$checks++
$normalized=Frame '0x010' @([pscustomobject]@{handle='0x010';pid=[uint32]10;class='synthetic-private-class'})
if((Get-StagingWindowWitness $before $normalized).changed){throw 'Equivalent handle spelling changed identity.'};$checks++
$focus=Get-StagingWindowWitness $before (Frame '0x0' @($row))
if(-not $focus.changed -or -not $focus.foreground_changed -or $focus.added_count){throw 'Focus-only delta lost.'};$checks++
$replacement=[pscustomobject]@{handle='0x10';pid=11;class='synthetic-private-class'}
$reuse=Get-StagingWindowWitness $before (Frame '0x10' @($replacement))
if($reuse.added_count -ne 1 -or $reuse.removed_count -ne 1){throw 'Handle reuse hidden.'};$checks++
$many=@(1..20 | ForEach-Object { [pscustomobject]@{handle=('0x{0:x}' -f ($_+32));pid=$_;class='private-title-like-data'} })
$bounded=Get-StagingWindowWitness (Frame '0x0' @()) (Frame '0x0' $many)
if(-not $bounded.truncated -or $bounded.added.Count -ne 16 -or $bounded.added_count -ne 20){throw 'Witness not bounded.'};$checks++
$encoded=ConvertTo-Json $bounded -Depth 8
if($encoded.Contains('private-title-like-data') -or $bounded.prompt_identity_established -or
    $bounded.transient_activity_excluded){throw 'Witness overclaims identity or leaks raw class.'};$checks++
Refused { Get-StagingWindowWitness $null $before }
Refused { Get-StagingWindowWitness $before (Frame 'bad' @()) }
Refused { Get-StagingWindowWitness $before (Frame '0x0' @($row,$row)) }
Refused { Get-StagingWindowWitness $before (Frame '0x0' @([pscustomobject]@{handle='0x1';pid=-1;class='x'})) }
Refused { Get-StagingWindowWitness $before $before 17 }
foreach($pidValue in @($true,'10',10.0,4294967296L)) {
    Refused { Get-StagingWindowWitness $before (Frame '0x0' @([pscustomobject]@{handle='0x1';pid=$pidValue;class='x'})) }
}
Refused { Get-StagingWindowWitness $before (Frame '0x0' @([pscustomobject]@{handle='0x0';pid=1;class='x'})) }
Refused { Get-StagingWindowWitness $before (Frame '0x0' @([pscustomobject]@{handle='0x1';pid=1;class=([string][char]0xD800)})) }
$safe=@{StageStarted=$true;StageOutcomeObserved=$true;GuardEverTripped=$false;
    AdmissionEverFailed=$false;
    BaselineKnown=$true;IdentityVerified=$true;BindingsStable=$true;TrustStable=$true;
    DisplayStable=$true;RemainingMilliseconds=60000}
$safePlan=Get-StagingPostmortemPlan @safe
if(-not $safePlan.permit_exact_package_removal -or $safePlan.cleanup_success_established){throw 'Attempt permission is not cleanup proof.'};$checks++
foreach($field in @('StageOutcomeObserved','BaselineKnown','IdentityVerified','BindingsStable','TrustStable','DisplayStable')) {
    $scenario=$safe.Clone();$scenario[$field]=$false;$plan=Get-StagingPostmortemPlan @scenario
    if($plan.permit_exact_package_removal -or -not $plan.collect_final_snapshot -or
        -not $plan.collect_package_inventory){throw 'Read-only collection coupled to privileged cleanup.'};$checks++
}
$guard=$safe.Clone();$guard.GuardEverTripped=$true
$latched=Get-StagingPostmortemPlan @guard
if($latched.permit_exact_package_removal -or -not $latched.admission_failure_latched -or
    -not $latched.collect_final_snapshot -or -not $latched.forbid_cleanup_success_claim){throw 'Later baseline restoration must not clear guard event.'};$checks++
$priorFailure=$safe.Clone();$priorFailure.AdmissionEverFailed=$true
$priorPlan=Get-StagingPostmortemPlan @priorFailure
if($priorPlan.permit_exact_package_removal -or -not $priorPlan.admission_failure_latched -or
    -not $priorPlan.forbid_cleanup_success_claim){throw 'Later identity recovery must not erase prior admission failure.'};$checks++
foreach($history in @('AdmissionEverFailed','GuardEverTripped')) {
    $missingHistory=$safe.Clone();$missingHistory.Remove($history)
    if((Get-StagingPostmortemPlan @missingHistory).permit_exact_package_removal){throw 'Omitted history must fail closed.'};$checks++
}
$interrupted=$guard.Clone();$interrupted.StageOutcomeObserved=$false
$uncertain=Get-StagingPostmortemPlan @interrupted
if(-not $uncertain.forbid_cleanup_success_claim -or $uncertain.stage_or_retry_permitted -or
    $uncertain.answer_prompt_permitted){throw 'Interrupted outcome must remain uncertain.'};$checks++
foreach($remaining in @(-1,0,5000)) {
    $expired=$safe.Clone();$expired.RemainingMilliseconds=$remaining;$plan=Get-StagingPostmortemPlan @expired
    if($plan.collect_final_snapshot -or $plan.collect_package_inventory -or $plan.permit_exact_package_removal){throw 'Expired budget permits work.'};$checks++
}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'display_staging_diagnostics.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Prototype syntax invalid.'}
$text=$ast.Extent.Text
foreach($forbidden in @('DllImport','Start-Process','ProcessStartInfo','pnputil','Import-Certificate','Get-AuthenticodeSignature')) {
    if($text.Contains($forbidden)){throw 'Pure policy prototype contains executor operation.'}
}
$checks++
$script:snapshotRead=$false
$observed=Invoke-StagingObservations { throw 'synthetic package failure' } {
    $script:snapshotRead=$true; return [pscustomobject]@{devices=@('fixture')}
}
if (-not $snapshotRead -or $observed.package_error -cne 'synthetic package failure' -or
    $null -eq $observed.snapshot -or $observed.snapshot_error) { throw 'Package failure suppressed final snapshot.' };$checks++
$observed=Invoke-StagingObservations { 'synthetic packages' } { throw 'synthetic snapshot failure' }
if ($observed.packages -cne 'synthetic packages' -or $observed.snapshot_error -cne 'synthetic snapshot failure' -or
    $observed.package_error -or $null -ne $observed.snapshot) { throw 'Snapshot failure suppressed package evidence.' };$checks++
$observed=Invoke-StagingObservations { throw 'package' } { throw 'snapshot' }
if ($observed.package_error -cne 'package' -or $observed.snapshot_error -cne 'snapshot') { throw 'Independent failures lost.' };$checks++
$hostFixture='C:/fixture/pwsh.exe';$pnpFixture='C:/fixture/pnp.exe';$inventoryFixture='C:/fixture/inventory.ps1'
Assert-StagingObservationCommand 'package-inventory' $pnpFixture @('/enum-drivers','/files') $hostFixture $pnpFixture $inventoryFixture
$checks++
Assert-StagingObservationCommand 'inventory' $hostFixture @('-NoProfile','-NonInteractive','-File',$inventoryFixture) $hostFixture $pnpFixture $inventoryFixture
$checks++
foreach($bad in @(
    @{label='one-staging-attempt';exe=$pnpFixture;argv=@('/add-driver','fixture.inf')},
    @{label='package-inventory';exe=$pnpFixture;argv=@('/add-driver','fixture.inf')},
    @{label='package-inventory';exe=$pnpFixture;argv=@('/enum-drivers','/files','/delete-driver')},
    @{label='package-inventory';exe=$hostFixture;argv=@('/enum-drivers','/files')},
    @{label='inventory';exe=$hostFixture;argv=@('-NoProfile','-NonInteractive','-File','other.ps1')},
    @{label='inventory';exe=$hostFixture;argv=@('-NoProfile','-NonInteractive','-Command','unsafe')}
)) {
    Refused { Assert-StagingObservationCommand $bad.label $bad.exe $bad.argv $hostFixture $pnpFixture $inventoryFixture }
}
$caption=Get-StagingCaptionClassification ' Windows Security '
if($caption.category -cne 'windows-security' -or $caption.prompt_answered -or $caption.purpose_proven){throw 'Caption overclaim/classification.'};$checks++
foreach($known in @(@('Security Warning','security-warning'),@('User Account Control','uac'))) {
    if((Get-StagingCaptionClassification $known[0]).category -cne $known[1]){throw 'Known caption category lost.'};$checks++
}
$secretTitle='synthetic-private-title-token'
$caption=Get-StagingCaptionClassification $secretTitle
if($caption.category -cne 'unclassified' -or ($caption|ConvertTo-Json).Contains($secretTitle) -or
    $caption.sha256 -notmatch '^[a-f0-9]{64}$'){throw 'Caption leaked raw text or lacks digest.'};$checks++
Refused { Get-StagingCaptionClassification ('x'*513) }
$absent=Get-StagingPackageObservation 'fixture' 10 $null
$empty=Get-StagingPackageObservation 'fixture' 11 ''
if($absent.known -or -not $empty.known -or $empty.names.Count -ne 0){throw 'Missing and empty package evidence conflated.'};$checks++
$baseline=Get-StagingPackageObservation 'baseline' 12 'oem2.inf oem1.inf oem2.inf'
$reordered=Get-StagingPackageObservation 'baseline' 13 'oem1.inf oem2.inf'
$added=Get-StagingPackageObservation 'final' 14 'oem1.inf oem2.inf oem3.inf MttVDD.inf'
if(($baseline.names -join '|') -cne 'oem1.inf|oem2.inf' -or $baseline.sha256 -cne $reordered.sha256 -or
    $baseline.sha256 -ceq $added.sha256 -or $baseline.pinned_inf_mentioned -or -not $added.pinned_inf_mentioned){throw 'Package receipt failed to distinguish retention candidate.'};$checks++
Refused { Get-StagingPackageObservation 'bad' 0 @('oem1.inf') }
$log=@'
>>> [Other import - private-fixture.inf]
!!! user cancelled untrusted publisher 0x11111111
>>> [Driver Package Import - MttVDD.inf]
!!! sig: publisher is not trusted. Dialog prompt. 0x800b0109
<<< [Exit status: FAILURE(0x800b0109)]
'@
$classified=Get-StagingSetupLogClassification $log
if($classified.relevant_sections -ne 1 -or -not $classified.mentions_untrusted -or
    -not $classified.mentions_prompt -or -not $classified.mentions_publisher -or $classified.mentions_user_cancel -or
    ($classified.hex_tokens -join '|') -cne '0x800b0109' -or $classified.policy_outcome_proven -or
    ($classified|ConvertTo-Json).Contains('private-fixture')){throw 'SetupAPI section scope/classification failed.'};$checks++
$none=Get-StagingSetupLogClassification 'unrelated private-text 0x12345678'
if($none.relevant_sections -ne 0 -or $none.hex_tokens.Count -ne 0 -or $none.mentions_prompt){throw 'Unrelated log became package evidence.'};$checks++
Refused { Get-StagingSetupLogClassification ('x'*262145) }
$checkpoint=@{present=$true;length=100L;identity='file-one';anchor='anchor-one'}
Assert-StagingLogContinuity $checkpoint 'file-one' 120 'anchor-one';$checks++
Refused { Assert-StagingLogContinuity $checkpoint 'file-two' 120 'anchor-one' }
Refused { Assert-StagingLogContinuity $checkpoint 'file-two' 100 'anchor-one' }
Refused { Assert-StagingLogContinuity $checkpoint 'file-one' 120 'anchor-two' }
Refused { Assert-StagingLogContinuity $checkpoint 'file-one' 99 'anchor-one' }
Refused { Assert-StagingLogContinuity $null 'file-one' 120 'anchor-one' }
Assert-StagingLogContinuity @{present=$false;length=0L} 'new-file' 10 'unused';$checks++
Write-Output ($checks.ToString()+' pure diagnostic-policy checks passed; no desktop/native/driver invocation.')
