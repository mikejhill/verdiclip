# Pure admission/ownership helpers. No process, device, trust or display operations.
Set-StrictMode -Version Latest
function Get-StagingBudget([long]$Elapsed, [long]$Deadline, [long]$Individual) {
    if ($Elapsed -lt 0 -or $Individual -le 0 -or $Deadline -le $Elapsed) {
        throw 'Staging phase deadline exhausted or invalid.'
    }
    return [int][Math]::Min($Individual, $Deadline - $Elapsed)
}
function Assert-StagingCompletion([long]$Elapsed, [long]$Deadline, [string]$Baseline, [string]$Observed) {
    if ($Elapsed -lt 0 -or $Elapsed -ge $Deadline -or -not $Baseline -or
        -not [string]::Equals($Baseline,$Observed,[StringComparison]::Ordinal)) {
        throw 'Late completion, changed window/foreground or invalid completion evidence.'
    }
}
function Get-PublishedPackages([string]$Text) {
    return @([regex]::Matches($Text,'(?i)\boem\d+\.inf\b') | ForEach-Object {
        $_.Value.ToLowerInvariant()
    } | Sort-Object -Unique)
}
function Get-NewStagedPackage([string[]]$Before, [string[]]$After) {
    $removed = @($Before | Where-Object { $_ -notin $After })
    $added = @($After | Where-Object { $_ -notin $Before } | Sort-Object -Unique)
    if ($removed.Count -ne 0 -or $added.Count -ne 1 -or
        $added[0] -notmatch '^oem\d+\.inf$') { throw 'Ambiguous package ownership.' }
    return $added[0]
}
function Get-StagingInfIdentity([string]$Text) {
    $models = [regex]::Match($Text,'(?ims)^\[Standard\.NTamd64\]\s*\r?\n(.*?)(?=^\[|\z)')
    if (-not $models.Success) { throw 'Pinned amd64 model section missing.' }
    $ids = @()
    foreach ($line in ($models.Groups[1].Value -split '\r?\n')) {
        $line = ($line -split ';',2)[0].Trim()
        if (-not $line) { continue }
        if ($line -notmatch '^%[^%]+%\s*=\s*[A-Za-z0-9_]+\s*,\s*(.+)$') {
            throw 'Unrecognized pinned model declaration.'
        }
        foreach ($part in ($Matches[1] -split ',')) {
            $id = $part.Trim().Trim('"')
            if ($id -notmatch '^[A-Za-z0-9_\\&.\-]+$') { throw 'Unsupported model ID.' }
            $ids += $id.ToUpperInvariant()
        }
    }
    $provider = [regex]::Match($Text,'(?im)^\s*ManufacturerName\s*=\s*"([^"\r\n]+)"\s*(?:;.*)?$')
    if ($ids.Count -eq 0 -or -not $provider.Success) { throw 'Pinned identity incomplete.' }
    return @{ids=@($ids | Sort-Object -Unique);provider=$provider.Groups[1].Value}
}
function Assert-NoStagingMatch([object[]]$Devices, [string[]]$Ids) {
    if ($Devices.Count -eq 0 -or $Ids.Count -eq 0) { throw 'Missing device/model inventory.' }
    foreach ($device in $Devices) {
        foreach ($id in @($device.hardware) + @($device.compatible)) {
            if ($id -in $Ids) { throw 'Preexisting matching device; staging refused.' }
        }
    }
}
function Assert-StagingSnapshotEqual($Before, $After) {
    foreach ($section in @('devices','trust','display')) {
        $left = ConvertTo-Json -InputObject $Before.$section -Depth 10 -Compress
        $right = ConvertTo-Json -InputObject $After.$section -Depth 10 -Compress
        if ($left -cne $right) { throw ('Unexpected inventory change: ' + $section) }
    }
}
function Get-StagingSnapshotReceipt($Snapshot) {
    if ($null -eq $Snapshot) { return $null }
    $receipt = [ordered]@{}
    foreach ($section in @('devices','trust','display')) {
        $json = ConvertTo-Json -InputObject $Snapshot.$section -Depth 10 -Compress
        $hash = [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($json))
        $receipt[$section + '_sha256'] = [Convert]::ToHexString($hash).ToLowerInvariant()
    }
    return $receipt
}
