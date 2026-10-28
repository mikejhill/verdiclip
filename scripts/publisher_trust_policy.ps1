# Pure predicates and cleanup orchestration; never touch a certificate store here.
Set-StrictMode -Version Latest
function Get-ExpectedPublisherSnapshot($Before) {
    # Model logical inheritance only. No CurrentUser store is opened or written here.
    $expected=ConvertFrom-Json ($Before | ConvertTo-Json -Depth 20) -Depth 20
    foreach($key in @('LocalMachine/TrustedPublisher','CurrentUser/TrustedPublisher')) {
        if($expected.trust.PSObject.Properties.Name -notcontains $key){throw 'Missing publisher view baseline.'}
        $current=$expected.trust.$key
        if($current -is [string]) {
            if($key -ceq 'LocalMachine/TrustedPublisher' -or $current -cne 'absent'){
                throw 'Invalid publisher baseline.'
            }
            $current=@() # An absent logical user view can expose the inherited machine leaf.
        }
        [string[]]$values=@(@($current)+'3CF8CF26D8BA266C3A483AB7D26D4A818E317D76' | Select-Object -Unique)
        [Array]::Sort($values,[StringComparer]::Ordinal)
        $expected.trust.$key=$values
    }
    return $expected
}
function Get-PublisherTrustDelta($Before,$After) {
    $keys=@(@($Before.PSObject.Properties.Name)+@($After.PSObject.Properties.Name) | Sort-Object -Unique)
    foreach($key in $keys) {
        $leftProperty=$Before.PSObject.Properties[$key];$rightProperty=$After.PSObject.Properties[$key]
        $left=$null;$right=$null
        if($null -ne $leftProperty){$left=$leftProperty.Value}
        if($null -ne $rightProperty){$right=$rightProperty.Value}
        $beforeJson=ConvertTo-Json -InputObject $left -Depth 4 -Compress
        $afterJson=ConvertTo-Json -InputObject $right -Depth 4 -Compress
        if($beforeJson -ceq $afterJson){continue}
        $beforeValues=@(if($null -ne $left -and $left -isnot [string]){$left})
        $afterValues=@(if($null -ne $right -and $right -isnot [string]){$right})
        $added=@($afterValues | Where-Object {$_ -notin $beforeValues})
        $removed=@($beforeValues | Where-Object {$_ -notin $afterValues})
        @{store=$key;added_count=$added.Count;removed_count=$removed.Count;
            pinned_added=('3CF8CF26D8BA266C3A483AB7D26D4A818E317D76' -cin $added);
            pinned_removed=('3CF8CF26D8BA266C3A483AB7D26D4A818E317D76' -cin $removed);
            before_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($beforeJson))).ToLowerInvariant();
            after_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($afterJson))).ToLowerInvariant()}
    }
}
function Get-PublisherIntermediateIndices([string]$Issuer,[object[]]$Certificates) {
    if(-not $Issuer -or $Certificates.Count -gt 16){throw 'Invalid embedded certificate bound/issuer.'}
    $visited=@{};$indices=[Collections.Generic.List[int]]::new()
    for($depth=0;$depth -lt 8;$depth++) {
        if($visited.ContainsKey($Issuer)){throw 'Embedded issuer cycle.'}
        $visited[$Issuer]=$true
        $matches=@(0..($Certificates.Count-1) | Where-Object {
            $Certificates.Count -gt 0 -and $Certificates[$_].subject -ceq $Issuer
        })
        if($matches.Count -eq 0){return ,$indices.ToArray()}
        if($matches.Count -ne 1){throw 'Ambiguous embedded issuer.'}
        $index=$matches[0];$row=$Certificates[$index]
        if(-not $row.ca -or -not $row.issuer){throw 'Embedded issuer is not a CA.'}
        # A self-issued certificate is never supplied as a trust anchor or ExtraStore input.
        # The platform must independently find and validate its system trust anchor.
        if($row.subject -ceq $row.issuer){return ,$indices.ToArray()}
        $indices.Add($index);$Issuer=$row.issuer
    }
    throw 'Embedded issuer depth exceeded.'
}
function Assert-PinnedPublisherIdentity($Identity) {
    if ($Identity.sha256 -cne '69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb' -or
        $Identity.thumbprint -cne '3CF8CF26D8BA266C3A483AB7D26D4A818E317D76' -or
        -not $Identity.code_signing_eku -or -not $Identity.end_entity -or
        -not $Identity.valid_now -or -not $Identity.authenticode_valid -or -not $Identity.chain_valid) {
        throw 'Exact publisher leaf, EKU, validity or chain verification failed.'
    }
}
function Invoke-PublisherRollback([scriptblock]$Remove, [scriptblock]$Verify) {
    # Verification runs independently even if Remove throws after changing state.
    $removeError=$null;$verifyError=$null;$verified=$false
    try { & $Remove | Out-Null } catch { $removeError=$_.Exception.Message }
    try { $verified=[bool](& $Verify) } catch { $verifyError=$_.Exception.Message }
    return @{remove_error=$removeError;verification_error=$verifyError;absent_verified=$verified}
}
