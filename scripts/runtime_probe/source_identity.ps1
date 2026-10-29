# Pure byte identity check; never loads the verified source or calls Windows APIs.
function Assert-RuntimeSourceIdentity([string]$Root,[string]$Manifest,[string]$ActualSha,[string]$ReviewedSha) {
    if($ActualSha -cnotmatch '\A[0-9a-f]{40}\z' -or $ActualSha -cne $ReviewedSha) {
        throw 'Actual immutable checkout SHA must equal the specifically reviewed SHA.'
    }
    $entries=@(Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json)
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    if($entries.Count -ne 4){throw 'Incomplete controller source manifest.'}
    foreach($entry in $entries) {
        if($entry.name -cnotmatch '\A(?:SoftwareDeviceLease|RuntimeController)(?:Tests)?\.cs\z' -or
            -not $seen.Add([string]$entry.name) -or $entry.sha256 -cnotmatch '\A[0-9a-f]{64}\z') {
            throw 'Malformed or duplicate source manifest entry.'
        }
        $path=Join-Path $Root $entry.name
        if((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse source refused.'}
        if((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $entry.sha256) {
            throw ('Reviewed source bytes changed: '+$entry.name)
        }
    }
}
