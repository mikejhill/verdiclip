# Pure policy, parser, source-vector and compile-only checks. No native calls.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'display_staging_policy.ps1')
$checks = 0
function Assert-Refused([scriptblock]$Action) {
    $refused = $false
    try { & $Action | Out-Null } catch { $refused = $true }
    if (-not $refused) { throw 'Expected fail-closed refusal.' }
    $script:checks++
}
if ((Get-StagingBudget 10 180000 15000) -ne 15000 -or
    (Get-StagingBudget 179000 180000 15000) -ne 1000) { throw 'Remaining deadline not enforced.' }
$checks++
Assert-Refused { Get-StagingBudget 180000 180000 15000 }
Assert-Refused { Get-StagingBudget -1 180000 15000 }
Assert-Refused { Get-StagingBudget 1 180000 0 }
Assert-StagingCompletion 5 10 'same-window-state' 'same-window-state' # fast exit still checked
$checks++
Assert-Refused { Assert-StagingCompletion 5 10 'baseline' 'late-window-change' }
Assert-Refused { Assert-StagingCompletion 10 10 'baseline' 'baseline' }
Assert-Refused { Assert-StagingCompletion 11 10 'baseline' 'baseline' }
Assert-Refused { Assert-StagingCompletion 1 10 '' '' }
$published = Get-PublishedPackages 'Published: OEM2.inf Original: mttvdd.inf; repeated oem2.inf'
if (($published -join ',') -cne 'oem2.inf') { throw 'Filename tokenization failed.' }
$checks++
if ((Get-NewStagedPackage @('oem1.inf') @('oem1.inf','oem2.inf')) -ne 'oem2.inf') { throw 'Exact addition refused.' }
$checks++
Assert-Refused { Get-NewStagedPackage @('oem1.inf') @('oem1.inf') }
Assert-Refused { Get-NewStagedPackage @('oem1.inf') @('oem2.inf') }
Assert-Refused { Get-NewStagedPackage @('oem1.inf') @('oem1.inf','oem2.inf','oem3.inf') }
Assert-Refused { Get-NewStagedPackage @() @('../oem2.inf') }
$fixture = @'
[Standard.NTamd64]
%DeviceName%=Install,Root\Fixture,Fixture\Compatible ; comment
[Strings]
ManufacturerName = "Fixture Provider"
'@
$parsed = Get-StagingInfIdentity $fixture
if (($parsed.ids -join '|') -ne 'FIXTURE\COMPATIBLE|ROOT\FIXTURE' -or $parsed.provider -ne 'Fixture Provider') {
    throw 'Pure INF identity parsing failed.'
}
$checks++
Assert-Refused { Get-StagingInfIdentity ($fixture.Replace('Root\Fixture','%Injected%')) }
Assert-Refused { Get-StagingInfIdentity ($fixture.Replace('Standard.NTamd64','Other.NTamd64')) }
Assert-Refused { Get-StagingInfIdentity ($fixture.Replace('ManufacturerName','MissingProvider')) }
$device = [pscustomobject]@{ hardware=@('Other');compatible=@('root\fixture');present=$false;class='AnyClass' }
Assert-Refused { Assert-NoStagingMatch @($device) @('ROOT\FIXTURE') }
Assert-Refused { Assert-NoStagingMatch @() @('ROOT\FIXTURE') }
Assert-NoStagingMatch @($device) @('Unrelated')
$checks++
$snapshot = [pscustomobject]@{devices=@('one');trust=@('root');display=@('monitor')}
Assert-StagingSnapshotEqual $snapshot $snapshot
$checks++
$receipt = Get-StagingSnapshotReceipt $snapshot
if ($receipt.devices_sha256 -cne (Get-StagingSnapshotReceipt $snapshot).devices_sha256 -or
    $receipt.devices_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Snapshot receipt unstable.' }
$checks++
foreach ($section in @('devices','trust','display')) {
    $changed = [pscustomobject]@{devices=@('one');trust=@('root');display=@('monitor')}
    $changed.$section = @('changed')
    Assert-Refused { Assert-StagingSnapshotEqual $snapshot $changed }
    if ((Get-StagingSnapshotReceipt $changed)[$section + '_sha256'] -ceq $receipt[$section + '_sha256']) {
        throw 'Changed snapshot hidden by receipt.'
    }
    $checks++
}
foreach ($file in @('test_display_staging.ps1','display_staging_inventory.ps1','inspect_display_package.ps1')) {
    $path = Join-Path $PSScriptRoot $file
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ('Syntax invalid: '+$file) }
    if ($file -eq 'test_display_staging.ps1') {
        $text=$ast.Extent.Text
        foreach ($forbidden in @("'/install'","'/force'","'/uninstall'","'/reboot'",'Import-Certificate','Set-DisplayConfig','Start-Process')) {
            if ($text.Contains($forbidden)) { throw ('Forbidden operation: '+$forbidden) }
        }
        foreach ($verb in @('/add-driver','/delete-driver')) {
            if ([regex]::Matches($text,[regex]::Escape("'"+$verb+"'")).Count -ne 1) { throw 'Operation count changed.' }
        }
        if (-not $text.Contains('$deadline = 180000L') -or -not $text.Contains('$deadline = 270000L') -or
            -not $text.Contains('$script:attempted = $true') -or -not $text.Contains('$child.StandardInput.Close()')) {
            throw 'Deadline/attempt/prompt safeguard missing.'
        }
        if (-not $text.Contains('$partialOperationUncertainty = $attempted -and -not $stageOutcomeObserved') -or
            -not $text.Contains('$cleanupVerified = $false')) { throw 'Interrupted staging must remain uncertain.' }
        $processHelper = $ast.Find({param($n)
            $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-StagingProcess'
        },$true).Extent.Text
        if ($processHelper.LastIndexOf('Assert-StagingCompletion') -le $processHelper.LastIndexOf('$output.Length') -or
            $processHelper.LastIndexOf('Assert-StagingCompletion') -ge $processHelper.LastIndexOf('return $output')) {
            throw 'Completion checks must follow output draining even after fast exit.'
        }
        $checks++
    }
    foreach ($call in @($ast.FindAll({param($n)
        $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Add-Type'
    },$true))) {
        # Compile repository C# constant definitions, NEVER invoke their methods.
        $definition=$call.CommandElements[-1]
        if ($definition -isnot [Management.Automation.Language.StringConstantExpressionAst]) { throw 'Unexpected dynamic C# source.' }
        Add-Type -TypeDefinition $definition.Value
        $checks++
    }
}
Write-Output ($checks.ToString()+' pure policy/static/compile-only checks passed; no native invocation.')
