# Pure predicates and cleanup orchestration; never touch a certificate store here.
Set-StrictMode -Version Latest
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
