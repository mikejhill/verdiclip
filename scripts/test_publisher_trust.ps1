# Pure predicates, mocked cleanup and AST checks. No certificate store/native invocation.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'publisher_trust_policy.ps1')
$checks=0
$embeddedFixture=@(
    @{subject='leaf';issuer='intermediate';ca=$false},
    @{subject='root';issuer='root';ca=$true},
    @{subject='unrelated';issuer='other-root';ca=$true},
    @{subject='intermediate';issuer='root';ca=$true}
)
$indices=Get-PublisherIntermediateIndices 'intermediate' $embeddedFixture
if($indices.Count -ne 1 -or $indices[0] -ne 3){throw 'Intermediate context included a root/unrelated/leaf certificate.'};$checks++
foreach($issuer in @('root','missing')){
    if((Get-PublisherIntermediateIndices $issuer $embeddedFixture).Count -ne 0){throw 'Root or absent issuer became extra trust input.'};$checks++
}
if((Get-PublisherIntermediateIndices 'missing' @()).Count -ne 0){throw 'Empty context mishandled.'};$checks++
$longChain=@(for($i=0;$i -lt 9;$i++){@{subject=('ca'+$i);issuer=('ca'+($i+1));ca=$true}})
foreach($bad in @(
    @{issuer='leaf';rows=$embeddedFixture},
    @{issuer='intermediate';rows=@($embeddedFixture[3],$embeddedFixture[3])},
    @{issuer='a';rows=@(@{subject='a';issuer='b';ca=$true},@{subject='b';issuer='a';ca=$true})},
    @{issuer='ca0';rows=$longChain},
    @{issuer='';rows=$embeddedFixture},
    @{issuer='a';rows=@(1..17 | ForEach-Object {@{subject='a';issuer='b';ca=$true}})}
)){
    $refused=$false;try{Get-PublisherIntermediateIndices $bad.issuer $bad.rows | Out-Null}catch{$refused=$true}
    if(-not $refused){throw 'Unsafe or ambiguous embedded chain context admitted.'};$checks++
}
$valid=@{sha256='69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb';
    thumbprint='3CF8CF26D8BA266C3A483AB7D26D4A818E317D76';code_signing_eku=$true;
    end_entity=$true;valid_now=$true;authenticode_valid=$true;chain_valid=$true}
