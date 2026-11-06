# Read-only, one-shot observation; never scroll, focus, activate or restore.
function Write-RequestedFixtureSnapshot {
    param([string]$Directory,[string]$Nonce,[long]$StartedTicks,[scriptblock]$Observe)
    $requestPath=Join-Path $Directory 'fixture-snapshot-request.json'
    $ackPath=Join-Path $Directory 'fixture-snapshot-ack.json'
    if ([IO.File]::Exists($ackPath) -or -not [IO.File]::Exists($requestPath)) { return }
    $file=Get-Item -LiteralPath $requestPath -Force
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint -or
        $file.Length -gt 512) { throw 'Invalid snapshot request file' }
    $request=[IO.File]::ReadAllText($requestPath)
    $pattern='\A\{"nonce":"([a-f0-9]{32})","operation":"snapshot","request_id":"([a-f0-9]{32})"\}\z'
    $match=[regex]::Match($request,$pattern)
    if (-not $match.Success -or $match.Groups[1].Value -cne $Nonce) {
        throw 'Invalid owned snapshot request'
    }
    # Getter execution follows request admission, never argument evaluation before it.
    $observation=& $Observe
    if ($observation -isnot [hashtable] -or $observation.Count -ne 3 -or
        -not $observation.ContainsKey('handle') -or
        -not $observation.ContainsKey('offset') -or
        -not $observation.ContainsKey('end_seen') -or
        $observation.handle -isnot [long] -or
        ($observation.offset -isnot [double] -and $observation.offset -isnot [int] -and
         $observation.offset -isnot [long]) -or $observation.end_seen -isnot [bool]) {
        throw 'Invalid read-only snapshot fields'
    }
    $Handle=$observation.handle
    $Offset=[double]$observation.offset
    $EndSeen=$observation.end_seen
    if ($Handle -le 0 -or $StartedTicks -le 0 -or [double]::IsNaN($Offset) -or
        [double]::IsInfinity($Offset) -or $Offset -lt 0) { throw 'Invalid snapshot observation' }
    $culture=[Globalization.CultureInfo]::InvariantCulture
    $json='{"nonce":"'+$Nonce+'","request_id":"'+$match.Groups[2].Value+
        '","pid":'+$PID.ToString($culture)+',"started_ticks":'+$StartedTicks.ToString($culture)+
        ',"hwnd":'+$Handle.ToString($culture)+',"offset":'+$Offset.ToString('R',$culture)+
        ',"end_seen":'+$EndSeen.ToString().ToLowerInvariant()+'}'
    $partial=Join-Path $Directory 'fixture-snapshot-ack.partial'
    [IO.File]::WriteAllText($partial,$json)
    [IO.File]::Move($partial,$ackPath) # Immutable first observation only.
}
