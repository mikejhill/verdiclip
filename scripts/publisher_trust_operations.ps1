# Called only inside the hosted staging executor's outer cleanup ownership.
# No Root/CA stores, elevation, prompts, certificate downloads or private keys.
function Open-PinnedPublisherStore([bool]$Write) {
    $store=[Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher','LocalMachine')
    try {
        $flags=[Security.Cryptography.X509Certificates.OpenFlags]::OpenExistingOnly
        if($Write){$flags=$flags -bor [Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite}
        else{$flags=$flags -bor [Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly}
        $store.Open($flags);return $store
    } catch {$store.Dispose();throw}
}
function Read-PinnedPublisherPresent {
    $store=Open-PinnedPublisherStore $false
    try {
        $found=$false
        foreach($cert in $store.Certificates){
            try {
                if($cert.Thumbprint -ceq '3CF8CF26D8BA266C3A483AB7D26D4A818E317D76') {
                    $hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($cert.RawData)).ToLowerInvariant()
                    if($hash -cne '69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb'){
                        throw 'Thumbprint collision or mismatched publisher data.'
                    }
                    $found=$true
                }
            } finally {$cert.Dispose()}
        }
        return $found
    } finally {$store.Dispose()}
}
function Read-VerifiedPublisherLeaf([string]$Catalog) {
    if([IO.FileInfo]::new($Catalog).Length -gt 1048576){throw 'Pinned catalog size exceeded.'}
    $catalogBytes=[IO.File]::ReadAllBytes($Catalog)
    if($catalogBytes.Length -gt 1048576 -or
        [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($catalogBytes)).ToLowerInvariant() -cne
        '08a0093fc9b2e32b287a6f8a77ca4de0a31830d29fc33d2b13a918dc859468f6'){
        throw 'Pinned catalog data mismatch.'
    }
    $signature=Get-AuthenticodeSignature -LiteralPath $Catalog
    $leaf=$signature.SignerCertificate
    if($null -eq $leaf){throw 'Catalog signer absent.'}
    $chain=[Security.Cryptography.X509Certificates.X509Chain]::new()
    $embedded=@()
    try {
        Add-Type -AssemblyName System.Security.Cryptography.Pkcs
        $cms=[Security.Cryptography.Pkcs.SignedCms]::new()
        $cms.Decode($catalogBytes) # Parse public certificates; no store import or downloaded code execution.
        $embedded=@($cms.Certificates)
        if($embedded.Count -gt 16){throw 'Embedded certificate count exceeded.'}
        $rows=@($embedded | ForEach-Object {
            $constraints=@($_.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' })
            @{subject=[Convert]::ToHexString($_.SubjectName.RawData);issuer=[Convert]::ToHexString($_.IssuerName.RawData);
                ca=($constraints.Count -eq 1 -and $constraints[0].CertificateAuthority)}
        })
        $intermediates=Get-PublisherIntermediateIndices ([Convert]::ToHexString($leaf.IssuerName.RawData)) $rows
        $inputHashes=@(foreach($index in $intermediates){
            # ExtraStore is only untrusted chain material. Self-issued roots are excluded above.
            $chain.ChainPolicy.ExtraStore.Add($embedded[$index]) | Out-Null
            [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($embedded[$index].RawData)).ToLowerInvariant()
        })
        # Offline, fail closed on unavailable revocation evidence; never fetch/import certificates.
        $chain.ChainPolicy.TrustMode=[Security.Cryptography.X509Certificates.X509ChainTrustMode]::System
        $chain.ChainPolicy.DisableCertificateDownloads=$true
        $chain.ChainPolicy.RevocationMode=[Security.Cryptography.X509Certificates.X509RevocationMode]::Offline
        $chain.ChainPolicy.VerificationFlags=[Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        $chain.ChainPolicy.UrlRetrievalTimeout=[TimeSpan]::FromSeconds(2)
        $chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.3')) | Out-Null
        $eku=@($leaf.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' })
        $basic=@($leaf.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' })
        $identity=@{
            sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($leaf.RawData)).ToLowerInvariant()
            thumbprint=$leaf.Thumbprint
            code_signing_eku=($eku.Count -eq 1 -and @($eku[0].EnhancedKeyUsages | Where-Object Value -EQ '1.3.6.1.5.5.7.3.3').Count -eq 1)
            end_entity=($basic.Count -eq 1 -and -not $basic[0].CertificateAuthority)
            valid_now=([datetime]::UtcNow -ge $leaf.NotBefore.ToUniversalTime() -and [datetime]::UtcNow -lt $leaf.NotAfter.ToUniversalTime())
            authenticode_valid=($signature.Status -eq 'Valid');chain_valid=$chain.Build($leaf)
        }
        $script:publisherVerification=@{identity=$identity;
            embedded_intermediate_sha256=$inputHashes;trust_mode='System';revocation_mode='Offline';
            revocation_flag=$chain.ChainPolicy.RevocationFlag.ToString();
            issuer_downloads_disabled=$true;
            chain_elements=@($chain.ChainElements | ForEach-Object {
                @{thumbprint=$_.Certificate.Thumbprint;
                    status=@($_.ChainElementStatus | ForEach-Object { $_.Status.ToString() })}
            });offline_chain_status=@($chain.ChainStatus | ForEach-Object { $_.Status.ToString() })}
        Assert-PinnedPublisherIdentity $identity
        # Copy public DER only; the catalog signature object remains separate.
        return [Security.Cryptography.X509Certificates.X509Certificate2]::new($leaf.RawData)
    } finally {foreach($cert in $embedded){$cert.Dispose()};$chain.Dispose();$leaf.Dispose()}
}
function Add-OwnedPublisherLeaf($Leaf,[scriptblock]$ClaimWrite) {
    $store=Open-PinnedPublisherStore $true
    try {
        if(Read-PinnedPublisherPresent){throw 'Publisher already present immediately before add.'}
        # Do not claim a leaf discovered before our write, even when the caller's earlier read was absent.
        & $ClaimWrite
        $store.Add($Leaf)
    } finally {$store.Dispose()}
}
function Remove-OwnedPublisherLeaf($Leaf) {
    # Caller owns a previously absent exact DER leaf; removal is independent of package failure.
    if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Leaf.RawData)).ToLowerInvariant() -cne
        '69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb'){throw 'Removal identity refused.'}
    $store=Open-PinnedPublisherStore $true
    try {$store.Remove($Leaf)} finally {$store.Dispose()}
}