Assert-PinnedPublisherIdentity $valid;$checks++
foreach($key in $valid.Keys){
    $invalid=$valid.Clone()
    if($invalid[$key] -is [string]){$invalid[$key]='wrong'}else{$invalid[$key]=$false}
    $refused=$false;try{Assert-PinnedPublisherIdentity $invalid}catch{$refused=$true}
    if(-not $refused){throw ('Verification accepted bad '+$key)};$checks++
}
foreach($removeFails in @($false,$true)){
    foreach($verifyFails in @($false,$true)){
        $script:verifiedCalled=$false
        $result=Invoke-PublisherRollback {if($removeFails){throw 'mock-remove'}} {
            $script:verifiedCalled=$true;if($verifyFails){throw 'mock-verify'};return $true
        }
        if(-not $verifiedCalled -or [bool]$result.remove_error -ne $removeFails -or
            [bool]$result.verification_error -ne $verifyFails -or $result.absent_verified -eq $verifyFails){
            throw 'Cleanup failure suppressed independent verification or misreported outcome.'
        };$checks++
    }
}
$result=Invoke-PublisherRollback {} {$false}
if($result.absent_verified){throw 'Retained leaf reported absent.'};$checks++
# Load function definitions only. Replace every store-facing dependency before exercising Add.
. (Join-Path $PSScriptRoot 'publisher_trust_operations.ps1')
function Open-PinnedPublisherStore([bool]$Write){
    if($scenario -eq 'open-failure'){throw 'mock-open'};return $fakeStore
}
function Read-PinnedPublisherPresent {return $scenario -eq 'inner-present'}
$fakeStore=[pscustomobject]@{}
$fakeStore | Add-Member ScriptMethod Dispose {}
$fakeStore | Add-Member ScriptMethod Add {
    param($Leaf)
    $script:fakeWrites++
    if($scenario -eq 'write-then-throw'){throw 'mock-partial-write'}
}
foreach($scenario in @('inner-present','open-failure','write-then-throw','success')){
    $script:claimed=$false;$script:fakeWrites=0;$script:fakeRemoves=0
    try { Add-OwnedPublisherLeaf 'public-fixture' {$script:claimed=$true} } catch {}
    finally { if($claimed){$script:fakeRemoves++} }
    $expected=$scenario -in @('write-then-throw','success')
    if($claimed -ne $expected -or $fakeWrites -ne [int]$expected -or $fakeRemoves -ne [int]$expected){
        throw ('Write ownership/independent rollback incorrect: '+$scenario)
    };$checks++
}
foreach($name in @('publisher_trust_policy.ps1','publisher_trust_operations.ps1','test_display_staging.ps1')){
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$tokens,[ref]$errors)
    if($errors.Count){throw ('Invalid syntax: '+$name)};$checks++
    $text=$ast.Extent.Text
    foreach($forbidden in @('Import-Certificate','certutil','CurrentUser','TrustedPeople','Set-ItemProperty',
        'Accept-Prompt','SendInput',"'/install'","'/force'","'/reboot'")){
        if($text.Contains($forbidden)){throw ('Out-of-scope operation: '+$forbidden)}
    }
    if($name -eq 'publisher_trust_operations.ps1'){
        foreach($required in @("::new('TrustedPublisher','LocalMachine')",'OpenExistingOnly',
            'DisableCertificateDownloads=$true','X509RevocationMode]::Offline',
            'X509ChainTrustMode]::System','ExtraStore.Add($embedded[$index])',
            'Get-PublisherIntermediateIndices','embedded_intermediate_sha256=$inputHashes',
            'Assert-PinnedPublisherIdentity $identity','Publisher already present immediately before add.')){
            if(-not $text.Contains($required)){throw ('Required safety check missing: '+$required)}
        }
        foreach($forbidden in @('CustomRootTrust','CustomTrustStore','X509RevocationMode]::NoCheck',
            'X509RevocationMode]::Online','IgnoreEndRevocationUnknown','IgnoreCertificateAuthorityRevocationUnknown')){
            if($text.Contains($forbidden)){throw ('Verification policy weakened: '+$forbidden)}
        };$checks++
        if([regex]::Matches($text,'\.Add\(\$Leaf\)').Count -ne 1 -or
            [regex]::Matches($text,'\.Remove\(\$Leaf\)').Count -ne 1){throw 'Trust mutation count changed.'};$checks++
    }
    if($name -eq 'test_display_staging.ps1'){
        if(-not $text.Contains('Add-OwnedPublisherLeaf $publisherLeaf { $script:publisherAddAttempted=$true }') -or
            $text.IndexOf('Read-VerifiedPublisherLeaf (') -ge $text.IndexOf('Add-OwnedPublisherLeaf $publisherLeaf') -or
            -not $text.Contains('if($publisherTrustRequested){$deadline=240000L}') -or
            -not $text.Contains('publisher_rollback=$publisherRollback')){throw 'Trust ownership/reserve/receipt safeguard missing.'};$checks++
        $finalizers=@($ast.FindAll({param($node)
            $node -is [Management.Automation.Language.TryStatementAst] -and
            $null -ne $node.Finally -and $node.Finally.Extent.Text.Contains('Invoke-PublisherRollback')
        },$true))
        if($finalizers.Count -lt 1){throw 'Certificate cleanup not in unconditional finally.'};$checks++
    }
}
$workflow=Get-Content -Raw (Join-Path $PSScriptRoot '../.github/workflows/ci.yml')
if(-not $workflow.Contains('publisher_trust_staging:') -or -not $workflow.Contains('./scripts/test_publisher_trust.ps1')){
    throw 'Explicit opt-in or pure policy preflight missing.'
};$checks++
function Test-DispatchCondition([string]$Condition,$Flags){
    # Evaluate this repository's small boolean-only gate grammar, never arbitrary commands.
    $expression=$Condition.Replace('github.event_name',"'workflow_dispatch'")
    $expression=[regex]::Replace($expression,'inputs\.([a-z_]+)',{
        param($match)
        if($Flags[$match.Groups[1].Value]){return '$true'}else{return '$false'}
    })
    $expression=$expression.Replace('!=',' -ne ').Replace('==',' -eq ').Replace('&&',' -and ').Replace('||',' -or ')
    $expression=$expression.Replace('always()','$true').Replace(' true',' $true')
    $expression=[regex]::Replace($expression,'!(?!=)',' -not ')
    $withoutAtoms=$expression.Replace("'workflow_dispatch'",'').Replace("'pull_request'",'').Replace("'merge_group'",'')
    $residual=[regex]::Replace($withoutAtoms,'\s+|[()]|\$(?:true|false)\b|-(?:and|or|eq|ne|not)\b','')
    if($residual.Length){throw 'Unexpected gate grammar.'}
    return [bool](& ([scriptblock]::Create($expression)))
}
$script:unexpectedGateCalled=$false
function Test-UnexpectedGateCommand {$script:unexpectedGateCalled=$true;return $true}
$refused=$false
try {Test-DispatchCondition 'Test-UnexpectedGateCommand' @{}} catch {$refused=$true}
if(-not $refused -or $unexpectedGateCalled){throw 'Gate evaluator admitted a command instead of boolean tokens.'};$checks++
foreach($workflowVariant in @($workflow.Replace("`r`n","`n"),$workflow.Replace("`r`n","`n").Replace("`n","`r`n"))){
$jobs=[regex]::Matches($workflowVariant,'(?ms)^  ([a-z][a-z0-9-]+):\r?\n(.*?)(?=^  [a-z][a-z0-9-]+:\r?\n|\z)')
foreach($job in $jobs){
    $body=$job.Groups[2].Value
    if($body -notmatch '(?m)^    if: (.+)$'){continue}
    $condition=$Matches[1].Trim()
    if($job.Groups[1].Value -eq 'driver-staging'){
        if(-not $condition.Contains('&& inputs.driver_store_staging &&') -or
            $condition.Contains('inputs.publisher_trust_staging != true') -or
            -not $body.Contains('timeout-minutes: 5') -or $body.Contains('upload-artifact') -or
            $body.Contains('cache') -or -not $body.Contains('contents: read')){throw 'Staging job scope changed.'}
    }elseif($condition -cne "github.event_name == 'pull_request'" -and
        -not $condition.Contains('inputs.publisher_trust_staging != true')){throw 'Publisher flag can run another job.'}
    $checks++
    # Publisher on plus every combination of the five other flags (32 cases per job).
    $otherFlags=@('rdp_prerequisites_only','desktop_capability_only','owned_window_canary',
        'driver_signature_preflight','driver_store_staging')
    for($mask=0;$mask -lt 32;$mask++){
        $flags=@{publisher_trust_staging=$true}
        for($bit=0;$bit -lt 5;$bit++){$flags[$otherFlags[$bit]]=($mask -band (1 -shl $bit)) -ne 0}
        $expected=$job.Groups[1].Value -eq 'driver-staging' -and $mask -eq 16
        if((Test-DispatchCondition $condition $flags) -ne $expected){throw ('Wrong publisher flag matrix: '+$job.Groups[1].Value+' mask '+$mask)}
        $checks++
    }
}
}
Write-Output ($checks.ToString()+' pure publisher identity/rollback/static checks passed; no store/native invocation.')
