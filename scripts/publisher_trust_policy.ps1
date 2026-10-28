# Pure predicates and cleanup orchestration; never touch a certificate store here.
Set-StrictMode -Version Latest
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
