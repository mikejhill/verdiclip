# Opt-in fixture readiness only: no cursor writes, focus changes or input.
function Update-OwnedPointerSetup {
    param([string]$Directory,[string]$Nonce,$Window,$Viewer,[hashtable]$State)
    $requestPath=Join-Path $Directory 'pointer-setup-request.json'
    $readyPath=Join-Path $Directory 'pointer-setup-ready.json'
    if ([IO.File]::Exists($readyPath)) { return }
    if ($null -eq $State.observer) {
        if (-not [IO.File]::Exists($requestPath)) { return }
        $file=Get-Item -LiteralPath $requestPath -Force
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked cursor setup request' }
        $stream=[IO.File]::Open($requestPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            $length=$stream.Length
            if ($length -lt 1 -or $length -gt 512) { throw 'Oversized or empty cursor setup request' }
            $buffer=[byte[]]::new([int]$length)
            $offset=0
            while ($offset -lt $length) {
                $read=$stream.Read($buffer,$offset,[int]$length-$offset)
                if ($read -le 0) { throw 'Short cursor setup request' }
                $offset+=$read
            }
            if ($stream.ReadByte() -ne -1) { throw 'Trailing cursor setup request data' }
            $request=[Text.Encoding]::UTF8.GetString($buffer,0,[int]$length)
        } finally { $stream.Dispose() }
        $pattern='\A\{"nonce":"([a-f0-9]{32})","operation":"cursor-setup","request_id":"([a-f0-9]{32})","x":(-?[0-9]{1,10}),"y":(-?[0-9]{1,10}),"target_hwnd":([1-9][0-9]{0,18})\}\z'
        $match=[regex]::Match($request,$pattern)
        if (-not $match.Success -or $match.Groups[1].Value -cne $Nonce) { throw 'Invalid cursor setup request' }
        $x=[int]::Parse($match.Groups[3].Value,[Globalization.CultureInfo]::InvariantCulture)
        $y=[int]::Parse($match.Groups[4].Value,[Globalization.CultureInfo]::InvariantCulture)
        $target=[long]::Parse($match.Groups[5].Value,[Globalization.CultureInfo]::InvariantCulture)
        if ($Window.IsDisposed -or -not $Window.Visible -or $Viewer.IsDisposed -or
            -not $Viewer.Visible -or $Viewer.Handle.ToInt64() -ne $target) { throw 'Cursor setup target unavailable' }
        $client=$Viewer.PointToClient([Drawing.Point]::new($x,$y))
        if (-not $Viewer.ClientRectangle.Contains($client)) { throw 'Cursor setup outside owned client' }
        $State.request_id=$match.Groups[2].Value
        $State.observer=[OwnedCursorReadiness]::new($Viewer.Handle,$x,$y,$Viewer.Cursor.Handle)
        $armed=@{nonce=$Nonce;request_id=$State.request_id;pid=$PID;hwnd=$Window.Handle.ToInt64();armed=$true}
        $partial=Join-Path $Directory 'pointer-setup-armed.partial'
        [IO.File]::WriteAllText($partial,($armed|ConvertTo-Json -Compress))
        [IO.File]::Move($partial,(Join-Path $Directory 'pointer-setup-armed.json'))
    }
    if ($State.observer.Count -eq 1) {
        # No observer remains installed during either capture implementation.
        $State.observer.ReleaseHandle()
        if ($State.observer.Handle -ne [IntPtr]::Zero) { throw 'Cursor setup observer detach unconfirmed' }
        $receipt=@{nonce=$Nonce;request_id=$State.request_id;pid=$PID;hwnd=$Window.Handle.ToInt64();handler_completed=[bool]$State.observer.HandlerCompleted;assigned_cursor_observed=[bool]$State.observer.AssignedCursorObserved;count=1}
        $partial=Join-Path $Directory 'pointer-setup-ready.partial'
        [IO.File]::WriteAllText($partial,($receipt|ConvertTo-Json -Compress))
        [IO.File]::Move($partial,$readyPath)
    }
}
